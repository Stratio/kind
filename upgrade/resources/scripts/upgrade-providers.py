#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Stratio Clouds <clouds-integration@stratio.com> — one-time migration of the Cluster API providers to v1beta2 (EKS, GKE, Azure VMs).

__version__ = "0.9.4"

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

import yaml

sys.stdout.reconfigure(line_buffering=True)

# Phase 1 (kubeadm providers still below v1.10): stay on the v1beta1 contract.
CLUSTERCTL_V1BETA1 = "v1.10.10"
CAPI_KUBEADM_V1BETA1 = "v1.10.10"
# Phase 2: everything to the v1beta2 contract. v1.13 is the n-3 limit from v1.10.
CLUSTERCTL_V1BETA2 = "v1.13.6"
CAPI = "v1.13.6"
CAPA = "v2.13.0"
# PLT-4891 test build of the fork rebased on upstream v1.13.1; becomes 1.13.1-0.5.0 once released.
CAPG = "1.13.1-0.5.0-PLT-4891.3"
# Last CAPZ built against CAPI v1.13 (go.mod @ v1.26.1); moved in one step from 0.9's v1.21.3.
CAPZ = "v1.26.1"
MIN_CORE_FOR_PHASE2 = (1, 10)
# upgrade-provisioner.py 0.9.x leaves cluster-operator on this line; anything else means it did not run.
SOURCE_OPERATOR_LINE = "0.7."

CAPI_REPO = os.environ.get("CAPI_REPO", "/root/.cluster-api/local-repository")

# Per infra provider. CAPG keeps the v1beta1 contract (accepted by a v1beta2 core until v1beta1 EOL), but the 1.6.1 fork
# cannot read a v1beta2 core, so it moves to the rebased fork.
INFRA_PROVIDERS = {
    "aws": {"name": "aws", "ns": "capa-system", "deploy": "capa-controller-manager",
            "secret": "capa-manager-bootstrap-credentials", "key": "credentials", "cred_env": "AWS_B64ENCODED_CREDENTIALS",
            "env": {"CAPA_EKS_IAM": "true", "EXP_MACHINE_POOL": "true", "CAPA_EKS_ADD_ROLES": "true"},
            "target": CAPA, "repo": "infrastructure-aws", "image": "cluster-api-aws"},
    "gcp": {"name": "gcp", "ns": "capg-system", "deploy": "capg-controller-manager",
            "secret": "capg-manager-bootstrap-credentials", "key": "credentials.json", "cred_env": "GCP_B64ENCODED_CREDENTIALS",
            "env": {"EXP_MACHINE_POOL": "true", "EXP_CAPG_GKE": "true"},
            "target": CAPG, "repo": "infrastructure-gcp", "image": "stratio"},
    "azure": {"name": "azure", "ns": "capz-system", "deploy": "capz-controller-manager",
              "secret": "capz-manager-bootstrap-credentials", "key": "subscription-id", "cred_env": "AZURE_SUBSCRIPTION_ID_B64",
              "env": {}, "target": CAPZ, "repo": "infrastructure-azure", "image": "cluster-api-azure"},
}
KUBEADM_DEPLOYMENTS = [
    ("capi-kubeadm-bootstrap-system", "capi-kubeadm-bootstrap-controller-manager"),
    ("capi-kubeadm-control-plane-system", "capi-kubeadm-control-plane-controller-manager"),
]
# Set in main once the provider is known: capi, the infra controller, then the kubeadm pair.
PROVIDER_DEPLOYMENTS = []
# clusterctl deletes and re-creates these; ask per verb, `can-i '*' '*'` answered yes for an identity denied `delete rolebindings`.
RBAC_VERBS = ("create", "delete", "patch")
RBAC_NAMESPACED = ("roles", "rolebindings", "deployments", "services", "serviceaccounts")
RBAC_CLUSTER = ("clusterroles", "clusterrolebindings", "customresourcedefinitions",
                "mutatingwebhookconfigurations", "validatingwebhookconfigurations")
# Managed control planes: Skind skips allow-all-egress in the kubeadm namespaces (createworker.go:812-831), and Calico's
# default tier denies, so kubeadm controllers started by clusterctl cannot reach the apiserver and --wait-providers times out.
EGRESS_POLICY = "allow-all-egress"
EGRESS_POLICY_YAML = ("apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\nmetadata:\n  name: allow-all-egress\n"
                      "spec:\n  egress:\n  - {}\n  podSelector: {}\n  policyTypes:\n  - Egress\n")
CA_DEPLOYMENT = "cluster-autoscaler-clusterapi-cluster-autoscaler"
OPERATOR_DEPLOYMENT = "keoscluster-controller-manager"
OPERATOR_VALUES_CM = "00-cluster-operator-helm-chart-default-values"
# Read through the same API version before and after, so any field the migration drops shows up as a diff.
CORE_CONTENT_KINDS = [
    "clusters.v1beta1.cluster.x-k8s.io", "machinepools.v1beta1.cluster.x-k8s.io", "machinedeployments.v1beta1.cluster.x-k8s.io",
    "machinesets.v1beta1.cluster.x-k8s.io", "machines.v1beta1.cluster.x-k8s.io", "machinehealthchecks.v1beta1.cluster.x-k8s.io",
]
INFRA_CONTENT_KINDS = {
    "aws": ["awsmanagedcontrolplanes.v1beta2.controlplane.cluster.x-k8s.io", "awsmanagedclusters.v1beta2.infrastructure.cluster.x-k8s.io",
            "awsmanagedmachinepools.v1beta2.infrastructure.cluster.x-k8s.io", "awsmachinetemplates.v1beta2.infrastructure.cluster.x-k8s.io",
            "awsmachines.v1beta2.infrastructure.cluster.x-k8s.io", "eksconfigtemplates.v1beta2.bootstrap.cluster.x-k8s.io",
            "eksconfigs.v1beta2.bootstrap.cluster.x-k8s.io"],
    "gcp": ["gcpmanagedclusters.v1beta1.infrastructure.cluster.x-k8s.io", "gcpmanagedcontrolplanes.v1beta1.infrastructure.cluster.x-k8s.io",
            "gcpmanagedmachinepools.v1beta1.infrastructure.cluster.x-k8s.io"],
    "azure": ["azureclusters.v1beta1.infrastructure.cluster.x-k8s.io", "azuremachinetemplates.v1beta1.infrastructure.cluster.x-k8s.io",
              "azuremachines.v1beta1.infrastructure.cluster.x-k8s.io", "azureclusteridentities.v1beta1.infrastructure.cluster.x-k8s.io",
              "kubeadmcontrolplanes.v1beta1.controlplane.cluster.x-k8s.io", "kubeadmconfigtemplates.v1beta1.bootstrap.cluster.x-k8s.io"],
}
# CAPZ bundles ASO; its docs require this label before clusterctl upgrade (aso.md:26-38 @ v1.26.1).
ASO_CRD_SELECTOR = "app.kubernetes.io/name=azure-service-operator"
VOLATILE_ANNOTATIONS = ("kubectl.kubernetes.io/last-applied-configuration", "cluster.x-k8s.io/conversion-data")
KEOSCLUSTER_WEBHOOKS = [
    ("MutatingWebhookConfiguration", "keoscluster-mutating-webhook-configuration"),
    ("ValidatingWebhookConfiguration", "keoscluster-validating-webhook-configuration"),
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Migrates Cluster API core and kubeadm providers to the v1beta2 API (CAPI " + CAPI + "; EKS: CAPA to " + CAPA +
                    "; GKE: CAPG to " + CAPG + "; Azure: CAPZ to " + CAPZ + "), then the v1beta2 cluster-operator, without changing the "
                    "k8s version. Run once on a "
                    "0.9 cluster (a 0.7.5 cluster runs upgrade-provisioner.py of 0.9 first).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("-y", "--yes", action="store_true", help="Do not wait for confirmation before the first mutating step")
    parser.add_argument("-k", "--kubeconfig", default=os.environ.get("KUBECONFIG", "~/.kube/config"), help="Kubeconfig of the management cluster")
    parser.add_argument("--cluster-operator", required=True, help="Target cluster-operator version (chart and image); must be a v1beta2-aware build")
    parser.add_argument("--clusterctl", default="clusterctl-" + CLUSTERCTL_V1BETA2, help="clusterctl " + CLUSTERCTL_V1BETA2 + " binary (phase 2)")
    parser.add_argument("--clusterctl-v1beta1", default="clusterctl", help="clusterctl " + CLUSTERCTL_V1BETA1 + " binary (backup and phase 1)")
    parser.add_argument("--backup-dir", default="./backup/upgrade-providers", help="Base directory for backups")
    parser.add_argument("--backup-owner", default=os.environ.get("BACKUP_OWNER"), help="uid:gid to own the backup when finished (the container writes as root)")
    parser.add_argument("--storage-migration-timeout", type=int, default=3, help="Minutes without any CRD settling before the script migrates the leftovers itself")
    parser.add_argument("--resume-from", help="Backup dir of a previous run that failed mid-clusterctl: tolerate missing/stopped providers and compare content against its snapshot")
    parser.add_argument("--dry-run", action="store_true", help="Run checks, backups and 'clusterctl upgrade plan' for real; only print mutating steps")
    return vars(parser.parse_args())


def run(command, mutating=False, allow_errors=False, env=None, retries=2, retry_delay=3):
    '''Run a shell command. Mutating commands are printed, not executed, in dry-run.'''
    if mutating and config["dry_run"]:
        print(f"\n[DRY-RUN] would run: {command}", flush=True)
        return ""
    full_env = dict(os.environ, **(env or {}))
    for attempt in range(retries + 1):
        result = subprocess.run(command, shell=True, capture_output=True, text=True, env=full_env)
        if result.returncode == 0 or allow_errors:
            return result.stdout
        if attempt < retries:
            time.sleep(retry_delay)
    raise Exception(f"'{command}' failed: {result.stderr.strip() or result.stdout.strip()}")


def kjson(args):
    return json.loads(run(f"{kubectl} {args} -o json"))


def parse_version(version):
    nums = re.findall(r"\d+", version or "")
    return tuple(int(n) for n in nums[:3]) if nums else (0, 0, 0)


def write_file(path, content):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    os.chmod(path, 0o600)


def info(message, end=" "):
    print(f"[INFO] {message}", end=end, flush=True)


def hand_over_backup(path):
    '''The backup holds credentials: keep 700/600 but give it to the invoking user.'''
    if config["backup_owner"] and os.path.isdir(path):
        subprocess.run(["chown", "-R", config["backup_owner"], path], check=False)


# ---------------------------------------------------------------- checks

def check_binaries():
    for label, binary, expected in (("phase 2", config["clusterctl"], CLUSTERCTL_V1BETA2),
                                    ("backup/phase 1", config["clusterctl_v1beta1"], CLUSTERCTL_V1BETA1)):
        info(f"Checking clusterctl for {label} ({binary}):")
        output = run(f"{binary} version -o short", allow_errors=True).strip()
        if parse_version(output)[:2] != parse_version(expected)[:2]:
            print("FAILED")
            sys.exit(f"[ERROR] {binary} reports '{output}', expected {expected}")
        print(f"OK ({output})")
    for label, path in (("CAPI " + CAPI, f"{CAPI_REPO}/cluster-api/{CAPI}/core-components.yaml"),
                        ("kubeadm bootstrap " + CAPI, f"{CAPI_REPO}/bootstrap-kubeadm/{CAPI}/bootstrap-components.yaml"),
                        ("kubeadm control plane " + CAPI, f"{CAPI_REPO}/control-plane-kubeadm/{CAPI}/control-plane-components.yaml")):
        info(f"Checking local repository manifests for {label}:")
        if not os.path.isfile(path):
            print("FAILED")
            sys.exit(f"[ERROR] missing {path}")
        print("OK")


def check_infra_manifests(infra, version):
    '''clusterctl reads the infra provider's metadata.yaml at the version it plans for, even when it is not upgraded.'''
    for name in ("metadata.yaml", "infrastructure-components.yaml"):
        path = f"{CAPI_REPO}/{infra['repo']}/{version}/{name}"
        info(f"Checking local repository {infra['repo']} {version} {name}:")
        if not os.path.isfile(path):
            print("FAILED")
            sys.exit(f"[ERROR] missing {path}")
        print("OK")


def get_cluster():
    keos_cluster = kjson("get keoscluster -A")["items"][0]
    cluster_config = kjson("get clusterconfig -A")["items"][0]
    return keos_cluster, cluster_config


def get_provider_versions():
    versions = {}
    for p in kjson("get providers -A")["items"]:
        versions[(p["type"], p["providerName"])] = p["version"]
    return versions


def preflight(keos_cluster, cluster_name):
    info("Running pre-flight checks:", end="\n")
    problems = []
    status = keos_cluster.get("status", {})
    if not (status.get("ready") and status.get("phase") == "Provisioned"):
        problems.append(f"KeosCluster is {status.get('ready')}/{status.get('phase')}, expected true/Provisioned")
    for ns, deploy in PROVIDER_DEPLOYMENTS[:2]:
        if config["resume_from"]:
            break
        d = kjson(f"-n {ns} get deploy {deploy}")
        if (d["status"].get("readyReplicas") or 0) < 1:
            problems.append(f"{ns}/{deploy} has no ready replica")
    ns = f"cluster-{cluster_name}"
    for kind in ("machinepools", "machinedeployments"):
        for o in kjson(f"-n {ns} get {kind}.cluster.x-k8s.io")["items"]:
            if o.get("status", {}).get("phase") != "Running":
                problems.append(f"{kind} {o['metadata']['name']} phase is {o.get('status', {}).get('phase')}")
    for n in kjson("get nodes")["items"]:
        ready = [c["status"] for c in n["status"]["conditions"] if c["type"] == "Ready"]
        if ready != ["True"] or n["spec"].get("unschedulable"):
            problems.append(f"node {n['metadata']['name']} is not Ready/schedulable")
    if run(f"{kubectl} get all -n cert-manager -l clusterctl.cluster.x-k8s.io/core=cert-manager --no-headers", allow_errors=True).strip():
        problems.append("cert-manager carries the clusterctl label: clusterctl would try to upgrade it")
    if not infra_credentials():
        problems.append(f"{infra['ns']}/{infra['secret']} ({infra['key']}) is missing or empty")
    operator = run(f"{kubectl} -n kube-system get helmrelease cluster-operator -o jsonpath='{{.spec.chart.spec.version}}'", allow_errors=True).strip()
    if not (operator.startswith(SOURCE_OPERATOR_LINE) or operator == config["cluster_operator"]):
        problems.append(f"cluster-operator chart is '{operator}': expected the {SOURCE_OPERATOR_LINE}x line upgrade-provisioner.py installs (or the target)")
    scopes = [(r, ns) for ns in sorted({ns for ns, _ in PROVIDER_DEPLOYMENTS}) for r in RBAC_NAMESPACED] + [(r, None) for r in RBAC_CLUSTER]
    for verb in RBAC_VERBS:
        for resource, ns in scopes:
            if run(f"{kubectl} auth can-i {verb} {resource}" + (f" -n {ns}" if ns else ""), allow_errors=True).strip() != "yes":
                problems.append(f"the kubeconfig identity cannot {verb} {resource}" + (f" in {ns}" if ns else ""))
    for p in problems:
        print(f"  [FAIL] {p}")
    if problems:
        sys.exit("[ERROR] Pre-flight checks failed, nothing was changed")
    print("  OK")


# ---------------------------------------------------------------- backup

def snapshot(cluster_name):
    '''Versions, storage and object names — compared before and after.'''
    snap = {"providers": {f"{k[0]}/{k[1]}": v for k, v in get_provider_versions().items()}, "crds": {}, "objects": {}}
    for crd in kjson("get crd")["items"]:
        if not crd["spec"]["group"].endswith("cluster.x-k8s.io"):
            continue
        snap["crds"][crd["metadata"]["name"]] = {
            "served": [v["name"] for v in crd["spec"]["versions"] if v.get("served")],
            "storage": next(v["name"] for v in crd["spec"]["versions"] if v.get("storage")),
            "storedVersions": crd.get("status", {}).get("storedVersions", []),
        }
    for kind in ("clusters", "machinedeployments", "machinesets", "machines", "machinepools"):
        items = kjson(f"-n cluster-{cluster_name} get {kind}.cluster.x-k8s.io")["items"]
        snap["objects"][kind] = sorted(o["metadata"]["name"] for o in items)
    snap["deployments"] = {}
    for ns, deploy in PROVIDER_DEPLOYMENTS:
        raw = run(f"{kubectl} -n {ns} get deploy {deploy} --ignore-not-found -o json", allow_errors=True).strip()
        if not raw:
            snap["deployments"][f"{ns}/{deploy}"] = {"image": None, "replicas": None, "args": []}
            continue
        d = json.loads(raw)
        c = d["spec"]["template"]["spec"]["containers"][0]
        snap["deployments"][f"{ns}/{deploy}"] = {"image": c["image"], "replicas": d["spec"].get("replicas"), "args": c.get("args", [])}
    return snap


def content_snapshot(cluster_name):
    '''labels, annotations, owners (kind/name) and spec of every CAPI/infra object, plus node labels and taints.'''
    content = {}
    for kind in CORE_CONTENT_KINDS + INFRA_CONTENT_KINDS[infra["name"]]:
        for o in kjson(f"-n cluster-{cluster_name} get {kind}")["items"]:
            md = o["metadata"]
            annotations = {k: v for k, v in md.get("annotations", {}).items() if k not in VOLATILE_ANNOTATIONS}
            # apiVersion left out on purpose: the owner references move from v1beta1 to v1beta2.
            owners = [{"kind": r["kind"], "name": r["name"]} for r in md.get("ownerReferences", [])]
            content[f"{kind.split('.')[0]}/{md['name']}"] = {"labels": md.get("labels", {}), "annotations": annotations,
                                                            "owners": owners, "spec": o.get("spec", {})}
    for n in kjson("get nodes")["items"]:
        content[f"node/{n['metadata']['name']}"] = {"labels": n["metadata"].get("labels", {}), "taints": n["spec"].get("taints", [])}
    return content


def flatten(value, prefix=""):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    if isinstance(value, list):
        out = {}
        for i, v in enumerate(value):
            out.update(flatten(v, f"{prefix}[{i}]"))
        return out or {prefix: []}
    return {prefix: value}


def diff_content(before, after):
    '''Removed or changed fields are possible losses; added fields are new defaults.'''
    lost, added = [], []
    for key in sorted(set(before) | set(after)):
        if key not in after:
            lost.append(f"{key}: object missing after the migration")
            continue
        if key not in before:
            added.append(f"{key}: new object")
            continue
        b, a = flatten(before[key]), flatten(after[key])
        for path in sorted(set(b) | set(a)):
            if path not in a:
                lost.append(f"{key} {path}: removed (was {b[path]!r})")
            elif path not in b:
                added.append(f"{key} {path}: added ({a[path]!r})")
            elif a[path] != b[path]:
                lost.append(f"{key} {path}: changed {b[path]!r} -> {a[path]!r}")
    return lost, added


def backup(backup_dir, cluster_name, core_version):
    info("Backing up into " + backup_dir, end="\n")
    os.makedirs(backup_dir, mode=0o700, exist_ok=True)

    # clusterctl only moves management clusters on its own contract: v1.10 for v1beta1, v1.13 once core is v1beta2.
    mover = config["clusterctl"] if parse_version(core_version)[:2] >= (1, 11) else config["clusterctl_v1beta1"]
    info(f"Backing up CAPI objects ({mover} move --to-directory):")
    move_dir = os.path.join(backup_dir, "capi-move")
    os.makedirs(move_dir, mode=0o700, exist_ok=True)
    try:
        run(f"{mover} move --kubeconfig {kubeconfig} -n cluster-{cluster_name} --to-directory {move_dir}", retries=0)
        print("OK")
    except Exception as e:
        if not config["resume_from"]:
            raise
        print(f"WARN ({str(e)[-200:]}) — the previous run's capi-move backup stays the reference")

    info("Backing up provider namespaces (deploy, svc, sa, secret, cm, role, rolebinding):")
    for ns in sorted({ns for ns, _ in PROVIDER_DEPLOYMENTS}):
        out = run(f"{kubectl} get deploy,svc,sa,secret,cm,role,rolebinding -n {ns} --show-managed-fields -o yaml")
        write_file(os.path.join(backup_dir, "namespaces", f"{ns}.yaml"), out)
    print("OK")

    info("Backing up cluster-scoped objects (RBAC, webhooks, CRDs, providers):")
    write_file(os.path.join(backup_dir, "cluster", "clusterrbac.yaml"),
               run(f"{kubectl} get clusterrole,clusterrolebinding -l clusterctl.cluster.x-k8s.io -o yaml"))
    write_file(os.path.join(backup_dir, "cluster", "webhooks.yaml"),
               run(f"{kubectl} get validatingwebhookconfiguration,mutatingwebhookconfiguration -o yaml"))
    crds = [c["metadata"]["name"] for c in kjson("get crd")["items"] if c["spec"]["group"].endswith("cluster.x-k8s.io")]
    write_file(os.path.join(backup_dir, "cluster", "crds-capi.yaml"), run(f"{kubectl} get crd {' '.join(crds)} -o yaml"))
    write_file(os.path.join(backup_dir, "cluster", "providers.yaml"), run(f"{kubectl} get providers -A -o yaml"))
    print("OK")

    info("Backing up KeosCluster, ClusterConfig, cluster-operator and cluster-autoscaler release state:")
    write_file(os.path.join(backup_dir, "keos", "keoscluster-clusterconfig.yaml"), run(f"{kubectl} get keoscluster,clusterconfig -A -o yaml"))
    for release in ("cluster-operator", "cluster-autoscaler"):
        write_file(os.path.join(backup_dir, "keos", f"helmrelease-{release}.yaml"),
                   run(f"{kubectl} -n kube-system get helmrelease {release} -o yaml", allow_errors=True))
        write_file(os.path.join(backup_dir, "keos", f"configmaps-{release}.yaml"),
                   run(f"{kubectl} -n kube-system get cm 00-{release}-helm-chart-default-values 02-{release}-helm-chart-override-values -o yaml", allow_errors=True))
        write_file(os.path.join(backup_dir, "keos", f"helm-values-{release}.yaml"),
                   run(f"{helm} -n kube-system get values {release} -o yaml", allow_errors=True))
        write_file(os.path.join(backup_dir, "keos", f"helm-manifest-{release}.yaml"),
                   run(f"{helm} -n kube-system get manifest {release}", allow_errors=True))
    print("OK")

    info("Recording version snapshot and object content (before):")
    snap = snapshot(cluster_name)
    snap["content"] = content_snapshot(cluster_name)
    write_file(os.path.join(backup_dir, "snapshot-before.json"), json.dumps(snap, indent=2))
    print(f"OK ({len(snap['content'])} objects and nodes)")
    return snap


# ---------------------------------------------------------------- clusterctl

def get_registry(keos_cluster, cluster_config):
    if not cluster_config["spec"].get("private_registry"):
        return None, False
    registry, pull_through = None, False
    for r in keos_cluster["spec"].get("docker_registries", []):
        if r.get("keos_registry"):
            registry = r["url"]
        pull_through = pull_through or bool(r.get("ecr_pull_through_cache_enabled"))
    return registry, pull_through


def write_clusterctl_config(path, core_version, infra_version, registry, pull_through):
    '''Own config file per phase, so the one upgrade-provisioner.py uses is never touched.'''
    cfg = {"providers": [
        {"name": "cluster-api", "type": "CoreProvider", "url": f"{CAPI_REPO}/cluster-api/{core_version}/core-components.yaml"},
        {"name": "kubeadm", "type": "BootstrapProvider", "url": f"{CAPI_REPO}/bootstrap-kubeadm/{core_version}/bootstrap-components.yaml"},
        {"name": "kubeadm", "type": "ControlPlaneProvider", "url": f"{CAPI_REPO}/control-plane-kubeadm/{core_version}/control-plane-components.yaml"},
        {"name": infra["name"], "type": "InfrastructureProvider",
         "url": f"{CAPI_REPO}/{infra['repo']}/{infra_version}/infrastructure-components.yaml"},
    ]}
    if registry:
        k8s = "k8s/" if pull_through else ""
        quay = "quay/" if pull_through else ""
        cfg["images"] = {
            "cluster-api": {"repository": f"{registry}/{k8s}cluster-api", "tag": core_version},
            "bootstrap-kubeadm": {"repository": f"{registry}/{k8s}cluster-api", "tag": core_version},
            "control-plane-kubeadm": {"repository": f"{registry}/{k8s}cluster-api", "tag": core_version},
            "cert-manager": {"repository": f"{registry}/{quay}jetstack"},
        }
        if infra["name"] == "azure":
            # Per component: a provider-wide tag would also retag the bundled ASO image (upgrade-provisioner.py:1753-1760).
            cfg["images"]["infrastructure-azure/cluster-api-azure-controller"] = {"repository": f"{registry}/{infra['image']}", "tag": infra_version}
            cfg["images"]["infrastructure-azure/azureserviceoperator"] = {"repository": f"{registry}/k8s"}
        elif infra["image"]:
            cfg["images"][infra["repo"]] = {"repository": f"{registry}/{k8s}{infra['image']}", "tag": infra_version}
    write_file(path, yaml.safe_dump(cfg, sort_keys=False))
    return path


# Capsule's namespace webhooks reject clusterctl re-applying capi-system & co. when forceTenantPrefix is on.
CLUSTERCTL_NS_SELECTOR = {"matchExpressions": [{"key": "clusterctl.cluster.x-k8s.io", "operator": "DoesNotExist"}]}
LEGACY_CAPSULE_WEBHOOKS = [("mutatingwebhookconfiguration", "capsule-mutating-webhook-configuration"),
                           ("validatingwebhookconfiguration", "capsule-validating-webhook-configuration")]


def namespace_webhook_indexes(webhooks):
    return [i for i, w in enumerate(webhooks) if any("namespaces" in (r.get("resources") or []) for r in w.get("rules", []))]


def capsule_exclude_clusterctl_namespaces(backup_dir):
    '''Returns the restore plan: [(kind, name, json-patch-restore)], empty if capsule is absent.'''
    info("Excluding clusterctl namespaces from capsule's namespace webhooks:")
    restore = []
    raw = run(f"{kubectl} get capsuleconfiguration default --ignore-not-found -o json", allow_errors=True).strip()
    if raw and json.loads(raw)["spec"].get("admission"):
        cc = json.loads(raw)
        write_file(os.path.join(backup_dir, "keos", "capsuleconfiguration-default.json"), raw)
        targets = [("capsuleconfiguration", "default", f"/spec/admission/{kind}/webhooks", cc["spec"]["admission"].get(kind, {}).get("webhooks", []))
                   for kind in ("mutating", "validating")]
        generated = cc["spec"]["admission"]["mutating"].get("name")
    else:
        targets = []
        for kind, name in LEGACY_CAPSULE_WEBHOOKS:
            raw = run(f"{kubectl} get {kind} {name} --ignore-not-found -o json", allow_errors=True).strip()
            if raw:
                write_file(os.path.join(backup_dir, "keos", f"{name}.json"), raw)
                targets.append((kind, name, "/webhooks", json.loads(raw)["webhooks"]))
        generated = None
    if not targets:
        print("SKIP (capsule not installed)")
        return restore
    for kind, name, path, webhooks in targets:
        patch, back = [], []
        for i in namespace_webhook_indexes(webhooks):
            original = webhooks[i].get("objectSelector")
            patch.append({"op": "add", "path": f"{path}/{i}/objectSelector", "value": CLUSTERCTL_NS_SELECTOR})
            back.append({"op": "add", "path": f"{path}/{i}/objectSelector", "value": original} if original else {"op": "remove", "path": f"{path}/{i}/objectSelector"})
        if patch:
            run(f"{kubectl} patch {kind} {name} --type json -p '{json.dumps(patch)}'", mutating=True)
            restore.append((kind, name, back))
    if generated and not config["dry_run"]:
        deadline = time.time() + 120
        while time.time() < deadline:
            mwc = kjson(f"get mutatingwebhookconfiguration {generated}")
            if all(mwc["webhooks"][i].get("objectSelector") == CLUSTERCTL_NS_SELECTOR for i in namespace_webhook_indexes(mwc["webhooks"])):
                break
            time.sleep(5)
        else:
            print("FAILED")
            raise Exception(f"capsule did not regenerate {generated} with the clusterctl namespace exclusion")
    print(f"OK ({', '.join(f'{k}/{n}' for k, n, _ in restore)})")
    return restore


def capsule_namespace_webhooks():
    '''Names of capsule's namespace webhooks — the ones capsule_exclude_clusterctl_namespaces() takes care of.'''
    names = set()
    for kind in ("mutatingwebhookconfiguration", "validatingwebhookconfiguration"):
        for w in kjson(f"get {kind}")["items"]:
            for h in w.get("webhooks", []):
                if "projectcapsule.dev" in h["name"] or "capsule.clastix.io" in h["name"]:
                    if any("namespaces" in (r.get("resources") or []) for r in h.get("rules", [])):
                        names.add(h["name"])
    return names


def admission_dry_run(expected_denials=()):
    '''Server-side dry-run of what clusterctl re-applies: every admission webhook runs, nothing is persisted.'''
    info("Admission check (server-side dry-run of provider Namespaces and Deployments):")
    denials = []
    for ns in sorted({ns for ns, _ in PROVIDER_DEPLOYMENTS}):
        objects = [("namespace", ns, "")] + [("deploy", d["metadata"]["name"], ns) for d in kjson(f"-n {ns} get deploy")["items"]]
        for kind, name, namespace in objects:
            o = kjson(f"{'-n ' + namespace if namespace else ''} get {kind} {name}")
            for k in ("resourceVersion", "uid", "creationTimestamp", "managedFields", "generation"):
                o["metadata"].pop(k, None)
            o.pop("status", None)
            out = subprocess.run(f"{kubectl} apply --dry-run=server -f -", shell=True, input=json.dumps(o), capture_output=True, text=True)
            if out.returncode != 0:
                hook = re.search(r'admission webhook "([^"]+)" denied', out.stderr)
                if hook and hook.group(1) in expected_denials:
                    continue
                denials.append(f"{kind}/{name}: {out.stderr.strip()[-300:]}")
    if denials:
        print("FAILED")
        for d in denials:
            print(f"  [DENIED] {d}")
        raise Exception("an admission webhook rejects what clusterctl will apply — nothing was upgraded")
    print("OK" + (f" (capsule namespace webhooks handled by the exclusion: {', '.join(sorted(expected_denials))})" if expected_denials else ""))


def capsule_restore(restore):
    if not restore:
        return
    info("Restoring capsule's namespace webhooks:")
    for kind, name, back in restore:
        run(f"{kubectl} patch {kind} {name} --type json -p '{json.dumps(back)}'", mutating=True)
    print("OK")


def infra_credentials():
    '''Base64 value of the infra provider's bootstrap credentials, as stored in its secret.'''
    key = infra["key"].replace(".", "\\.")
    return run(f"{kubectl} -n {infra['ns']} get secret {infra['secret']} -o jsonpath='{{.data.{key}}}'", allow_errors=True).strip()


def clusterctl_env():
    # Same variables upgrade-provisioner.py passes per provider (:3128-3163 @ 0.9.4).
    return dict(infra["env"], **{infra["cred_env"]: infra_credentials(), "CLUSTER_TOPOLOGY": "true",
                                 "CLUSTERCTL_DISABLE_VERSIONCHECK": "true", "GOPROXY": "off"})


def clusterctl_apply(binary, cfg, args, log_file, label):
    info(f"{label}: clusterctl upgrade apply {args}:")
    command = f"{binary} upgrade apply --kubeconfig {kubeconfig} --config {cfg} {args} --wait-providers"
    if config["dry_run"]:
        print(f"\n[DRY-RUN] would run: {command}")
        return
    result = subprocess.run(command, shell=True, capture_output=True, text=True, env=dict(os.environ, **clusterctl_env()))
    write_file(log_file, result.stdout + result.stderr)
    if result.returncode != 0:
        print("FAILED")
        raise Exception(f"{label} failed (exit {result.returncode}), see {log_file}: {result.stderr.strip()[-600:]}")
    print("OK")


def scale(ns, deploy, replicas, wait=True):
    run(f"{kubectl} -n {ns} scale deploy {deploy} --replicas {replicas}", mutating=True)
    if wait and replicas > 0 and not config["dry_run"]:
        run(f"{kubectl} -n {ns} rollout status deploy {deploy} --timeout 180s", retries=0)


def kubeadm_egress_allow():
    '''Temporary egress for the kubeadm namespaces; returns the namespaces where this run created the policy.'''
    info("Allowing egress in the kubeadm namespaces for the upgrade:")
    added = []
    for ns, _ in KUBEADM_DEPLOYMENTS:
        if run(f"{kubectl} -n {ns} get networkpolicy {EGRESS_POLICY} --ignore-not-found -o name", allow_errors=True).strip():
            continue
        run(f"printf '%s' '{EGRESS_POLICY_YAML}' | {kubectl} -n {ns} apply -f -", mutating=True)
        added.append(ns)
    print(f"OK ({', '.join(added) or 'already present'})")
    return added


def kubeadm_egress_remove(added):
    for ns in added:
        run(f"{kubectl} -n {ns} delete networkpolicy {EGRESS_POLICY} --ignore-not-found", mutating=True, allow_errors=True)


def kubeadm_replicas(ns, deploy):
    '''0 on a managed control plane; otherwise what the Deployment had before (Azure: kubeadm runs the control plane).'''
    if managed:
        return 0
    return before["deployments"][f"{ns}/{deploy}"]["replicas"] or 1


def restore_provider_replicas():
    info(f"Restoring provider replicas (capi/{infra['deploy'].split('-')[0]} 2, kubeadm {'0 on a managed control plane' if managed else 'as before'}):")
    for ns, deploy in PROVIDER_DEPLOYMENTS[:2]:
        scale(ns, deploy, 2)
    for ns, deploy in KUBEADM_DEPLOYMENTS:
        replicas = kubeadm_replicas(ns, deploy)
        scale(ns, deploy, replicas, wait=replicas > 0)
    print("OK")


def label_aso_crds():
    info("Labelling ASO CRDs as part of infrastructure-azure (CAPZ pre-upgrade step):")
    run(f"{kubectl} label customresourcedefinitions --selector={ASO_CRD_SELECTOR} cluster.x-k8s.io/provider=infrastructure-azure --overwrite",
        mutating=True)
    print("OK")


def parse_gates(args):
    raw = next((a.split("=", 1)[1] for a in args if a.startswith("--feature-gates=")), "")
    return dict(g.split("=", 1) for g in raw.split(",") if "=" in g)


def report_feature_gates(before):
    '''Every controller, changed or not, so an unchanged CAPA is shown explicitly (report only, never patched).'''
    after = snapshot(cluster_name)["deployments"]
    info("Controller feature gates before -> after (report only, not patched):", end="\n")
    for key, old in before["deployments"].items():
        b, a = parse_gates(old["args"]), parse_gates(after.get(key, {}).get("args", []))
        if b == a:
            print(f"  [SAME] {key}: {','.join(f'{k}={v}' for k, v in sorted(a.items()))}")
            continue
        changes = [f"{k} {b[k]}->{a[k]}" for k in sorted(b.keys() & a.keys()) if b[k] != a[k]]
        changes += [f"{k}={b[k]} removed" for k in sorted(b.keys() - a.keys())]
        changes += [f"{k}={a[k]} new" for k in sorted(a.keys() - b.keys())]
        print(f"  [DIFF] {key}: {'; '.join(changes)}")


def unsettled_crds():
    '''CAPI CRDs with v1beta2 storage whose storedVersions still list an older version.'''
    return [c for c in kjson("get crd")["items"]
            if c["spec"]["group"].endswith("cluster.x-k8s.io")
            and next(v["name"] for v in c["spec"]["versions"] if v.get("storage")) == "v1beta2"
            and c.get("status", {}).get("storedVersions") != ["v1beta2"]]


def migrate_crd(crd):
    '''Same as CAPI's crdmigrator (crd_migrator.go:239-250,380-420): no-op SSA per object, then storedVersions=[v1beta2].'''
    group, plural, kind = crd["spec"]["group"], crd["spec"]["names"]["plural"], crd["spec"]["names"]["kind"]
    objects = kjson(f"get {plural}.v1beta2.{group} -A")["items"]
    for o in objects:
        md = o["metadata"]
        stub = {"apiVersion": f"{group}/v1beta2", "kind": kind,
                "metadata": {k: md[k] for k in ("name", "namespace", "uid", "resourceVersion") if k in md}}
        out = subprocess.run(f"{kubectl} apply --server-side --field-manager=upgrade-providers -f -", shell=True,
                             input=json.dumps(stub), capture_output=True, text=True)
        if out.returncode != 0 and not re.search(r"NotFound|not found|Conflict|the object has been modified", out.stderr):
            raise Exception(f"storage migration of {kind} {md.get('namespace', '')}/{md['name']} failed: {out.stderr.strip()}")
    patch = {"metadata": {"resourceVersion": crd["metadata"]["resourceVersion"]}, "status": {"storedVersions": ["v1beta2"]}}
    run(f"{kubectl} patch crd {crd['metadata']['name']} --subresource=status --type=merge -p '{json.dumps(patch)}'", retries=0)
    return len(objects)


def wait_storage_migration():
    '''Give each controller's crdmigrator a chance, then migrate whatever it left (its controller is at 0 replicas or gated off).'''
    info(f"Storage migration of CAPI CRDs to v1beta2 (controllers first, then leftovers; no-progress window {config['storage_migration_timeout']}m):", end="\n")
    if config["dry_run"]:
        for c in unsettled_crds():
            print(f"  [DRY-RUN] {c['metadata']['name']} storedVersions={c.get('status', {}).get('storedVersions')} — would be migrated if still pending after the controllers")
        return
    stall = config["storage_migration_timeout"] * 60
    deadline, last = time.time() + stall, None
    while True:
        pending = unsettled_crds()
        if not pending:
            print("  OK — every CAPI CRD stores only v1beta2 (migrated by the controllers)")
            return
        if last is None or len(pending) < last:
            deadline, last = time.time() + stall, len(pending)
        if time.time() > deadline:
            break
        time.sleep(15)
    for c in unsettled_crds():
        count = migrate_crd(c)
        print(f"  [MIGRATED] {c['metadata']['name']}: {count} object(s) rewritten, storedVersions -> [v1beta2] (no controller migrated it)")
    left = [c["metadata"]["name"] for c in unsettled_crds()]
    if left:
        raise Exception(f"storedVersions still not [v1beta2] on: {', '.join(left)}")
    print("  OK — every CAPI CRD stores only v1beta2")


# ---------------------------------------------------------------- cluster-operator and cluster-autoscaler

def stop_operator():
    info("Suspending cluster-operator HelmRelease and stopping keoscluster-controller-manager:")
    run(f"{kubectl} -n kube-system patch helmrelease cluster-operator --type merge -p '{{\"spec\":{{\"suspend\":true}}}}'", mutating=True)
    scale("kube-system", OPERATOR_DEPLOYMENT, 0, wait=False)
    print("OK")


def backup_and_delete_keoscluster_webhooks(backup_dir):
    info("Backing up and disabling KeosCluster webhooks:")
    manifest = run(f"{helm} get manifest -n kube-system cluster-operator")
    selected = subprocess.run(["yq", 'select(.kind == "ValidatingWebhookConfiguration" or .kind == "MutatingWebhookConfiguration")'],
                              input=manifest, capture_output=True, text=True)
    if selected.returncode != 0 or not all(f"kind: {k}" in selected.stdout for k, _ in KEOSCLUSTER_WEBHOOKS):
        print("FAILED")
        raise Exception("could not extract both KeosCluster webhooks from the cluster-operator release manifest")
    write_file(os.path.join(backup_dir, "keos", "keoscluster-webhooks.yaml"), selected.stdout)
    for kind, name in KEOSCLUSTER_WEBHOOKS:
        run(f"{kubectl} delete {kind} {name} --ignore-not-found", mutating=True)
    print("OK")


def restore_keoscluster_webhooks(backup_dir):
    info("Restoring KeosCluster webhooks:")
    path = os.path.join(backup_dir, "keos", "keoscluster-webhooks.yaml")
    if not os.path.isfile(path):
        print("SKIP (no backup)")
        return
    missing = [n for k, n in KEOSCLUSTER_WEBHOOKS if not run(f"{kubectl} get {k} {n} --ignore-not-found -o name", allow_errors=True).strip()]
    if missing:
        run(f"{kubectl} create -f {path}", mutating=True, allow_errors=True)
    for kind, name in KEOSCLUSTER_WEBHOOKS:
        run(f"{kubectl} label {kind} {name} app.kubernetes.io/managed-by=Helm --overwrite", mutating=True)
        run(f"{kubectl} annotate {kind} {name} meta.helm.sh/release-name=cluster-operator meta.helm.sh/release-namespace=kube-system --overwrite", mutating=True)
    print("OK")


def update_clusterconfig(cluster_config, operator_version):
    info("Updating ClusterConfig (capx versions and cluster-operator version):")
    capx = {"capi_version": CAPI}
    if infra["name"] == "aws":
        capx.update({"capa_version": CAPA, "capa_image_version": CAPA})
    elif infra["name"] == "azure":
        capx.update({"capz_version": CAPZ, "capz_image_version": CAPZ})
    patch = {"spec": {"capx": capx, "cluster_operator_version": operator_version, "cluster_operator_image_version": operator_version}}
    name, ns = cluster_config["metadata"]["name"], cluster_config["metadata"]["namespace"]
    run(f"{kubectl} -n {ns} patch clusterconfig {name} --type merge -p '{json.dumps(patch)}'", mutating=True)
    print("OK")


def upgrade_operator(operator_version):
    info(f"Upgrading cluster-operator to {operator_version}:")
    cm = kjson(f"-n kube-system get cm {OPERATOR_VALUES_CM}")
    values = cm["data"]["values.yaml"]
    image = run(f"{kubectl} -n kube-system get deploy {OPERATOR_DEPLOYMENT} -o jsonpath='{{.spec.template.spec.containers[0].image}}'").strip()
    repo, current = image.rsplit(":", 1)
    if values.count(f"tag: {current}") != 1:
        print("FAILED")
        raise Exception(f"expected exactly one 'tag: {current}' in {OPERATOR_VALUES_CM}")
    cm["data"]["values.yaml"] = values.replace(f"tag: {current}", f"tag: {operator_version}")
    cm["metadata"] = {"name": cm["metadata"]["name"], "namespace": cm["metadata"]["namespace"]}
    if config["dry_run"]:
        print(f"\n[DRY-RUN] would set image tag {current} -> {operator_version} in {OPERATOR_VALUES_CM}")
    else:
        subprocess.run(f"{kubectl} apply -f -", shell=True, input=json.dumps(cm), text=True, check=True, capture_output=True)
    run(f"{kubectl} -n kube-system patch helmrelease cluster-operator --type merge -p "
        f"'{{\"spec\":{{\"suspend\":false,\"chart\":{{\"spec\":{{\"version\":\"{operator_version}\"}}}}}}}}'", mutating=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run(f"{kubectl} -n kube-system annotate helmrelease cluster-operator reconcile.fluxcd.io/requestedAt={now} --overwrite", mutating=True)
    if not config["dry_run"]:
        run(f"{kubectl} -n kube-system wait helmrelease cluster-operator --for=condition=Ready --timeout=10m", retries=0)
        run(f"{kubectl} -n kube-system wait deploy {OPERATOR_DEPLOYMENT} --for=jsonpath='{{.spec.template.spec.containers[0].image}}'={repo}:{operator_version} --timeout=5m", retries=0)
        run(f"{kubectl} -n kube-system rollout status deploy {OPERATOR_DEPLOYMENT} --timeout 5m", retries=0)
    print("OK")


def restart_cluster_autoscaler(replicas):
    '''The CA resolves the CAPI API version only at startup (clusterapi_controller.go newMachineController).'''
    info(f"Starting cluster-autoscaler again ({replicas} replicas) so it resolves the CAPI API version anew:")
    if replicas == 0:
        print("SKIP (not deployed)")
        return
    scale("kube-system", CA_DEPLOYMENT, replicas)
    if config["dry_run"]:
        return
    deadline = time.time() + 180
    while time.time() < deadline:
        # Only the leader logs it (after leader election), so read every CA pod.
        logs = run(f"{kubectl} -n kube-system logs -l app.kubernetes.io/name=clusterapi-cluster-autoscaler --since=5m --tail=-1", allow_errors=True)
        match = re.search(r'Using version "(v1beta\d)" for API group "cluster.x-k8s.io"', logs)
        if match:
            if match.group(1) != "v1beta2":
                print("FAILED")
                raise Exception(f"cluster-autoscaler resolved {match.group(1)}, expected v1beta2")
            print("OK (v1beta2)")
            return
        time.sleep(10)
    print("WARN (startup line not found in the logs, check manually)")


# ---------------------------------------------------------------- verification

def verify(before, keos_name):
    info("Verifying the result:", end="\n")
    after = snapshot(keos_name)
    after["content"] = content_snapshot(keos_name)
    write_file(os.path.join(backup_dir, "snapshot-after.json"), json.dumps(after, indent=2))
    lost, added = diff_content(before["content"], after["content"])
    write_file(os.path.join(backup_dir, "content-diff.txt"), "== removed or changed\n" + "\n".join(lost) + "\n\n== added\n" + "\n".join(added) + "\n")
    print(f"  Object content: {len(lost)} removed/changed field(s), {len(added)} added — detail in content-diff.txt")
    for line in lost:
        print(f"  [CONTENT] {line}")
    problems = []
    expected = {"CoreProvider/cluster-api": CAPI, "BootstrapProvider/kubeadm": CAPI, "ControlPlaneProvider/kubeadm": CAPI,
                f"InfrastructureProvider/{infra['name']}": infra_target}
    for key, version in expected.items():
        if after["providers"].get(key) != version:
            problems.append(f"provider {key} is {after['providers'].get(key)}, expected {version}")
    problems += [f"CRD {c['metadata']['name']} storedVersions {c.get('status', {}).get('storedVersions')}" for c in unsettled_crds()]
    for kind in ("clusters", "machinedeployments", "machinepools", "machinesets", "machines"):
        if before["objects"][kind] != after["objects"][kind]:
            problems.append(f"{kind} changed: {before['objects'][kind]} -> {after['objects'][kind]}")
    for ns, deploy in PROVIDER_DEPLOYMENTS:
        want = kubeadm_replicas(ns, deploy) if (ns, deploy) in KUBEADM_DEPLOYMENTS else 2
        got = after["deployments"][f"{ns}/{deploy}"]["replicas"]
        if got != want:
            problems.append(f"{ns}/{deploy} replicas {got}, expected {want}")
    for pdb in kjson("get pdb -A")["items"]:
        if pdb["metadata"]["namespace"] in [ns for ns, _ in PROVIDER_DEPLOYMENTS[:2]] and pdb.get("status", {}).get("disruptionsAllowed", 0) < 1:
            problems.append(f"PDB {pdb['metadata']['namespace']}/{pdb['metadata']['name']} allows 0 disruptions")
    kc = kjson(f"-n cluster-{keos_name} get keoscluster {keos_name}").get("status", {})
    if not (kc.get("ready") and kc.get("phase") == "Provisioned"):
        problems.append(f"KeosCluster is {kc.get('ready')}/{kc.get('phase')}")
    for p in problems:
        print(f"  [FAIL] {p}")
    if problems:
        raise Exception(f"{len(problems)} verification check(s) failed")
    print("  OK — providers at target, storage v1beta2 only, no worker object replaced, replicas and PDBs restored, KeosCluster ready")


def wait_keoscluster_ready(name, timeout_seconds=600):
    info("Waiting for the KeosCluster to be ready/Provisioned:")
    if config["dry_run"]:
        print("DRY-RUN")
        return
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        s = kjson(f"-n cluster-{name} get keoscluster {name}").get("status", {})
        if s.get("ready") and s.get("phase") == "Provisioned":
            print("OK")
            return
        time.sleep(15)
    print("FAILED")
    raise Exception("KeosCluster did not reach ready/Provisioned")


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    start = time.time()
    config = parse_args()
    print(f"[INFO] upgrade-providers {__version__} — mode: " + ("DRY-RUN (no changes will be applied)" if config["dry_run"] else "REAL — applying changes"))
    kubeconfig = os.path.expanduser(config["kubeconfig"])
    kubectl = f"kubectl --kubeconfig {kubeconfig}"
    helm = f"helm --kubeconfig {kubeconfig}"

    check_binaries()
    keos_cluster, cluster_config = get_cluster()
    cluster_name = keos_cluster["metadata"]["name"]
    provider = keos_cluster["spec"]["infra_provider"]
    managed = keos_cluster["spec"]["control_plane"].get("managed")
    print(f"[INFO] Cluster: {cluster_name} — provider: {provider} — managed control plane: {managed}")
    if not ((provider in ("aws", "gcp") and managed) or (provider == "azure" and not managed)):
        sys.exit("[ERROR] Supported: EKS, GKE and Azure VMs (managed control plane on AWS/GCP, unmanaged on Azure)")
    infra = INFRA_PROVIDERS[provider]
    PROVIDER_DEPLOYMENTS = [("capi-system", "capi-controller-manager"), (infra["ns"], infra["deploy"])] + KUBEADM_DEPLOYMENTS

    versions = get_provider_versions()
    core = versions.get(("CoreProvider", "cluster-api"))
    kubeadm = [versions.get(("BootstrapProvider", "kubeadm")), versions.get(("ControlPlaneProvider", "kubeadm"))]
    infra_current = versions.get(("InfrastructureProvider", infra["name"]))
    infra_target = infra["target"] or infra_current
    print(f"[INFO] Current: core {core}, kubeadm bootstrap/control-plane {kubeadm[0]}/{kubeadm[1]}, {infra['name']} {infra_current}"
          f" (target {infra_target}{'' if infra['target'] else ', kept'})")
    if parse_version(core)[:2] < MIN_CORE_FOR_PHASE2:
        sys.exit(f"[ERROR] Core is {core}: run upgrade-provisioner.py first (it brings CAPI to {CLUSTERCTL_V1BETA1})")
    check_infra_manifests(infra, infra_target)
    phase1 = any(parse_version(v)[:2] < MIN_CORE_FOR_PHASE2 for v in kubeadm)
    at_target = core == CAPI and infra_current == infra_target and all(v == CAPI for v in kubeadm)
    print(f"[INFO] Plan: phase 1 (kubeadm to {CAPI_KUBEADM_V1BETA1}) {'NEEDED' if phase1 else 'SKIP'}; "
          f"phase 2 (all to v1beta2) {'SKIP (already at target)' if at_target else 'NEEDED'}; cluster-operator to {config['cluster_operator']}")

    preflight(keos_cluster, cluster_name)

    backup_dir = os.path.join(config["backup_dir"], datetime.now().strftime("%Y%m%d-%H%M%S") + ("-dryrun" if config["dry_run"] else ""))
    before = backup(backup_dir, cluster_name, core)
    if config["resume_from"]:
        with open(os.path.join(config["resume_from"], "snapshot-before.json")) as f:
            before = json.load(f)
        print(f"[INFO] Resuming: content and object names are compared against {config['resume_from']}/snapshot-before.json")
    registry, pull_through = get_registry(keos_cluster, cluster_config)
    print(f"[INFO] Private registry: {registry or 'no'}{' (ECR pull-through)' if pull_through else ''}")
    if infra["image"] and not registry:
        hand_over_backup(backup_dir)
        sys.exit(f"[ERROR] {infra['name']} {infra_target} is pulled from the private registry and the cluster has none. Nothing was changed. Backup: {backup_dir}")
    cfg_v1beta2 = write_clusterctl_config(os.path.join(backup_dir, "clusterctl-v1beta2.yaml"), CAPI, infra_target, registry, pull_through)

    if not at_target:
        info("Validating the plan (clusterctl upgrade plan, read-only):")
        try:
            plan = run(f"{config['clusterctl']} upgrade plan --kubeconfig {kubeconfig} --config {cfg_v1beta2}", env=clusterctl_env(), retries=0)
        except Exception as e:
            print("FAILED")
            hand_over_backup(backup_dir)
            sys.exit(f"[ERROR] {e}\n[ERROR] Nothing was changed. Backup: {backup_dir}")
        write_file(os.path.join(backup_dir, "clusterctl-upgrade-plan.txt"), plan)
        print("OK")

    if not config["dry_run"] and not config["yes"]:
        if input("Press ENTER to start the migration or any other key to abort: ") != "":
            sys.exit(0)

    ca_replicas = int(run(f"{kubectl} -n kube-system get deploy {CA_DEPLOYMENT} --ignore-not-found -o jsonpath='{{.spec.replicas}}'", allow_errors=True).strip() or 0)
    webhooks_deleted = False
    capsule_plan = []
    egress_added = []
    try:
        if ca_replicas:
            info("Stopping cluster-autoscaler:")
            scale("kube-system", CA_DEPLOYMENT, 0, wait=False)
            print("OK")
        stop_operator()
        if not at_target:
            capsule_plan = capsule_exclude_clusterctl_namespaces(backup_dir)
        # Always run it; without a live capsule exclusion (dry-run, or nothing left for clusterctl) its denials are expected.
        admission_dry_run(capsule_namespace_webhooks() if (config["dry_run"] or at_target) else ())
        if managed and not at_target:
            egress_added = kubeadm_egress_allow()

        if phase1 and not at_target:
            cfg_v1beta1 = write_clusterctl_config(os.path.join(backup_dir, "clusterctl-v1beta1.yaml"), CAPI_KUBEADM_V1BETA1, infra_current, registry, pull_through)
            clusterctl_apply(config["clusterctl_v1beta1"], cfg_v1beta1,
                             f"--bootstrap kubeadm:{CAPI_KUBEADM_V1BETA1} --control-plane kubeadm:{CAPI_KUBEADM_V1BETA1}",
                             os.path.join(backup_dir, "clusterctl-phase1.log"), "Phase 1")
            for ns, deploy in KUBEADM_DEPLOYMENTS:
                scale(ns, deploy, 0, wait=False)

        if not at_target:
            if provider == "azure":
                label_aso_crds()
            phase2 = f"--core cluster-api:{CAPI} --bootstrap kubeadm:{CAPI} --control-plane kubeadm:{CAPI}"
            if infra["target"]:
                phase2 += f" --infrastructure {infra['name']}:{infra['target']}"
            clusterctl_apply(config["clusterctl"], cfg_v1beta2, phase2, os.path.join(backup_dir, "clusterctl-phase2.log"), "Phase 2")
        capsule_restore(capsule_plan)
        capsule_plan = []
        restore_provider_replicas()
        kubeadm_egress_remove(egress_added)
        egress_added = []
        if not config["dry_run"]:
            report_feature_gates(before)
        wait_storage_migration()

        backup_and_delete_keoscluster_webhooks(backup_dir)
        webhooks_deleted = True
        update_clusterconfig(cluster_config, config["cluster_operator"])
        restore_keoscluster_webhooks(backup_dir)
        webhooks_deleted = False
        upgrade_operator(config["cluster_operator"])
        restart_cluster_autoscaler(ca_replicas)
        wait_keoscluster_ready(cluster_name)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        print("[INFO] Recovery: restoring webhooks, cluster-operator and cluster-autoscaler (no downgrade is attempted)")
        try:
            capsule_restore(capsule_plan)
            kubeadm_egress_remove(egress_added)
            if webhooks_deleted:
                restore_keoscluster_webhooks(backup_dir)
            run(f"{kubectl} -n kube-system patch helmrelease cluster-operator --type merge -p '{{\"spec\":{{\"suspend\":false}}}}'", mutating=True, allow_errors=True)
            scale("kube-system", OPERATOR_DEPLOYMENT, 2, wait=False)
            if ca_replicas:
                scale("kube-system", CA_DEPLOYMENT, ca_replicas, wait=False)
        except Exception as recovery_error:
            print(f"[ERROR] Recovery also failed: {recovery_error} — manual intervention required, backup in {backup_dir}")
        hand_over_backup(backup_dir)
        sys.exit(f"[ERROR] Migration did NOT complete. Backup: {backup_dir}")

    try:
        if not config["dry_run"]:
            verify(before, cluster_name)
        else:
            lost, added = diff_content(before["content"], content_snapshot(cluster_name))
            print(f"[INFO] Content diff self-check (nothing changed, expect 0/0): {len(lost)} removed/changed, {len(added)} added")
            for line in lost + added:
                print(f"  [CONTENT] {line}")
    finally:
        hand_over_backup(backup_dir)
    minutes, seconds = divmod(time.time() - start, 60)
    print(f"[INFO] upgrade-providers finished in {int(minutes)}m{int(seconds)}s — backup: {backup_dir}")
    print("[INFO] Mode was: " + ("DRY-RUN (no changes were applied)" if config["dry_run"] else "REAL — changes were applied"))
