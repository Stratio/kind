# -*- coding: utf-8 -*-
"""commons.backup — moved verbatim from upgrade-provisioner.py (PLT-4916)."""

import os
import sys
import json
import subprocess
from upgrade_lib.commons.shell import execute_command, run_command
from upgrade_lib import state as S

def backup(backup_dir, namespace, cluster_name, dry_run):
    '''Backup CAPX cluster move files, capsule webhooks and CAPI/CAPX namespace secrets'''

    print("[INFO] Backing up files into directory " + backup_dir)
    # Backup CAPX files
    print("[INFO] Backing up CAPX files:", end =" ", flush=True)
    if dry_run:
        print("DRY-RUN")
    else:
        os.makedirs(backup_dir + "/" + namespace, exist_ok=True)
        command = "clusterctl --kubeconfig " + S.kubeconfig + " -n cluster-" + cluster_name + " move --to-directory " + backup_dir + "/" + namespace + " >/dev/null 2>&1"
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
        capsule_backed_up = False
        command = S.kubectl + " get mutatingwebhookconfigurations capsule-mutating-webhook-configuration"
        status, _ = subprocess.getstatusoutput(command)
        if status == 0:
            command = S.kubectl + " get mutatingwebhookconfigurations capsule-mutating-webhook-configuration -o yaml 2>/dev/null > " + backup_dir + "/capsule/capsule-mutating-webhook-configuration.yaml"
            status, output = subprocess.getstatusoutput(command)
            if status != 0:
                print("FAILED")
                print("[ERROR] Backing up capsule files failed:\n" + output)
                sys.exit(1)
            capsule_backed_up = True
        command = S.kubectl + " get validatingwebhookconfigurations capsule-validating-webhook-configuration"
        status, output = subprocess.getstatusoutput(command)
        if status == 0:
            command = S.kubectl + " get validatingwebhookconfigurations capsule-validating-webhook-configuration -o yaml 2>/dev/null > " + backup_dir + "/capsule/capsule-validating-webhook-configuration.yaml"
            status, output = subprocess.getstatusoutput(command)
            if status != 0:
                print("FAILED")
                print("[ERROR] Backing up capsule files failed:\n" + output)
                sys.exit(1)
            capsule_backed_up = True
        if capsule_backed_up:
            print("OK")
        else:
            print("SKIP")
    else:
        print("DRY-RUN")
    # Backup CAPI/CAPA/CAPZ/CAPG secrets
    capx_namespaces = [
        "capi-system",
        "capi-kubeadm-bootstrap-system",
        "capi-kubeadm-control-plane-system",
        "capa-system",
        "capz-system",
        "capg-system",
    ]
    print("[INFO] Backing up CAPX secrets:", end=" ", flush=True)
    if dry_run:
        print("DRY-RUN")
    else:
        capx_secrets_backed_up = False
        for ns in capx_namespaces:
            # Check if the namespace exists
            check_ns_cmd = S.kubectl + f" get namespace {ns} --ignore-not-found -o name"
            ns_status, ns_output = subprocess.getstatusoutput(check_ns_cmd)
            if ns_status != 0 or not ns_output.strip():
                continue
            # List secrets in the namespace
            list_cmd = S.kubectl + f" get secret -n {ns} -o name"
            list_status, list_output = subprocess.getstatusoutput(list_cmd)
            if list_status != 0 or not list_output.strip():
                continue
            ns_backup_dir = backup_dir + "/capx-secrets/" + ns
            os.makedirs(ns_backup_dir, exist_ok=True)
            for secret_ref in list_output.strip().splitlines():
                secret_name = secret_ref.split("/")[-1]
                out_file = ns_backup_dir + "/" + secret_name + ".yaml"
                dump_cmd = S.kubectl + f" get secret -n {ns} {secret_name} -o yaml 2>/dev/null > {out_file}"
                dump_status, dump_output = subprocess.getstatusoutput(dump_cmd)
                if dump_status != 0:
                    print("FAILED")
                    print(f"[ERROR] Backing up secret {ns}/{secret_name} failed:\n" + dump_output)
                    sys.exit(1)
                capx_secrets_backed_up = True
        if capx_secrets_backed_up:
            print("OK")
        else:
            print("SKIP")

def prepare_capsule(dry_run):
    '''Prepare capsule for the upgrade process'''

    print("[INFO] Preparing capsule-mutating-webhook-configuration for the upgrade process:", end =" ", flush=True)
    if not dry_run:
        command = S.kubectl + " get mutatingwebhookconfigurations capsule-mutating-webhook-configuration"
        status, output = subprocess.getstatusoutput(command)
        if status != 0:
            if "NotFound" in output:
                print("SKIP")
            else:
                print("FAILED")
                print("[ERROR] Preparing capsule-mutating-webhook-configuration failed:\n" + output)
                sys.exit(1)
        else:
            command = (S.kubectl + " get mutatingwebhookconfigurations capsule-mutating-webhook-configuration -o json | " +
                    '''jq -r '.webhooks[0].objectSelector |= {"matchExpressions":[{"key":"name","operator":"NotIn","values":["kube-system","tigera-operator","calico-system","cert-manager","capi-system","''' +
                    S.namespace + '''","capi-kubeadm-bootstrap-system","capi-kubeadm-control-plane-system"]},{"key":"kubernetes.io/metadata.name","operator":"NotIn","values":["kube-system","tigera-operator","calico-system","cert-manager","capi-system","''' +
                    S.namespace + '''","capi-kubeadm-bootstrap-system","capi-kubeadm-control-plane-system"]}]}' | ''' + S.kubectl + " apply -f -")
            execute_command(command, False)
    else:
        print("DRY-RUN")

    print("[INFO] Preparing capsule-validating-webhook-configuration for the upgrade process:", end =" ", flush=True)
    if not dry_run:
        command = S.kubectl + " get validatingwebhookconfigurations capsule-validating-webhook-configuration"
        status, output = subprocess.getstatusoutput(command)
        if status != 0:
            if "NotFound" in output:
                print("SKIP")
            else:
                print("FAILED")
                print("[ERROR] Preparing capsule-validating-webhook-configuration failed:\n" + output)
                sys.exit(1)
        else:
            command = (S.kubectl + " get validatingwebhookconfigurations capsule-validating-webhook-configuration -o json | " +
                    '''jq -r '.webhooks[] |= (select(.name == "namespaces.capsule.clastix.io").objectSelector |= ({"matchExpressions":[{"key":"name","operator":"NotIn","values":["''' +
                    S.namespace + '''","tigera-operator","calico-system"]},{"key":"kubernetes.io/metadata.name","operator":"NotIn","values":["''' +
                    S.namespace + '''","tigera-operator","calico-system"]}]}))' | ''' + S.kubectl + " apply -f -")
            execute_command(command, False)
    else:
        print("DRY-RUN")

def restore_capsule(dry_run):
    '''Restore capsule after the upgrade process'''

    print("[INFO] Restoring capsule-mutating-webhook-configuration:", end =" ", flush=True)
    if not dry_run:
        command = S.kubectl + " get mutatingwebhookconfigurations capsule-mutating-webhook-configuration"
        status, output = subprocess.getstatusoutput(command)
        if status != 0:
            if "NotFound" in output:
                print("SKIP")
            else:
                print("FAILED")
                print("[ERROR] Restoring capsule-mutating-webhook-configuration failed:\n" + output)
                sys.exit(1)
        else:
            command = (S.kubectl + " get mutatingwebhookconfigurations capsule-mutating-webhook-configuration -o json | " +
                    "jq -r '.webhooks[0].objectSelector |= {}' | " + S.kubectl + " apply -f -")
            execute_command(command, False)
    else:
        print("DRY-RUN")

    print("[INFO] Restoring capsule-validating-webhook-configuration:", end =" ", flush=True)
    if not dry_run:
        command = S.kubectl + " get validatingwebhookconfigurations capsule-validating-webhook-configuration"
        status, output = subprocess.getstatusoutput(command)
        if status != 0:
            if "NotFound" in output:
                print("SKIP")
            else:
                print("FAILED")
                print("[ERROR] Restoring capsule-validating-webhook-configuration failed:\n" + output)
                sys.exit(1)
        else:
            command = (S.kubectl + " get validatingwebhookconfigurations capsule-validating-webhook-configuration -o json | " +
                    """jq -r '.webhooks[] |= (select(.name == "namespaces.capsule.clastix.io").objectSelector |= {})' """ +
                    "| " + S.kubectl + " apply -f -")
            execute_command(command, False)
    else:
        print("DRY-RUN")

def capsule_nodes_webhook(action, backup_dir, dry_run):
    '''Remove/restore capsule's nodes webhook entry — can block a new CP Machine's join (PLT-3295).'''

    webhook_config = "capsule-validating-webhook-configuration"
    raw, err = run_command(f"{S.kubectl} get validatingwebhookconfigurations {webhook_config} -o json", allow_errors=True)
    if err:
        return
    config_json = json.loads(raw)
    webhook_names = [w["name"] for w in config_json.get("webhooks", [])]
    if "nodes.capsule.clastix.io" not in webhook_names and action == "patch":
        return

    capsule_backup_dir = backup_dir + "/capsule"
    backup_file = capsule_backup_dir + "/nodes-webhook-entry.json"

    if action == "patch":
        print("[INFO] Removing nodes.capsule.clastix.io webhook for the CP bump:", end=" ", flush=True)
        if dry_run:
            print("DRY-RUN")
            return
        os.makedirs(capsule_backup_dir, exist_ok=True)
        nodes_webhook = next(w for w in config_json["webhooks"] if w["name"] == "nodes.capsule.clastix.io")
        with open(backup_file, 'w') as f:
            json.dump(nodes_webhook, f)
        config_json["webhooks"] = [w for w in config_json["webhooks"] if w["name"] != "nodes.capsule.clastix.io"]
        patch_file = "/tmp/capsule_validating_without_nodes.json"
        with open(patch_file, 'w') as f:
            json.dump(config_json, f)
        run_command(f"{S.kubectl} replace -f {patch_file}")
        os.remove(patch_file)
        print("OK")

    elif action == "restore":
        print("[INFO] Restoring nodes.capsule.clastix.io webhook:", end=" ", flush=True)
        if dry_run:
            print("DRY-RUN")
            return
        if not os.path.exists(backup_file):
            print("SKIPPED (no backup found — entry was already present)")
            return
        if "nodes.capsule.clastix.io" in webhook_names:
            print("SKIPPED (already present)")
            return
        with open(backup_file) as f:
            nodes_webhook = json.load(f)
        config_json["webhooks"].append(nodes_webhook)
        patch_file = "/tmp/capsule_validating_with_nodes.json"
        with open(patch_file, 'w') as f:
            json.dump(config_json, f)
        run_command(f"{S.kubectl} replace -f {patch_file}")
        os.remove(patch_file)
        print("OK")
