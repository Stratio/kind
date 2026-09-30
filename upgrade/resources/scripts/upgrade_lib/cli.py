# -*- coding: utf-8 -*-
"""cli — moved verbatim from upgrade-provisioner.py (PLT-4916)."""

import argparse
import sys
import re
from upgrade_lib.versions import CLOUD_PROVISIONER, CLOUD_PROVISIONER_LAST_PREVIOUS_RELEASE, CLUSTER_OPERATOR, CLUSTER_OPERATOR_UPGRADE_SUPPORT

def parse_args():
    parser = argparse.ArgumentParser(
        description='''This script upgrades cloud-provisioner from ''' + CLOUD_PROVISIONER_LAST_PREVIOUS_RELEASE + ''' to ''' + CLOUD_PROVISIONER +
                    ''' by upgrading mainly cluster-operator from ''' + CLUSTER_OPERATOR_UPGRADE_SUPPORT + ''' to ''' + CLUSTER_OPERATOR + ''' .
                        It requires kubectl, helm and jq binaries in $PATH.
                        A component (or all) must be selected for upgrading.
                        By default, the process will wait for confirmation for every component selected for upgrade.''',
                                    formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("-y", "--yes", action="store_true", help="Do not wait for confirmation between tasks")
    parser.add_argument("-k", "--kubeconfig", help="Set the kubeconfig file for kubectl commands, It can also be set using $KUBECONFIG variable", default="~/.kube/config")
    parser.add_argument("-p", "--vault-password", help="Set the vault password for decrypting secrets", required=True)
    parser.add_argument("-s", "--secrets", help="Set the secrets file for decrypting secrets", default="secrets.yml")
    parser.add_argument("--cluster-operator", help="Set the cluster-operator target version", default=CLUSTER_OPERATOR)
    parser.add_argument("--disable-backup", action="store_true", help="Disable backing up files before upgrading (enabled by default)")
    parser.add_argument("--disable-prepare-capsule", action="store_true", help="Disable preparing capsule for the upgrade process (enabled by default)")
    parser.add_argument("--dry-run", action="store_true", help="Do not upgrade components. This invalidates all other options")
    parser.add_argument("--private", action="store_true", help="Treats the Docker registry and the Helm repository as private")
    parser.add_argument("--ecr-pull-through", action="store_true", help="Force ECR pull-through cache mode regardless of KeosCluster spec")
    parser.add_argument("--skip-preflight-checks", action="store_true", help="Skip cluster health checks before upgrading (NOT recommended: an unhealthy cluster can make the upgrade worse, e.g. a partially-drained node or a CAPI controller stuck at 1 replica)")
    parser.add_argument("--k8s-version", help="Set the target k8s minor version to bump the cluster to (e.g. 1.36). Defaults to the provider target: EKS 1.36, Azure VMs and GKE 1.37. Applied as a single patch to KeosCluster.spec.k8s_version — the KeosCluster webhook's +1-minor-per-patch limit is bypassed the same way the rest of this script already bypasses it for clusterctl", default=None)
    parser.add_argument("--start-from-k8s-version", action="store_true", help="Skip the interactive Y/N confirmation before bumping k8s_version (the bump itself is still a single patch to --k8s-version, not a resume-from-intermediate-step mechanism)")
    parser.add_argument("--node-image-map", help='Azure only: JSON map of every intermediate minor to its VM image resource ID, e.g. \'{"1.33":"<id>","1.34":"<id>","1.35":"<id>"}\'. Required for a k8s_version bump on provider=azure — never hardcode a version-to-image table, the caller must supply the right image per minor')
    parser.add_argument("--control-plane-timeout", type=positive_minutes, default=90, help="Minutes to wait for the control plane to reach each target minor (EKS/GKE control plane, KubeadmControlPlane on Azure). Absolute limit per wait")
    parser.add_argument("--node-convergence-timeout", type=positive_minutes, default=90, help="Minutes the worker node rollout may go without progress (no further node or node pool reaching the target minor) before aborting. The limit restarts every time progress is made, so it does not depend on the number of nodes or node pools")
    args = parser.parse_args()
    return vars(args)

def positive_minutes(value):
    minutes = int(value)
    if minutes <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive number of minutes, got {value}")
    return minutes

def get_version(version):
    '''Get the version number'''

    return re.sub(r'\D', '', version)

def print_upgrade_support():
    '''Print the upgrade support message'''

    print("[WARN] Upgrading cloud-provisioner from a version minor than " + CLOUD_PROVISIONER_LAST_PREVIOUS_RELEASE + " to " + CLOUD_PROVISIONER + " is NOT SUPPORTED")
    print("[WARN] You have to upgrade to cloud-provisioner:"+ CLOUD_PROVISIONER_LAST_PREVIOUS_RELEASE + " first")
    sys.exit(0)

def request_confirmation():
    '''Request confirmation to continue'''

    enter = input("Press ENTER to continue upgrading the cluster or any other key to abort: ")
    if enter != "":
        sys.exit(0)
