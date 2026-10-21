# Informe de recursos y limpieza en Azure

Function App de Azure que vigila el gasto de una suscripción: avisa de VMs de riesgo, envía un informe diario y limpia de noche los discos e IPs públicas que quedan sueltos. Es la versión Azure de las Lambdas de AWS descritas en [`docs/aws/accounts/new_accounts.md`](../aws/accounts/new_accounts.md#lambda) y usa el mismo canal de correo.

Horas en UTC.

| Función | Qué hace | Cuándo | ¿Toca recursos? |
|---|---|---|---|
| `urgent` | Correo con las VMs encendidas de riesgo (serie N con GPU, o 32 vCPU o más), solo si hay alguna | Cada 4 h | No, solo lee |
| `daily` | Informe: VMs, NAT gateways, VNets y balanceadores en grupos sin VMs encendidas, Azure Bastion, IPs públicas y discos sueltos, resultado de la última limpieza y coste del día anterior por servicio | Lunes a viernes, 07:00 | No, solo lee |
| `cleanup` | Borra los discos sin conectar y las IPs públicas sin asociar que llevan más de 1 día así | Lunes a viernes, 23:00 | Sí, borra |

## Cómo se evita borrar algo por sorpresa

1. La primera noche que la limpieza ve un disco o una IP sueltos, solo los **marca** con la hora (etiquetas `cleanup-unattached-since` y `cleanup-unassociated-since`; Azure no admite `/` en los nombres de etiqueta).
2. El informe de la mañana siguiente los muestra como "se borra esta noche".
3. Si a la noche siguiente siguen sueltos, se borran. El informe del día después muestra lo borrado y los errores.

Para conservar algo a propósito:

| Recurso | Cómo |
|---|---|
| Disco | Etiqueta `NoAutoDelete=true` (o volver a conectarlo) |
| VNet | Etiquetas `NoAutoDelete=true` y `keep-reason=<motivo>`. La limpieza nunca borra VNets |
| IP pública | No hay forma: toda IP sin asociar más de 1 día se borra |
| Grupo con bloqueo `CanNotDelete` | Lo que haya dentro no se puede borrar; el informe lo muestra como "locked, kept" |

## Correo

Sale por el mismo topic de Amazon SNS que los informes de AWS (desde `no-reply@sns.amazonaws.com`), con asunto `[Clouds][Azure][<suscripción>][Report] daily resources <fecha>` o `[Clouds][Azure][<suscripción>][URGENT] <n> risky VMs running`.

La Function publica en AWS **sin ningún secreto guardado**: su identidad administrada pide a Entra ID un token para la aplicación `clouds-report-aws-publisher`, y AWS lo cambia por credenciales de 1 hora (`AssumeRoleWithWebIdentity`) del rol `azure-cloud-report-publisher`. Ese rol solo puede asumirlo esa identidad y solo puede hacer `sns:Publish` en los topics del informe.

## Permisos de la identidad de la Function

| Rol | Ámbito | Para qué |
|---|---|---|
| Reader | Suscripción | Inventario |
| Cost Management Reader | Suscripción | Coste del día anterior |
| Tag Contributor | Suscripción | Poner y quitar las marcas de la limpieza |
| Clouds cleanup (disks and public IPs) | Suscripción | Rol propio: solo `Microsoft.Compute/disks/delete` y `Microsoft.Network/publicIPAddresses/delete` |

## Desplegar en una suscripción

Requisitos: ser Owner de la suscripción, poder registrar aplicaciones en Entra ID, y un perfil de AWS CLI con permisos de IAM en la cuenta del topic.

```bash
cd docs/azure/scripts/cloud-resources-report

# Crea o actualiza grupo, almacenamiento, Function App, roles, app de Entra, proveedor OIDC y rol de AWS, y sube el código
SUB=<id de la suscripción> SUB_NAME=<nombre corto> TOPIC_ARN=arn:aws:sns:eu-west-1:<cuenta>:<topic del informe> bash deploy.sh

# Para pruebas, publicar en el topic de pruebas (solo llega a quien esté suscrito a él)
SUB=<id> SUB_NAME=<nombre> TOPIC_ARN=arn:aws:sns:eu-west-1:<cuenta>:cloud-report-test bash deploy.sh
```

Para probar una función desplegada sin esperar a su hora, se cambia su horario en la configuración de la app (los horarios son las variables `URGENT_SCHEDULE`, `DAILY_SCHEDULE` y `CLEANUP_SCHEDULE`, formato NCRONTAB con segundos) y después se restaura:

```bash
az functionapp config appsettings set --subscription <id> -g cloud-guardrails -n <function app> \
  --settings "DAILY_SCHEDULE=0 <minuto> <hora> * * *"
# ... tras la ejecución:
az functionapp config appsettings set --subscription <id> -g cloud-guardrails -n <function app> \
  --settings "DAILY_SCHEDULE=0 0 7 * * 1-5"
```

Los logs de cada ejecución están en Application Insights (mismo nombre que la Function App).
