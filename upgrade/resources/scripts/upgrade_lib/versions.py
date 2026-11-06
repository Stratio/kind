# -*- coding: utf-8 -*-
"""versions — moved verbatim from upgrade-provisioner.py (PLT-4916)."""


#!/usr/bin/env python3
# -*- coding: utf-8 -*-

##############################################################
# Author: Stratio Clouds <clouds-integration@stratio.com>    #
# Supported provisioner versions: 0.9.X                      #
# Supported cloud providers:                                 #
#   - EKS                                                    #
#   - Azure VMs                                              #
#   - GKE                                                    #
##############################################################

__version__ = "0.10.0"

# NOTE: plain semver since 0.9.0, no legacy "0.17.0-0.X" prefix.
CLOUD_PROVISIONER = "0.10.0"

# Must match a minor in keoscluster_webhook.go:61 k8sVersionSupported (bare "major.minor", no "v").
# EKS is patched to ".0" (ignored by EKS), GKE resolves a real patch, Azure uses AZURE_K8S_VERSION_BY_MINOR.
# Target minor per cloud (PLT-4916, PLT-5234): 1.37 on every provider. On GKE, resolve_gke_version() needs the
# minor in the cluster's channel (or validMasterVersions): 1.37 is in RAPID, REGULAR and EXTENDED.
K8S_VERSION_BY_PROVIDER = {"aws": "1.37", "azure": "1.37", "gcp": "1.37"}

# Azure only: exact version each minor step is patched to; must match that minor's image in --node-image-map (PLT-4916).
AZURE_K8S_VERSION_BY_MINOR = {"1.36": "v1.36.5", "1.37": "v1.37.1"}

# First cluster-operator release with the v1beta2 core objects (PLT-4852) and k8s 1.36 (PLT-4916).
CLUSTER_OPERATOR = "0.8.0-m.2"

# Flux's own default (5m) is too short for a DaemonSet rollout (maxUnavailable=1) — a
# fixed value doesn't scale with node count either (verified live 2026-08-25), so
# compute_helm_release_timeout() below replaces this constant; kept as fallback only.
HELM_RELEASE_TIMEOUT_FALLBACK = "15m"

# In --dry-run, mutating calls are intercepted by run_command() and never change cluster state, so these checks still run for real against current live state but with a shrunk timeout/poll budget instead of waiting on a condition that cannot change.
DRY_RUN_HELM_RELEASE_TIMEOUT = "30s"

DRY_RUN_POD_HEALTH_TIMEOUT_SECONDS = 30

DRY_RUN_CLUSTER_OPERATOR_WAIT_TIMEOUT = "30s"

DRY_RUN_KEOSCLUSTER_READY_TIMEOUT_SECONDS = 30

CLUSTER_OPERATOR_UPGRADE_SUPPORT = "0.7.X"

# Cushion after each minor step converges — CP churn can trigger transient
# leader-election loss in keoscluster-controller-manager mid-step.
STEP_SETTLE_SECONDS = 90

# Azure only: how long to wait before the first orphan-resource check (avoids racing a
# legitimately in-progress new CP replica that hasn't registered yet) and how often to
# repeat it inside wait_for_capi_kcp_version's 10s polling loop (PLT-4792).
CP_ORPHAN_CHECK_GRACE_SECONDS = 300

CP_ORPHAN_CHECK_INTERVAL_SECONDS = 60

# A mid-join etcd member is indistinguishable from a leaked one except by how long it stays that
# way (cluster-api#14197), so a candidate must hold the same state across this whole window.
CP_ORPHAN_CONFIRM_SECONDS = 600

CLOUD_PROVISIONER_LAST_PREVIOUS_RELEASE = "0.9.X"

CLUSTERCTL = "v1.10.10"
# Providers must already be on the v1beta2 line (upgrade-providers.py, PLT-4852); clusterctl is then skipped.
MIN_CAPI_CORE = "v1.13.0"

CAPI = "v1.13.6"

CAPI_KUBEADM_BOOTSTRAP = "v1.13.6"

CAPI_KUBEADM_CONTROL_PLANE = "v1.13.6"

CAPA = "v2.13.0"

# Milestone installed by upgrade-providers.py (#990); switch to the final PLT-4891 release (1.13.1-0.1.0) once pinned.
CAPG = "1.13.1-0.1.0-M1"

CAPZ = "v1.26.1"

TIGERA_OPERATOR_CALICOCTL_VERSION = "v3.33.0"

TIGERA_OPERATOR_CONTROLLER_VERSION = "v1.44.0"

# Flux v2.8.8 components shipped by the flux2 2.18.4 chart, pinned in templates/common/<minor>/flux2-helm-values.tmpl.
FLUX_IMAGE_TAGS = {
    "cli": "v2.8.8",
    "helmController": "v1.5.5",
    "kustomizeController": "v1.8.5",
    "sourceController": "v1.8.5",
}

# Bootstrap set of projectcalico/calico#14027: the chart's blanket default also tolerates the cordon taint (PLT-5023).
TIGERA_OPERATOR_TOLERATIONS = [
    {"key": "CriticalAddonsOnly", "operator": "Exists"},
    {"key": "node-role.kubernetes.io/master", "operator": "Exists", "effect": "NoSchedule"},
    {"key": "node-role.kubernetes.io/control-plane", "operator": "Exists", "effect": "NoSchedule"},
    {"key": "node.kubernetes.io/not-ready", "operator": "Exists", "effect": "NoSchedule"},
    {"key": "node.kubernetes.io/network-unavailable", "operator": "Exists", "effect": "NoSchedule"},
    {"key": "node.cloudprovider.kubernetes.io/uninitialized", "operator": "Exists", "effect": "NoSchedule"},
]

TIGERA_OPERATOR_LEGACY_TOLERATIONS = [
    {"effect": "NoExecute", "operator": "Exists"},
    {"effect": "NoSchedule", "operator": "Exists"},
]

# Stratio CA build with every cloud provider (aws for EKS, clusterapi for Azure) and kubernetes/autoscaler#9693, as create cluster
# 1.36/1.37 sets it: EKS, and Azure with a private registry. Official images hit "unknown machine for node" on scale-down for
# AWSManagedMachinePool (CAPA has no Machine object for managed nodegroups); #9693 is not in any upstream 1.36 release yet.
CLUSTER_AUTOSCALER_MP_SCALEDOWN_FIX_VERSION = "v1.36.1-0.1"
# Path of that build in the keos registry, outside any registry.k8s.io pull-through prefix (autoscaling/, k8s/).
CLUSTER_AUTOSCALER_MP_SCALEDOWN_FIX_REPOSITORY = "stratio/cluster-autoscaler"
# Azure without a private registry: upstream image, as create cluster (no MachinePools there, so the #9693 fix is not needed).
CLUSTER_AUTOSCALER_UPSTREAM_REPOSITORY = "registry.k8s.io/autoscaling/cluster-autoscaler"
CLUSTER_AUTOSCALER_UPSTREAM_VERSION = "v1.36.1"

# Azure only: azurefile-csi-driver 1.35.11 images, as create cluster pins them (templates/azure/<minor>/azurefile-csi-driver-helm-values.tmpl);
# release-1.35 publishes chart 1.35.11 twice with different defaults, so the tags cannot be left to the chart.
AZUREFILE_CSI_DRIVER_IMAGES = {
    ("image", "azurefile"): ("/oss/v2/kubernetes-csi/azurefile-csi", "v1.35.11"),
    ("image", "csiProvisioner"): ("/oss/v2/kubernetes-csi/csi-provisioner", "v6.4.0"),
    ("image", "csiResizer"): ("/oss/v2/kubernetes-csi/csi-resizer", "v2.3.0"),
    ("image", "livenessProbe"): ("/oss/v2/kubernetes-csi/livenessprobe", "v2.20.0"),
    ("image", "nodeDriverRegistrar"): ("/oss/v2/kubernetes-csi/csi-node-driver-registrar", "v2.18.0"),
    ("snapshot", "image", "csiSnapshotter"): ("/oss/v2/kubernetes-csi/csi-snapshotter", "v8.7.0"),
    ("snapshot", "image", "csiSnapshotController"): ("/oss/v2/kubernetes-csi/snapshot-controller", "v8.7.0"),
}

# Image hosts create cluster replaces with the keos registry in private mode (templates/*/<minor>/*-helm-values.tmpl).
PUBLIC_IMAGE_REGISTRIES = {"quay.io", "docker.io", "registry.k8s.io", "ghcr.io", "public.ecr.aws", "mcr.microsoft.com"}
# A previous keos registry in another account/region/project (ECR, ACR, GAR, GCR).
PRIVATE_IMAGE_REGISTRY_PATTERNS = (".dkr.ecr.", ".azurecr.io", "-docker.pkg.dev")
GCR_IMAGE_REGISTRIES = {"gcr.io", "eu.gcr.io", "us.gcr.io", "asia.gcr.io"}
# Helm repositories on ECR/ACR/GAR authenticate through the HelmRepository provider, not a secretRef (create without auth_required).
CLOUD_REGISTRY_MARKERS = (".dkr.ecr.", ".azurecr.io", "-docker.pkg.dev")
# Registries owned by the cloud provider (EKS add-on images), never moved.
PROVIDER_OWNED_IMAGE_REGISTRIES = ("602401143452.dkr.ecr.",)

# create cluster applies the same RBAC (createworker/files/aws/aws-node_rbac.yaml): the vpc-cni addon re-apply drops patch on pods from aws-node.
AWS_NODE_ANNOTATE_POD_IP_RBAC = """---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  labels:
    app.kubernetes.io/name: aws-node
    k8s-app: aws-node
  name: aws-node-annotate-pod-ip
rules:
- apiGroups:
  - ""
  resources:
  - pods
  verbs:
  - patch
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  labels:
    k8s-app: aws-node
  name: aws-node-annotate-pod-ip
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: aws-node-annotate-pod-ip
subjects:
- kind: ServiceAccount
  name: aws-node
  namespace: kube-system"""

# EKS only (MachinePools only on 0.10): one cluster-autoscaler, release "cluster-autoscaler" with cloudProvider aws, as create cluster.
# Deployment of that release with cloudProvider clusterapi and with aws (both seen live on 0.9 clusters)
CLUSTER_AUTOSCALER_CLUSTERAPI_DEPLOYMENT = "cluster-autoscaler-clusterapi-cluster-autoscaler"
CLUSTER_AUTOSCALER_AWS_DEPLOYMENT = "cluster-autoscaler-aws-cluster-autoscaler"
# Second release the 0.9 upgrade-provisioner.py installs for MachinePools on clusters from 0.7.5 (fullnameOverride = release name)
CLUSTER_AUTOSCALER_AWS_09_RELEASE = "cluster-autoscaler-aws"
# The clusterawsadm role of every EKS MachinePool (CAPA DefaultEKSNodegroupRole), the only mp_role_name cluster-operator accepts
EKS_NODEGROUP_ROLE = "eks-nodegroup.cluster-api-provider-aws.sigs.k8s.io"
# Same name and document as create cluster (createworker/aws.go clusterAutoscalerPolicyName/clusterAutoscalerPolicyDocument)
CLUSTER_AUTOSCALER_AWS_POLICY_NAME = "cluster-autoscaler.cluster-api-provider-aws.sigs.k8s.io"
CLUSTER_AUTOSCALER_AWS_POLICY_DOCUMENT = '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["autoscaling:DescribeAutoScalingGroups","autoscaling:DescribeAutoScalingInstances","autoscaling:DescribeLaunchConfigurations","autoscaling:DescribeScalingActivities","ec2:DescribeImages","ec2:DescribeInstanceTypes","ec2:DescribeLaunchTemplateVersions","ec2:GetInstanceTypesFromInstanceRequirements","eks:DescribeNodegroup"],"Resource":["*"]},{"Effect":"Allow","Action":["autoscaling:SetDesiredCapacity","autoscaling:TerminateInstanceInAutoScalingGroup"],"Resource":["*"],"Condition":{"Null":{"aws:ResourceTag/k8s.io/cluster-autoscaler/enabled":"false"}}}]}'
# create cluster file createworker/files/aws/allow-egress-imds_gnetpol.yaml; the CA aws reads its node role credentials from IMDS
IMDS_ALLOW_GNP = "allow-traffic-to-aws-imds"
IMDS_DENY_GNP = "deny-all-traffic-to-aws-imds"
IMDS_ALLOW_CLUSTER_AUTOSCALER_AWS_SELECTOR = "app.kubernetes.io/name == 'aws-cluster-autoscaler'"

# Azure only: cloud-provider-azure's own per-minor image table can reference an
# unpublished CCM tag (found live 2026-08-20: k8s 1.32 -> v1.32.16, missing everywhere).
# Mirrors templates/azure/<minor>/cloud-provider-azure-helm-values.tmpl instead.
CLOUD_PROVIDER_AZURE_CCM_VERSION_BY_MINOR = {
    "1.35": "v1.35.9",
    "1.36": "v1.36.6",
    "1.37": "v1.37.0",
}

common_charts = {
    "cert-manager": {
        "version": "v1.21.2",
        "namespace": "cert-manager",
        "repo": "https://charts.jetstack.io"
    },
    "cluster-autoscaler": {
        "version": "9.59.0",
        "namespace": "kube-system",
        "repo": "https://kubernetes.github.io/autoscaler"
    },
    "cluster-operator": {
        "version": "0.8.0-m.2",
        "namespace": "kube-system",
        "repo": ""
    },
    "flux2": {
        "version": "2.18.4",
        "namespace": "kube-system",
        "repo": "https://fluxcd-community.github.io/helm-charts",
        "release_name": "flux"
    },
    "tigera-operator": {
        "version": "v3.33.0",
        "namespace": "tigera-operator",
        "repo": "https://docs.projectcalico.org/charts"
    }
}

aws_eks_charts = {
    "aws-load-balancer-controller": {
        "version": "3.4.3",
        "namespace": "kube-system",
        "repo": "https://aws.github.io/eks-charts"
    }
}

azure_vm_charts = {
    "azuredisk-csi-driver": {
        "version": "1.36.0",
        "namespace": "kube-system",
        "repo": "https://raw.githubusercontent.com/kubernetes-sigs/azuredisk-csi-driver/master/charts"
    },
    "azurefile-csi-driver": {
        "version": "1.35.11",
        "namespace": "kube-system",
        "repo": "https://raw.githubusercontent.com/kubernetes-sigs/azurefile-csi-driver/release-1.35/charts"
    },
    "cloud-provider-azure": {
        "version": "1.36.0",
        "namespace": "kube-system",
        "repo": "https://raw.githubusercontent.com/kubernetes-sigs/cloud-provider-azure/master/helm/repo"
    }
}
