# Informe de recursos y limpieza en GCP

Cloud Function (gen2) que vigila el gasto de un proyecto de GCP: avisa de instancias de riesgo, envía un informe diario y limpia de noche los discos sueltos y los restos de clusters que ya no existen (IPs `<cluster>-apiserver`, reglas de firewall, rutas y subredes). Es la versión GCP de las Lambdas de AWS descritas en [`docs/aws/cloud-resources-report.md`](../aws/cloud-resources-report.md) y de la Function App de [Azure](../azure/cloud-resources-report.md), y usa el mismo canal de correo.

Horas en UTC. Las lanza Cloud Scheduler con una petición HTTP autenticada (OIDC).

| Modo | Qué hace | Cuándo | ¿Toca recursos? |
|---|---|---|---|
| `urgent` | Correo con las instancias encendidas de riesgo (con GPU o familia de aceleradores, o 32 vCPU o más), solo si hay alguna | Cada 4 h | No, solo lee |
| `daily` | Informe: instancias, clusters GKE, Cloud NAT, forwarding rules (balanceadores), IPs externas reservadas sin usar, restos de clusters que ya no existen, discos sueltos y resultado de la última limpieza | Lunes a viernes, 07:00 | No, solo lee |
| `cleanup` | Borra los discos sin conectar que llevan más de 1 día así y los restos de clusters que ya no existen (ver [Restos de clusters](#restos-de-clusters)) | Lunes a viernes, 23:00 | Solo con `CLEANUP_MODE=actual_deletion`. En `report_only` solo marca y anuncia |

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
| Cualquier otra IP externa reservada | Se conserva siempre: la limpieza nunca la toca y el informe solo la lista |
| Redes base que usa cloud-provisioner para desplegar, con sus subredes, routers, NAT y reglas de firewall propias | Nunca se tocan: la limpieza solo reconoce los nombres que crea un cluster |

## Restos de clusters

Lo que un cluster crea en las redes base y queda suelto cuando el cluster desaparece. Las reglas de firewall, las rutas y las subredes no admiten labels en GCP, así que el margen de 1 día se cuenta desde su creación.

| Recurso | Cómo se reconoce | Se borra cuando |
|---|---|---|
| IP `<cluster>-apiserver` | IP del API server del cluster | No hay ningún cluster GKE `<cluster>` ni ninguna instancia `<cluster>-*` (en cualquier estado) |
| Reglas de firewall `allow-<cluster>-cluster` y `allow-<cluster>-healthchecks` | Las crea CAPG por cluster | Igual que la IP |
| Reglas de firewall y subredes `gke-<cluster>-<8 hex>-*` | Las crea GKE por cluster | No hay ningún cluster GKE `<cluster>` |
| Reglas de firewall `k8s-*` | Las crea Kubernetes para los Services `LoadBalancer`; apuntan a las etiquetas de los nodos | Ninguna instancia lleva sus etiquetas de destino |
| Rutas con siguiente salto en una instancia | Rutas de pods de un cluster | La instancia ya no existe |

Si falla el listado de clusters GKE, no se borra ningún resto esa noche.

## Correo

Sale por el mismo topic de Amazon SNS que los informes de AWS (desde `no-reply@sns.amazonaws.com`), con asunto `[Clouds][GCP][<proyecto>][Report] daily resources <fecha>` o `[Clouds][GCP][<proyecto>][URGENT] <n> risky instances running`.

La función publica en AWS **sin ningún secreto guardado**: pide a Google un token de identidad de su cuenta de servicio (audiencia `sts.amazonaws.com`) y AWS lo cambia por credenciales de 1 hora (`AssumeRoleWithWebIdentity`) del rol `gcp-cloud-report-publisher`. Google es un proveedor de identidad integrado en AWS, no hace falta crear un proveedor OIDC. La confianza del rol exige `accounts.google.com:sub` = uniqueId de la cuenta de servicio y `accounts.google.com:oaud` = `sts.amazonaws.com`, y el rol solo puede hacer `sns:Publish` en los topics del informe.

## Cuenta de servicio y permisos

La función y los jobs de Cloud Scheduler usan la cuenta de servicio por defecto de Compute del proyecto (`<número>-compute@developer.gserviceaccount.com`), que tiene `roles/editor`: incluye todo lo que necesita la función (listar, borrar y etiquetar recursos de Compute, listar clusters GKE, leer logs e invocar la función). Acordado con Infra en INFRA-2291: no hay cuenta ni rol propios.

Consecuencia: cualquier VM del proyecto que corra con esa cuenta puede obtener credenciales del rol `gcp-cloud-report-publisher` y publicar en los topics del informe (solo `sns:Publish`).

## Desplegar en un proyecto

Requisitos: `gcloud` y credenciales de aplicación (`gcloud auth application-default login`) con acceso al proyecto, y un perfil de AWS CLI con permisos de IAM en la cuenta del topic.

```bash
cd docs/gcp/scripts/cloud-resources-report

# Crea o actualiza APIs, rol de AWS, función y jobs; corre con la cuenta por defecto de Compute (RUN_AS_SA=<email> para otra)
PROJECT=<proyecto> TOPIC_ARN=arn:aws:sns:eu-west-1:<cuenta>:<topic del informe> CLEANUP_MODE=report_only bash deploy.sh

# Para pruebas, publicar en el topic de pruebas (solo llega a quien esté suscrito a él)
PROJECT=<proyecto> TOPIC_ARN=arn:aws:sns:eu-west-1:<cuenta>:cloud-report-test CLEANUP_MODE=report_only bash deploy.sh
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
