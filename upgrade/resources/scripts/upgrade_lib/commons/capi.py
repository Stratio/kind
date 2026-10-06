# -*- coding: utf-8 -*-
"""commons.capi — moved verbatim from upgrade-provisioner.py (PLT-4916)."""

import os
import sys
import yaml
import re
from datetime import datetime
from upgrade_lib.commons.shell import redact_command, run_command
from upgrade_lib.versions import CAPA, CAPG, CAPI, CAPI_KUBEADM_BOOTSTRAP, CAPI_KUBEADM_CONTROL_PLANE, CAPZ, MIN_CAPI_CORE
from upgrade_lib import state as S


def capi_target_deployments(provider):
    '''(namespace, deployment, target version) of every CAPI provider this upgrade expects for the given cloud.'''
    targets = [("capi-system", "capi-controller-manager", CAPI)]
    if provider == "aws":
        targets.append(("capa-system", "capa-controller-manager", CAPA))
    elif provider == "gcp":
        targets.append(("capg-system", "capg-controller-manager", CAPG))
    elif provider == "azure":
        targets.append(("capz-system", "capz-controller-manager", CAPZ))
        targets.append(("capi-kubeadm-bootstrap-system", "capi-kubeadm-bootstrap-controller-manager", CAPI_KUBEADM_BOOTSTRAP))
        targets.append(("capi-kubeadm-control-plane-system", "capi-kubeadm-control-plane-controller-manager", CAPI_KUBEADM_CONTROL_PLANE))
    return targets

def get_provider_image_tag(namespace, deploy):
    image, _ = run_command(
        f"{S.kubectl} -n {namespace} get deploy {deploy} -o jsonpath='{{.spec.template.spec.containers[?(@.name==\"manager\")].image}}'",
        allow_errors=True
    )
    image = (image or "").strip()
    return image.rsplit(":", 1)[-1] if ":" in image else "unknown"

def require_capi_core_min_version():
    '''Refuse to run when CAPI core is older than MIN_CAPI_CORE: 0.9.x clusters must run upgrade-providers.py first (PLT-4852).'''
    print("[INFO] Checking CAPI core version:", end=" ", flush=True)
    current = get_provider_image_tag("capi-system", "capi-controller-manager")
    found, required = re.match(r"v?(\d+)\.(\d+)", current), re.match(r"v?(\d+)\.(\d+)", MIN_CAPI_CORE)
    if not found or (int(found.group(1)), int(found.group(2))) < (int(required.group(1)), int(required.group(2))):
        print("FAILED")
        print(f"[ERROR] CAPI core is {current}; this upgrade requires {MIN_CAPI_CORE} or later. "
              "Run upgrade-providers.py first to move the Cluster API providers to the v1beta2 line.")
        sys.exit(1)
    print(f"OK ({current})")

def require_providers_at_target(provider):
    '''This script never runs a CAPI hop: every provider must already be at its exact target (set by upgrade-providers.py),
    otherwise clusterctl would run and could downgrade one (e.g. CAPG back to the pre-v1beta2 fork).'''
    print("[INFO] Checking Cluster API providers are at target versions:", end=" ", flush=True)
    mismatches = []
    for namespace, deploy, target in capi_target_deployments(provider):
        current = get_provider_image_tag(namespace, deploy)
        if current != target:
            mismatches.append(f"{deploy}: {current} (expected {target})")
    if mismatches:
        print("FAILED")
        for m in mismatches:
            print("[ERROR]   " + m)
        print("[ERROR] Run upgrade-providers.py of this release first; upgrade-provisioner.py does not upgrade Cluster API providers.")
        sys.exit(1)
    print("OK")

def create_clusterctl_config_for_private_registry(registry_url, provider, pull_through=False):
    """Create or update clusterctl config file to use private registry"""
    print("[INFO] Configuring clusterctl for private registry:", end=" ", flush=True)

    config_dir = os.path.expanduser("~/.cluster-api")
    config_file = os.path.join(config_dir, "clusterctl.yaml")

    # Create config directory if it doesn't exist
    os.makedirs(config_dir, exist_ok=True)

    # Read existing config or create new one
    config_data = {}
    if os.path.exists(config_file):
        # Backup existing config
        backup_file = config_file + ".backup-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        run_command(f"cp {config_file} {backup_file}", allow_errors=True)
        print(f"\n[DEBUG] Backed up existing config to {backup_file}")

        # Load existing config
        with open(config_file, 'r') as f:
            config_data = yaml.safe_load(f) or {}
        print(f"[DEBUG] Loaded existing clusterctl config")

    # Update images section for private registry
    if 'images' not in config_data:
        config_data['images'] = {}

    k8s_prefix  = "k8s/"  if pull_through else ""
    quay_prefix = "quay/" if pull_through else ""

    # Align the image overrides with the original installation logic.
    config_data['images']['cluster-api'] = {
        'repository': f"{registry_url}/{k8s_prefix}cluster-api",
        'tag': CAPI,
    }
    config_data['images']['bootstrap-kubeadm'] = {
        'repository': f"{registry_url}/{k8s_prefix}cluster-api",
        'tag': CAPI,
    }
    config_data['images']['control-plane-kubeadm'] = {
        'repository': f"{registry_url}/{k8s_prefix}cluster-api",
        'tag': CAPI,
    }
    config_data['images']['cert-manager'] = {
        'repository': f"{registry_url}/{quay_prefix}jetstack"
    }

    if provider == "aws":
        config_data['images']['infrastructure-aws'] = {
            'repository': f"{registry_url}/{k8s_prefix}cluster-api-aws",
            'tag': CAPA,
        }
    elif provider == "gcp":
        config_data['images']['infrastructure-gcp'] = {
            'repository': f"{registry_url}/stratio",
            'tag': CAPG,
        }
    elif provider == "azure":
        config_data['images']['infrastructure-azure/cluster-api-azure-controller'] = {
            'repository': f"{registry_url}/cluster-api-azure",
            'tag': CAPZ,
        }
        config_data['images']['infrastructure-azure/azureserviceoperator'] = {
            'repository': f"{registry_url}/k8s"
        }
        config_data['images']['infrastructure-azure/kube-rbac-proxy'] = {
            'repository': f"{registry_url}/kubebuilder"
        }
        config_data['images']['infrastructure-azure/nmi'] = {
            'repository': f"{registry_url}/oss/azure/aad-pod-identity"
        }

    # Write updated configuration
    with open(config_file, 'w') as f:
        yaml.safe_dump(config_data, f, default_flow_style=False, sort_keys=False)

    print("OK")
    print(f"[DEBUG] Updated clusterctl config at {config_file}")
    print(f"[DEBUG] Images will be pulled from private registry: {registry_url}")

    if provider == "gcp":
        # GCP local manifests need additional image rewrites beyond clusterctl overrides.
        patch_local_repository_manifests(config_dir, registry_url)

def patch_local_repository_manifests(config_dir, registry_url):
    """Patch local repository YAML manifests to use private registry"""
    print("[INFO] Patching local repository manifests:", end=" ", flush=True)

    local_repo = os.path.join(config_dir, "local-repository")
    if not os.path.exists(local_repo):
        print("SKIP (no local repository found)")
        return

    patched_count = 0
    total_replacements = 0

    for root, _, files in os.walk(local_repo):
        for file in files:
            if file.endswith(".yaml"):
                filepath = os.path.join(root, file)
                try:
                    with open(filepath, 'r') as f:
                        content = f.read()

                    # Replace registry.k8s.io with private registry
                    # This regex captures the full image path
                    original_content = content
                    new_content, count = re.subn(
                        r'registry\.k8s\.io/([^\s:"\']+)',
                        f'{registry_url}/stratio/\\1',
                        content
                    )
                    # Fix CAPG manifest that uses e2e image tag
                    new_content, gcp_count = re.subn(
                        r'gcr\.io/k8s-staging-cluster-api-gcp/cluster-api-gcp-controller:[^\s"\']+',
                        f'{registry_url}/stratio/cluster-api-gcp-controller:{CAPG}',
                        new_content
                    )
                    count += gcp_count
                    # Only write if changes were made
                    if new_content != original_content:
                        with open(filepath, 'w') as f:
                            f.write(new_content)
                        patched_count += 1
                        total_replacements += count
                        print(f"\n[DEBUG] Patched {filepath}: {count} replacements", flush=True)
                except Exception as e:
                    print(f"\n[WARN] Failed to patch {filepath}: {e}")

    print(f"\nOK ({patched_count} files patched, {total_replacements} total replacements)")

def patch_clusterctl_images(registry_url):
    '''Patch Cluster API provider image references to use a private registry'''
    print("[INFO] Patching Cluster API provider images for private registry:", end=" ", flush=True)

    repo_base = os.environ.get("CAPI_REPO")
    if not repo_base:
        print("SKIP (CAPI_REPO not set)")
        return

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

def upgrade_cluster_api_providers(provider, provider_current_versions=None):
    '''Upgrade CAPI core/infra providers via clusterctl. clusterctl always scales down and
    recreates each provider's Deployment even if already at target version (upgrader.go
    doUpgrade() only skips on NextVersion==""); skip the whole call when unneeded to avoid that churn.'''

    provider_current_versions = provider_current_versions or {}

    target_versions = {"capi-controller-manager": CAPI}
    if provider == "aws":
        target_versions["capa-controller-manager"] = CAPA
    elif provider == "gcp":
        target_versions["capg-controller-manager"] = CAPG
    elif provider == "azure":
        target_versions["capz-controller-manager"] = CAPZ
        target_versions["capi-kubeadm-bootstrap-controller-manager"] = CAPI_KUBEADM_BOOTSTRAP
        target_versions["capi-kubeadm-control-plane-controller-manager"] = CAPI_KUBEADM_CONTROL_PLANE

    if provider_current_versions and all(
        provider_current_versions.get(deploy) == target for deploy, target in target_versions.items()
    ):
        print("[INFO] Upgrading Cluster API providers: already at target versions: SKIP")
        return

    print("[INFO] Upgrading Cluster API providers:", end=" ", flush=True)

    command = (
        f"{S.env_vars} clusterctl upgrade apply "
        f"--kubeconfig {S.kubeconfig} "
        f"--core cluster-api:{CAPI} "
    )

    # Bootstrap and control-plane providers are only needed for unmanaged clusters (Azure VMs).
    # EKS and GKE manage the control plane themselves, so these providers are not upgraded.
    if provider == "azure":
        command += (
            f"--bootstrap kubeadm:{CAPI_KUBEADM_BOOTSTRAP} "
            f"--control-plane kubeadm:{CAPI_KUBEADM_CONTROL_PLANE} "
        )

    if provider == "aws":
        command += f"--infrastructure aws:{CAPA} "
    elif provider == "azure":
        command += f"--infrastructure azure:{CAPZ} "
    elif provider == "gcp":
        command += f"--infrastructure gcp:{CAPG} "

    command += "--wait-providers"

    safe_command = redact_command(command)
    print(f"\n[DEBUG] Full clusterctl command: {safe_command}", flush=True)

    run_command(command)

    print("OK")

def restore_capi_capx_ha_replicas(provider):
    '''Re-scale CAPI/CAPX controller Deployments to 2 (HA). clusterctl reinstalls upgraded
    providers with the upstream manifest's "replicas: 1" — no upgrade path re-applies the
    HA scaling `create cluster` sets, which combined with their PDB (minAvailable:1) can deadlock draining. Idempotent.'''
    print("[INFO] Restoring CAPI/CAPX HA replicas:", end=" ", flush=True)

    deployments = [("capi-system", "capi-controller-manager")]
    if provider == "aws":
        deployments.append(("capa-system", "capa-controller-manager"))
    elif provider == "gcp":
        deployments.append(("capg-system", "capg-controller-manager"))
    elif provider == "azure":
        deployments.append(("capz-system", "capz-controller-manager"))
        deployments.append(("capi-kubeadm-bootstrap-system", "capi-kubeadm-bootstrap-controller-manager"))
        deployments.append(("capi-kubeadm-control-plane-system", "capi-kubeadm-control-plane-controller-manager"))

    try:
        for namespace, deploy in deployments:
            run_command(f"{S.kubectl} -n {namespace} scale deploy {deploy} --replicas 2")
            run_command(f"{S.kubectl} -n {namespace} rollout status deploy {deploy} --timeout 90s")
        print("OK")
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error restoring CAPI/CAPX HA replicas: {e}")
        raise e
