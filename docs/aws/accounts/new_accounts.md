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

Lo primero que haremos será solicitar un aumento de cuotas para los siguientes recursos:

| Recurso | Cantidad |
|---------|----------|
| Elastic IP address quota per NAT gateway | 8 |
| NAT gateways per Availability Zone | 30 |
| Internet gateways per Region | 20 |
| EC2-VPC Elastic IPs | 50 |

Pre-requisitos usuario con los permisos necesarios para poder hacer las queries a la API de AWS.

| Service | Access level |
|---------|--------------|
| Service Quotas | Full Access |

## Get service quotas

```bash
# Elastic IP address quota per NAT gateway
aws service-quotas get-service-quota --service-code vpc --quota-code L-5F53652F | jq '.Quota.Value' | cat
# NAT gateways per Availability Zone
aws service-quotas get-service-quota --service-code vpc --quota-code L-026E1A4D | jq '.Quota.Value' | cat
# Internet gateways per Region
aws service-quotas get-service-quota --service-code vpc --quota-code L-A4707A72 | jq '.Quota.Value' | cat
# EC2-VPC Elastic IPs
aws service-quotas get-service-quota --service-code ec2 --quota-code L-0263D0A3 | jq '.Quota.Value' | cat
```

# IAM

## Esquema de usuarios y políticas

> User: cloud-provisioner  
>>  Description: Full access to enhance development  
    Policy: stratio-cloud-provisioner  

> User: cloud-provisioner-eks
>>    Description: Only EKS deployments  
>>    Policy:  
>>        - stratio-eks-policy (EKS deployments) [stratio-eks-policy](../Permissions/EKS/eks_permission_ref.json)  
>>        - stratio-fc-policy (CloudFormation) [stratio-fc-policy](../Permissions/EKS/eks_Cloud_Formation.json)  
>>        - stratio-eks-offline (Offline EKS deployments)  

> User: cloud-provisioner-aws
>>    Description: Only AWS deployments  
>>    Policy:  
>>        - stratio-aws-policy (AWS deployments) [stratio-aws-policy](../Permissions/AWS/aws_permission_ref.json)  
>>        - stratio-fc-policy (CloudFormation) [stratio-fc-policy](../Permissions/AWS/aws_Cloud_Formation.json)  
>>        - stratio-aws-offline (Offline AWS deployments)  

* Nota: Todas las políticas/roles deberían de llevar la misma nomenclatura, por ejemplo: 'stratio-'

# Passbolt

[Stratio-Passbolt](https://llavero.int.stratio.com/)  
Compartir de forma segura mediante passbolt las credenciales de los usuarios creados.

# CloudFormation

Asegurarse de que el despliegue del stack de CloudFormation se hace de forma satisfactoria, desplegando un cluster de EKS con el cloud-provisioner.

Crear de forma manual el role: "AWSServiceRoleForAmazonEKS" y asociarle la política "AmazonEKSServiceRolePolicy".

# ECR

Crear repositorio y subir todas las imagenes necesarias para los despliegues

# Lambda

Cada cuenta lleva cinco funciones Lambda que vigilan el gasto: una envía avisos e informes por correo y cuatro limpian por la noche los recursos que quedan sueltos tras borrar clusters de prueba. Todas viven en `eu-west-1`, recorren todas las regiones habilitadas de la cuenta y corren en Python 3.12. El código y los scripts de despliegue están en [`scripts/`](scripts/).

Horas en UTC.

| Función | Qué hace | Cuándo | ¿Toca recursos? |
|---|---|---|---|
| `CloudResourcesReport` | **Aviso urgente**: correo con las instancias de riesgo (familias GPU/aceleradoras `g`, `p`, `inf`, `trn`, `dl`, `f`, o tamaño 8xlarge o mayor), solo si hay alguna. **Informe diario**: instancias encendidas más de 8 h, clusters EKS de más de 1 día con sus grupos de nodos, NAT gateways, VPCs sin instancias, balanceadores, IPs y discos sueltos, grupos de logs de EKS, resultado de la limpieza nocturna y coste del día anterior por concepto | Aviso cada 4 h; informe de lunes a viernes a las 07:00 | No, solo lee |
| `deleteoldOIDC` | Borra los proveedores OIDC de IAM de clusters EKS que ya no existen en ninguna región, si tienen más de 2 días. Nunca toca proveedores que no sean de EKS | Lunes a viernes, 23:00 | Sí, borra |
| `deleteUnusedEBS` | Borra los volúmenes EBS que llevan más de 1 día sin conectar | Lunes a viernes, 23:00 | Sí, borra |
| `deleteUnusedEIPs` | Libera las Elastic IPs que llevan más de 1 día sin asociar | Lunes a viernes, 23:00 | Sí, libera |
| `cleanupEksLogGroups` | Pone 30 días de caducidad a los grupos de logs `/aws/eks/*` que no la tienen, y borra los grupos ya vacíos de clusters que no existen, 1 día después de marcarlos. Nunca borra un grupo con datos | Lunes a viernes, 23:00 | Sí: caducidad y borrado de grupos vacíos |

## Cómo se evita borrar algo por sorpresa

1. La primera noche que una limpieza ve un volumen, una IP o un grupo de logs vacío sueltos, solo lo **marca** con la hora (etiquetas `cleanup.stratio.com/unattached-since`, `cleanup.stratio.com/unassociated-since` y `cleanup.stratio.com/empty-since`).
2. El informe de la mañana siguiente lo muestra como "se borra esta noche".
3. Si a la noche siguiente sigue suelto, se borra. El informe del día después muestra lo borrado, los errores y si alguna limpieza falló o no se ejecutó (cada ejecución deja una línea `CLEANUP_SUMMARY {json}` en su log).

Para conservar algo a propósito:

| Recurso | Cómo |
|---|---|
| Volumen EBS | Etiqueta `NoAutoDelete=true` (o volver a conectarlo) |
| VPC | Etiquetas `NoAutoDelete=true` y `keep-reason=<motivo>`; el informe la lista como "KEPT ON PURPOSE (<motivo>)". Ninguna función borra VPCs |
| Elastic IP | No hay forma: toda IP sin asociar más de 1 día se libera |

En fin de semana no se ejecuta ninguna limpieza: lo marcado el viernes se anuncia el lunes por la mañana y se borra el lunes por la noche.

## Correo

Los correos llegan por Amazon SNS desde `no-reply@sns.amazonaws.com`, con asunto `[Clouds][AWS][<cuenta>][Report] daily resources <fecha>` o `[Clouds][AWS][<cuenta>][URGENT] <n> risky instances running`. Los destinatarios son las suscripciones de email confirmadas del topic de cada cuenta. Amazon SES no sirve aquí: el DMARC de `stratio.com` (`p=reject`, alineación estricta) rechaza los correos que SES firma como `amazonses.com`.

## Desplegar en una cuenta

Requisitos: un perfil de AWS CLI con permisos de IAM, Lambda, EventBridge y SNS en la cuenta.

```bash
cd docs/aws/accounts/scripts

# Limpiezas nocturnas: crea o actualiza las cuatro funciones, sus roles, sus reglas y las prueba en modo solo informe
PROFILE=<perfil> bash cleanup-lambdas/deploy.sh

# Informe y aviso urgente; CREATE_TOPIC=1 crea el topic SNS si no existe
PROFILE=<perfil> TOPIC=cloud-resources-report CREATE_TOPIC=1 bash cloud-resources-report/deploy.sh

# Suscribir un correo (llega un email de confirmación que hay que aceptar)
aws sns subscribe --profile <perfil> --region eu-west-1 \
  --topic-arn arn:aws:sns:eu-west-1:<cuenta>:cloud-resources-report \
  --protocol email --notification-endpoint <correo>

# Probar el informe diario sin enviarlo (tarda ~1 min; desde el portátil es mucho más lento)
aws lambda invoke --profile <perfil> --region eu-west-1 --function-name CloudResourcesReport \
  --payload '{"mode":"daily","dry_run":true}' --cli-binary-format raw-in-base64-out --cli-read-timeout 950 /dev/stdout
```

Cada limpieza acepta el evento `{"operating_mode": "actual_deletion"}` para actuar; cualquier otro evento solo informa. Las reglas nocturnas que crea `deploy.sh` ya le pasan ese evento.

Pendiente de montar: comprobar el estado del stack de CloudFormation y avisar si hay algún error.