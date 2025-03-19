#!/usr/bin/env python3
# -*- coding: utf-8 -*-

##############################################################
# Author: Stratio Clouds <clouds-integration@stratio.com>    #
# Supported provisioner versions: 0.4.X                      #
# Supported cloud providers:                                 #
#   - EKS                                                    #
#   - Azure VMs                                              #
##############################################################

__version__ = "0.5.8"

import argparse
import os
import sys
import json
import subprocess
import yaml
import base64
import re
import zlib
import time
from datetime import datetime
from ansible_vault import Vault
from jinja2 import Template

CLOUD_PROVISIONER = "0.17.0-0.5"
CLUSTER_OPERATOR = "0.3.7"
CLUSTER_OPERATOR_UPGRADE_SUPPORT = "0.2.0"
CLOUD_PROVISIONER_LAST_PREVIOUS_RELEASE = "0.17.0-0.4"

AWS_LOAD_BALANCER_CONTROLLER_CHART = "1.6.2"

CLUSTERCTL = "v1.5.3"

CAPI = "v1.5.3"
CAPA = "v2.2.1"
CAPG = "v1.4.0"
CAPZ = "v1.11.4"

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
    parser.add_argument("-p", "--vault-password", help="Set the vault password file for decrypting secrets", required=True)
    parser.add_argument("-s", "--secrets", help="Set the secrets file for decrypting secrets", default="secrets.yml")
    parser.add_argument("-d", "--descriptor", help="Set the cluster descriptor file", default="cluster.yaml")
    parser.add_argument("--enable-lb-controller", action="store_true", help="Install AWS Load Balancer Controller for EKS clusters (disabled by default)")
    parser.add_argument("--disable-backup", action="store_true", help="Disable backing up files before upgrading (enabled by default)")
    parser.add_argument("--disable-prepare-capsule", action="store_true", help="Disable preparing capsule for the upgrade process (enabled by default)")
    parser.add_argument("--dry-run", action="store_true", help="Do not upgrade components. This invalidates all other options")
    parser.add_argument("--skip-k8s-intermediate-version", action="store_true", help="Skipping workers intermediate kubernetes version upgrade")
    args = parser.parse_args()
    return vars(args)

def backup(backup_dir, namespace, cluster_name, dry_run):
    print("[INFO] Backing up files into directory " + backup_dir)
    # Backup CAPX files
    print("[INFO] Backing up CAPX files:", end =" ", flush=True)
    if dry_run:
        print("DRY-RUN")
    else:
        os.makedirs(backup_dir + "/" + namespace, exist_ok=True)
        command = "clusterctl --kubeconfig " + kubeconfig + " -n cluster-" + cluster_name + " move --to-directory " + backup_dir + "/" + namespace + " >/dev/null 2>&1"
        status, output = subprocess.getstatusoutput(command)
        if status != 0:
            print("FAILED")
            print("[ERROR] Backing up CAPX files failed:\n" + output)
            sys.exit(1)
        else:
            print("OK")
    # Backup capsule files
    print("[INFO] Backing up capsule files:", end =" ", flush=True)
    if not dry_run:
        os.makedirs(backup_dir + "/capsule", exist_ok=True)
        command = kubectl + " get mutatingwebhookconfigurations capsule-mutating-webhook-configuration"
        status, _ = subprocess.getstatusoutput(command)
        if status == 0:
            command = kubectl + " get mutatingwebhookconfigurations capsule-mutating-webhook-configuration -o yaml 2>/dev/null > " + backup_dir + "/capsule/capsule-mutating-webhook-configuration.yaml"
            status, output = subprocess.getstatusoutput(command)
            if status != 0:
                print("FAILED")
                print("[ERROR] Backing up capsule files failed:\n" + output)
                sys.exit(1)
        command = kubectl + " get validatingwebhookconfigurations capsule-validating-webhook-configuration"
        status, output = subprocess.getstatusoutput(command)
        if status == 0:
            command = kubectl + " get validatingwebhookconfigurations capsule-validating-webhook-configuration -o yaml 2>/dev/null > " + backup_dir + "/capsule/capsule-validating-webhook-configuration.yaml"
            status, output = subprocess.getstatusoutput(command)
            if status != 0:
                print("FAILED")
                print("[ERROR] Backing up capsule files failed:\n" + output)
                sys.exit(1)
            else:
                print("OK")
        if "NotFound" in output:
            print("SKIP")
    else:
        print("DRY-RUN")

def prepare_capsule(dry_run):
    print("[INFO] Preparing capsule-mutating-webhook-configuration for the upgrade process:", end =" ", flush=True)
    if not dry_run:
        command = kubectl + " get mutatingwebhookconfigurations capsule-mutating-webhook-configuration"
        status, output = subprocess.getstatusoutput(command)
        if status != 0:
            if "NotFound" in output:
                print("SKIP")
            else:
                print("FAILED")
                print("[ERROR] Preparing capsule-mutating-webhook-configuration failed:\n" + output)
                sys.exit(1)
        else:
            command = (kubectl + " get mutatingwebhookconfigurations capsule-mutating-webhook-configuration -o json | " +
                    '''jq -r '.webhooks[0].objectSelector |= {"matchExpressions":[{"key":"name","operator":"NotIn","values":["kube-system","tigera-operator","calico-system","cert-manager","capi-system","''' +
                    namespace + '''","capi-kubeadm-bootstrap-system","capi-kubeadm-control-plane-system"]},{"key":"kubernetes.io/metadata.name","operator":"NotIn","values":["kube-system","tigera-operator","calico-system","cert-manager","capi-system","''' +
                    namespace + '''","capi-kubeadm-bootstrap-system","capi-kubeadm-control-plane-system"]}]}' | ''' + kubectl + " apply -f -")
            execute_command(command, False)
    else:
        print("DRY-RUN")

    print("[INFO] Preparing capsule-validating-webhook-configuration for the upgrade process:", end =" ", flush=True)
    if not dry_run:
        command = kubectl + " get validatingwebhookconfigurations capsule-validating-webhook-configuration"
        status, _ = subprocess.getstatusoutput(command)
        if status != 0:
            if "NotFound" in output:
                print("SKIP")
            else:
                print("FAILED")
                print("[ERROR] Preparing capsule-validating-webhook-configuration failed:\n" + output)
                sys.exit(1)
        else:
            command = (kubectl + " get validatingwebhookconfigurations capsule-validating-webhook-configuration -o json | " +
                    '''jq -r '.webhooks[] |= (select(.name == "namespaces.capsule.clastix.io").objectSelector |= ({"matchExpressions":[{"key":"name","operator":"NotIn","values":["''' +
                    namespace + '''","tigera-operator","calico-system"]},{"key":"kubernetes.io/metadata.name","operator":"NotIn","values":["''' +
                    namespace + '''","tigera-operator","calico-system"]}]}))' | ''' + kubectl + " apply -f -")
            execute_command(command, False)
    else:
        print("DRY-RUN")

def restore_capsule(dry_run):
    print("[INFO] Restoring capsule-mutating-webhook-configuration:", end =" ", flush=True)
    if not dry_run:
        command = kubectl + " get mutatingwebhookconfigurations capsule-mutating-webhook-configuration"
        status, output = subprocess.getstatusoutput(command)
        if status != 0:
            if "NotFound" in output:
                print("SKIP")
            else:
                print("FAILED")
                print("[ERROR] Restoring capsule-mutating-webhook-configuration failed:\n" + output)
                sys.exit(1)
        else:
            command = (kubectl + " get mutatingwebhookconfigurations capsule-mutating-webhook-configuration -o json | " +
                    "jq -r '.webhooks[0].objectSelector |= {}' | " + kubectl + " apply -f -")
            execute_command(command, False)
    else:
        print("DRY-RUN")

    print("[INFO] Restoring capsule-validating-webhook-configuration:", end =" ", flush=True)
    if not dry_run:
        command = kubectl + " get validatingwebhookconfigurations capsule-validating-webhook-configuration"
        status, _ = subprocess.getstatusoutput(command)
        if status != 0:
            if "NotFound" in output:
                print("SKIP")
            else:
                print("FAILED")
                print("[ERROR] Restoring capsule-validating-webhook-configuration failed:\n" + output)
                sys.exit(1)
        else:
            command = (kubectl + " get validatingwebhookconfigurations capsule-validating-webhook-configuration -o json | " +
                    """jq -r '.webhooks[] |= (select(.name == "namespaces.capsule.clastix.io").objectSelector |= {})' """ +
                    "| " + kubectl + " apply -f -")
            execute_command(command, False)
    else:
        print("DRY-RUN")

def delete_cluster_operator_webhooks(dry_run):
    print("[INFO] Deleting keoscluster-validating-webhook-configuration for the upgrade process:", end =" ", flush=True)
    if not dry_run:
        command = kubectl + " delete validatingwebhookconfigurations keoscluster-validating-webhook-configuration"
        status, output = subprocess.getstatusoutput(command)
        if status != 0:
            if "NotFound" in output:
                print("SKIP")
            else:
                print("FAILED")
                print("[ERROR] Deleting keoscluster-validating-webhook-configuration failed:\n" + output)
                sys.exit(1)
        print("OK")
    else:
        print("DRY-RUN")

def restore_cluster_operator_webhooks(dry_run):
    print("[INFO] Restoring keoscluster-validating-webhook-configuration for the upgrade process:", end =" ", flush=True)
    if not dry_run:
        cluster_operator_revision = get_chart_revision("cluster-operator", "kube-system")
        command = helm + " rollback -n kube-system cluster-operator " + cluster_operator_revision
        status, output = subprocess.getstatusoutput(command)
        if status != 0:
            if "NotFound" in output:
                print("SKIP")
            else:
                print("FAILED")
                print("[ERROR] Restoring keoscluster-validating-webhook-configuration failed:\n" + output)
                sys.exit(1)
        print("OK")
    else:
        print("DRY-RUN")

def install_lb_controller(cluster_name, account_id, dry_run):
    print("[INFO] Installing LoadBalancer Controller:", end =" ", flush=True)
    if not dry_run:
        chart_version = get_chart_version("aws-load-balancer-controller", "kube-system")
        if chart_version == AWS_LOAD_BALANCER_CONTROLLER_CHART:
            print("SKIP")
            return
        gnpPatch = {
                        "spec": {
                                    "selector":
                                        "app.kubernetes.io/name in {'aws-ebs-csi-driver', 'aws-load-balancer-controller' } || " +
                                        "cluster.x-k8s.io/provider == 'infrastructure-aws' || " +
                                        "k8s-app == 'aws-cloud-controller-manager'"
                                }
                    }
        gnpPatch_file = open('./gnpPatch.yaml', 'w')
        gnpPatch_file.write(yaml.dump(gnpPatch, default_flow_style=False))
        gnpPatch_file.close()
        command = kubectl + " patch globalnetworkpolicy allow-traffic-to-aws-imds-capa --type merge --patch-file gnpPatch.yaml"
        execute_command(command, False, False)
        os.remove('./gnpPatch.yaml')
        role_name = cluster_name + "-lb-controller-manager"
        command = (helm + " -n kube-system install aws-load-balancer-controller aws-load-balancer-controller" +
                    " --wait --version " + AWS_LOAD_BALANCER_CONTROLLER_CHART +
                    " --set clusterName=" + cluster_name +
                    " --set podDisruptionBudget.minAvailable=1" +
                    " --set serviceAccount.annotations.\"eks\\.amazonaws\\.com/role-arn\"=arn:aws:iam::" + account_id + ":role/" + role_name +
                    " --repo https://aws.github.io/eks-charts")
        execute_command(command, False)
    else:
        print("DRY-RUN")

def patch_clusterrole_aws_node(dry_run):
    aws_node_clusterrole_name = "aws-node"
    print("[INFO] Modifying aws-node ClusterRole:", end =" ", flush=True)
    if not dry_run:
        get_clusterrole_command = kubectl + " get clusterrole -o json " + aws_node_clusterrole_name + " | jq -r '.rules'"
        cluster_role_rule = json.loads(execute_command(get_clusterrole_command, False))
        rule_pods_index = next((i for i, rule in enumerate(cluster_role_rule) if 'pods' in rule.get('resources', [])), None)
        if rule_pods_index is not None:
            verbs = cluster_role_rule[rule_pods_index].get('verbs', [])
            if 'patch' not in verbs:
                patch = [
                    {
                        "op": "add",
                        "path": f"/rules/{rule_pods_index}/verbs/-",
                        "value": "patch"
                    }
                ]
                patch_clusterrole_command = kubectl + " patch clusterrole " + aws_node_clusterrole_name + " --type=json -p='" + json.dumps(patch) + "'"
                execute_command(patch_clusterrole_command, False, False)
        else:
            print("[ERROR] Pods resource not found in the ClusterRole " + aws_node_clusterrole_name)
            sys.exit(1)
    else:
        print("DRY-RUN")

def validate_k8s_version(validation, dry_run):
    if validation == "first":
        minor = "27"
        dry_run_version = "1.27.X"
    elif validation == "second":
        minor = "28"
        dry_run_version = "1.28.X"
    if not dry_run:
        supported_k8s_versions = r"^1\.("+ minor +")\.\d+$"
        desired_k8s_version = input("Please provide the Kubernetes version to which you want to upgrade: ")
        
        if not re.match(supported_k8s_versions, desired_k8s_version):
            print("[ERROR] The only supported Kubernetes versions are: 1."+ minor +".X")
            sys.exit(1)

        while True:
            response = input(f"Are you sure you want to upgrade to version {desired_k8s_version}? (yes/no): ").strip().lower()
            if response in ["yes", "y"]:
                return desired_k8s_version
            elif response in ["no", "n"]:
                print("[INFO] Upgrade canceled by user.")
                sys.exit(1)
            else:
                print("[ERROR] Invalid input. Please enter 'yes/y' or 'no/n'")
    else:
        return dry_run_version
    
def get_kubernetes_version():
    command = kubectl + " get nodes -ojsonpath='{range .items[*]}{.status.nodeInfo.kubeletVersion}{\"\\n\"}{end}' | sort | uniq"
    output = execute_command(command, False, False)

    return output.strip()

def wait_for_workers(cluster_name, current_k8s_version):
    print("[INFO] Waiting for the Kubernetes version upgrade - worker nodes:", end =" ", flush=True)
    previous_node = 1
    while previous_node != 0:
        command = (
            kubectl + " get nodes"
            + " -ojsonpath='{range .items[?(@.status.nodeInfo.kubeletVersion==\"" + current_k8s_version + "\")]}{.metadata.name}{\"\\n\"}{end}'"
        )
        output = execute_command(command, False, False)
        previous_node = len(output.splitlines())
        time.sleep(30)
    command = kubectl + " wait --for=condition=Ready nodes --all --timeout 5m"
    execute_command(command, False, False)
    command = (
        kubectl + " wait --for=jsonpath=\"{.status.ready}\"=true KeosCluster "
        + cluster_name + " -n cluster-" + cluster_name + " --timeout 10m"
    )
    execute_command(command, False)

def is_node_image_defined(node_group):
    # Check if 'node_image' is defined in node_group
    return 'node_image' in node_group

def prompt_for_node_image(node_name, kubernetes_version):
    node_image = input(f"Please provide the image ID associated with the Kubernetes version: {kubernetes_version} for {node_name}: ")

    while True:
        response = input(f"Are you sure you want to use node image: {node_image} for {node_name}? (yes/no): ").strip().lower()
        if response in ["yes", "y"]:
            if node_name == "control-plane":
                return node_image
            else:
                return {"node_image": node_image}
        elif response in ["no", "n"]:
            print("[INFO] Upgrade canceled by user.")
            sys.exit(1)
        else:
            print("[ERROR] Invalid input. Please enter 'yes/y' or 'no/n'")
    

def get_k8s_lower_version(versions):
    # Extract the version numbers from the strings
    version_1_num = versions.splitlines()[0].split('-')[0][1:]  # Remove 'v' prefix and split at '-'
    version_2_num = versions.splitlines()[1].split('-')[0][1:]  # Remove 'v' prefix and split at '-'
    
    # Convert version strings to tuples of integers (e.g., "1.27.12" -> (1, 27, 12))
    version_1_tuple = tuple(map(int, version_1_num.split('.')))
    version_2_tuple = tuple(map(int, version_2_num.split('.')))

    if version_1_tuple < version_2_tuple:
        return versions.splitlines()[0]
    else:
        return versions.splitlines()[1]

def cp_global_network_policy(action, networks, provider, backup_dir, dry_run):
    command = kubectl + " get GlobalNetworkPolicy allow-all-traffic-from-control-plane"
    status, _ = subprocess.getstatusoutput(command)
    if status == 0:
        os.makedirs(backup_dir + "/calico", exist_ok=True)
        backup_file = os.path.join(backup_dir, "calico", "allow-all-traffic-from-control-plane_gnp.yaml")
        if action == "patch":
            print("[INFO] Applying temporal allow control plane GlobalNetworkPolicy:", end =" ", flush=True)
            command = kubectl + " get GlobalNetworkPolicy allow-all-traffic-from-control-plane -o yaml 2>/dev/null > " + backup_file
            execute_command(command, dry_run, False)

            # Fetch network CIDRs, with defaults if not provided
            vpc_cidr = networks.get("vpc_cidr", "10.0.0.0/16")
            pods_cidr = networks.get("pods_cidr", "192.168.0.0/16")
            allow_cp_temporal_gnp = {
                "apiVersion": "crd.projectcalico.org/v1",
                "kind": "GlobalNetworkPolicy",
                "metadata": {
                    "name": "allow-all-traffic-from-control-plane"
                },
                "spec": {
                    "order": 0,
                    "selector": "all()",
                    "ingress": [
                        {
                            "action": "Allow",
                            "source": {
                                "nets": [
                                    vpc_cidr,
                                    pods_cidr
                                ]
                            }
                        }
                    ]
                }
            }
            allow_cp_temporal_gnp_file = 'allow_cp_temporal_gnp.yaml'
            with open(allow_cp_temporal_gnp_file, 'w') as gnpPatch_file:
                yaml.dump(allow_cp_temporal_gnp, gnpPatch_file, default_flow_style=False)
            command = kubectl + " patch GlobalNetworkPolicy allow-all-traffic-from-control-plane --type merge --patch-file " + allow_cp_temporal_gnp_file
            execute_command(command, dry_run)
            os.remove(allow_cp_temporal_gnp_file)
        elif action == "restore":
            print("[INFO] Restoring allow control plane GlobalNetworkPolicy:", end =" ", flush=True)
            if provider == "azure":
                encapsulation = "vxlan"
            else:
                encapsulation = "ipip"
            command = kubectl + " get node -lkubernetes.io/os=linux,node-role.kubernetes.io/control-plane= -oyaml"
            keos_control_plane_nodes_raw = execute_command(command, dry_run, False)
            control_plane_nodes = yaml.safe_load(keos_control_plane_nodes_raw)
            allow_temporal_gnp_template = Template('''
apiVersion: crd.projectcalico.org/v1
kind: GlobalNetworkPolicy
metadata:
  name: allow-all-traffic-from-control-plane
spec:
  order: 0
  selector: all()
  ingress:
  - action: Allow
    source:
      nets:
{% for item in control_plane_nodes['items'] %}
{% set node = item.metadata %}
{% if 'projectcalico.org/IPv4IPIPTunnelAddr' in node.annotations %}
      - {{ node.annotations['projectcalico.org/IPv4IPIPTunnelAddr'] }}/32
{% elif 'projectcalico.org/IPv4VXLANTunnelAddr' in node.annotations %}
      - {{ node.annotations['projectcalico.org/IPv4VXLANTunnelAddr'] }}/32
{% endif %}
{% for address in item.status.addresses %}
{% if address.type == 'InternalIP' %}
      - {{ address.address }}/32
{% endif %}
{% endfor %}
{% endfor %}
''')
            rendered_allow_cp_gnp_yaml = allow_temporal_gnp_template.render(
                control_plane_nodes=control_plane_nodes,
                encapsulation=encapsulation
            )                        
            allow_cp_gnp_file = 'allow_cp_gnp.yaml'
            with open(allow_cp_gnp_file, 'w') as gnpPatch_file:
                gnpPatch_file.write(rendered_allow_cp_gnp_yaml)
            command = kubectl + " patch GlobalNetworkPolicy allow-all-traffic-from-control-plane --type merge --patch-file " + allow_cp_gnp_file
            execute_command(command, dry_run)
            os.remove(allow_cp_gnp_file)

def upgrade_k8s(cluster_name, control_plane, worker_nodes, networks, desired_k8s_version, provider, managed, backup_dir, dry_run):
    aks_enabled = provider == "azure" and managed
    current_k8s_version = get_kubernetes_version()
    current_minor_version = int(current_k8s_version.split('.')[1])
    desired_minor_version = int(desired_k8s_version.split('.')[1])

    if dry_run:
        print(f"[INFO] Updating kubernetes to version {desired_k8s_version}: DRY-RUN", flush=True)
        return

    if len(current_k8s_version.splitlines()) == 1:
        if current_minor_version < desired_minor_version:
            print(f"[INFO] Initiating upgrade to kubernetes to version {desired_k8s_version}", flush=True)

            if not managed:
                cp_global_network_policy("patch", networks, provider, backup_dir, dry_run)

            cp_patched_image_node = ""
            if is_node_image_defined(control_plane):
                cp_patched_image_node = prompt_for_node_image("control-plane", desired_k8s_version)

            updated_worker_nodes = []
            for worker_node in worker_nodes:
                if is_node_image_defined(worker_node):
                    wn_patched_image_node = prompt_for_node_image(f"worker node: {worker_node['name']}", desired_k8s_version)
                    updated_worker_nodes.append({**worker_node, **wn_patched_image_node})
                else:
                    print(f"[INFO] node_image is not defined in worker node: {worker_node['name']}", flush=True)
                    updated_worker_nodes.append(worker_node)

            patch_upgrade = [
                {"op": "replace", "path": "/spec/control_plane/node_image", "value": cp_patched_image_node},
                {"op": "replace", "path": "/spec/worker_nodes", "value": updated_worker_nodes},
                {"op": "replace", "path": "/spec/k8s_version", "value": f"v{desired_k8s_version}"}
            ]

            patch_json = json.dumps(patch_upgrade)
            command = f"{kubectl} -n cluster-{cluster_name} patch KeosCluster {cluster_name} --type='json' -p='{patch_json}'"
            execute_command(command, False, False)

            print("[INFO] Waiting for the Kubernetes version upgrade - control plane:", end=" ", flush=True)
            command = (
                f"{kubectl} wait --for=jsonpath=\"{{.status.phase}}\"=\"Updating worker nodes\""
                f" KeosCluster {cluster_name} --namespace=cluster-{cluster_name} --timeout=25m"
            )
            execute_command(command, False)

            if provider == "aws" and managed:
                patch_clusterrole_aws_node(dry_run)

            wait_for_workers(cluster_name, current_k8s_version)

            if not managed:
                cp_global_network_policy("restore", networks, provider, backup_dir, dry_run)

        elif current_minor_version == desired_minor_version:
            print(f"[INFO] Updating Kubernetes to version {desired_k8s_version}: SKIP", flush=True)

    elif len(current_k8s_version.splitlines()) == 2:
        lower_k8s_version = get_k8s_lower_version(current_k8s_version)
        print("[INFO] Waiting for the Kubernetes version upgrade - control plane:", end=" ", flush=True)
        
        command = (
            f"{kubectl} wait --for=jsonpath=\"{{.status.phase}}\"=\"Updating worker nodes\""
            f" KeosCluster {cluster_name} --namespace=cluster-{cluster_name} --timeout=25m"
        )
        execute_command(command, False)

        if provider == "aws" and managed:
            patch_clusterrole_aws_node(dry_run)

        wait_for_workers(cluster_name, lower_k8s_version)

        if not managed:
            cp_global_network_policy("restore", networks, provider, backup_dir, dry_run)

    else:
        print("[FAILED] More than two different versions of Kubernetes are in the cluster, which requires human action", flush=True)
        sys.exit(1)

def upgrade_cluster_operator(kubeconfig, helm_repo, provider, credentials, cluster_name, dry_run):
    # Check if cluster-operator is already upgraded
    cluster_operator_version = get_chart_version("cluster-operator", "kube-system")
    if cluster_operator_version == CLUSTER_OPERATOR:
        if dry_run:
            print("[INFO] Upgrading Cluster Operator to " + CLUSTER_OPERATOR + ": DRY-RUN")
        else:
            print("[INFO] Upgrading Cluster Operator to " + CLUSTER_OPERATOR + ": SKIP")
    else:
        print("[INFO] Applying new ClusterConfig CRD:", end =" ", flush=True)
        # https://github.com/Stratio/cluster-operator/blob/master/deploy/helm/cluster-operator/crds/installer.stratio.com_clusterconfigs.yaml
        clusterconfig_crd = "eJzFVkuP0zAQvvdXjMRhLyTdwgXlhgqHFQ+hLdpr5CST1NSxgx9lC+K/M7bTR9J0VYFW+FTPjL+Z+eaRJkkyYx1/QG24khnQb3y0KP3NpJs3JuVqvl3MNlxWGSydsaq9R6OcLvEd1lxyS5azFi2rmGXZDIBJqSzzYuOvAKWSVishUCcNynTjCiwcFxXqAL53vb1NF6/SBT2RrMUMSkHeUNPrmjcUhzSWeZDUWO3h01K1M9Nh6b00Wrkug2mjiNhH02cSwZcBPMgFN/bDue4jiYO+E04zMQ4rqAyXjRNMj5SkM6XqKJXP3n3HSqxI1iccwkn6XLeLghhcRLRyjS2LwQLQe/n2y93D69VADFChKTXvbOBuEDJwA3aNEF9ArXS4DgMHAj1gdZrcaMv3HMVz0hcn0pHnGx9ctCIFNQRG532WWPX5gKpJTpFp7DQalLFFBsDgjZgEVXzD0qawQu1hwKyVE5XvI7paQihVI/nPAzZ5VMGpYBb7ch0Pl5S1ZAK2TDh8SQ4qaNmOYLwXcPIEL5iYFD4pjfSwVhmsre1MNp833O7ngXqqddT5u3lobV44q7SZV7hFMTe8SZgu19wSutM4JxqTELoMM5G21QvdT5C5GcRqd75ZqHOpn04UoWOfqIDvWl9z1j+NWRyJ9iLPzv371VfYuw7FGLMfeD8+NMcSeMKID9SxiLVWbcBEWXWKGO47jNOrEahxRcutr/t3otb6WqWwDEsCCgTX0d7AKoU7SdIWxZIZfPYCeKZN4om9rgSn+21sHFk7Uex30oV6DWZ1RbaDuSFTrn1n03ygn4fzTbU/01PrD25MLor8uHjHBj6kmjlhM6iZMHimjqkVSglk4yHtF0nuvTMiPuctazDfTu2KJzidhPorkJhmJ5jEPK638/eXyfKnZY+5k2tkwq53UwbBhLeuzWBxezttQB/DYDCtjgn4ZdSgvpDeWSvFyPmWeiHX2NC3SE+E9w+1/KH0hih/PtoOsV2k7f/w6teRH7Rh1MnFjrxq8Glmnbl69IP1YPhVYfy+vWr6J2M4E0a8DKx2sTHoD5ymaT2VuOLwPdrH3mcCv37P/gDZnAI8"
        clusterconfig_decoded = zlib.decompress(base64.b64decode(clusterconfig_crd))
        if dry_run:
            print("DRY-RUN")
        else:
            p = subprocess.Popen(["kubectl", "--kubeconfig", kubeconfig, "apply", "--server-side", "--force-conflicts", "-f", "-"], stdin=subprocess.PIPE, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            _, errors = p.communicate(input=yaml.dump(yaml.safe_load(clusterconfig_decoded), default_flow_style=False))
            if errors == "":
                print("OK")
            else:
                print("FAILED")
                print("[ERROR] " + errors)

        # Upgrade cluster-operator
        print("[INFO] Upgrading Cluster Operator " + CLUSTER_OPERATOR + ":", end =" ", flush=True)
        if dry_run:
            print("DRY-RUN")
        else:
            command = (helm + " upgrade --reuse-values -n kube-system cluster-operator" +
                " --set app.containers.controllerManager.image.tag=" + CLUSTER_OPERATOR)
            if helm_repo["type"] == "generic":
                command += " cluster-operator --repo " + helm_repo["url"]
            else:
                command += " " + helm_repo["url"] + "/cluster-operator"
            if "user" in helm_repo:
                command += " --username=" + helm_repo["user"]
                command += " --password=" + helm_repo["pass"]
            execute_command(command, False)

def execute_command(command, dry_run, result = True, max_retries=3, retry_delay=5):
    output = ""
    retries = 0

    while retries < max_retries:
        if dry_run:
            if result:
                print("DRY-RUN")
            return ""  # No output in dry-run mode
        else:
            status, output = subprocess.getstatusoutput(command)
            if status == 0:
                if result:
                    print("OK")
                return output
            else:
                if "Unable to connect to the server: net/http: TLS handshake timeout" in output:
                    retries += 1
                    time.sleep(retry_delay)
                else:
                    print("FAILED")
                    print("[ERROR] " + output)
                    sys.exit(1)

def get_chart_version(chart, namespace):
    command = helm + " -n " + namespace + " list"
    output = execute_command(command, False, False)
    # NAME                NAMESPACE   REVISION    UPDATED                                 STATUS      CHART                   APP VERSION
    # cluster-operator    kube-system 1           2025-03-17 10:11:40.845888283 +0000 UTC deployed    cluster-operator-0.2.0  0.2.0 
    for line in output.split("\n"):
        splitted_line = line.split()
        if chart == splitted_line[0]:
            if chart == "cluster-operator":
                return splitted_line[9]
            else:
                return splitted_line[8].split("-")[-1]
    return None

def get_chart_revision(chart, namespace):
    command = helm + " -n " + namespace + " list"
    output = execute_command(command, False, False)
    # NAME                NAMESPACE   REVISION    UPDATED                                 STATUS      CHART                   APP VERSION
    # cluster-operator    kube-system 1           2025-03-17 10:11:40.845888283 +0000 UTC deployed    cluster-operator-0.2.0  0.2.0 
    for line in output.split("\n"):
        splitted_line = line.split()
        if chart == splitted_line[0]:
            return splitted_line[2]
    return None

def get_version(version):
    return re.sub(r'\D', '', version)

def print_upgrade_support():
    print("[WARN] Upgrading cloud-provisioner from a version minor than " + CLOUD_PROVISIONER_LAST_PREVIOUS_RELEASE + " to " + CLOUD_PROVISIONER + " is NOT SUPPORTED")
    print("[WARN] You have to upgrade to cloud-provisioner:"+ CLOUD_PROVISIONER_LAST_PREVIOUS_RELEASE + " first")
    sys.exit(0)

def verify_upgrade():
    print("[INFO] Verifying upgrade process")
    cluster_operator_version = get_chart_version("cluster-operator", "kube-system")
    if cluster_operator_version is None:
        if os.path.exists('./clusteroperator.values'):
            return
        else:
            print_upgrade_support()
    patch_version = get_version(cluster_operator_version)
    if int(patch_version[:2]) < int(get_version(CLUSTER_OPERATOR)[:2]):
        if int(patch_version) < int(get_version(CLUSTER_OPERATOR_UPGRADE_SUPPORT)):
            print_upgrade_support()
    elif int(patch_version) > int(get_version(CLUSTER_OPERATOR)):
        print("[WARN] Downgrading cloud-provisioner from a version major than " + CLUSTER_OPERATOR + " is NOT SUPPORTED")
        sys.exit(0)
    return

def request_confirmation():
    enter = input("Press ENTER to continue upgrading the cluster or any other key to abort: ")
    if enter != "":
        sys.exit(0)

if __name__ == '__main__':
    # Init variables
    start_time = time.time()
    backup_dir = "./backup/upgrade/"
    binaries = ["clusterctl", "kubectl", "helm", "jq"]
    helm_repo = {}

    # Parse arguments
    config = parse_args()

    # Set kubeconfig
    if os.environ.get("KUBECONFIG"):
        kubeconfig = os.environ.get("KUBECONFIG")
    else:
        kubeconfig = os.path.expanduser(config["kubeconfig"])

    # Check binaries
    for binary in binaries:
        if not subprocess.getstatusoutput("which " + binary)[0] == 0:
            print("[ERROR] " + binary + " binary not found in $PATH")
            sys.exit(1)

    command = "clusterctl version -o short"
    status, output = subprocess.getstatusoutput(command)
    if (status != 0) or (get_version(output) < get_version(CLUSTERCTL)):
        print("[ERROR] clusterctl version " + CLUSTERCTL + " is required")
        sys.exit(1)

    # Check paths
    if not os.path.exists(config["descriptor"]):
        print("[ERROR] Descriptor file not found")
        sys.exit(1)
    if not os.path.exists(config["secrets"]):
        print("[ERROR] Secrets file not found")
        sys.exit(1)
    if not os.path.exists(kubeconfig):
        print("[ERROR] Kubeconfig file not found")
        sys.exit(1)

    print("[INFO] Using kubeconfig: " + kubeconfig)

    # Set kubectl
    kubectl = "kubectl --kubeconfig " + kubeconfig

    # Set helm
    helm = "helm --kubeconfig " + kubeconfig

    # Get cluster descriptor
    with open(config["descriptor"]) as file:
        cluster_file = list(yaml.safe_load_all(file))
    file.close()

    # Initialize variables
    keos_cluster = None
    cluster_config = None

    # Assign documents to variables based on their order
    for doc in cluster_file:
        if doc['kind'] == 'KeosCluster':
            keos_cluster = doc
        elif doc['kind'] == 'ClusterConfig':
            cluster_config = doc

    if not keos_cluster:
        print("[ERROR] KeosCluster cannot be empty")
        sys.exit(1)

    # Set cluster_name
    if "metadata" in keos_cluster:
        cluster_name = keos_cluster["metadata"]["name"]
    else:
        print("[ERROR] KeosCluster definition not found. Ensure that KeosCluster is defined before ClusterConfig in the descriptor file")
        sys.exit(1)
    print("[INFO] Cluster name: " + cluster_name)
    if not config["dry_run"] and not config["yes"]:
        request_confirmation()

    # Check kubectl access
    command = kubectl + " get cl -A --no-headers | awk '{print $1}'"
    status, output = subprocess.getstatusoutput(command)
    if status != 0 or output != "cluster-" + cluster_name:
        print("[ERROR] Cluster not found. Verify the kubeconfig file")
        sys.exit(1)

    # Get secrets
    try:
        vault = Vault(config["vault_password"])
        data = vault.load(open(config["secrets"]).read())
    except Exception as e:
        print("[ERROR] Decoding secrets file failed:\n" + str(e))
        sys.exit(1)

    # Set env vars
    env_vars = "CLUSTER_TOPOLOGY=true CLUSTERCTL_DISABLE_VERSIONCHECK=true GOPROXY=off"
    provider = keos_cluster["spec"]["infra_provider"]
    managed = keos_cluster["spec"]["control_plane"]["managed"]
    if provider == "aws":
        namespace = "capa-system"
        version = CAPA
        credentials = subprocess.getoutput(kubectl + " -n " + namespace + " get secret capa-manager-bootstrap-credentials -o jsonpath='{.data.credentials}'")
        env_vars += " CAPA_EKS_IAM=true AWS_B64ENCODED_CREDENTIALS=" + credentials
    if provider == "gcp":
        namespace = "capg-system"
        version = CAPG
        credentials = subprocess.getoutput(kubectl + " -n " + namespace + " get secret capg-manager-bootstrap-credentials -o json | jq -r '.data[\"credentials.json\"]'")
        if managed:
            env_vars += " EXP_MACHINE_POOL=true EXP_CAPG_GKE=true"
        env_vars += " GCP_B64ENCODED_CREDENTIALS=" + credentials
    if provider == "azure":
        namespace = "capz-system"
        version = CAPZ
        if managed:
            env_vars += " EXP_MACHINE_POOL=true"
        if "credentials" in data["secrets"]["azure"]:
            credentials = data["secrets"]["azure"]["credentials"]
            env_vars += " AZURE_CLIENT_ID_B64=" + base64.b64encode(credentials["client_id"].encode("ascii")).decode("ascii")
            env_vars += " AZURE_CLIENT_SECRET_B64=" + base64.b64encode(credentials["client_secret"].encode("ascii")).decode("ascii")
            env_vars += " AZURE_SUBSCRIPTION_ID_B64=" + base64.b64encode(credentials["subscription_id"].encode("ascii")).decode("ascii")
            env_vars += " AZURE_TENANT_ID_B64=" + base64.b64encode(credentials["tenant_id"].encode("ascii")).decode("ascii")
        else:
            print("[ERROR] Azure credentials not found in secrets file")
            sys.exit(1)

    if "github_token" in data["secrets"]:
        env_vars += " GITHUB_TOKEN=" + data["secrets"]["github_token"]
        helm = "GITHUB_TOKEN=" + data["secrets"]["github_token"] + " " + helm
        kubectl = "GITHUB_TOKEN=" + data["secrets"]["github_token"] + " " + kubectl

    # Set helm repository
    helm_repo["url"] = keos_cluster["spec"]["helm_repository"]["url"]
    helm_repo["type"] = keos_cluster["spec"]["helm_repository"]["type"]
    if "auth_required" in keos_cluster["spec"]["helm_repository"]:
        if keos_cluster["spec"]["helm_repository"]["auth_required"]:
            if "user" in data["secrets"]["helm_repository"] and "pass" in data["secrets"]["helm_repository"]:
                helm_repo["user"] = data["secrets"]["helm_repository"]["user"]
                helm_repo["pass"] = data["secrets"]["helm_repository"]["pass"]
            else:
                print("[ERROR] Helm repository credentials not found in secrets file")
                sys.exit(1)

    # Verify upgrade
    verify_upgrade()

    # Backup
    if not config["disable_backup"]:
        now = datetime.now()
        backup_dir = backup_dir + now.strftime("%Y%m%d-%H%M%S")
        backup(backup_dir, namespace, cluster_name, config["dry_run"])

    # Prepare capsule
    if not config["disable_prepare_capsule"]:
        prepare_capsule(config["dry_run"])

    # EKS LoadBalancer Controller
    if config["enable_lb_controller"]:
        if provider == "aws" and managed:
            account_id = data["secrets"]["aws"]["credentials"]["account_id"]
            install_lb_controller(cluster_name, account_id, config["dry_run"])
        else:
            print("[WARN] AWS LoadBalancer Controller is only supported for EKS managed clusters")
            sys.exit(0)

    # Cluster Operator
    upgrade_cluster_operator(kubeconfig, helm_repo, provider, credentials, cluster_name, config["dry_run"])

    # Prepare cluster-operator for skipping validations to avoid upgrading to k8s intermediate versions
    if config["skip_k8s_intermediate_version"]:
        delete_cluster_operator_webhooks(config["dry_run"])

    # Restore capsule
    if not config["disable_prepare_capsule"]:
        restore_capsule(config["dry_run"])

    networks = keos_cluster["spec"].get("networks", {})
    # Update kubernetes version to 1.27.X
    current_k8s_version = get_kubernetes_version()
    if "1.26" in current_k8s_version and not config["skip_k8s_intermediate_version"]:
        required_k8s_version=validate_k8s_version("first", config["dry_run"])
        upgrade_k8s(cluster_name, keos_cluster["spec"]["control_plane"], keos_cluster["spec"]["worker_nodes"], networks, required_k8s_version, provider, managed, backup_dir, config["dry_run"])

    # Update kubernetes version to 1.28.X
    required_k8s_version=validate_k8s_version("second", config["dry_run"])
    upgrade_k8s(cluster_name, keos_cluster["spec"]["control_plane"], keos_cluster["spec"]["worker_nodes"], networks, required_k8s_version, provider, managed, backup_dir, config["dry_run"])
    
    if config["skip_k8s_intermediate_version"]:
        restore_cluster_operator_webhooks(config["dry_run"])

    end_time = time.time()
    elapsed_time = end_time - start_time
    minutes, seconds = divmod(elapsed_time, 60)
    print("[INFO] Upgrade process finished successfully in " + str(int(minutes)) + " minutes and " + "{:.2f}".format(seconds) + " seconds")
