# -*- coding: utf-8 -*-
"""providers.azure — moved verbatim from upgrade-provisioner.py (PLT-4916)."""

import os
import sys
import json
import yaml
import time
from urllib.parse import urlparse
from upgrade_lib.commons.shell import run_command
from upgrade_lib.versions import AZUREFILE_CSI_DRIVER_IMAGES, CLOUD_PROVIDER_AZURE_CCM_VERSION_BY_MINOR, CP_ORPHAN_CHECK_GRACE_SECONDS, CP_ORPHAN_CHECK_INTERVAL_SECONDS, CP_ORPHAN_CONFIRM_SECONDS
from upgrade_lib import state as S

def update_cloud_provider_azure_image_tag_value(values_file):
    '''Pin CCM/cloud-node-manager imageTag to a known-published version for the
    cluster's CURRENT k8s minor (chart upgrades run before bump_k8s_version()).'''

    try:
        current_minor = ".".join(S.keos_cluster["spec"]["k8s_version"].lstrip("v").split(".")[:2])
        ccm_tag = CLOUD_PROVIDER_AZURE_CCM_VERSION_BY_MINOR.get(current_minor)
        if not ccm_tag:
            print(f"[WARN] No known-good cloud-provider-azure CCM tag for k8s {current_minor} — leaving chart defaults as-is")
            return

        with open(values_file, 'r') as file:
            values = yaml.safe_load(file)

        for component in ("cloudControllerManager", "cloudNodeManager"):
            section = values.setdefault(component, {})
            repo = section.get("imageRepository", "")
            if repo and "/oss/v2/kubernetes" not in repo:
                repo = repo.replace("/oss/kubernetes", "/oss/v2/kubernetes")
            section["imageRepository"] = repo
            section["imageTag"] = ccm_tag

        with open(values_file, 'w') as file:
            yaml.safe_dump(values, file, default_flow_style=False)

    except Exception as e:
        print(f"An error occurred: {e}")

def update_azurefile_csi_driver_image_tag_value(values_file):
    '''Set the azurefile-csi-driver image repositories and tags as create cluster pins them, keeping baseRepo'''

    try:
        with open(values_file, 'r') as file:
            values = yaml.safe_load(file) or {}

        for path, (repository, tag) in AZUREFILE_CSI_DRIVER_IMAGES.items():
            section = values
            for key in path:
                if not isinstance(section.get(key), dict):
                    section[key] = {}
                section = section[key]
            section["repository"] = repository
            section["tag"] = tag

        with open(values_file, 'w') as file:
            yaml.safe_dump(values, file, default_flow_style=False)

    except Exception as e:
        print(f"An error occurred: {e}")

_orphan_member_first_seen = {}

_orphan_node_first_seen = {}

def confirmed_orphans(candidates, key_of, tracker):
    '''Track a first-seen timestamp per candidate across polls and return those that have held
    the state for CP_ORPHAN_CONFIRM_SECONDS, oldest first. Candidates that recovered are dropped.'''

    now = time.time()
    keys = {key_of(c) for c in candidates}
    for key in keys:
        tracker.setdefault(key, now)
    for stale in set(tracker) - keys:
        del tracker[stale]
    confirmed = [c for c in candidates if now - tracker[key_of(c)] >= CP_ORPHAN_CONFIRM_SECONDS]
    return sorted(confirmed, key=lambda c: tracker[key_of(c)])

def cleanup_orphaned_cp_resources(cluster_name, dry_run):
    '''Azure only: remove a CP etcd member or Node left behind when cluster-api's Machine
    controller silently skips its cleanup on delete (PLT-4792, cluster-api#13221 family +
    machine_controller.go errNilNodeRef). Listing is read-only; removals skip under dry_run.'''

    cp_namespace = "cluster-" + cluster_name

    machine_output, _ = run_command(
        f"{S.kubectl} get machine -n {cp_namespace} -l cluster.x-k8s.io/control-plane -o json",
        allow_errors=True
    )
    try:
        machines = json.loads(machine_output).get("items", [])
    except (ValueError, TypeError):
        return  # transient kubectl failure — retried on the next interval, not worth aborting the bump over
    if not machines:
        # An empty result means the query itself failed transiently — a real CP always has
        # at least one Machine. Otherwise every live control-plane Node gets flagged as
        # orphaned and deleted (seen live 2026-09-14).
        return

    # Match on Node names like cluster-api's own reconcileEtcdMembers does: an etcd member is
    # named after the Node, which is not guaranteed to equal the Machine name.
    live_node_names = {
        m["status"]["nodeRef"]["name"] for m in machines
        if m.get("status", {}).get("nodeRef")
    }
    if not live_node_names:
        return  # every Machine is still provisioning — nothing can be judged orphaned yet

    node_output, _ = run_command(
        f"{S.kubectl} get node -l node-role.kubernetes.io/control-plane -o json",
        allow_errors=True
    )
    try:
        nodes = json.loads(node_output).get("items", [])
    except (ValueError, TypeError):
        return
    if not nodes:
        return

    # A mixed 3.5/3.6 etcd can answer members with an empty name (az-5180-a2), so a member is also matched by its peer URL host
    live_addresses = {
        a["address"] for obj in machines + nodes for a in obj.get("status", {}).get("addresses") or []
        if a.get("type") in ("InternalIP", "ExternalIP") and a.get("address")
    }

    running_node = next(
        (m["status"]["nodeRef"]["name"] for m in machines
         if m.get("status", {}).get("phase") == "Running" and m.get("status", {}).get("nodeRef")),
        None
    )
    if running_node:
        etcdctl_tls = (
            "--cacert=/etc/kubernetes/pki/etcd/ca.crt "
            "--cert=/etc/kubernetes/pki/etcd/server.crt "
            "--key=/etc/kubernetes/pki/etcd/server.key "
        )
        etcdctl_exec = f"{S.kubectl} exec -n kube-system etcd-{running_node} -c etcd -- etcdctl "
        etcdctl = etcdctl_exec + "--endpoints=https://127.0.0.1:2379 " + etcdctl_tls
        member_output, _ = run_command(etcdctl + "member list -w json", allow_errors=True)
        try:
            members = json.loads(member_output).get("members", [])
        except (ValueError, TypeError):
            members = []
        if members:
            def member_is_healthy(member):
                # A running member is not an orphan; one that never started (mid-join leak) has no client URL to answer
                for url in member.get("clientURLs") or []:
                    out, _ = run_command(etcdctl_exec + f"--endpoints={url} " + etcdctl_tls + "endpoint health --command-timeout=5s", allow_errors=True)
                    if " is healthy:" in out:
                        return True
                return False
            orphans = [
                m for m in members
                if m.get("name", "") not in live_node_names
                and not {urlparse(u).hostname for u in m.get("peerURLs") or []} & live_addresses
                and not member_is_healthy(m)
            ]
            # A healthy candidate drops out of the list, so its hold restarts if it turns unhealthy later
            confirmed = confirmed_orphans(orphans, lambda m: m["ID"], _orphan_member_first_seen)
            if orphans and not confirmed:
                print(f"[INFO] {len(orphans)} etcd member(s) with no matching Node — holding until the confirmation window elapses")
            elif confirmed and len(members) < 3:
                print(f"[WARN] Orphaned etcd member(s) confirmed but only {len(members)} member(s) present — refusing to remove")
            elif confirmed:
                member = confirmed[0]
                member_id = member.get("name") or f"{member['ID']:x}"
                print(f"[WARN] Orphaned etcd member with no matching Node: {member_id}")
                if not dry_run:
                    _, err = run_command(etcdctl + f"member remove {member['ID']:x}", allow_errors=True)
                    if err:
                        print(f"[WARN] Failed to remove orphaned etcd member {member_id}: {err.strip()}")
                    else:
                        print(f"[INFO] Removed orphaned etcd member {member_id}")

    # Only Ready=Unknown (kubelet gone) is an orphan: Ready=False or no Ready condition is a CP still joining.
    orphan_nodes = [
        n for n in nodes
        if n["metadata"]["name"] not in live_node_names
        and any(c.get("type") == "Ready" and c.get("status") == "Unknown"
                for c in n.get("status", {}).get("conditions", []))
    ]
    confirmed_nodes = confirmed_orphans(orphan_nodes, lambda n: n["metadata"]["name"], _orphan_node_first_seen)
    if orphan_nodes and not confirmed_nodes:
        print(f"[INFO] {len(orphan_nodes)} control-plane Node(s) with no matching Machine — holding until the confirmation window elapses")
    elif confirmed_nodes and len(nodes) < 2:
        print(f"[WARN] Orphaned Node(s) confirmed but only {len(nodes)} control-plane Node(s) present — refusing to delete")
    elif confirmed_nodes:
        node_name = confirmed_nodes[0]["metadata"]["name"]
        print(f"[WARN] Orphaned Node with no matching Machine: {node_name}")
        if not dry_run:
            _, err = run_command(f"{S.kubectl} delete node {node_name}", allow_errors=True)
            if err:
                print(f"[WARN] Failed to delete orphaned Node {node_name}: {err.strip()}")
            else:
                print(f"[INFO] Deleted orphaned Node {node_name}")

def wait_for_capi_kcp_version(cluster_name, target_version, timeout_minutes=None):
    '''Wait for the real CP rollout to converge on target_version's minor.'''

    if timeout_minutes is None:
        timeout_minutes = S.config["control_plane_timeout"]
    kcp_name = cluster_name + "-control-plane"
    cp_namespace = "cluster-" + cluster_name
    target_minor_prefix = "v" + ".".join(target_version.lstrip("v").split(".")[:2]) + "."
    print(f"[INFO] Waiting for the real control plane to reach {target_version} (timeout {timeout_minutes}m):", end=" ", flush=True)
    loop_start = time.time()
    deadline = loop_start + timeout_minutes * 60
    last_orphan_check = loop_start
    while time.time() < deadline:
        output, _ = run_command(
            # v1beta2 explicitly: upgrade-providers.py already moved the core, and its status has no ready/updatedReplicas
            f"{S.kubectl} get kubeadmcontrolplanes.v1beta2.controlplane.cluster.x-k8s.io {kcp_name} -n {cp_namespace} -o json",
            allow_errors=True
        )
        try:
            kcp = json.loads(output)
            status = kcp.get("status", {})
            desired_replicas = kcp.get("spec", {}).get("replicas")
            replicas = status.get("replicas")
            ready_replicas = status.get("readyReplicas")
            up_to_date_replicas = status.get("upToDateReplicas")
            available = any(c.get("type") == "Available" and c.get("status") == "True" for c in status.get("conditions", []))
            converged = (
                status.get("version", "").startswith(target_minor_prefix) and
                available and
                replicas == ready_replicas == up_to_date_replicas == desired_replicas
            )
        except (ValueError, TypeError):
            converged = False
        if converged:
            print("OK")
            return
        now = time.time()
        if now - loop_start > CP_ORPHAN_CHECK_GRACE_SECONDS and now - last_orphan_check > CP_ORPHAN_CHECK_INTERVAL_SECONDS:
            cleanup_orphaned_cp_resources(cluster_name, S.config["dry_run"])
            last_orphan_check = now
        time.sleep(10)
    raise Exception(f"Timed out after {timeout_minutes}m waiting for KubeadmControlPlane to converge on {target_version}")

def configure_azure_credentials(vault_secrets_data):
    print("[INFO] Configuring Azure CLI credentials", end=" ", flush=True)
    azure_client_id = vault_secrets_data['secrets']['azure']['credentials']['client_id']
    azure_client_secret = vault_secrets_data['secrets']['azure']['credentials']['client_secret']
    azure_subscription_id = vault_secrets_data['secrets']['azure']['credentials']['subscription_id']
    azure_tenant_id = vault_secrets_data['secrets']['azure']['credentials']['tenant_id']

    command = f"az login --service-principal --username {azure_client_id} \
                --password {azure_client_secret} --tenant {azure_tenant_id}"

    try:
        run_command(command)
        print("OK")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Azure CLI login failed: {e}")
        sys.exit(1)

def repin_cloud_provider_azure_after_bump(cluster_name, timeout_minutes=15):
    '''Azure only, at the end of every run (charts pin the PRE-bump minor and are skipped on a relaunch; live 2026-10-01:
    k8s v1.36.5 with CCM v1.35.9): re-pin the CCM/cloud-node-manager to the minor the kube-apiserver runs and wait
    for the pods to roll; nothing when both already carry that tag.'''

    components = ("cloudControllerManager", "cloudNodeManager")
    print("[INFO] Re-pinning cloud-provider-azure CCM/cloud-node-manager to the kube-apiserver minor:", end=" ", flush=True)
    version_output, _ = run_command(f"{S.kubectl} version -o json", allow_errors=True)
    spec_version, _ = run_command(
        f"{S.kubectl} get keoscluster {cluster_name} -n cluster-{cluster_name} -o jsonpath='{{.spec.k8s_version}}'", allow_errors=True)
    try:
        server = json.loads(version_output)["serverVersion"]
        live_minor = f"{server['major']}.{''.join(ch for ch in server['minor'] if ch.isdigit())}"
    except (ValueError, TypeError, KeyError):
        print("SKIP")
        print("[WARN] Could not read the kube-apiserver version — CCM left as is")
        return
    spec_minor = ".".join((spec_version or "").strip().lstrip("v").split(".")[:2])
    # The CCM may be one minor older than the kube-apiserver, never newer: wait until the bump has reached the apiserver
    if live_minor != spec_minor:
        print("SKIP")
        print(f"[WARN] kube-apiserver runs k8s {live_minor} but KeosCluster spec.k8s_version is '{(spec_version or '').strip()}' — CCM left as is")
        return
    ccm_tag = CLOUD_PROVIDER_AZURE_CCM_VERSION_BY_MINOR.get(live_minor)
    if not ccm_tag:
        print("SKIP")
        print(f"[WARN] No known-good cloud-provider-azure CCM tag for k8s {live_minor} — CCM left as is")
        return
    configmap = "00-cloud-provider-azure-helm-chart-default-values"
    values_yaml, _ = run_command(f"{S.kubectl} get configmap {configmap} -n kube-system -o jsonpath='{{.data.values\\.yaml}}'", allow_errors=True)
    if not values_yaml:
        print(f"SKIP (ConfigMap kube-system/{configmap} not found)")
        return
    values = yaml.safe_load(values_yaml) or {}
    current = {c: (values.get(c) or {}).get("imageTag") for c in components}
    if all(tag == ccm_tag for tag in current.values()):
        print(f"OK (already {ccm_tag} for k8s {live_minor})")
        return
    if S.config["dry_run"]:
        print(f"DRY-RUN ({current} -> {ccm_tag} for k8s {live_minor})")
        return
    for component in components:
        values.setdefault(component, {})["imageTag"] = ccm_tag
    values_file = "/tmp/cloud-provider-azure_repin_values.yaml"
    with open(values_file, 'w') as file:
        yaml.safe_dump(values, file, default_flow_style=False)
    run_command(
        f"{S.kubectl} create configmap {configmap} -n kube-system --from-file=values.yaml={values_file} "
        f"--dry-run=client -o yaml | {S.kubectl} apply -f -"
    )
    run_command(
        f"{S.kubectl} annotate helmrelease cloud-provider-azure -n kube-system "
        f"reconcile.fluxcd.io/requestedAt=\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" --overwrite"
    )
    deadline = time.time() + timeout_minutes * 60
    while time.time() < deadline:
        ccm_output, _ = run_command(f"{S.kubectl} get pods -n kube-system -l component=cloud-controller-manager -o json", allow_errors=True)
        cnm_output, _ = run_command(f"{S.kubectl} get pods -n kube-system -l k8s-app=cloud-node-manager -o json", allow_errors=True)
        try:
            pods = json.loads(ccm_output).get("items", []) + json.loads(cnm_output).get("items", [])
            converged = bool(pods) and all(
                status.get("image", "").endswith(f":{ccm_tag}") and status.get("ready")
                for pod in pods for status in pod.get("status", {}).get("containerStatuses", [])
            )
        except (ValueError, TypeError):
            converged = False
        if converged:
            print("OK")
            os.remove(values_file)
            return
        time.sleep(15)
    raise Exception(f"Timed out after {timeout_minutes}m waiting for the cloud-provider-azure pods to run {ccm_tag}")
