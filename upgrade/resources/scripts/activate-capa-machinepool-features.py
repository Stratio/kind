#!/usr/bin/env python3
# -*- coding: utf-8 -*-

##############################################################
# Author: Stratio Clouds <clouds-integration@stratio.com>    #
# Supported provisioner versions: 0.9.0                      #
# Supported cloud providers:                                 #
#   - EKS (AWS managed)                                      #
##############################################################

__version__ = "0.1.0"

import argparse
import json
import os
import re
import subprocess
import sys

from ansible_vault import Vault

CAPA_NAMESPACE = "capa-system"
CAPA_DEPLOYMENT = "capa-controller-manager"
CAPA_CONTAINER_INDEX = 0

# Minimum versions required for MachinePool support — upgrade-provisioner.py is
# responsible for getting the cluster there, this script only validates it already is.
MIN_CLUSTER_OPERATOR_VERSION = "0.7.0"
MIN_CAPA_VERSION = "v2.9.3"

# Feature gates required for MachinePool support
REQUIRED_FEATURE_GATES = {
    "MachinePool": "true",
    "EKSAllowAddRoles": "true",
}

kubectl = "kubectl"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def run_command(command, allow_errors=False):
    '''Run a shell command, return (output, returncode).'''
    status, output = subprocess.getstatusoutput(command)
    if status != 0 and not allow_errors:
        print("FAILED")
        print(f"[ERROR] {output}")
        sys.exit(1)
    return output, status


def _version_gte(version, minimum):
    '''
    Return True if version >= minimum, comparing semver-style (strips leading "v").

    Tolerates dev/pre-release suffixes (e.g. "0.7.0-PLT-4265-01.5") by taking the
    leading numeric prefix of each of the first 3 dot-separated components instead
    of discarding any component that is not a pure digit string — a naive
    `if x.isdigit()` filter would silently drop "0-PLT-4265-01" entirely, truncating
    the version to 2 components and comparing it incorrectly against a 3-component
    minimum.
    '''
    def parse(v):
        parts = v.lstrip("v").split(".")[:3]
        result = []
        for p in parts:
            match = re.match(r"\d+", p)
            result.append(int(match.group()) if match else 0)
        return result
    try:
        return parse(version) >= parse(minimum)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Prerequisite validation
# ---------------------------------------------------------------------------

def validate_prerequisites():
    '''Validate that the cluster is ready to have MachinePool support activated.'''

    print("[INFO] Validating prerequisites...")

    # 1. Detect provider and managed mode
    print("[INFO] Checking cluster provider:", end=" ", flush=True)
    cmd = kubectl + " get keoscluster -A -o jsonpath='{.items[0].spec.infra_provider}'"
    provider, _ = run_command(cmd, allow_errors=True)
    provider = provider.strip().strip("'")
    if provider != "aws":
        print(f"FAILED\n[ERROR] Provider '{provider}' is not supported. Only 'aws' is supported.")
        sys.exit(1)
    print(f"OK ({provider})")

    print("[INFO] Checking managed control plane:", end=" ", flush=True)
    cmd = kubectl + " get keoscluster -A -o jsonpath='{.items[0].spec.control_plane.managed}'"
    managed, _ = run_command(cmd, allow_errors=True)
    managed = managed.strip().strip("'")
    if managed != "true":
        print("FAILED\n[ERROR] Only EKS managed clusters (control_plane.managed=true) are supported.")
        sys.exit(1)
    print("OK")

    # 2. Check cluster-operator minimum version
    print(f"[INFO] Checking cluster-operator version (>= {MIN_CLUSTER_OPERATOR_VERSION}):", end=" ", flush=True)
    cmd = kubectl + " get deployment keoscluster-controller-manager -n kube-system -o jsonpath='{.spec.template.spec.containers[0].image}'"
    co_image, _ = run_command(cmd, allow_errors=True)
    co_image = co_image.strip().strip("'")
    co_version = co_image.split(":")[-1] if ":" in co_image else ""
    if not co_version:
        print(f"FAILED\n[ERROR] Could not determine cluster-operator version from image '{co_image}'.")
        sys.exit(1)
    if not _version_gte(co_version, MIN_CLUSTER_OPERATOR_VERSION):
        print(f"FAILED\n[ERROR] cluster-operator version '{co_version}' is below minimum '{MIN_CLUSTER_OPERATOR_VERSION}'. "
              "Run upgrade-provisioner.py first.")
        sys.exit(1)
    print(f"OK ({co_version})")

    # 3. Check CAPA minimum version
    print(f"[INFO] Checking CAPA version (>= {MIN_CAPA_VERSION}):", end=" ", flush=True)
    cmd = kubectl + f" get deployment {CAPA_DEPLOYMENT} -n {CAPA_NAMESPACE} -o jsonpath='{{.spec.template.spec.containers[0].image}}'"
    capa_image, _ = run_command(cmd, allow_errors=True)
    capa_image = capa_image.strip().strip("'")
    capa_version = capa_image.split(":")[-1] if ":" in capa_image else ""
    # Normalize: strip any suffix after the semver (e.g. v2.9.2-keos.1 → v2.9.2)
    capa_semver = capa_version.split("-")[0] if "-" in capa_version else capa_version
    if not capa_semver:
        print(f"FAILED\n[ERROR] Could not determine CAPA version from image '{capa_image}'.")
        sys.exit(1)
    if not _version_gte(capa_semver, MIN_CAPA_VERSION):
        print(f"FAILED\n[ERROR] CAPA version '{capa_version}' is below minimum '{MIN_CAPA_VERSION}'. "
              "Run upgrade-provisioner.py first to upgrade CAPA.")
        sys.exit(1)
    print(f"OK ({capa_version})")

    # 4. Check KeosCluster status.ready
    print("[INFO] Checking KeosCluster status.ready:", end=" ", flush=True)
    cmd = kubectl + " get keoscluster -A -o jsonpath='{.items[0].status.ready}'"
    ready, _ = run_command(cmd, allow_errors=True)
    ready = ready.strip().strip("'")
    if ready != "true":
        print(f"FAILED\n[ERROR] KeosCluster status.ready={ready}. "
              "Resolve any pending reconciliation before continuing.")
        sys.exit(1)
    print("OK")

    print("[INFO] All prerequisites satisfied.")


# ---------------------------------------------------------------------------
# Patch CAPA feature gates
# ---------------------------------------------------------------------------

def _get_capa_args():
    '''Return the current args list of the CAPA manager container.'''
    cmd = (kubectl + f" get deployment {CAPA_DEPLOYMENT} -n {CAPA_NAMESPACE}"
           f" -o jsonpath='{{.spec.template.spec.containers[{CAPA_CONTAINER_INDEX}].args}}'")
    output, _ = run_command(cmd)
    return json.loads(output)


def _find_feature_gates_index(args):
    '''Return the index of the --feature-gates arg, or -1 if not found.'''
    for i, arg in enumerate(args):
        if arg.startswith("--feature-gates="):
            return i
    return -1


def _parse_feature_gates(arg):
    '''Parse "--feature-gates=K=V,K=V,..." into a dict.'''
    raw = arg.split("=", 1)[1]
    result = {}
    for pair in raw.split(","):
        k, v = pair.split("=", 1)
        result[k.strip()] = v.strip()
    return result


def check_capa_feature_gates():
    '''
    Verify MachinePool and EKSAllowAddRoles feature gates are enabled in CAPA.

    This script does NOT patch the CAPA deployment directly — upgrade-provisioner.py
    is the one that sets these gates, via EXP_MACHINE_POOL/CAPA_EKS_ADD_ROLES env vars
    passed to `clusterctl upgrade apply`, which reinstalls CAPA's manifests wholesale.
    A raw `kubectl patch` here would be overwritten on the next clusterctl-driven
    upgrade, so it is not a durable fix — only upgrade-provisioner.py's own mechanism is.
    '''

    print("[INFO] Checking CAPA feature gates:", end=" ", flush=True)

    args = _get_capa_args()
    idx = _find_feature_gates_index(args)

    if idx == -1:
        print("FAILED\n[ERROR] --feature-gates argument not found in CAPA deployment.")
        sys.exit(1)

    gates = _parse_feature_gates(args[idx])

    already_set = all(gates.get(k) == v for k, v in REQUIRED_FEATURE_GATES.items())
    if already_set:
        print("OK (already set)")
        return

    missing = {k: v for k, v in REQUIRED_FEATURE_GATES.items() if gates.get(k) != v}
    print("FAILED")
    print(f"[ERROR] Missing/incorrect CAPA feature gates: {missing}.")
    print("[ERROR] Run upgrade-provisioner.py targeting cluster-operator >= "
          f"{MIN_CLUSTER_OPERATOR_VERSION} / CAPA >= {MIN_CAPA_VERSION} first — "
          "it sets these gates as part of its own clusterctl upgrade apply step.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Argument parsing and main
# ---------------------------------------------------------------------------

def configure_aws_credentials(vault_secrets_data):
    print("[INFO] Configuring AWS credentials:", end=" ", flush=True)
    aws_creds = vault_secrets_data['secrets']['aws']['credentials']
    os.environ["AWS_PAGER"] = ""
    os.environ["AWS_ACCESS_KEY_ID"] = aws_creds['access_key']
    os.environ["AWS_SECRET_ACCESS_KEY"] = aws_creds['secret_key']
    os.environ["AWS_DEFAULT_REGION"] = aws_creds['region']
    role_arn = aws_creds.get('role_arn')
    if role_arn:
        result = subprocess.run(
            ["aws", "sts", "assume-role", "--role-arn", role_arn, "--role-session-name", "activate-mp-session"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print("FAILED")
            print(result.stderr)
            sys.exit(1)
        creds = json.loads(result.stdout)["Credentials"]
        os.environ["AWS_ACCESS_KEY_ID"] = creds["AccessKeyId"]
        os.environ["AWS_SECRET_ACCESS_KEY"] = creds["SecretAccessKey"]
        os.environ["AWS_SESSION_TOKEN"] = creds["SessionToken"]
    print("OK")


def load_secrets(secrets_path, vault_password):
    print("[INFO] Reading secrets file:", end=" ", flush=True)
    if not os.path.exists(secrets_path):
        print(f"FAILED\n[ERROR] Secrets file '{secrets_path}' not found.")
        sys.exit(1)
    try:
        vault = Vault(vault_password)
        data = vault.load(open(secrets_path).read())
        print("OK")
        return data
    except Exception as e:
        print(f"FAILED\n[ERROR] Could not decrypt secrets file: {e}")
        sys.exit(1)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Validate that an EKS-managed KeosCluster meets the minimum "
                     "cluster-operator/CAPA versions and CAPA feature gates required "
                     "for MachinePool support. Run upgrade-provisioner.py first if any "
                     "check fails — this script is read-only, it does not change "
                     "anything itself.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("-k", "--kubeconfig",
                        help="Path to kubeconfig file. Can also be set via $KUBECONFIG.",
                        default=None)
    parser.add_argument("-p", "--vault-password",
                        help="Vault password to decrypt secrets.yml.",
                        required=True)
    parser.add_argument("-s", "--secrets",
                        help="Path to the encrypted secrets file.",
                        default="secrets.yml")
    return parser.parse_args()


def main():
    global kubectl
    args = parse_args()

    if args.kubeconfig:
        kubectl = f"kubectl --kubeconfig {os.path.expanduser(args.kubeconfig)}"

    vault_secrets_data = load_secrets(args.secrets, args.vault_password)
    configure_aws_credentials(vault_secrets_data)

    print("[INFO] This script is read-only — it only validates prerequisites and CAPA")
    print("       feature gates. If any check below fails, run upgrade-provisioner.py")
    print("       targeting cluster-operator >= "
          f"{MIN_CLUSTER_OPERATOR_VERSION} / CAPA >= {MIN_CAPA_VERSION} first.")
    print("")

    validate_prerequisites()
    check_capa_feature_gates()

    print("")
    print("=" * 70)
    print("  PREPARATION COMPLETE")
    print("  cluster-operator/CAPA versions and CAPA feature gates verified.")
    print("")
    print("  You can now add MachinePool workers to the KeosCluster descriptor")
    print("  (omit node_image; ami_type defaults to BOTTLEROCKET_x86_64) and")
    print("  migrate off existing MachineDeployment workers manually.")
    print("=" * 70)
    print("")
    print("RESULT: OK")


if __name__ == "__main__":
    main()
