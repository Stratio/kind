#!/usr/bin/env python3
# -*- coding: utf-8 -*-

##############################################################
# Author: Stratio Clouds <clouds-integration@stratio.com>    #
# Supported provisioner versions: 0.7.X                      #
# Supported cloud providers:                                 #
#   - EKS                                                    #
#   - Azure VMs                                              #
#   - GKE                                                    #
##############################################################

__version__ = "0.8.0"

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

CLOUD_PROVISIONER = "0.17.0-0.8"
CLUSTER_OPERATOR = "0.6.0"
CLUSTER_OPERATOR_UPGRADE_SUPPORT = "0.5.X"
CLOUD_PROVISIONER_LAST_PREVIOUS_RELEASE = "0.17.0-0.7"

AWS_LOAD_BALANCER_CONTROLLER_CHART = "1.11.0"

CLUSTERCTL = "v1.10.8"

CAPI = "v1.10.8"
CAPI_KUBEADM_BOOTSTRAP = "v1.10.8"
CAPI_KUBEADM_CONTROL_PLANE = "v1.10.8"
CAPA = "v2.9.2"
CAPG = "1.6.1-0.4.0"
CAPZ = "v1.21.1"

TIGERA_OPERATOR_CALICOCTL_VERSION = "3.30.2"
TIGERA_OPERATOR_CONTROLLER_VERSION = "v1.38.5"

common_charts = {
    "cert-manager": {
        "version": "v1.19.1",
        "namespace": "cert-manager",
        "repo": "https://charts.jetstack.io"
    },
    "cluster-autoscaler": {
        "version": "9.52.1",
        "namespace": "kube-system",
        "repo": "https://kubernetes.github.io/autoscaler"
    },
    "cluster-operator": {
        "version": "0.6.0",
        "namespace": "kube-system",
        "repo": ""
    },
    "flux2": {
        "version": "2.17.2",
        "namespace": "kube-system",
        "repo": "https://fluxcd-community.github.io/helm-charts"
    },
    "tigera-operator": {
        "version": "v3.30.2",
        "namespace": "tigera-operator",
        "repo": "https://docs.projectcalico.org/charts"
    }
}

aws_eks_charts = {
    "aws-load-balancer-controller": {
        "version": "1.14.1",
        "namespace": "kube-system",
        "repo": "https://aws.github.io/eks-charts"
    }
}

azure_vm_charts = {
    "azuredisk-csi-driver": {
        "version": "1.33.5",
        "namespace": "kube-system",
        "repo": "https://raw.githubusercontent.com/kubernetes-sigs/azuredisk-csi-driver/master/charts"
    },
    "azurefile-csi-driver": {
        "version": "1.34.1",
        "namespace": "kube-system",
        "repo": "https://raw.githubusercontent.com/kubernetes-sigs/azurefile-csi-driver/master/charts"
    },
    "cloud-provider-azure": {
        "version": "1.34.2",
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

    command = kubectl + " get deploy cluster-autoscaler-clusterapi-cluster-autoscaler -n kube-system --ignore-not-found -o=jsonpath='{.spec.replicas}'"
    output = execute_command(command, False, False)

    if output.strip() == "":
        print("[INFO] Cluster autoscaler not deployed: SKIP")
        return
    
    current_replicas = int(output)

    if current_replicas == replicas:
        print("[INFO] Cluster autoscaler already at desired replicas: SKIP")
        return

    scaling_type = "Scaling down" if current_replicas > replicas else "Scaling up"
    print(f"[INFO] {scaling_type} cluster autoscaler replicas:", end=" ", flush=True)

    if dry_run:
        print("DRY-RUN")
        return

    # Scale
    command = kubectl + f" scale deploy cluster-autoscaler-clusterapi-cluster-autoscaler -n kube-system --replicas={replicas}"
    execute_command(command, False)

    # Wait until ready
    command = kubectl + " wait deployment cluster-autoscaler-clusterapi-cluster-autoscaler -n kube-system --for=condition=Available --timeout=5m"
    execute_command(command, False)

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

        # We need to use --server-side and --force-conflicts flags to avoid metadata.resourceVersion conflicts
        command = f"{kubectl} apply -f {release_file} -n {chart_namespace} --server-side --force-conflicts"
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

        # ------------------------------------------------------------------
        # Update cluster-operator
        # ------------------------------------------------------------------
        cluster_config["spec"]["cluster_operator_version"] = cluster_operator_version
        cluster_config["spec"]["cluster_operator_image_version"] = cluster_operator_version
        cluster_config["spec"]["private_helm_repo"] = private_helm_repo

        # ------------------------------------------------------------------
        # Update CAPX (Cluster API providers)
        # ------------------------------------------------------------------
        if "capx" not in cluster_config["spec"]:
            cluster_config["spec"]["capx"] = {}

        # Always update CAPI
        cluster_config["spec"]["capx"]["capi_version"] = CAPI

        if provider == "aws":
            cluster_config["spec"]["capx"]["capa_version"] = CAPA
            cluster_config["spec"]["capx"]["capa_image_version"] = CAPA

        elif provider == "gcp":
            cluster_config["spec"]["capx"]["capg_version"] = CAPG
            cluster_config["spec"]["capx"]["capg_image_version"] = CAPG

        elif provider == "azure":
            cluster_config["spec"]["capx"]["capz_version"] = CAPZ
            cluster_config["spec"]["capx"]["capz_image_version"] = CAPZ

        # ------------------------------------------------------------------
        # Update Helm charts list
        # ------------------------------------------------------------------
        cluster_config["spec"]["charts"] = []
        for chart_name, chart_data in charts.items():
            cluster_config["spec"]["charts"].append({
                "name": chart_name,
                "version": chart_data["version"]
            })

        # ------------------------------------------------------------------
        # Patch ClusterConfig
        # ------------------------------------------------------------------
        clusterconfig_json = json.dumps(cluster_config)
        command = (
            f"{kubectl} patch clusterconfig {clusterconfig_name} "
            f"-n {clusterconfig_namespace} --type merge -p '{clusterconfig_json}'"
        )

        run_command(command)

        print("OK")

    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error updating the clusterconfig: {e}")
        raise e

def patch_clusterctl_images(registry_url):
    print("[INFO] Patching Cluster API provider images for private registry:", end=" ", flush=True)

    repo_base = os.environ.get("CAPI_REPO")

    for root, _, files in os.walk(repo_base):
        for file in files:
            if file.endswith(".yaml"):
                filepath = os.path.join(root, file)

                with open(filepath, "r") as f:
                    content = f.read()

                content = re.sub(
                    r"registry\.k8s\.io",
                    registry_url,
                    content
                )

                with open(filepath, "w") as f:
                    f.write(content)

    print("OK")

def upgrade_cluster_api_providers(provider):
    print("[INFO] Upgrading Cluster API providers:", end=" ", flush=True)

    command = (
        f"{env_vars} clusterctl upgrade apply "
        f"--kubeconfig {kubeconfig} "
        f"--core cluster-api:{CAPI} "
    )

    # Add bootstrap and control-plane flags only for Azure
    if provider == "azure":
        command += (
            f"--bootstrap kubeadm:{CAPI_KUBEADM_BOOTSTRAP} "
            f"--control-plane kubeadm:{CAPI_KUBEADM_CONTROL_PLANE} "
        )

    if provider == "aws":
        command += f"--infrastructure aws:{CAPA} "
    elif provider == "gcp":
        command += f"--infrastructure gcp:{CAPG} "
    elif provider == "azure":
        command += f"--infrastructure azure:{CAPZ} "

    command += "--wait-providers"

    run_command(command)
    print("OK")
    
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
    # Set start time
    start_time = time.time()
    print("[INFO] Starting cluster upgrade process")
    print("[INFO] Setting up the environment...")
    
    # Set backup directory
    backup_dir = "./backup/upgrade/"
    print("Backup directory: " + backup_dir)


    # Configure the logger
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)

    # Parse arguments
    config = parse_args()

    # Set kubeconfig
    print("[INFO] Setting kubeconfig:", end =" ", flush=True)
    if os.environ.get("KUBECONFIG"):
        kubeconfig = os.environ.get("KUBECONFIG")
    else:
        kubeconfig = os.path.expanduser(config["kubeconfig"])
    print("OK")

    # Check clusterctl version
    print("[INFO] Checking clusterctl version:", end =" ", flush=True)
    command = "clusterctl version -o short"
    status, output = subprocess.getstatusoutput(command)
    if (status != 0) or (get_version(output) < get_version(CLUSTERCTL)):
        print("[ERROR] clusterctl version " + CLUSTERCTL + " is required")
        sys.exit(1)
    print("OK")

    # Check if secrets file and kubeconfig file exist
    print("[INFO] Checking secrets file and kubeconfig file:", end =" ", flush=True)
    if not os.path.exists(config["secrets"]):
        print("[ERROR] Secrets file not found")
        sys.exit(1)
    if not os.path.exists(kubeconfig):
        print("[ERROR] Kubeconfig file not found")
        sys.exit(1)
    print("OK")

    # Get data from vault secrets file (secrets.yml)
    print("[INFO] Reading secrets file", end =" ", flush=True)  
    try:
        vault = Vault(config["vault_password"])
        vault_secrets_data = vault.load(open(config["secrets"]).read())
    except Exception as e:
        print("[ERROR] Decoding secrets file failed:\n" + str(e))
        sys.exit(1)
    print("OK")

    # Configure aws CLI
    print("[INFO] Configuring cloud provider CLI credentials", end =" ", flush=True)  
    if 'aws' in vault_secrets_data['secrets']:
        configure_aws_credentials(vault_secrets_data)
    elif 'azure' in vault_secrets_data['secrets']:
        configure_azure_credentials(vault_secrets_data)
    print("OK")

    # Print kubeconfig path
    print("[INFO] Using kubeconfig: " + kubeconfig)

    # Set kubectl
    print("[INFO] Setting kubectl with kubeconfig", end =" ", flush=True)
    kubectl = "kubectl --kubeconfig " + kubeconfig

    # Set helm
    print("[INFO] Setting helm with kubeconfig", end =" ", flush=True)
    helm = "helm --kubeconfig " + kubeconfig
    
    # Detect provider early from secrets file
    if 'aws' in vault_secrets_data['secrets']:
        provider = "aws"
    elif 'azure' in vault_secrets_data['secrets']:
        provider = "azure"
    elif 'gcp' in vault_secrets_data['secrets']:
        provider = "gcp"
    else:
        print("[ERROR] Unable to detect provider from secrets file")
        sys.exit(1)
    
    print("[INFO] Detected provider: " + provider)
    
    # Extract cluster name from kubeconfig context
    try:
        context_cmd = f"kubectl --kubeconfig {kubeconfig} config current-context"
        current_context = subprocess.check_output(context_cmd, shell=True, text=True, stderr=subprocess.DEVNULL).strip()
        # Try to extract cluster name from context (format varies by provider)
        # For EKS: usually contains cluster name
        # For AKS: format is typically clustername
        # For GKE: format is gke_project_zone_clustername
        if provider == "gcp" and "gke_" in current_context:
            cluster_name_guess = current_context.split("_")[-1]
        elif provider == "aws" and "@" in current_context:
            cluster_name_guess = current_context.split("@")[1].split(".")[0]
        else:
            cluster_name_guess = current_context.split("/")[-1].split("@")[-1]
        print(f"[INFO] Detected cluster name from context: {cluster_name_guess}")
    except Exception as e:
        cluster_name_guess = None
        print(f"[WARN] Could not extract cluster name from kubeconfig context: {e}")
    
    # Validate kubectl access BEFORE trying to get resources
    print("[INFO] Validating kubectl access to the cluster:", end =" ", flush=True)

    def test_kubectl():
        command = kubectl + " get ns >/dev/null 2>&1"
        return subprocess.call(command, shell=True) == 0

    # If kubectl access fails, attempt kubeconfig refresh for supported providers before exiting with error
    if not test_kubectl():
        print("FAILED (attempting kubeconfig refresh)", flush=True)

        if provider == "aws":
            region = vault_secrets_data['secrets']['aws']['credentials']['region']
            
            # Try to get cluster name from context or use provided name
            if cluster_name_guess:
                cluster_name_for_refresh = cluster_name_guess
            else:
                print("[ERROR] Cannot refresh kubeconfig: cluster name not detected from context")
                print("[HINT] Ensure your kubeconfig has a valid context set")
                sys.exit(1)

            refresh_cmd = (
                f"aws eks update-kubeconfig "
                f"--name {cluster_name_for_refresh} "
                f"--region {region} "
                f"--kubeconfig {kubeconfig}"
            )
            
            print(f"[INFO] Attempting to refresh kubeconfig for cluster: {cluster_name_for_refresh}")
            status = subprocess.call(refresh_cmd, shell=True)

            if status != 0:
                print("[ERROR] Failed to refresh kubeconfig")
                sys.exit(1)

            # Rebuild kubectl with refreshed kubeconfig
            kubectl = "kubectl --kubeconfig " + kubeconfig

            if not test_kubectl():
                print("[ERROR] kubectl still failing after kubeconfig refresh")
                sys.exit(1)

            print("OK (kubeconfig refreshed)")
            
        elif provider == "azure":
            print("[ERROR] kubectl access failed")
            print("[HINT] For Azure, refresh upgrade credentials:")
            print("[HINT] For Azure, refresh credentials by updating the kubeconfig file:")
            print(f"  1. Locate your kubeconfig at: {kubeconfig}")
            print(f"  2. Update the authentication credentials manually in the file")
            print(f"  3. Ensure the user credentials or service principal tokens are valid")
            print("[ACTION REQUIRED] After updating the credentials in the kubeconfig file, please re-run this script")
            sys.exit(1)
            
        elif provider == "gcp":
            print("[ERROR] kubectl access failed")
            print("[HINT] For GCP, refresh credentials with:")
            print(f"  gcloud container clusters get-credentials <cluster-name> --region <region> --project <project-id>")
            sys.exit(1)
            
        else:
            print("[ERROR] kubectl access failed and auto-refresh not supported for this provider")
            sys.exit(1)
    else:
        print("OK")
    
    # Get KeosCluster and ClusterConfig
    print("[INFO] Getting KeosCluster and ClusterConfig", end =" ", flush=True)
    keos_cluster, cluster_config = get_keos_cluster_cluster_config()
    print("OK")

    # Get cluster_name from KeosCluster metadata
    print("[INFO] Getting cluster name from KeosCluster metadata", end =" ", flush=True)
    if "metadata" in keos_cluster:
        cluster_name = keos_cluster["metadata"]["name"]
    else:
        print("[ERROR] KeosCluster definition not found. Ensure that KeosCluster is defined before ClusterConfig in the descriptor file")
        sys.exit(1)
    print("OK")

    print("[INFO] Cluster name: " + cluster_name)
    
    # Verify provider matches
    provider_from_cluster = keos_cluster["spec"]["infra_provider"]
    if provider != provider_from_cluster:
        print(f"[WARN] Provider mismatch: detected '{provider}' from secrets but cluster reports '{provider_from_cluster}'")
        provider = provider_from_cluster
    
    print("[INFO] Provider: " + provider)
    
    if not config["dry_run"] and not config["yes"]:
        request_confirmation()

    # Check supported upgrades (provider already retrieved above)
    managed = keos_cluster["spec"]["control_plane"]["managed"]
    if not ((provider == "aws" and managed) or (provider == "azure" and not managed) or (provider == "gcp" and managed)):
        print("[ERROR] Upgrade is only supported for EKS, GKE and Azure VMs clusters")
        sys.exit(1)

    # Setting clusterctl env vars 
    env_vars = "CLUSTER_TOPOLOGY=true CLUSTERCTL_DISABLE_VERSIONCHECK=true GOPROXY=off"

    # Get and update the helm repository if needed
    helm_repository_current = get_helm_repository(keos_cluster)
    helm_repository = input(f"The current helm repository is: {helm_repository_current}. Do you want to indicate a new helm repository? Press enter or specify new repository: ")
    if helm_repository == "" or helm_repository == helm_repository_current:
        print("[INFO] Helm repository unchanged: SKIP")
    else:
        validate_helm_repository(helm_repository)
        update_helm_repository(cluster_name, helm_repository, config["dry_run"])

    # Scale down cluster-autoscaler to avoid issues during the upgrade process
    scale_cluster_autoscaler(0, config["dry_run"])
 
    # Configure provider-specific environment variables and credentials for clusterctl
    print("[INFO] Configuring provider-specific environment variables for clusterctl:", end=" ", flush=True)

    if provider == "aws":
        # AWS/CAPA (Cluster API Provider AWS) configuration
        namespace = "capa-system"
        version = CAPA
        # Extract AWS credentials from Kubernetes secret
        credentials = subprocess.getoutput(kubectl + " -n " + namespace + " get secret capa-manager-bootstrap-credentials -o jsonpath='{.data.credentials}'")
        # Enable EKS IAM integration and set base64-encoded credentials
        env_vars += " CAPA_EKS_IAM=true AWS_B64ENCODED_CREDENTIALS=" + credentials

    elif provider == "gcp":
        # GCP/CAPG (Cluster API Provider GCP) configuration
        namespace = "capg-system"
        version = CAPG
        # Extract GCP service account credentials from Kubernetes secret
        credentials = subprocess.getoutput(kubectl + " -n " + namespace + " get secret capg-manager-bootstrap-credentials -o json | jq -r '.data[\"credentials.json\"]'")
        # Enable experimental features for managed GKE clusters
        if managed:
            env_vars += " EXP_MACHINE_POOL=true EXP_CAPG_GKE=true"
        # Set base64-encoded GCP credentials
        env_vars += " GCP_B64ENCODED_CREDENTIALS=" + credentials

    elif provider == "azure":
        # Azure/CAPZ (Cluster API Provider Azure) configuration
        namespace = "capz-system"
        version = CAPZ
        # Enable experimental machine pool support for managed clusters
        if managed:
            env_vars += " EXP_MACHINE_POOL=true"
        
        # Configure Azure service principal credentials from vault secrets
        if "credentials" in vault_secrets_data["secrets"]["azure"]:
            credentials = vault_secrets_data["secrets"]["azure"]["credentials"]
            # Encode Azure credentials in base64 format for clusterctl
            env_vars += " AZURE_CLIENT_ID_B64=" + base64.b64encode(credentials["client_id"].encode("ascii")).decode("ascii")
            env_vars += " AZURE_CLIENT_SECRET_B64=" + base64.b64encode(credentials["client_secret"].encode("ascii")).decode("ascii")
            env_vars += " AZURE_SUBSCRIPTION_ID_B64=" + base64.b64encode(credentials["subscription_id"].encode("ascii")).decode("ascii")
            env_vars += " AZURE_TENANT_ID_B64=" + base64.b64encode(credentials["tenant_id"].encode("ascii")).decode("ascii")
        else:
            print("[ERROR] Azure credentials not found in secrets file")
            sys.exit(1)

    print("OK")

    # Set GITHUB_TOKEN env var if exists in vault secrets to avoid hitting github API rate limits during the upgrade process
    print("[INFO] Setting GITHUB_TOKEN environment:", end=" ", flush=True)
    if "github_token" in vault_secrets_data["secrets"]:
        env_vars += " GITHUB_TOKEN=" + vault_secrets_data["secrets"]["github_token"]
        helm = "GITHUB_TOKEN=" + vault_secrets_data["secrets"]["github_token"] + " " + helm
        kubectl = "GITHUB_TOKEN=" + vault_secrets_data["secrets"]["github_token"] + " " + kubectl
        print("OK")
    else:
        print("SKIP (not configured)")

    # Configure backup if not disabled
    if not config["disable_backup"]:
        now = datetime.now()
        backup_dir = backup_dir + now.strftime("%Y%m%d-%H%M%S")
        backup(backup_dir, namespace, cluster_name, config["dry_run"])
    else:
        print("[INFO] Backup disabled: SKIP")

    # Prepare capsule
    if not config["disable_prepare_capsule"]:
        prepare_capsule(config["dry_run"])
    else:
        print("[INFO] Capsule preparation disabled: SKIP")

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
    print("[INFO] All charts updated successfully")
    
    # Restore capsule
    if not config["disable_prepare_capsule"]:
        restore_capsule(config["dry_run"])
    
    print("[INFO] Waiting for the cluster-operator helmrelease to be ready...")
    command = f"{kubectl} wait helmrelease cluster-operator -n kube-system --for=jsonpath='{{.status.conditions[?(@.type==\"Ready\")].status}}'=True --timeout=5m"
    run_command(command)
    print("[INFO] Upgrading Cluster Operator components...")
    print("[INFO] Suspending cluster-operator helmrelease:", end =" ", flush=True)

    command = kubectl + " patch helmrelease cluster-operator -n kube-system --type merge --patch '{\"spec\":{\"suspend\":true}}'"
    run_command(command)
    print("OK")
    
    stop_keoscluster_controller()
    disable_keoscluster_webhooks()
    update_clusterconfig(cluster_config, charts_to_upgrade, provider, cluster_operator_version)

    # Upgrade Cluster API providers
    print("[INFO] Upgrading Cluster API providers...")
    # If private registry is used, we need to patch the Cluster API provider manifests to use the private registry before upgrading them, otherwise the upgrade will fail because clusterctl will try to pull the images from the public registry 
    if private_registry:
        registry_url = get_keos_registry_url(keos_cluster)
        patch_clusterctl_images(registry_url)
    upgrade_cluster_api_providers(provider)
    print("[INFO] Cluster API providers upgraded successfully")

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
    print("[INFO] keoscluster-controller-manager is Available")
    
    print("[INFO] Restoring cluster-autoscaler replicas")
    scale_cluster_autoscaler(2, config["dry_run"])
   
    end_time = time.time()
    elapsed_time = end_time - start_time
    minutes, seconds = divmod(elapsed_time, 60)
    print("[INFO] Upgrade process finished successfully in " + str(int(minutes)) + " minutes and " + "{:.2f}".format(seconds) + " seconds")