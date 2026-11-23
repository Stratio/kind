# -*- coding: utf-8 -*-
"""commons.backup — moved verbatim from upgrade-provisioner.py (PLT-4916)."""

import os
import sys
import json
import subprocess
import time
from upgrade_lib.commons.shell import execute_command, run_command
from upgrade_lib import state as S
from upgrade_lib.versions import CAPI

def backup(backup_dir, namespace, cluster_name, dry_run):
    '''Backup CAPX cluster move files, capsule webhooks and CAPI/CAPX namespace secrets'''

    print("[INFO] Backing up files into directory " + backup_dir)
    # Backup CAPX files
    print("[INFO] Backing up CAPX files:", end =" ", flush=True)
    if dry_run:
        print("DRY-RUN")
    else:
        os.makedirs(backup_dir + "/" + namespace, exist_ok=True)
        # upgrade-providers.py has already moved the core to v1beta2; the image's default clusterctl (v1.10.10) rejects it
        command = "clusterctl-" + CAPI + " --kubeconfig " + S.kubeconfig + " -n cluster-" + cluster_name + " move --to-directory " + backup_dir + "/" + namespace + " 2>&1"
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
        output, _ = run_command(S.kubectl + " get capsuleconfiguration default --ignore-not-found -o yaml", allow_errors=True)
        if output.strip():
            with open(backup_dir + "/capsule/capsuleconfiguration-default.yaml", "w") as f:
                f.write(output)
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

def capsule_system_namespaces():
    '''Namespaces kept out of capsule's namespace webhooks during the upgrade (the capsule 0.x mutating list below)'''
    return ["kube-system", "tigera-operator", "calico-system", "cert-manager", "capi-system", S.namespace,
            "capi-kubeadm-bootstrap-system", "capi-kubeadm-control-plane-system"]

def capsule_configuration():
    '''CapsuleConfiguration "default" when capsule (1.0.x) generates its webhooks from spec.admission, else None'''
    output, _ = run_command(S.kubectl + " get capsuleconfiguration default --ignore-not-found -o json", allow_errors=True)
    try:
        cc = json.loads(output) if output.strip() else {}
    except ValueError:
        return None
    return cc if cc.get("spec", {}).get("admission") else None

def capsule_namespace_webhook_paths(cc):
    '''JSON paths of the namespace webhooks in CapsuleConfiguration.spec.admission, with their current objectSelector'''
    paths = []
    for kind in ("mutating", "validating"):
        for i, webhook in enumerate(cc["spec"]["admission"].get(kind, {}).get("webhooks", [])):
            if any("namespaces" in (rule.get("resources") or []) for rule in webhook.get("rules", [])):
                paths.append((f"/spec/admission/{kind}/webhooks/{i}/objectSelector", webhook.get("objectSelector")))
    return paths

def wait_capsule_webhooks_regenerated(cc, selector_of):
    '''Capsule regenerates its dynamic webhook configurations from the CapsuleConfiguration: wait until they carry selector_of(name)'''
    deadline = time.time() + 120
    while time.time() < deadline:
        done = True
        for kind in ("mutating", "validating"):
            name = cc["spec"]["admission"].get(kind, {}).get("name")
            if not name:
                continue
            raw, _ = run_command(f"{S.kubectl} get {kind}webhookconfiguration {name} -o json", allow_errors=True)
            try:
                webhooks = json.loads(raw).get("webhooks", [])
            except ValueError:
                done = False
                continue
            for webhook in webhooks:
                if any("namespaces" in (rule.get("resources") or []) for rule in webhook.get("rules", [])):
                    if (webhook.get("objectSelector") or {}) != (selector_of(webhook["name"]) or {}):
                        done = False
        if done:
            return
        time.sleep(5)
    raise Exception("capsule did not regenerate its dynamic webhook configurations within 120s")

def prepare_capsule_configuration(cc, dry_run):
    '''Capsule 1.0.x: exclude the system namespaces in CapsuleConfiguration.spec.admission; the originals are kept for restore_capsule()'''
    print("[INFO] Preparing CapsuleConfiguration default (namespace webhooks) for the upgrade process:", end=" ", flush=True)
    if dry_run:
        print("DRY-RUN")
        return
    exclusion = {"matchExpressions": [{"key": "kubernetes.io/metadata.name", "operator": "NotIn", "values": capsule_system_namespaces()}]}
    patch, restore = [], []
    for path, original in capsule_namespace_webhook_paths(cc):
        patch.append({"op": "add", "path": path, "value": exclusion})
        # An exclusion left by an interrupted run is not an original selector
        if original and original != exclusion:
            restore.append({"op": "add", "path": path, "value": original})
        else:
            restore.append({"op": "remove", "path": path})
    if not patch:
        print("SKIP (no namespace webhooks)")
        return
    if S.backup_dir:
        os.makedirs(S.backup_dir + "/capsule", exist_ok=True)
        with open(S.backup_dir + "/capsule/capsuleconfiguration-restore-patch.json", "w") as f:
            json.dump(restore, f)
    run_command(f"{S.kubectl} patch capsuleconfiguration default --type json -p '{json.dumps(patch)}'")
    S.capsule_restore_patch = restore
    wait_capsule_webhooks_regenerated(cc, lambda name: exclusion)
    print("OK")

def restore_capsule_configuration(cc, dry_run):
    '''Capsule 1.0.x: put back the objectSelectors prepare_capsule_configuration() replaced'''
    print("[INFO] Restoring CapsuleConfiguration default (namespace webhooks):", end=" ", flush=True)
    if dry_run:
        print("DRY-RUN")
        return
    if not S.capsule_restore_patch:
        print("SKIP (not prepared in this run)")
        return
    run_command(f"{S.kubectl} patch capsuleconfiguration default --type json -p '{json.dumps(S.capsule_restore_patch)}'")
    restored = {op["path"]: op.get("value") for op in S.capsule_restore_patch}
    cc = capsule_configuration() or cc
    names = {}
    for kind in ("mutating", "validating"):
        for i, webhook in enumerate(cc["spec"]["admission"].get(kind, {}).get("webhooks", [])):
            names[webhook["name"]] = restored.get(f"/spec/admission/{kind}/webhooks/{i}/objectSelector")
    wait_capsule_webhooks_regenerated(cc, lambda name: names.get(name))
    S.capsule_restore_patch = None
    print("OK")

def prepare_capsule(dry_run):
    '''Prepare capsule for the upgrade process'''

    cc = capsule_configuration()
    if cc:
        prepare_capsule_configuration(cc, dry_run)
        return

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

    cc = capsule_configuration()
    if cc:
        restore_capsule_configuration(cc, dry_run)
        return

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
