# AZURE Permissions

Requirements:
- Service Principal (cloud-provisioner credentials)
  - Application Id = descriptor (client_id)
  - AAD - Application Secret ID = descriptor (client_secret)
- Resource group (capz for example) (control-plane and workers identities)
  - Managed Identity (capz-agentpool-restricted) --> Azure roles Assignments (capz-test-role-node)
  - Managed Identity (capz-control-plane-restricted) --> Azure roles Assignments (capz-test-role-control-plane)

### Permissions Table

**Test:** cloud-provisioner create cluster --name jazure --vault-password "123456"  -d cluster-aks.yaml --delete-previous --avoid-creation  

| Permission | Needed for | Description | Resource | Application |
| --- | --- | --- | --- | --- |
| Microsoft.ContainerRegistry/registries/pull/read | Get ACR auth token | Failed to obtain the ACR token with the provided credentials | Microsoft.ContainerRegistry | Provisioner |

**Test:** cloud-provisioner create cluster --name jazure --retain --vault-password 123456 --keep-mgmt (CAPZ)

| Permission | Needed for | Description | Resource | Application |
| --- | --- | --- | --- | --- |
| Microsoft.Resources/subscriptions/resourcegroups/read | Get ResourceGroup | does not have authorization to perform action 'Microsoft.Resources/subscriptions/resourcegroups/read' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx | Microsoft.Resources | Provisioner | 
| Microsoft.Resources/subscriptions/resourcegroups/write | Create ResourceGroup | does not have authorization to perform action 'Microsoft.Resources/subscriptions/resourcegroups/write' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx | Microsoft.Resources | Provisioner |
| Microsoft.Network/virtualNetworks/read | Get VirtualNetwork | does not have authorization to perform action 'Microsoft.Network/virtualNetworks/read' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.Network/virtualNetworks/jazure | Microsoft.Network | Provisioner |
| Microsoft.Resources/tags/read | Get Tags | does not have authorization to perform action 'Microsoft.Resources/tags/read' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.Network/virtualNetworks/jazure/providers/Microsoft.Resources/tags/default | Microsoft.Resources | Provisioner |
| Microsoft.Network/virtualNetworks/write | Create VirtualNetwork | does not have authorization to perform action 'Microsoft.Network/virtualNetworks/write' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.Network/virtualNetworks/jazure | Microsoft.Network | Provisioner |
| Microsoft.Network/virtualNetworks/subnets/read | Get Subnet | does not have authorization to perform action 'Microsoft.Network/virtualNetworks/subnets/read' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.Network/virtualNetworks/jazure/subnets/jazure | Microsoft.Network | Provisioner |
| Microsoft.Network/virtualNetworks/subnets/write | Create Subnet | does not have authorization to perform action 'Microsoft.Network/virtualNetworks/subnets/write' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.Network/virtualNetworks/jazure/subnets/jazure | Microsoft.Network | Provisioner |
| Microsoft.ContainerService/managedClusters/read | Get AKS Cluster | does not have authorization to perform action 'Microsoft.ContainerService/managedClusters/read' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.ContainerService/managedClusters/jazure | Microsoft.ContainerService | Provisioner |
| Microsoft.ContainerService/managedClusters/write | Create AKS Cluster | does not have authorization to perform action 'Microsoft.ContainerService/managedClusters/write' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.ContainerService/managedClusters/jazure | Microsoft.ContainerService | Provisioner |
| Microsoft.Network/virtualNetworks/subnets/join/action | Join Subnet | does not have authorization to perform action 'Microsoft.Network/virtualNetworks/subnets/join/action' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.Network/virtualNetworks/jazure/subnets/jazure | Microsoft.Network | Provisioner |
| Microsoft.ManagedIdentity/userAssignedIdentities/assign/action | userAssignedIdentities assign | does not have permission to perform action 'Microsoft.ManagedIdentity/userAssignedIdentities/assign/action' | Microsoft.ManagedIdentity | Provisioner |
| Microsoft.ContainerService/managedClusters/listClusterAdminCredential/action | listClusterAdminCredential | does not have permission to perform action 'Microsoft.ContainerService/managedClusters/listClusterAdminCredential/action' | Microsoft.ContainerService | Provisioner |
| Microsoft.ContainerService/managedClusters/agentPools/read | Get AKS AgentPool | does not have authorization to perform action 'Microsoft.ContainerService/managedClusters/agentPools/read' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.ContainerService/managedClusters/jazure/agentPools/jazure1mp1 | Microsoft.ContainerService | Provisioner |
| Microsoft.Compute/virtualMachineScaleSets/* | Failed to find scale set over resource group machine pool | failed to find vm scale set in resource group jazure-nodes matching pool named jazure1mp0 | Microsoft.Compute | Provisioner |
| "Microsoft.ManagedIdentity/userAssignedIdentities/*/read" "Microsoft.ManagedIdentity/userAssignedIdentities/*/assign/action" "Microsoft.Authorization/*/read" "Microsoft.Insights/alertRules/*" "Microsoft.Resources/subscriptions/resourceGroups/read" "Microsoft.Resources/deployments/*" "Microsoft.Support/*" | failed to reconcile AzureManagedControlPlane | The cluster using user-assigned managed identity must be granted 'Managed Identity Operator' role to assign kubelet identity. | Microsoft.ManagedIdentity Microsoft.Insights Microsoft.Resources Microsoft.Support | Provisioner |
| Microsoft.ContainerRegistry/registries/pull/read | Get ACR auth token | Failed to authorize: failed to fetch anonymous token | Microsoft.ContainerRegistry | Provisioner |

**Test:** cloud-provisioner create cluster --name jazure --retain --vault-password 123456 (same permissions as --keep-mgmt) (same as above)  

**Test:** clusterctl move --kubeconfig remote_kubeconfig --to-kubeconfig local_kubeconfig --namespace cluster-jazure --dry-run    
**Note:** Stop cluster-operator before perform the operation (kw scale deploy keoscluster-controller-manager --replicas 0 -n kube-system)  
Performing move...
********************************************************
This is a dry-run move, will not perform any real action
********************************************************
Discovering Cluster API objects  
Moving Cluster API objects Clusters=1  
Moving Cluster API objects ClusterClasses=0  
Creating objects in the target cluster  
Deleting objects from the source cluster  

❯ clusterctl move --kubeconfig remote_kubeconfig --to-kubeconfig local_kubeconfig --namespace cluster-jazure (no needed additonal permissions)  
**Note:** Stop cluster-operator before perform the operation (k scale deploy keoscluster-controller-manager --replicas 0 -n kube-system)  
Performing move...  
Discovering Cluster API objects  
Moving Cluster API objects Clusters=1  
Moving Cluster API objects ClusterClasses=0  
Creating objects in the target cluster  
Deleting objects from the source cluster  

❯ clusterctl move --to-kubeconfig remote_kubeconfig --kubeconfig local_kubeconfig --namespace cluster-jazure
**Note:** start cluster-operator after perform the operation (k scale deploy keoscluster-controller-manager --replicas 2 -n kube-system)
Performing move...  
Discovering Cluster API objects  
Moving Cluster API objects Clusters=1  
Moving Cluster API objects ClusterClasses=0  
Creating objects in the target cluster  
Deleting objects from the source cluster  

❯ clusterctl --kubeconfig /home/jnovoa/.kube/configs/remote_kubeconfig describe cluster jazure -n cluster-jazure
NAME                                                  READY  SEVERITY  REASON  SINCE  MESSAGE
Cluster/jazure                                        True                     24m
├─ClusterInfrastructure - AzureManagedCluster/jazure
├─ControlPlane - AzureManagedControlPlane/jazure      True                     24m
└─Workers
  ├─MachinePool/jazure1-mp-0                          True                     94s
  ├─MachinePool/jazure1-mp-1                          True                     94s
  └─MachinePool/jazure1-mp-2                          True                     94s

**Test:** scale up with cluster-operator

| Permission | Needed for | Description | Resource | Application |
| --- | --- | --- | --- | --- |
| Microsoft.ContainerService/managedClusters/agentPools/write | Scale up | does not have authorization to perform action 'Microsoft.ContainerService/managedClusters/agentPools/write' over scope '/subscriptions/6e2a38cd-ef16-47b3-a75e-5a4960cedf65/resourceGroups/jazure/providers/Microsoft.ContainerService/managedClusters/jazure/agentPools/jazure1mp1' | Microsoft.ContainerService | Provisioner |

| NAME |      CLUSTER  | DESIRED |  REPLICAS |  PHASE  |   AGE   |  VERSION
| --- | --- | --- | --- | --- | --- | --- |
| jazure1-mp-0  | jazure  |  1  |       1    |      Running    | 7m11s  | v1.26.3 |
| jazure1-mp-1  | jazure  |  1  |       1    |      Running    | 7m11s  | v1.26.3 |
| jazure1-mp-2  | jazure  |  2  |       1    |      ScalingUp  | 7m10s  | v1.26.3 |

**Test:** Destroy Machine on Azure UI (self-healing) (azure1w1-md-2) (same as above)

| NAMESPACE | NAME | CLUSTER | REPLICAS | READY | UPDATED | UNAVAILABLE | PHASE  | AGE | VERSION
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cluster-azure1 | azure1w1-md-0 | azure1 | 1 | 1 | 1 | |0 | Running | 26m | v1.24.10 |
| cluster-azure1 | azure1w1-md-1 | azure1 | 1 | 1 | 1 | |0 | Running | 26m | v1.24.10 |
| cluster-azure1 | azure1w1-md-2 | azure1 | 1 | 1 | 1 | |0 |  ScalingUp| 64m | v1.24.10 |
| cluster-azure1 | azure1w1-md-3 | azure1 | 1 | 1 | 1 | |0 | Running | 48s | v1.24.10 |

**Test:** kubectl --kubeconfig local_kubeconfig -n cluster-azure1 patch KubeadmControlPlane azure1-control-plane --type merge -p '{"spec":{"version":"v1.25.9"}}' 
(upgrade control-plane k8s version from 1.24.10 to 1.25.9) (no more permissions needed)

**Test:** kubectl --kubeconfig local_kubeconfig -n cluster-azure1 patch machinedeployments.cluster.x-k8s.io azure1w1-md-0 --type merge -p '{"spec":{"template":{"spec":{"version":"v1.25.9"}}}}'  (no more permissions needed)  

| NAME | CLUSTER | REPLICAS | READY | UPDATED | UNAVAILABLE | PHASE | AGE | VERSION |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| azure1w1-md-0 |  azure1 | 1 | 1 | 1 | 0 | Running | 17m | v1.24.10  |
| azure1w1-md-1 |  azure1 | 1 | 1 | 1 | 0 | Running | 17m | v1.24.10  |
| azure1w1-md-2 |  azure1 | 1 | 1 | 1 | 0 | Running | 17m | v1.24.10  |

| NAME | CLUSTER | REPLICAS | READY | UPDATED | UNAVAILABLE | PHASE | AGE | VERSION |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| azure1w1-md-0 | azure1 | 2 | 2 | 1 | 0 | Running     | 18m | v1.25.9  |
| azure1w1-md-1 | azure1 | 1 | 1 | 1 | 0 | Running     | 18m | v1.24.10 |
| azure1w1-md-2 | azure1 | 1 | 1 | 1 | 0 | Running     | 18m | v1.24.10 |

| NAME | CLUSTER | REPLICAS | READY | UPDATED | UNAVAILABLE | PHASE | AGE | VERSION |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| azure1w1-md-0 | azure1 | 1 | 1 | 1 | 0 | Running | 21m | v1.25.9  |
| azure1w1-md-1 | azure1 | 1 | 1 | 1 | 0 | Running | 21m | v1.24.10 | 
| azure1w1-md-2 | azure1 | 1 | 1 | 1 | 0 | Running | 21m | v1.24.10 | 


**Test:** Delete cluster (From local container)
kubectl --kubeconfig local_kubeconfig -n cluster-azure1 delete cluster azure1

| Permission | Needed for | Description | Resource | Application |
| --- | --- | --- | --- | --- |
| Microsoft.Resources/subscriptions/resourcegroups/delete | Delete ResourceGroup | does not have authorization to perform action 'Microsoft.Resources/subscriptions/resourcegroups/delete' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx | Microsoft.Resources | Provisioner |
| Microsoft.Compute/disks/delete | Delete disks | does not have authorization to perform action 'Microsoft.Compute/disks/delete' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.Compute/disks/xxxxxx-md-1-mdqww_OSDisk | Microsoft.Compute | Provisioner |
| Microsoft.Network/virtualNetworks/delete | Delete virtualNetworks | does not have authorization to perform action 'Microsoft.Network/virtualNetworks/delete' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.Network/virtualNetworks/xxxxxx-vnet | Microsoft.Network | Provisioner |
| Microsoft.Network/networkSecurityGroups/delete | Delete networkSecurityGroups | does not have authorization to perform action 'Microsoft.Network/networkSecurityGroups/delete' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.Network/networkSecurityGroups/xxxxxx-node-nsg | Microsoft.Network | Provisioner |
| Microsoft.Network/routeTables/delete | Delete routeTables | does not have authorization to perform action 'Microsoft.Network/routeTables/delete' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.Network/routeTables/xxxxxx-node-routetable | Microsoft.Network | Provisioner |
| Microsoft.Network/publicIPAddresses/delete | Delete publicIPAddresses | does not have authorization to perform action 'Microsoft.Network/publicIPAddresses/delete' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.Network/publicIPAddresses/pip-xxxxxx-node-natgw-1 | Microsoft.Network | Provisioner |
| Microsoft.Network/natGateways/delete | Delete natGateways | does not have authorization to perform action 'Microsoft.Network/natGateways/delete' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.Network/natGateways/xxxxxx-node-natgw-1 | Microsoft.Network | Provisioner |
| Microsoft.Network/virtualNetworks/subnets/delete | Delete subnets | does not have authorization to perform action 'Microsoft.Network/virtualNetworks/subnets/delete' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.Network/virtualNetworks/xxxxxx-vnet/subnets/node-subnet | Microsoft.Network | Provisioner |
| Microsoft.Network/loadBalancers/delete | Delete loadBalancers | does not have authorization to perform action 'Microsoft.Network/loadBalancers/delete' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.Network/loadBalancers/xxxxxx-public-lb | Microsoft.Network | Provisioner |
| Microsoft.Network/networkInterfaces/delete | Delete networkInterfaces | does not have authorization to perform action 'Microsoft.Network/networkInterfaces/delete' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.Network/networkInterfaces/xxxxxx-control-plane-8xhk2-nic | Microsoft.Network | Provisioner |
| Microsoft.Compute/virtualMachines/delete | Delete virtualMachines | does not have authorization to perform action 'Microsoft.Compute/virtualMachines/delete' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.Compute/virtualMachines/xxxxxx-control-plane-8xhk2 | Microsoft.Compute | Provisioner |

**Test**: Keos Install

| Permission | Needed for | Description | Resource | Application |
| --- | --- | --- | --- | --- |
| kubelet Failed to pull image "eosregistry.azurecr.io/keos/stratio/capsule:0.1.1-0.3.1 | pull image "eosregistry.azurecr.io/keos/stratio/capsule:0.1.1-0.3.1 | Microsoft.ContainerRegistry/registries/pull/read | Microsoft.ContainerRegistry/registries/pull/read | keos (workers) |
| Microsoft.Network/publicIPAddresses/write | Create publicIPAddresses | does not have authorization to perform action 'Microsoft.Network/publicIPAddresses/write' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.Network/publicIPAddresses/azure1-a3832d7641b0f422cadfe775c6e96cb9 | Microsoft.Network | keos (Control-plane) |
| Microsoft.Compute/disks/read | /subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/azure1/providers/Microsoft.Compute/disks/pvc-4816139c-df5c-43c9-bc51-394164b1522c | Microsoft.Compute | keos (Control-plane & workers) |
| Microsoft.Compute/disks/write | Create disks | does not have authorization to perform action 'Microsoft.Compute/disks/write' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/xxxxxx/providers/Microsoft.Compute/disks/pvc-4816139c-df5c-43c9-bc51-394164b1522c | Microsoft.Compute | keos (Control-plane & workers) |
| Microsoft.Network/dnsZones/read | Read dnsZones | does not have authorization to perform action 'Microsoft.Network/dnsZones/read' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/azure1/providers/Microsoft.Network/dnsZones/domain.ext' | Microsoft.Network | keos (Control-plane) |
| Microsoft.Network/privateDnsZones/read | Read privateDnsZones | does not have authorization to perform action 'Microsoft.Network/privateDnsZones/read' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/azure1/providers/Microsoft.Network/privateDnsZones/domain.ext' | Microsoft.Network | keos (Control-plane) |
| Microsoft.Network/publicIPAddresses/read | Read publicIPAddresses | does not have authorization to perform action 'Microsoft.Network/publicIPAddresses/read' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/azure1/providers/Microsoft.Network/publicIPAddresses/azure1-a3832d7641b0f422cadfe775c6e96cb9 | Microsoft.Network | keos (Control-plane) |
| Microsoft.Network/publicIPAddresses/write | Create publicIPAddresses | does not have authorization to perform action 'Microsoft.Network/publicIPAddresses/write' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/azure1/providers/Microsoft.Network/publicIPAddresses/azure1-a3832d7641b0f422cadfe775c6e96cb9 | Microsoft.Network | keos (Control-plane) |
| Microsoft.Network/networkSecurityGroups/read | Read networkSecurityGroups | does not have authorization to perform action 'Microsoft.Network/networkSecurityGroups/read' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/azure1/providers/Microsoft.Network/dnsZones/domain.ext' | Microsoft.Network | keos (Control-plane) |
| Microsoft.Network/loadBalancers/write | Create loadBalancers | does not have authorization to perform action 'Microsoft.Network/loadBalancers/write' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/azure1/providers/Microsoft.Network/loadBalancers/azure1' | Microsoft.Network | keos (Control-plane) |
| Microsoft.Network/loadBalancers/read | Read loadBalancers | does not have authorization to perform action 'Microsoft.Network/loadBalancers/read' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/azure1/providers/Microsoft.Network/loadBalancers/azure1' | Microsoft.Network | keos (Control-plane) |
| Microsoft.Network/networkInterfaces/read | Read networkInterfaces | does not have authorization to perform action 'Microsoft.Network/networkInterfaces/read' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/azure1/providers/Microsoft.Network/networkInterfaces/azure1w1-md-2-c59n2-nic' | Microsoft.Network | keos (Control-plane) |
| Microsoft.Network/networkInterfaces/write | Create networkInterfaces | does not have authorization to perform action 'Microsoft.Network/networkInterfaces/write' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/azure1/providers/Microsoft.Network/networkInterfaces/azure1w1-md-2-c59n2-nic' | Microsoft.Network | keos (Control-plane) |
| Microsoft.Network/virtualNetworks/subnets/join/action | Join subnets | does not have authorization to perform action 'Microsoft.Network/virtualNetworks/subnets/join/action' over scope '/subscriptions/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/resourceGroups/azure1/providers/Microsoft.Network/virtualNetworks/azure1/subnets/node-subnet' | Microsoft.Network | keos (Control-plane) |
