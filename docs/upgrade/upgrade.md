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
5. [Pre-upgrade `1.29` tasks](#pre-upgrade-129-tasks)
4. [Upgrade `k8s` to `1.29`](#upgrade-k8s-to-129)
5. [Pre-upgrade `1.30` tasks](#pre-upgrade-130-tasks)
6. [Upgrade `k8s` to `1.30`](#upgrade-k8s-to-130)

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
  * Patch `KubeadmConfigTemplates` <!-- TODO: Revisar (se está ejecutando con `allow_errors=True`) -->
  * Update `00-metrics-server-helm-chart-default-values` `configmap` to add `affinities` and `tolerations` <!-- TODO: Revisar -->
  * Create `azure-cloud-provider` `secret`
* Install `Flux` <!-- TODO: Revisar implementación -->
* Upgrade `CAPX` <!-- TODO: Revisar implementación -->
* Adopt installed Helm charts into `Flux`
* Install `cert-manager` Helm chart <!-- TODO: Revisar (realmente se adapta el chart como se hace con los demás en el paso anterior) -->
* Upgrade Helm charts to `k8s` `1.28` linked version <!-- TODO: Revisar implementación (creo que se podría saltar estas actualizaciones y dejar sólo la actualización a las versiones de los charts enlazados con `k8s` `1.30`) -->
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

### Pre-upgrade `1.29` tasks

* Prepare `tigera-operator` <!-- TODO: Revisar (se hacen demasiadas cosas aquí y la función `restart_tigera_operator_manifest` no es muy intuitiva) -->
* Prepare `cluster-operator`:
  * Check `cluster-operator` `HelmRelease`
  * Patch `cluster-operator` `HelmRelease` `spec.suspend: true`
  * Stop `cluster-operator` `controller`
  * Patch `keoscluster-validating-webhook-configuration` timeout <!-- TODO: Revisar (no es necesario viendo el siguiente paso) -->
  * Disable `cluster-operator` `webhooks`
  * Patch `ClusterConfig` with upgraded versions
  * Patch `KeosCluster` with new `HelmRepository` and `Volumes` data <!-- TODO: Revisar (se modifican muchos elementos del objeto `KeosCluster`) -->
  * Restore `cluster-operator` `webhooks`
  * Start `cluster-operator` `controller`
  * Patch `cluster-operator` `HelmRelease` `spec.suspend: false`
  * Check `cluster-operator` `HelmRelease`

### Upgrade `k8s` to `1.29`

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
* Upgrade Helm charts to `k8s` `1.29` linked version <!-- TODO: Revisar implementación (creo que se podría saltar estas actualizaciones y dejar sólo la actualización a las versiones de los charts enlazados con `k8s` `1.30`) -->
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

### Pre-upgrade `1.30` tasks

* Prepare `cluster-operator`:
  * Disable `cluster-operator` `webhooks` (only if `--skip-k8s-intermediate-version`)

### Upgrade `k8s` to `1.30`

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
* Patch `KubeadmControlPlane` (only in `Azure VMs`) <!-- TODO: Revisar (se está ejecutando con `allow_errors=True`) -->
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
* Patch `allow-all-traffic-from-control-plane` `GNP` (only in `Azure VM`)
* Updating default volumes <!-- TODO: Revisar implementación -->
* Restore `allow-all-traffic-from-control-plane` `GNP` (only in `Azure VMs`)
* Cluster autoscaler scale up

## Known issues

* `private` registry configuration is disabled during upgrade. This means that the process would not work in `offline` environments

## Notes

* During the upgrade process some charts and images are upgraded:

  * EKS
    * Charts
    ```bash
    $ diff eks-charts-0.5.txt eks-charts-0.6.txt
    < calico              tigera-operator 1         2025-03-21 10:12:35.936183898 +0000 UTC deployed  tigera-operator-v3.26.1   v3.26.1    
    < cluster-autoscaler  kube-system     1         2025-03-21 10:13:20.585279217 +0000 UTC deployed  cluster-autoscaler-9.29.1 1.27.2     
    < cluster-operator    kube-system     3         2025-03-21 11:01:17.138098065 +0000 UTC deployed  cluster-operator-0.3.7    0.3.7      
    ---
    > cert-manager        cert-manager    1         2025-03-21 12:18:50.685700669 +0000 UTC deployed  cert-manager-v1.14.5      v1.14.5    
    > cluster-autoscaler  kube-system     4         2025-03-21 12:51:00.160273471 +0000 UTC deployed  cluster-autoscaler-9.37.0 1.30.0     
    > cluster-operator    kube-system     5         2025-03-21 12:19:00.855246511 +0000 UTC deployed  cluster-operator-0.4.2    0.4.2      
    > flux                kube-system     2         2025-03-21 12:15:43.157037495 +0000 UTC deployed  flux2-2.12.2              2.2.2      
    > tigera-operator     tigera-operator 2         2025-03-21 12:19:06.098177304 +0000 UTC deployed  tigera-operator-v3.28.2   v3.28.2 
    ```
    * Images
    ```bash
    $ diff eks-images-0.5.txt eks-images-0.6.txt
    <       image: 066635153087.dkr.ecr.il-central-1.amazonaws.com/eks/kube-proxy:v1.28.15-minimal-eksbuild.9
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/aws-ebs-csi-driver:v1.29.1
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/coredns:v1.10.1-eksbuild.18
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-attacher:v4.5.0-eks-1-29-7
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-node-driver-registrar:v2.10.0-eks-1-29-7
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-provisioner:v4.0.0-eks-1-29-7
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-resizer:v1.10.0-eks-1-29-7
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-snapshotter:v7.0.1-eks-1-29-7
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/kube-proxy:v1.28.15-minimal-eksbuild.9
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/livenessprobe:v2.12.0-eks-1-29-7
    <       image: 963353511234.dkr.ecr.eu-west-1.amazonaws.com/keos/stratio/cluster-operator:0.3.7
    <       image: docker.io/calico/csi:v3.26.1
    <       image: docker.io/calico/kube-controllers:v3.26.1
    <       image: docker.io/calico/node-driver-registrar:v3.26.1
    <       image: docker.io/calico/node:v3.26.1
    <       image: docker.io/calico/pod2daemon-flexvol:v3.26.1
    <       image: docker.io/calico/typha:v3.26.1
    ---
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/aws-ebs-csi-driver:v1.32.0
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/coredns:v1.11.4-eksbuild.2
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-attacher:v4.6.1-eks-1-30-8
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-node-driver-registrar:v2.11.0-eks-1-30-8
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-provisioner:v5.0.1-eks-1-30-8
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-resizer:v1.11.1-eks-1-30-8
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-snapshotter:v8.0.1-eks-1-30-8
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/kube-proxy:v1.30.9-minimal-eksbuild.3
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/livenessprobe:v2.13.0-eks-1-30-8
    >       image: 963353511234.dkr.ecr.eu-west-1.amazonaws.com/keos/stratio/cluster-operator:0.4.2
    >       image: docker.io/calico/csi:v3.28.2
    >       image: docker.io/calico/kube-controllers:v3.28.2
    >       image: docker.io/calico/node-driver-registrar:v3.28.2
    >       image: docker.io/calico/node:v3.28.2
    >       image: docker.io/calico/pod2daemon-flexvol:v3.28.2
    >       image: docker.io/calico/typha:v3.28.2
    <       image: quay.io/jetstack/cert-manager-cainjector:v1.13.1
    <       image: quay.io/jetstack/cert-manager-controller:v1.13.1
    <       image: quay.io/jetstack/cert-manager-webhook:v1.13.1
    <       image: quay.io/tigera/operator:v1.30.5
    <       image: registry.k8s.io/autoscaling/cluster-autoscaler:v1.27.2
    <       image: registry.k8s.io/cluster-api-aws/cluster-api-aws-controller:v2.2.1
    <       image: registry.k8s.io/cluster-api/cluster-api-controller:v1.5.3
    <     - image: docker.io/calico/pod2daemon-flexvol:v3.26.1
    ---
    >       image: ghcr.io/fluxcd/helm-controller:v0.37.2
    >       image: ghcr.io/fluxcd/source-controller:v1.2.3
    >       image: quay.io/jetstack/cert-manager-cainjector:v1.14.5
    >       image: quay.io/jetstack/cert-manager-controller:v1.14.5
    >       image: quay.io/jetstack/cert-manager-webhook:v1.14.5
    >       image: quay.io/tigera/operator:v1.34.5
    >       image: registry.k8s.io/autoscaling/cluster-autoscaler:v1.30.0
    >       image: registry.k8s.io/cluster-api-aws/cluster-api-aws-controller:v2.5.2
    >       image: registry.k8s.io/cluster-api/cluster-api-controller:v1.7.4
    >     - image: docker.io/calico/pod2daemon-flexvol:v3.28.2
    ```
