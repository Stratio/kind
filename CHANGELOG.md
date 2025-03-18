# Changelog

## 0.17.0-0.8.0 (upcoming)

* Pending changelog

## Previous development

### Branched to branch-0.17.0-0.7 (2025-03-18)

* [PLT-1621] Support k8s v1.32
* [PLT-1394] Bump Flux chart version to 2.14.1
* [PLT-1393] Bump Tigera Operator version to v3.29.1
* [PLT-1741] Bump cluster-operator references to 0.5.0 version. Update EKS addons dependencies documentation



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



