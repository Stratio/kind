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
4. [Upgrade `k8s` to `1.27`](#upgrade-k8s-to-127)
5. [Upgrade `k8s` to `1.28`](#upgrade-k8s-to-128)

### Validations and set-up

* User `flags`.
* Required installed `binaries` validation.
* Required `files` validation:
  * Descriptor. Defaults to `cluster.yaml`
  * Secrets. Defaults to `secrets.yml`
  * Kubeconfig. Defaults to `KUBECONFIG` env or `~/.kube/config`
* Required `kubernetes` `resources` validation:
  * The `Cluster`
* Upgrade path validation:
  * Check whether `EKS` or `Azure VMs`
  * Check `--skip-k8s-intermediate-version` only if `EKS`
  * `cluster-operator` upgrade version
* Helm repository set-up
* Environment variables set-up

### Backup

* `CAPX` files
* `Capsule` files

### Pre-upgrade tasks

* Prepare `capsule` <!-- TODO: Revisar -->
  * Add `clouds` components to `capsule-mutating-webhook-configuration` `objectSelector`
* Install `aws-load-balancer-controller` (Only if `--enable-lb-controller`)
* Upgrade `cluster-operator`
  * Apply new `CRDs`
  * Upgrade Helm chart
* Delete `cluster-operator` webhooks (only if `--skip-k8s-intermediate-version`)
* Restore `capsule` <!-- TODO: Revisar -->
  * Restore `capsule-mutating-webhook-configuration` `objectSelector`

### Upgrade `k8s` to `1.27`

> [!IMPORTANT]
> This step is skipped when using `--skip-k8s-intermediate-version` flag

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
* Restore `allow-all-traffic-from-control-plane` `GNP` (only in `Azure VM`)

### Upgrade `k8s` to `1.28`

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
* Restore `allow-all-traffic-from-control-plane` `GNP` (only in `Azure VM`)
* Restore `cluster-operator` webhooks (only if `--skip-k8s-intermediate-version`)

## Known issues

There is no current known issues.

## Notes

* Since the `upgrade-provisioner.py` script is distributed as it is, the human operator must be ensure its requirements before using it. These requirements are documented in the [stratio-docs upgrade documentation](../../stratio-docs/en/modules/operations-manual/pages/operations-manual.adoc#stratio-cloud-provisioner-upgrade-to-05).

The easiest way to achieve this is executing the `upgrade-provisioner.py` script from the cluster `keos-installer` container. In this case the only requirement missing is the `clusterctl` client which can be installed in the `keos-installer` container:

```bash
export CLUSTERCTL=v1.5.3
curl -L https://github.com/kubernetes-sigs/cluster-api/releases/download/${CLUSTERCTL}/clusterctl-linux-amd64 -o /usr/local/bin/clusterctl \
    && chmod +x /usr/local/bin/clusterctl
```

* During the upgrade process some charts and images are upgraded:

  * EKS
    * Charts
    ```bash
    $ diff eks-charts-0.4.txt eks-charts-0.4-0.5.txt

    < cluster-operator    kube-system     1         2025-03-21 10:13:24.979083086 +0000 UTC deployed  cluster-operator-0.2.0    0.2.0      
    ---
    > cluster-operator    kube-system     3         2025-03-21 11:01:17.138098065 +0000 UTC deployed  cluster-operator-0.3.7    0.3.7
    ```
    * Images
    ```bash
    $ diff eks-images-0.4.txt eks-images-0.4-0.5.txt
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/amazon-k8s-cni-init:v1.12.6-eksbuild.2
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/amazon-k8s-cni:v1.12.6-eksbuild.2
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/aws-ebs-csi-driver:v1.19.0
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/coredns:v1.9.3-eksbuild.3
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-attacher:v4.3.0-eks-1-27-3
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-node-driver-registrar:v2.8.0-eks-1-27-3
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-provisioner:v3.5.0-eks-1-27-3
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-resizer:v1.8.0-eks-1-27-3
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-snapshotter:v6.2.1-eks-1-27-3
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/kube-proxy:v1.26.7-minimal-eksbuild.2
    <       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/livenessprobe:v2.10.0-eks-1-27-3
    <       image: <cluster_docker_registry>/stratio/cluster-operator:0.2.0
    ---
    >       image: 066635153087.dkr.ecr.il-central-1.amazonaws.com/eks/kube-proxy:v1.28.15-minimal-eksbuild.9
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/amazon-k8s-cni-init:v1.19.2-eksbuild.5
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/amazon-k8s-cni:v1.19.2-eksbuild.5
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/amazon/aws-network-policy-agent:v1.1.6-eksbuild.2
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/aws-ebs-csi-driver:v1.29.1
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/coredns:v1.10.1-eksbuild.18
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-attacher:v4.5.0-eks-1-29-7
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-node-driver-registrar:v2.10.0-eks-1-29-7
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-provisioner:v4.0.0-eks-1-29-7
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-resizer:v1.10.0-eks-1-29-7
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/csi-snapshotter:v7.0.1-eks-1-29-7
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/kube-proxy:v1.28.15-minimal-eksbuild.9
    >       image: 602401143452.dkr.ecr.eu-west-1.amazonaws.com/eks/livenessprobe:v2.12.0-eks-1-29-7
    >       image: <cluster_docker_registry>/stratio/cluster-operator:0.3.7
    ```
