# Get the Azure AD Application ID and Role Assignments

```bash

❯ az ad app list --query "[?contains(displayName, 'cloud-provisioner')].{name:displayName, appId:appId} | sort_by(@, &name)" --output table
Name                              AppId
--------------------------------  ------------------------------------
cloud-provisioner                 7cf3ce06-2689-4c17-b2da-09df11fb725b  
cloud-provisioner-restricted      e979a59d-ba11-4fcc-b174-64cfab548943  
cloud-provisioner-restricted-aks  e3b6e5b9-e729-4612-a3cf-4bda74c240c4  

❯ az role assignment list --all --assignee 7cf3ce06-2689-4c17-b2da-09df11fb725b --query "[].{Principal:principalName, Role:roleDefinitionName, Scope:scope}" --output table
Principal                             Role         Scope
------------------------------------  -----------  ---------------------------------------------------------------------------------------------------------------------------------------
7cf3ce06-2689-4c17-b2da-09df11fb725b  Contributor  /subscriptions/6e2a38cd-ef16-47b3-a75e-5a4960cedf65
7cf3ce06-2689-4c17-b2da-09df11fb725b  AcrPull      eosregistry
7cf3ce06-2689-4c17-b2da-09df11fb725b  AcrPull      offlineregistry
7cf3ce06-2689-4c17-b2da-09df11fb725b  AcrPush      offlineregistry

❯ az role assignment list --all --assignee e979a59d-ba11-4fcc-b174-64cfab548943 --query "[].{Principal:principalName, Role:roleDefinitionName, Scope:scope}" --output table
Principal                             Role                  Scope
------------------------------------  --------------------  -----------------------------------------------------------------------
e979a59d-ba11-4fcc-b174-64cfab548943  capz-role-restricted  /subscriptions/6e2a38cd-ef16-47b3-a75e-5a4960cedf65/resourceGroups/capz
e979a59d-ba11-4fcc-b174-64cfab548943  capz-role-restricted  /subscriptions/6e2a38cd-ef16-47b3-a75e-5a4960cedf65

❯ az role assignment list --all --assignee e3b6e5b9-e729-4612-a3cf-4bda74c240c4 --query "[].{Principal:principalName, Role:roleDefinitionName, Scope:scope}" --output table
Principal                             Role                      Scope
------------------------------------  ------------------------  -----------------------------------------------------------------------
e3b6e5b9-e729-4612-a3cf-4bda74c240c4  capz-role-restricted-aks  /subscriptions/6e2a38cd-ef16-47b3-a75e-5a4960cedf65/resourceGroups/capz
e3b6e5b9-e729-4612-a3cf-4bda74c240c4  capz-role-restricted-aks  /subscriptions/6e2a38cd-ef16-47b3-a75e-5a4960cedf65

# Get roles permissions

❯ az role definition list --name "Contributor" --output table
Name         Type                                     Description
-----------  ---------------------------------------  -------------------------------------------------------------------------------------------------------------------------------------------------------------------
Contributor  Microsoft.Authorization/roleDefinitions  Grants full access to manage all resources, but does not allow you to assign roles in Azure RBAC, manage assignments in Azure Blueprints, or share image galleries.

❯ az role definition list --name "AcrPull" --output table
Name         Type                                   Description
-------  ---------------------------------------    -------------
AcrPush  Microsoft.Authorization/roleDefinitions    acr pull

❯ az role definition list --name "AcrPush" --output table
Name         Type                                   Description
-------  ---------------------------------------    -------------
AcrPull  Microsoft.Authorization/roleDefinitions    acr push

❯ az role definition list --name "capz-role-restricted" --output table
Name         Type                                   Description
-------  ---------------------------------------    -------------
capz-role-restricted  Microsoft.Authorization/roleDefinitions  Restricted role for cloud-provisioner unmanaged

❯ az role definition list --name "capz-role-restricted-aks" --output table
Name                      Type                                     Description
------------------------  ---------------------------------------  ----------------------------
capz-role-restricted-aks  Microsoft.Authorization/roleDefinitions  Restricted role for cloud-provisioner aks

```

# Managed Identity

## Azure Unmanaged

> capz-controlplane  
>> Role: capz-role-controlplane
```bash
# Get json permissions
❯ az role definition list --name "capz-role-controlplane"
```

> capz-agentpool-restricted
>> Role: capz-role-node
```bash
# Get json permissions
❯ az role definition list --name "capz-role-node"
```

## Azure Managed

> capz-aks-controlplane  
>> Role: capz-aks-role-controlplane
```bash
# Get json permissions
❯ az role definition list --name "capz-aks-role-controlplane"
```

> capz-aks-agp-restricted
>> Role: capz-aks-role-node
```bash
# Get json permissions
❯ az role definition list --name "capz-aks-role-node"
```

# Relationship Table
| Scope   |	Application |	ClientID    |	Type	|   Managed Identity    |	Role    |	spec    |
| ---     |	---         |	---         |	---	|   ---                 |	---     |	---        |
| ALL	    |Cloud-provisioner|7cf3ce06-2689-4c17-b2da-09df11fb725b|Development|capz-agentpool|AcrPull, Managed Identity Operator, Contributor|credentials.azure.client_id|
| Azure managed|cloud-provisioner-restricted-aks|e3b6e5b9-e729-4612-a3cf-4bda74c240c4|Production|capz-aks-cloud-provisioner|capz-role-restricted-aks|credentials.azure.client_id|
| Azure managed|cloud-provisioner-restricted-aks|e3b6e5b9-e729-4612-a3cf-4bda74c240c4|Production|capz-aks-controlplane|capz-aks-role-controlplane|security.control_plane_identity|
| Azure managed|cloud-provisioner-restricted-aks|e3b6e5b9-e729-4612-a3cf-4bda74c240c4|Production|capz-aks-agp-restricted|capz-aks-role-node|security.nodes_identity|
| Azure unmanaged|cloud-provisioner-restricted|e979a59d-ba11-4fcc-b174-64cfab548943|Production|capz-cloud-provisioner|capz-role-restricted|credentials.azure.client_id|
| Azure unmanaged|cloud-provisioner-restricted|e979a59d-ba11-4fcc-b174-64cfab548943|Production|capz-controlplane|capz-role-controlplane|security.control_plane_identity|
| Azure unmanaged|cloud-provisioner-restricted|e979a59d-ba11-4fcc-b174-64cfab548943|Production|capz-agentpool-restricted|capz-role-node|security.nodes_identity|