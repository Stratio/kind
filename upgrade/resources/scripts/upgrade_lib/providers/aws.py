# -*- coding: utf-8 -*-
"""providers.aws — moved verbatim from upgrade-provisioner.py (PLT-4916)."""

import os
import sys
import json
import subprocess
import time
from upgrade_lib.commons.shell import execute_command, run_command
from upgrade_lib import state as S

def patch_clusterrole_aws_node(dry_run):
    '''Patch aws-node ClusterRole'''

    aws_node_clusterrole_name = "aws-node"
    print("[INFO] Modifying aws-node ClusterRole:", end =" ", flush=True)
    if not dry_run:
        command = f"{S.kubectl} get clusterrole -o json {aws_node_clusterrole_name} | jq -r '.rules'"
        cluster_role_rules_output = execute_command(command, False, False)

        try:
            cluster_role_rules = json.loads(cluster_role_rules_output)
        except json.JSONDecodeError as e:
            print(f"[ERROR] Failed to parse ClusterRole rules as JSON: {e}")
            sys.exit(1)

        rule_pods_index = next((i for i, rule in enumerate(cluster_role_rules) if 'pods' in rule.get('resources', [])), None)
        if rule_pods_index is not None:
            verbs = cluster_role_rules[rule_pods_index].get('verbs', [])
            if 'patch' not in verbs:
                patch = [
                    {
                        "op": "add",
                        "path": f"/rules/{rule_pods_index}/verbs/-",
                        "value": "patch"
                    }
                ]
                patch_command = f"{S.kubectl} patch clusterrole {aws_node_clusterrole_name} --type=json -p='{json.dumps(patch)}'"
                execute_command(patch_command, False, True)
            else:
                print("SKIP")
        else:
            print(f"[ERROR] Pods resource not found in the ClusterRole {aws_node_clusterrole_name}")
            sys.exit(1)
    else:
        print("DRY-RUN")

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
