# Changelog

## 0.17.0-0.8.0 (upcoming)

* Upcoming changelog

## 0.17.0-0.7.3 (2025-07-15)

* [PLT-2562] Fix `cluster-api-gcp` image reference during cluster creation
* [PLT-2389] Fix `cloud-provisioner` image reference during cluster creation

## 0.17.0-0.7.2 (2025-06-19)

* [PLT-2335] Fix `role_arn` configuration management when it is not enabled
* [PLT-2335] Bump cluster-operator to 0.5.2 version
* [PLT-2259] Refresh kubeconfig on cloud-provisioner deployment
* [PLT-1549] Activate NodePool SecureBoot
* [PLT-1762] [EKS] Soportar instalaciones con Assume Role
* [PLT-2226] Set private repository by default
* [PLT-2289] Add safe-to-evict annotations in Flux pods
* [PLT-2305][EKS] Asegurar la creación de la política de red en el namespace calico-system para permitir su salida

### Major changes & deprecations

* Docker registry and Helm repository are configured as `private` by default. They can be configured via `private_registry` and `private_helm_repo` in the cluster `ClusterConfig`

## 0.17.0-0.7.1 (2025-06-05)

* [PLT-2244] Disable setting CRIVolume by default
* [PLT-2108] Configurar Assume role (STS) de forma manual.
* [PLT-2099] Fix coredns PDB specification
* [PLT-2131] Improve workers checks during cloud-provisioner upgrade to avoid timeouts
* [PLT-2098] Improve kubernetes version checks during cloud-provisioner-upgrade
* [PLT-2124] Bump cluster-autoscaler to v1.32.0 version and its chart version to 9.46.6
* [PLT-2143] Bump cluster-operator to 0.5.1 version
* [PLT-2143] Support empty CRIVolume and ETCDVolume references in KubeadmControlPlane and AzureMachineTemplate templates
* [PLT-2244] Disable setting CRIVolume by default
* [PLT-2176] Enabling ControlPlaneKubeletLocalMode feature gate to avoid upgrade issues in Azure
* [PLT-1496] Ensure CAPG provisioner version references are set to 1.6.1-0.3.1
* [PLT-2204] Ensure referencing cloud-provisioner image release instead of prerelease version when creating a cluster

## 0.17.0-0.7.0 (2025-04-30)

* [PLT-1917] Support private registry during cloud-provisioner upgrades
* [PLT-1968] Fix cert-manager chart upgrade when using and oci Helm repository
* [PLT-1971] Fix upgrade when using a non oci Helm repository
* [PLT-1957] Fix aws-load-balancer-controller upgrade
* [PLT-1956] Improve cluster-operator backup and restore management during upgrade
* [PLT-1958] Improve aws-node ClusterRole patch exception handling during upgrade
* [PLT-1652] Allow skipping kubernetes intermediate version during upgrade
* [PLT-1887] Dynamic region describe
* [PLT-1849] Fix aws-load-balancer-controller annotation
* [PLT-1621] Add kubernetes 1.32 support
* [PLT-1741] Bump cluster-operator references to 0.5.0 version. Update EKS addons dependencies documentation
* [PLT-1682] Improve kindest/node and stratio-capi-image management
* [PLT-1317] Remove non-suported AKS, managed AWS and managed GCP references
* [PLT-1628] Fix capz images registry and repository references. Replace cloud-provider-azure
* [PLT-1394] Bump Flux version to 2.14.1
* [PLT-1393] Bump Tigera Operator version to v3.29.1
* [PLT-1628] Fix coredns, cluster-api-gcp and kube-rbac-proxy image registry and repository references
* [PLT-1332] [GKE] Validaciones parámetros GKE
* [PLT-1330] CMEK, SA & CIDRs
* [PLT-964] Validaciones nuevos parámetros GKE
* [PLT-1156] Add deny-all-egress-imds_gnetpol
* [PLT-1309] Update docker images requirements documentation. Include stratio-capi-image to cicd flow
* [PLT-719] Doc 0.5 to master
* [PLT-1178] fix aws-load-balancer-controller


## Previous development

### Branched to branch-0.17.0-0.6 (2024-10-25)

* [Core] Ensure CoreDNS replicas are assigned to different nodes
* [Core] Added the default creation of volumes for containerd, etcd and root, if not indicated in the keoscluster
* [Core] Support k8s v1.30
* [Core] Deprecated Kubernetes versions prior to 1.28
* [PLT-817] Bump Tigera Operator version to v3.28.2
* [PLT-965] Disable managed Monitoring and Logging
* [PLT-806] Support for private clusters on GKE
* [PLT-920] Added use-local-stratio-image flag to reuse local image
* [PLT-917] Replace coredns yaml files with a single coredns tmpl file
* [PLT-929] Removed calico installation as policy manager by helm chart in GKE
* [PLT-911] Support for Disable External Endpoint in GKE
* [PLT-923] Remove path /stratio from container image reference for kube-rbac-proxy image
* [PLT-992] Uncouple CAPX from cloud provisioner and allow to specify versions in clusterconfig 
* [PLT-988] Uncouple CAPX from Dockerfile
* [PLT-964] Add GKE Private Cluster Validations
