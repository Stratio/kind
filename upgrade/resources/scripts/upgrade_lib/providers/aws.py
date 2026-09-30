# -*- coding: utf-8 -*-
"""providers.aws — moved verbatim from upgrade-provisioner.py (PLT-4916)."""

import os
import sys
import json
import subprocess
from upgrade_lib.commons.shell import execute_command
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
