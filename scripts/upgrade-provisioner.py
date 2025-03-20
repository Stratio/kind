#!/usr/bin/env python3
# -*- coding: utf-8 -*-

##############################################################
# Author: Stratio Clouds <clouds-integration@stratio.com>    #
# Supported provisioner versions: 0.4.X                      #
# Supported cloud providers:                                 #
#   - EKS                                                    #
#   - Azure VMs                                              #
#   - GKE                                                    #
##############################################################

__version__ = "0.7.0"

import argparse
import os
import sys
import json
import subprocess
import yaml
import base64
import logging
import re
import zlib
import time
from datetime import datetime
from ansible_vault import Vault
from jinja2 import Template, Environment, FileSystemLoader
from ruamel.yaml import YAML
from io import StringIO

CLOUD_PROVISIONER = "0.17.0-0.6"
# TODO: Change when releasing cluster-operator
CLUSTER_OPERATOR = "0.5.0-M1" 
CLUSTER_OPERATOR_UPGRADE_SUPPORT = "0.4.X"
CLOUD_PROVISIONER_LAST_PREVIOUS_RELEASE = "0.17.0-0.6"

AWS_LOAD_BALANCER_CONTROLLER_CHART = "1.8.1"

CLUSTERCTL = "v1.7.2"

CAPI = "v1.7.4"
CAPA = "v2.5.2"
CAPG = "1.6.1-0.2.0"
CAPZ = "v1.12.4"

# Chart Versions
eks_chart_versions = {
    "30": {
        "cluster-autoscaler": {"chart_version": "9.37.0"},
        "cluster-operator": {"chart_version": "0.5.0"},
        "tigera-operator": {"chart_version": "v3.29.1"},
        "aws-load-balancer-controller": {"chart_version": "1.8.1"},
        "flux": {"chart_version": "2.14.1"},
        "flux2": {"chart_version": "2.14.1"}
    },
    "31": {
        "cluster-autoscaler": {"chart_version": "9.46.0"},
        "cluster-operator": {"chart_version": "0.5.0"},
        "tigera-operator": {"chart_version": "v3.29.1"},
        "aws-load-balancer-controller": {"chart_version": "1.11.0"},
        "flux": {"chart_version": "2.14.1"},
        "flux2": {"chart_version": "2.14.1"}
    },
    "32": {
        "cluster-autoscaler": {"chart_version": "9.46.0"},
        "cluster-operator": {"chart_version": "0.5.0"},
        "tigera-operator": {"chart_version": "v3.29.1"},
        "aws-load-balancer-controller": {"chart_version": "1.11.0"},
        "flux": {"chart_version": "2.14.1"},
        "flux2": {"chart_version": "2.14.1"},
        "cert-manager": {"chart_version": "v1.17.0"}
    }
}

azure_vm_chart_versions = {
    "30": {
        "azuredisk-csi-driver": {"chart_version": "v1.30.1"},
        "azurefile-csi-driver": {"chart_version": "v1.30.2"},
        "cloud-provider-azure": {"chart_version": "v1.30.4"},
        "cluster-autoscaler": {"chart_version": "9.37.0"},
        "tigera-operator": {"chart_version": "v3.29.1"},
        "cluster-operator": {"chart_version": "0.5.0"},
        "flux": {"chart_version": "2.14.1"}
    },
    "31": {
        "azuredisk-csi-driver": {"chart_version": "v1.31.2"},
        "azurefile-csi-driver": {"chart_version": "v1.31.2"},
        "cloud-provider-azure": {"chart_version": "v1.31.1"},
        "cluster-autoscaler": {"chart_version": "9.46.0"},
        "tigera-operator": {"chart_version": "v3.29.1"},
        "cluster-operator": {"chart_version": "0.5.0"},
        "flux": {"chart_version": "2.14.1"}
    },
    "32": {
        "azuredisk-csi-driver": {"chart_version": "v1.31.2"},
        "azurefile-csi-driver": {"chart_version": "v1.31.2"},
        "cloud-provider-azure": {"chart_version": "v1.32.0"},
        "cluster-autoscaler": {"chart_version": "9.46.0"},
        "tigera-operator": {"chart_version": "v3.29.1"},
        "cluster-operator": {"chart_version": "0.5.0"},
        "flux": {"chart_version": "2.14.1"},
        "cert-manager": {"chart_version": "v1.17.0"}
    }
}


namespaces = {
    'aws-cloud-controller-manager': 'kube-system',
    'aws-load-balancer-controller': 'kube-system',
    'aws-ebs-csi-driver': 'kube-system',
    'azuredisk-csi-driver': 'kube-system',
    'azurefile-csi-driver': 'kube-system',
    'cloud-provider-azure': 'kube-system',
    'cluster-autoscaler': 'kube-system',
    'calico': 'tigera-operator',
    'tigera-operator': 'tigera-operator',
    'cert-manager': 'cert-manager',
    "flux": "kube-system",
    "flux2": "kube-system",
    "cluster-operator": "kube-system"
}


#Updatable Charts
updatable_charts = ["cluster-autoscaler", "cloud-provider-azure", "cert-manager"]

# Definir repositorios específicos
specific_repos = {
    'aws-cloud-controller-manager': 'https://kubernetes.github.io/cloud-provider-aws',
    'aws-load-balancer-controller': 'https://aws.github.io/eks-charts',
    'aws-ebs-csi-driver': 'https://kubernetes-sigs.github.io/aws-ebs-csi-driver',
    'azuredisk-csi-driver': 'https://raw.githubusercontent.com/kubernetes-sigs/azuredisk-csi-driver/master/charts',
    'azurefile-csi-driver': 'https://raw.githubusercontent.com/kubernetes-sigs/azurefile-csi-driver/master/charts',
    'cloud-provider-azure': 'https://raw.githubusercontent.com/kubernetes-sigs/cloud-provider-azure/master/helm/repo',
    'cluster-autoscaler': 'https://kubernetes.github.io/autoscaler',
    'tigera-operator': 'https://docs.projectcalico.org/charts',
    'cert-manager': 'https://charts.jetstack.io',
    "flux": "https://fluxcd-community.github.io/helm-charts",
    "flux2": "https://fluxcd-community.github.io/helm-charts",
    "cluster-operator": ""
}


# Crear entorno de Jinja2 para cargar las plantillas
template_dir = './templates'
env = Environment(loader=FileSystemLoader(template_dir))

# Cargar plantillas
helmrepository_template = env.get_template('helmrepository_template.yaml')
helmrelease_template = env.get_template('helmrelease_template.yaml')

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
    parser.add_argument("--enable-lb-controller", action="store_true", help="Install AWS Load Balancer Controller for EKS clusters (disabled by default)")
    parser.add_argument("--disable-backup", action="store_true", help="Disable backing up files before upgrading (enabled by default)")
    parser.add_argument("--disable-prepare-capsule", action="store_true", help="Disable preparing capsule for the upgrade process (enabled by default)")
    parser.add_argument("--dry-run", action="store_true", help="Do not upgrade components. This invalidates all other options")
    parser.add_argument("--upgrade-cloud-provisioner-only", action="store_true", help="Prepare the upgrade process for the cloud-provisioner upgrade only")
    parser.add_argument("--skip-k8s-intermediate-version", action="store_true", help="Skip workers intermediate kubernetes version upgrade")
    args = parser.parse_args()
    return vars(args)

def backup(backup_dir, namespace, cluster_name, dry_run):
    '''Backup CAPX and capsule files'''
    
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
    '''Prepare capsule for the upgrade process'''
    
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
    '''Restore capsule after the upgrade process'''
    
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

def install_lb_controller(cluster_name, account_id, dry_run):
    '''Install AWS Load Balancer Controller for EKS clusters'''
    
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
    '''Patch aws-node ClusterRole'''

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

def scale_cluster_autoscaler(replicas, dry_run):
    '''Scale cluster-autoscaler deployment'''
    
    command = kubectl + " get deploy cluster-autoscaler-clusterapi-cluster-autoscaler -n kube-system -o=jsonpath='{.spec.replicas}'"
    output = execute_command(command, False, False)
    current_replicas = int(output)
    if current_replicas > replicas:
        scaling_type = "Scaling down"
    elif current_replicas < replicas:
        scaling_type = "Scaling up"
    else:
        scaling_type = "Scaling"
    print("[INFO] " + scaling_type + " cluster autoscaler replicas:", end =" ", flush=True)
    if dry_run:
        print("DRY-RUN")
    elif scaling_type == "Scaling":
        print("SKIP")
    else:
        command = kubectl + " scale deploy cluster-autoscaler-clusterapi-cluster-autoscaler -n kube-system --replicas=" + str(replicas)
        output = execute_command(command, False)
    
def validate_k8s_version(validation, dry_run):
    '''Validate the Kubernetes version to upgrade'''
    
    if validation == "first":
        minor = "31"
        dry_run_version = "1.31.X"
    elif validation == "second":
        minor = "32"
        dry_run_version = "1.32.X"
    if not dry_run:
        desired_k8s_version = upgrade_k8s_version_desired_version(minor, 0)

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
    
def upgrade_k8s_version_desired_version(minor, tries):
    '''Get the Kubernetes desired version'''

    supported_k8s_versions = r"^1\.("+ minor +")\.\d+$"
    desired_k8s_version = input("Please provide the Kubernetes version to which you want to upgrade: ")
    if not re.match(supported_k8s_versions, desired_k8s_version):
        if tries > 0:
            print("[ERROR] The only supported Kubernetes versions are: 1."+ minor +".X")
            sys.exit(1)
        else:
            print("[ERROR] Unsupported downgrade of Kubernetes Version. The only supported Kubernetes versions are: 1."+ minor +".X")
            upgrade_k8s_version_desired_version(minor, tries + 1)
    return desired_k8s_version

def get_kubernetes_version():
    '''Get the Kubernetes version'''
    
    command = kubectl + " get nodes -ojsonpath='{range .items[*]}{.status.nodeInfo.kubeletVersion}{\"\\n\"}{end}' | sort | uniq"
    output = execute_command(command, False, False)

    return output.strip()

def wait_for_workers(cluster_name, current_k8s_version):
    '''Wait for the worker nodes to be updated'''
    
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
    '''Check if 'node_image' is defined in node_group'''
    
    return 'node_image' in node_group

def prompt_for_node_image(node_name, kubernetes_version):
    '''Prompt user for node image'''
    
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
    '''Get the lower version of the two Kubernetes versions'''
    
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
    '''Patch or restore the allow control plane GlobalNetworkPolicy'''
    
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
    '''Upgrade Kubernetes version'''

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
            
            command = (
            "kubectl wait --for=jsonpath=\"{.status.ready}\"=true KeosCluster "
            + cluster_name + " -n cluster-" + cluster_name + " --timeout 60m"
            )
            execute_command(command, False, False)
        

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
        # If upgrade had failed previously, the cluster may have two different versions of Kubernetes
        
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

def wait_for_keos_cluster(cluster_name, time):
    '''Wait for the KeosCluster to be ready'''
    
    command = (
        "kubectl wait --for=jsonpath=\"{.status.ready}\"=true KeosCluster "
        + cluster_name + " -n cluster-" + cluster_name + " --timeout "+time+"m"
    )
    execute_command(command, False, False)

def update_helm_registry(cluster_name, oci_registry, dry_run):
    '''Update the Helm registry'''
    
    wait_for_keos_cluster(cluster_name, "10")

    
    patch_helm_registry = [
        {"op": "replace", "path": "/spec/helm_repository/url", "value": oci_registry},
    ]

    patch_json = json.dumps(patch_helm_registry)
    command = f"{kubectl} -n cluster-{cluster_name} patch KeosCluster {cluster_name} --type='json' -p='{patch_json}'"
    execute_command(command, False, False)
    
    patch_helmRepository = [
        {"op": "replace", "path": "/spec/url", "value": oci_registry},
    ]
    patch_json = json.dumps(patch_helmRepository)
    existing_helmrepo, err = run_command(f"{kubectl} get helmrepository -n kube-system keos --ignore-not-found", allow_errors=True)
    if "doesn't have a resource type \"helmrepository\"" in err:
        existing_helmrepo = False
        
    if existing_helmrepo:
        command = f"{kubectl} -n kube-system patch helmrepository keos --type='json' -p='{patch_json}'"
        execute_command(command, False, False)
    
    wait_for_keos_cluster(cluster_name, "10")

def execute_command(command, dry_run, result = True, max_retries=3, retry_delay=5):
    '''Execute a command and handle the output'''
    
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
                retries += 1
                if retries < max_retries:
                    time.sleep(retry_delay)
                else:
                    print("FAILED")
                    print("[ERROR] " + output)
                    sys.exit(1)

def get_chart_version(chart, namespace):
    '''Get the version of a Helm chart'''
    
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

def get_keos_cluster_cluster_config():
    '''Get the KeosCluster and ClusterConfig objects'''
    
    try:
        keoscluster_list_output, err = run_command(kubectl + " get keoscluster -A -o json")
        keos_cluster = json.loads(keoscluster_list_output)["items"][0]
        clusterconfig_list_output, err = run_command(kubectl + " get clusterconfig -A -o json")
        cluster_config = json.loads(clusterconfig_list_output)["items"][0]
        return keos_cluster, cluster_config
    except Exception as e:
        print(f"[ERROR] {e}.")
        raise e
    
    
def run_command(command, allow_errors=False, retries=3, retry_delay=2):
    '''Run a command and return the output'''
    
    attempts = 0
    
    while attempts <= retries:
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        
        if result.returncode == 0:
            return result.stdout, result.stderr  
        
        # If the command fails and the error is allowed, return the result without raising an exception
        if allow_errors:
            return result.stdout, result.stderr
        
        # If the command fails and the error is not allowed, but there are retries left, wait and retry        
        attempts += 1
        if attempts > retries:
            raise Exception(f"Error executing '{command}' after {retries + 1} attempts: {result.stderr}")
        
        time.sleep(retry_delay)

def get_helm_registry_oci(keos_cluster):
    '''Get the Helm registry URL'''
    
    try:
        helm_registry_oci = keos_cluster["spec"]["helm_repository"]["url"]
        
        if helm_registry_oci:
            return helm_registry_oci
        else:
            return None
    except KeyError as e:
        return None

def get_deploy_version(deploy, namespace, container):
    '''Get the version of a deployment'''
    
    command = f"{kubectl} -n " + namespace + " get deploy " + deploy + " -o json  | jq -r '.spec.template.spec.containers[].image' | grep '" + container + "' | cut -d: -f2"
    output = execute_command(command, False, False)
    return output.split("@")[0]
        
def get_keos_registry_url(keos_cluster):
    '''Get the Keos registry URL'''
    
    docker_registries = keos_cluster["spec"]["docker_registries"]
    for registry in docker_registries:
        if registry.get("keos_registry", False):
            return registry["url"]
    return ""


def get_pods_cidr(keos_cluster):
    '''Get the pods CIDR'''
    
    try:
        return keos_cluster["spec"]["networks"]["pods_cidr"]
    except KeyError:
        return ""


def render_values_template(values_file, keos_cluster, cluster_config, credentials, cluster_operator_version):
    '''Render the values template'''
    
    try:
        values_params = {
            "private": cluster_config["spec"]["private_registry"],
            "cluster_name": keos_cluster["metadata"]["name"],
            "registry": get_keos_registry_url(keos_cluster),
            "provider": keos_cluster["spec"]["infra_provider"],
            "managed_cluster": keos_cluster["spec"]["control_plane"]["managed"],
            "pods_cidr": get_pods_cidr(keos_cluster),
            "cluster_operator_version" : cluster_operator_version,
            "credentials": credentials
        }
        
        template = env.get_template(values_file)
        rendered_values = template.render(values_params)
        return rendered_values
    except Exception as e:
        raise e
    
def update_chart_versions(keos_cluster, cluster_config, charts, crendentials, cluster_operator_version):
    '''Update the chart versions'''
    
    try:
        
        charts_updated = {}
        updated = False
        k8s_version = keos_cluster["spec"]["k8s_version"].split(".")[1]
        provider = keos_cluster["spec"]["infra_provider"]
        print(f"[INFO] Updating chart versions for Kubernetes {k8s_version} in {provider}:")
        for chart_name, chart_info in charts[k8s_version].items():
            print(f"[INFO] Updating chart {chart_name} to version {chart_info['chart_version']}:", end =" ", flush=True)
            chart_version = chart_info["chart_version"]
            if k8s_version == "30":
                updated = update_helmrelease_version(chart_name, namespaces.get(chart_name), chart_version)
                if chart_name == "cluster-operator":
                    file_type = "override" 
                    update_helmrelease_values(chart_name, namespaces.get(chart_name), f"values/{provider}/{chart_name}_{file_type}_values.tmpl", keos_cluster, cluster_config, credentials, cluster_operator_version, upgrade_cloud_provisioner_only)
            elif chart_name in updatable_charts:
                updated = update_helmrelease_version(chart_name, namespaces.get(chart_name), chart_version)
            else:
                print("SKIP")
            if updated and not chart_name == "cluster-operator":
                charts_updated[chart_name] = chart_version
            if k8s_version == "32" and chart_name == "cert-manager":
                file_type = "default"
                update_helmrelease_values(chart_name, namespaces.get(chart_name), f"values/{provider}/{chart_name}_{file_type}_values.tmpl", keos_cluster, cluster_config, credentials, cluster_operator_version, upgrade_cloud_provisioner_only)

        return charts_updated
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error updating chart versions: {e}")
        raise e

def update_helmrelease_version(chart_name, namespace, version):
    '''Update the version of a HelmRelease'''
    
    try:
        
        check_command = f"{kubectl} get helmrelease {chart_name} -n {namespace}"
        stdout, stderr = run_command(check_command, allow_errors=True)
        
        if "not found" in stderr.lower():
            print("SKIP")
            return  False

        update_command = f"{kubectl} get helmrelease {chart_name} -n {namespace} -o json | jq '.spec.chart.spec.version = \"{version}\"' | kubectl apply -f -"
        run_command(update_command)
        print("OK")
        return True
        
    except Exception as e:
        error_message = str(e)
        if "the object has been modified; please apply your changes to the latest version and try again" in error_message:
            print("[WARN] The object has been modified; please apply your changes to the latest version and try again. Ignoring the error for", chart_name)
            return False
        else:
            print("FAILED")
            print(f"[ERROR] Error updating the version of the chart {chart_name}: {e}")
            raise e

def update_helmrelease_values(chart_name, namespace, values_file, keos_cluster, cluster_config, credentials, cluster_operator_version, upgrade_cloud_provisioner_only):
    '''Update the values of a HelmRelease'''

    try:
        print(f"[INFO] Updating values for chart {chart_name} in namespace {namespace}:", end =" ", flush=True)
        
        values = render_values_template(values_file, keos_cluster, cluster_config, credentials, cluster_operator_version)
        values_json = json.dumps({"data": {"values.yaml": values}})
        
        cm_name = f"01-{chart_name}-helm-chart-override-values"
        
        if chart_name == "flux" and not upgrade_cloud_provisioner_only:
            cm_name = f"02-{chart_name}-helm-chart-override-values"
            
        command = f"{kubectl} patch configmap {cm_name} -n {namespace} --type merge -p '{values_json}'"
            
        run_command(command)
        print("OK")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error updating the values for chart {chart_name} in namespace {namespace}: {e}")
        raise e
            
def stop_keoscluster_controller():
    '''Stop the KEOSCluster controller'''
    
    try:
        print("[INFO] Stopping keoscluster-controller-manager deployment:", end =" ", flush=True)
        run_command(f"{kubectl} scale deployment -n kube-system keoscluster-controller-manager --replicas=0", allow_errors=True)

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
        
        run_command(f"{kubectl} delete validatingwebhookconfiguration keoscluster-validating-webhook-configuration", allow_errors=True)
        run_command(f"{kubectl} delete mutatingwebhookconfiguration keoscluster-mutating-webhook-configuration", allow_errors=True)
        print("OK")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error disabling KEOSCluster webhooks: {e}")
        raise e

def backup_keoscluster_webhooks():
    '''Backup the KEOSCluster webhooks'''

    try:
        if not os.path.exists(backup_dir+'/cluster-operator'):
            os.makedirs(backup_dir+'/cluster-operator')
        print("[INFO] Backing up KEOSCluster webhooks...")
        print("[INFO] Backup of validation webhooks:", end =" ", flush=True)
        validating_webhook = run_command(f"{kubectl} get validatingwebhookconfiguration keoscluster-validating-webhook-configuration -o json --ignore-not-found")
        if isinstance(validating_webhook, tuple):
            validating_webhook = validating_webhook[0]
        else:
            print("SKIP")
        if validating_webhook != "":
            validating_webhook_json = json.loads(validating_webhook)  
            
            with open(backup_dir+'/cluster-operator/keoscluster-validating-webhook-configuration-backup.json', 'w') as f:
                json.dump(validating_webhook_json, f, indent=4)
        else:
            print("SKIP")
        print("OK")
        
        print("[INFO] Backup of mutation webhooks:", end =" ", flush=True)
        mutating_webhook = run_command("kubectl get mutatingwebhookconfiguration keoscluster-mutating-webhook-configuration -o json --ignore-not-found")
        if isinstance(mutating_webhook, tuple):
            mutating_webhook = mutating_webhook[0]
        else:
            print("SKIP")
        if mutating_webhook != "":
            mutating_webhook_json = json.loads(mutating_webhook)  
            with open(backup_dir+'/cluster-operator/keoscluster-mutating-webhook-configuration-backup.json', 'w') as f:
                json.dump(mutating_webhook_json, f, indent=4)
        else:
            print("SKIP")
        
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
        
        cluster_config["spec"]["cluster_operator_version"] = cluster_operator_version
        
        for chart_name, chart_version in charts.items():
            existing_chart = next((chart for chart in cluster_config["spec"]["charts"] if chart["name"] == chart_name), None)
    
            if existing_chart:
                # Si existe, actualizar la versión
                existing_chart["version"] = chart_version
            else:
                # Si no existe, añadir una nueva entrada
                cluster_config["spec"]["charts"].append({"name": chart_name, "version": chart_version})
            # cluster_config["spec"]["charts"].append({"name": chart_name, "version": chart_version})
        clusterconfig_json = json.dumps(cluster_config)
        command = f"{kubectl} patch clusterconfig {clusterconfig_name} -n {clusterconfig_namespace} --type merge -p '{clusterconfig_json}'"
        output, err = run_command(command)
        print("OK")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error updating the clusterconfig: {e}")
        raise e
    
def restore_keoscluster_webhooks():
    '''Restore the KEOSCluster webhooks'''

    try:
        print("[INFO] Restoring KEOSCluster webhooks from backup...")
        print("[INFO] Restoring validation webhooks:", end =" ", flush=True)

        with open(backup_dir+'/cluster-operator/keoscluster-validating-webhook-configuration-backup.json', 'r') as f:
            validating_webhook = json.load(f)
            with open(backup_dir+'/cluster-operator/keoscluster-validating-webhook-configuration.yaml', 'w') as backup_file:
                yaml.dump(validating_webhook, backup_file)
            run_command(f"{kubectl} create -f {backup_dir}/cluster-operator/keoscluster-validating-webhook-configuration.yaml", allow_errors=True)
            print("OK")
        
        print("[INFO] Restoring mutation webhooks:", end =" ", flush=True)

        with open(backup_dir+'/cluster-operator/keoscluster-mutating-webhook-configuration-backup.json', 'r') as f:
            mutating_webhook = json.load(f)
            with open(backup_dir+'/cluster-operator/keoscluster-mutating-webhook-configuration.yaml', 'w') as backup_file:
                yaml.dump(mutating_webhook, backup_file)
            run_command(f"{kubectl} create -f {backup_dir}/cluster-operator/keoscluster-mutating-webhook-configuration.yaml", allow_errors=True)
            print("OK")
        
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error restoring KEOSCluster webhooks from backup: {e}")
        raise e

def start_keoscluster_controller():
    '''Start the KEOSCluster controller'''

    try:
        print("[INFO] Starting keoscluster-controller-manager deployment:", end =" ", flush=True)

        run_command(f"{kubectl} scale deployment -n kube-system keoscluster-controller-manager --replicas=2")
        run_command(f"{kubectl} wait --for=condition=Available deployment/keoscluster-controller-manager -n kube-system --timeout=300s")
        print("OK")

    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error starting the KEOSCluster controller: {e}")

        raise e

def update_configmap(namespace, configmap_name, key_to_update, yaml_key_to_remove):
    '''Update the ConfigMap'''

    try:
        print(f"[INFO] Updating the ConfigMap '{configmap_name}'. Removing {yaml_key_to_remove} from default values:", end=" ", flush=True)
        ryaml = YAML()
        ryaml.preserve_quotes = True  # Mantener las quotes y el formato original
        ryaml.default_flow_style = False

        command_get_cm = f"kubectl get configmap {configmap_name} -n {namespace} -o yaml"
        configmap_yaml = run_command(command_get_cm)
        if configmap_yaml is None:
            raise Exception(f"ConfigMap '{configmap_name}' does not exist in namespace '{namespace}'.")

        if isinstance(configmap_yaml, tuple):
            configmap_yaml = configmap_yaml[0]
            
        configmap_dict = ryaml.load(configmap_yaml)

        if "data" not in configmap_dict or key_to_update not in configmap_dict["data"]:
            raise Exception(f"The key '{key_to_update}' does not exist in the ConfigMap '{configmap_name}'.")
        
        data_yaml_content = configmap_dict["data"][key_to_update]
        data_dict = ryaml.load(data_yaml_content)

        if yaml_key_to_remove in data_dict:
            del data_dict[yaml_key_to_remove]
        else:
            print("SKIP")
            return

        stream = StringIO()
        ryaml.dump(data_dict, stream)
        formatted_yaml = stream.getvalue().rstrip('\n') 

        updated_yaml_escaped = formatted_yaml.replace('\n', '\\n').replace('"', '\\"')

        command_patch_cm = (
            f"kubectl patch configmap {configmap_name} -n {namespace} "
            f"--type merge -p '{{\"data\": {{\"{key_to_update}\": \"{updated_yaml_escaped}\"}}}}'"
        )
        run_command(command_patch_cm)

        print("OK")

    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error updating the ConfigMap '{configmap_name}': {e}")

def configure_aws_credentials(vault_secrets_data):
    print(f"[INFO] Configuring AWS CLI credentials", end=" ", flush=True)
    aws_access_key = vault_secrets_data['secrets']['aws']['credentials']['access_key']
    aws_secret_key = vault_secrets_data['secrets']['aws']['credentials']['secret_key']
    aws_region = vault_secrets_data['secrets']['aws']['credentials']['region']

    command = f"aws configure set aws_access_key_id {aws_access_key}; \
                aws configure set aws_secret_access_key {aws_secret_key}; \
                aws configure set region {aws_region} "

    run_command(command)
    print("OK")

def configure_azure_credentials(vault_secrets_data):
    print(f"[INFO] Configuring Azure CLI credentials", end=" ", flush=True)
    azure_client_id = vault_secrets_data['secrets']['azure']['credentials']['client_id']
    azure_client_secret = vault_secrets_data['secrets']['azure']['credentials']['client_secret']
    azure_subscription_id = vault_secrets_data['secrets']['azure']['credentials']['subscription_id']
    azure_tenant_id = vault_secrets_data['secrets']['azure']['credentials']['tenant_id']

    command = f"az login --service-principal --username {azure_client_id} \
                --password {azure_client_secret} --tenant {azure_tenant_id}"

    run_command(command)
    print("OK")


if __name__ == '__main__':
    start_time = time.time()
    backup_dir = "./backup/upgrade/"
    helm_repo = {}
    # Configurar el logger
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)

    # Parse arguments
    config = parse_args()

    # Set kubeconfig
    if os.environ.get("KUBECONFIG"):
        kubeconfig = os.environ.get("KUBECONFIG")
    else:
        kubeconfig = os.path.expanduser(config["kubeconfig"])

    command = "clusterctl version -o short"
    status, output = subprocess.getstatusoutput(command)
    if (status != 0) or (get_version(output) < get_version(CLUSTERCTL)):
        print("[ERROR] clusterctl version " + CLUSTERCTL + " is required")
        sys.exit(1)

    if not os.path.exists(config["secrets"]):
        print("[ERROR] Secrets file not found")
        sys.exit(1)
    if not os.path.exists(kubeconfig):
        print("[ERROR] Kubeconfig file not found")
        sys.exit(1)

    # Get secrets
    try:
        vault = Vault(config["vault_password"])
        vault_secrets_data = vault.load(open(config["secrets"]).read())
    except Exception as e:
        print("[ERROR] Decoding secrets file failed:\n" + str(e))
        sys.exit(1)

    # Configure aws CLI
    if 'aws' in vault_secrets_data['secrets']:
        configure_aws_credentials(vault_secrets_data)
    elif 'azure' in vault_secrets_data['secrets']:
        configure_azure_credentials(vault_secrets_data)

    print("[INFO] Using kubeconfig: " + kubeconfig)

    # Set kubectl
    kubectl = "kubectl --kubeconfig " + kubeconfig

    # Set helm
    helm = "helm --kubeconfig " + kubeconfig
    
    keos_cluster, cluster_config = get_keos_cluster_cluster_config()

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

    # Check supported upgrades
    provider = keos_cluster["spec"]["infra_provider"]
    managed = keos_cluster["spec"]["control_plane"]["managed"]
    if not ((provider == "aws" and managed) or (provider == "azure" and not managed) or (provider == "gcp" and managed)):
        print("[ERROR] Upgrade is only supported for EKS, GKE and Azure VMs clusters")
        sys.exit(1)

    # Check special flags
    upgrade_cloud_provisioner_only = config["upgrade_cloud_provisioner_only"]
    skip_k8s_intermediate_version = config["skip_k8s_intermediate_version"]
    if provider != "aws" and (skip_k8s_intermediate_version or upgrade_cloud_provisioner_only):
        print("[ERROR] --upgrade-cloud-provisioner-only and --skip-k8s-intermediate-version flags are only supported for EKS")
        sys.exit(1)

    # Set env vars
    env_vars = "CLUSTER_TOPOLOGY=true CLUSTERCTL_DISABLE_VERSIONCHECK=true GOPROXY=off"
    helm_registry_oci = get_helm_registry_oci(keos_cluster)
    helm_registry = input(f"The current helm repository is: {helm_registry_oci}. Do you want to indicate a new helm repository? Press enter or specify new repository: ")
    if helm_registry != "" and helm_registry != helm_registry_oci:
        update_helm_registry(cluster_name, helm_registry, config["dry_run"]) 

    # Update the clusterconfig and keoscluster
    keos_cluster, cluster_config = get_keos_cluster_cluster_config()
    cluster_operator_version = config["cluster_operator"]
    if provider == "aws":
        chart_versions = eks_chart_versions
    elif provider == "azure":
        chart_versions = azure_vm_chart_versions
    if cluster_operator_version != "0.5.0":
        if provider == "aws":
            for version_key, charts in chart_versions.items():
                if "cluster-operator" in charts.keys():
                    charts["cluster-operator"]["chart_version"] = cluster_operator_version
        elif provider == "azure":
            for version_key, charts in chart_versions.items():
                if "cluster-operator" in charts.keys():
                    charts["cluster-operator"]["chart_version"] = cluster_operator_version
    
    scale_cluster_autoscaler(0, config["dry_run"])
    
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
        if config['user_assign_identity'] == "":
            print("[ERROR] The flag --user-assign-identity must be indicated with azure provider")
            sys.exit(1)
        namespace = "capz-system"
        version = CAPZ
        if managed:
            env_vars += " EXP_MACHINE_POOL=true"
        if "credentials" in vault_secrets_data["secrets"]["azure"]:
            credentials = vault_secrets_data["secrets"]["azure"]["credentials"]
            env_vars += " AZURE_CLIENT_ID_B64=" + base64.b64encode(credentials["client_id"].encode("ascii")).decode("ascii")
            env_vars += " AZURE_CLIENT_SECRET_B64=" + base64.b64encode(credentials["client_secret"].encode("ascii")).decode("ascii")
            env_vars += " AZURE_SUBSCRIPTION_ID_B64=" + base64.b64encode(credentials["subscription_id"].encode("ascii")).decode("ascii")
            env_vars += " AZURE_TENANT_ID_B64=" + base64.b64encode(credentials["tenant_id"].encode("ascii")).decode("ascii")
        else:
            print("[ERROR] Azure credentials not found in secrets file")
            sys.exit(1)

    if "github_token" in vault_secrets_data["secrets"]:
        env_vars += " GITHUB_TOKEN=" + vault_secrets_data["secrets"]["github_token"]
        helm = "GITHUB_TOKEN=" + vault_secrets_data["secrets"]["github_token"] + " " + helm
        kubectl = "GITHUB_TOKEN=" + vault_secrets_data["secrets"]["github_token"] + " " + kubectl

    # Set helm repository
    helm_repo["url"] = keos_cluster["spec"]["helm_repository"]["url"]
    if "auth_required" in keos_cluster["spec"]["helm_repository"]:
        if keos_cluster["spec"]["helm_repository"]["auth_required"]:
            if "user" in vault_secrets_data["secrets"]["helm_repository"] and "pass" in vault_secrets_data["secrets"]["helm_repository"]:
                helm_repo["user"] = vault_secrets_data["secrets"]["helm_repository"]["user"]
                helm_repo["pass"] = vault_secrets_data["secrets"]["helm_repository"]["pass"]
            else:
                print("[ERROR] Helm repository credentials not found in secrets file")
                sys.exit(1)
                
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
            account_id = vault_secrets_data["secrets"]["aws"]["credentials"]["account_id"]
            install_lb_controller(cluster_name, account_id, config["dry_run"])
        else:
            print("[WARN] AWS LoadBalancer Controller is only supported for EKS clusters")
            sys.exit(0)

    # Cluster Operator
    # It is needed due to the changes in authentication with azure cloud from k8s kubelet
    if provider == "azure":
        update_configmap("kube-system", "00-metrics-server-helm-chart-default-values", "values.yaml", "affinity")
        update_configmap("kube-system", "00-metrics-server-helm-chart-default-values", "values.yaml", "tolerations")
   
    charts = update_chart_versions(keos_cluster, cluster_config, chart_versions, credentials, cluster_operator_version)
    
    # Restore capsule
    if not config["disable_prepare_capsule"]:
        restore_capsule(config["dry_run"])
    
    networks = keos_cluster["spec"].get("networks", {})
    current_k8s_version = get_kubernetes_version()
    
    if "1.30" in current_k8s_version:
        print("[INFO] Waiting for the cluster-operator helmrelease to be ready...")
        command = f"{kubectl} wait helmrelease cluster-operator -n kube-system --for=jsonpath='{{.status.conditions[?(@.type==\"Ready\")].status}}'=True --timeout=5m"
        run_command(command)
        print("[INFO] Upgrading Cluster Operator components...")
        print("[INFO] Stoping cluster-operator helmrelease:", end =" ", flush=True)

        command = kubectl + " patch helmrelease cluster-operator -n kube-system --type merge --patch '{\"spec\":{\"suspend\":true}}'"
        run_command(command)
        print("OK")
        
        stop_keoscluster_controller()
        disable_keoscluster_webhooks()
        update_clusterconfig(cluster_config, charts, provider, cluster_operator_version)
        keos_cluster, cluster_config = get_keos_cluster_cluster_config()
        provider = keos_cluster["spec"]["infra_provider"]
        restore_keoscluster_webhooks()
        start_keoscluster_controller()
        print("[INFO] Waiting for the cluster-operator helmrelease to be ready:", end =" ", flush=True)
        command = kubectl + " patch helmrelease cluster-operator -n kube-system --type merge --patch '{\"spec\":{\"suspend\":false}}'"
        run_command(command)
        command = kubectl + " wait helmrelease cluster-operator -n kube-system --for=condition=Ready --timeout=5m"
        run_command(command)
        print("OK")
        
        cluster_name = keos_cluster["metadata"]["name"]
        
        print("[INFO] Waiting for keoscluster to be ready:", end =" ", flush=True)
        
        command = (
            kubectl + " wait --for=jsonpath=\"{.status.ready}\"=true KeosCluster "
            + cluster_name + " -n cluster-" + cluster_name + " --timeout 5m"
        )
        execute_command(command, False)
                
        command = "kubectl wait deployment -n kube-system keoscluster-controller-manager --for=condition=Available --timeout=5m"
        run_command(command)
        
        if not skip_k8s_intermediate_version:
            keos_cluster, cluster_config = get_keos_cluster_cluster_config()
            required_k8s_version=validate_k8s_version("first", False)
            # Update kubernetes version to 1.31.X
            upgrade_k8s(cluster_name, keos_cluster["spec"]["control_plane"], keos_cluster["spec"]["worker_nodes"], networks, required_k8s_version, provider, managed, backup_dir, False)
        
        keos_cluster, cluster_config = get_keos_cluster_cluster_config()
        charts = update_chart_versions(keos_cluster, cluster_config, chart_versions, credentials, cluster_operator_version)
        current_k8s_version = get_kubernetes_version()
    
    if "1.31" in current_k8s_version or ("1.30" in current_k8s_version and skip_k8s_intermediate_version)::
        print("[INFO] Waiting for the cluster-operator helmrelease to be ready:", end =" ", flush=True)
        command = f"{kubectl} wait --for=condition=Available deployment/keoscluster-controller-manager -n kube-system --timeout=300s"
        run_command(command)
        command = f"{kubectl} wait helmrelease cluster-operator -n kube-system --for=condition=Ready --timeout=5m"
        run_command(command)
        print("OK")

        if skip_k8s_intermediate_version:
            # Prepare cluster-operator for skipping validations to avoid upgrading to k8s intermediate versions
            disable_keoscluster_webhooks()

        required_k8s_version=validate_k8s_version("second", False)
        keos_cluster, cluster_config = get_keos_cluster_cluster_config()
        # Update kubernetes version to 1.32.X
        upgrade_k8s(cluster_name, keos_cluster["spec"]["control_plane"], keos_cluster["spec"]["worker_nodes"], networks, required_k8s_version, provider, managed, backup_dir, False)

        if skip_k8s_intermediate_version:
            restore_keoscluster_webhooks()

        keos_cluster, cluster_config = get_keos_cluster_cluster_config()
        charts = update_chart_versions(keos_cluster, cluster_config, chart_versions, credentials, cluster_operator_version)
    
    if not managed:
        cp_global_network_policy("restore", networks, provider, backup_dir, False)
        
    scale_cluster_autoscaler(2, config["dry_run"])
   
    end_time = time.time()
    elapsed_time = end_time - start_time
    minutes, seconds = divmod(elapsed_time, 60)
    print("[INFO] Upgrade process finished successfully in " + str(int(minutes)) + " minutes and " + "{:.2f}".format(seconds) + " seconds")
