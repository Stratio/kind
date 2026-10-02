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
# Target minor per cloud (user decision 2026-09-30, PLT-4916): 1.36 everywhere in 0.10.0, 1.37 from 0.10.1.
K8S_VERSION_BY_PROVIDER = {"aws": "1.36", "azure": "1.36", "gcp": "1.36"}

# Azure only: exact version each minor step is patched to; must match that minor's image in --node-image-map (PLT-4916).
AZURE_K8S_VERSION_BY_MINOR = {"1.36": "v1.36.5"}

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

TIGERA_OPERATOR_CALICOCTL_VERSION = "v3.32.2"

TIGERA_OPERATOR_CONTROLLER_VERSION = "v1.42.6"

# AWS only: official CA images hit "unknown machine for node" on scale-down for
# AWSManagedMachinePool (CAPA has no Machine object for managed nodegroups). kubernetes/autoscaler#9693
# fixes it but isn't backported to any release yet — known, accepted risk pinning DEPENDENCIES' version.
CLUSTER_AUTOSCALER_MP_SCALEDOWN_FIX_VERSION = "v1.36.1"

# Azure only: cloud-provider-azure's own per-minor image table can reference an
# unpublished CCM tag (found live 2026-08-20: k8s 1.32 -> v1.32.16, missing everywhere).
# Mirrors templates/azure/<minor>/cloud-provider-azure-helm-values.tmpl instead.
CLOUD_PROVIDER_AZURE_CCM_VERSION_BY_MINOR = {
    "1.32": "v1.34.2",
    "1.34": "v1.34.2",
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
        "version": "2.19.1",
        "namespace": "kube-system",
        "repo": "https://fluxcd-community.github.io/helm-charts",
        "release_name": "flux"
    },
    "tigera-operator": {
        "version": "v3.32.2",
        "namespace": "tigera-operator",
        "repo": "https://docs.projectcalico.org/charts"
    }
}

aws_eks_charts = {
    "aws-load-balancer-controller": {
        "version": "3.4.0",
        "namespace": "kube-system",
        "repo": "https://aws.github.io/eks-charts"
    }
}

azure_vm_charts = {
    "azuredisk-csi-driver": {
        "version": "1.34.5",
        "namespace": "kube-system",
        "repo": "https://raw.githubusercontent.com/kubernetes-sigs/azuredisk-csi-driver/master/charts"
    },
    "azurefile-csi-driver": {
        "version": "1.35.7",
        "namespace": "kube-system",
        "repo": "https://raw.githubusercontent.com/kubernetes-sigs/azurefile-csi-driver/master/charts"
    },
    "cloud-provider-azure": {
        "version": "1.36.0",
        "namespace": "kube-system",
        "repo": "https://raw.githubusercontent.com/kubernetes-sigs/cloud-provider-azure/master/helm/repo"
    }
}
