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
e979a59d-ba11-4fcc-b174-64cfab548943  capz-role-keos-dns    /subscriptions/6e2a38cd-ef16-47b3-a75e-5a4960cedf65

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

❯ az role definition list --name "capz-role-keos-dns" --output table
Name                Type                                     Description
------------------  ---------------------------------------  -----------------------------------------------------------------------------------
capz-role-keos-dns  Microsoft.Authorization/roleDefinitions  Restricted role for the keos-installer external-dns prerequisite (Azure unmanaged)

```

# capz-role-keos-dns + Microsoft Graph (keos external-dns prerequisite)

The keos-installer external-dns prerequisite for Azure
([installation-prerequisites.adoc, branch-1.4](https://github.com/Stratio/keos-installer/blob/branch-1.4/stratio-docs/es/modules/operations-guide/pages/installation/installation-prerequisites.adoc?plain=1#L152-L221))
creates the public and private DNS zones, links the private zone to the cluster VNet, creates the external-dns
Service Principal and gives it Reader + Contributor on both zones. To run it with the deployment user
`cloud-provisioner-restricted` (`e979a59d-…`) that user needs, **on top of `capz-role-restricted`**:

1. The Azure RBAC role `capz-role-keos-dns` (DNS zones, VNet link, role assignments).
2. The Microsoft Graph application permission `Application.ReadWrite.OwnedBy` (creating the external-dns Service Principal). Graph permissions can't go into an Azure RBAC role.

`capz-role-keos-dns` is a complement, not a standalone role: the reads it relies on (`Microsoft.Network/dnsZones/read`,
`Microsoft.Network/privateDnsZones/read`, `Microsoft.Network/virtualNetworks/read`, `Microsoft.Authorization/*/read`)
come from `capz-role-restricted`. Tested only with both roles assigned (PLT-5022, 2026-10-15).

## Role definition

```json
{
  "Name": "capz-role-keos-dns",
  "Description": "Restricted role for the keos-installer external-dns prerequisite (Azure unmanaged)",
  "Actions": [
    "Microsoft.Network/privateDnsZones/write",
    "Microsoft.Network/privateDnsZones/delete",
    "Microsoft.Network/privateDnsZones/virtualNetworkLinks/read",
    "Microsoft.Network/privateDnsZones/virtualNetworkLinks/write",
    "Microsoft.Network/privateDnsZones/virtualNetworkLinks/delete",
    "Microsoft.Network/dnsZones/write",
    "Microsoft.Network/dnsZones/delete",
    "Microsoft.Network/virtualNetworks/join/action",
    "Microsoft.Authorization/roleAssignments/write",
    "Microsoft.Authorization/roleAssignments/delete"
  ],
  "AssignableScopes": ["/subscriptions/<subscription-id>"]
}
```

```bash
# create (Owner / User Access Administrator on the subscription)
❯ az role definition create --role-definition @capz-role-keos-dns.json

# update: same JSON, keep "Name" and do NOT add "Id" — with "Id" az 2.58 also demands "roleName"
# and fails with KeyError: 'roleName' (azure/cli/command_modules/role/custom.py:98-110)
❯ az role definition update --role-definition @capz-role-keos-dns.json

# assign to the deployment user at subscription scope (no condition)
❯ az role assignment create --role capz-role-keos-dns \
    --assignee-object-id <deployment-user-sp-object-id> --assignee-principal-type ServicePrincipal \
    --scope /subscriptions/<subscription-id>
```

`az role assignment create` prints `"roleDefinitionName": null` for the new assignment — normal; check it with
`az role assignment list --all --assignee <appId>`.

## Microsoft Graph permission (Entra ID admin only)

`az ad sp create-for-rbac` and `az ad app list` call Microsoft Graph. Without a Graph permission both fail with
`Insufficient privileges to complete the operation.`, whatever Azure role the user has (an Owner of the subscription
gets the same error). Only an Entra ID administrator can grant it; requested in INFRA-2283 and granted on 2026-10-15.

`Application.ReadWrite.OwnedBy` is enough: it lets the deployment user create applications, list them, resolve
`--assignee <appId>` and delete them — only the ones it owns. `Application.ReadWrite.All` (what the dev superuser
`cloud-provisioner` has) would allow changing any application in the tenant.

Grant only OwnedBy with a direct app role assignment. Don't use "Grant admin consent" in the portal: it grants every
*requested* permission of the App Registration at once.

```bash
❯ az ad sp show --id 00000003-0000-0000-c000-000000000000 --query id -o tsv     # Microsoft Graph SP object id in the tenant
❯ az ad sp show --id <deployment-user-appId> --query id -o tsv                  # deployment user SP object id
❯ az rest --method post \
    --url https://graph.microsoft.com/v1.0/servicePrincipals/<deployment-user-sp-object-id>/appRoleAssignments \
    --body '{"principalId":"<deployment-user-sp-object-id>","resourceId":"<graph-sp-object-id>","appRoleId":"18a4783c-866b-4cc7-a460-3d5e5662c884"}'

# check: Microsoft Graph 18a4783c-866b-4cc7-a460-3d5e5662c884 (= Application.ReadWrite.OwnedBy)
❯ az rest --method get \
    --url https://graph.microsoft.com/v1.0/servicePrincipals/<deployment-user-sp-object-id>/appRoleAssignments \
    --query "value[].{api:resourceDisplayName,role:appRoleId}" -o table
```

Graph app role ids: `Application.ReadWrite.OwnedBy` = `18a4783c-866b-4cc7-a460-3d5e5662c884`,
`Application.ReadWrite.All` = `1bfefb4e-e0b5-418b-a88f-73c46d2cc8e9`.

### Gotchas seen in the tests

- **Cached token after the grant**: az keeps using the Graph token it got before the grant (`roles=[]`, valid ~24 h),
  so the deployment user still gets `Insufficient privileges`. Delete only the token cache of that az profile
  (`$AZURE_CONFIG_DIR/msal_token_cache.json`, not the stored credentials) or log in again. Check the token claims
  without printing the token:

  ```bash
  ❯ az account get-access-token --resource-type ms-graph --query accessToken -o tsv \
      | cut -d. -f2 | tr '_-' '/+' | base64 -d 2>/dev/null | jq -c '{iat:(.iat|todate),roles}'
  ```

- **Entra ID replication right after creating objects** — both cleared by repeating the same command a few seconds later:
  - `az ad sp create-for-rbac` → `Resource '<new app object id>' does not exist or one of its queried reference-property objects are not present.`
    The app is already created; the retry prints `Found an existing application instance … We will patch it.` and adds the Service Principal and the secret.
  - first `az role assignment create --assignee <appId>` → `Cannot find user or service principal in graph database for '<appId>'`.

- **`az network private-dns link vnet create` is not idempotent**: if the link exists it fails with
  `PreconditionFailed … exists already and hence cannot be created again`, whatever the permissions. Check with
  `az network private-dns link vnet show` before re-running the step.

## Fallback when Graph can't be granted to the deployment user

If the customer won't give Graph to the deployment user, split step "create the external-dns Service Principal":
someone allowed to register applications (an Entra ID admin, or any user if the tenant policy
`defaultUserRolePermissions.allowedToCreateApps` is `true`) creates it and hands over the appId, the secret and the
**object id**; the deployment user (only `capz-role-restricted` + `capz-role-keos-dns`) does everything else.
Tested end to end on 2026-10-15.

```bash
# identity allowed to register apps
❯ out=$(az ad sp create-for-rbac --name ${AZURE_SERVICE_PRINCIPAL_NAME} --output json)
❯ export AZURE_APP_SECRET=$(echo "$out" | jq -r .password)
❯ export AZURE_APP_ID=$(echo "$out" | jq -r .appId)
❯ export AZURE_APP_OBJECT_ID=$(az ad sp show --id ${AZURE_APP_ID} --query id --output tsv)

# deployment user, without Graph: --assignee <appId> needs Graph to resolve it and fails with PrincipalNotFound,
# so pass the object id
❯ az role assignment create --role "Reader" --assignee-object-id ${AZURE_APP_OBJECT_ID} \
    --assignee-principal-type ServicePrincipal --scope ${AZURE_PUBLIC_HOSTED_ZONE_ID}
```

Same for Contributor on the public zone and Reader/Contributor on the private zone.

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
| AWS managed|cloud-provisioner-restricted-aks|e3b6e5b9-e729-4612-a3cf-4bda74c240c4|Production|capz-aks-cloud-provisioner|capz-role-restricted-aks|credentials.azure.client_id|
| AWS managed|cloud-provisioner-restricted-aks|e3b6e5b9-e729-4612-a3cf-4bda74c240c4|Production|capz-aks-controlplane|capz-aks-role-controlplane|security.control_plane_identity|
| AWS managed|cloud-provisioner-restricted-aks|e3b6e5b9-e729-4612-a3cf-4bda74c240c4|Production|capz-aks-agp-restricted|capz-aks-role-node|security.nodes_identity|
| AWS unmanaged|cloud-provisioner-restricted|e979a59d-ba11-4fcc-b174-64cfab548943|Production|capz-cloud-provisioner|capz-role-restricted|credentials.azure.client_id|
| AWS unmanaged|cloud-provisioner-restricted|e979a59d-ba11-4fcc-b174-64cfab548943|Production|capz-controlplane|capz-role-controlplane|security.control_plane_identity|
| AWS unmanaged|cloud-provisioner-restricted|e979a59d-ba11-4fcc-b174-64cfab548943|Production|capz-agentpool-restricted|capz-role-node|security.nodes_identity|
| Azure unmanaged|cloud-provisioner-restricted|e979a59d-ba11-4fcc-b174-64cfab548943|Production|-|capz-role-keos-dns + Graph Application.ReadWrite.OwnedBy|keos external-dns prerequisite|