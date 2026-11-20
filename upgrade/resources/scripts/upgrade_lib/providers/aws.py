# -*- coding: utf-8 -*-
"""providers.aws — moved verbatim from upgrade-provisioner.py (PLT-4916)."""

import os
import sys
import json
import subprocess
import time
import yaml
from upgrade_lib.commons.keoscluster import wait_for_keoscluster_settled
from upgrade_lib.commons.network import ensure_imds_allow
from upgrade_lib.commons.shell import execute_command, run_command
from upgrade_lib.versions import AWS_NODE_ANNOTATE_POD_IP_RBAC, CLUSTER_AUTOSCALER_AWS_09_RELEASE, CLUSTER_AUTOSCALER_AWS_POLICY_DOCUMENT, CLUSTER_AUTOSCALER_AWS_POLICY_NAME, EKS_NODEGROUP_ROLE, IMDS_ALLOW_CLUSTER_AUTOSCALER_AWS_SELECTOR, IMDS_ALLOW_GNP, IMDS_DENY_GNP
from upgrade_lib import state as S

def ensure_aws_node_annotate_pod_ip_rbac(dry_run):
    '''Ensure the aws-node-annotate-pod-ip RBAC that create cluster applies (the vpc-cni addon re-apply drops patch on pods from aws-node)'''

    print("[INFO] Ensuring aws-node-annotate-pod-ip ClusterRole and ClusterRoleBinding:", end=" ", flush=True)
    command = S.kubectl + " apply -f - <<'EOF'\n" + AWS_NODE_ANNOTATE_POD_IP_RBAC + "\nEOF"
    execute_command(command, dry_run)

def ensure_mp_role_name(cluster_name, dry_run):
    '''Set KeosCluster spec.control_plane.aws.mp_role_name to the clusterawsadm EKS nodegroup role when unset; cluster-operator uses it as roleName of every AWSManagedMachinePool'''

    print("[INFO] Ensuring KeosCluster spec.control_plane.aws.mp_role_name:", end=" ", flush=True)
    current, _ = run_command(f"{S.kubectl} -n cluster-{cluster_name} get keoscluster {cluster_name} -o jsonpath='{{.spec.control_plane.aws.mp_role_name}}'", allow_errors=True)
    if current.strip():
        print(f"SKIP (already set: {current.strip()})")
        return
    manual_patch = (f"kubectl -n cluster-{cluster_name} patch keoscluster {cluster_name} --type=merge -p "
                    f"'{{\"spec\":{{\"control_plane\":{{\"aws\":{{\"mp_role_name\":\"{EKS_NODEGROUP_ROLE}\"}}}}}}}}'")
    role, err = run_command(f"aws iam get-role --role-name {EKS_NODEGROUP_ROLE} --query Role.RoleName --output text", allow_errors=True)
    if role.strip() != EKS_NODEGROUP_ROLE:
        print("WARN")
        print(f"[WARN] IAM role {EKS_NODEGROUP_ROLE} not found or not readable: {err.strip()}")
        print(f"[WARN] To give MachinePools a fixed role, create it (clusterawsadm bootstrap iam create-cloudformation-stack) and run: {manual_patch}")
        return
    if dry_run:
        print("DRY-RUN")
        return
    print("role found")
    # The KeosCluster webhook rejects updates until the cluster is ready (e.g. right after a k8s_version bump)
    try:
        wait_for_keoscluster_settled(cluster_name)
    except Exception as e:
        print("TIMEOUT")
        print(f"[WARN] {e}: mp_role_name not set. Once the KeosCluster is ready, run: {manual_patch}")
        return
    print("[INFO] Setting KeosCluster spec.control_plane.aws.mp_role_name:", end=" ", flush=True)
    patch = json.dumps({"spec": {"control_plane": {"aws": {"mp_role_name": EKS_NODEGROUP_ROLE}}}})
    out, err = run_command(f"{S.kubectl} -n cluster-{cluster_name} patch keoscluster {cluster_name} --type=merge -p '{patch}'", allow_errors=True)
    if "patched" not in out:
        print("WARN")
        print(f"[WARN] Could not set mp_role_name: {err.strip()}")
        print(f"[WARN] Run it by hand: {manual_patch}")
        return
    print("OK")

def configure_aws_credentials(vault_secrets_data):
    print("[INFO] Configuring AWS CLI credentials", end=" ", flush=True)

    aws_creds = vault_secrets_data['secrets']['aws']['credentials']
    aws_access_key = aws_creds['access_key']
    aws_secret_key = aws_creds['secret_key']
    aws_region = aws_creds['region']
    role_arn = aws_creds.get('role_arn')

    # Disable AWS pager inside containers
    os.environ["AWS_PAGER"] = ""

    # Base credentials
    os.environ["AWS_ACCESS_KEY_ID"] = aws_access_key
    os.environ["AWS_SECRET_ACCESS_KEY"] = aws_secret_key
    os.environ["AWS_DEFAULT_REGION"] = aws_region

    # If role_arn exists → assume role
    if role_arn:
        assume_cmd = [
            "aws", "sts", "assume-role",
            "--role-arn", role_arn,
            "--role-session-name", "upgrade-session"
        ]

        result = subprocess.run(assume_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print("FAILED")
            print(result.stderr)
            sys.exit(1)

        creds = json.loads(result.stdout)["Credentials"]

        os.environ["AWS_ACCESS_KEY_ID"] = creds["AccessKeyId"]
        os.environ["AWS_SECRET_ACCESS_KEY"] = creds["SecretAccessKey"]
        os.environ["AWS_SESSION_TOKEN"] = creds["SessionToken"]

    print("OK")

def wait_for_eks_worker_convergence(cluster_name, target_minor, stall_minutes=None):
    '''AWS only: the k8s_version bump returns once the EKS control plane reaches target_minor, but
    the MachinePool nodegroups roll afterwards (live 2026-10-01: ~12 min later). Wait for every
    nodegroup to be ACTIVE on target_minor and every Node's kubelet to match.'''

    if stall_minutes is None:
        stall_minutes = S.config["node_convergence_timeout"]
    print(f"[INFO] Waiting for every EKS nodegroup and node to reach {target_minor} (timeout {stall_minutes}m without progress):", end=" ", flush=True)
    if S.config["dry_run"]:
        print("DRY-RUN")
        return
    best_progress = -1
    deadline = time.time() + stall_minutes * 60
    while time.time() < deadline:
        progress = best_progress
        try:
            # Names from the AWSManagedMachinePools, not `aws eks list-nodegroups`: the deploying IAM
            # user may lack eks:ListNodegroups (live 2026-10-02: AccessDenied, the wait never ended).
            nodegroups_output, _ = run_command(
                f"{S.kubectl} get awsmanagedmachinepools -n cluster-{cluster_name} "
                f"-o jsonpath='{{range .items[*]}}{{.spec.eksNodegroupName}}{{\"\\n\"}}{{end}}'",
                allow_errors=True
            )
            nodegroups = [line.strip() for line in nodegroups_output.splitlines() if line.strip()]
            converged_nodegroups = 0
            for nodegroup in nodegroups:
                output, _ = run_command(
                    f"aws eks describe-nodegroup --cluster-name {cluster_name} --nodegroup-name {nodegroup} "
                    f"--query 'nodegroup.[status,version]' --output text",
                    allow_errors=True
                )
                if output.split() == ["ACTIVE", target_minor]:
                    converged_nodegroups += 1
            nodes_output, _ = run_command(f"{S.kubectl} get nodes -o json", allow_errors=True)
            nodes = json.loads(nodes_output).get("items", [])
            converged_nodes = sum(
                1 for node in nodes
                if node.get("status", {}).get("nodeInfo", {}).get("kubeletVersion", "").startswith(f"v{target_minor}.")
            )
            progress = converged_nodegroups + converged_nodes
            converged = converged_nodegroups == len(nodegroups) and bool(nodes) and converged_nodes == len(nodes)
        except (ValueError, TypeError, AttributeError):
            converged = False
        if converged:
            print("OK")
            return
        if progress > best_progress:
            best_progress = progress
            deadline = time.time() + stall_minutes * 60
        time.sleep(30)
    raise Exception(f"No progress for {stall_minutes}m waiting for EKS nodegroups and nodes to reach {target_minor}")

def run_aws_iam(command):
    '''Run an AWS CLI IAM command, retrying up to 3 times, 5 s apart, a failure other than AccessDenied, NoSuchEntity or EntityAlreadyExists'''

    for attempt in range(3):
        out, err = run_command(command, allow_errors=True)
        if not err.strip() or any(code in err for code in ("AccessDenied", "NoSuchEntity", "EntityAlreadyExists")) or attempt == 2:
            return out, err
        time.sleep(5)

def ensure_cluster_autoscaler_aws_iam(dry_run):
    '''Create the cluster-autoscaler aws policy when missing and attach it to the MachinePool node role, outside the IAM stack, as create cluster'''

    print(f"[INFO] Ensuring IAM policy {CLUSTER_AUTOSCALER_AWS_POLICY_NAME} on the role {EKS_NODEGROUP_ROLE}:", end=" ", flush=True)
    account, err = run_command("aws sts get-caller-identity --query Account --output text", allow_errors=True)
    if not account.strip():
        print("FAILED")
        print(f"[ERROR] Could not get the AWS account id: {err.strip()}")
        sys.exit(1)
    policy_arn = f"arn:aws:iam::{account.strip()}:policy/{CLUSTER_AUTOSCALER_AWS_POLICY_NAME}"
    manual = (f"aws iam create-policy --policy-name {CLUSTER_AUTOSCALER_AWS_POLICY_NAME} --policy-document '{CLUSTER_AUTOSCALER_AWS_POLICY_DOCUMENT}' (if missing); "
              f"aws iam attach-role-policy --role-name {EKS_NODEGROUP_ROLE} --policy-arn {policy_arn}")
    get_policy = f"aws iam get-policy --policy-arn {policy_arn} --query Policy.Arn --output text"
    out, err = run_aws_iam(get_policy)
    actions = []
    if out.strip() != policy_arn:
        if "NoSuchEntity" not in err:
            print("FAILED")
            print(f"[ERROR] Could not read the IAM policy {policy_arn}: {err.strip()}")
            print(f"[ERROR] Grant the permission or run it by hand, then relaunch: {manual}")
            sys.exit(1)
        actions.append(("create-policy", f"aws iam create-policy --policy-name {CLUSTER_AUTOSCALER_AWS_POLICY_NAME} --policy-document '{CLUSTER_AUTOSCALER_AWS_POLICY_DOCUMENT}'"))
    out, err = run_aws_iam(f"aws iam list-attached-role-policies --role-name {EKS_NODEGROUP_ROLE} --query 'AttachedPolicies[].PolicyArn' --output text")
    if err.strip():
        print("FAILED")
        print(f"[ERROR] Could not list the policies of the IAM role {EKS_NODEGROUP_ROLE}: {err.strip()}")
        print(f"[ERROR] Every EKS MachinePool uses this role (clusterawsadm bootstrap iam create-cloudformation-stack creates it). Then relaunch: {manual}")
        sys.exit(1)
    if policy_arn not in out.split():
        actions.append((f"attach to {EKS_NODEGROUP_ROLE}", f"aws iam attach-role-policy --role-name {EKS_NODEGROUP_ROLE} --policy-arn {policy_arn}"))
    if not actions:
        print("SKIP (already attached)")
        return
    if dry_run:
        print("DRY-RUN (" + ", ".join(name for name, _ in actions) + ")")
        return
    for name, command in actions:
        _, err = run_aws_iam(command)
        if name == "create-policy" and "EntityAlreadyExists" in err:
            # Created in the meantime by another run in the same account
            out, err = run_aws_iam(get_policy)
            if out.strip() == policy_arn:
                continue
            err = err.strip() or f"EntityAlreadyExists, but get-policy returned '{out.strip()}'"
        if err.strip():
            print("FAILED")
            print(f"[ERROR] IAM {name} failed: {err.strip()}")
            print(f"[ERROR] Grant the permission or run it by hand, then relaunch: {manual}")
            sys.exit(1)
    print("OK (" + ", ".join(name for name, _ in actions) + ")")

def ensure_imds_allow_cluster_autoscaler_aws(dry_run):
    '''Add the cluster-autoscaler aws pods to the IMDS allow GlobalNetworkPolicy, as create cluster'''

    ensure_imds_allow(IMDS_ALLOW_GNP, IMDS_DENY_GNP, IMDS_ALLOW_CLUSTER_AUTOSCALER_AWS_SELECTOR, "cluster-autoscaler aws", dry_run)

def remove_cluster_autoscaler_aws_09_release(dry_run):
    '''Delete the second CA release that the 0.9 upgrade adds for MachinePools: on 0.10 the release cluster-autoscaler becomes the only one, with cloudProvider aws'''

    print(f"[INFO] Removing the HelmRelease {CLUSTER_AUTOSCALER_AWS_09_RELEASE} (release cluster-autoscaler takes over with cloudProvider aws):", end=" ", flush=True)
    _, err = run_command(f"{S.kubectl} get helmrelease {CLUSTER_AUTOSCALER_AWS_09_RELEASE} -n kube-system -o name", allow_errors=True)
    if "NotFound" in err:
        print("SKIP (not installed)")
        return
    if err.strip():
        print("FAILED")
        print(f"[ERROR] Could not read the HelmRelease {CLUSTER_AUTOSCALER_AWS_09_RELEASE}: {err.strip()}")
        sys.exit(1)
    from upgrade_lib.commons.helm import helmrelease_is_suspended, set_helmrelease_suspend  # here: commons.helm imports this module
    suspended = helmrelease_is_suspended(CLUSTER_AUTOSCALER_AWS_09_RELEASE, "kube-system")
    if dry_run:
        print("DRY-RUN (resume, then delete)" if suspended else "DRY-RUN")
        return
    # helm-controller skips the uninstall of a suspended HelmRelease
    if suspended:
        set_helmrelease_suspend(CLUSTER_AUTOSCALER_AWS_09_RELEASE, "kube-system", False)
    # helm-controller uninstalls the release (Deployment, RBAC) before the HelmRelease is gone
    run_command(f"{S.kubectl} delete helmrelease {CLUSTER_AUTOSCALER_AWS_09_RELEASE} -n kube-system --wait=true --timeout=5m")
    # The values ConfigMaps, plus the status ConfigMap and the leader-election Lease that release ran with (not Helm objects)
    for cm in (f"00-{CLUSTER_AUTOSCALER_AWS_09_RELEASE}-helm-chart-default-values", f"02-{CLUSTER_AUTOSCALER_AWS_09_RELEASE}-helm-chart-override-values", f"{CLUSTER_AUTOSCALER_AWS_09_RELEASE}-status"):
        run_command(f"{S.kubectl} delete configmap {cm} -n kube-system --ignore-not-found", allow_errors=True)
    run_command(f"{S.kubectl} delete lease {CLUSTER_AUTOSCALER_AWS_09_RELEASE} -n kube-system --ignore-not-found", allow_errors=True)
    print("OK")

def update_cluster_autoscaler_aws_values(values_file):
    '''Release cluster-autoscaler with cloudProvider aws, as create cluster renders it for EKS: MachinePool ASGs found by the
    cluster tags, no CAPI label filter. Kept as is when the cluster was already created with the CA aws.'''

    try:
        with open(values_file, 'r') as file:
            values = yaml.safe_load(file) or {}

        name = S.keos_cluster["metadata"]["name"]
        values["cloudProvider"] = "aws"
        values["awsRegion"] = S.keos_cluster["spec"]["region"]
        auto_discovery = values.setdefault("autoDiscovery", {})
        auto_discovery["clusterName"] = name
        auto_discovery["tags"] = ["k8s.io/cluster-autoscaler/enabled", f"k8s.io/cluster-autoscaler/{name}"]
        auto_discovery.pop("labels", None)

        with open(values_file, 'w') as file:
            yaml.safe_dump(values, file, default_flow_style=False)

    except Exception as e:
        print(f"An error occurred: {e}")
