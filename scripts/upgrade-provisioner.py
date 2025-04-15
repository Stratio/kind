#!/usr/bin/env python3
# -*- coding: utf-8 -*-

##############################################################
# Author: Stratio Clouds <clouds-integration@stratio.com>    #
# Supported provisioner versions: 0.6.X                      #
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
from urllib.parse import urlparse

CLOUD_PROVISIONER = "0.17.0-0.6"
CLUSTER_OPERATOR = "0.5.0-a315600" 
CLUSTER_OPERATOR_UPGRADE_SUPPORT = "0.4.X"
CLOUD_PROVISIONER_LAST_PREVIOUS_RELEASE = "0.17.0-0.6"

AWS_LOAD_BALANCER_CONTROLLER_CHART = "1.8.1"

CLUSTERCTL = "v1.7.2"

CAPI = "v1.7.4"
CAPA = "v2.5.2"
CAPG = "1.6.1-0.2.0"
CAPZ = "v1.12.4"

TIGERA_OPERATOR_CALICOCTL_VERSION = "3.29.1"
TIGERA_OPERATOR_CONTROLLER_VERSION = "v1.36.2"

common_charts = {
    "cert-manager": {
        "version": "v1.17.0",
        "namespace": "cert-manager",
        "repo": "https://charts.jetstack.io"
    },
    "cluster-autoscaler": {
        "version": "9.46.0",
        "namespace": "kube-system",
        "repo": "https://kubernetes.github.io/autoscaler"
    },
    "cluster-operator": {
        "version": "0.5.0",
        "namespace": "kube-system",
        "repo": ""
    },
    "flux2": {
        "version": "2.14.1",
        "namespace": "kube-system",
        "repo": "https://fluxcd-community.github.io/helm-charts"
    },
    "tigera-operator": {
        "version": "v3.29.1",
        "namespace": "tigera-operator",
        "repo": "https://docs.projectcalico.org/charts"
    }
}

aws_eks_charts = {
    "aws-load-balancer-controller": {
        "version": "1.11.0",
        "namespace": "kube-system",
        "repo": "https://aws.github.io/eks-charts"
    }
}

azure_vm_charts = {
    "azuredisk-csi-driver": {
        "version": "v1.31.2",
        "namespace": "kube-system",
        "repo": "https://raw.githubusercontent.com/kubernetes-sigs/azuredisk-csi-driver/master/charts"
    },
    "azurefile-csi-driver": {
        "version": "v1.31.2",
        "namespace": "kube-system",
        "repo": "https://raw.githubusercontent.com/kubernetes-sigs/azurefile-csi-driver/master/charts"
    },
    "cloud-provider-azure": {
        "version": "v1.32.0",
        "namespace": "kube-system",
        "repo": "https://raw.githubusercontent.com/kubernetes-sigs/cloud-provider-azure/master/helm/repo"
    }
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
    parser.add_argument("--disable-backup", action="store_true", help="Disable backing up files before upgrading (enabled by default)")
    parser.add_argument("--disable-prepare-capsule", action="store_true", help="Disable preparing capsule for the upgrade process (enabled by default)")
    parser.add_argument("--dry-run", action="store_true", help="Do not upgrade components. This invalidates all other options")
    parser.add_argument("--skip-k8s-intermediate-version", action="store_true", help="Skip workers intermediate kubernetes version upgrade")
    parser.add_argument("--private", action="store_true", help="Treats the Docker registry and the Helm repository as private")
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

def patch_clusterrole_aws_node(dry_run):
    '''Patch aws-node ClusterRole'''

    aws_node_clusterrole_name = "aws-node"
    print("[INFO] Modifying aws-node ClusterRole:", end =" ", flush=True)
    if not dry_run:
        command = f"{kubectl} get clusterrole -o json {aws_node_clusterrole_name} | jq -r '.rules'"
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
                patch_command = f"{kubectl} patch clusterrole {aws_node_clusterrole_name} --type=json -p='{json.dumps(patch)}'"
                execute_command(patch_command, False, True)
            else:
                print("SKIP")
        else:
            print(f"[ERROR] Pods resource not found in the ClusterRole {aws_node_clusterrole_name}")
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

def validate_helm_repository(helm_repository):
    '''Validate the Helm repository'''

    try:
        url = urlparse(helm_repository)
        if not all([url.scheme, url.netloc]):
            raise ValueError(f"The Helm repository '{helm_repository}' is invalid.")
    except ValueError:
        raise ValueError(f"The Helm repository '{helm_repository}' is invalid.")


def update_helm_repository(cluster_name, helm_repository, dry_run):
    '''Update the Helm repository'''
    
    wait_for_keos_cluster(cluster_name, "10")

    
    patch_helm_repository = [
        {"op": "replace", "path": "/spec/helm_repository/url", "value": helm_repository},
    ]

    patch_json = json.dumps(patch_helm_repository)
    command = f"{kubectl} -n cluster-{cluster_name} patch KeosCluster {cluster_name} --type='json' -p='{patch_json}'"
    execute_command(command, False, False)
    
    patch_helmRepository = [
        {"op": "replace", "path": "/spec/url", "value": helm_repository},
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

def get_helm_repository(keos_cluster):
    '''Get the Helm registry URL'''
    
    try:
        helm_repository = keos_cluster["spec"]["helm_repository"]["url"]
        
        if helm_repository:
            return helm_repository
        else:
            return None
    except KeyError as e:
        return None

def get_deploy_version(deploy, namespace, container):
    '''Get the version of a deployment'''
    
    command = f"{kubectl} -n " + namespace + " get deploy " + deploy + " -o json  | jq -r '.spec.template.spec.containers[].image' | grep '" + container + "' | cut -d: -f2"
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
            command = f"{kubectl} get {kind} {name} "
            if ns:
                command = command + f" -n {ns}"
            output, err = run_command(command, allow_errors=True)
            if "not found" in err.lower():
                
                continue
        except Exception as e:
            print("FAILED")
            print(f"[ERROR] Error checking the existence of {kind} {name}: {e}")
            return
        
        command = f"{kubectl} {action_type} {kind} {name} {annotation_label_key}={annotation_label_value} --overwrite "
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


def get_pods_cidr(keos_cluster):
    '''Get the pods CIDR'''
    
    try:
        return keos_cluster["spec"]["networks"]["pods_cidr"]
    except KeyError:
        return ""


def render_values_template(values_file, keos_cluster, cluster_config):
    '''Render the values template'''
    
    try:
        values_params = {
            "private": cluster_config["spec"]["private_registry"] or private_registry,
            "cluster_name": keos_cluster["metadata"]["name"],
            "registry": get_keos_registry_url(keos_cluster),
            "provider": keos_cluster["spec"]["infra_provider"],
            "managed_cluster": keos_cluster["spec"]["control_plane"]["managed"]
        }
        
        template = env.get_template(values_file)
        rendered_values = template.render(values_params)
        return rendered_values
    except Exception as e:
        raise e

def create_default_values(chart_name, namespace, values_file, provider):
    '''Create defaults values file'''

    charts_requiring_values_update_all = []
    charts_requiring_values_update_provider = []
    try:
        if chart_name in charts_requiring_values_update_all:
            values = render_values_template( f"values/{chart_name}_default_values.tmpl", keos_cluster, cluster_config)
        elif chart_name in charts_requiring_values_update_provider:
            values = render_values_template( f"values/{provider}/{chart_name}_default_values.tmpl", keos_cluster, cluster_config)
        else:
            values, err = run_command(f"{helm} get values {chart_name} -n {namespace} --output yaml")
        run_command(f"echo '{values}' > {values_file}")
    except Exception as e:
        raise

def update_cluster_operator_image_tag_value(values_file, cluster_operator_version):
    '''Update cluster-operator image tag value'''

    try:
        with open(values_file, 'r') as file:
            values = yaml.safe_load(file)

        values['app']['containers']['controllerManager']['image']['tag'] = cluster_operator_version

        with open(values_file, 'w') as file:
            yaml.safe_dump(values, file, default_flow_style=False)

    except Exception as e:
        print(f"An error occurred: {e}")

def update_tigera_operator_image_tag_value(values_file):
    '''Update cluster-operator image tag value'''

    try:
        with open(values_file, 'r') as file:
            values = yaml.safe_load(file)

        values['calicoctl']['tag'] = TIGERA_OPERATOR_CALICOCTL_VERSION
        values['tigeraOperator']['version'] = TIGERA_OPERATOR_CONTROLLER_VERSION

        with open(values_file, 'w') as file:
            yaml.safe_dump(values, file, default_flow_style=False)

    except Exception as e:
        print(f"An error occurred: {e}")

def create_empty_values_file(values_file):
    ''' Create an empty values file'''
    
    try:
        open(values_file, 'w').close()  
    except Exception as e:
        raise e

def create_configmap_from_values(configmap_name, namespace, values_file):
    '''Create a ConfigMap from values'''
    
    try:
        command = f"{kubectl} create configmap {configmap_name} -n {namespace} --from-file=values.yaml={values_file} --dry-run=client -o yaml | kubectl apply -f -"
        run_command(command)
    except Exception as e:
        raise e

def filter_installed_charts(charts):
    '''Remove not installed charts'''

    try:
        output, err = run_command(helm  + " list --all-namespaces --output json")
        charts_installed = json.loads(output)
        charts_installed_names = [chart["name"] for chart in charts_installed]

        charts_filtered = {chart_name: chart_data for chart_name, chart_data in charts.items() if chart_name in charts_installed_names}
        return charts_filtered
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error getting charts installed {e}.")
        raise e

def upgrade_chart(chart_name, chart_data):
    '''Update chart HelmRelease'''
    chart_repo = chart_data["repo"]
    chart_version = chart_data["version"]
    chart_namespace = chart_data["namespace"]
    
    release_name = chart_name
    if chart_name == "flux2":
        release_name = "flux"
    repo_name = release_name
    repo_schema = "default"
    repo_username = ""
    repo_password = ""
    repo_auth_required = False
    repo_url = chart_repo

    if chart_name in "cluster-operator" or private_helm_repo:
        repo_name = "keos"
        repo_url =  keos_cluster["spec"]["helm_repository"]["url"]
        if "auth_required" in keos_cluster["spec"]["helm_repository"]:
            if keos_cluster["spec"]["helm_repository"]["auth_required"]:
                if "user" in vault_secrets_data["secrets"]["helm_repository"] and "pass" in vault_secrets_data["secrets"]["helm_repository"]:
                    repo_auth_required= True
                    repo_username = vault_secrets_data["secrets"]["helm_repository"]["user"]
                    repo_password = vault_secrets_data["secrets"]["helm_repository"]["pass"]
                else:
                    print("[ERROR] Helm repository credentials not found in secrets file")
                    sys.exit(1)
        if urlparse(repo_url).scheme == "oci":
            repo_schema = "oci"

    default_values_file = f"/tmp/{release_name}_default_values.yaml"
    empty_values_file = f"/tmp/{release_name}_empty_values.yaml"
    
    create_default_values(release_name, chart_namespace, default_values_file, provider)
    if release_name == "cluster-operator":
        update_cluster_operator_image_tag_value(default_values_file, cluster_operator_version)
    elif release_name == "tigera-operator":
        update_tigera_operator_image_tag_value(default_values_file)

    create_empty_values_file(empty_values_file)
    
    create_configmap_from_values(f"00-{release_name}-helm-chart-default-values", chart_namespace, default_values_file)
    create_configmap_from_values(f"02-{release_name}-helm-chart-override-values", chart_namespace, empty_values_file)

    helm_repo_data = {
        'repository_name': repo_name,
        'namespace': chart_namespace,
        'interval': '10m',
        'repository_url': repo_url,
        'schema': repo_schema,
        'provider': provider,
        'auth_required': repo_auth_required,
        'username': repo_username,
        'password': repo_password
    }
    
    helm_release_data = {
        'ReleaseName': release_name,
        'ChartName': chart_name,
        'ChartNamespace': chart_namespace,
        'ChartVersion': chart_version,
        'ChartRepoRef': repo_name,
        'HelmReleaseSourceInterval': '1m',
        'HelmReleaseInterval': '1m',
        'HelmReleaseRetries': 3
    }

    try:
        helmrepository_yaml = helmrepository_template.render(helm_repo_data)
        helmrelease_yaml = helmrelease_template.render(helm_release_data)

        repository_file = f'/tmp/{release_name}_helmrepository.yaml'
        release_file = f'/tmp/{release_name}_helmrelease.yaml'

        with open(repository_file, 'w') as f:
            f.write(helmrepository_yaml)

        with open(release_file, 'w') as f:
            f.write(helmrelease_yaml)

        command = f"{kubectl} apply -f {repository_file} "
        run_command(command)

        # We need to use --server-side flag to avoid metadata.resourceVersion conflicts
        command = f"{kubectl} apply -f {release_file} -n {chart_namespace} --server-side"
        run_command(command)
        
        print("OK")
    except Exception as e:
        raise e

def upgrade_charts(charts):
    '''Update the charts'''
    
    try:
        print(f"[INFO] Updating charts versions:")
        for chart_name, chart_data in charts.items():
            chart_version = chart_data["version"]
            print(f"[INFO] Updating chart {chart_name} to version {chart_version}:", end =" ", flush=True)
            upgrade_chart(chart_name, chart_data)
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error updating chart: {e}")
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
    
    backup_file = backup_dir + "/cluster-operator/keoscluster-webhooks.yaml"
    try:
        if not os.path.exists(os.path.dirname(backup_file)):
            os.makedirs(os.path.dirname(backup_file))
        print("[INFO] Backing up KEOSCluster webhooks...")
        print("[INFO] Backup of validation webhooks:", end =" ", flush=True)
        command = f"{helm} get manifest -n kube-system cluster-operator"
        command += f" | yq 'select(.kind == \"ValidatingWebhookConfiguration\" or .kind == \"MutatingWebhookConfiguration\")'"
        command += f" > {backup_file}"
        execute_command(command, False)
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
        cluster_config["spec"]["cluster_operator_image_version"] = cluster_operator_version
        cluster_config["spec"]["private_helm_repo"] = private_helm_repo
        cluster_config["spec"]["charts"] = []
        for chart_name, chart_data in charts.items():
            cluster_config["spec"]["charts"].append({"name": chart_name, "version": chart_data["version"]})
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

    backup_file = backup_dir + "/cluster-operator/keoscluster-webhooks.yaml"
    resources_webhooks = [
        {"kind": "MutatingWebhookConfiguration", "name": "keoscluster-mutating-webhook-configuration", "namespace": "kube-system"},
        {"kind": "ValidatingWebhookConfiguration", "name": "keoscluster-validating-webhook-configuration", "namespace": "kube-system"},
    ]
    try:
        print("[INFO] Restoring KEOSCluster webhooks from backup...")
        run_command(f"{kubectl} create -f {backup_file}", allow_errors=True)

        print("[INFO] Labeling and annotating webhooks...", end =" ", flush=True)
        update_annotation_label("app.kubernetes.io/managed-by", "Helm", resources_webhooks, "label")
        update_annotation_label("meta.helm.sh/release-name", "cluster-operator", resources_webhooks)
        update_annotation_label("meta.helm.sh/release-namespace", "kube-system", resources_webhooks)
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
    skip_k8s_intermediate_version = config["skip_k8s_intermediate_version"]
    if provider != "aws" and skip_k8s_intermediate_version:
        print("[ERROR] -skip-k8s-intermediate-version flags are only supported for EKS")
        sys.exit(1)

    # Set env vars
    env_vars = "CLUSTER_TOPOLOGY=true CLUSTERCTL_DISABLE_VERSIONCHECK=true GOPROXY=off"
    helm_repository_current = get_helm_repository(keos_cluster)
    helm_repository = input(f"The current helm repository is: {helm_repository_current}. Do you want to indicate a new helm repository? Press enter or specify new repository: ")
    if helm_repository != "" and helm_repository != helm_repository_current:
        validate_helm_repository(helm_repository)
        update_helm_repository(cluster_name, helm_repository, config["dry_run"]) 

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

    # Backup
    if not config["disable_backup"]:
        now = datetime.now()
        backup_dir = backup_dir + now.strftime("%Y%m%d-%H%M%S")
        backup(backup_dir, namespace, cluster_name, config["dry_run"])

    # Prepare capsule
    if not config["disable_prepare_capsule"]:
        prepare_capsule(config["dry_run"])

    # Update the clusterconfig and keoscluster
    keos_cluster, cluster_config = get_keos_cluster_cluster_config()

    private_registry = config["private"]
    private_helm_repo = config["private"] 
    cluster_operator_version = config["cluster_operator"]
    
    charts_to_upgrade = common_charts
    if provider == "aws":
        # Since aws-load-balancer-controller is optional we need to check if is installed
        aws_eks_charts_installed = filter_installed_charts(aws_eks_charts)
        charts_to_upgrade.update(aws_eks_charts_installed)
    elif provider == "azure":
        charts_to_upgrade.update(azure_vm_charts)
    charts_to_upgrade["cluster-operator"]["chart_version"] = cluster_operator_version

    upgrade_charts(charts_to_upgrade)
    
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
        update_clusterconfig(cluster_config, charts_to_upgrade, provider, cluster_operator_version)
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
        
        current_k8s_version = get_kubernetes_version()
    
    if "1.31" in current_k8s_version or ("1.30" in current_k8s_version and skip_k8s_intermediate_version):
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

    if not managed:
        cp_global_network_policy("restore", networks, provider, backup_dir, False)
        
    scale_cluster_autoscaler(2, config["dry_run"])
   
    end_time = time.time()
    elapsed_time = end_time - start_time
    minutes, seconds = divmod(elapsed_time, 60)
    print("[INFO] Upgrade process finished successfully in " + str(int(minutes)) + " minutes and " + "{:.2f}".format(seconds) + " seconds")
