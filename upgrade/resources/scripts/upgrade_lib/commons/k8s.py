# -*- coding: utf-8 -*-
"""commons.k8s — moved verbatim from upgrade-provisioner.py (PLT-4916)."""

import os
import json
from upgrade_lib.commons.shell import execute_command, run_command
from upgrade_lib import state as S

def scale_cluster_autoscaler(replicas, dry_run):
    '''Scale cluster-autoscaler deployment'''

    command = S.kubectl + " get deploy cluster-autoscaler-clusterapi-cluster-autoscaler -n kube-system --ignore-not-found -o=jsonpath='{.spec.replicas}'"
    output = execute_command(command, False, False)

    if output.strip() == "":
        print("[INFO] Cluster autoscaler not deployed: SKIP")
        return

    current_replicas = int(output)

    if current_replicas == replicas:
        print("[INFO] Cluster autoscaler already at desired replicas: SKIP")
        return

    scaling_type = "Scaling down" if current_replicas > replicas else "Scaling up"
    print(f"[INFO] {scaling_type} cluster autoscaler replicas:", end=" ", flush=True)

    if dry_run:
        print("DRY-RUN")
        return

    # Scale
    command = S.kubectl + f" scale deploy cluster-autoscaler-clusterapi-cluster-autoscaler -n kube-system --replicas={replicas}"
    execute_command(command, False, False)

    # Wait until ready
    command = S.kubectl + " wait deployment cluster-autoscaler-clusterapi-cluster-autoscaler -n kube-system --for=condition=Available --timeout=5m"
    execute_command(command, False, False)

    print("OK")

def get_keos_cluster_cluster_config():
    '''Get the KeosCluster and ClusterConfig objects'''

    try:
        keoscluster_list_output, err = run_command(S.kubectl + " get keoscluster -A -o json")
        keos_cluster = json.loads(keoscluster_list_output)["items"][0]
        clusterconfig_list_output, err = run_command(S.kubectl + " get clusterconfig -A -o json")
        cluster_config = json.loads(clusterconfig_list_output)["items"][0]
        return keos_cluster, cluster_config
    except Exception as e:
        print(f"[ERROR] {e}.")
        raise e

def preflight_cluster_health_checks(keos_cluster, cluster_name, provider):
    '''Verify the cluster is healthy before any mutating step — best-effort, skips checks it can't query.'''
    print("[INFO] Running pre-flight cluster health checks:")
    problems = []

    # 1. KeosCluster.status.ready
    ready = keos_cluster.get("status", {}).get("ready")
    if ready is not True:
        problems.append(f"KeosCluster.status.ready = {ready} (expected true) — the cluster is not in a stable reconciled state")

    # 2. Unhealthy pods cluster-wide
    pods_json, _ = run_command(f"{S.kubectl} get pods -A -o json", allow_errors=True)
    if pods_json:
        try:
            pods = json.loads(pods_json).get("items", [])
        except Exception:
            pods = []
        for pod in pods:
            name = f"{pod['metadata']['namespace']}/{pod['metadata']['name']}"
            phase = pod.get("status", {}).get("phase")
            if phase not in ("Running", "Succeeded", "Pending"):
                problems.append(f"Pod {name}: phase={phase}")
                continue
            bad_reasons = [
                cs["state"]["waiting"]["reason"]
                for cs in pod.get("status", {}).get("containerStatuses", [])
                if "waiting" in cs.get("state", {}) and cs["state"]["waiting"].get("reason") in
                   ("CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError")
            ]
            if bad_reasons:
                problems.append(f"Pod {name}: {', '.join(bad_reasons)}")

    # 3. CAPI / CAPX controller-manager HA replicas (Skind provider.go:1207/1275 expect 2)
    deployments = [("capi-system", "capi-controller-manager")]
    if provider == "aws":
        deployments.append(("capa-system", "capa-controller-manager"))
    elif provider == "gcp":
        deployments.append(("capg-system", "capg-controller-manager"))
    elif provider == "azure":
        deployments.append(("capz-system", "capz-controller-manager"))
        deployments.append(("capi-kubeadm-bootstrap-system", "capi-kubeadm-bootstrap-controller-manager"))
        deployments.append(("capi-kubeadm-control-plane-system", "capi-kubeadm-control-plane-controller-manager"))
    for namespace, deploy in deployments:
        available, err = run_command(
            f"{S.kubectl} -n {namespace} get deploy {deploy} -o jsonpath='{{.status.availableReplicas}}'",
            allow_errors=True
        )
        if "NotFound" in (err or ""):
            continue
        if available.strip() != "2":
            problems.append(f"Deployment {namespace}/{deploy}: availableReplicas={available.strip() or '0'} (expected 2 for HA) — a node hosting the only replica can deadlock draining during this upgrade")

    # 4. Machine objects stuck outside the normal lifecycle phases
    machines_json, _ = run_command(f"{S.kubectl} get machine -n cluster-{cluster_name} -o json", allow_errors=True)
    if machines_json:
        try:
            machines = json.loads(machines_json).get("items", [])
        except Exception:
            machines = []
        for m in machines:
            phase = m.get("status", {}).get("phase")
            if phase not in ("Running", "Provisioning", "Pending", "ScalingUp"):
                problems.append(f"Machine {m['metadata']['name']}: phase={phase} — resolve before upgrading, the upgrade will add more churn on top of this")

    # 5. Paused MachineDeployments (informational only — pausing is sometimes intentional)
    mds_json, _ = run_command(f"{S.kubectl} get machinedeployment -n cluster-{cluster_name} -o json", allow_errors=True)
    if mds_json:
        try:
            mds = json.loads(mds_json).get("items", [])
        except Exception:
            mds = []
        paused = [md["metadata"]["name"] for md in mds if md.get("spec", {}).get("paused")]
        if paused:
            print(f"    [INFO] Paused MachineDeployments (template changes won't roll out until unpaused): {', '.join(paused)}")

    # 6. Stale ENIConfig security group (PLT-4509 legacy, fixed in Skind commit 5010a9de).
    #    MachineDeployment nodes self-heal via CAPA's ensureSecurityGroups; MachinePool has no
    #    equivalent, so a new one would inherit the stale SG. Fixes the object here (non-blocking).
    if provider == "aws" and get_pods_cidr(keos_cluster):
        real_sg, _ = run_command(
            f"aws eks describe-cluster --name {cluster_name} "
            "--query cluster.resourcesVpcConfig.clusterSecurityGroupId --output text",
            allow_errors=True
        )
        real_sg = (real_sg or "").strip()
        if real_sg:
            eniconfig_json, _ = run_command(f"{S.kubectl} get eniconfig -o json", allow_errors=True)
            if eniconfig_json:
                try:
                    eniconfigs = json.loads(eniconfig_json).get("items", [])
                except Exception:
                    eniconfigs = []
                for ec in eniconfigs:
                    ec_name = ec["metadata"]["name"]
                    current_sgs = ec.get("spec", {}).get("securityGroups", [])
                    if current_sgs and current_sgs[0] != real_sg:
                        print(f"    [WARN] ENIConfig {ec_name} has a stale security group ({current_sgs[0]}, expected {real_sg}) — fixing")
                        run_command(
                            f'{S.kubectl} patch eniconfig {ec_name} --type=merge '
                            f'-p \'{{"spec":{{"securityGroups":["{real_sg}"]}}}}\'',
                            allow_errors=True
                        )

    # 7. Missing kubeadm RBAC binding for the apiserver's kubelet client (Azure VMs only, kubeadm-based
    #    CP). Self-corrects, not blocking.
    if provider == "azure":
        binding_out, _ = run_command(
            f"{S.kubectl} get clusterrolebinding kubeadm:apiserver-kubelet-client -o name", allow_errors=True
        )
        if not binding_out.strip():
            role_out, _ = run_command(
                f"{S.kubectl} get clusterrole system:kubelet-api-admin -o name", allow_errors=True
            )
            if role_out.strip():
                import tempfile
                print("    [WARN] ClusterRoleBinding kubeadm:apiserver-kubelet-client is missing "
                      "(kubeadm bootstrap step) — without it CAPI's etcd health checks can hang "
                      "a k8s_version bump indefinitely. Creating it now.")
                with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
                    f.write(
                        "apiVersion: rbac.authorization.k8s.io/v1\n"
                        "kind: ClusterRoleBinding\n"
                        "metadata:\n"
                        "  name: kubeadm:apiserver-kubelet-client\n"
                        "roleRef:\n"
                        "  apiGroup: rbac.authorization.k8s.io\n"
                        "  kind: ClusterRole\n"
                        "  name: system:kubelet-api-admin\n"
                        "subjects:\n"
                        "  - kind: User\n"
                        "    name: kube-apiserver-kubelet-client\n"
                        "    apiGroup: rbac.authorization.k8s.io\n"
                    )
                    manifest_path = f.name
                run_command(f"{S.kubectl} apply -f {manifest_path}", allow_errors=True)
                os.unlink(manifest_path)
            else:
                print("    [WARN] ClusterRoleBinding kubeadm:apiserver-kubelet-client is missing, but "
                      "the built-in role system:kubelet-api-admin is also missing — skipping auto-fix "
                      "(unexpected apiserver bootstrap state, needs manual investigation)")

    if not problems:
        print("    OK: no issues found")
        return

    print(f"    [WARN] {len(problems)} issue(s) found:")
    for p in problems:
        print(f"      - {p}")

    if S.config["skip_preflight_checks"]:
        print("    [WARN] --skip-preflight-checks set: continuing anyway")
        return
    if S.config["dry_run"]:
        print("    [INFO] dry-run: would abort here without --skip-preflight-checks")
        return

    raise Exception(f"{len(problems)} pre-flight issue(s) found — fix them first, or re-run with --skip-preflight-checks if you accept the risk")

def get_deploy_version(deploy, namespace, container):
    '''Get the version of a deployment'''

    command = f"{S.kubectl} -n " + namespace + " get deploy " + deploy + " -o json  | jq -r '.spec.template.spec.containers[].image' | grep '" + container + "' | cut -d: -f2"
    output = execute_command(command, False, False)
    return output.split("@")[0]

def update_annotation_label(annotation_label_key, annotation_label_value, resources, type="annotation"):
    '''Update the annotation or label of a resource'''

    for resource in resources:
        kind = resource["kind"]
        name = resource["name"]
        ns = resource.get("namespace")
        action_type = "annotate"
        if type == "label":
            action_type = "label"
        try:
            command = f"{S.kubectl} get {kind} {name} "
            if ns:
                command = command + f" -n {ns}"
            output, err = run_command(command, allow_errors=True)
            if "not found" in err.lower():

                continue
        except Exception as e:
            print("FAILED")
            print(f"[ERROR] Error checking the existence of {kind} {name}: {e}")
            return

        command = f"{S.kubectl} {action_type} {kind} {name} {annotation_label_key}={annotation_label_value} --overwrite "
        if ns:
            command = command + f" -n {ns}"
        output, err = run_command(command)

def get_keos_registry_url(keos_cluster):
    '''Get the Keos registry URL'''

    docker_registries = keos_cluster["spec"]["docker_registries"]
    for registry in docker_registries:
        if registry.get("keos_registry", False):
            return registry["url"]
    return ""

def is_ecr_pull_through_enabled(keos_cluster):
    '''Return True if ECR pull-through cache is enabled in the cluster or forced via --ecr-pull-through flag'''

    if S.config.get("ecr_pull_through", False):
        return True
    for registry in keos_cluster["spec"].get("docker_registries", []):
        if registry.get("ecr_pull_through_cache_enabled", False):
            return True
    return False

def get_pods_cidr(keos_cluster):
    '''Get the pods CIDR'''

    try:
        return keos_cluster["spec"]["networks"]["pods_cidr"]
    except KeyError:
        return ""

def is_private_registry_enabled(cluster_config):
    '''Return the effective private registry setting'''

    return cluster_config.get("spec", {}).get("private_registry", False) or S.config.get("private", False)

def is_private_helm_repo_enabled(cluster_config):
    '''Return the effective private Helm repository setting'''
    return cluster_config.get("spec", {}).get("private_helm_repo", False) or S.config.get("private", False)
