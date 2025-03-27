# Upgrade

- [Upgrade flow](#upgrade-flow)
- [Known issues](#known-issues)
- [Notes](#notes)

## Upgrade Flow

In order to upgrade `cloud-provisioner` we need to execute the `upgrade-provisioner.py` [script](../../scripts/upgrade-provisioner.py).

This script execute these steps sequentially:

1. [Validations and set-up](#validations-and-set-up)
2. [Backup](#backup)
3. [Pre-upgrade tasks](#pre-upgrade-tasks)
5. [Pre-upgrade `1.31` tasks](#pre-upgrade-131-tasks)
4. [Upgrade `k8s` to `1.31`](#upgrade-k8s-to-131)
5. [Pre-upgrade `1.32` tasks](#pre-upgrade-132-tasks)
6. [Upgrade `k8s` to `1.32`](#upgrade-k8s-to-132)

### Validations and set-up

* User `flags` validation.
* Required `files` validation:
  * Descriptor. Defaults to `cluster.yaml`
  * Secrets. Defaults to `secrets.yml`
  * Kubeconfig. Defaults to `KUBECONFIG` env or `~/.kube/config`
* Required cloud `credentials` validation:
  * AWS CLI configuration
  * Azure CLI configuration
* Required `kubernetes` `resources` validation:
  * The `Cluster`
* Upgrade path validation:
  * Check whether `EKS` or `Azure VMs`
  * Check `upgrade_cloud_provisioner_only` and `--skip-k8s-intermediate-version` only if `EKS`
* Helm repository set-up
  * `KeosCluster` patch to update `spec.helm_repository.url`
  * `HelmRepository` `keos` patch (if exists) to update `spec.url`
  * Create Helm charts version data structure (`chart_versions`)
* Cluster autoscaler scale down
* Environment variables set-up

### Backup

* `CAPX` files
* `Capsule` files <!-- TODO: Revisar -->

### Pre-upgrade tasks

* Prepare `capsule` <!-- TODO: Revisar -->
  * Add `clouds` components to `capsule-mutating-webhook-configuration` and `capsule-validating-webhook-configuration` `objectSelector`
* EKS
  * Install `aws-load-balancer-controller` (only if `--enable-lb-controller`)
  * Recreate `allow-traffic-to-aws-imds-capa` `GlobalNetworkPolicy` <!-- TODO: Revisar implementación -->
* Azure
  * Update `00-metrics-server-helm-chart-default-values` `configmap` to add `affinities` and `tolerations` <!-- TODO: Revisar -->
* Upgrade Helm charts to `k8s` `1.30` linked version <!-- TODO: Revisar implementación (creo que se podría saltar estas actualizaciones y dejar sólo la actualización a las versiones de los charts enlazados con `k8s` `1.30`) -->
  * EKS
    * `cluster-autoscaler`
    * `cluster-operator`
    * `tigera-operator`
    * `aws-load-balancer-controller`
    * `flux`
  * Azure VMs
    * `azuredisk-csi-driver`
    * `azurefile-csi-driver`
    * `cloud-provider-azure`
    * `cluster-autoscaler`
    * `tigera-operator`
    * `cluster-operator`
    * `flux`
* Restore `capsule` <!-- TODO: Revisar -->
  * Restore `capsule-mutating-webhook-configuration` and `capsule-validating-webhook-configuration` `objectSelector`

### Pre-upgrade `1.31` tasks

* Prepare `cluster-operator`:
  * Check `cluster-operator` `HelmRelease`
  * Patch `cluster-operator` `HelmRelease` `spec.suspend: true`
  * Stop `cluster-operator` `controller`
  * Patch `keoscluster-validating-webhook-configuration` timeout <!-- TODO: Revisar (no es necesario viendo el siguiente paso) -->
  * Disable `cluster-operator` `webhooks`
  * Patch `ClusterConfig` with upgraded versions
  * Restore `cluster-operator` `webhooks`
  * Start `cluster-operator` `controller`
  * Patch `cluster-operator` `HelmRelease` `spec.suspend: false`
  * Check `cluster-operator` `HelmRelease`

### Upgrade `k8s` to `1.31`

> [!IMPORTANT]
> This step is skipped when using `--skip-k8s-intermediate-version` flag

* Require `k8s` version to user
* Validate `k8s` version provided by user
* Patch `allow-all-traffic-from-control-plane` `GNP` (only in `Azure VMs`)
* Check and require `node_image` for `control-plane` and `workers` (only if `node_image` is already set in `KeosCluster`)
* Patch `KeosCluster`:
  * Patch `k8s` version
  * Patch `node_image` (if is already set)
* Wait for `control-plane` upgrade
  * Check `KeosCluster` status
* Patch `aws-node` `ClusterRole` (only if `EKS`) <!-- TODO: Revisar (no sé si sigue siendo necesario en esta versión) -->
* Wait for `workers` upgrade
  * Check `kubectl get nodes` version
  * Check `KeosCluster` status
* Restore `allow-all-traffic-from-control-plane` `GNP` (only in `Azure VM`)
* Upgrade Helm charts to `k8s` `1.31` linked version <!-- TODO: Revisar implementación (creo que se podría saltar estas actualizaciones y dejar sólo la actualización a las versiones de los charts enlazados con `k8s` `1.30`) -->
  * EKS
    * `cluster-autoscaler`
    * `cluster-operator`
    * `tigera-operator`
    * `aws-load-balancer-controller`
    * `flux`
  * Azure VMs
    * `azuredisk-csi-driver`
    * `azurefile-csi-driver`
    * `cloud-provider-azure`
    * `cluster-autoscaler`
    * `tigera-operator`
    * `cluster-operator`
    * `flux`

### Pre-upgrade `1.32` tasks

* Prepare `cluster-operator`:
  * Disable `cluster-operator` `webhooks` (only if `--skip-k8s-intermediate-version`)

### Upgrade `k8s` to `1.32`

* Require `k8s` version to user
* Validate `k8s` version provided by user
* Patch `allow-all-traffic-from-control-plane` `GNP` (only in `Azure VM`)
* Check and require `node_image` for `control-plane` and `workers` (only if `node_image` is already set in `KeosCluster`)
* Patch `KeosCluster`:
  * Patch `k8s` version
  * Patch `node_image` (if is already set)
* Wait for `control-plane` upgrade
  * Check `KeosCluster` status
* Patch `aws-node` `ClusterRole` (only if `EKS`)
* Wait for `workers` upgrade
  * Check `kubectl get nodes` version
  * Check `KeosCluster` status
* Restore `allow-all-traffic-from-control-plane` `GNP` (only in `Azure VMs`)
* Restore `cluster-operator` webhooks (only if `--skip-k8s-intermediate-version`)
* Upgrade Helm charts to `k8s` `1.30` linked version <!-- TODO: Revisar implementación -->
  * EKS
    * `cluster-autoscaler`
    * `cluster-operator`
    * `tigera-operator`
    * `aws-load-balancer-controller`
    * `flux`
  * Azure VMs
    * `azuredisk-csi-driver`
    * `azurefile-csi-driver`
    * `cloud-provider-azure`
    * `cluster-autoscaler`
    * `tigera-operator`
    * `cluster-operator`
    * `flux`
* Restore `allow-all-traffic-from-control-plane` `GNP` (only in `Azure VMs`)
* Cluster autoscaler scale up

## Known issues

* `private` registry configuration is disabled during upgrade. This means that the process would not work in `offline` environments

## Notes

* During the upgrade process some charts and images are upgraded:

  * EKS
    * Charts
    ```bash
    $ diff eks-charts-0.6.txt eks-charts-0.7.txt
    < NAME                NAMESPACE       REVISION  UPDATED                                 STATUS    CHART                     APP VERSION
    < cert-manager        cert-manager    1         2025-03-21 12:18:50.685700669 +0000 UTC deployed  cert-manager-v1.14.5      v1.14.5    
    < cluster-autoscaler  kube-system     4         2025-03-21 12:51:00.160273471 +0000 UTC deployed  cluster-autoscaler-9.37.0 1.30.0     
    < cluster-operator    kube-system     5         2025-03-21 12:19:00.855246511 +0000 UTC deployed  cluster-operator-0.4.2    0.4.2      
    < flux                kube-system     2         2025-03-21 12:15:43.157037495 +0000 UTC deployed  flux2-2.12.2              2.2.2      
    < tigera-operator     tigera-operator 2         2025-03-21 12:19:06.098177304 +0000 UTC deployed  tigera-operator-v3.28.2   v3.28.2    
    ---
    > NAME                NAMESPACE       REVISION  UPDATED                                 STATUS    CHART                           APP VERSION  
    > cert-manager        cert-manager    3         2025-03-21 15:41:36.577156594 +0000 UTC deployed  cert-manager-v1.17.0            v1.17.0      
    > cluster-autoscaler  kube-system     5         2025-03-21 15:41:16.31999084 +0000 UTC  deployed  cluster-autoscaler-9.46.0       1.32.0       
    > cluster-operator    kube-system     7         2025-03-21 15:09:34.8197919 +0000 UTC   deployed  cluster-operator-0.5.0-a315600  0.5.0-a315600
    > flux                kube-system     4         2025-03-21 15:03:35.412950788 +0000 UTC deployed  flux2-2.14.1                    2.4.0        
    > tigera-operator     tigera-operator 3         2025-03-21 15:03:08.883661654 +0000 UTC deployed  tigera-operator-v3.29.1         v3.29.1  
    ```
    * Images
    ```bash
    $ diff eks-images-0.5.txt eks-images-0.6.txt
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/aws-ebs-csi-driver:v1.32.0
    ---
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/aws-ebs-csi-driver:v1.39.0
    6,13c6,13
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-attacher:v4.6.1-eks-1-30-8
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-node-driver-registrar:v2.11.0-eks-1-30-8
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-provisioner:v5.0.1-eks-1-30-8
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-resizer:v1.11.1-eks-1-30-8
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-snapshotter:v8.0.1-eks-1-30-8
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/kube-proxy:v1.30.9-minimal-eksbuild.3
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/livenessprobe:v2.13.0-eks-1-30-8
    <       image: 963353511234.dkr.ecr.eu-west-1.amazonaws.com/keos/stratio/cluster-operator:0.4.2
    ---
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-attacher:v4.8.0-eks-1-31-12
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-node-driver-registrar:v2.13.0-eks-1-31-12
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-provisioner:v5.1.0-eks-1-31-12
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-resizer:v1.12.0-eks-1-31-11
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-snapshotter:v8.2.0-eks-1-31-12
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/kube-proxy:v1.32.0-minimal-eksbuild.2
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/livenessprobe:v2.14.0-eks-1-31-12
    >       image: 963353511234.dkr.ecr.eu-west-1.amazonaws.com/keos/stratio/cluster-operator:0.5.0-a315600
    21,25c21,25
    <       image: ghcr.io/fluxcd/helm-controller:v0.37.2
    <       image: ghcr.io/fluxcd/source-controller:v1.2.3
    <       image: quay.io/jetstack/cert-manager-cainjector:v1.14.5
    <       image: quay.io/jetstack/cert-manager-controller:v1.14.5
    <       image: quay.io/jetstack/cert-manager-webhook:v1.14.5
    ---
    >       image: ghcr.io/fluxcd/helm-controller:v1.1.0
    >       image: ghcr.io/fluxcd/source-controller:v1.4.1
    >       image: quay.io/jetstack/cert-manager-cainjector:v1.17.0
    >       image: quay.io/jetstack/cert-manager-controller:v1.17.0
    >       image: quay.io/jetstack/cert-manager-webhook:v1.17.0
    27c27
    <       image: registry.k8s.io/autoscaling/cluster-autoscaler:v1.30.0
    ---
    >       image: registry.k8s.io/autoscaling/cluster-autoscaler:v1.32.0
    ```
