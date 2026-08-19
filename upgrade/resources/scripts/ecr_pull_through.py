#!/usr/bin/env python3
# -*- coding: utf-8 -*-

##############################################################
# Author: Stratio Clouds <clouds-integration@stratio.com>    #
# Purpose: ECR pull-through cache migration                  #
#   - Upgrade cluster-operator                               #
#   - Enable ecr_pull_through_cache_enabled in KeosCluster   #
##############################################################

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from urllib.parse import urlparse

from ansible_vault import Vault
import yaml

# Force line buffering so log lines stay in execution order (e.g. when piped to a file).
sys.stdout.reconfigure(line_buffering=True)

kubectl = ""
helm = ""


# ── Utilities ────────────────────────────────────────────────────────────────

def run_command(command, allow_errors=False, retries=3, retry_delay=2):
    attempts = 0
    while attempts <= retries:
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout, result.stderr
        if allow_errors:
            return result.stdout, result.stderr
        attempts += 1
        if attempts > retries:
            raise Exception(f"Error executing '{command}' after {retries + 1} attempts: {result.stderr}")
        time.sleep(retry_delay)


def configure_aws_credentials(vault_secrets_data):
    print("[INFO] Configuring AWS CLI credentials", end=" ", flush=True)

    aws_creds = vault_secrets_data['secrets']['aws']['credentials']
    os.environ["AWS_PAGER"] = ""
    os.environ["AWS_ACCESS_KEY_ID"] = aws_creds['access_key']
    os.environ["AWS_SECRET_ACCESS_KEY"] = aws_creds['secret_key']
    os.environ["AWS_DEFAULT_REGION"] = aws_creds['region']

    role_arn = aws_creds.get('role_arn')
    if role_arn:
        result = subprocess.run(
            ["aws", "sts", "assume-role",
             "--role-arn", role_arn,
             "--role-session-name", "ecr-pull-through-session"],
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


def validate_kubectl_access(region):
    '''Verify kubectl access, refreshing the kubeconfig via aws eks update-kubeconfig if needed.'''
    global kubectl

    def test_kubectl():
        return subprocess.call(f"{kubectl} get ns >/dev/null 2>&1", shell=True) == 0

    print("[INFO] Validating kubectl access to the cluster:", end=" ", flush=True)
    if test_kubectl():
        print("OK")
        return

    print("FAILED (attempting kubeconfig refresh)", flush=True)

    try:
        context_cmd = f"{kubectl} config current-context"
        current_context = subprocess.check_output(context_cmd, shell=True, text=True, stderr=subprocess.DEVNULL).strip()
        cluster_name_guess = current_context.split("@")[1].split(".")[0] if "@" in current_context else current_context.split("/")[-1]
    except Exception as e:
        print(f"[ERROR] Cannot refresh kubeconfig: could not detect cluster name from context ({e})")
        print("[HINT] Ensure your kubeconfig has a valid context set")
        sys.exit(1)

    kubeconfig_path = kubectl.split("--kubeconfig ")[-1]
    refresh_cmd = f"aws eks update-kubeconfig --name {cluster_name_guess} --region {region} --kubeconfig {kubeconfig_path}"
    print(f"[INFO] Attempting to refresh kubeconfig for cluster: {cluster_name_guess}")
    if subprocess.call(refresh_cmd, shell=True) != 0:
        print("[ERROR] Failed to refresh kubeconfig")
        sys.exit(1)

    if not test_kubectl():
        print("[ERROR] kubectl still failing after kubeconfig refresh")
        print("[HINT] The AWS identity active in this environment may lack RBAC access to this cluster — refreshing the kubeconfig cannot fix that, it can only fix a stale one.")
        sys.exit(1)

    print("OK (kubeconfig refreshed)")


def get_keos_cluster():
    output, _ = run_command(f"{kubectl} get keoscluster -A -o json")
    items = json.loads(output)["items"]
    if not items:
        raise Exception("No KeosCluster found")
    return items[0]


def get_keos_registry_url(keos_cluster):
    for registry in keos_cluster["spec"].get("docker_registries", []):
        if registry.get("keos_registry", False):
            return registry["url"]
    raise Exception("No keos_registry entry found in KeosCluster spec.docker_registries")


def get_helm_repository_url(keos_cluster):
    try:
        return keos_cluster["spec"]["helm_repository"]["url"]
    except KeyError:
        raise Exception("No helm_repository.url in KeosCluster spec")


def ecr_registry_host(url):
    """Extract registry hostname from an OCI or HTTPS URL."""
    clean = url.replace("oci://", "https://") if url.startswith("oci://") else url
    return urlparse(clean).hostname


def ecr_login(repo_url):
    host = ecr_registry_host(repo_url)
    region = host.split(".")[3]  # <account>.dkr.ecr.<region>.amazonaws.com
    run_command(
        f"aws ecr get-login-password --region {region} | "
        f"{helm} registry login {host} --username AWS --password-stdin"
    )



def apply_configmap(cm_json):
    """Write ConfigMap JSON to a temp file and kubectl apply it."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tf:
        tf.write(json.dumps(cm_json))
        tf_path = tf.name
    try:
        run_command(f"{kubectl} apply -f {tf_path}")
    finally:
        os.unlink(tf_path)


def wait_helmrelease_ready(release, namespace, timeout=300):
    print(f"[INFO] Waiting for HelmRelease {release}/{namespace} to be Ready", end=" ", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        out, _ = run_command(
            f"{kubectl} get helmrelease {release} -n {namespace} "
            f"-o jsonpath='{{.status.conditions[?(@.type==\"Ready\")].status}}'",
            allow_errors=True
        )
        if out.strip() == "True":
            print("OK")
            return
        time.sleep(10)
    raise Exception(f"HelmRelease {release} not Ready after {timeout}s")


def wait_keoscluster_provisioned(timeout=300):
    print("[INFO] Waiting for KeosCluster Provisioned", end=" ", flush=True)
    run_command(
        f"{kubectl} wait keoscluster --all -A "
        f"--for=jsonpath='{{.status.phase}}'=Provisioned --timeout={timeout}s"
    )
    print("OK")


def strip_server_side_fields(obj):
    '''Remove fields the apiserver populates, so a later `kubectl apply` of this
    snapshot doesn't conflict with whatever resourceVersion the object has by then.'''
    obj.pop("status", None)
    for field in ("resourceVersion", "uid", "generation", "creationTimestamp", "managedFields", "selfLink"):
        obj["metadata"].pop(field, None)


def backup_pre_upgrade_state(kc_name, kc_ns, old_version, helm_repo_url_current, cm_json):
    '''Snapshot everything this script is about to mutate, for manual recovery only (not an automatic rollback).'''
    backup_dir = os.path.join("backup", "ecr_pull_through", datetime.utcnow().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(backup_dir, exist_ok=True)

    print(f"[INFO] Backing up pre-upgrade state to {backup_dir}", end=" ", flush=True)

    for crd_name in ("keosclusters.installer.stratio.com", "clusterconfigs.installer.stratio.com"):
        out, err = run_command(f"{kubectl} get crd {crd_name} -o yaml", allow_errors=True)
        if out:
            crd_obj = yaml.safe_load(out)
            strip_server_side_fields(crd_obj)
            with open(os.path.join(backup_dir, f"{crd_name}.yaml"), "w") as f:
                yaml.safe_dump(crd_obj, f)

    strip_server_side_fields(cm_json)
    with open(os.path.join(backup_dir, "configmap-default-values.json"), "w") as f:
        f.write(json.dumps(cm_json, indent=2))

    helmrepo_url_out, _ = run_command(f"{kubectl} get helmrepository keos -n kube-system -o jsonpath='{{.spec.url}}'", allow_errors=True)
    ecr_pull_through_out, _ = run_command(
        f"{kubectl} get keoscluster {kc_name} -n {kc_ns} "
        f"-o jsonpath='{{.spec.docker_registries[0].ecr_pull_through_cache_enabled}}'",
        allow_errors=True
    )

    state = {
        "keoscluster_name": kc_name,
        "keoscluster_namespace": kc_ns,
        "cluster_operator_version": old_version,
        "keoscluster_helm_repository_url": helm_repo_url_current,
        "helmrepository_keos_url": helmrepo_url_out.strip(),
        "ecr_pull_through_cache_enabled_before": ecr_pull_through_out.strip() or "false",
    }
    with open(os.path.join(backup_dir, "state.json"), "w") as f:
        json.dump(state, f, indent=2)

    print("OK")
    return backup_dir


def find_latest_backup():
    '''Most recently created backup/ecr_pull_through/<timestamp>/ dir (folder names sort chronologically).'''
    state_files = sorted(glob.glob(os.path.join("backup", "ecr_pull_through", "*", "state.json")))
    if not state_files:
        return None
    return os.path.dirname(state_files[-1])


def restore(backup_dir):
    if not backup_dir:
        backup_dir = find_latest_backup()
        if not backup_dir:
            raise Exception("No backup found under backup/ecr_pull_through/ — nothing to restore")
        print(f"[INFO] No --restore path given, using latest backup: {backup_dir}")

    state_path = os.path.join(backup_dir, "state.json")
    if not os.path.exists(state_path):
        hint = ""
        if os.path.isabs(backup_dir) and not backup_dir.startswith(os.getcwd()):
            hint = (" — this looks like a host path; --restore takes a path relative to "
                    "the container's working directory (run `ls backup/ecr_pull_through/` "
                    "inside the container to see available backups)")
        raise Exception(f"{state_path} not found — not a valid backup directory{hint}")
    with open(state_path) as f:
        state = json.load(f)

    if "keoscluster_name" in state:
        kc_name = state["keoscluster_name"]
        kc_ns = state["keoscluster_namespace"]
    else:
        # Backups taken before this field was added — fall back to resolving it live.
        print("[INFO] Backup predates keoscluster_name/namespace tracking, resolving KeosCluster live", end=" ", flush=True)
        keos_cluster = get_keos_cluster()
        kc_name = keos_cluster["metadata"]["name"]
        kc_ns = keos_cluster["metadata"]["namespace"]
        print(f"OK ({kc_name}/{kc_ns})")

    print(f"[INFO] Restoring from {backup_dir} (cluster-operator {state['cluster_operator_version']})")

    for crd_name in ("keosclusters.installer.stratio.com", "clusterconfigs.installer.stratio.com"):
        crd_file = os.path.join(backup_dir, f"{crd_name}.yaml")
        if os.path.exists(crd_file):
            print(f"[INFO] Restoring CRD {crd_name}", end=" ", flush=True)
            run_command(f"{kubectl} apply -f {crd_file}")
            print("OK")

    print("[INFO] Restoring cluster-operator ConfigMap", end=" ", flush=True)
    run_command(f"{kubectl} apply -f {os.path.join(backup_dir, 'configmap-default-values.json')}")
    print("OK")

    print(f"[INFO] Restoring KeosCluster.spec.helm_repository.url to {state['keoscluster_helm_repository_url']}", end=" ", flush=True)
    run_command(
        f"{kubectl} patch keoscluster {kc_name} -n {kc_ns} --type=json "
        f"-p '[{{\"op\":\"replace\",\"path\":\"/spec/helm_repository/url\",\"value\":\"{state['keoscluster_helm_repository_url']}\"}}]'"
    )
    print("OK")

    print(f"[INFO] Restoring HelmRepository keos url to {state['helmrepository_keos_url']}", end=" ", flush=True)
    run_command(
        f"{kubectl} patch helmrepository keos -n kube-system --type=merge "
        f"-p '{{\"spec\":{{\"url\":\"{state['helmrepository_keos_url']}\"}}}}'"
    )
    print("OK")

    print(f"[INFO] Restoring HelmRelease cluster-operator chart version to {state['cluster_operator_version']}", end=" ", flush=True)
    run_command(
        f"{kubectl} patch helmrelease cluster-operator -n kube-system --type=merge "
        f"-p '{{\"spec\":{{\"chart\":{{\"spec\":{{\"version\":\"{state['cluster_operator_version']}\"}}}}}}}}'"
    )
    print("OK")

    print("[INFO] Forcing HelmRelease reconciliation", end=" ", flush=True)
    ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    run_command(
        f"{kubectl} annotate helmrelease cluster-operator -n kube-system "
        f"reconcile.fluxcd.io/requestedAt={ts} --overwrite"
    )
    print("OK")

    wait_helmrelease_ready("cluster-operator", "kube-system")
    wait_keoscluster_provisioned()

    print(f"[INFO] Restoring ecr_pull_through_cache_enabled to {state['ecr_pull_through_cache_enabled_before']}", end=" ", flush=True)
    run_command(
        f"{kubectl} patch keoscluster {kc_name} -n {kc_ns} --type=json "
        f"-p '[{{\"op\":\"replace\",\"path\":\"/spec/docker_registries/0/ecr_pull_through_cache_enabled\","
        f"\"value\":{state['ecr_pull_through_cache_enabled_before']}}}]'"
    )
    print("OK")

    print(f"\n[OK] Restored cluster-operator {state['cluster_operator_version']} and prior registry configuration from {backup_dir}.")


def print_manual_recovery_instructions(backup_dir, error):
    print(f"\n[ERROR] {error}")
    print(f"[ERROR] Upgrade failed partway through — no automatic rollback was attempted.")
    print(f"[ACTION REQUIRED] Pre-upgrade state backed up at: {backup_dir}")
    print(f"[ACTION REQUIRED] To restore it, run: python3 ecr_pull_through.py -p <vault-password> --restore {backup_dir}")



# ── Main flow ─────────────────────────────────────────────────────────────────

def run(new_co_version, helm_registry_override=None):
    keos_cluster = get_keos_cluster()
    ecr_url = get_keos_registry_url(keos_cluster)
    helm_repo_url_current = get_helm_repository_url(keos_cluster)
    if helm_registry_override:
        helm_repo_url = helm_registry_override
    else:
        answer = input(f"The current Helm registry is: {helm_repo_url_current}. Press ENTER to use it or specify a different one: ")
        helm_repo_url = answer.strip() if answer.strip() else helm_repo_url_current
    kc_name = keos_cluster["metadata"]["name"]
    kc_ns = keos_cluster["metadata"]["namespace"]

    print(f"[INFO] Cluster: {kc_name} / ECR: {ecr_url}")
    print(f"[INFO] Helm registry: {helm_repo_url}")

    # ── Upgrade cluster-operator + enable pull-through ────────────────────────

    print("\n--- cluster-operator upgrade + ecr_pull_through_cache_enabled ---\n")

    print("[INFO] Detecting current cluster-operator version from ConfigMap", end=" ", flush=True)
    out, _ = run_command(
        f"{kubectl} get configmap 00-cluster-operator-helm-chart-default-values "
        f"-n kube-system -o jsonpath='{{.data.values\\.yaml}}'"
    )
    m = re.search(r'^\s+tag:\s+(\S+)', out, re.MULTILINE)
    if not m:
        print("FAILED")
        raise Exception("Cannot detect current cluster-operator tag in ConfigMap")
    old_version = m.group(1)
    print(f"OK ({old_version})")

    cm_out, _ = run_command(
        f"{kubectl} get configmap 00-cluster-operator-helm-chart-default-values -n kube-system -o json"
    )
    cm = json.loads(cm_out)

    backup_dir = backup_pre_upgrade_state(kc_name, kc_ns, old_version, helm_repo_url_current, cm)

    try:
        if ".dkr.ecr." in helm_repo_url:
            print("[INFO] Logging into ECR registry", end=" ", flush=True)
            ecr_login(helm_repo_url)
            print("OK")

        print(f"[INFO] Applying CRDs from cluster-operator {new_co_version}", end=" ", flush=True)
        with tempfile.TemporaryDirectory() as tmpdir:
            run_command(f"{helm} pull {helm_repo_url}/cluster-operator --version {new_co_version} -d {tmpdir}")
            tarballs = glob.glob(f"{tmpdir}/*.tgz")
            if not tarballs:
                raise Exception("No chart tarball found after helm pull")
            run_command(f"tar xzf {tarballs[0]} -C {tmpdir} cluster-operator/crds/ 2>/dev/null || true")
            crd_files = glob.glob(f"{tmpdir}/cluster-operator/crds/*.yaml")
            if not crd_files:
                print("SKIP (no CRDs in chart)")
            else:
                for crd_file in crd_files:
                    run_command(f"{kubectl} apply -f {crd_file}")
                print("OK")

        print("[INFO] Verifying ecr_pull_through_cache_enabled field in CRD", end=" ", flush=True)
        out, _ = run_command(
            f"{kubectl} get crd keosclusters.installer.stratio.com "
            f"-o jsonpath='{{.spec.versions[0].schema.openAPIV3Schema.properties.spec.properties"
            f".docker_registries.items.properties.ecr_pull_through_cache_enabled}}'",
            allow_errors=True
        )
        if "boolean" not in out:
            print("WARN — field not present; KeosCluster patch will be silently ignored")
        else:
            print("OK")

        print("[INFO] Updating cluster-operator image tag in ConfigMap", end=" ", flush=True)
        cm["data"]["values.yaml"] = cm["data"]["values.yaml"].replace(
            f"tag: {old_version}", f"tag: {new_co_version}"
        )
        apply_configmap(cm)
        print("OK")

        print(f"[INFO] Patching KeosCluster helm_repository.url to {helm_repo_url}", end=" ", flush=True)
        run_command(
            f"{kubectl} patch keoscluster {kc_name} -n {kc_ns} "
            f"--type=json -p '[{{\"op\":\"replace\",\"path\":\"/spec/helm_repository/url\",\"value\":\"{helm_repo_url}\"}}]'"
        )
        print("OK")

        print("[INFO] Patching HelmRepository keos url", end=" ", flush=True)
        run_command(
            f"{kubectl} patch helmrepository keos -n kube-system "
            f"--type=merge -p '{{\"spec\":{{\"url\":\"{helm_repo_url}\"}}}}'"
        )
        print("OK")

        print("[INFO] Patching HelmRelease cluster-operator chart version", end=" ", flush=True)
        run_command(
            f"{kubectl} patch helmrelease cluster-operator -n kube-system "
            f"--type=merge -p '{{\"spec\":{{\"chart\":{{\"spec\":{{\"version\":\"{new_co_version}\"}}}}}}}}'"
        )
        print("OK")

        print("[INFO] Forcing HelmRelease reconciliation", end=" ", flush=True)
        ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        run_command(
            f"{kubectl} annotate helmrelease cluster-operator -n kube-system "
            f"reconcile.fluxcd.io/requestedAt={ts} --overwrite"
        )
        print("OK")

        wait_helmrelease_ready("cluster-operator", "kube-system")
        wait_keoscluster_provisioned()

        print(f"[INFO] Patching KeosCluster {kc_name} ecr_pull_through_cache_enabled=true", end=" ", flush=True)
        try:
            run_command(
                f"{kubectl} patch keoscluster {kc_name} -n {kc_ns} "
                f"--type=json -p '[{{\"op\":\"add\","
                f"\"path\":\"/spec/docker_registries/0/ecr_pull_through_cache_enabled\","
                f"\"value\":true}}]'"
            )
        except Exception:
            run_command(
                f"{kubectl} patch keoscluster {kc_name} -n {kc_ns} "
                f"--type=json -p '[{{\"op\":\"replace\","
                f"\"path\":\"/spec/docker_registries/0/ecr_pull_through_cache_enabled\","
                f"\"value\":true}}]'"
            )
        print("OK")

        out, _ = run_command(
            f"{kubectl} get keoscluster {kc_name} -n {kc_ns} "
            f"-o jsonpath='{{.spec.docker_registries[0].ecr_pull_through_cache_enabled}}'",
            allow_errors=True
        )
        if out.strip() != "true":
            print(f"[WARN] ecr_pull_through_cache_enabled={out.strip()!r} — CRD may not have the field yet")
        else:
            print("[INFO] Verified ecr_pull_through_cache_enabled=true in KeosCluster")

        print("\n[OK] cluster-operator upgrade and ECR pull-through flag enabled.")

    except Exception as e:
        print_manual_recovery_instructions(backup_dir, e)
        raise


# ── Entry point ───────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="ECR pull-through cache migration.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("-p", "--vault-password", required=True,
                        help="Vault password for decrypting secrets.yml")
    parser.add_argument("-s", "--secrets", default="secrets.yml",
                        help="Vault-encrypted secrets file")
    parser.add_argument("-k", "--kubeconfig", default="~/.kube/config",
                        help="Kubeconfig file path (or set $KUBECONFIG)")
    parser.add_argument("--cluster-operator",
                        help="Target cluster-operator version (e.g. 0.5.3). Required unless --restore is used.")
    parser.add_argument("--helm-registry",
                        help="Override Helm registry URL for pulling the cluster-operator chart "
                             "(e.g. oci://963353511234.dkr.ecr.eu-west-1.amazonaws.com/helm-devel). "
                             "Defaults to the helm_repository.url in the KeosCluster spec.")
    parser.add_argument("--restore", nargs="?", const="", default=None, metavar="BACKUP_DIR",
                        help="Restore a previous backup instead of upgrading. Without a path, "
                             "restores the most recent backup under backup/ecr_pull_through/.")
    args = parser.parse_args()
    if args.restore is None and not args.cluster_operator:
        parser.error("--cluster-operator is required unless --restore is used")
    return args


if __name__ == '__main__':
    args = parse_args()

    kubeconfig = os.environ.get("KUBECONFIG") or os.path.expanduser(args.kubeconfig)
    if not os.path.exists(kubeconfig):
        print(f"[ERROR] Kubeconfig not found: {kubeconfig}")
        sys.exit(1)

    kubectl = f"kubectl --kubeconfig {kubeconfig}"
    helm = f"helm --kubeconfig {kubeconfig}"

    print("[INFO] Reading secrets file", end=" ", flush=True)
    if not os.path.exists(args.secrets):
        print(f"\n[ERROR] Secrets file not found: {args.secrets}")
        sys.exit(1)
    try:
        vault = Vault(args.vault_password)
        vault_secrets_data = vault.load(open(args.secrets).read())
    except Exception as e:
        print(f"\n[ERROR] Failed to decrypt secrets: {e}")
        sys.exit(1)
    print("OK")

    if 'aws' not in vault_secrets_data.get('secrets', {}):
        print("[ERROR] No AWS credentials in secrets file. ECR pull-through is only supported for AWS/EKS.")
        sys.exit(1)

    configure_aws_credentials(vault_secrets_data)
    validate_kubectl_access(vault_secrets_data['secrets']['aws']['credentials']['region'])

    try:
        if args.restore is not None:
            restore(args.restore or None)
        else:
            run(args.cluster_operator, helm_registry_override=args.helm_registry)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)
