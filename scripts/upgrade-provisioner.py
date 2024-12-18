#!/usr/bin/env python3
# -*- coding: utf-8 -*-

##############################################################
# Author: Stratio Clouds <clouds-integration@stratio.com>    #
# Supported provisioner versions: 0.4.X                      #
# Supported cloud providers:                                 #
#   - AWS VMs & EKS                                          #
#   - GCP VMs                                                #
#   - Azure VMs & AKS                                        #
##############################################################

__version__ = "0.5.0"

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

CLOUD_PROVISIONER = "0.17.0-0.6.0"
CLUSTER_OPERATOR = "0.4.0" 
CLUSTER_OPERATOR_UPGRADE_SUPPORT = "0.2.0"
CLOUD_PROVISIONER_LAST_PREVIOUS_RELEASE = "0.17.0-0.5.0"

AWS_LOAD_BALANCER_CONTROLLER_CHART = "1.8.1"

CLUSTERCTL = "v1.7.2"

CAPI = "v1.7.4"
CAPA = "v2.5.2"
CAPG = "1.6.1-0.2.0"
CAPZ = "v1.12.4"

#Chart Versions
eks_chart_versions = {
    "28": {
        "cluster-autoscaler": {"chart_version": "9.34.1", "app_version": "1.28.1"},
        "cluster-operator": {"chart_version": "0.4.0-aa18b1f", "app_version": "0.4.0-aa18b1f"},
        "tigera-operator": {"chart_version": "v3.28.2", "app_version": "v3.28.2"},
        "aws-load-balancer-controller": {"chart_version": "1.8.0", "app_version": "v2.8.0"},
        # "cert-manager": {"chart_version": "v1.14.5", "app_version": "v1.14.5"},
        #"flux2": {"chart_version": "2.12.2", "app_version": "2.2.2"},
        "flux": {"chart_version": "2.12.2", "app_version": "2.2.2"}
    },
    "29": {
        "cluster-autoscaler": {"chart_version": "9.35.0", "app_version": "1.29.0"},
        "cluster-operator": {"chart_version": "0.4.0-aa18b1f", "app_version": "0.4.0-aa18b1f"},
        "tigera-operator": {"chart_version": "v3.28.2", "app_version": "v3.28.2"},
        "aws-load-balancer-controller": {"chart_version": "1.8.0", "app_version": "v2.8.0"},
        # "cert-manager": {"chart_version": "v1.14.5", "app_version": "v1.14.5"},
        #"flux2": {"chart_version": "2.12.2", "app_version": "2.2.2"},
        "flux": {"chart_version": "2.12.2", "app_version": "2.2.2"}
    },
    "30": {
        "cluster-autoscaler": {"chart_version": "9.37.0", "app_version": "1.30.0"},
        "cluster-operator": {"chart_version": "0.4.0-aa18b1f", "app_version": "0.4.0-aa18b1f"},
        "tigera-operator": {"chart_version": "v3.28.2", "app_version": "v3.28.2"},
        "aws-load-balancer-controller": {"chart_version": "1.8.1", "app_version": "v2.8.1"},
        # "cert-manager": {"chart_version": "v1.14.5", "app_version": "v1.14.5"},
        #"flux2": {"chart_version": "2.12.2", "app_version": "2.2.2"},
        "flux": {"chart_version": "2.12.2", "app_version": "2.2.2"}
    }
}

azure_vm_chart_versions = {
    "28": {
        "azuredisk-csi-driver": {"chart_version": "v1.30.1", "app_version": "v1.30.1"},
        "azurefile-csi-driver": {"chart_version": "v1.30.2", "app_version": "v1.30.2"},
        "cloud-provider-azure": {"chart_version": "v1.28.5", "app_version": "v1.28.7"},
        "cluster-autoscaler": {"chart_version": "9.34.1", "app_version": "1.28.1"},
        "tigera-operator": {"chart_version": "v3.28.2", "app_version": "v3.28.2"},
        "cluster-operator": {"chart_version": "0.4.0-aa18b1f", "app_version": "0.4.0-aa18b1f"},
       # "flux2": {"chart_version": "2.12.2", "app_version": "2.2.2"},
        "flux": {"chart_version": "2.12.2", "app_version": "2.2.2"}
    },
    "29": {
        "azuredisk-csi-driver": {"chart_version": "v1.30.1", "app_version": "v1.30.1"},
        "azurefile-csi-driver": {"chart_version": "v1.30.2", "app_version": "v1.30.2"},
        "cloud-provider-azure": {"chart_version": "v1.29.0", "app_version": "v1.29.0"},
        "cluster-autoscaler": {"chart_version": "9.35.0", "app_version": "1.29.0"},
        "tigera-operator": {"chart_version": "v3.28.2", "app_version": "v3.28.2"},
        "cluster-operator": {"chart_version": "0.4.0-aa18b1f", "app_version": "0.4.0-aa18b1f"},
        #"flux2": {"chart_version": "2.12.2", "app_version": "2.2.2"},
        "flux": {"chart_version": "2.12.2", "app_version": "2.2.2"}
    },
    "30": {
        "azuredisk-csi-driver": {"chart_version": "v1.30.1", "app_version": "v1.30.1"},
        "azurefile-csi-driver": {"chart_version": "v1.30.2", "app_version": "v1.30.2"},
        "cloud-provider-azure": {"chart_version": "v1.30.4", "app_version": "v1.30.4"},
        "cluster-autoscaler": {"chart_version": "9.37.0", "app_version": "1.30.0"},
        "tigera-operator": {"chart_version": "v3.28.2", "app_version": "v3.28.2"},
        "cluster-operator": {"chart_version": "0.4.0-aa18b1f", "app_version": "0.4.0-aa18b1f"},
        #"flux2": {"chart_version": "2.12.2", "app_version": "2.2.2"},
        "flux": {"chart_version": "2.12.2", "app_version": "2.2.2"}
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
        # "flux2": "kube-system",
        "flux": "kube-system",
        "cluster-operator": "kube-system"
    }
        
        
#Updatable Charts
updatable_charts = ["cluster-autoscaler", "cloud-provider-azure"]

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
    # "flux2": "https://fluxcd-community.github.io/helm-charts",
    "flux": "https://fluxcd-community.github.io/helm-charts",
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
        description='''This script upgrades cloud-provisioner from 0.17.0-0.4.0 to ''' + CLOUD_PROVISIONER +
                    ''' by upgrading mainly cluster-operator from ''' + CLUSTER_OPERATOR_UPGRADE_SUPPORT + ''' to ''' + CLUSTER_OPERATOR + ''' .
                        It requires kubectl, helm and jq binaries in $PATH.
                        A component (or all) must be selected for upgrading.
                        By default, the process will wait for confirmation for every component selected for upgrade.''',
                                    formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("-y", "--yes", action="store_true", help="Do not wait for confirmation between tasks")
    parser.add_argument("-k", "--kubeconfig", help="Set the kubeconfig file for kubectl commands, It can also be set using $KUBECONFIG variable", default="~/.kube/config")
    parser.add_argument("-p", "--vault-password", help="Set the vault password file for decrypting secrets", required=True)
    parser.add_argument("-s", "--secrets", help="Set the secrets file for decrypting secrets", default="secrets.yml")
    parser.add_argument("-i", "--user-assign-identity", help="Set the secrets file for decrypting secrets")
    # parser.add_argument("-d", "--descriptor", help="Set the cluster descriptor file", default="cluster.yaml")
    parser.add_argument("--enable-lb-controller", action="store_true", help="Install AWS Load Balancer Controller for EKS clusters (disabled by default)")
    parser.add_argument("--disable-backup", action="store_true", help="Disable backing up files before upgrading (enabled by default)")
    parser.add_argument("--disable-prepare-capsule", action="store_true", help="Disable preparing capsule for the upgrade process (enabled by default)")
    parser.add_argument("--dry-run", action="store_true", help="Do not upgrade components. This invalidates all other options")
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
    aws_node_clusterrole = """
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: aws-node
rules:
  - apiGroups:
      - crd.k8s.amazonaws.com
    resources:
      - eniconfigs
    verbs: ["list", "watch", "get"]
  - apiGroups: [""]
    resources:
      - namespaces
    verbs: ["list", "watch", "get"]
  - apiGroups: [""]
    resources:
      - pods
    verbs: ["list", "watch", "get", "patch"]
  - apiGroups: [""]
    resources:
      - nodes
    verbs: ["list", "watch", "get"]
  - apiGroups: ["", "events.k8s.io"]
    resources:
      - events
    verbs: ["create", "patch", "list"]
  - apiGroups: ["networking.k8s.aws"]
    resources:
      - policyendpoints
    verbs: ["get", "list", "watch"]
  - apiGroups: ["networking.k8s.aws"]
    resources:
      - policyendpoints/status
    verbs: ["get"]
  - apiGroups:
      - vpcresources.k8s.aws
    resources:
      - cninodes
    verbs: ["get", "list", "watch", "patch"]
"""
    print("[INFO] Modifying aws-node ClusterRole:", end =" ", flush=True)
    if not dry_run:
        command = "cat <<EOF | " + kubectl + " apply -f -" + aws_node_clusterrole + "EOF"
        execute_command(command, False)
    else:
        print("DRY-RUN")

def scale_cluster_autoscaler(replicas, dry_run):
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
    if validation == "first":
        minor = "29"
        dry_run_version = "1.29.X"
    elif validation == "second":
        minor = "30"
        dry_run_version = "1.30.X"
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

            if not aks_enabled:
                scale_cluster_autoscaler(0, dry_run)

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

            if not aks_enabled:
                scale_cluster_autoscaler(2, dry_run)

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

        if not aks_enabled:
            scale_cluster_autoscaler(2, dry_run)

    else:
        print("[FAILED] More than two different versions of Kubernetes are in the cluster, which requires human action", flush=True)
        sys.exit(1)

def wait_for_keos_cluster(cluster_name, time):
    command = (
        "kubectl wait --for=jsonpath=\"{.status.ready}\"=true KeosCluster "
        + cluster_name + " -n cluster-" + cluster_name + " --timeout "+time+"m"
    )
    execute_command(command, False, False)

def update_helm_registry(cluster_name, oci_registry, dry_run):
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
        #logger.info("Updating existing keos HelmRepository")
        command = f"{kubectl} -n kube-system patch helmrepository keos --type='json' -p='{patch_json}'"
        execute_command(command, False, False)
    
    wait_for_keos_cluster(cluster_name, "10")
    
def update_docker_registry(cluster_name, docker_registry, dry_run):
    wait_for_keos_cluster(cluster_name, "10")
    index = -1
    keos_cluster, cluster_config = get_keos_cluster_cluster_config()
    
    # Verificar que el campo docker_registries existe
    if "docker_registries" in keos_cluster["spec"]:
        for i, registry in enumerate(keos_cluster["spec"]["docker_registries"]):
            if registry.get("keos_registry"):
                index = i
                break
    
    # Si no se encuentra un índice válido, lanzar una excepción
    if index == -1:
        raise ValueError("A Registry with keos_registry: true was not found")
    
    patch_docker_registry = [
        {"op": "replace", "path": f"/spec/docker_registries/{index}/url", "value": docker_registry},
    ]
    patch_json = json.dumps(patch_docker_registry)
    command = f"{kubectl} -n cluster-{cluster_name} patch KeosCluster {cluster_name} --type='json' -p='{patch_json}'"
    
    if not dry_run:
        execute_command(command, False, False)
    
    wait_for_keos_cluster(cluster_name, "10")

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
    for line in output.split("\n"):
        splitted_line = line.split()
        if chart == splitted_line[0]:
            if chart == "cluster-operator":
                return splitted_line[9]
            else:
                return splitted_line[8].split("-")[-1]
    return None

def get_version(version):
    return re.sub(r'\D', '', version)

def print_upgrade_support():
    print("[WARN] Upgrading cloud-provisioner from a version minor than " + CLOUD_PROVISIONER_LAST_PREVIOUS_RELEASE + " to " + CLOUD_PROVISIONER + " is NOT SUPPORTED")
    print("[WARN] You have to upgrade to cloud-provisioner:"+ CLOUD_PROVISIONER_LAST_PREVIOUS_RELEASE + " first")
    sys.exit(0)

def request_confirmation():
    enter = input("Press ENTER to continue upgrading the cluster or any other key to abort: ")
    if enter != "":
        sys.exit(0)

def get_keos_cluster_cluster_config():
    try:
        #logger.info("Obteniendo keoscluster...")
        keoscluster_list_output, err = run_command(kubectl + " get keoscluster -A -o json")
        keos_cluster = json.loads(keoscluster_list_output)["items"][0]
        clusterconfig_list_output, err = run_command(kubectl + " get clusterconfig -A -o json")
        cluster_config = json.loads(clusterconfig_list_output)["items"][0]
        return keos_cluster, cluster_config
    except Exception as e:
        print(f"[ERROR] {e}.")
        #logger.error(f"Error al obtener la lista de keoscluster: {e}")
        raise e
    
    
def run_command(command, allow_errors=False, retries=3, retry_delay=2):
    attempts = 0
    
    while attempts <= retries:
        #logger.debug(f"Ejecutando comando: {command} (Intento {attempts + 1}/{retries + 1})")
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        
        if result.returncode == 0:
            return result.stdout, result.stderr  # Comando exitoso, retornar resultados
        
        # Si el comando falla y se permite el error, devolver el resultado sin excepción
        if allow_errors:
            #logger.warning(f"Comando fallido con error permitido '{command}': {result.stderr}")
            return result.stdout, result.stderr
        
        # Si el comando falla y no se permite el error, pero quedan reintentos, esperar y reintentar
        #logger.error(f"Error ejecutando '{command}': {result.stderr}")
        
        attempts += 1
        if attempts > retries:
            # Si se han agotado los reintentos, lanzar excepción
            raise Exception(f"Error executing '{command}' after {retries + 1} attempts: {result.stderr}")
        
        # Espera antes del próximo intento
        time.sleep(retry_delay)
        #logger.info(f"Reintentando comando '{command}'...")

def get_docker_registry(keos_cluster):
    try:
        # Obtener el valor de docker_registry
        docker_registries = keos_cluster["spec"]["docker_registries"]
        
        for i, docker_registry in enumerate(docker_registries):
            if docker_registry["keos_registry"]:
                return docker_registry["url"]
            else:
                continue
    except KeyError as e:
        # Si falta alguna de las claves en el diccionario, manejar la excepción
        #logger.info(f"Error: clave no encontrada {e}")
        return None

def get_helm_registry_oci(keos_cluster):
    try:
        # Obtener el valor de helm_repository.url
        helm_registry_oci = keos_cluster["spec"]["helm_repository"]["url"]
        
        # Comprobar si helm_registry_oci tiene un valor válido
        if helm_registry_oci:
            #logger.info(f"El valor de helm_registry_oci es: {helm_registry_oci}")
            return helm_registry_oci
        else:
            #logger.info("El campo 'helm_repository.url' está vacío.")
            return None
    except KeyError as e:
        # Si falta alguna de las claves en el diccionario, manejar la excepción
        #logger.info(f"Error: clave no encontrada {e}")
        return None

# Verificar si Flux está instalado
def check_flux_installed():
    print("[INFO] Installing Flux:", end =" ", flush=True)
    #logger.info("Comprobando si Flux está instalado...")
    try:
        charts = get_installed_helm_charts()
        for chart in charts:
            if chart['name'] == 'flux':
                print("SKIP")
                #logger.info("Flux está instalado.")
                return True
        #logger.warning("Flux no está instalado. Procediendo a instalarlo...")
        return False
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error checking flux installation{e}.")
        #logger.error("Error al verificar la instalación de Flux.")
        raise e

# Listar todos los Helm charts instalados en el clúster
def get_installed_helm_charts():
    #logger.info("Obteniendo lista de Helm charts instalados en el clúster...")
    try:
        output, err = run_command(helm  + " list --all-namespaces --output json")
        charts = json.loads(output)
        #logger.info(f"Se encontraron {len(charts)} charts instalados.")
        return charts
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error getting charts installed {e}.")
        #logger.error("Error al obtener la lista de charts instalados.")
        raise e

# Instalar Flux si no está presente
def install_flux(provider):
    repository_url = "https://fluxcd-community.github.io/helm-charts"
    chart_name = "flux2"
    release_name = "flux"
    chart_version = "2.12.2"
    namespace = "kube-system"
    values_file = "files/flux-values.yaml"

    try:
        #logger.info("Añadiendo el repositorio de Flux...")
        run_command(f"{helm} repo add fluxcd {repository_url}")
        #logger.info("Actualizando el repositorio de Flux...")
        run_command(f"{helm} repo update")  # Actualizar el repositorio
        #logger.info("Instalando Flux...")
        run_command(f"{helm} install {release_name} fluxcd/{chart_name} --version {chart_version} -n {namespace} --create-namespace --values {values_file}")
        if provider == "azure":
            #logger.info("Creando AzurePodIdentityException...")
            install_azurePodIdentityException()
        #logger.info("Flux ha sido instalado exitosamente.")
        print("OK")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] {e}.")
        #logger.error(f"Error al instalar Flux: {e}")
        raise
    
def install_azurePodIdentityException():
    try:
        azurePodIdentityException = """
---
apiVersion: aadpodidentity.k8s.io/v1
kind: AzurePodIdentityException
metadata:
  name: flux-source-controller
  namespace: kube-system
spec:
  podLabels:
    app: source-controller
"""
        command = f"cat <<EOF | {kubectl} apply -f -" + azurePodIdentityException + "EOF"
        output, err = run_command(command, allow_errors=True)
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error installing AzurePodIdentityException {e}.")
        raise
    
def update_allow_global_netpol(provider):
    try:
        print("[INFO] Updating IMDS GlobalNetworkPolicy:", end =" ", flush=True)
        globalnetpol_name = "allow-traffic-to-aws-imds-capa"
        # Actualizar el valor del nombre globalnetpol_name
        if provider == "azure":
            print("SKIP")
            return
        elif provider == "gcp":
            globalnetpol_name = "allow-traffic-to-gcp-imds-capa"
            
        #logger.info("Comprobando si la política global existe en el cluster...")
        check_command = f"{kubectl} get globalnetworkpolicies.crd.projectcalico.org "
        check_result, err = run_command(check_command)
        #logger.info(f"Resultado de la comprobación: {check_result}")
        if globalnetpol_name in check_result:
            #logger.info(f"Eliminando la política {globalnetpol_name} existente...")
            delete_command = f"{kubectl} delete globalnetworkpolicies.crd.projectcalico.org {globalnetpol_name}"
            run_command(delete_command)

            
        #logger.info("Aplicando la nueva política global...")
        globalnetpol_allow = ""
        if provider == "aws":
            globalnetpol_allow = """
---
apiVersion: crd.projectcalico.org/v1
kind: GlobalNetworkPolicy
metadata:
  name: allow-traffic-to-aws-imds
spec:
  egress:
  - action: Allow
    destination:
      nets:
      - 169.254.169.254/32
    protocol: TCP
  order: 0
  namespaceSelector: kubernetes.io/metadata.name in { 'kube-system', 'capa-system' }
  selector: app.kubernetes.io/name == 'aws-load-balancer-controller' || cluster.x-k8s.io/provider == 'infrastructure-aws' || k8s-app == 'aws-cloud-controller-manager' || app in {'ebs-csi-controller', 'source-controller'}
  types:
  - Egress
"""

        command = f"cat <<EOF | {kubectl} apply -f -" + globalnetpol_allow + "EOF"
        run_command(command)
        #logger.info("Valor de allow_global_netpol actualizado con éxito.")
        print("OK")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] {e}.")
        #logger.error("Error al actualizar el valor de allow_global_netpol.")
        raise e
    
def upgrade_capx(managed, provider, namespace, version, env_vars):
    # CAPI = "v1.7.4"
    print("[INFO] Upgrading " + namespace.split("-")[0] + " to " + version + " and capi to " + CAPI + ":", end =" ", flush=True)
    capx_version = get_deploy_version(namespace.split("-")[0] + "-controller-manager", namespace, "controller")
    capi_version = get_deploy_version("capi-controller-manager", "capi-system", "controller")
    if capx_version == version and capi_version == CAPI:
        print("SKIP")
    else:
        command = (env_vars + " clusterctl upgrade apply --wait-providers" +
                    " --core capi-system/cluster-api:" + CAPI +
                    " --infrastructure " + namespace + "/" + provider + ":" + version)
        if not managed:
            command += ( " --bootstrap capi-kubeadm-bootstrap-system/kubeadm:" + CAPI +
                " --control-plane capi-kubeadm-control-plane-system/kubeadm:" + CAPI )
                    
        #logger.info(f"Ejecutando comando: {command}")
        execute_command(command, False)
        if provider == "azure":
            command =  f"{kubectl} -n " + namespace + " rollout status ds capz-nmi --timeout 120s"
            execute_command(command, False, False)

    deployments = [
        {"name": namespace.split("-")[0] + "-controller-manager", "namespace": namespace},
        {"name": "capi-controller-manager", "namespace": "capi-system"}
    ]
    if not managed:
        deployments.append({"name": "capi-kubeadm-control-plane-controller-manager", "namespace": "capi-kubeadm-control-plane-system"})
        deployments.append({"name": "capi-kubeadm-bootstrap-controller-manager", "namespace": "capi-kubeadm-bootstrap-system"})
    for deploy in deployments:
        print("[INFO] Setting priorityClass system-node-critical to " + deploy["name"] + ":", end =" ", flush=True)
        command =  f"{kubectl} -n " + deploy["namespace"] + " get deploy " + deploy["name"] + " -o jsonpath='{.spec.template.spec.priorityClassName}'"
        priorityClassName = execute_command(command, False, False)
        if priorityClassName == "system-node-critical":
            print("SKIP")
        else:
            command = f"{kubectl} -n " + deploy["namespace"] + " patch deploy " + deploy["name"] + " -p '{\"spec\": {\"template\": {\"spec\": {\"priorityClassName\": \"system-node-critical\"}}}}' --type=merge"
            execute_command(command, False, False)
            command =  f"{kubectl}  -n " + deploy["namespace"] + " rollout status deploy " + deploy["name"] + " --timeout 120s"
            execute_command(command, False)

    replicas = "2"
    print("[INFO] Scaling " + namespace.split("-")[0] + "-controller-manager to " + replicas + " replicas:", end =" ", flush=True)
    command = f"{kubectl}  -n " + namespace + " scale --replicas " + replicas + " deploy " + namespace.split("-")[0] + "-controller-manager"
    execute_command(command, False)
    print("[INFO] Scaling capi-controller-manager to " + replicas + " replicas:", end =" ", flush=True)
    command = f"{kubectl}  -n capi-system scale --replicas " + replicas + " deploy capi-controller-manager"
    execute_command(command, False)

    # For AKS/EKS clusters scale capi-kubeadm-control-plane-controller-manager and capi-kubeadm-bootstrap-controller-manager to 0 replicas
    if managed:
        replicas = "0"
    print("[INFO] Scaling capi-kubeadm-control-plane-controller-manager to " + replicas + " replicas:", end =" ", flush=True)
    command = f"{kubectl}  -n capi-kubeadm-control-plane-system scale --replicas " + replicas + " deploy capi-kubeadm-control-plane-controller-manager"
    execute_command(command, False)
    print("[INFO] Scaling capi-kubeadm-bootstrap-controller-manager to " + replicas + " replicas:", end =" ", flush=True)
    command = f"{kubectl}  -n capi-kubeadm-bootstrap-system scale --replicas " + replicas + " deploy capi-kubeadm-bootstrap-controller-manager"
    execute_command(command, False)

# Function que compruebe si existen los pdbs en el namespace stratio-genai y elimine todos los pdbs
def delete_stratio_genai_pdb():
    try:
        print("[INFO] Disabling PodDisruptionBudgets in stratio-genai namespace:", end =" ", flush=True)
        #logger.info("Comprobando si existen los pdb en el namespace stratio-genai...")
        command = f"{kubectl} get pdb -n stratio-genai"
        output, err = run_command(command)
        if output:
            #logger.info("Eliminando los pdb en el namespace stratio-genai...")
            command = f"{kubectl} delete pdb --all -n stratio-genai"
            run_command(command)
            print("OK")
            #logger.info("Los pdb en el namespace stratio-genai han sido eliminados.")
        else:
            print("SKIP")
            #logger.info("No se encontraron pdb en el namespace stratio-genai.")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] {e}.")
        #logger.error("Error al eliminar los pdb en el namespace stratio-genai.")
        raise e

def get_deploy_version(deploy, namespace, container):
    command = f"{kubectl} -n " + namespace + " get deploy " + deploy + " -o json  | jq -r '.spec.template.spec.containers[].image' | grep '" + container + "' | cut -d: -f2"
    output = execute_command(command, False, False)
    return output.split("@")[0]


# Obtener lista de Helm charts y adoptarlos uno por uno
def adopt_all_helm_charts(keos_cluster, credentials, specific_charts):
    charts = get_installed_helm_charts()
    for chart in charts:
        try:
            if chart['name'] == 'calico':
                chart['name'] = 'tigera-operator'
            if chart['name'] in specific_charts["28"] and chart['name'] != "flux":
                print(f"[INFO] Adopting chart {chart['name']} in namespace {chart['namespace']}:", end =" ", flush=True)
                chart['provider'] = keos_cluster["spec"]["infra_provider"]
                adopt_helm_chart(chart, credentials)
                
                #logger.info(f"Ignorando chart {chart['name']} (no se encuentra en la lista de charts específicos).")
        except Exception as e:
            print("FAILED")
            print(f"[ERROR] {e}")
                #logger.error(f"Error al adoptar el chart {chart['name']} en el namespace {chart['namespace']}: {e}")

def check_releases(namespace):
    #command = ["helm", "list", "--namespace", namespace, "--output", "json"]
    command = f"{helm} list --namespace {namespace} --output json"
    try:
        # Ejecutar el comando y obtener la salida en formato JSON
        #result = subprocess.run(command, capture_output=True, text=True, check=True)
        result, err = run_command(command)
        releases = json.loads(result)
        
        # Verificar la existencia de las releases
        calico_exists = any(release['name'] == 'calico' for release in releases)
        tigera_operator_exists = any(release['name'] == 'tigera-operator' for release in releases)

        return calico_exists, tigera_operator_exists

    except Exception as e:
        print(f"[ERROR] {e}")

def delete_release(release_name, namespace):
    resources = [
            {"kind": "ServiceAccount", "name": "tigera-operator", "namespace": "tigera-operator"},
            {"kind": "ClusterRole", "name": "tigera-operator"},
            {"kind": "ClusterRoleBinding", "name": "tigera-operator"},
            {"kind": "Deployment", "name": "tigera-operator", "namespace": "tigera-operator"},
            {"kind": "Installation", "name": "default"}
        ]
    update_annotation_label("calico", "tigera-operator", "helm.sh/resource-policy", "keep", resources)
    command = f"{helm} uninstall {release_name} --namespace {namespace}"
    run_command(command, False)
    # delete_command = ["helm", "uninstall", release_name, "--namespace", namespace]
    # subprocess.run(delete_command, check=True)
        
def check_and_delete_releases(namespace):
    attempts = 0
    max_attempts = 10
    while attempts < max_attempts:
        attempts += 1
        # logger.info(f"Intento {attempts} de {max_attempts} para verificar las releases...")

        calico_exists, tigera_operator_exists = check_releases(namespace)

        if calico_exists and tigera_operator_exists:
            delete_release("calico", namespace)  # Eliminar solo "calico"
            # logger.info("Ambas releases están presentes. Se ha eliminado 'calico'.")
            break
        else:
            # logger.info("No se encontraron ambas releases, esperando 20 segundos...")
            time.sleep(20)
        
# Generar y aplicar HelmRelease y HelmRepository
def adopt_helm_chart(chart, credentials):
    
    #logger.info(f"Adoptando chart {chart} ...")
    chart_name, chart_version = chart["chart"].rsplit("-", 1)
    #logger.info(f"chart_name: {chart_name}")
    
    # Verificar si ya existe un HelmRelease con el nombre chart_name
    existing_helmrelease, err = run_command(f"{kubectl} get helmrelease {chart_name} -n {chart['namespace']} --ignore-not-found")
    if existing_helmrelease:
        print("SKIP")
        #logger.info(f"HelmRelease {chart_name} ya existe en el namespace {chart['namespace']}. No se realizará ninguna acción.")
        return
    
    schema = "default"
    repo = specific_repos[chart_name]
    repo_name = chart_name
    user = ""
    password = ""
    # Ignorar charts no listados en `specific_repos`
    if chart_name not in specific_repos:
        print("SKIP")
        #logger.info(f"Ignorando chart {chart_name} (no se encuentra en la lista de repositorios específicos).")
        return
    
    # Imprimir el chart que se va a adoptar
    #logger.info(f"Adoptando chart {chart['name']} en el namespace {chart['namespace']}.")
    if chart_name in "cluster-operator":
        #logger.info("El chart cluster-operator no tiene un repositorio específico. Recuperando...")
        repo =  keos_cluster["spec"]["helm_repository"]["url"]
        #logger.info(f"El repositorio de Helm para cluster-operator es {repo}")
        schema = "oci"
        repo_name = "keos"
        
    if chart_name == "tigera-operator":
        #logger.info("Actualizando la anotación...")
        resources = [
            {"kind": "ServiceAccount", "name": "tigera-operator", "namespace": "tigera-operator"},
            {"kind": "ClusterRole", "name": "tigera-operator"},
            {"kind": "ClusterRoleBinding", "name": "tigera-operator"},
            {"kind": "Deployment", "name": "tigera-operator", "namespace": "tigera-operator"},
            {"kind": "Installation", "name": "default"}
        ]
        update_annotation_label("tigera-operator", "tigera-operator", "meta.helm.sh/release-name", "tigera-operator", resources)
        
    default_values_file = f"/tmp/{chart_name}_default_values.yaml"
    release_values_file = f"/tmp/{chart_name}_release_values.yaml"
    empty_values_file = f"/tmp/{chart_name}_empty_values.yaml"
    
    export_release_values(chart_name, chart['namespace'], release_values_file, chart['provider'], credentials)
       
    if chart_name == "cert-manager":
        export_default_values(chart, repo, default_values_file)
        create_configmap_from_values(f"00-{chart_name}-helm-chart-default-values", chart['namespace'], default_values_file)
        create_configmap_from_values(f"01-{chart_name}-helm-chart-override-values", chart['namespace'], release_values_file)
    else: 
        create_empty_values_file(empty_values_file)
        create_configmap_from_values(f"00-{chart_name}-helm-chart-default-values", chart['namespace'], release_values_file)
        create_configmap_from_values(f"01-{chart_name}-helm-chart-override-values", chart['namespace'], empty_values_file)
    
    if namespaces.get(chart_name):
        namespace = namespaces[chart_name]
    else:
        namespace = "kube-system"
        
    # Contexto para HelmRepository
    repository_context = {
        'repository_name': repo_name,
        'namespace': chart['namespace'],
        'interval': '10m',
        'repository_url': repo,
        'schema': schema,
        'provider': chart['provider'],
        'username': user,
        'password': password
    }
    
    # Contexto para HelmRelease
    release_context = {
        'ChartName': chart_name,
        'ChartNamespace': chart['namespace'],
        'ChartVersion': chart_version,
        'ChartRepoRef': repo_name,
        'HelmReleaseSourceInterval': '1m',
        'HelmReleaseInterval': '1m',
        'HelmReleaseRetries': 3
    }

    # Renderizar YAML de HelmRepository y HelmRelease
    try:
        #logger.info("Renderizando plantilla HelmRepository...")
        helmrepository_yaml = helmrepository_template.render(repository_context)
        #logger.info("Renderizando plantilla HelmRelease...")
        helmrelease_yaml = helmrelease_template.render(release_context)
    except Exception as e:
        #logger.error(f"Error renderizando plantillas para el chart {chart['name']}: {e}")
        raise e

    # Guardar en archivos temporales
    try:
        repository_file = f'/tmp/{release_context["ChartName"]}_helmrepository.yaml'
        release_file = f'/tmp/{release_context["ChartName"]}_helmrelease.yaml'

        #logger.info(f"Escribiendo HelmRepository en {repository_file}...")
        with open(repository_file, 'w') as f:
            f.write(helmrepository_yaml)

        #logger.info(f"Escribiendo HelmRelease en {release_file}...")
        with open(release_file, 'w') as f:
            f.write(helmrelease_yaml)
    except Exception as e:
        #logger.error(f"Error al escribir archivos temporales para {chart['name']}: {e}")
        raise e
    

    # Aplicar los manifiestos al clúster
    try:
        command = f"{kubectl} apply -f {repository_file} "
        #logger.info(f"Aplicando HelmRepository para {chart['name']} en el namespace {chart['namespace']}...")
        run_command(command)
        
        command = f"{kubectl} apply -f {release_file} -n {chart['namespace']}"
        #logger.info(f"Aplicando HelmRelease para {chart['name']} en el namespace {chart['namespace']}...")
        run_command(command)
        
        # logger.info(f"Adoptado chart {chart['name']} en el namespace {chart['namespace']}.")
        if chart_name == "tigera-operator":
            check_and_delete_releases("tigera-operator")
        
        print("OK")

    except Exception as e:
        #logger.error(f"Error al aplicar HelmRepository o HelmRelease para {chart['name']}: {e}")
        raise e
    
def update_annotation_label(name, namespace, annotation_label_key, annotation_label_value, resources, type="annotation"):
    
    for resource in resources:
        kind = resource["kind"]
        name = resource["name"]
        ns = resource.get("namespace")
        action_type = "annotate"
        if type == "label":
            action_type = "label"
        try: 
            command = f"{kubectl} get {kind} {name} "
            #log = f"Comprobando la existencia de {kind} {name} "
            if ns:
                command = command + f" -n {ns}"
                #log = log + f"en el namespace {ns}..."
            #logger.info(log)
            output, err = run_command(command, allow_errors=True)
            if "not found" in err.lower():
                
                #logger.warning(f"{kind} {name} no encontrado. No se aplicará ninguna acción.")
                continue
        except Exception as e:
            print("FAILED")
            print(f"[ERROR] Error checking the existence of {kind} {name}: {e}")
            #logger.error(f"Error al comprobar la existencia de {kind} {name}: {e}")
            return
        
        command = f"{kubectl} {action_type} {kind} {name} {annotation_label_key}={annotation_label_value} --overwrite "
        #log = f"Aplicando {action_type} {annotation_label_key} en el {kind} {name} "
        if ns:
            command = command + f" -n {ns}"
            #log = log + f"en el namespace {ns}..."
        #logger.info(log)
        #logger.info(f"command: {command}")
        output, err = run_command(command)
        
def get_keos_registry_url(keos_cluster):
    docker_registries = keos_cluster["spec"]["docker_registries"]
    for registry in docker_registries:
        if registry.get("keos_registry", False):
            return registry["url"]
    return ""


def get_pods_cidr(keos_cluster):
    try:
        return keos_cluster["spec"]["networks"]["pods_cidr"]
    except KeyError:
        return ""


def render_values_template(values_file, keos_cluster, cluster_config, credentials):
    try:
        #logger.info(f"Renderizando el template de valores {values_file}...")
        # creds = get_credentials(keos_cluster["spec"]["infra_provider"])
        values_params = {
            "private": cluster_config["spec"]["private_registry"],
            "cluster_name": keos_cluster["metadata"]["name"],
            "registry": get_keos_registry_url(keos_cluster),
            "provider": keos_cluster["spec"]["infra_provider"],
            "managed_cluster": keos_cluster["spec"]["control_plane"]["managed"],
            "pods_cidr": get_pods_cidr(keos_cluster),
            "cluster_operator_version" : "0.4.0-aa18b1f",
            "credentials": credentials
        }
        #logger.info(f"Parametros para el template de valores: {values_params}")
        # Cargar la plantilla
        template = env.get_template(values_file)
        # Renderizar la plantilla
        rendered_values = template.render(values_params)
        #logger.info(f"Plantilla de valores renderizada con éxito, values: {rendered_values}.")
        return rendered_values
    except Exception as e:
        logger.error(f"Error al renderizar el template de valores: {e}")
        raise e
    
def prepare_calico_kube_controller(n):

    #logger.info("Preparando el controlador de Calico...")
    netpol = """
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-all-egress
  namespace: calico-system
spec:
  egress:
  - {}
  podSelector: {}
  policyTypes:
  - Egress
"""
    command = "cat <<EOF | kubectl apply -f -" + netpol + "EOF"
    output, err = run_command(command, allow_errors=True)
    
    rollout_command = "kubectl rollout restart -n calico-system deployment calico-kube-controllers"
    run_command(rollout_command)
    
def restart_tigera_operator_manifest(provider, tigera_version="v3.28.2"):
    try:
        #logger.info("Comprobando version de installation")
        command = kubectl+ " get installation.operator.tigera.io/default -n tigera-operator -o jsonpath='{.status.calicoVersion}'"
        output, err = run_command(command, allow_errors=True)
        if output == tigera_version:
            check_and_delete_releases("tigera-operator")
            prepare_calico_kube_controller(1)
            command = f"{kubectl} wait --for=condition=Ready installation.operator.tigera.io/default  -n tigera-operator --timeout=300s"
            #logger.info(f"Command: {command}")
            output = execute_command(command, False, result=False, max_retries=6, retry_delay=5)
            print("SKIP")
            #logger.info("La version de installation es la misma que la version de Tigera")
            return
        #logger.info("Reiniciando el operador de Tigera...")
        command = f'{kubectl} wait --for=jsonpath="{{.spec.version}}"={tigera_version} helmchart -n kube-system tigera-operator-tigera-operator --timeout=3m'        
        #logger.info(f"Command: {command}")
        output = execute_command(command, False, result=False, max_retries=6, retry_delay=5)
        #logger.info(f"Output: {output}")
        command = f"{kubectl} wait --namespace tigera-operator --for=condition=Ready helmrelease/tigera-operator --timeout=5m"
        #logger.info(f"Command: {command}")
        output = execute_command(command, False, result=False, max_retries=6, retry_delay=5)
        #logger.info(f"Output: {output}")
        command = f"{kubectl} wait --for=condition=Available deploy -n tigera-operator tigera-operator --all --timeout=300s"
        #logger.info(f"Command: {command}")
        output = execute_command(command, False, result=False, max_retries=6, retry_delay=5)
        #logger.info(f"Output: {output}")
        if provider == "aws":
            time.sleep(120)
        if provider == "azure":
            
            command = "kubectl wait --namespace=calico-system --for=condition=Ready=False pods --timeout=300s --all"
            #logger.info(f"Command: {command}")
            output = run_command(command)
            #logger.info(f"Output: {output}")
            # logger.info("Paramos 4 minutos para que se reinicie el operador de Tigera...")
            # time.sleep(300)
            command = "kubectl delete po -n calico-system --all --force --grace-period=0"
            #logger.info(f"Command: {command}")
            output = execute_command(command, False, result=False, max_retries=6, retry_delay=5)
            #logger.info(f"Output: {output}")
            command = "kubectl wait --for=delete pods --all --namespace=calico-system --timeout=300s"
            #logger.info(f"Command: {command}")
            output = execute_command(command, False, result=False, max_retries=6, retry_delay=5)
            #logger.info(f"Output: {output}")
            
        command = "helm get manifest tigera-operator -n tigera-operator | kubectl apply -f -"
        #logger.info(f"Command: {command}")
        output = execute_command(command, False, result=False, max_retries=6, retry_delay=5)
        if provider == "azure" and "installation.operator.tigera.io/default configured" in output:
            time.sleep(120)
            restart_tigera_operator_manifest(provider, tigera_version)
        prepare_calico_kube_controller(1)
        #logger.info(f"Output: {output}")
        if provider == "aws":
            prepare_calico_kube_controller(1)
            command = f"{kubectl} wait --for=condition=Ready installation.operator.tigera.io/default  -n tigera-operator --timeout=300s"
            #logger.info(f"Command: {command}")
            output = execute_command(command, False, result=False, max_retries=6, retry_delay=5)
            #logger.info(f"Output: {output}")
            #prepare_calico_kube_controller(1)
        command = f"{kubectl} wait --for=condition=Ready installation.operator.tigera.io/default  -n tigera-operator --timeout=300s"
        #logger.info(f"Command: {command}")
        output, err = run_command(command)
        #logger.info(f"Output: {output}")
        command = f"{kubectl} wait --for=condition=Available deployment -n calico-system --all --timeout=300s"
        #logger.info(f"Command: {command}")
        output = execute_command(command, False, result=False, max_retries=6, retry_delay=5)
        #logger.info(f"Output: {output}")
        #logger.info("Operador de Tigera reiniciado con éxito.")
        print("OK")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] {e}.")
        #logger.error("Error al reiniciar el operador de Tigera.")
        raise e

def export_release_values(chart_name, namespace, release_values_file, provider, credentials):
    """Exporta los valores de un release de Helm instalado."""
    try:
        name = chart_name
        allow_errors = False
        #logger.info(f"Exportando valores del release {chart_name} en el namespace {namespace}...")
        #logger.info(f"chart_name: {chart_name}")
        if chart_name == "cert-manager":
            #logger.info(f"El chart {chart_name} no requiere valores de release; creando archivo vacío en {release_values_file}...")
            open(release_values_file, 'w').close()  # Crea un archivo vacío
            #logger.info(f"Archivo de valores del release {release_values_file} creado vacío para {chart_name}.")
            return
        elif chart_name == "cluster-operator":
            values, err = run_command(f"{helm} get values {name} -n {namespace} --output yaml > {release_values_file}", allow_errors=allow_errors)
            #logger.info(f"comando: helm get values {name} -n {namespace} --output yaml > {release_values_file}")
            #logger.info(f"Valores del release exportados exitosamente en {release_values_file}.")
        else:
            values = render_values_template( f"values/{provider}/{chart_name}_default_values.tmpl", keos_cluster, cluster_config, credentials)
            run_command(f"echo '{values}' > {release_values_file}")
            #logger.info(f"Valores del release exportados exitosamente en {release_values_file}.")
        return values
    except Exception as e:
        #logger.error(f"Error al exportar los valores del release {name}: {e}")
        raise

def create_empty_values_file(values_file):
    try:
        #logger.info(f"Creando archivo de valores vacío en {values_file}...")
        open(values_file, 'w').close()  # Crea un archivo vacío
        #logger.info(f"Archivo de valores vacío creado.")
    except Exception as e:
        #logger.error(f"Error al crear archivo de valores vacío: {e}")
        raise e

def export_default_values(chart, repo, default_values_file):
    """Exporta los valores predeterminados de un chart de Helm."""
    try: 
        #logger.info(f"Exportando valores predeterminados para {chart}...")
        chart_name, chart_version = chart["chart"].rsplit("-", 1)
        command = f"{helm} show values --repo {repo} --version {chart_version} {chart_name}> {default_values_file}"
        if chart['name'] == "cluster-operator":
            command = f"{helm} show values {repo}/{chart_name} --version {chart['app_version']} > {default_values_file}"
        
        #logger.info(f"Exportando valores predeterminados para {chart_name} y version {chart_version}...")
        default_values, err = run_command(command)
        #logger.info(f"Valores predeterminados exportados exitosamente en {default_values_file}.")
        return default_values
    except Exception as e:
        #logger.error(f"Error al exportar los valores predeterminados de {chart_name}: {e}")
        raise
    
    
def create_configmap_from_values(configmap_name, namespace, values_file):
    #logger.info(f"Creando ConfigMap {configmap_name} en el namespace {namespace}...")
    try:
        command = f"{kubectl} create configmap {configmap_name} -n {namespace} --from-file=values.yaml={values_file} --dry-run=client -o yaml | kubectl apply -f -"
        run_command(command)
        #logger.info(f"ConfigMap {configmap_name} creado con éxito.")
    except Exception as e:
        #logger.error(f"Error al crear ConfigMap {configmap_name}: {e}")
        raise e

def install_cert_manager(provider):
    try:
        print("[INFO] Adopting cert-manager...")
        chart_cert_manager = {
            'name': 'cert-manager',
            'namespace': 'cert-manager',
            'chart': 'cert-manager-v1.14.5',
            'app_version': 'v1.14.5',
            'provider': provider
        }
        # Verificar si ya existe un HelmRelease con el nombre cert-manager
        existing_helmrelease, err = run_command(f"{kubectl} get helmrelease cert-manager -n cert-manager --ignore-not-found")
        if existing_helmrelease:
            #logger.info(f"HelmRelease cert-manager ya existe en el namespace cert-manager. No se realizará ninguna acción.")
            return
        
        #logger.info("Instalando cert-manager...")
        resources_service_accounts = [
            {"kind": "ServiceAccount", "name": "cert-manager-cainjector", "namespace": "cert-manager"},
            {"kind": "ServiceAccount", "name": "cert-manager", "namespace": "cert-manager"},
            {"kind": "ServiceAccount", "name": "cert-manager-webhook", "namespace": "cert-manager"},
            {"kind": "ClusterRole", "name": "cert-manager-cainjector"},
            {"kind": "ClusterRole", "name": "cert-manager-controller-issuers"},
            {"kind": "ClusterRole", "name": "cert-manager-cluster-view"},
            {"kind": "ClusterRole", "name": "cert-manager-controller-approve:cert-manager-io"},
            {"kind": "ClusterRole", "name": "cert-manager-controller-certificates"},
            {"kind": "ClusterRole", "name": "cert-manager-controller-certificatesigningrequests"},
            {"kind": "ClusterRole", "name": "cert-manager-controller-challenges"},
            {"kind": "ClusterRole", "name": "cert-manager-controller-clusterissuers"},
            {"kind": "ClusterRole", "name": "cert-manager-controller-ingress-shim"},
            {"kind": "ClusterRole", "name": "cert-manager-controller-orders"},
            {"kind": "ClusterRole", "name": "cert-manager-edit"},
            {"kind": "ClusterRole", "name": "cert-manager-view"},
            {"kind": "ClusterRole", "name": "cert-manager-webhook:subjectaccessreviews"},
            {"kind": "ClusterRoleBinding", "name": "cert-manager-cainjector"},
            {"kind": "ClusterRoleBinding", "name": "cert-manager-controller-approve:cert-manager-io"},
            {"kind": "ClusterRoleBinding", "name": "cert-manager-controller-certificates"},
            {"kind": "ClusterRoleBinding", "name": "cert-manager-controller-certificatesigningrequests"},
            {"kind": "ClusterRoleBinding", "name": "cert-manager-controller-challenges"},
            {"kind": "ClusterRoleBinding", "name": "cert-manager-controller-clusterissuers"},
            {"kind": "ClusterRoleBinding", "name": "cert-manager-controller-ingress-shim"},
            {"kind": "ClusterRoleBinding", "name": "cert-manager-controller-issuers"},
            {"kind": "ClusterRoleBinding", "name": "cert-manager-controller-orders"},
            {"kind": "ClusterRoleBinding", "name": "cert-manager-webhook:subjectaccessreviews"},
            {"kind": "Role", "name": "cert-manager-cainjector:leaderelection", "namespace": "kube-system"},
            {"kind": "Role", "name": "cert-manager:leaderelection", "namespace": "kube-system"},
            {"kind": "Role", "name": "cert-manager-webhook:dynamic-serving", "namespace": "cert-manager"},
            {"kind": "RoleBinding", "name": "cert-manager-cainjector:leaderelection", "namespace": "kube-system"},
            {"kind": "RoleBinding", "name": "cert-manager:leaderelection", "namespace": "kube-system"},
            {"kind": "RoleBinding", "name": "cert-manager-webhook:dynamic-serving", "namespace": "cert-manager"},
            {"kind": "Service", "name": "cert-manager", "namespace": "cert-manager"},
            {"kind": "Service", "name": "cert-manager-webhook", "namespace": "cert-manager"},
            {"kind": "Deployment", "name": "cert-manager", "namespace": "cert-manager"},
            {"kind": "Deployment", "name": "cert-manager-webhook", "namespace": "cert-manager"},
            {"kind": "Deployment", "name": "cert-manager-cainjector", "namespace": "cert-manager"},
            {"kind": "MutatingWebhookConfiguration", "name": "cert-manager-webhook"},
            {"kind": "ValidatingWebhookConfiguration", "name": "cert-manager-webhook"},
            {"kind": "CustomResourceDefinition", "name": "certificaterequests.cert-manager.io"},
            {"kind": "CustomResourceDefinition", "name": "certificates.cert-manager.io"},
            {"kind": "CustomResourceDefinition", "name": "challenges.acme.cert-manager.io"},
            {"kind": "CustomResourceDefinition", "name": "clusterissuers.cert-manager.io"},
            {"kind": "CustomResourceDefinition", "name": "issuers.cert-manager.io"},
            {"kind": "CustomResourceDefinition", "name": "orders.acme.cert-manager.io"},
        ]
        # Annotating and labeling existing resources
        print("[INFO] Labeling existing resources:", end =" ", flush=True)
        update_annotation_label("cert-manager", "cert-manager", "app.kubernetes.io/managed-by", "Helm", resources_service_accounts, "label")
        print("OK")
        print("[INFO] Annotating with 'meta.helm.sh/release-name' existing resources:", end =" ", flush=True)
        update_annotation_label("cert-manager", "cert-manager", "meta.helm.sh/release-name", "cert-manager", resources_service_accounts)
        print("OK")
        print("[INFO] Annotating with 'meta.helm.sh/release-namespace' existing resources:", end =" ", flush=True)
        update_annotation_label("cert-manager", "cert-manager", "meta.helm.sh/release-namespace", "cert-manager", resources_service_accounts)
        print("OK")
        
        print("[INFO] Adopted cert-manager:", end =" ", flush=True)
        adopt_helm_chart(chart_cert_manager, "")
        #logger.info("cert-manager instalado con éxito.")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] {e}")
        #logger.error("Error al instalar cert-manager.")
        raise e
    
def update_chart_versions(keos_cluster, cluster_config, charts, crendentials):
    try:
        
        charts_updated = {}
        updated = False
        k8s_version = keos_cluster["spec"]["k8s_version"].split(".")[1]
        provider = keos_cluster["spec"]["infra_provider"]
        print(f"[INFO] Updating chart versions for Kubernetes {k8s_version} in {provider}:")
        #logger.info(f"Actualizando las versiones de los charts para Kubernetes {k8s_version} en {provider}...")
        for chart_name, chart_info in charts[k8s_version].items():
            print(f"[INFO] Updating chart {chart_name} to version {chart_info['chart_version']}:", end =" ", flush=True)
            #logger.info(f"Actualizando el chart {chart_name}...")
            # Obtener la versión actual del chart
            chart_version = chart_info["chart_version"]
            # Obtener la versión actual de la aplicación
            app_version = chart_info["app_version"]
            # Actualizar la versión del chart si es diferente
            if k8s_version == "28":
                updated = update_helmrelease_version(chart_name, namespaces.get(chart_name), chart_version)
            elif chart_name in updatable_charts:
                #logger.info(f"Dentro de updatable_charts. Actualizando el chart {chart_name} a la versión {app_version}...")
                updated = update_helmrelease_version(chart_name, namespaces.get(chart_name), chart_version)
            else:
                print("SKIP")
            if updated and not chart_name == "cluster-operator":
                charts_updated[chart_name] = chart_version
            # Actualizar los valores del chart si es diferente
            # if k8s_version == "28" and updated:
            if k8s_version == "28" and updated and not chart_name == "tigera-operator":
                file_type = "default"
                if chart_name == "cluster-operator":
                    file_type = "override" 
                update_helmrelease_values(chart_name, namespaces.get(chart_name), f"values/{provider}/{chart_name}_{file_type}_values.tmpl", keos_cluster, cluster_config, credentials)
            
            #logger.info(f"Chart {chart_name} actualizado a la versión {chart_version} y valores actualizados.")
        return charts_updated
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error updating chart versions: {e}")
        #logger.error(f"Error al actualizar las versiones de los charts: {e}")
        raise e

# Función adaptada para actualizar la versión del HelmRelease
def update_helmrelease_version(chart_name, namespace, version):
    try:
        #logger.info(f"Actualizando la versión del chart {chart_name} en el namespace {namespace}...")
        
        # Comprobar si el HelmRelease existe antes de intentar actualizarlo
        check_command = f"{kubectl} get helmrelease {chart_name} -n {namespace}"
        stdout, stderr = run_command(check_command, allow_errors=True)
        
        if "not found" in stderr.lower():
            print("SKIP")
            #logger.warning(f"El HelmRelease {chart_name} no existe en el namespace {namespace}. Saliendo sin hacer cambios.")
            return  False# Salir de la función sin error si no existe

        # Ejecutar el comando de actualización si el HelmRelease existe
        update_command = f"{kubectl} get helmrelease {chart_name} -n {namespace} -o json | jq '.spec.chart.spec.version = \"{version}\"' | kubectl apply -f -"
        run_command(update_command)
        print("OK")
        #logger.info(f"Versión del chart {chart_name} actualizada a {version}.")
        return True
        
    except Exception as e:
        error_message = str(e)
        if "the object has been modified; please apply your changes to the latest version and try again" in error_message:
            #logger.warning(f"El objeto ha sido modificado; por favor aplique sus cambios a la última versión e intente nuevamente. Ignorando el error para {chart_name}.")
            print("[WARN] The object has been modified; please apply your changes to the latest version and try again. Ignoring the error for", chart_name)
            return False
        else:
            print("FAILED")
            print(f"[ERROR] Error updating the version of the chart {chart_name}: {e}")
            #logger.error(f"Error al actualizar la versión del chart {chart_name}: {e}")
            raise e

#Funcion para actualizar los values por defecto de un chart en helmrelease en el configmap
def update_helmrelease_values(chart_name, namespace, values_file, keos_cluster, cluster_config, credentials):
    try:
        print(f"[INFO] Updating values for chart {chart_name} in namespace {namespace}:", end =" ", flush=True)
        # Definicion de la plantilla de valores por defecto
        #logger.info(f"Actualizando los valores del chart {chart_name} en el namespace {namespace}...")
        values = render_values_template(values_file, keos_cluster, cluster_config, credentials)
        # Convertir a JSON para escapar correctamente caracteres especiales
        values_json = json.dumps({"data": {"values.yaml": values}})
        
        cm_name = f"01-{chart_name}-helm-chart-override-values"
        if chart_name == "flux":
            cm_name = f"02-{chart_name}-helm-chart-override-values"
        
        command = f"{kubectl} patch configmap {cm_name} -n {namespace} --type merge -p '{values_json}'"
            
        run_command(command)
        print("OK")
        #logger.info(f"Valores del chart {chart_name} actualizados con éxito.")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error updating the values for chart {chart_name} in namespace {namespace}: {e}")
        #logger.error(f"Error al actualizar los valores del chart {chart_name}: {e}")
        raise e

def patch_kubeadm_config_templates(namespace):
    #logger.info("Aplicando patch a los KubeadmConfigTemplates...")
    print("[INFO] Applying patch to KubeadmConfigTemplates:", end =" ", flush=True)
    # Comando kubectl para obtener los KubeadmConfigTemplate en formato JSON
    command = f"{kubectl} get kubeadmconfigtemplate -n {namespace} -o json"
    try:
        # Ejecutar el comando y obtener la salida
        result, err = run_command(command)
        #logger.info(f"Salida del comando: {result}")
        data = json.loads(result)
        #logger.info(f"Data: {data}")
        
        # Iterar sobre los items obtenidos y aplicar el patch a cada uno
        for item in data.get("items", []):
            name = item["metadata"]["name"]
            #logger.info(f"Aplicando patch a {name}...")
            
            patch = [
                {
                    "op": "remove",
                    "path": "/spec/template/spec/joinConfiguration/nodeRegistration/kubeletExtraArgs/azure-container-registry-config"
                }, {
                    "op": "add",
                    "path": "/spec/template/spec/joinConfiguration/nodeRegistration/kubeletExtraArgs/image-credential-provider-bin-dir",
                    "value": "/var/lib/kubelet/credential-provider"
                }, {
                    "op": "add",
                    "path": "/spec/template/spec/joinConfiguration/nodeRegistration/kubeletExtraArgs/image-credential-provider-config",
                    "value": "/var/lib/kubelet/credential-provider-config.yaml"
                }
            ]
            
            
            for subpatch in patch:
                command = f"{kubectl} patch kubeadmconfigtemplate {name} -n {namespace} --type=json -p '[{json.dumps(subpatch)}]'"
                run_command(command, allow_errors=True)
                
            # Ejecutar el comando patch para el KubeadmConfigTemplate
            # subprocess.run(cmd_patch, check=True)
        print(f"OK")
    
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] {e}")
        
def patch_kubeadm_controlplane(namespace):
    #logger.info("Aplicando patch a los KubeadmConfigTemplates...")
    print("[INFO] Applying patch to KubeadmControlPLane:", end =" ", flush=True)
    # Comando kubectl para obtener los KubeadmConfigTemplate en formato JSON
    command = f"{kubectl} get kubeadmcontrolplane -n {namespace} -o json"
    try:
        # Ejecutar el comando y obtener la salida
        result, err = run_command(command)
        if "image-credential-provider-bin-dir" in result and "image-credential-provider-config" in result and "azure-container-registry-config" not in result:
            print("SKIP")
            return
        #logger.info(f"Salida del comando: {result}")
        data = json.loads(result)
        #logger.info(f"Data: {data}")
        
        # Iterar sobre los items obtenidos y aplicar el patch a cada uno
        for item in data.get("items", []):
            name = item["metadata"]["name"]
            #logger.info(f"Aplicando patch a {name}...")
            
            patch = [
                {
                    "op": "remove",
                    "path": "/spec/kubeadmConfigSpec/joinConfiguration/nodeRegistration/kubeletExtraArgs/azure-container-registry-config"
                }, {
                    "op": "add",
                    "path": "/spec/kubeadmConfigSpec/joinConfiguration/nodeRegistration/kubeletExtraArgs/image-credential-provider-bin-dir",
                    "value": "/var/lib/kubelet/credential-provider"
                }, {
                    "op": "add",
                    "path": "/spec/kubeadmConfigSpec/joinConfiguration/nodeRegistration/kubeletExtraArgs/image-credential-provider-config",
                    "value": "/var/lib/kubelet/credential-provider-config.yaml"
                },{
                    "op": "remove",
                    "path": "/spec/kubeadmConfigSpec/initConfiguration/nodeRegistration/kubeletExtraArgs/azure-container-registry-config"
                }, {
                    "op": "add",
                    "path": "/spec/kubeadmConfigSpec/initConfiguration/nodeRegistration/kubeletExtraArgs/image-credential-provider-bin-dir",
                    "value": "/var/lib/kubelet/credential-provider"
                }, {
                    "op": "add",
                    "path": "/spec/kubeadmConfigSpec/initConfiguration/nodeRegistration/kubeletExtraArgs/image-credential-provider-config",
                    "value": "/var/lib/kubelet/credential-provider-config.yaml"
                }
            ]
            
            
            for subpatch in patch:
                command = f"{kubectl} patch kubeadmcontrolplane {name} -n {namespace} --type=json -p '[{json.dumps(subpatch)}]'"
                run_command(command, allow_errors=True)
                
            # Ejecutar el comando patch para el KubeadmConfigTemplate
            # subprocess.run(cmd_patch, check=True)
        print(f"OK")
        print("[INFO] Waiting to begin the updating kubelet process in controlplane nodes:", end =" ", flush=True)
        command = (
            kubectl + " wait --for=jsonpath=\"{.status.ready}\"=false KeosCluster "
            + cluster_name + " -n cluster-" + cluster_name + " --timeout 5m"
        )
        execute_command(command, False)
        print("[INFO] Waiting for the controlplane nodes to be recreated:", end =" ", flush=True)
        command = (
            kubectl + " wait --for=jsonpath=\"{.status.ready}\"=true KeosCluster "
            + cluster_name + " -n cluster-" + cluster_name + " --timeout 5m"
        )
        execute_command(command, False)
        
    
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] {e}")
            
def stop_keoscluster_controller():
    try:
        print("[INFO] Stopping keoscluster-controller-manager deployment:", end =" ", flush=True)
        #logger.info("Deteniendo el controlador de KEOSCluster...")
        run_command(f"{kubectl} scale deployment -n kube-system keoscluster-controller-manager --replicas=0", allow_errors=True)
        #logger.info("Controlador de KEOSCluster detenido con éxito.")
        print("OK")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error stopping the KEOSCluster controller: {e}")
        #logger.error("Error al detener el controlador de KEOSCluster.")
        raise e

def disable_keoscluster_webhooks():
    """Elimina los webhooks de validación y mutación de KEOSCluster"""
    try:
        backup_keoscluster_webhooks()
        print("[INFO] Disabling KEOSCluster webhooks:", end =" ", flush=True)
        #logger.info("Eliminando los webhooks de validación y mutación de KEOSCluster...")
        run_command(f"{kubectl} delete validatingwebhookconfiguration keoscluster-validating-webhook-configuration", allow_errors=True)
        run_command(f"{kubectl} delete mutatingwebhookconfiguration keoscluster-mutating-webhook-configuration", allow_errors=True)
        #logger.info("Webhooks eliminados exitosamente.")
        print("OK")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error disabling KEOSCluster webhooks: {e}")
        #logger.error("Error al eliminar los webhooks.")
        raise e

def backup_keoscluster_webhooks():
    """Realiza el backup de los webhooks de validación y mutación"""
    try:
        #Create cluster-operator directory in backup directory
        if not os.path.exists(backup_dir+'/cluster-operator'):
            os.makedirs(backup_dir+'/cluster-operator')
        print("[INFO] Backing up KEOSCluster webhooks...")
        #logger.info("Haciendo backup de los webhooks...")
        print("[INFO] Backup of validation webhooks:", end =" ", flush=True)
        # Backup de los webhooks de validación
        validating_webhook = run_command(f"{kubectl} get validatingwebhookconfiguration keoscluster-validating-webhook-configuration -o json --ignore-not-found")
        if isinstance(validating_webhook, tuple):
            validating_webhook = validating_webhook[0]
        else:
            print("SKIP")
        if validating_webhook != "":
            validating_webhook_json = json.loads(validating_webhook)  # Convierte el string JSON a un objeto Python
            
            with open(backup_dir+'/cluster-operator/keoscluster-validating-webhook-configuration-backup.json', 'w') as f:
                json.dump(validating_webhook_json, f, indent=4)
        else:
            print("SKIP")
        print("OK")
        
        print("[INFO] Backup of mutation webhooks:", end =" ", flush=True)
        # Backup de los webhooks de mutación
        mutating_webhook = run_command("kubectl get mutatingwebhookconfiguration keoscluster-mutating-webhook-configuration -o json --ignore-not-found")
        if isinstance(mutating_webhook, tuple):
            mutating_webhook = mutating_webhook[0]
        else:
            print("SKIP")
        if mutating_webhook != "":
            mutating_webhook_json = json.loads(mutating_webhook)  # Convierte el string JSON a un objeto Python
            with open(backup_dir+'/cluster-operator/keoscluster-mutating-webhook-configuration-backup.json', 'w') as f:
                json.dump(mutating_webhook_json, f, indent=4)
        else:
            print("SKIP")
        
        print("OK")

        #logger.info("Backup de los webhooks realizado con éxito.")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error backing up KEOSCluster webhooks: {e}")
        #logger.error(f"Error al hacer el backup de los webhooks: {e}")
        raise e

def update_clusterconfig(cluster_config, charts, provider):
    try:
        print("[INFO] Updating clusterconfig:", end =" ", flush=True)
        #logger.info("Actualizando el clusterconfig...")
        #logger.info(f"Charts actualizados: {charts}")
        clusterconfig_name = cluster_config["metadata"]["name"]
        clusterconfig_namespace = cluster_config["metadata"]["namespace"]
        
        cluster_config["spec"]["cluster_operator_version"] = "0.4.0"
        cluster_config["spec"]["capx"] = {}
        cluster_config["spec"]["capx"]["capi_version"] = "v1.7.4"
        if provider == "aws":
            cluster_config["spec"]["capx"]["capa_image_version"] = "v2.5.2"
            cluster_config["spec"]["capx"]["capa_version"] = "v2.5.2"
        if provider == "azure":
            cluster_config["spec"]["capx"]["capz_image_version"] = "v1.12.4"
            cluster_config["spec"]["capx"]["capz_version"] = "v1.12.4"
        cluster_config["spec"]["private_helm_repo"] = False
        cluster_config["spec"]["charts"] = []
        for chart_name, chart_version in charts.items():
            cluster_config["spec"]["charts"].append({"name": chart_name, "version": chart_version})
        clusterconfig_json = json.dumps(cluster_config)
        command = f"{kubectl} patch clusterconfig {clusterconfig_name} -n {clusterconfig_namespace} --type merge -p '{clusterconfig_json}'"
        output, err = run_command(command)
        #logger.info(f"Command: {command}, output: {output}")
        #logger.info("Clusterconfig actualizado con éxito.")
        print("OK")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error updating the clusterconfig: {e}")
        #logger.error("Error al actualizar el clusterconfig.")
        raise e
    
def update_keoscluster(keos_cluster, provider):
    try:
        print("[INFO] Updating keoscluster:", end =" ", flush=True)
        #logger.info("Actualizando el keoscluster...")
        keoscluster_name = keos_cluster["metadata"]["name"]
        keoscluster_namespace = keos_cluster["metadata"]["namespace"]
        managed_cluster = keos_cluster["spec"]["control_plane"]["managed"]
        del keos_cluster["metadata"]["resourceVersion"] 
        del keos_cluster["metadata"]["uid"] 
        del keos_cluster["metadata"]["annotations"]["cluster-operator.stratio.com/last-configuration"]
        keos_cluster["spec"]["helm_repository"]["release_interval"] = "1m"
        keos_cluster["spec"]["helm_repository"]["release_retries"] = 3
        keos_cluster["spec"]["helm_repository"]["release_source_interval"] = "1m"
        keos_cluster["spec"]["helm_repository"]["repository_interval"] = "10m"
        if not managed_cluster:
            if provider == "azure":
                keos_cluster["spec"]["control_plane"]["cri_volume"] = {"enabled": True, "size": 128, "type": "Standard_LRS"}
                keos_cluster["spec"]["control_plane"]["etcd_volume"] = {"enabled": True, "size": 8, "type": "Standard_LRS"}
                if not keos_cluster["spec"]["control_plane"].get("root_volume"):
                    keos_cluster["spec"]["control_plane"]["root_volume"] = {"size": 128, "type": "Standard_LRS"}
        for wn in keos_cluster['spec']['worker_nodes']:
            type_volume = ""
            if provider == "aws":
                type_volume = "gp3"
            elif provider == "azure":
                type_volume = "Standard_LRS"
            wn["cri_volume"] = {"enabled": True, "size": 128, "type": type_volume}
            if not wn.get("root_volume"):
                wn["root_volume"] = {"size": 128, "type": "gp3"}
        
        # Convertir keoscluster a JSON
        keoscluster_json = json.dumps(keos_cluster)
        
        # Actualizar el comando kubectl patch
        command = f"kubectl patch keoscluster {keoscluster_name} -n {keoscluster_namespace} --type merge -p '{keoscluster_json}'"
        output, err = run_command(command)
        if "no change" in output.lower() and "cri_volume" in command:
            #logger.info("No se realizaron cambios en el keoscluster. Reintentando...")
            output, err = run_command(command)
        #logger.info(f"Command: {command}, output: {output}")
        #logger.info("Keoscluster actualizado con éxito.")
        print("OK") 
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error updating the keoscluster: {e}")
        #logger.error("Error al actualizar el keoscluster.")
        raise e   
    
def restore_keoscluster_webhooks():
    """Restaura los webhooks desde el backup"""
    try:
        print("[INFO] Restoring KEOSCluster webhooks from backup...")
        #logger.info("Restaurando los webhooks de KEOSCluster desde el backup...")
        print("[INFO] Restoring validation webhooks:", end =" ", flush=True)
        # Restaurar el webhook de validación
        with open(backup_dir+'/cluster-operator/keoscluster-validating-webhook-configuration-backup.json', 'r') as f:
            validating_webhook = json.load(f)
            with open(backup_dir+'/cluster-operator/keoscluster-validating-webhook-configuration.yaml', 'w') as backup_file:
                yaml.dump(validating_webhook, backup_file)
            run_command(f"{kubectl} create -f {backup_dir}/cluster-operator/keoscluster-validating-webhook-configuration.yaml", allow_errors=True)
            print("OK")
        
        print("[INFO] Restoring mutation webhooks:", end =" ", flush=True)
        # Restaurar el webhook de mutación
        with open(backup_dir+'/cluster-operator/keoscluster-mutating-webhook-configuration-backup.json', 'r') as f:
            mutating_webhook = json.load(f)
            with open(backup_dir+'/cluster-operator/keoscluster-mutating-webhook-configuration.yaml', 'w') as backup_file:
                yaml.dump(mutating_webhook, backup_file)
            run_command(f"{kubectl} create -f {backup_dir}/cluster-operator/keoscluster-mutating-webhook-configuration.yaml", allow_errors=True)
            print("OK")
        #logger.info("Webhooks restaurados exitosamente.")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error restoring KEOSCluster webhooks from backup: {e}")
        #logger.error("Error al restaurar los webhooks desde el backup.")
        raise e

def start_keoscluster_controller():
    try:
        print("[INFO] Starting keoscluster-controller-manager deployment:", end =" ", flush=True)
        #logger.info("Iniciando el controlador de KEOSCluster...")
        run_command(f"{kubectl} scale deployment -n kube-system keoscluster-controller-manager --replicas=2")
        run_command(f"{kubectl} wait --for=condition=Available deployment/keoscluster-controller-manager -n kube-system --timeout=300s")
        print("OK")
        #logger.info("Controlador de KEOSCluster iniciado con éxito.")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error starting the KEOSCluster controller: {e}")
        #logger.error("Error al iniciar el controlador de KEOSCluster.")
        raise e

def update_default_volumes(keos_cluster):
    try:
        
        last_kc = keos_cluster["metadata"]["annotations"]["cluster-operator.stratio.com/last-configuration"]
        keoscluster_name = keos_cluster["metadata"]["name"]
        keoscluster_namespace = keos_cluster["metadata"]["namespace"]
        if '"cri_volume":{"enabled":false}' in last_kc:
            print("SKIP")
            #logger.info("Los volúmenes CRI y etcd ya están deshabilitados.")
            return
        disabled_cri_vol = disable_cri_etcd_volume(last_kc)
        keos_cluster["metadata"]["annotations"]["cluster-operator.stratio.com/last-configuration"] = disabled_cri_vol
        # Convertir keoscluster a JSON
        keoscluster_json = json.dumps(keos_cluster)
        #logger.info(f"Actualizando el keoscluster {keoscluster_name} en el namespace {keoscluster_namespace}...")
        #logger.info(f"Nuevo keoscluster: {keoscluster_json}")
        
        # Actualizar el comando kubectl patch
        command = f"kubectl patch keoscluster {keoscluster_name} -n {keoscluster_namespace} --type merge -p '{keoscluster_json}'"
        output,err = run_command(command, allow_errors=True)
        if "Operation cannot be fulfilled" in err:
            #logger.error("Error al actualizar el keoscluster.")
            keos_cluster, clusterconfig = get_keos_cluster_cluster_config()
            update_default_volumes(keos_cluster)
        command = (
            kubectl + " wait --for=jsonpath=\"{.status.ready}\"=false KeosCluster "
            + cluster_name + " -n cluster-" + cluster_name + " --timeout 5m"
        )
        execute_command(command, False)
        #logger.info(f"Output: {output}")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error updating the keoscluster: {e}")
        #logger.error("Error al actualizar el keoscluster.")
        raise e

def create_and_apply_azure_secret(name, namespace, tenantId, subscriptionId, cluster_name, location, userAssignIdentity):
    try:
        print(f"[INFO] Creating the Azure CSI disk secret '{name}' in the namespace '{namespace}':", end=" ", flush=True)

        # Crear la estructura de configuración para el cloud-config
        cloud_config = {
            "cloud": "AzurePublicCloud",
            "tenantId": tenantId,
            "subscriptionId": subscriptionId,
            "resourceGroup": cluster_name,
            "securityGroupName": f"{cluster_name}-node-nsg",
            "securityGroupResourceGroup": cluster_name,
            "location": location,
            "vmType": "standard",
            "vnetResourceGroup": cluster_name,
            "vnetName": f"{cluster_name}-vnet",
            "subnetName": "node-subnet",
            "routeTableName": f"{cluster_name}-node-routetable",
            "loadBalancerSku": "Standard",
            "loadBalancerName": "",
            "maximumLoadBalancerRuleCount": 250,
            "useManagedIdentityExtension": True,
            "useInstanceMetadata": True,
            "userAssignedIdentityID": userAssignIdentity
        }

        # Serializar el diccionario a JSON
        cloud_config_json = json.dumps(cloud_config)

        # Codificar el JSON a Base64
        cloud_config_base64 = base64.b64encode(cloud_config_json.encode('utf-8')).decode('utf-8')

        # Crear la estructura del secreto en YAML
        secret_yaml = f"""
apiVersion: v1
kind: Secret
metadata:
  name: {name}
  namespace: {namespace}
type: Opaque
data:
  cloud-config: {cloud_config_base64}
"""

        # Crear el archivo temporal YAML para el secreto
        secret_file = "/tmp/azure_secret.yaml"
        with open(secret_file, "w") as f:
            f.write(secret_yaml)

        # Aplicar el secreto usando kubectl
        kubectl = "kubectl"  # Asegúrate de tener kubectl configurado correctamente
        command = f"{kubectl} apply -f {secret_file}"
        run_command(command)

        print("OK")

    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error creating and applying the Azure secret '{name}: {e}")
        
def patch_webhook_timeout(webhook_name, webhook_path, new_timeout):
    """
    Updates the timeoutSeconds field of a webhook in a ValidatingWebhookConfiguration.

    Args:
        webhook_name (str): Name of the ValidatingWebhookConfiguration.
        webhook_path (str): Name of the webhook within the resource.
        new_timeout (int): New value for timeoutSeconds.

    Returns:
        None
    """
    try:
        print(f"[INFO] Updating the timeoutSeconds of the webhook '{webhook_path}' in {webhook_name}:", end=" ", flush=True)
        # 1. Obtener la configuración actual
        webhook_config_json = run_command(f"kubectl get validatingwebhookconfiguration {webhook_name} -o json")
        if isinstance(webhook_config_json, tuple):
            webhook_config_json = webhook_config_json[0]
        webhook_config = json.loads(webhook_config_json)

        # 2. Verificar que existe el webhook especificado
        for webhook in webhook_config["webhooks"]:
            if webhook["name"] == webhook_path:
                webhook["timeoutSeconds"] = new_timeout
                break
        else:
            raise Exception(f"[ERROR] Can not find webhook '{webhook_path}' in {webhook_name}.")

        # 3. Guardar el nuevo contenido en un archivo temporal
        updated_config_file = "/tmp/updated_webhook_config.json"
        with open(updated_config_file, "w") as f:
            json.dump(webhook_config, f, indent=2)

        # 4. Aplicar el parche
        run_command(f"kubectl apply -f {updated_config_file}")
        print("OK")

    except Exception as e:
        print("FAILED")
        print(f"[ERROR] {e}")

def update_configmap(namespace, configmap_name, key_to_update, yaml_key_to_remove):
    """
    Recupera un ConfigMap, actualiza el valor YAML eliminando una key específica y guarda el cambio.

    Args:
        namespace (str): Namespace del ConfigMap.
        configmap_name (str): Nombre del ConfigMap.
        key_to_update (str): Key del ConfigMap donde está el contenido YAML.
        yaml_key_to_remove (str): Key a eliminar dentro del YAML del valor.

    Returns:
        None
    """
    try:
        print(f"[INFO] Updating the ConfigMap '{configmap_name}'. Removing {yaml_key_to_remove} from default values:", end=" ", flush=True)
        ryaml = YAML()
        ryaml.preserve_quotes = True  # Mantener las quotes y el formato original
        ryaml.default_flow_style = False

        # 1. Obtener el ConfigMap
        command_get_cm = f"kubectl get configmap {configmap_name} -n {namespace} -o yaml"
        configmap_yaml = run_command(command_get_cm)
        # print(f"configmap_yaml: {configmap_yaml}")
        if configmap_yaml is None:
            raise Exception(f"ConfigMap '{configmap_name}' does not exist in namespace '{namespace}'.")

        # print(f"[DEBUG] Type of configmap_yaml: {type(configmap_yaml)}")
        # print(f"[DEBUG] Content of configmap_yaml: {configmap_yaml}")
        if isinstance(configmap_yaml, tuple):
            configmap_yaml = configmap_yaml[0]
            
        configmap_dict = ryaml.load(configmap_yaml)
        # print(f"configmap_dict: {configmap_dict}")

        # 2. Validar la existencia de la key a actualizar
        if "data" not in configmap_dict or key_to_update not in configmap_dict["data"]:
            raise Exception(f"The key '{key_to_update}' does not exist in the ConfigMap '{configmap_name}'.")

        
        # 3. Parsear el valor YAML existente
        data_yaml_content = configmap_dict["data"][key_to_update]
        data_dict = ryaml.load(data_yaml_content)

        # print(f"data_dict: {data_dict}")
        # 4. Eliminar la key en el YAML si existe
        if yaml_key_to_remove in data_dict:
            del data_dict[yaml_key_to_remove]
        else:
            print("SKIP")
            return

        # 5. Convertir de nuevo el YAML con formato en bloque '|-' (sin salto al final)
        stream = StringIO()
        ryaml.dump(data_dict, stream)
        # print(f"stream.getvalue(): {stream.getvalue()}")
        formatted_yaml = stream.getvalue().rstrip('\n')  # Eliminar salto final para usar el formato '|-'

        # print(f"formatted_yaml: {formatted_yaml}")
        # 6. Actualizar el ConfigMap
        # Reemplazar la cadena de saltos de línea adecuadamente en la cadena YAML
        updated_yaml_escaped = formatted_yaml.replace('\n', '\\n').replace('"', '\\"')

        # print(f"updated_yaml_escaped: {updated_yaml_escaped}")
        # El comando debe ser generado con la escapatoria correcta
        command_patch_cm = (
            f"kubectl patch configmap {configmap_name} -n {namespace} "
            f"--type merge -p '{{\"data\": {{\"{key_to_update}\": \"{updated_yaml_escaped}\"}}}}'"
        )
        run_command(command_patch_cm)

        print("OK")

    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error updating the ConfigMap '{configmap_name}': {e}")

def disable_cri_etcd_volume(last_kc):
    # Compilar la expresión regular
    regex_cri = re.compile(r'"cri_volume":\{[^}]*\}')
    regex_etcd = re.compile(r'"etcd_volume":\{[^}]*\}')
    
    # Reemplazar las coincidencias por "cri_volume":{"enabled":false}
    result = regex_cri.sub('"cri_volume":{"enabled":false}', last_kc)
    result = regex_etcd.sub('"etcd_volume":{"enabled":false}', result)
    
    return result


if __name__ == '__main__':
    
   
    # Init variables
    start_time = time.time()
    backup_dir = "./backup/upgrade/"
    binaries = ["clusterctl", "kubectl", "helm", "jq"]
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

    # Get secrets
    try:
        vault = Vault(config["vault_password"])
        data = vault.load(open(config["secrets"]).read())
    except Exception as e:
        print("[ERROR] Decoding secrets file failed:\n" + str(e))
        sys.exit(1)

    # Set env vars
    env_vars = "CLUSTER_TOPOLOGY=true CLUSTERCTL_DISABLE_VERSIONCHECK=true GOPROXY=off"
    helm_registry_oci = get_helm_registry_oci(keos_cluster)
    docker_registry = get_docker_registry(keos_cluster)
    helm_registry = input(f"The current helm repository is: {helm_registry_oci}. Do you want to indicate a new helm repository? Press enter or specify new repository: ")
    if helm_registry != "" and helm_registry != helm_registry_oci:
        update_helm_registry(cluster_name, helm_registry, config["dry_run"]) 
    new_docker_registry = input(f"The current docker registry is: {docker_registry}. Do you want to indicate a new docker registry? Press enter or specify new docker registry: ")
    if new_docker_registry != "" and new_docker_registry != docker_registry:
        update_docker_registry(cluster_name, new_docker_registry, config["dry_run"])
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
        if config['user_assign_identity'] == "":
            print("[ERROR] The flag --user-assign-identity must be indicated with azure provider")
            sys.exit(1)
        userAssignIdentity = config['user_assign_identity']
        print(f"[INFO] User assigned identity: {userAssignIdentity}")
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
    if "auth_required" in keos_cluster["spec"]["helm_repository"]:
        if keos_cluster["spec"]["helm_repository"]["auth_required"]:
            if "user" in data["secrets"]["helm_repository"] and "pass" in data["secrets"]["helm_repository"]:
                helm_repo["user"] = data["secrets"]["helm_repository"]["user"]
                helm_repo["pass"] = data["secrets"]["helm_repository"]["pass"]
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
            account_id = data["secrets"]["aws"]["credentials"]["account_id"]
            install_lb_controller(cluster_name, account_id, config["dry_run"])
        else:
            print("[WARN] AWS LoadBalancer Controller is only supported for EKS managed clusters")
            sys.exit(0)

    # Cluster Operator
    if provider == "azure":
        patch_kubeadm_config_templates("cluster-" + cluster_name)
        update_configmap("kube-system", "00-metrics-server-helm-chart-default-values", "values.yaml", "affinity")
        update_configmap("kube-system", "00-metrics-server-helm-chart-default-values", "values.yaml", "tolerations")
        create_and_apply_azure_secret("azure-cloud-provider", "kube-system", credentials["tenant_id"], credentials["subscription_id"], cluster_name, keos_cluster["spec"]["region"], userAssignIdentity)
    if provider == "aws":
        update_allow_global_netpol(provider)
    if not check_flux_installed():
        install_flux(provider)
    upgrade_capx(managed, provider, namespace, version, env_vars)
    
    if provider == "aws":
        chart_versions = eks_chart_versions
    elif provider == "azure":
        chart_versions = azure_vm_chart_versions
    adopt_all_helm_charts(keos_cluster, credentials, chart_versions)
    install_cert_manager(provider)
    charts = update_chart_versions(keos_cluster, cluster_config, chart_versions, credentials)
    
    #update_chart_versions(keos_cluster, cluster_config, chart_versions, credentials)

    # Restore capsule
    if not config["disable_prepare_capsule"]:
        restore_capsule(config["dry_run"])
    
    networks = keos_cluster["spec"].get("networks", {})
    # Update kubernetes version to 1.27.X
    current_k8s_version = get_kubernetes_version()
    delete_stratio_genai_pdb()
    
    if "1.28" in current_k8s_version:
        
        tigera_version = chart_versions["28"]["tigera-operator"]["chart_version"] 
        print(f"[INFO] Restarting Tigera Operator: ", end =" ", flush=True)
        restart_tigera_operator_manifest(provider,tigera_version=tigera_version)
        #logger.info("Esperando a que helmrelease cluster-operator esté listo...")
        print("[INFO] Waiting for the cluster-operator helmrelease to be ready...")
        command = f"{kubectl} wait helmrelease cluster-operator -n kube-system --for=jsonpath='{{.status.conditions[?(@.type==\"Ready\")].status}}'=True --timeout=5m"
        run_command(command)
        print("[INFO] Upgrading Cluster Operator components...")
        print("[INFO] Stoping cluster-operator helmrelease:", end =" ", flush=True)

        #logger.info("Deteniendo helmrelease cluster-operator...")
        command = kubectl + " patch helmrelease cluster-operator -n kube-system --type merge --patch '{\"spec\":{\"suspend\":true}}'"
        run_command(command)
        # command = (
        #     kubectl+" wait --for=jsonpath=\"{.status.ready}\"=true KeosCluster "
        #     + cluster_name + " -n cluster-" + cluster_name + " --timeout 5m"
        # )
        # execute_command(command, False)
        print("OK")
        
        
        stop_keoscluster_controller()
        patch_webhook_timeout("keoscluster-validating-webhook-configuration", "vkeoscluster.kb.io", 30)
        disable_keoscluster_webhooks()
        update_clusterconfig(cluster_config, charts, provider)
        keos_cluster, cluster_config = get_keos_cluster_cluster_config()
        provider = keos_cluster["spec"]["infra_provider"]
        update_keoscluster(keos_cluster, provider)
        restore_keoscluster_webhooks()
        start_keoscluster_controller()
        print("[INFO] Waiting for the cluster-operator helmrelease to be ready:", end =" ", flush=True)
        #logger.info("Iniciando helmrelease cluster-operator...")
        command = kubectl + " patch helmrelease cluster-operator -n kube-system --type merge --patch '{\"spec\":{\"suspend\":false}}'"
        run_command(command)
        #logger.info("Esperando a que helmrelease cluster-operator esté listo...")
        command = kubectl + " wait helmrelease cluster-operator -n kube-system --for=condition=Ready --timeout=5m"
        run_command(command)
        
        cluster_name = keos_cluster["metadata"]["name"]
        
        
        #run_command(command)
        command = (
            kubectl + " wait --for=jsonpath=\"{.status.ready}\"=true KeosCluster "
            + cluster_name + " -n cluster-" + cluster_name + " --timeout 5m"
        )
        execute_command(command, False)
        
        keos_cluster, cluster_config = get_keos_cluster_cluster_config()
        #logger.info(f"KeosCluster: {json.dumps(keos_cluster)}")
        
        
        keos_cluster, cluster_config = get_keos_cluster_cluster_config()
        #logger.info(f"KeosCluster: {keos_cluster}")
        command = "kubectl wait deployment -n kube-system keoscluster-controller-manager --for=condition=Available --timeout=5m"
        run_command(command)
        required_k8s_version=validate_k8s_version("first", False)
        #required_k8s_version="1.29.7"
        upgrade_k8s(cluster_name, keos_cluster["spec"]["control_plane"], keos_cluster["spec"]["worker_nodes"], networks, required_k8s_version, provider, managed, backup_dir, False)
    keos_cluster, cluster_config = get_keos_cluster_cluster_config()
    charts = update_chart_versions(keos_cluster, cluster_config, chart_versions, credentials)
    current_k8s_version = get_kubernetes_version()
    
    if "1.29" in current_k8s_version:
        required_k8s_version=validate_k8s_version("second", False)
        #required_k8s_version="1.30.0"
        print("[INFO] Waiting for the cluster-operator helmrelease to be ready:", end =" ", flush=True)
        command = f"{kubectl} wait --for=condition=Available deployment/keoscluster-controller-manager -n kube-system --timeout=300s"
        run_command(command)
        command = f"{kubectl} wait helmrelease cluster-operator -n kube-system --for=condition=Ready --timeout=5m"
        run_command(command)
        print("OK")
        
        keos_cluster, cluster_config = get_keos_cluster_cluster_config()
        upgrade_k8s(cluster_name, keos_cluster["spec"]["control_plane"], keos_cluster["spec"]["worker_nodes"], networks, required_k8s_version, provider, managed, backup_dir, False)
        
    if provider == "azure":
        patch_kubeadm_controlplane("cluster-" + cluster_name)
    keos_cluster, cluster_config = get_keos_cluster_cluster_config()
    charts = update_chart_versions(keos_cluster, cluster_config, chart_versions, credentials)
    
    
    if not managed:
        cp_global_network_policy("patch", networks, provider, backup_dir, False)
        
    print("[INFO] Updating default volumes:", end =" ", flush=True)
    keos_cluster, cluster_config = get_keos_cluster_cluster_config()
    update_default_volumes(keos_cluster)
    time.sleep(30)
        
    print("[INFO] Waiting for the CRI Volumes updating in Controlplane:", end =" ", flush=True)
    command = (
        f"{kubectl} wait --for=jsonpath=\"{{.status.phase}}\"=\"Updating worker nodes\""
        f" KeosCluster {cluster_name} --namespace=cluster-{cluster_name} --timeout=25m"
    )
    execute_command(command, False)
    
    print("[INFO] Waiting for the CRI Volumes updating in WorkerNodes:", end =" ", flush=True)
    if provider == "azure":
        command = f'kubectl get azuremachines -o json -n cluster-{cluster_name} | jq \'.items[] | select((.spec.dataDisks == null) or (.spec.dataDisks | all(.nameSuffix != "cri_disk")) or .status.ready != true) | .metadata.name\''
    if provider == "aws":
        command = f'kubectl get awsmachines -o json -n cluster-{cluster_name} | jq \'.items[] | select((.spec.nonRootVolumes == null) or (.spec.nonRootVolumes | all(.deviceName != "/dev/xvdc")) or .status.ready != true) | .metadata.name\''

    i = 1
    while i !=0:
        output = execute_command(command, False, False)
        i = len(output.splitlines())
        time.sleep(30)
    print("OK")
    if not managed:
        cp_global_network_policy("restore", networks, provider, backup_dir, False)
   
    #     "kubectl wait --for=jsonpath=\"{.status.ready}\"=true KeosCluster "
    #     + cluster_name + " -n cluster-" + cluster_name + " --timeout 60m"
    # )
    # execute_command(command, False)
    
    end_time = time.time()
    elapsed_time = end_time - start_time
    minutes, seconds = divmod(elapsed_time, 60)
    print("[INFO] Upgrade process finished successfully in " + str(int(minutes)) + " minutes and " + "{:.2f}".format(seconds) + " seconds")
