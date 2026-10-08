# -*- coding: utf-8 -*-
"""providers.gcp — moved verbatim from upgrade-provisioner.py (PLT-4916)."""

import os
import sys
import json
import subprocess
import base64
import time
from ruamel.yaml import YAML
from io import StringIO
from upgrade_lib.commons.shell import run_command
from upgrade_lib import state as S

# List of GCP (CAPG) CRD names that require conversion webhook cleanup before clusterctl upgrade
CAPG_CRDS = [
    "gcpclusters.infrastructure.cluster.x-k8s.io",
    "gcpclustertemplates.infrastructure.cluster.x-k8s.io",
    "gcpmanagedclusters.infrastructure.cluster.x-k8s.io",
    "gcpmanagedcontrolplanes.infrastructure.cluster.x-k8s.io",
    "gcpmanagedmachinepools.infrastructure.cluster.x-k8s.io",
    "gcpmachines.infrastructure.cluster.x-k8s.io",
    "gcpmachinetemplates.infrastructure.cluster.x-k8s.io",
]

def patch_capg_crds_live():
    for crd in CAPG_CRDS:
        # Remove conversion webhook from CRD to avoid caBundle PEM errors during clusterctl upgrade.
        # Both patches are best-effort: if the conversion block doesn't exist the patch is a no-op.
        print(f"[INFO] Removing conversion webhook from {crd} (best-effort):", end=" ", flush=True)
        run_command(
            f"{S.kubectl} patch crd {crd} --type=json "
            "-p='[{\"op\":\"remove\",\"path\":\"/spec/conversion\"}]'",
            allow_errors=True
        )
        run_command(
            f"{S.kubectl} patch crd {crd} --type=merge "
            "-p='{\"spec\":{\"conversion\":{\"strategy\":\"None\"}}}'",
            allow_errors=True
        )
        print("OK (best-effort)")

def patch_gcp_crd_conversion_webhook(config_dir):
    """Remove conversion webhook from GCP CRDs in local repository to avoid caBundle errors"""
    print("[INFO] Removing conversion webhooks from GCP CRDs:", end=" ", flush=True)

    gcp_repo = os.path.join(config_dir, "local-repository", "infrastructure-gcp")
    if not os.path.exists(gcp_repo):
        print("SKIP (no GCP repository found)")
        return

    patched_files = []
    patched_crds = 0

    for root, _, files in os.walk(gcp_repo):
        for file in files:
            if file.endswith(".yaml"):
                filepath = os.path.join(root, file)
                try:
                    with open(filepath, 'r') as f:
                        content = f.read()

                    yaml_parser = YAML()
                    yaml_parser.preserve_quotes = True
                    yaml_parser.width = 4096

                    documents = list(yaml_parser.load_all(content))
                    modified = False

                    for doc in documents:
                        if doc and doc.get('kind') == 'CustomResourceDefinition':
                            crd_name = doc.get('metadata', {}).get('name', '')
                            if crd_name in CAPG_CRDS:
                                if 'spec' in doc and 'conversion' in doc['spec']:
                                    del doc['spec']['conversion']
                                    modified = True
                                    patched_crds += 1
                                    print(f"\n[DEBUG] Removed conversion from {crd_name} in {filepath}", flush=True)

                        # Fallback: if conversion block still exists but empty/partial, force strategy None
                        if doc and doc.get('kind') == 'CustomResourceDefinition':
                            crd_name = doc.get('metadata', {}).get('name', '')
                            if crd_name in CAPG_CRDS:
                                if 'spec' in doc and 'conversion' in doc['spec']:
                                    doc['spec']['conversion'] = {"strategy": "None"}
                                    modified = True
                                    print(f"\n[DEBUG] Forced conversion.strategy=None for {crd_name} in {filepath}", flush=True)

                    if modified:
                        output = StringIO()
                        yaml_parser.dump_all(documents, output)
                        with open(filepath, 'w') as f:
                            f.write(output.getvalue())
                        patched_files.append(filepath)

                except Exception as e:
                    print(f"\n[WARN] Failed to patch CRD in {filepath}: {e}")

    if patched_files:
        print(f"\nOK ({len(patched_files)} files patched, {patched_crds} CRDs patched)")
    else:
        print("OK (no CRDs needed patching)")

def resolve_gke_version(cluster_name, target_minor):
    '''Resolve a real, currently-valid GKE version for target_minor (e.g. "1.35") — unlike EKS,
    GKE never accepts a bare "X.Y.0" (confirmed live 2026-09-16: GCPManagedControlPlane rejects
    it with "No valid versions with the prefix ... found").'''

    gcp_creds = S.vault_secrets_data['secrets']['gcp']['credentials']
    project_id = gcp_creds['project_id']
    location = gcp_creds.get('region') or gcp_creds.get('zone')

    channel_output, _ = run_command(
        f"gcloud container clusters describe {cluster_name} --zone {location} --project {project_id} "
        f"--format='value(releaseChannel.channel)'",
        allow_errors=True
    )
    channel = channel_output.strip()

    config_output, _ = run_command(
        f"gcloud container get-server-config --zone {location} --project {project_id} --format=json"
    )
    server_config = json.loads(config_output)

    valid_versions = []
    for c in server_config.get("channels", []):
        if c.get("channel") == channel:
            valid_versions = c.get("validVersions", [])
            break
    if not valid_versions:
        valid_versions = server_config.get("validMasterVersions", [])

    patches = sorted(
        {v.split("-gke.")[0] for v in valid_versions if v.startswith(f"{target_minor}.")},
        key=lambda v: int(v.rsplit(".", 1)[1])
    )
    if not patches:
        raise Exception(f"No valid GKE version found for minor {target_minor} (channel '{channel}')")
    return "v" + patches[-1]

def wait_for_gke_node_pool_convergence(cluster_name, target_minor, stall_minutes=None):
    '''GCP only: wait for every GKE node pool AND every real node to reach target_minor. GKE sets a
    pool's `version` to the target as soon as it accepts the request, before any node has rolled
    (live 2026-09-18), so check the real kubeletVersion too — pools with 0 nodes have none.'''

    gcp_creds = S.vault_secrets_data['secrets']['gcp']['credentials']
    project_id = gcp_creds['project_id']
    location = gcp_creds.get('region') or gcp_creds.get('zone')
    if stall_minutes is None:
        stall_minutes = S.config["node_convergence_timeout"]
    print(f"[INFO] Waiting for every GKE node pool and node to reach {target_minor} (timeout {stall_minutes}m without progress):", end=" ", flush=True)
    best_progress = -1
    deadline = time.time() + stall_minutes * 60
    while time.time() < deadline:
        progress = best_progress
        pools_output, _ = run_command(
            f"gcloud container node-pools list --cluster {cluster_name} --zone {location} "
            f"--project {project_id} --format='value(name,version,status)'",
            allow_errors=True
        )
        nodes_output, _ = run_command(f"{S.kubectl} get nodes -o json", allow_errors=True)
        pools = [line.split() for line in pools_output.splitlines() if line.strip()]
        converged_pools = sum(
            1 for p in pools if len(p) == 3 and p[2] == "RUNNING" and p[1].startswith(f"{target_minor}.")
        )
        pools_converged = bool(pools) and converged_pools == len(pools)
        try:
            nodes = json.loads(nodes_output).get("items", [])
            converged_nodes = sum(
                1 for node in nodes
                if node.get("status", {}).get("nodeInfo", {}).get("kubeletVersion", "").startswith(f"v{target_minor}.")
            )
            kubelets_converged = converged_nodes == len(nodes)
            progress = converged_pools + converged_nodes
        except (ValueError, TypeError):
            kubelets_converged = False
        if pools_converged and kubelets_converged:
            print("OK")
            return
        if progress > best_progress:
            best_progress = progress
            deadline = time.time() + stall_minutes * 60
        time.sleep(30)
    raise Exception(f"No progress for {stall_minutes}m waiting for GKE node pools to reach {target_minor} ({best_progress} pool(s) + node(s) converged)")

def wait_for_gke_node_pool_convergence(cluster_name, target_minor, timeout_minutes=90):
    '''GCP only: wait for every GKE node pool AND every real node to reach target_minor. GKE sets a
    pool's `version` to the target as soon as it accepts the request, before any node has rolled
    (live 2026-09-18), so check the real kubeletVersion too — pools with 0 nodes have none.'''

    gcp_creds = S.vault_secrets_data['secrets']['gcp']['credentials']
    project_id = gcp_creds['project_id']
    location = gcp_creds.get('region') or gcp_creds.get('zone')
    print(f"[INFO] Waiting for every GKE node pool and node to reach {target_minor} (timeout {timeout_minutes}m):", end=" ", flush=True)
    deadline = time.time() + timeout_minutes * 60
    while time.time() < deadline:
        pools_output, _ = run_command(
            f"gcloud container node-pools list --cluster {cluster_name} --zone {location} "
            f"--project {project_id} --format='value(name,version,status)'",
            allow_errors=True
        )
        nodes_output, _ = run_command(f"{S.kubectl} get nodes -o json", allow_errors=True)
        pools = [line.split() for line in pools_output.splitlines() if line.strip()]
        pools_converged = bool(pools) and all(
            len(p) == 3 and p[2] == "RUNNING" and p[1].startswith(f"{target_minor}.") for p in pools
        )
        try:
            nodes = json.loads(nodes_output).get("items", [])
            kubelets_converged = all(
                node.get("status", {}).get("nodeInfo", {}).get("kubeletVersion", "").startswith(f"v{target_minor}.")
                for node in nodes
            )
        except (ValueError, TypeError):
            kubelets_converged = False
        if pools_converged and kubelets_converged:
            print("OK")
            return
        time.sleep(30)
    raise Exception(f"Timed out after {timeout_minutes}m waiting for GKE node pools to reach {target_minor}")

def configure_gcp_credentials(vault_secrets_data):
    """Configure GCP gcloud credentials from service account key"""
    print("[INFO] Configuring GCP gcloud credentials", end=" ", flush=True)

    try:
        gcp_creds = vault_secrets_data['secrets']['gcp']['credentials']
        project_id = gcp_creds['project_id']

        # Check if service_account_key exists (JSON key content)
        if 'service_account_key' in gcp_creds:
            # Write service account key to temporary file
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as key_file:
                json.dump(gcp_creds['service_account_key'], key_file)
                key_path = key_file.name

            # Activate service account
            activate_cmd = f"gcloud auth activate-service-account --key-file={key_path} --quiet"
            result = subprocess.run(activate_cmd, shell=True, capture_output=True, text=True)

            # Clean up key file
            os.remove(key_path)

            if result.returncode != 0:
                print("FAILED")
                print(f"[ERROR] gcloud auth failed: {result.stderr}")
                sys.exit(1)

        # Set default project
        project_cmd = f"gcloud config set project {project_id} --quiet"
        result = subprocess.run(project_cmd, shell=True, capture_output=True, text=True)

        if result.returncode != 0:
            print("FAILED")
            print(f"[ERROR] Setting GCP project failed: {result.stderr}")
            sys.exit(1)

        print("OK")

    except KeyError as e:
        print("FAILED")
        print(f"[ERROR] Missing GCP credential field: {e}")
        sys.exit(1)
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] GCP credential configuration failed: {e}")
        sys.exit(1)

def activate_capg_service_account(kubectl, kubeconfig):
    """
    Activates the CAPG service account from capg-manager-bootstrap-credentials
    and regenerates kubeconfig so gke-gcloud-auth-plugin uses this identity.
    """

    print("[INFO] Activating CAPG service account:", end=" ", flush=True)

    try:
        # Get credentials.json from secret
        cmd = (
            f"{kubectl} -n capg-system get secret "
            f"capg-manager-bootstrap-credentials "
            f"-o jsonpath='{{.data.credentials\\.json}}'"
        )

        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

        if result.returncode != 0 or not result.stdout.strip():
            print("FAILED")
            print(result.stderr)
            sys.exit(1)

        credentials_json = base64.b64decode(result.stdout.strip()).decode("utf-8")
        credentials = json.loads(credentials_json)

        # Write temporary key file
        import tempfile
        key_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        key_file.write(credentials_json)
        key_file.close()
        key_path = key_file.name

        # Activate SA
        subprocess.run(
            f"gcloud auth activate-service-account "
            f"--key-file={key_path} --quiet",
            shell=True,
            check=True
        )

        # Set project
        subprocess.run(
            f"gcloud config set project {credentials['project_id']} --quiet",
            shell=True,
            check=True
        )

        # Extract cluster name from kubeconfig context
        context = subprocess.check_output(
            f"kubectl --kubeconfig {kubeconfig} config current-context",
            shell=True,
            text=True
        ).strip()

        cluster_name = context.split("_")[-1]

        # 🔥 Get region from VAULT secrets (NOT from CAPG secret)
        gcp_creds = S.vault_secrets_data['secrets']['gcp']['credentials']
        location = gcp_creds.get('region') or gcp_creds.get('zone')

        if not location:
            raise Exception("GCP region/zone not found in vault secrets")

        # Refresh kubeconfig using correct region
        subprocess.run(
            f"KUBECONFIG={kubeconfig} "
            f"gcloud container clusters get-credentials {cluster_name} "
            f"--region {location} "
            f"--project {credentials['project_id']}",
            shell=True,
            check=True
        )

        print("OK")

    except Exception as e:
        print("FAILED")
        print(e)
        sys.exit(1)
