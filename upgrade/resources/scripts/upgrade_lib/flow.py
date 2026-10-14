# -*- coding: utf-8 -*-
"""flow — moved verbatim from upgrade-provisioner.py (PLT-4916)."""

import os
import sys
import subprocess
import base64
import logging
import time
from datetime import datetime
from ansible_vault import Vault
from upgrade_lib.cli import get_version, parse_args, request_confirmation
from upgrade_lib.commons.backup import backup, prepare_capsule, restore_capsule
from upgrade_lib.commons.capi import create_clusterctl_config_for_private_registry, require_capi_core_min_version, require_providers_at_target, restore_capi_capx_ha_replicas, upgrade_cluster_api_providers
from upgrade_lib.commons.helm import filter_installed_charts, get_helm_repository, print_planned_changes, update_helm_repository, upgrade_charts, validate_helm_repository
from upgrade_lib.commons.k8s import get_keos_cluster_cluster_config, get_keos_registry_url, is_ecr_pull_through_enabled, is_private_helm_repo_enabled, is_private_registry_enabled, preflight_cluster_health_checks, scale_cluster_autoscaler
from upgrade_lib.commons.keoscluster import bump_k8s_version, parse_k8s_minor, disable_keoscluster_webhooks, restore_keoscluster_webhooks, start_keoscluster_controller, stop_keoscluster_controller, update_clusterconfig, wait_for_k8s_version_bump
from upgrade_lib.commons.shell import execute_command, run_command
from upgrade_lib.providers.aws import configure_aws_credentials, wait_for_eks_worker_convergence
from upgrade_lib.providers.azure import configure_azure_credentials, repin_cloud_provider_azure_after_bump
from upgrade_lib.providers.gcp import activate_capg_service_account, configure_gcp_credentials, patch_capg_crds_live, patch_gcp_crd_conversion_webhook
from upgrade_lib.versions import AZURE_K8S_VERSION_BY_MINOR, CAPA, CAPG, CAPZ, CLUSTERCTL, K8S_VERSION_BY_PROVIDER, DRY_RUN_CLUSTER_OPERATOR_WAIT_TIMEOUT, DRY_RUN_KEOSCLUSTER_READY_TIMEOUT_SECONDS, aws_eks_charts, azure_vm_charts, common_charts
from upgrade_lib import state as S


def run():
    start_time = time.time()
    print("[INFO] Starting cluster upgrade process")
    print("[INFO] Setting up the environment...")

    # Set backup directory
    S.backup_dir = "./backup/upgrade/"
    print("[INFO] Backup directory: " + S.backup_dir)

    # Configure the logger
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)

    # Parse arguments
    S.config = parse_args()

    print("[INFO] Mode: " + ("DRY-RUN (no changes will be applied)" if S.config["dry_run"] else "REAL — applying changes"))

    # Set kubeconfig
    print("[INFO] Setting kubeconfig:", end =" ", flush=True)
    if os.environ.get("KUBECONFIG"):
        S.kubeconfig = os.environ.get("KUBECONFIG")
    else:
        S.kubeconfig = os.path.expanduser(S.config["kubeconfig"])
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
    if not os.path.exists(S.config["secrets"]):
        print("[ERROR] Secrets file not found")
        sys.exit(1)
    if not os.path.exists(S.kubeconfig):
        print("[ERROR] Kubeconfig file not found")
        sys.exit(1)
    print("OK")

    # Get data from vault secrets file (secrets.yml)
    print("[INFO] Reading secrets file", end =" ", flush=True)
    try:
        vault = Vault(S.config["vault_password"])
        S.vault_secrets_data = vault.load(open(S.config["secrets"]).read())
    except Exception as e:
        print("[ERROR] Decoding secrets file failed:\n" + str(e))
        sys.exit(1)
    print("OK")

    # Configure cloud provider CLI
    if 'aws' in S.vault_secrets_data['secrets']:
        configure_aws_credentials(S.vault_secrets_data)
    elif 'azure' in S.vault_secrets_data['secrets']:
        configure_azure_credentials(S.vault_secrets_data)
    elif 'gcp' in S.vault_secrets_data['secrets']:
        configure_gcp_credentials(S.vault_secrets_data)
    else:
        print("[ERROR] Unable to detect provider from secrets file for CLI configuration")
        sys.exit(1)

    # Print kubeconfig path
    print("[INFO] Using kubeconfig: " + S.kubeconfig)

    # Set kubectl
    print("[INFO] Setting kubectl with kubeconfig", end =" ", flush=True)
    S.kubectl = "kubectl --kubeconfig " + S.kubeconfig
    print("OK")

    # Set helm
    print("[INFO] Setting helm with kubeconfig", end =" ", flush=True)
    S.helm = "helm --kubeconfig " + S.kubeconfig
    print("OK")

    # Detect provider early from secrets file
    if 'aws' in S.vault_secrets_data['secrets']:
        S.provider = "aws"
    elif 'azure' in S.vault_secrets_data['secrets']:
        S.provider = "azure"
    elif 'gcp' in S.vault_secrets_data['secrets']:
        S.provider = "gcp"
    else:
        print("[ERROR] Unable to detect provider from secrets file")
        sys.exit(1)

    print("[INFO] Detected provider: " + S.provider)

    # Extract cluster name from kubeconfig context
    try:
        context_cmd = f"kubectl --kubeconfig {S.kubeconfig} config current-context"
        current_context = subprocess.check_output(context_cmd, shell=True, text=True, stderr=subprocess.DEVNULL).strip()
        # Extract cluster name from context — format varies by provider (EKS/AKS: contains it
        # directly; GKE: gke_project_zone_clustername).
        if S.provider == "gcp" and "gke_" in current_context:
            cluster_name_guess = current_context.split("_")[-1]
        elif S.provider == "aws" and "@" in current_context:
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
        command = S.kubectl + " get ns >/dev/null 2>&1"
        return subprocess.call(command, shell=True) == 0

    # If kubectl access fails, attempt kubeconfig refresh for supported providers before exiting with error
    if not test_kubectl():
        print("FAILED (attempting kubeconfig refresh)", flush=True)

        if S.provider == "aws":
            region = S.vault_secrets_data['secrets']['aws']['credentials']['region']

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
                f"--kubeconfig {S.kubeconfig}"
            )

            print(f"[INFO] Attempting to refresh kubeconfig for cluster: {cluster_name_for_refresh}")
            status = subprocess.call(refresh_cmd, shell=True)

            if status != 0:
                print("[ERROR] Failed to refresh kubeconfig")
                sys.exit(1)

            # Rebuild kubectl with refreshed kubeconfig
            S.kubectl = "kubectl --kubeconfig " + S.kubeconfig

            if not test_kubectl():
                print("[ERROR] kubectl still failing after kubeconfig refresh")
                sys.exit(1)

            print("OK (kubeconfig refreshed)")

        elif S.provider == "azure":
            print("[ERROR] kubectl access failed")
            print("[HINT] For Azure, refresh upgrade credentials:")
            print("[HINT] For Azure, refresh credentials by updating the kubeconfig file:")
            print(f"  1. Locate your kubeconfig at: {S.kubeconfig}")
            print(f"  2. Update the authentication credentials manually in the file")
            print(f"  3. Ensure the user credentials or service principal tokens are valid")
            print("[ACTION REQUIRED] After updating the credentials in the kubeconfig file, please re-run this script")
            sys.exit(1)

        elif S.provider == "gcp":
            # Get GCP credentials from vault
            gcp_creds = S.vault_secrets_data['secrets']['gcp']['credentials']
            project_id = gcp_creds['project_id']
            region = gcp_creds.get('region', gcp_creds.get('zone'))  # Support both region and zone

            # Try to get cluster name from context or use provided name
            if cluster_name_guess:
                cluster_name_for_refresh = cluster_name_guess
            else:
                print("[ERROR] Cannot refresh kubeconfig: cluster name not detected from context")
                print("[HINT] Ensure your kubeconfig has a valid context set")
                sys.exit(1)

            # Determine if it's a regional or zonal cluster
            if region:
                location_flag = f"--region {region}"
            else:
                print("[ERROR] Cannot refresh kubeconfig: region/zone not found in secrets")
                print("[HINT] Ensure 'region' or 'zone' is set in GCP credentials")
                sys.exit(1)

            refresh_cmd = (
                f"KUBECONFIG={S.kubeconfig} gcloud container clusters get-credentials {cluster_name_for_refresh} "
                f"{location_flag} "
                f"--project {project_id}"
            )

            print(f"[INFO] Attempting to refresh kubeconfig for cluster: {cluster_name_for_refresh}")
            status = subprocess.call(refresh_cmd, shell=True)

            if status != 0:
                print("[ERROR] Failed to refresh kubeconfig")
                sys.exit(1)

            # Rebuild kubectl with refreshed kubeconfig
            S.kubectl = "kubectl --kubeconfig " + S.kubeconfig

            if not test_kubectl():
                print("[ERROR] kubectl still failing after kubeconfig refresh")
                sys.exit(1)

            print("OK (kubeconfig refreshed)")

        else:
            print("[ERROR] kubectl access failed and auto-refresh not supported for this provider")
            sys.exit(1)
    else:
        print("OK")

    # Activate CAPG service account and refresh GKE kubeconfig after kubectl validation
    if S.provider == "gcp":
        activate_capg_service_account(S.kubectl, S.kubeconfig)

    # Get KeosCluster and ClusterConfig
    print("[INFO] Getting KeosCluster and ClusterConfig", end =" ", flush=True)
    S.keos_cluster, S.cluster_config = get_keos_cluster_cluster_config()
    print("OK")

    # Get cluster_name from KeosCluster metadata
    print("[INFO] Getting cluster name from KeosCluster metadata", end =" ", flush=True)
    if "metadata" in S.keos_cluster:
        cluster_name = S.keos_cluster["metadata"]["name"]
    else:
        print("[ERROR] KeosCluster definition not found. Ensure that KeosCluster is defined before ClusterConfig in the descriptor file")
        sys.exit(1)
    print("OK")

    print("[INFO] Cluster name: " + cluster_name)

    # Verify provider matches
    provider_from_cluster = S.keos_cluster["spec"]["infra_provider"]
    if S.provider != provider_from_cluster:
        print(f"[WARN] Provider mismatch: detected '{S.provider}' from secrets but cluster reports '{provider_from_cluster}'")
        S.provider = provider_from_cluster

    print("[INFO] Provider: " + S.provider)

    max_k8s_version = K8S_VERSION_BY_PROVIDER[S.provider]
    if not S.config["k8s_version"]:
        S.config["k8s_version"] = max_k8s_version
    elif parse_k8s_minor(S.config["k8s_version"]) > parse_k8s_minor(max_k8s_version):
        print(f"[ERROR] --k8s-version {S.config['k8s_version']} is above the {S.provider} target {max_k8s_version} for this release")
        sys.exit(1)
    print("[INFO] Target k8s version: " + S.config["k8s_version"])

    # Azure steps one minor at a time and every step needs an AZURE_K8S_VERSION_BY_MINOR entry; fail before the chart upgrades, not mid-bump.
    if S.provider == "azure":
        min_azure_start = min(parse_k8s_minor(m) for m in AZURE_K8S_VERSION_BY_MINOR)
        min_azure_start = (min_azure_start[0], min_azure_start[1] - 1)
        if parse_k8s_minor(S.keos_cluster["spec"]["k8s_version"]) < min_azure_start:
            print(f"[ERROR] Cluster k8s_version is {S.keos_cluster['spec']['k8s_version']}; this release upgrades Azure VMs clusters from {min_azure_start[0]}.{min_azure_start[1]} onwards — upgrade it to {min_azure_start[0]}.{min_azure_start[1]} with the previous release first")
            sys.exit(1)

    preflight_cluster_health_checks(S.keos_cluster, cluster_name, S.provider)

    require_capi_core_min_version()
    require_providers_at_target(S.provider)

    if not S.config["dry_run"] and not S.config["yes"]:
        request_confirmation()

    # Check supported upgrades (provider already retrieved above)
    managed = S.keos_cluster["spec"]["control_plane"]["managed"]
    if not ((S.provider == "aws" and managed) or (S.provider == "azure" and not managed) or (S.provider == "gcp" and managed)):
        print("[ERROR] Upgrade is only supported for EKS, GKE and Azure VMs clusters")
        sys.exit(1)

    # Setting clusterctl env vars
    S.env_vars = "CLUSTER_TOPOLOGY=true CLUSTERCTL_DISABLE_VERSIONCHECK=true GOPROXY=off"

    # Get and update the helm repository if needed
    helm_repository_current = get_helm_repository(S.keos_cluster)
    helm_repository = input(f"The current helm repository is: {helm_repository_current}. Do you want to indicate a new helm repository? Press enter or specify new repository: ")
    if helm_repository == "" or helm_repository == helm_repository_current:
        print("[INFO] Helm repository unchanged: SKIP")
    else:
        validate_helm_repository(helm_repository)
        update_helm_repository(cluster_name, helm_repository, S.config["dry_run"])
        S.config["helm_repository_override"] = helm_repository

    # Scale down cluster-autoscaler to avoid issues during the upgrade process
    scale_cluster_autoscaler(0, S.config["dry_run"])

    # Configure provider-specific environment variables and credentials for clusterctl
    print("[INFO] Configuring provider-specific environment variables for clusterctl:", end=" ", flush=True)

    if S.provider == "aws":
        # AWS/CAPA (Cluster API Provider AWS) configuration
        S.namespace = "capa-system"
        version = CAPA
        # Extract AWS credentials from Kubernetes secret
        credentials = subprocess.getoutput(S.kubectl + " -n " + S.namespace + " get secret capa-manager-bootstrap-credentials -o jsonpath='{.data.credentials}'")
        # Enable EKS IAM integration and set base64-encoded credentials
        S.env_vars += " CAPA_EKS_IAM=true AWS_B64ENCODED_CREDENTIALS=" + credentials
        # MachinePool + EKSAllowAddRoles feature gates for CAPA (needed for AWSManagedMachinePool)
        if managed:
            # clusterctl skips reinstalling CAPA if its version is unchanged, so these
            # env vars are a no-op in that case — report which case this run is in.
            capa_args, _ = run_command(
                f"{S.kubectl} get deployment capa-controller-manager -n capa-system "
                "-o jsonpath='{.spec.template.spec.containers[?(@.name==\"manager\")].args}'",
                allow_errors=True
            )
            gates_already_active = "MachinePool=true" in capa_args and "EKSAllowAddRoles=true" in capa_args
            if gates_already_active:
                print("[INFO] CAPA feature gates (MachinePool, EKSAllowAddRoles) already active: SKIP")
            else:
                print("[INFO] CAPA feature gates (MachinePool, EKSAllowAddRoles) will be requested for this upgrade "
                      "(only takes effect if clusterctl actually reinstalls CAPA, i.e. its version changes)")
            S.env_vars += " EXP_MACHINE_POOL=true CAPA_EKS_ADD_ROLES=true"

    elif S.provider == "gcp":
        # GCP/CAPG (Cluster API Provider GCP) configuration
        S.namespace = "capg-system"
        version = CAPG
        # Extract GCP service account credentials from Kubernetes secret
        credentials = subprocess.getoutput(S.kubectl + " -n " + S.namespace + " get secret capg-manager-bootstrap-credentials -o json | jq -r '.data[\"credentials.json\"]'")
        # Enable experimental features for managed GKE clusters
        if managed:
            S.env_vars += " EXP_MACHINE_POOL=true EXP_CAPG_GKE=true"
        # Set base64-encoded GCP credentials
        S.env_vars += " GCP_B64ENCODED_CREDENTIALS=" + credentials

    elif S.provider == "azure":
        # Azure/CAPZ (Cluster API Provider Azure) configuration
        S.namespace = "capz-system"
        version = CAPZ
        # Enable experimental machine pool support for managed clusters
        if managed:
            S.env_vars += " EXP_MACHINE_POOL=true"

        # Configure Azure service principal credentials from vault secrets
        if "credentials" in S.vault_secrets_data["secrets"]["azure"]:
            credentials = S.vault_secrets_data["secrets"]["azure"]["credentials"]
            # Encode Azure credentials in base64 format for clusterctl
            S.env_vars += " AZURE_CLIENT_ID_B64=" + base64.b64encode(credentials["client_id"].encode("ascii")).decode("ascii")
            S.env_vars += " AZURE_CLIENT_SECRET_B64=" + base64.b64encode(credentials["client_secret"].encode("ascii")).decode("ascii")
            S.env_vars += " AZURE_SUBSCRIPTION_ID_B64=" + base64.b64encode(credentials["subscription_id"].encode("ascii")).decode("ascii")
            S.env_vars += " AZURE_TENANT_ID_B64=" + base64.b64encode(credentials["tenant_id"].encode("ascii")).decode("ascii")
        else:
            print("[ERROR] Azure credentials not found in secrets file")
            sys.exit(1)

    print("OK")

    # Set GITHUB_TOKEN env var if exists in vault secrets to avoid hitting github API rate limits during the upgrade process
    print("[INFO] Setting GITHUB_TOKEN environment:", end=" ", flush=True)
    if "github_token" in S.vault_secrets_data["secrets"]:
        S.env_vars += " GITHUB_TOKEN=" + S.vault_secrets_data["secrets"]["github_token"]
        S.helm = "GITHUB_TOKEN=" + S.vault_secrets_data["secrets"]["github_token"] + " " + S.helm
        S.kubectl = "GITHUB_TOKEN=" + S.vault_secrets_data["secrets"]["github_token"] + " " + S.kubectl
        print("OK")
    else:
        print("SKIP (not configured)")

    # Configure backup if not disabled
    if not S.config["disable_backup"]:
        now = datetime.now()
        S.backup_dir = S.backup_dir + now.strftime("%Y%m%d-%H%M%S")
        backup(S.backup_dir, S.namespace, cluster_name, S.config["dry_run"])
    else:
        print("[INFO] Backup disabled: SKIP")

    # Prepare capsule
    if not S.config["disable_prepare_capsule"]:
        prepare_capsule(S.config["dry_run"])
    else:
        print("[INFO] Capsule preparation disabled: SKIP")

    # Re-fetch KeosCluster and ClusterConfig to ensure we work with the latest state before upgrading
    print("[INFO] Re-fetching KeosCluster and ClusterConfig:", end=" ", flush=True)
    S.keos_cluster, S.cluster_config = get_keos_cluster_cluster_config()
    print("OK")

    private_registry = is_private_registry_enabled(S.cluster_config)
    S.private_helm_repo = is_private_helm_repo_enabled(S.cluster_config)
    S.cluster_operator_version = S.config["cluster_operator"]

    charts_to_upgrade = dict(common_charts)
    if S.provider == "aws":
        # Since aws-load-balancer-controller is optional we need to check if is installed
        aws_eks_charts_installed = filter_installed_charts(aws_eks_charts)
        charts_to_upgrade.update(aws_eks_charts_installed)
    elif S.provider == "azure":
        charts_to_upgrade.update(azure_vm_charts)
    charts_to_upgrade["cluster-operator"]["version"] = S.cluster_operator_version

    # Filter out charts that are not installed to avoid errors
    charts_to_upgrade = filter_installed_charts(charts_to_upgrade)

    chart_current_versions, provider_current_versions = print_planned_changes(charts_to_upgrade, S.provider)

    upgrade_charts(charts_to_upgrade, chart_current_versions)
    print("[INFO] All charts updated successfully")

    # Restore capsule
    if not S.config["disable_prepare_capsule"]:
        restore_capsule(S.config["dry_run"])

    # A run killed mid critical-section can leave spec.suspend=true behind, which Flux
    # never reconciles — the wait below would then loop until timeout on every re-run
    # (kubectl requires observedGeneration>=generation, wait/condition.go:73-74).
    suspend_check, _ = run_command(
        f"{S.kubectl} get helmrelease cluster-operator -n kube-system -o jsonpath='{{.spec.suspend}}'",
        allow_errors=True
    )
    if suspend_check.strip() == "true":
        print("[WARN] cluster-operator helmrelease is suspended (leftover from a previously interrupted run) — unsuspending before waiting:", end=" ", flush=True)
        run_command(f"{S.kubectl} patch helmrelease cluster-operator -n kube-system --type merge --patch '{{\"spec\":{{\"suspend\":false}}}}'")
        print("OK")

    print("[INFO] Waiting for the cluster-operator helmrelease to be ready:", end=" ", flush=True)
    cluster_operator_wait_timeout = DRY_RUN_CLUSTER_OPERATOR_WAIT_TIMEOUT if S.config["dry_run"] else "5m"
    command = f"{S.kubectl} wait helmrelease cluster-operator -n kube-system --for=condition=Ready --timeout={cluster_operator_wait_timeout}"
    try:
        run_command(command)
        print("OK")
    except Exception as e:
        print("[WARN] HelmRelease not ready, checking status...")
        status_cmd = f"{S.kubectl} get helmrelease cluster-operator -n kube-system -o jsonpath='{{.status}}'"
        status_output, _ = run_command(status_cmd, allow_errors=True)
        print(f"[INFO] HelmRelease status: {status_output}")
        raise e
    print("[INFO] Upgrading Cluster Operator components...")

    # Recovery (except block below) must cover every step from here on — a failure in
    # any of them (e.g. 2026-08-24: the ready/Provisioned check) must not leave the
    # HelmRelease suspended or webhooks/controller in a half-touched state.
    try:
        print("[INFO] Suspending cluster-operator helmrelease:", end =" ", flush=True)
        command = S.kubectl + " patch helmrelease cluster-operator -n kube-system --type merge --patch '{\"spec\":{\"suspend\":true}}'"
        run_command(command)
        print("OK")

        print("[INFO] Verifying KeosCluster is ready/Provisioned before this critical section:", end=" ", flush=True)
        # Raised to 5min (seen live 2026-08-26: 95s to replace) — real read of current state even in --dry-run, just a short budget since nothing here mutates.
        deadline = time.time() + (DRY_RUN_KEOSCLUSTER_READY_TIMEOUT_SECONDS if S.config["dry_run"] else 300)
        while True:
            ready_output, _ = run_command(
                f"{S.kubectl} get keoscluster {cluster_name} -n cluster-{cluster_name} -o jsonpath='{{.status.ready}} {{.status.phase}}'",
                allow_errors=True
            )
            ready_parts = ready_output.split()
            if len(ready_parts) == 2 and ready_parts[0] == "true" and ready_parts[1] == "Provisioned":
                break
            if time.time() >= deadline:
                print("FAILED")
                raise Exception(
                    f"KeosCluster is not ready/Provisioned before this critical section "
                    f"(status: '{ready_output}') — refusing to proceed on top of an unsettled cluster. "
                    f"Investigate and re-run once status.ready=true and status.phase=Provisioned."
                )
            time.sleep(5)
        print("OK")

        stop_keoscluster_controller()

        disable_keoscluster_webhooks()
        update_clusterconfig(S.cluster_config, charts_to_upgrade, S.provider, S.cluster_operator_version)

        # -------------------------------------------------
        # Private registry configuration for GCP (critical: must run before clusterctl upgrade)
        # -------------------------------------------------
        if private_registry:
            registry_url = get_keos_registry_url(S.keos_cluster)
            print(f"[DEBUG] Using private registry: {registry_url}")
            create_clusterctl_config_for_private_registry(registry_url, S.provider, pull_through=is_ecr_pull_through_enabled(S.keos_cluster))
            if S.provider == "gcp":
                # Also patch GCP CRDs to remove conversion webhooks (caBundle issue)
                config_dir = os.path.expanduser("~/.cluster-api")
                patch_gcp_crd_conversion_webhook(config_dir)

        # -------------------------------------------------
        # GCP CRD conversion webhook cleanup (prevents caBundle PEM error during clusterctl upgrade)
        # -------------------------------------------------
        if S.provider == "gcp":
            patch_capg_crds_live()
        # -------------------------------------------------
        # Execute clusterctl upgrade
        # -------------------------------------------------
        upgrade_cluster_api_providers(S.provider, provider_current_versions)
        print("[INFO] Cluster API providers upgraded successfully")
        restore_capi_capx_ha_replicas(S.provider)

        # Azure/GCP step the CP one minor at a time and need the controller running to
        # propagate each step (confirmed live 2026-09-16 for GCP: controlPlaneVersion never
        # advanced with the controller stopped). AWS keeps it stopped for its single patch.
        if S.provider in ("azure", "gcp"):
            start_keoscluster_controller()

        # k8s_version bump — controller stays stopped until the end for AWS only; Azure/GCP
        # step the CP one minor at a time instead (2026-08-24: a direct jump stuck old-etcd CP
        # replicas on Azure).
        k8s_version_bumped = bump_k8s_version(S.keos_cluster, cluster_name, S.config["k8s_version"], S.config["start_from_k8s_version"], S.config["dry_run"], provider=S.provider, node_image_map=S.config["node_image_map"])
    except Exception as e:
        print(f"[ERROR] Critical section failed ({e}) — attempting controlled recovery: restoring webhooks and controller before aborting")
        try:
            restore_keoscluster_webhooks()
            start_keoscluster_controller()
            run_command(
                S.kubectl + " patch helmrelease cluster-operator -n kube-system --type merge --patch '{\"spec\":{\"suspend\":false}}'",
                allow_errors=True
            )
            print("[INFO] Recovery completed: KeosCluster webhooks and controller restored, but the upgrade itself did NOT complete")
        except Exception as recovery_error:
            print(f"[ERROR] Recovery ALSO failed: {recovery_error}")
            print("[ERROR] Cluster may be left with KeosCluster webhooks disabled and the controller stopped — manual intervention required")
        raise e

    restore_keoscluster_webhooks()
    start_keoscluster_controller()
    print("[INFO] Resuming cluster-operator helmrelease:", end =" ", flush=True)
    command = S.kubectl + " patch helmrelease cluster-operator -n kube-system --type merge --patch '{\"spec\":{\"suspend\":false}}'"
    run_command(command)
    print("OK")

    print("[INFO] Waiting for the cluster-operator helmrelease to be ready:", end =" ", flush=True)
    cluster_operator_wait_timeout = DRY_RUN_CLUSTER_OPERATOR_WAIT_TIMEOUT if S.config["dry_run"] else "5m"
    command = S.kubectl + f" wait helmrelease cluster-operator -n kube-system --for=condition=Ready --timeout={cluster_operator_wait_timeout}"
    try:
        run_command(command)
        print("OK")
    except Exception as e:
        print("FAILED")
        print("[ERROR] HelmRelease failed to become ready, checking status...")
        status_cmd = f"{S.kubectl} get helmrelease cluster-operator -n kube-system -o yaml"
        status_output, _ = run_command(status_cmd, allow_errors=True)
        print(f"[DEBUG] HelmRelease details:\n{status_output}")
        print("[HINT] Check if the Helm chart exists in the registry and credentials are correct")
        raise e

    if k8s_version_bumped:
        # A k8s_version bump can take much longer than the generic 5m wait below —
        # CAPA advances the real control plane one minor at a time (see
        # wait_for_k8s_version_bump() docstring), so wait on the real signal instead.
        wait_for_k8s_version_bump(cluster_name, S.provider, S.config["k8s_version"])
        if S.provider == "aws":
            wait_for_eks_worker_convergence(cluster_name, S.config["k8s_version"])
        if S.provider == "azure":
            repin_cloud_provider_azure_after_bump(S.config["k8s_version"])
    else:
        print("[INFO] Waiting for keoscluster to be ready:", end =" ", flush=True)

        command = (
            S.kubectl + " wait --for=jsonpath=\"{.status.ready}\"=true KeosCluster "
            + cluster_name + " -n cluster-" + cluster_name + " --timeout 5m"
        )
        execute_command(command, False)

    command = S.kubectl + " wait deployment -n kube-system keoscluster-controller-manager --for=condition=Available --timeout=5m"
    try:
        run_command(command)
        print("[INFO] keoscluster-controller-manager is Available")
    except Exception as e:
        print("[ERROR] Failed to wait for keoscluster-controller-manager:", e)

    print("[INFO] Restoring cluster-autoscaler replicas")
    scale_cluster_autoscaler(2, S.config["dry_run"])

    end_time = time.time()
    elapsed_time = end_time - start_time
    minutes, seconds = divmod(elapsed_time, 60)
    print("[INFO] Upgrade process finished successfully in " + str(int(minutes)) + " minutes and " + "{:.2f}".format(seconds) + " seconds")
    print("[INFO] Mode was: " + ("DRY-RUN (no changes were applied)" if S.config["dry_run"] else "REAL — changes were applied"))
