#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''Run a command (e.g. clusterawsadm) with the AWS credentials of the secrets file, ignoring any mounted AWS profile'''

import argparse
import os
import subprocess
import sys
from ansible_vault import Vault

# Credential sources that would take precedence over, or be used instead of, the secrets file keys.
AWS_CREDENTIAL_ENV_VARS = [
    "AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN",
    "AWS_ROLE_ARN", "AWS_ROLE_SESSION_NAME", "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_CONTAINER_CREDENTIALS_FULL_URI", "AWS_CONTAINER_AUTHORIZATION_TOKEN",
]

def parse_args():
    parser = argparse.ArgumentParser(
        description="Run a command with the AWS access key of the secrets file (the identity create cluster uses for clusterawsadm)",
        usage="%(prog)s -p VAULT_PASSWORD [-s SECRETS] -- COMMAND [ARGS...]")
    parser.add_argument("-p", "--vault-password", required=True, help="Set the vault password to decrypt secrets")
    parser.add_argument("-s", "--secrets", default="secrets.yml", help="Set the secrets file for decrypting secrets")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to run, after --")
    args = parser.parse_args()
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("missing the command to run after --")
    return args

def secrets_aws_env(secrets_file, vault_password):
    '''Environment whose only AWS credentials are the secrets file access key (role_arn not assumed, as in create cluster)'''

    try:
        with open(secrets_file) as f:
            vault_secrets_data = Vault(vault_password).load(f.read())
        aws_creds = vault_secrets_data["secrets"]["aws"]["credentials"]
        access_key, secret_key, region = aws_creds["access_key"], aws_creds["secret_key"], aws_creds["region"]
    except Exception as e:
        print(f"[ERROR] Could not read the AWS credentials from {secrets_file}: {type(e).__name__}")
        sys.exit(1)

    env = {k: v for k, v in os.environ.items() if k not in AWS_CREDENTIAL_ENV_VARS}
    env.update({
        "AWS_ACCESS_KEY_ID": access_key,
        "AWS_SECRET_ACCESS_KEY": secret_key,
        "AWS_REGION": region,
        "AWS_DEFAULT_REGION": region,
        "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
        "AWS_CONFIG_FILE": "/dev/null",
        "AWS_PAGER": "",
    })
    return env

def main():
    args = parse_args()
    env = secrets_aws_env(args.secrets, args.vault_password)

    identity = subprocess.run(["aws", "sts", "get-caller-identity", "--query", "Arn", "--output", "text"],
                              env=env, capture_output=True, text=True)
    if identity.returncode != 0:
        print("[ERROR] AWS rejected the credentials of the secrets file")
        print(identity.stderr.strip())
        sys.exit(1)
    print(f"[INFO] Running with the AWS identity of the secrets file: {identity.stdout.strip()}", flush=True)

    sys.exit(subprocess.run(args.command, env=env).returncode)

if __name__ == '__main__':
    main()
