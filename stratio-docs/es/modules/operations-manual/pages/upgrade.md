# Upgrade Provisioner Script

## Descripción

El script `upgrade-provisioner.py` automatiza el proceso de actualización de clústeres en Kubernetes gestionados en los siguientes escenarios:
- **EKS** en AWS
- **Azure sobre máquinas virtuales**

Este script permite actualizar versiones de componentes y gráficos Helm (como `cluster-autoscaler` y `tigera-operator`), validar versiones de Kubernetes y personalizar configuraciones mediante plantillas Jinja2.

## Requisitos

### Requisitos generales

- **Python 3.8 o superior**
- **Dependencias de Python** (especificadas en el archivo `requirements.txt`):
  - `ansible-vault==2.1.0`
  - `jinja2==3.1.4`
  - `PyYAML==6.0.2`
  - `ruamel.yaml==0.18.6`
  - `ruamel.yaml.clib==0.2.8`
- Herramientas externas instaladas:
  - **kubectl**: para interactuar con Kubernetes.
  - **helm**: para la gestión de gráficos Helm.
  - **jq**: para procesar JSON en línea de comandos.

### Requisitos específicos para Azure

- La flag `--user-assign-identity` es **obligatoria**.
- Configura una identidad asignada por el usuario para interactuar con los recursos de Azure.

## Uso

### Sintaxis

***bash
python3 upgrade-provisioner.py [OPTIONS]
***

Flags disponibles:

| Flag                         | Descripción                                                                      | Valor predeterminado         | Obligatoria       |
|------------------------------|----------------------------------------------------------------------------------|------------------------------|-------------------|
| -y, --yes                     | No requiere confirmación entre tareas (modo automático).                         | False                        | No                |
| -k, --kubeconfig              | Especifica el archivo de configuración de kubectl a utilizar.                    | ~/.kube/config               | No                |
| -p, --vault-password          | Archivo con la contraseña del vault necesaria para descifrar secretos.           | None                         | Sí                |
| -s, --secrets                 | Archivo de secretos cifrados que debe descifrarse.                               | secrets.yml                  | No                |
| -i, --user-assign-identity    | Identidad asignada por el usuario necesaria en el caso de Azure.                 | None                         | Sí (Azure)        |
| --enable-lb-controller        | Instala el controlador de balanceador de carga en clústeres de EKS (desactivado por defecto). | False                  | No                |
| --disable-backup              | Deshabilita el respaldo de archivos antes de la actualización (habilitado por defecto). | False                      | No                |
| --disable-prepare-capsule     | Deshabilita la preparación del entorno para el proceso de actualización.         | False                        | No                |
| --dry-run                     | Muestra los pasos del proceso sin realizar cambios en el sistema.                | False                        | No                |

### Ejecución básica

**Para EKS en AWS:**

***bash
python3 upgrade-provisioner.py -p /ruta/vault-password --kubeconfig ~/.kube/config
***

**Para Azure sobre máquinas virtuales:**

***bash
python3 upgrade-provisioner.py -p /ruta/vault-password --user-assign-identity <identity-id>
***

## Directorios necesarios

El directorio de trabajo debe contener:

- `upgrade-provisioner.py` (el script principal)
- `templates/` (directorio con plantillas Jinja2 requeridas)
- `files/` (archivos adicionales, como configuraciones para Helm)
- `requirements.txt` (archivo con las dependencias necesarias)

## Uso en Docker

Para ejecutar el script en un contenedor Docker:

1. Construye la imagen Docker desde el Dockerfile:

***bash
docker build -t upgrade-provisioner .
***

2. Ejecuta el contenedor:

***bash
docker run --rm -v ~/.kube:/root/.kube -v $(pwd):/app upgrade-provisioner -p /app/secrets/vault-password
***

## Limitaciones

- Solo soporta clústeres de Kubernetes en:
  - Amazon EKS
  - Azure sobre máquinas virtuales

- No soporta otras soluciones de Kubernetes, como AKS gestionado en Azure o GKE en Google Cloud.

## Licencia

Este script es mantenido por Stratio Clouds. Para soporte, contacta con clouds-integration@stratio.com.
