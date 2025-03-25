# Changelog

## 0.17.0-0.7.0 (2025-03-25)

* [PLT-1652] Allow skipping kubernetes intermediate version during upgrade
* [PLT-1887] -  Dynamic region describe
* [PLT-1849] -  Fix aws-load-balancer-controller annotation
* [PLT-1762] -  [EKS] Soportar instalaciones con Assume Role
* [PLT-1621] -  Add kubernetes 1.32 support  - [`#689`](https://github.com/Stratio/kind/pull/689)
* [PLT-1741] -  Bump cluster-operator references to 0.5.0 version. Update EKS addons dependencies documentation  - [`#701`](https://github.com/Stratio/kind/pull/701)
* [PLT-1682] -  Improve kindest/node and stratio-capi-image management  - [`#685`](https://github.com/Stratio/kind/pull/685)
* [PLT-1317] -  Remove non-suported AKS, managed AWS and managed GCP references  - [`#692`](https://github.com/Stratio/kind/pull/692)
* [PLT-1628] -  Fix capz images registry and repository references. Replace cloud-provider-azure …  - [`#686`](https://github.com/Stratio/kind/pull/686)
* [PLT-1394] -  Bump Flux version to 2.14.1  - [`#662`](https://github.com/Stratio/kind/pull/662)
* [PLT-1393] -  Bump Tigera Operator version to v3.29.1  - [`#661`](https://github.com/Stratio/kind/pull/661)
* [PLT-1628] -  Fix coredns, cluster-api-gcp and kube-rbac-proxy image registry and repository references  - [`#675`](https://github.com/Stratio/kind/pull/675)
* [PLT-1332] -  [GKE] Validaciones parámetros GKE  - [`#657`](https://github.com/Stratio/kind/pull/657)
* [PLT-1330] -  CMEK, SA & CIDRs  - [`#642`](https://github.com/Stratio/kind/pull/642)
* [PLT-964] -  Validaciones nuevos parámetros GKE  - [`#626`](https://github.com/Stratio/kind/pull/626)
* [PLT-1156] -  Add deny-all-egress-imds_gnetpol  - [`#629`](https://github.com/Stratio/kind/pull/629)
* [PLT-1309] -  Update docker images requirements documentation. Include stratio-capi-image to cicd flow  - [`#663`](https://github.com/Stratio/kind/pull/663)
* [PLT-719] -  Doc 0.5 to master  - [`#645`](https://github.com/Stratio/kind/pull/645)
* [PLT-1178] -  fix aws-load-balancer-controller  - [`#640`](https://github.com/Stratio/kind/pull/640)

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



