# -*- coding: utf-8 -*-
"""commons.helm — moved verbatim from upgrade-provisioner.py (PLT-4916)."""

import os
import sys
import json
import shlex
import yaml
import time
from jinja2 import Template, Environment, FileSystemLoader
from urllib.parse import urlparse
from upgrade_lib.commons.k8s import get_keos_registry_url, is_ecr_pull_through_enabled, is_private_registry_enabled
from upgrade_lib.commons.keoscluster import wait_for_keos_cluster
from upgrade_lib.commons.shell import execute_command, redact_command, run_command
from upgrade_lib.providers.azure import update_cloud_provider_azure_image_tag_value
from upgrade_lib.versions import CAPA, CAPG, CAPI, CAPI_KUBEADM_BOOTSTRAP, CAPI_KUBEADM_CONTROL_PLANE, CAPZ, CLUSTER_AUTOSCALER_MP_SCALEDOWN_FIX_VERSION, DRY_RUN_HELM_RELEASE_TIMEOUT, DRY_RUN_POD_HEALTH_TIMEOUT_SECONDS, HELM_RELEASE_TIMEOUT_FALLBACK, TIGERA_OPERATOR_CALICOCTL_VERSION, TIGERA_OPERATOR_CONTROLLER_VERSION
from upgrade_lib import state as S

_helm_release_timeout_cache = None

def compute_helm_release_timeout():
    '''Scale the Flux HelmRelease timeout with the real, current node count (workers + CP).'''
    global _helm_release_timeout_cache
    if _helm_release_timeout_cache is not None:
        return _helm_release_timeout_cache
    if S.config["dry_run"]:
        _helm_release_timeout_cache = DRY_RUN_HELM_RELEASE_TIMEOUT
        return _helm_release_timeout_cache
    nodes_output, _ = run_command(f"{S.kubectl} get nodes --no-headers", allow_errors=True)
    node_count = len(nodes_output.strip().splitlines()) if nodes_output else 0
    if not node_count:
        _helm_release_timeout_cache = HELM_RELEASE_TIMEOUT_FALLBACK
    else:
        minutes = max(15, 5 + node_count * 2)
        _helm_release_timeout_cache = f"{minutes}m"
    return _helm_release_timeout_cache

# Set up Jinja2 environment to load templates from the templates directory
template_dir = './templates'

env = Environment(loader=FileSystemLoader(template_dir))

# Load Jinja2 templates for HelmRepository and HelmRelease manifests
helmrepository_template = env.get_template('helmrepository_template.yaml')

helmrelease_template = env.get_template('helmrelease_template.yaml')

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
    command = f"{S.kubectl} -n cluster-{cluster_name} patch KeosCluster {cluster_name} --type='json' -p='{patch_json}'"
    execute_command(command, dry_run, False)

    patch_helmRepository = [
        {"op": "replace", "path": "/spec/url", "value": helm_repository},
    ]
    patch_json = json.dumps(patch_helmRepository)
    existing_helmrepo, err = run_command(f"{S.kubectl} get helmrepository -n kube-system keos --ignore-not-found", allow_errors=True)
    if "doesn't have a resource type \"helmrepository\"" in err:
        existing_helmrepo = False

    if existing_helmrepo:
        command = f"{S.kubectl} -n kube-system patch helmrepository keos --type='json' -p='{patch_json}'"
        execute_command(command, dry_run, False)

    wait_for_keos_cluster(cluster_name, "10")

def get_chart_version(chart, namespace):
    '''Get the version of a Helm chart'''

    command = S.helm + " -n " + namespace + " list"
    output = execute_command(command, False, False)
    for line in output.split("\n"):
        splitted_line = line.split()
        if chart == splitted_line[0]:
            # helm list output columns (0-indexed):
            # 0:NAME  1:NAMESPACE  2:REVISION  3:UPDATED(date)  4:UPDATED(time)
            # 5:UPDATED(timezone)  6:UPDATED(utc)  7:STATUS  8:CHART  9:APP_VERSION
            if chart == "cluster-operator":
                return splitted_line[9]
            else:
                return splitted_line[8].split("-")[-1]
    return None

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

def render_values_template(values_file, keos_cluster, cluster_config):
    '''Render the values template'''

    try:
        values_params = {
            "private": is_private_registry_enabled(cluster_config),
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
            values = render_values_template( f"values/{chart_name}_default_values.tmpl", S.keos_cluster, S.cluster_config)
        elif chart_name in charts_requiring_values_update_provider:
            values = render_values_template( f"values/{provider}/{chart_name}_default_values.tmpl", S.keos_cluster, S.cluster_config)
        else:
            values, err = run_command(f"{S.helm} get values {chart_name} -n {namespace} --output yaml")
        if is_ecr_pull_through_enabled(S.keos_cluster):
            registry_url = get_keos_registry_url(S.keos_cluster)
            pull_through_substitutions = [
                (f"{registry_url}/tigera",    f"{registry_url}/quay/tigera"),
                (f"{registry_url}/jetstack",  f"{registry_url}/quay/jetstack"),
                (f"{registry_url}/fluxcd",    f"{registry_url}/ghcr/fluxcd"),
                (f"{registry_url}/autoscaling", f"{registry_url}/k8s/autoscaling"),
                (f"{registry_url}/eks/",      f"{registry_url}/ecrpublic/eks/"),
            ]
            for old, new in pull_through_substitutions:
                values = values.replace(old, new)
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

def update_cluster_autoscaler_image_tag_value(values_file):
    '''Pin cluster-autoscaler to the Stratio #9693 build used on every provider that deploys it. This custom image lives
    at "{registry}/autoscaling/...", never behind the k8s.io ECR pull-through cache like the
    official one — must undo create_default_values()'s rewrite or the tag hits a dead path (verified live).'''

    try:
        with open(values_file, 'r') as file:
            values = yaml.safe_load(file)

        image = values.setdefault('image', {})
        image['tag'] = CLUSTER_AUTOSCALER_MP_SCALEDOWN_FIX_VERSION
        if 'repository' in image:
            image['repository'] = image['repository'].replace('/k8s/autoscaling', '/autoscaling')

        with open(values_file, 'w') as file:
            yaml.safe_dump(values, file, default_flow_style=False)

    except Exception as e:
        print(f"An error occurred: {e}")

def update_tigera_operator_image_tag_value(values_file):
    '''Update tigera-operator calicoctl/controller image tags. Currently match the chart's
    own defaults exactly (verified live via `helm pull`) — kept as explicit pins so a future
    chart bump can't silently drift the version without us noticing.'''

    try:
        with open(values_file, 'r') as file:
            values = yaml.safe_load(file)

        values['calicoctl']['tag'] = TIGERA_OPERATOR_CALICOCTL_VERSION
        # Calico v3.33 ships calicoctl in the consolidated calico/calico image; calico/ctl no longer exists
        values['calicoctl']['image'] = values['calicoctl'].get('image', '').replace('/calico/ctl', '/calico/calico')
        values['tigeraOperator']['version'] = TIGERA_OPERATOR_CONTROLLER_VERSION

        # The chart defaults whisker/goldmane to true, so an upgrade from a chart older than
        # v3.30 installs them unasked; force both from the descriptor instead.
        observability_enabled = S.keos_cluster["spec"].get("calico", {}).get("observability_enabled", False)
        # setdefault, not assignment: the chart passes the sibling keys on as the CR spec.
        values.setdefault('whisker', {})['enabled'] = observability_enabled
        values.setdefault('goldmane', {})['enabled'] = observability_enabled

        # Apply ECR pull-through prefixes to registry fields.
        # The registry URL is stored separately from the image path in tigera values,
        # so string substitution in create_default_values() never matches — must be done here.
        if is_ecr_pull_through_enabled(S.keos_cluster):
            registry_url = get_keos_registry_url(S.keos_cluster)
            quay_registry = f"{registry_url}/quay"
            dockerhub_registry = f"{registry_url}/dockerhub"
            if values.get('tigeraOperator', {}).get('registry', '').startswith(registry_url) and \
               not values['tigeraOperator']['registry'].startswith(quay_registry):
                values['tigeraOperator']['registry'] = quay_registry
            if 'installation' in values:
                values['installation']['registry'] = quay_registry
                values['installation']['imagePath'] = 'calico'
            calico_image = values.get('calicoctl', {}).get('image', '')
            if calico_image.startswith(registry_url) and not calico_image.startswith(dockerhub_registry):
                values['calicoctl']['image'] = calico_image.replace(
                    f"{registry_url}/calico", f"{dockerhub_registry}/calico", 1)

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
        command = f"{S.kubectl} create configmap {configmap_name} -n {namespace} --from-file=values.yaml={values_file} --dry-run=client -o yaml | kubectl apply -f -"
        run_command(command)
    except Exception as e:
        raise e

def filter_installed_charts(charts):
    '''Remove not installed charts'''

    try:
        output, err = run_command(S.helm  + " list --all-namespaces --output json")
        charts_installed = json.loads(output)
        charts_installed_names = [chart["name"] for chart in charts_installed]

        charts_filtered = {
            chart_name: chart_data
            for chart_name, chart_data in charts.items()
            if chart_data.get("release_name", chart_name) in charts_installed_names
        }
        return charts_filtered
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error getting charts installed {e}.")
        raise e

def apply_chart_crds(chart_name, chart_version, repo_url, repo_schema, repo_username=None, repo_password=None, dry_run=False, rendered=False):
    '''Pull chart and apply CRDs — Helm upgrade never updates CRDs, must be done explicitly.
    rendered=True: the chart IS the CRDs (in templates/), applied server-side and fatal on error.'''

    import tempfile
    import glob

    print(f"[INFO] Applying CRDs for {chart_name} {chart_version}:", end=" ", flush=True)
    with tempfile.TemporaryDirectory() as tmpdir:
        # Neither the registry login nor the actual chart pull run in dry-run — dry-run must
        # not depend on network/registry state. Prints the resolved pull command instead, so
        # the target repo (default or the one typed at the helm-repository prompt) is visible.
        if repo_schema == "oci":
            pull_cmd = f"{S.helm} pull {repo_url}/{chart_name} --version {chart_version} -d {tmpdir}"
        else:
            pull_cmd = f"{S.helm} pull {chart_name} --repo {repo_url} --version {chart_version} -d {tmpdir}"
            if repo_username and repo_password:
                pull_cmd += f" --username {shlex.quote(repo_username)} --password {shlex.quote(repo_password)}"

        if dry_run:
            print(f"DRY-RUN (would run: {redact_command(pull_cmd)})")
            return

        try:
            if repo_schema == "oci":
                registry = repo_url.replace("oci://", "").split("/")[0]
                if ".dkr.ecr." in registry:
                    region = registry.split(".")[3]
                    run_command(f"aws ecr get-login-password --region {region} | {S.helm} registry login {registry} --username AWS --password-stdin")
                elif ".azurecr.io" in registry:
                    acr_name = registry.split(".")[0]
                    run_command(
                        f"az acr login --name {acr_name} --expose-token --output tsv --query accessToken | "
                        f"{S.helm} registry login {registry} --username 00000000-0000-0000-0000-000000000000 --password-stdin"
                    )
                elif ".pkg.dev" in registry:
                    run_command(f"gcloud auth print-access-token | {S.helm} registry login {registry} --username oauth2accesstoken --password-stdin")
            run_command(pull_cmd)
        except Exception as e:
            print("FAILED")
            raise Exception(f"could not pull chart {chart_name} {chart_version} from {repo_url} to apply its CRDs: {e}") from e

        tarballs = glob.glob(f"{tmpdir}/*.tgz")
        if not tarballs:
            print("SKIP (no tarball found)")
            return

        tarball = tarballs[0]
        if rendered:
            # Some CRDs exceed the client-side apply annotation limit (charts/tigera-operator/README.md@v3.32.2)
            run_command(f"{S.helm} template {chart_name} {tarball} | {S.kubectl} apply --server-side --force-conflicts -f -")
            print("OK")
            return
        run_command(f"tar xzf {tarball} -C {tmpdir} {chart_name}/crds/ 2>/dev/null || true")

        crd_files = glob.glob(f"{tmpdir}/{chart_name}/crds/*.yaml")
        if not crd_files:
            print("SKIP (no CRDs in chart)")
            return

        # Applying individual CRD files IS best-effort: a given CRD may have no real
        # schema change in this version, so a single kubectl apply failure here shouldn't
        # block the whole upgrade the way a missing/unreachable chart should.
        try:
            for crd_file in crd_files:
                run_command(f"{S.kubectl} apply -f {crd_file}")
            print("OK")
        except Exception as e:
            print(f"WARN ({e}) — continuing without CRD update")

def wait_for_helmrelease_ready(release_name, namespace, timeout="15m"):
    '''Wait for a HelmRelease to report Ready=True, raising with the real status on failure'''

    command = f"{S.kubectl} wait helmrelease {release_name} -n {namespace} --for=condition=Ready --timeout={timeout}"
    try:
        run_command(command, retries=0)  # kubectl wait already has its own timeout/retry semantics
    except Exception as e:
        status_output, _ = run_command(
            f"{S.kubectl} get helmrelease {release_name} -n {namespace} -o jsonpath='{{.status.conditions}}'",
            allow_errors=True
        )
        raise Exception(f"HelmRelease {namespace}/{release_name} not Ready: {status_output}") from e

def check_release_pods_healthy(chart_name, release_name, namespace, timeout_seconds=None, poll_interval=5):
    '''Best-effort check that the release's pods are actually healthy, not just Ready in Flux.
    Retries for up to timeout_seconds — a rollout in progress can transiently look unhealthy.
    In --dry-run nothing is actually rolling out, so the retry budget shrinks accordingly.'''
    if timeout_seconds is None:
        timeout_seconds = DRY_RUN_POD_HEALTH_TIMEOUT_SECONDS if S.config["dry_run"] else 300

    # Charts whose pods carry none of the generic labels (verified live on azure-4852up, PLT-4916); all selectors are merged.
    chart_selectors = {
        "flux2": ["app in (helm-controller,kustomize-controller,notification-controller,source-controller)"],
        "cloud-provider-azure": ["component=cloud-controller-manager", "k8s-app=cloud-node-manager"],
    }

    def list_pods(selector):
        pods_json, _ = run_command(f"{S.kubectl} get pods -n {namespace} -l '{selector}' -o json", allow_errors=True)
        try:
            return json.loads(pods_json).get("items", []) if pods_json else []
        except Exception:
            return []

    def get_pods():
        if chart_name in chart_selectors:
            return [pod for selector in chart_selectors[chart_name] for pod in list_pods(selector)]
        for selector in (f"app.kubernetes.io/instance={release_name}", f"app.kubernetes.io/name={chart_name}", f"k8s-app={chart_name}"):
            pods = list_pods(selector)
            if pods:
                return pods
        return []

    def evaluate(pods):
        unhealthy = []
        for pod in pods:
            pod_name = pod["metadata"]["name"]
            phase = pod.get("status", {}).get("phase")
            container_statuses = pod.get("status", {}).get("containerStatuses", [])
            bad_reasons = [
                cs["state"]["waiting"]["reason"]
                for cs in container_statuses
                if "waiting" in cs.get("state", {}) and cs["state"]["waiting"].get("reason") in
                   ("CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError")
            ]
            all_ready = all(cs.get("ready", False) for cs in container_statuses) if container_statuses else False
            if phase not in ("Running", "Succeeded") or bad_reasons or not all_ready:
                unhealthy.append((pod_name, phase, bad_reasons))
        return unhealthy

    pods = get_pods()
    if not pods:
        print(f"(pod health check skipped for {release_name}: no pods matched known labels)", end=" ")
        return

    elapsed = 0
    unhealthy = evaluate(pods)
    while unhealthy and elapsed < timeout_seconds:
        time.sleep(poll_interval)
        elapsed += poll_interval
        pods = get_pods() or pods
        unhealthy = evaluate(pods)

    if unhealthy:
        details = []
        for pod_name, phase, bad_reasons in unhealthy:
            describe_output, _ = run_command(f"{S.kubectl} describe pod {pod_name} -n {namespace}", allow_errors=True)
            details.append(f"--- {pod_name} (phase={phase}, reasons={bad_reasons}) ---\n{describe_output[-1500:]}")
        raise Exception(f"{len(unhealthy)} unhealthy pod(s) for release {release_name} after {timeout_seconds}s:\n" + "\n".join(details))

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

    if chart_name == "cluster-operator" or S.private_helm_repo:
        repo_name = "keos"
        # In dry-run the KeosCluster is never patched, so read the value the operator typed.
        repo_url = S.config.get("helm_repository_override") or S.keos_cluster["spec"]["helm_repository"]["url"]
        if "auth_required" in S.keos_cluster["spec"]["helm_repository"]:
            if S.keos_cluster["spec"]["helm_repository"]["auth_required"]:
                if "user" in S.vault_secrets_data["secrets"]["helm_repository"] and "pass" in S.vault_secrets_data["secrets"]["helm_repository"]:
                    repo_auth_required= True
                    repo_username = S.vault_secrets_data["secrets"]["helm_repository"]["user"]
                    repo_password = S.vault_secrets_data["secrets"]["helm_repository"]["pass"]
                else:
                    print("[ERROR] Helm repository credentials not found in secrets file")
                    sys.exit(1)
        if urlparse(repo_url).scheme == "oci":
            repo_schema = "oci"

    default_values_file = f"/tmp/{release_name}_default_values.yaml"
    empty_values_file = f"/tmp/{release_name}_empty_values.yaml"
    previous_values_file = f"/tmp/{release_name}_previous_default_values.yaml"
    default_values_configmap = f"00-{release_name}-helm-chart-default-values"
    previous_values = ""
    values_written = False
    release_applied = False

    # Cleanup function for temp files
    def cleanup_temp_files():
        for temp_file in [default_values_file, empty_values_file, previous_values_file]:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass

    try:
        create_default_values(release_name, chart_namespace, default_values_file, S.provider)
        if release_name == "cluster-operator":
            update_cluster_operator_image_tag_value(default_values_file, S.cluster_operator_version)
        elif release_name == "tigera-operator":
            update_tigera_operator_image_tag_value(default_values_file)
        elif release_name == "cluster-autoscaler" and S.provider in ("aws", "azure"):
            update_cluster_autoscaler_image_tag_value(default_values_file)
        elif release_name == "cloud-provider-azure" and S.provider == "azure":
            update_cloud_provider_azure_image_tag_value(default_values_file)

        create_empty_values_file(empty_values_file)

        # Kept to undo the values write if the new chart never gets applied (live 2026-10-01: new
        # tigera image + old chart RBAC -> CrashLoopBackOff, and the pre-flight blocked the relaunch).
        previous_values, _ = run_command(
            f"{S.kubectl} get configmap {default_values_configmap} -n {chart_namespace} -o jsonpath='{{.data.values\\.yaml}}'",
            allow_errors=True
        )
        with open(previous_values_file, 'w') as f:
            f.write(previous_values)

        create_configmap_from_values(default_values_configmap, chart_namespace, default_values_file)
        values_written = True
        create_configmap_from_values(f"02-{release_name}-helm-chart-override-values", chart_namespace, empty_values_file)

        helm_repo_data = {
            'repository_name': repo_name,
            'namespace': chart_namespace,
            'interval': '10m',
            'repository_url': repo_url,
            'schema': repo_schema,
            'provider': S.provider,
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
            'HelmReleaseRetries': 3,
            'HelmReleaseTimeout': compute_helm_release_timeout()
        }

        if chart_name == "cluster-operator":
            apply_chart_crds(chart_name, chart_version, repo_url, repo_schema, repo_username, repo_password, S.config["dry_run"])
        elif chart_name == "tigera-operator":
            # Calico >= v3.32 no longer ships its CRDs in this chart (charts/tigera-operator/README.md@v3.32.2)
            apply_chart_crds("crd.projectcalico.org.v1", chart_version, repo_url, repo_schema, repo_username, repo_password, S.config["dry_run"], rendered=True)

        helmrepository_yaml = helmrepository_template.render(helm_repo_data)
        helmrelease_yaml = helmrelease_template.render(helm_release_data)

        repository_file = f'/tmp/{release_name}_helmrepository.yaml'
        release_file = f'/tmp/{release_name}_helmrelease.yaml'

        with open(repository_file, 'w') as f:
            f.write(helmrepository_yaml)

        with open(release_file, 'w') as f:
            f.write(helmrelease_yaml)

        # We need to use --server-side and --force-conflicts flags to avoid metadata.resourceVersion conflicts
        run_command(f"{S.kubectl} apply -f {repository_file} --server-side --force-conflicts")
        run_command(f"{S.kubectl} apply -f {release_file} -n {chart_namespace} --server-side --force-conflicts")
        release_applied = True

        # cluster-operator has its own dedicated wait right after upgrade_charts() returns
        if chart_name != "cluster-operator":
            wait_for_helmrelease_ready(release_name, chart_namespace, timeout=compute_helm_release_timeout())
            check_release_pods_healthy(chart_name, release_name, chart_namespace)

        print("OK")

        # Cleanup temp files after successful apply
        cleanup_temp_files()
        if os.path.exists(repository_file):
            os.remove(repository_file)
        if os.path.exists(release_file):
            os.remove(release_file)

    except Exception as e:
        # Once the HelmRelease is applied, chart and values are a matching pair — leave them to Flux.
        if values_written and not release_applied and previous_values.strip():
            print(f"[WARN] {release_name}: the new chart was not applied, restoring the previous default values:", end=" ", flush=True)
            try:
                create_configmap_from_values(default_values_configmap, chart_namespace, previous_values_file)
                print("OK")
            except Exception as restore_error:
                print(f"FAILED ({restore_error})")
        cleanup_temp_files()
        raise e

def print_planned_changes(charts, provider):
    '''Print current (live, queried from the cluster) vs target versions before applying any change.

    Returns (chart_current_versions, provider_current_versions) so callers can skip
    components that are already at their target version.
    '''

    print("[INFO] Planned changes:")

    installed_versions = {}
    try:
        installed_output, _ = run_command(f"{S.helm} list --all-namespaces --output json", allow_errors=True)
        if installed_output:
            for release in json.loads(installed_output):
                installed_versions[release["name"]] = release.get("chart", "")
    except Exception:
        pass

    chart_current_versions = {}
    for chart_name, chart_data in charts.items():
        release_name = "flux" if chart_name == "flux2" else chart_name
        chart_field = installed_versions.get(release_name, "")
        # helm's "chart" field is "<chartname>-<version>" — strip the chart name prefix
        current = chart_field[len(chart_name) + 1:] if chart_field.startswith(f"{chart_name}-") else "unknown"
        chart_current_versions[chart_name] = current
        print(f"    {chart_name}: {current} -> {chart_data['version']}")

    provider_deployments = [("capi-system", "capi-controller-manager", CAPI)]
    if provider == "aws":
        provider_deployments.append(("capa-system", "capa-controller-manager", CAPA))
    elif provider == "gcp":
        provider_deployments.append(("capg-system", "capg-controller-manager", CAPG))
    elif provider == "azure":
        provider_deployments.append(("capi-kubeadm-bootstrap-system", "capi-kubeadm-bootstrap-controller-manager", CAPI_KUBEADM_BOOTSTRAP))
        provider_deployments.append(("capi-kubeadm-control-plane-system", "capi-kubeadm-control-plane-controller-manager", CAPI_KUBEADM_CONTROL_PLANE))
        provider_deployments.append(("capz-system", "capz-controller-manager", CAPZ))

    provider_current_versions = {}
    for namespace, deploy, target_version in provider_deployments:
        image, _ = run_command(
            f"{S.kubectl} -n {namespace} get deploy {deploy} -o jsonpath='{{.spec.template.spec.containers[?(@.name==\"manager\")].image}}'",
            allow_errors=True
        )
        current_version = image.rsplit(":", 1)[-1] if image and ":" in image else "unknown"
        provider_current_versions[deploy] = current_version
        print(f"    {deploy}: {current_version} -> {target_version}")

    return chart_current_versions, provider_current_versions

def upgrade_charts(charts, chart_current_versions=None):
    '''Update the charts, skipping ones already at target version (Flux's HelmRelease
    reconciliation is idempotent — verified live, re-running an unchanged chart creates no new revision).'''

    chart_current_versions = chart_current_versions or {}
    # cluster-autoscaler/tigera-operator carry a values-level image tag override independent of
    # the chart version — skipping them would also skip re-applying that override. Never skip these.
    never_skip = {"cluster-autoscaler", "tigera-operator"}
    if is_ecr_pull_through_enabled(S.keos_cluster):
        # create_default_values() rewrites image paths for pull-through — an already-matching
        # chart would otherwise SKIP and keep its pre-pull-through path forever.
        never_skip = set(charts.keys())
    try:
        print(f"[INFO] Updating charts versions:")
        for chart_name, chart_data in charts.items():
            chart_version = chart_data["version"]
            if chart_name not in never_skip and chart_current_versions.get(chart_name) == chart_version:
                print(f"[INFO] Chart {chart_name} already at version {chart_version}: SKIP")
                continue
            print(f"[INFO] Updating chart {chart_name} to version {chart_version}:", end =" ", flush=True)
            upgrade_chart(chart_name, chart_data)
    except Exception as e:
        print("FAILED")
        print(f"[ERROR] Error updating chart: {e}")
        raise e
