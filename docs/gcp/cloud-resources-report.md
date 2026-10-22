# Informe de recursos y limpieza en GCP

Cloud Function (gen2) que vigila el gasto de un proyecto de GCP: avisa de instancias de riesgo, envía un informe diario y limpia de noche los discos sueltos y las IPs `<cluster>-apiserver` de clusters que ya no existen. Es la versión GCP de las Lambdas de AWS descritas en [`docs/aws/cloud-resources-report.md`](../aws/cloud-resources-report.md) y de la Function App de [Azure](../azure/cloud-resources-report.md), y usa el mismo canal de correo.

Horas en UTC. Las lanza Cloud Scheduler con una petición HTTP autenticada (OIDC).

| Modo | Qué hace | Cuándo | ¿Toca recursos? |
|---|---|---|---|
| `urgent` | Correo con las instancias encendidas de riesgo (con GPU o familia de aceleradores, o 32 vCPU o más), solo si hay alguna | Cada 4 h | No, solo lee |
| `daily` | Informe: instancias, clusters GKE, Cloud NAT, forwarding rules (balanceadores), IPs externas reservadas sin usar, discos sueltos y resultado de la última limpieza | Lunes a viernes, 07:00 | No, solo lee |
| `cleanup` | Borra los discos sin conectar y las IPs `<cluster>-apiserver` sin usar de clusters que ya no existen, cuando llevan más de 1 día así | Lunes a viernes, 23:00 | Solo con `CLEANUP_MODE=actual_deletion`. En `report_only` solo marca y anuncia |

No hay sección de coste: el proyecto no exporta la facturación a BigQuery.

Estado en `clusterapi-369611`: la limpieza está en `report_only`. El informe muestra lo que se borraría como "due tonight (cleanup in report_only: not deleted)".

## Cómo se evita borrar algo por sorpresa

1. La primera noche que la limpieza ve un disco o una IP de cluster sueltos, solo los **marca** con la hora (labels `cleanup-unattached-since` y `cleanup-unassociated-since`, en segundos epoch: los valores de label de GCP no admiten `:`).
2. El informe de la mañana siguiente los muestra como "se borra esta noche".
3. Si a la noche siguiente siguen sueltos, se borran. El informe del día después muestra lo borrado y los errores (lo lee de la línea `CLEANUP_SUMMARY {json}` que deja la limpieza en Cloud Logging).

Para conservar algo a propósito:

| Recurso | Cómo |
|---|---|
| Disco | Label `noautodelete=true` (GCP solo admite minúsculas en las claves) o volver a conectarlo |
| Disco de una PVC (el driver PD CSI guarda la PVC en la descripción del disco, clave `kubernetes.io/created-for/pvc/name`) | Automático: se conserva mientras haya algún cluster GKE en su región. El informe lo muestra como "PVC of a live cluster" |
| IP `<cluster>-apiserver` (la IP del API server de un cluster) | Se conserva mientras exista un cluster GKE con ese nombre o alguna instancia `<cluster>-*`, en cualquier estado. Si no queda ninguno, se borra al cabo de 1 día |
| Cualquier otra IP externa reservada | Son reservas de los administradores del proyecto: la limpieza nunca las toca y el informe solo las lista |

## Correo

Sale por el mismo topic de Amazon SNS que los informes de AWS (desde `no-reply@sns.amazonaws.com`), con asunto `[Clouds][GCP][<proyecto>][Report] daily resources <fecha>` o `[Clouds][GCP][<proyecto>][URGENT] <n> risky instances running`.

La función publica en AWS **sin ningún secreto guardado**: pide a Google un token de identidad de su cuenta de servicio (audiencia `sts.amazonaws.com`) y AWS lo cambia por credenciales de 1 hora (`AssumeRoleWithWebIdentity`) del rol `gcp-cloud-report-publisher`. Google es un proveedor de identidad integrado en AWS, no hace falta crear un proveedor OIDC. La confianza del rol exige `accounts.google.com:sub` = uniqueId de la cuenta de servicio y `accounts.google.com:oaud` = `sts.amazonaws.com`, y el rol solo puede hacer `sns:Publish` en los topics del informe.

## Cuenta de servicio y permisos

| Cuenta de servicio | Permisos | Cuándo |
|---|---|---|
| `cloud-resources-report@<proyecto>` | Rol propio `cloudsCleanupReport` (listar instancias, discos, direcciones, routers, forwarding rules y clusters; borrar y etiquetar discos y direcciones) + `roles/logging.viewer`, y `roles/run.invoker` sobre la función | Diseño final. Crear el rol y asignarlo requiere `roles/iam.roleAdmin` y `roles/resourcemanager.projectIamAdmin` |
| Cuenta por defecto de Compute (`<número>-compute@developer.gserviceaccount.com`, `roles/editor`) | Todos los que necesita la función | Provisional en `clusterapi-369611` mientras no se asignen los roles anteriores. Cualquier VM que corra con esa cuenta puede publicar en los topics del informe |

## Desplegar en un proyecto

Requisitos: `gcloud` y credenciales de aplicación (`gcloud auth application-default login`) con acceso al proyecto, y un perfil de AWS CLI con permisos de IAM en la cuenta del topic.

```bash
cd docs/gcp/scripts/cloud-resources-report

# Con su propia cuenta de servicio (crea la cuenta, el rol propio y las asignaciones; necesita permisos de administración de IAM)
PROJECT=<proyecto> TOPIC_ARN=arn:aws:sns:eu-west-1:<cuenta>:<topic del informe> CLEANUP_MODE=report_only bash deploy.sh

# Sin permisos de administración de IAM: con una cuenta de servicio que ya tenga los permisos
RUN_AS_SA=<número>-compute@developer.gserviceaccount.com PROJECT=<proyecto> TOPIC_ARN=arn:aws:sns:eu-west-1:<cuenta>:<topic del informe> CLEANUP_MODE=report_only bash deploy.sh

# Para pruebas, publicar en el topic de pruebas (solo llega a quien esté suscrito a él)
RUN_AS_SA=<cuenta de servicio> PROJECT=<proyecto> TOPIC_ARN=arn:aws:sns:eu-west-1:<cuenta>:cloud-report-test CLEANUP_MODE=report_only bash deploy.sh
```

Para probar sin esperar a su hora:

```bash
gcloud scheduler jobs run clouds-report-daily --project <proyecto> --location europe-west4
gcloud logging read 'resource.labels.service_name="clouds-report"' --project <proyecto> --limit 50 --format='value(timestamp,textPayload)'
```

Prueba local sin escribir nada (sin labels, sin correo):

```bash
GCP_PROJECT_ID=<proyecto> python -c 'import gcp_report as r; r.main({"mode": "cleanup", "no_writes": True}); r.main({"mode": "daily", "dry_run": True})'
```
