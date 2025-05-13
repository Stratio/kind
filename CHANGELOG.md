# Changelog

## 0.17.0-0.6.6 (2025-05-13)

* [PLT-2131] Improve workers checks during cloud-provisioner upgrade to avoid timeouts
* [PLT-2091] Ensure flux NetworkPolicy requirements during cloud-provisioner upgrade
* [PLT-2092] Avoid adopting non cloud-provisioner charts during cloud-provisioner upgrade
* [PLT-2098] Improve kubernetes version checks during cloud-provisioner-upgrade

## 0.17.0-0.6.5 (2025-04-25)

* [PLT-1917] Support private registry during cloud-provisioner upgrades
* [PLT-1968] Fix cert-manager chart upgrade when using and oci Helm repository
* [PLT-1971] Fix upgrade when using a non oci Helm repository
* [PLT-1957] Fix aws-load-balancer-controller upgrade
* [PLT-1956] Improve cluster-operator backup and restore management during upgrade
* [PLT-1958] Improve aws-node ClusterRole patch exception handling during upgrade

## 0.17.0-0.6.4 (2025-03-24)

* [PLT-1887] Dynamic region describe
* [PLT-1849] Fix aws-load-balancer-controller annotation
* [PLT-1621] Added new flag to support only provisioner upgrades

## 0.17.0-0.6.3 (2025-03-06)

* [PLT-1741] Bump cluster-operator references to 0.4.2 version. Update EKS addons dependencies documentation

## 0.17.0-0.6.2 (2025-02-26)

* [PLT-1628] Fix coredns image registry and repository references
* [PLT-1628] Fix cluster-api-gcp image registry and repository references
* [PLT-1628] Fix kube-rbac-proxy image registry and repository references
* [Core] Fix upgrade process

## 0.17.0-0.6.1 (2025-01-28)

* [PLT-1330] CMEK - Service accounts & Secondary CIDR ranges adaption to R4.7
* [Core] Fix [PLT-964]

## 0.17.0-0.6.0 (2024-11-11)

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
* [PLT-964] Add GKE Private Cluster Validations.

## 0.17.0-0.5.3 (2024-09-24)

* [Core] Adapted for GKE support

## 0.17.0-0.5.2 (2024-06-25)

* [Core] Delete cluster autoscaler references in the upgrade script

## 0.17.0-0.5.1 (2024-06-21)

* [Core] Ensure that keoscluster exists in the upgrade script
* [Core] Fix clusterctl move retries

## 0.17.0-0.5.0 (2024-06-10)

* [Core] Update runc golang module to fix GHSA-xr7r-f8xq-vfvv
* [Core] Improve command execution retries
* [Core] Uncouple chart installation from Dockerfile
* [Core] Support k8s v1.28
* [Core] Fix panic when keos_version is not defined
* [Core] Script the upgrade

## 0.17.0-0.4.0 (2024-02-22)

* [Core] Support offline deployments
* [Core] Added validation for regions
* [Core] Added infrastructure validations for azs, vpcs, subnets and k8s versions
* [Core] Upgrade go version to 1.20
* [Azure] Bump cluster-api-provider-azure to v1.11.4
* [Azure] Add priority class to NMI
* [Core] Bump cluster api to v1.5.3
* [Core] Enable scale from zero for node groups
* [Core] Added new CR ClusterConfig for cluster configurations
* [Core] Support OCI helm repositories
* [Core] Restrict the maximum number of unhealthy nodes in MachineHealthCheck
* [Core] Set custom maxUnhealthy for CP and workers
* [Core] Added default retrieval of the latest cluster-operator helm chart.
* [Core] Override the cluster-operator chart and image versions in clusterconfig
* [AWS][EKS] Support aws load balancer controller manager (optional)

## 0.17.0-0.3.7 (2024-01-31)

* [Azure] HotFix: Disable Azure cloud routes and fix Azure csi drivers in upgrade script
* [Azure] HotFix: Remove Azure cloud route table maintenance
* [Core] Downgrade CCM to match k8s version 1.26
* [Azure] Disable nodes CIDR in Azure
* [Internal] Add utility to upload keos installer docker images
* [Docs] Fix: EFS permissions
* [Docs] Add AWS details
* [Core] Fix: check if coredns PDB already exists before deploying

## 0.17.0-0.3.6 (2023-12-21)

* [Core] HotFix: storageclass.parameters.label validation

## 0.17.0-0.3.5 (2023-12-19)

* [Core] Change create_iam default behaviour (to false)
* [Docs] Add example full descriptor v1beta1
* [Docs] Update documentation
* [Core] Update upgrade script (upgrade-provisioner_v0.3.py)
* [Docs] Update required policies
* [Core] Add coredns PDB
* [Core] Add cluster-autoscaler annotations to evict local volumes (for coredns, metrics-server, calico, cloud-controllers and CSIs)

## 0.17.0-0.3.4 (2023-11-17)

* [Core] Conditionally increase replicas for capi controllers
* [Core] Add PDB and PriorityClass to capx components
* [Core] Fix authentication for helm repositories
* [Azure] Add PriorityClass to NMI components
* [Core] Add upgrade script from 0.2 to 0.3

## 0.17.0-0.3.3 (2023-10-11)

* [Core] Add remote command execution retries

## 0.17.0-0.3.2 (2023-09-29)

* [Core] Bump cluster-operator due to hotfix

## 0.17.0-0.3.1 (2023-09-28)

* [Core] Add status in KeosCluster
* [Azure] Bump Azure provider to 1.10.4

## 0.17.0-0.3.0 (2023-09-14)

* [Core] Customize coredns configuration
* [Core] Fix wait conditions for unmanaged clusters
* [AWS] Bump cluster-api-provider-aws to v2.2.0
* [AWS] Add clusterAPI capabilities for AWS VMs
* [AWS] Add EKS secrets encryption support
* [Azure] Add Azure file CSI driver
* [Azure] Bump cluster-api-provider-azure to v1.10.0: Fix Azure VMs load balancer health check
* [GCP] Bump cluster-api-provider-gcp to v1.4.0: Fix GCP VMs load balancer health check
* [Core] Bump cluster api to v1.5.1
* [Core] Bump Calico to v3.26.1
* [AWS] Bump cluster-api-provider-aws to v1.5.1
* [AWS] Bump clusterawsadm to v2.2.1
* [Azure] Bump cluster-api-provider-azure unmanaged to v1.9.8
* [Azure] Bump cluster-api-provider-azure managed to v1.10.3

## 0.17.0-0.2.0 (2023-07-03)

* Add clusterAPI capabilities for AKS
* Add clusterAPI capabilities for Azure VMs

## 0.17.0-0.1.0 (2023-03-31)

* Add clusterAPI capabilities for EKS

## Previous development

