#!/usr/bin/env python3
# -*- coding: utf-8 -*-

##############################################################
# Author: Stratio Clouds <clouds-integration@stratio.com>    #
# Purpose: ECR pull-through cache migration                  #
#   prepare - Upgrade cluster-operator + enable pull-through #
#   migrate - Migrate cloud-provisioner components           #
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


def migrate_workload(kind, name, namespace, prefix, ecr_url):
    """Migrate all containers and initContainers of a workload to pull-through prefix."""
    out, _ = run_command(
        f"{kubectl} get {kind} {name} -n {namespace} -o json",
        allow_errors=True
    )
    if not out.strip():
        print(f"  [{namespace}/{name}] NOT FOUND — skip")
        return
    spec = json.loads(out)
    pod_spec = spec.get("spec", {}).get("template", {}).get("spec", {})
    all_containers = (
        [("containers", c, i) for i, c in enumerate(pod_spec.get("containers") or [])] +
        [("initContainers", c, i) for i, c in enumerate(pod_spec.get("initContainers") or [])]
    )
    patches = []
    for field, container, idx in all_containers:
        img = container.get("image", "")
        if not img.startswith(ecr_url) or img.startswith(f"{ecr_url}/{prefix}/"):
            continue
        new_img = f"{ecr_url}/{prefix}/{img[len(ecr_url) + 1:]}"
        patches.append({"op": "replace", "path": f"/spec/template/spec/{field}/{idx}/image", "value": new_img})
        print(f"    {img} → {new_img}")
    if not patches:
        print(f"  [{namespace}/{name}] already pull-through — skip")
        return
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tf:
        tf.write(json.dumps(patches))
        tf_path = tf.name
    try:
        run_command(f"{kubectl} patch {kind} {name} -n {namespace} --type=json --patch-file={tf_path}")
    finally:
        os.unlink(tf_path)
    run_command(f"{kubectl} rollout status {kind}/{name} -n {namespace} --timeout=180s")


def set_pull_through(deploy, namespace, prefix, ecr_url):
    out, _ = run_command(
        f"{kubectl} get deployment {deploy} -n {namespace} "
        f"-o jsonpath='{{.spec.template.spec.containers[0].image}}'",
        allow_errors=True
    )
    img = out.strip()
    if not img:
        print(f"  [{namespace}/{deploy}] NOT FOUND — skip")
        return
    if img.startswith(f"{ecr_url}/{prefix}/"):
        print(f"  [{namespace}/{deploy}] already pull-through — skip")
        return
    new_img = f"{ecr_url}/{prefix}/{img[len(ecr_url) + 1:]}"
    run_command(f"{kubectl} set image deployment/{deploy} -n {namespace} '*={new_img}'")
    run_command(f"{kubectl} rollout status deployment/{deploy} -n {namespace} --timeout=180s")
    print(f"  [{namespace}/{deploy}] {img} → {new_img}")


# ── Main flow ─────────────────────────────────────────────────────────────────

def run(new_co_version, helm_registry_override=None):
    keos_cluster = get_keos_cluster()
    ecr_url = get_keos_registry_url(keos_cluster)
    helm_repo_url_current = get_helm_repository_url(keos_cluster)
    answer = input(f"The current Helm registry is: {helm_repo_url_current}. Press ENTER to use it or specify a different one: ")
    helm_repo_url = helm_registry_override or (answer.strip() if answer.strip() else helm_repo_url_current)
    kc_name = keos_cluster["metadata"]["name"]
    kc_ns = keos_cluster["metadata"]["namespace"]

    print(f"[INFO] Cluster: {kc_name} / ECR: {ecr_url}")
    print(f"[INFO] Helm registry: {helm_repo_url}")

    # ── Phase 1: upgrade cluster-operator + enable pull-through ──────────────

    print("\n--- Phase 1: cluster-operator upgrade + ecr_pull_through_cache_enabled ---\n")

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
    cm_out, _ = run_command(
        f"{kubectl} get configmap 00-cluster-operator-helm-chart-default-values -n kube-system -o json"
    )
    cm = json.loads(cm_out)
    cm["data"]["values.yaml"] = cm["data"]["values.yaml"].replace(
        f"tag: {old_version}", f"tag: {new_co_version}"
    )
    apply_configmap(cm)
    print("OK")

    if helm_repo_url != helm_repo_url_current:
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

    # ── Phase 2: migrate cloud-provisioner components ─────────────────────────

    print("\n--- Phase 2: migrate cloud-provisioner components to ECR pull-through ---\n")

    print("[INFO] CAPI controllers (k8s registry):")
    set_pull_through("capi-controller-manager",                       "capi-system",                       "k8s", ecr_url)
    set_pull_through("capi-kubeadm-bootstrap-controller-manager",     "capi-kubeadm-bootstrap-system",     "k8s", ecr_url)
    set_pull_through("capi-kubeadm-control-plane-controller-manager", "capi-kubeadm-control-plane-system", "k8s", ecr_url)

    print("[INFO] CAPA controller (k8s registry):")
    set_pull_through("capa-controller-manager", "capa-system", "k8s", ecr_url)

    print("[INFO] cert-manager (quay registry):")
    for deploy in ["cert-manager", "cert-manager-cainjector", "cert-manager-webhook"]:
        set_pull_through(deploy, "cert-manager", "quay", ecr_url)

    print("[INFO] cluster-autoscaler (k8s registry):")
    set_pull_through("cluster-autoscaler-clusterapi-cluster-autoscaler", "kube-system", "k8s", ecr_url)

    print("[INFO] aws-load-balancer-controller (ecrpublic registry):")
    set_pull_through("aws-load-balancer-controller", "kube-system", "ecrpublic", ecr_url)

    print("[INFO] kube-rbac-proxy sidecar (quay registry via ConfigMap):", end=" ", flush=True)
    old_rbac = f"{ecr_url}/kubebuilder/kube-rbac-proxy"
    new_rbac = f"{ecr_url}/quay/brancz/kube-rbac-proxy"
    cm_values_out, _ = run_command(
        f"{kubectl} get configmap 00-cluster-operator-helm-chart-default-values "
        f"-n kube-system -o jsonpath='{{.data.values\\.yaml}}'",
        allow_errors=True
    )
    if new_rbac in cm_values_out:
        print("already pull-through — skip")
    elif old_rbac not in cm_values_out:
        print(f"SKIP (pattern not found in ConfigMap)")
    else:
        cm_out, _ = run_command(
            f"{kubectl} get configmap 00-cluster-operator-helm-chart-default-values -n kube-system -o json"
        )
        cm = json.loads(cm_out)
        cm["data"]["values.yaml"] = cm["data"]["values.yaml"].replace(old_rbac, new_rbac)
        apply_configmap(cm)
        ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        run_command(
            f"{kubectl} annotate helmrelease cluster-operator -n kube-system "
            f"reconcile.fluxcd.io/requestedAt={ts} --overwrite"
        )
        run_command(
            f"{kubectl} rollout status deployment keoscluster-controller-manager "
            f"-n kube-system --timeout=180s"
        )
        print("OK")

    print("[INFO] Tigera operator (quay registry):")
    migrate_workload("deployment", "tigera-operator", "tigera-operator", "quay", ecr_url)

    print("[INFO] Calico (quay registry via Installation CR):", end=" ", flush=True)
    out, _ = run_command(f"{kubectl} get installation default -o json", allow_errors=True)
    if not out.strip():
        print("Installation CR not found — skip")
    else:
        inst = json.loads(out)
        current_path = inst.get("spec", {}).get("imagePath", "")
        target_path = "quay/calico"
        if current_path == target_path:
            print("already pull-through — skip")
        else:
            run_command(
                f"{kubectl} patch installation default --type=merge "
                f"-p '{{\"spec\":{{\"imagePath\":\"{target_path}\"}}}}'"
            )
            print(f"OK ({current_path!r} → {target_path!r})")
            # Wait for tigera-operator to reconcile and update the DaemonSet template
            # before calling rollout status (which would return immediately otherwise).
            print("[INFO] Waiting for tigera-operator to update calico-node template...", flush=True)
            expected_prefix = f"{ecr_url}/quay/"
            deadline = time.time() + 120
            while time.time() < deadline:
                ds_img, _ = run_command(
                    f"{kubectl} get daemonset calico-node -n calico-system "
                    f"-o jsonpath='{{.spec.template.spec.containers[0].image}}'",
                    allow_errors=True
                )
                if ds_img.strip().startswith(expected_prefix):
                    break
                time.sleep(5)
            else:
                raise RuntimeError("Timeout: tigera-operator did not update calico-node template")
            run_command(f"{kubectl} rollout status daemonset/calico-node -n calico-system --timeout=240s")
            run_command(f"{kubectl} rollout status deployment/calico-typha -n calico-system --timeout=180s", allow_errors=True)
            run_command(f"{kubectl} rollout status deployment/calico-kube-controllers -n calico-system --timeout=180s")
            print("OK")

    print("[INFO] Flux (ghcr registry):")
    migrate_workload("deployment", "helm-controller",   "kube-system", "ghcr", ecr_url)
    migrate_workload("deployment", "source-controller", "kube-system", "ghcr", ecr_url)

    print("\n[OK] Migration completed.")


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
    parser.add_argument("--cluster-operator", required=True,
                        help="Target cluster-operator version (e.g. 0.5.3)")
    parser.add_argument("--helm-registry",
                        help="Override Helm registry URL for pulling the cluster-operator chart "
                             "(e.g. oci://963353511234.dkr.ecr.eu-west-1.amazonaws.com/helm-devel). "
                             "Defaults to the helm_repository.url in the KeosCluster spec.")
    return parser.parse_args()


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

    try:
        run(args.cluster_operator, helm_registry_override=args.helm_registry)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)
