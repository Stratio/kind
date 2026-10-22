# Resumen tareas al crear una nueva cuenta

- [Resumen tareas al crear una nueva cuenta](#resumen-tareas-al-crear-una-nueva-cuenta)
  - [Quotas](#quotas)
  - [Get service quotas](#get-service-quotas)
- [IAM](#iam)
  - [Esquema de usuarios y políticas](#esquema-de-usuarios-y-políticas)
- [Passbolt](#passbolt)
- [CloudFormation](#cloudformation)
- [ECR](#ecr)
- [Lambda](#lambda)

## Quotas

Lo primero que haremos será solicitar un aumento de cuotas para los siguientes recursos. Las cuotas son por región: pedirlas en cada región donde se vayan a crear clusters.

| Recurso | Código | Por defecto | Cantidad |
|---------|--------|-------------|----------|
| Elastic IP address quota per NAT gateway | `vpc` `L-5F53652F` | 2 | 8 |
| NAT gateways per Availability Zone | `vpc` `L-FE5A380F` | 5 | 30 |
| Internet gateways per Region | `vpc` `L-A4707A72` | 5 | 20 |
| EC2-VPC Elastic IPs | `ec2` `L-0263D0A3` | 5 | 50 |

Son los valores de la cuenta 963 en `eu-west-1`; la 975 sigue en los valores por defecto salvo 12 Elastic IPs (comprobado el 2026-10-21).

Pre-requisitos usuario con los permisos necesarios para poder hacer las queries a la API de AWS.

| Service | Access level |
|---------|--------------|
| Service Quotas | Full Access |

## Get service quotas

```bash
REGION=eu-west-1
# Elastic IP address quota per NAT gateway
aws service-quotas get-service-quota --region $REGION --service-code vpc --quota-code L-5F53652F | jq '.Quota.Value' | cat
# NAT gateways per Availability Zone
aws service-quotas get-service-quota --region $REGION --service-code vpc --quota-code L-FE5A380F | jq '.Quota.Value' | cat
# Internet gateways per Region
aws service-quotas get-service-quota --region $REGION --service-code vpc --quota-code L-A4707A72 | jq '.Quota.Value' | cat
# EC2-VPC Elastic IPs
aws service-quotas get-service-quota --region $REGION --service-code ec2 --quota-code L-0263D0A3 | jq '.Quota.Value' | cat
```

Para pedir el aumento (ejemplo con NAT gateways):

```bash
aws service-quotas request-service-quota-increase --region $REGION --service-code vpc --quota-code L-FE5A380F --desired-value 30
```

# IAM

## Esquema de usuarios y políticas

Esquema de la cuenta 963 (comprobado el 2026-10-21):

| Identidad | Tipo | Para qué | Políticas |
|---|---|---|---|
| `cloud-provisioner` | Usuario | Desarrollo, acceso amplio | `stratio-cloud-provisioner`, `stratio-eks-full`, `stratio-ecr-full`, `ecr-public-read`, `capa-management-components-policy` y gestionadas de AWS (`AmazonEC2FullAccess`, `AmazonRoute53FullAccess`, `AmazonRoute53ResolverFullAccess`, `CloudWatchLogsFullAccess`, `AmazonSSMReadOnlyAccess`) |
| `cloud-provisioner-eks` | Usuario | Despliegues de EKS | `stratio-eks-policy`, `stratio-eks-cf-policy`, `capa-management-components-policy` |
| `capa-management-components` | Rol | Rol que se asume con `role_arn` en el descriptor de credenciales | `stratio-eks-policy`, `stratio-aws-cf-policy` |

Contenido de cada política:

| Política | Referencia |
|---|---|
| `stratio-eks-policy` | [`stratio-eks-policy.json`](../../../stratio-docs/es/modules/ROOT/assets/attachments/stratio-eks-policy.json) (la de la documentación de producto). La de la 963 no es idéntica: le faltan las acciones de launch templates y Auto Scaling groups (`ec2:CreateLaunchTemplate`, `autoscaling:CreateAutoScalingGroup`…) y tiene además `eks:UpdateNodegroupVersion` |
| `stratio-eks-cf-policy` / `stratio-aws-cf-policy` | [`stratio-aws-temp-policy.json`](../../../stratio-docs/es/modules/operations-manual/assets/attachments/stratio-aws-temp-policy.json), ver [CloudFormation](#cloudformation) |
| `capa-management-components-policy` | Solo `sts:AssumeRole` sobre el rol `capa-management-components` |

`docs/aws/Permissions/EKS/eks_permission_ref.json` está desactualizado respecto a `stratio-eks-policy.json` (le faltan 23 acciones): usar el de stratio-docs.

* Nota: Todas las políticas/roles deberían de llevar la misma nomenclatura, por ejemplo: 'stratio-'

# Passbolt

[Stratio-Passbolt](https://llavero.int.stratio.com/)  
Compartir de forma segura mediante passbolt las credenciales de los usuarios creados.

# CloudFormation

El stack `cluster-api-provider-aws-sigs-k8s-io` crea los roles y políticas de IAM que usan CAPA y los nodos de EKS. Lo crea o actualiza `cloud-provisioner create cluster` cuando el descriptor lleva `spec.security.aws.create_iam: true` (paso `[CAPA] Ensuring IAM security`): ejecuta `clusterawsadm bootstrap iam create-cloudformation-stack` con la configuración de `createCloudFormationStack` en [`aws.go`](../../../pkg/cluster/internal/create/actions/createworker/aws.go). Con `create_iam: false` (valor por defecto) el stack tiene que existir ya.

Qué crea (todos los nombres acaban en `.cluster-api-provider-aws.sigs.k8s.io`; comprobado en las cuentas 963 y 975 el 2026-10-21):

| Recurso | Nombres |
|---|---|
| Roles | `controllers`, `control-plane`, `nodes`, `eks-controlplane`, `eks-nodegroup` |
| Instance profiles | `controllers`, `control-plane`, `nodes` |
| Políticas gestionadas | `controllers`, `controllers-eks`, `control-plane`, `csi`, `nodes` |

Comprobar que existe y está bien:

```bash
aws cloudformation describe-stacks --stack-name cluster-api-provider-aws-sigs-k8s-io --region eu-west-1 \
  --query 'Stacks[0].[StackStatus,CreationTime,LastUpdatedTime]' --output text | cat
```

## Una sola región por cuenta

El stack se crea en la región del cluster (`AWS_REGION`), pero los roles de IAM son globales y tienen nombre fijo. Si ya existe en una región, crearlo en otra falla con `Resource of type 'AWS::IAM::Role' with identifier 'nodes.cluster-api-provider-aws.sigs.k8s.io' already exists` y el stack queda en `ROLLBACK_COMPLETE` (pasó en la 963, `eu-west-2`, 2023-12-14). En nuestras cuentas vive en `eu-west-1`: los clusters de otras regiones van con `create_iam: false` (no probado en vivo).

## Permisos del usuario de despliegue

Con `create_iam: true` el usuario de despliegue necesita las acciones de IAM y CloudFormation de [`stratio-aws-temp-policy.json`](../../../stratio-docs/es/modules/operations-manual/assets/attachments/stratio-aws-temp-policy.json). En la 963 es la política `stratio-eks-cf-policy`, adjunta a `cloud-provisioner-eks`, con las mismas acciones (comprobado el 2026-10-21).

## Nodos MachinePool con ECR pull-through

El rol `eks-nodegroup` solo admite ARNs de políticas gestionadas. Si los clusters usan `docker_registries[].ecr_pull_through_cache_enabled: true`, crear antes una política con `ecr:BatchImportUpstreamImage` y `ecr:CreateRepository` y pasar su ARN en `spec.security.aws.nodegroup_extra_policy_arn` (exige `create_iam: true`). En la 963 el rol ya lleva `ecr-pull-through-cache-machinepool` (actualización del stack del 2026-10-08); en la 975 no.

El stack se actualiza con la configuración de cada create con `create_iam: true`: sin `nodegroup_extra_policy_arn`, la plantilla generada no incluye esa política (comprobado con `clusterawsadm bootstrap iam print-cloudformation-template`). No está probado si al actualizar el stack se desasocia del rol; en la 963, lanzar los create con `create_iam: false` o con ese ARN.

## Rol AWSServiceRoleForAmazonEKS

No hace falta crearlo a mano: EKS lo crea al crear el primer cluster de la cuenta ([documentación de AWS](https://docs.aws.amazon.com/eks/latest/userguide/using-service-linked-roles-eks.html#create-service-linked-role-eks)), y `stratio-eks-policy` incluye `iam:CreateServiceLinkedRole`. En la 963 y la 975 ya existe (desde 2023), así que la creación automática no se ha probado en nuestras cuentas.

# ECR

Crear los repositorios y subir las imágenes que usan los despliegues. Listas por componente en [`docs/images/aws/`](../../images/aws/) y [`docs/images/commons/`](../../images/commons/).

Si los clusters van a usar ECR pull-through cache (`ecr_pull_through_cache_enabled: true`), crear antes las reglas de pull-through para `registry.k8s.io`, `docker.io`, `ghcr.io`, `quay.io` y `public.ecr.aws` (prerrequisitos en `stratio-docs/es/modules/operations-manual/pages/installation.adoc`, apartado "Prerrequisitos para `ecr_pull_through_cache_enabled`"), y la política del rol `eks-nodegroup` de [Nodos MachinePool con ECR pull-through](#nodos-machinepool-con-ecr-pull-through).

# Lambda

El informe diario, el aviso urgente y las limpiezas nocturnas de cada cuenta se describen en [`docs/aws/cloud-resources-report.md`](../cloud-resources-report.md).
