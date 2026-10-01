# -*- coding: utf-8 -*-
"""commons.keoscluster — moved verbatim from upgrade-provisioner.py (PLT-4916)."""

import os
import json
import subprocess
import re
import time
from upgrade_lib.commons.backup import capsule_nodes_webhook
from upgrade_lib.commons.k8s import is_private_helm_repo_enabled, is_private_registry_enabled, update_annotation_label
from upgrade_lib.commons.network import cp_global_network_policy
from upgrade_lib.commons.shell import execute_command, run_command
from upgrade_lib.providers.azure import wait_for_capi_kcp_version
from upgrade_lib.providers.gcp import resolve_gke_version, wait_for_gke_node_pool_convergence
from upgrade_lib.versions import AZURE_K8S_VERSION_BY_MINOR, CAPA, CAPG, CAPI, CAPZ, STEP_SETTLE_SECONDS
from upgrade_lib import state as S

def wait_for_keos_cluster(cluster_name, timeout_minutes):
    '''Wait for the KeosCluster to be ready'''

    command = (
        "kubectl wait --for=jsonpath=\"{.status.ready}\"=true KeosCluster "
        + cluster_name + " -n cluster-" + cluster_name + " --timeout "+timeout_minutes+"m"
    )
    execute_command(command, False, False)

def stop_keoscluster_controller():
    '''Stop the KEOSCluster controller'''

    try:
        print("[INFO] Stopping keoscluster-controller-manager deployment:", end =" ", flush=True)
        run_command(f"{S.kubectl} scale deployment -n kube-system keoscluster-controller-manager --replicas=0")

        if S.config["dry_run"]:
            print("DRY-RUN")
            return

        # Wait until pods actually terminate before touching CRDs/providers live
        deadline = time.time() + 120
        while time.time() < deadline:
            pods, _ = run_command(
                f"{S.kubectl} get pods -n kube-system -l app.kubernetes.io/name=keoscluster-controller-manager --no-headers",
                allow_errors=True
            )
            if not pods.strip():
                break
            time.sleep(5)
        else:
            raise Exception("keoscluster-controller-manager pods still present after scale-down timeout")

        print("OK")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error stopping the KEOSCluster controller: {e}")
        raise e

def disable_keoscluster_webhooks():
    '''Disable the KEOSCluster webhooks'''

    try:
        backup_keoscluster_webhooks()
        print("[INFO] Disabling KEOSCluster webhooks:", end =" ", flush=True)

        _, err = run_command(f"{S.kubectl} delete validatingwebhookconfiguration keoscluster-validating-webhook-configuration", allow_errors=True)
        if err and "NotFound" not in err:
            raise Exception(f"Failed to delete validatingwebhookconfiguration: {err}")

        _, err = run_command(f"{S.kubectl} delete mutatingwebhookconfiguration keoscluster-mutating-webhook-configuration", allow_errors=True)
        if err and "NotFound" not in err:
            raise Exception(f"Failed to delete mutatingwebhookconfiguration: {err}")

        print("OK")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error disabling KEOSCluster webhooks: {e}")
        raise e

def backup_keoscluster_webhooks():
    '''Backup the KEOSCluster webhooks'''

    backup_file = S.backup_dir + "/cluster-operator/keoscluster-webhooks.yaml"
    try:
        if not os.path.exists(os.path.dirname(backup_file)):
            os.makedirs(os.path.dirname(backup_file))
        print("[INFO] Backing up KEOSCluster webhook configurations:", end =" ", flush=True)

        manifest, _ = run_command(f"{S.helm} get manifest -n kube-system cluster-operator")  # check exit code before piping to yq

        yq_result = subprocess.run(
            ["yq", 'select(.kind == "ValidatingWebhookConfiguration" or .kind == "MutatingWebhookConfiguration")'],
            input=manifest, capture_output=True, text=True
        )
        if yq_result.returncode != 0:
            raise Exception(f"yq filtering failed: {yq_result.stderr}")

        with open(backup_file, 'w') as f:
            f.write(yq_result.stdout)

        expected_kinds = ["ValidatingWebhookConfiguration", "MutatingWebhookConfiguration"]
        missing = [k for k in expected_kinds if f"kind: {k}" not in yq_result.stdout]
        if missing:
            raise Exception(f"Backup file is missing expected webhook kind(s): {', '.join(missing)}")

        print("OK")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error backing up KEOSCluster webhooks: {e}")
        raise e

def update_clusterconfig(cluster_config, charts, provider, cluster_operator_version):
    '''Update the clusterconfig'''

    try:
        print("[INFO] Updating clusterconfig:", end =" ", flush=True)

        clusterconfig_name = cluster_config["metadata"]["name"]
        clusterconfig_namespace = cluster_config["metadata"]["namespace"]

        # ------------------------------------------------------------------
        # Update cluster-operator
        # ------------------------------------------------------------------
        cluster_config["spec"]["cluster_operator_version"] = cluster_operator_version
        cluster_config["spec"]["cluster_operator_image_version"] = cluster_operator_version
        cluster_config["spec"]["private_registry"] = is_private_registry_enabled(cluster_config)
        cluster_config["spec"]["private_helm_repo"] = is_private_helm_repo_enabled(cluster_config)

        # ------------------------------------------------------------------
        # Update CAPX (Cluster API providers)
        # ------------------------------------------------------------------
        if "capx" not in cluster_config["spec"]:
            cluster_config["spec"]["capx"] = {}

        # Always update CAPI
        cluster_config["spec"]["capx"]["capi_version"] = CAPI

        if provider == "aws":
            cluster_config["spec"]["capx"]["capa_version"] = CAPA
            cluster_config["spec"]["capx"]["capa_image_version"] = CAPA

        elif provider == "gcp":
            cluster_config["spec"]["capx"]["capg_version"] = CAPG
            cluster_config["spec"]["capx"]["capg_image_version"] = CAPG

        elif provider == "azure":
            cluster_config["spec"]["capx"]["capz_version"] = CAPZ
            cluster_config["spec"]["capx"]["capz_image_version"] = CAPZ

        # ------------------------------------------------------------------
        # Update Helm charts list
        # ------------------------------------------------------------------
        cluster_config["spec"]["charts"] = []
        for chart_name, chart_data in charts.items():
            cluster_config["spec"]["charts"].append({
                "name": chart_name,
                "version": chart_data["version"]
            })

        # ------------------------------------------------------------------
        # Patch ClusterConfig
        # ------------------------------------------------------------------
        clusterconfig_json = json.dumps(cluster_config)
        command = (
            f"{S.kubectl} patch clusterconfig {clusterconfig_name} "
            f"-n {clusterconfig_namespace} --type merge -p '{clusterconfig_json}'"
        )

        run_command(command)

        print("OK")

    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error updating the clusterconfig: {e}")
        raise e

def parse_k8s_minor(version):
    '''Extract (major, minor) as ints from a k8s version string like "v1.32.0" or "1.32".'''

    match = re.match(r'v?(\d+)\.(\d+)', version)
    if not match:
        raise ValueError(f"Cannot parse k8s version: {version}")
    return (int(match.group(1)), int(match.group(2)))

def azure_k8s_version(minor):
    '''Exact vX.Y.Z the Azure upgrade patches a minor to — must match that minor's node image.'''
    minor = minor.lstrip("v")
    if minor not in AZURE_K8S_VERSION_BY_MINOR:
        raise Exception(f"No Azure k8s version for minor {minor} in AZURE_K8S_VERSION_BY_MINOR (upgrade_lib/versions.py)")
    return AZURE_K8S_VERSION_BY_MINOR[minor]

def bump_k8s_version(keos_cluster, cluster_name, target_minor, start_from_k8s_version, dry_run, provider=None, node_image_map=None):
    '''AWS: single patch (CAPA steps it internally). Azure/GCP: step one minor at a time —
    Azure via node_image_map; GCP because GKE rejects a >1-minor master jump (confirmed live
    2026-09-16, see Tasks/PLT-4792/Issues/gke-master-upgrade-single-minor-only.md).'''

    current_version = keos_cluster["spec"]["k8s_version"]
    current_minor = parse_k8s_minor(current_version)
    target_minor_tuple = parse_k8s_minor(target_minor)

    if current_minor == target_minor_tuple:
        print(f"[INFO] k8s_version already at target {target_minor}: SKIP")
        if provider == "azure":
            # Resuming after a crash right at the final step: k8s_version already matches
            # target, but the worker rollout it triggered may not have converged yet.
            for wn in keos_cluster["spec"].get("worker_nodes", []):
                if wn.get("node_image"):
                    wait_for_capi_md_convergence(cluster_name, wn["name"], current_version)
        elif provider == "gcp":
            # Same resume case: the control plane is already at target but node pools may not be.
            wait_for_gke_node_pool_convergence(cluster_name, target_minor)
        return False
    if current_minor > target_minor_tuple:
        # Plain Exception, not sys.exit(): SystemExit isn't an Exception subclass and would
        # skip the critical section's controlled-recovery except block (webhooks stay disabled).
        raise Exception(f"Cluster k8s_version ({current_version}) is newer than the requested target (v{target_minor}.0) — downgrade is not supported")

    if provider == "gcp":
        target_version = resolve_gke_version(cluster_name, target_minor)
    elif provider == "azure":
        target_version = azure_k8s_version(target_minor)
    else:
        target_version = f"v{target_minor}.0"
    print(f"[INFO] Planned k8s_version bump: {current_version} -> {target_version}")
    if provider == "azure":
        steps = []
        step_major, step_minor = current_minor
        target_major, target_minor_num = target_minor_tuple
        while (step_major, step_minor) != (target_major, target_minor_num):
            step_minor += 1
            steps.append(azure_k8s_version(f"{step_major}.{step_minor}"))
        print(f"[INFO] Control plane will step through each minor in order: {' -> '.join([current_version] + steps)}")

        # Validated here, ahead of the dry_run cutoff below, so a malformed --node-image-map
        # is caught by a dry-run too, not only once a real bump is confirmed.
        if not node_image_map:
            raise Exception("provider=azure requires --node-image-map for a k8s_version bump")
        try:
            image_map = json.loads(node_image_map) if isinstance(node_image_map, str) else node_image_map
        except Exception as e:
            raise Exception(f"--node-image-map is not valid JSON: {e}")

        # Must mirror isNodeImage in cluster-operator api/v1beta1/keoscluster_webhook.go
        node_image_re = re.compile(
            r'(?:^/subscriptions/[\w-]+/resourceGroups/[\w.-]+/providers/Microsoft\.Compute/images/[\w.-]+\Z)'
            r'|(?:^/CommunityGalleries/[\w.-]+/images/[\w.-]+/versions/[\w.-]+\Z)',
            re.IGNORECASE | re.ASCII,
        )
        bad_images = {k: v for k, v in image_map.items() if not node_image_re.match(str(v))}
        if bad_images:
            raise Exception(
                "--node-image-map values must have the format "
                "/subscriptions/[SUBSCRIPTION_ID]/resourceGroups/[RESOURCE_GROUP]/providers/Microsoft.Compute/images/[IMAGE_NAME] "
                "or /CommunityGalleries/[GALLERY_ID]/Images/[REPO_NAME]/Versions/[IMAGE_VERSION]. "
                "A Compute Gallery (SIG) resource ID is not accepted by the KeosCluster webhook and, because "
                f"this script disables that webhook, it would be persisted and break every later reconcile: {bad_images}"
            )

        # Same reasoning as the format check above: the per-minor completeness check the
        # stepping loop does later (missing an entry for minor X.Y) is pure map lookup against
        # `steps`, already computed — no reason to wait for a real run to catch a short map.
        missing_minors = [s.lstrip("v").rsplit(".", 1)[0] for s in steps if s.lstrip("v").rsplit(".", 1)[0] not in image_map]
        if missing_minors:
            raise Exception(f"--node-image-map is missing an entry for minor(s): {', '.join(missing_minors)}")

    if dry_run:
        print("[INFO] Bumping k8s_version: DRY-RUN")
        return False

    if not start_from_k8s_version:
        while True:
            answer = input(f"Proceed with the k8s_version bump {current_version} -> {target_version}? [y/N]: ").strip().lower()
            if answer in ("", "n", "no"):
                print("[INFO] k8s_version bump: SKIP (not confirmed)")
                return False
            if answer in ("y", "yes"):
                break
            print("[WARN] Please answer 'y' or 'n'")

    if provider == "azure":
        major, minor = current_minor
        target_major, target_minor_num = target_minor_tuple

        # Resuming after an interrupted run: KeosCluster.spec.k8s_version may already say
        # the current step, but that doesn't mean it converged — verify for real before
        # stepping any further.
        wait_for_capi_kcp_version(cluster_name, f"v{major}.{minor}.0")

        cp_global_network_policy("patch", keos_cluster, S.backup_dir, dry_run)
        capsule_nodes_webhook("patch", S.backup_dir, dry_run)
        try:
            while (major, minor) != (target_major, target_minor_num):
                minor += 1
                step_version = azure_k8s_version(f"{major}.{minor}")
                step_key = f"{major}.{minor}"
                step_image = image_map.get(step_key)
                if not step_image:
                    raise Exception(f"--node-image-map is missing an entry for minor {step_key}")

                print(f"[INFO] Stepping control plane to {step_version}:", end=" ", flush=True)
                ops = [
                    {"op": "replace", "path": "/spec/k8s_version", "value": step_version},
                    {"op": "replace", "path": "/spec/control_plane/node_image", "value": step_image},
                ]
                # cluster-operator bumps every worker MD's spec.version at EACH step
                # regardless of node_image (see below) — patch node_image here too, every
                # step, so the replaced Machine actually boots the matching kubelet.
                for i, wn in enumerate(keos_cluster["spec"].get("worker_nodes", [])):
                    if wn.get("node_image"):
                        ops.append({"op": "replace", "path": f"/spec/worker_nodes/{i}/node_image", "value": step_image})
                command = (
                    S.kubectl + " patch keoscluster " + cluster_name + " -n cluster-" + cluster_name +
                    " --type=json -p '" + json.dumps(ops) + "'"
                )
                run_command(command)
                print("OK")
                wait_for_capi_kcp_version(cluster_name, step_version)
                # Wait for the real rollout this step's patch triggered (both k8s_version
                # and node_image now change together, see comment above).
                for wn in keos_cluster["spec"].get("worker_nodes", []):
                    if wn.get("node_image"):
                        wait_for_capi_md_convergence(cluster_name, wn["name"], step_version)
                print(f"[INFO] Letting the cluster settle for {STEP_SETTLE_SECONDS}s before the next step:", end=" ", flush=True)
                time.sleep(STEP_SETTLE_SECONDS)
                print("OK")
        finally:
            cp_global_network_policy("restore", keos_cluster, S.backup_dir, dry_run)
            capsule_nodes_webhook("restore", S.backup_dir, dry_run)
        return True

    if provider == "gcp":
        major, minor = current_minor
        target_major, target_minor_num = target_minor_tuple
        steps = []
        while (major, minor) != (target_major, target_minor_num):
            minor += 1
            steps.append(f"{major}.{minor}")
        print(f"[INFO] Control plane will step through each minor in order: {' -> '.join([current_version] + steps)}")
        for step_key in steps:
            step_version = resolve_gke_version(cluster_name, step_key)
            print(f"[INFO] Stepping control plane to {step_version}:", end=" ", flush=True)
            command = (
                S.kubectl + " patch keoscluster " + cluster_name + " -n cluster-" + cluster_name +
                " --type=merge -p '{\"spec\":{\"k8s_version\":\"" + step_version + "\"}}'"
            )
            run_command(command)
            print("OK")
            wait_for_k8s_version_bump(cluster_name, provider, step_key)
            # GKE only tolerates nodes 2 minors behind the CP, so workers must converge before
            # the next step, not at the end (live 2026-09-18: ended 3 minors behind).
            wait_for_gke_node_pool_convergence(cluster_name, step_key)
            wait_for_keoscluster_settled(cluster_name)
        return True

    print(f"[INFO] Patching k8s_version to {target_version}:", end=" ", flush=True)
    command = (
        S.kubectl + " patch keoscluster " + cluster_name + " -n cluster-" + cluster_name +
        " --type=merge -p '{\"spec\":{\"k8s_version\":\"" + target_version + "\"}}'"
    )
    run_command(command)
    print("OK")
    return True

def verify_control_plane_patch_propagated(cluster_name, target_minor, timeout_seconds=120, poll_interval=10):
    '''Verify the k8s_version bump reached AWSManagedControlPlane before the long
    AWS-side wait — guards against the PLT-4265 annotation-resync gap.'''

    target_version = f"v{target_minor}.0"
    print(f"[INFO] Verifying cluster-operator propagated the bump to AWSManagedControlPlane (timeout {timeout_seconds}s):", end=" ", flush=True)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        output, _ = run_command(
            f"{S.kubectl} -n cluster-{cluster_name} get awsmanagedcontrolplane {cluster_name}-control-plane -o jsonpath='{{.spec.version}}'",
            allow_errors=True
        )
        if output.strip() == target_version:
            print("OK")
            return
        time.sleep(poll_interval)
    raise Exception(
        f"cluster-operator never propagated k8s_version to AWSManagedControlPlane "
        f"(still not {target_version} after {timeout_seconds}s) — keoscluster-controller-manager "
        f"likely reset its diff-tracking annotation (cluster-operator.stratio.com/last-configuration) "
        f"on restart and treated this reconcile as a first-time sync instead of detecting the "
        f"k8s_version change. Check the annotation and keoscluster-controller-manager logs before retrying "
        f"— do not just re-run this script, the patched KeosCluster spec already matches the target "
        f"so a plain re-run would SKIP the bump step entirely."
    )

def wait_for_k8s_version_bump(cluster_name, provider, target_minor, timeout_minutes=None):
    '''Wait for a k8s_version bump to fully land. AWS: status.ready can read True transiently
    between CAPA's internal minor steps (observed live), so poll `aws eks describe-cluster`
    directly instead. Azure reuses bump_k8s_version()'s own convergence check. GCP: same
    transient-ready risk confirmed live 2026-09-16 (status.ready read True while
    GCPManagedControlPlane was stuck in a permanent error loop) — poll `gcloud container
    clusters describe` directly instead of the generic ready wait.'''

    if timeout_minutes is None:
        timeout_minutes = S.config["control_plane_timeout"]
    if provider == "aws":
        verify_control_plane_patch_propagated(cluster_name, target_minor)
        print(f"[INFO] Waiting for the real EKS control plane to reach {target_minor} (timeout {timeout_minutes}m):", end=" ", flush=True)
        deadline = time.time() + timeout_minutes * 60
        while time.time() < deadline:
            output, _ = run_command(
                f"aws eks describe-cluster --name {cluster_name} --query 'cluster.[status,version]' --output text",
                allow_errors=True
            )
            parts = output.split()
            if len(parts) == 2 and parts[0] == "ACTIVE" and parts[1] == target_minor:
                print("OK")
                return
            time.sleep(30)
        raise Exception(f"Timed out after {timeout_minutes}m waiting for the EKS control plane to reach {target_minor}")

    if provider == "azure":
        wait_for_capi_kcp_version(cluster_name, f"v{target_minor}.0", timeout_minutes)
        return

    if provider == "gcp":
        gcp_creds = S.vault_secrets_data['secrets']['gcp']['credentials']
        project_id = gcp_creds['project_id']
        location = gcp_creds.get('region') or gcp_creds.get('zone')
        print(f"[INFO] Waiting for the real GKE control plane to reach {target_minor} (timeout {timeout_minutes}m):", end=" ", flush=True)
        deadline = time.time() + timeout_minutes * 60
        while time.time() < deadline:
            output, _ = run_command(
                f"gcloud container clusters describe {cluster_name} --zone {location} --project {project_id} "
                f"--format='value(status,currentMasterVersion)'",
                allow_errors=True
            )
            parts = output.split()
            if len(parts) == 2 and parts[0] == "RUNNING" and parts[1].startswith(f"{target_minor}."):
                print("OK")
                return
            time.sleep(30)
        raise Exception(f"Timed out after {timeout_minutes}m waiting for the GKE control plane to reach {target_minor}")

    print(f"[INFO] Waiting for KeosCluster to be ready after k8s_version bump (timeout {timeout_minutes}m):", end=" ", flush=True)
    command = (
        S.kubectl + " wait --for=jsonpath=\"{.status.ready}\"=true KeosCluster " +
        cluster_name + " -n cluster-" + cluster_name + f" --timeout {timeout_minutes}m"
    )
    run_command(command)
    print("OK")

def restore_keoscluster_webhooks():
    '''Restore the KEOSCluster webhooks'''

    backup_file = S.backup_dir + "/cluster-operator/keoscluster-webhooks.yaml"
    resources_webhooks = [
        {"kind": "MutatingWebhookConfiguration", "name": "keoscluster-mutating-webhook-configuration", "namespace": "kube-system"},
        {"kind": "ValidatingWebhookConfiguration", "name": "keoscluster-validating-webhook-configuration", "namespace": "kube-system"},
    ]
    try:
        print("[INFO] Restoring KEOSCluster webhooks from backup:", end =" ", flush=True)
        _, err = run_command(f"{S.kubectl} create -f {backup_file}", allow_errors=True)
        if err and "AlreadyExists" not in err:
            raise Exception(f"Failed to restore webhooks from backup: {err}")

        # "create" succeeding (or no-op'ing on AlreadyExists) isn't proof the webhook is
        # actually there — verify both objects exist before declaring the restore done.
        for resource in resources_webhooks:
            _, check_err = run_command(f"{S.kubectl} get {resource['kind']} {resource['name']}", allow_errors=True)
            if "NotFound" in check_err or "not found" in check_err.lower():
                raise Exception(f"{resource['kind']}/{resource['name']} missing after restore attempt")

        print("OK")

        print("[INFO] Labeling and annotating webhooks:", end =" ", flush=True)
        update_annotation_label("app.kubernetes.io/managed-by", "Helm", resources_webhooks, "label")
        update_annotation_label("meta.helm.sh/release-name", "cluster-operator", resources_webhooks)
        update_annotation_label("meta.helm.sh/release-namespace", "kube-system", resources_webhooks)
        print("OK")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error restoring KEOSCluster webhooks from backup: {e}")
        raise e

def wait_for_capi_md_convergence(cluster_name, wn_name, target_version, stall_minutes=None):
    '''Wait for every worker MachineDeployment of wn_name to converge on target_version —
    checks the real kubeletVersion on each Node, not just spec.version (a Machine can say
    the right version while still running the old node_image's kubelet, see PLAN.md).'''

    if stall_minutes is None:
        stall_minutes = S.config["node_convergence_timeout"]
    cp_namespace = "cluster-" + cluster_name
    target_minor_prefix = "v" + ".".join(target_version.lstrip("v").split(".")[:2])
    print(f"[INFO] Waiting for the real worker nodes ({wn_name}) to reach {target_version} (timeout {stall_minutes}m without progress):", end=" ", flush=True)
    best_progress = -1
    deadline = time.time() + stall_minutes * 60
    while time.time() < deadline:
        progress = best_progress
        output, _ = run_command(
            f"{S.kubectl} get machinedeployment -n {cp_namespace} -o json",
            allow_errors=True
        )
        nodes_output, _ = run_command(f"{S.kubectl} get nodes -o json", allow_errors=True)
        try:
            mds = [
                md for md in json.loads(output).get("items", [])
                if md.get("metadata", {}).get("name", "").startswith(f"{wn_name}-md-")
            ]
            spec_converged = bool(mds) and all(
                md.get("spec", {}).get("template", {}).get("spec", {}).get("version") == target_version and
                md.get("status", {}).get("phase") == "Running" and
                md.get("status", {}).get("unavailableReplicas", 0) in (0, None) and
                md.get("status", {}).get("replicas") == md.get("status", {}).get("readyReplicas") == md.get("status", {}).get("updatedReplicas")
                for md in mds
            )
            wn_nodes = [
                node for node in json.loads(nodes_output).get("items", [])
                if node.get("metadata", {}).get("name", "").startswith(f"{wn_name}-md-")
            ]
            progress = sum(
                1 for node in wn_nodes
                if node.get("status", {}).get("nodeInfo", {}).get("kubeletVersion", "").startswith(target_minor_prefix)
            )
            kubelet_converged = bool(wn_nodes) and progress == len(wn_nodes)
            converged = spec_converged and kubelet_converged and len(wn_nodes) == sum(md.get("status", {}).get("replicas", 0) for md in mds)
        except (ValueError, TypeError):
            converged = False
        if converged:
            print("OK")
            return
        if progress > best_progress:
            best_progress = progress
            deadline = time.time() + stall_minutes * 60
        time.sleep(10)
    raise Exception(f"No progress for {stall_minutes}m waiting for worker nodes ({wn_name}) to reach {target_version} ({best_progress} node(s) converged)")

def wait_for_keoscluster_settled(cluster_name, timeout_minutes=30):
    '''Wait for cluster-operator to finish its own reconcile — not the same as the infrastructure
    having converged. Patching the next step mid-reconcile left the control plane stuck once
    (live 2026-09-18, 19s before it closed); Azure buys the same margin with a fixed sleep.'''

    print(f"[INFO] Waiting for the KeosCluster to settle before the next step (timeout {timeout_minutes}m):", end=" ", flush=True)
    deadline = time.time() + timeout_minutes * 60
    while time.time() < deadline:
        output, _ = run_command(
            f"{S.kubectl} get keoscluster {cluster_name} -n cluster-{cluster_name} "
            f"-o jsonpath='{{.status.ready}} {{.status.phase}}'",
            allow_errors=True
        )
        if output.strip() == "true Provisioned":
            print("OK")
            return
        time.sleep(15)
    raise Exception(f"Timed out after {timeout_minutes}m waiting for the KeosCluster to settle")

def wait_for_keoscluster_settled(cluster_name, timeout_minutes=30):
    '''Wait for cluster-operator to finish its own reconcile — not the same as the infrastructure
    having converged. Patching the next step mid-reconcile left the control plane stuck once
    (live 2026-09-18, 19s before it closed); Azure buys the same margin with a fixed sleep.'''

    print(f"[INFO] Waiting for the KeosCluster to settle before the next step (timeout {timeout_minutes}m):", end=" ", flush=True)
    deadline = time.time() + timeout_minutes * 60
    while time.time() < deadline:
        output, _ = run_command(
            f"{S.kubectl} get keoscluster {cluster_name} -n cluster-{cluster_name} "
            f"-o jsonpath='{{.status.ready}} {{.status.phase}}'",
            allow_errors=True
        )
        if output.strip() == "true Provisioned":
            print("OK")
            return
        time.sleep(15)
    raise Exception(f"Timed out after {timeout_minutes}m waiting for the KeosCluster to settle")

def start_keoscluster_controller():
    '''Start the KEOSCluster controller'''

    try:
        print("[INFO] Starting keoscluster-controller-manager deployment:", end =" ", flush=True)

        run_command(f"{S.kubectl} scale deployment -n kube-system keoscluster-controller-manager --replicas=2")
        run_command(f"{S.kubectl} wait --for=condition=Available deployment/keoscluster-controller-manager -n kube-system --timeout=300s")
        print("OK")

    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error starting the KEOSCluster controller: {e}")

        raise e
