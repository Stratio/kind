# Usar la imagen de Golang 1.22 como base
FROM golang:1.22

ARG UPGRADE_DIR=CTS/resources/scripts


# Init feature gates
ENV CLUSTER_TOPOLOGY=true
ENV CLUSTERCTL_DISABLE_VERSIONCHECK=true

# Tools versions
ARG CLUSTERCTL=1.7.4
ENV CLUSTERAWSADM=v2.5.2
ARG PYTHON_VERSION=3.12
ARG KUBECTL_VERSION=1.30.1
ARG HELM_VERSION=3.15.2

# Cluster-api artifacts
ENV CAPI_REPO=/root/.cluster-api/local-repository
ARG CAPA=v2.5.2
ARG CAPG=1.6.1-0.2.1
ARG CAPZ=v1.12.4
ENV CAPG_FORK_URL_GKE="https://github.com/Stratio/cluster-api-provider-gcp/releases/download/${CAPG}/"


# Actualizar paquetes e instalar herramientas esenciales
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3=${PYTHON_VERSION}* \
    python3-pip=${PYTHON_VERSION}* \
    curl \
    jq vim python3-pip git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Instalar kubectl
RUN curl -LO https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/amd64/kubectl \
    && install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

# Instalar helm
RUN curl -LO https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz \
    && tar -xzf helm-v${HELM_VERSION}-linux-amd64.tar.gz \
    && mv linux-amd64/helm /usr/local/bin/helm \
    && rm -rf helm-v${HELM_VERSION}-linux-amd64.tar.gz linux-amd64

# Instalar clusterctl
RUN curl -L https://github.com/kubernetes-sigs/cluster-api/releases/download/v${CLUSTERCTL}/clusterctl-linux-amd64 \
    -o /usr/local/bin/clusterctl \
    && chmod +x /usr/local/bin/clusterctl

# Verificar las versiones de las herramientas instaladas
RUN python3 --version && \
    kubectl version --client --output=yaml && \
    helm version && \
    clusterctl version && \
    jq --version

# Add aliases
RUN echo 'alias k="kubectl"' >> ~/.bash_aliases \
    && echo 'alias capi-logs="kubectl -n capi-system logs -f deploy/capi-controller-manager"' >> ~/.bash_aliases \
    && echo 'alias capa-logs="kubectl -n capa-system logs -f deploy/capa-controller-manager"' >> ~/.bash_aliases \
    && echo 'alias capg-logs="kubectl -n capg-system logs -f deploy/capg-controller-manager"' >> ~/.bash_aliases \
    && echo 'alias capz-logs="kubectl -n capz-system logs -f deploy/capz-controller-manager"' >> ~/.bash_aliases \
    && echo 'alias kc-logs="kubectl -n kube-system logs -f deploy/keoscluster-controller-manager"' >> ~/.bash_aliases


# Prepare cluster-api private repository
RUN mkdir -p ${CAPI_REPO}/infrastructure-aws/${CAPA} ${CAPI_REPO}/infrastructure-gcp/${CAPG} ${CAPI_REPO}/infrastructure-azure/${CAPZ} ${CAPI_REPO}/cluster-api/${CLUSTERCTL} ${CAPI_REPO}/bootstrap-kubeadm/${CLUSTERCTL} ${CAPI_REPO}/control-plane-kubeadm/${CLUSTERCTL} ${CROSSPLANE_CACHE} \
  && echo "providers:" > /root/.cluster-api/clusterctl.yaml \
  && echo "  - name: aws\n    url: ${CAPI_REPO}/infrastructure-aws/${CAPA}/infrastructure-components.yaml\n    type: InfrastructureProvider" >> /root/.cluster-api/clusterctl.yaml \
  && echo "  - name: gcp\n    url: ${CAPI_REPO}/infrastructure-gcp/${CAPG}/infrastructure-components.yaml\n    type: InfrastructureProvider" >> /root/.cluster-api/clusterctl.yaml \
  && echo "  - name: azure\n    url: ${CAPI_REPO}/infrastructure-azure/${CAPZ}/infrastructure-components.yaml\n    type: InfrastructureProvider" >> /root/.cluster-api/clusterctl.yaml \
  && echo "  - name: kubeadm\n    url: ${CAPI_REPO}/bootstrap-kubeadm/${CLUSTERCTL}/bootstrap-components.yaml\n    type: BootstrapProvider" >> /root/.cluster-api/clusterctl.yaml \
  && echo "  - name: kubeadm\n    url: ${CAPI_REPO}/control-plane-kubeadm/${CLUSTERCTL}/control-plane-components.yaml\n    type: ControlPlaneProvider" >> /root/.cluster-api/clusterctl.yaml \
  && echo "  - name: cluster-api\n    url: ${CAPI_REPO}/cluster-api/${CLUSTERCTL}/core-components.yaml\n    type: CoreProvider" >> /root/.cluster-api/clusterctl.yaml \
  && echo "images:" >> /root/.cluster-api/clusterctl.yaml \
  && echo "  cluster-api:" >> /root/.cluster-api/clusterctl.yaml \
  && echo "    repository: {{KEOS_REGISTRY_URL}}/cluster-api" >> /root/.cluster-api/clusterctl.yaml \
  && echo "    tag: ${CLUSTERCTL}" >> /root/.cluster-api/clusterctl.yaml \
  && echo "  bootstrap-kubeadm:" >> /root/.cluster-api/clusterctl.yaml \
  && echo "    repository: {{KEOS_REGISTRY_URL}}/cluster-api" >> /root/.cluster-api/clusterctl.yaml \
  && echo "    tag: ${CLUSTERCTL}" >> /root/.cluster-api/clusterctl.yaml \
  && echo "  control-plane-kubeadm:" >> /root/.cluster-api/clusterctl.yaml \
  && echo "    repository: {{KEOS_REGISTRY_URL}}/cluster-api" >> /root/.cluster-api/clusterctl.yaml \
  && echo "    tag: ${CLUSTERCTL}" >> /root/.cluster-api/clusterctl.yaml \
  && echo "  infrastructure-aws:" >> /root/.cluster-api/clusterctl.yaml \
  && echo "    repository: {{KEOS_REGISTRY_URL}}/cluster-api-aws" >> /root/.cluster-api/clusterctl.yaml \
  && echo "    tag: ${CAPA}" >> /root/.cluster-api/clusterctl.yaml \
  && echo "  infrastructure-gcp:" >> /root/.cluster-api/clusterctl.yaml \
  && echo "    repository: {{KEOS_REGISTRY_URL}}/cluster-api-gcp" >> /root/.cluster-api/clusterctl.yaml \
  && echo "    tag: ${CAPG}" >> /root/.cluster-api/clusterctl.yaml \
  && echo "  infrastructure-azure:" >> /root/.cluster-api/clusterctl.yaml \
  && echo "    repository: {{KEOS_REGISTRY_URL}}/cluster-api-azure" >> /root/.cluster-api/clusterctl.yaml \
  && echo "    tag: ${CAPZ}" >> /root/.cluster-api/clusterctl.yaml \
  && echo "  cert-manager:" >> /root/.cluster-api/clusterctl.yaml \
  && echo "    repository: {{KEOS_REGISTRY_URL}}/cert-manager" >> /root/.cluster-api/clusterctl.yaml 
  

# Download cluster-api artifacts
RUN for i in metadata.yaml infrastructure-components.yaml; do \
      curl -L https://github.com/kubernetes-sigs/cluster-api-provider-aws/releases/download/${CAPA}/${i} -o ${CAPI_REPO}/infrastructure-aws/${CAPA}/${i} \
      && curl -L https://github.com/Stratio/cluster-api-provider-gcp/releases/download/${CAPG}/${i} -o ${CAPI_REPO}/infrastructure-gcp/${CAPG}/${i} \
      && curl -L https://github.com/kubernetes-sigs/cluster-api-provider-azure/releases/download/${CAPZ}/${i} -o ${CAPI_REPO}/infrastructure-azure/${CAPZ}/${i}; done

RUN curl -L  https://github.com/kubernetes-sigs/cluster-api/releases/download/${CLUSTERCTL}/core-components.yaml -o ${CAPI_REPO}/cluster-api/${CLUSTERCTL}/core-components.yaml \
    && curl -L https://github.com/kubernetes-sigs/cluster-api/releases/download/${CLUSTERCTL}/bootstrap-components.yaml -o ${CAPI_REPO}/bootstrap-kubeadm/${CLUSTERCTL}/bootstrap-components.yaml \
    && curl -L https://github.com/kubernetes-sigs/cluster-api/releases/download/${CLUSTERCTL}/control-plane-components.yaml -o ${CAPI_REPO}/control-plane-kubeadm/${CLUSTERCTL}/control-plane-components.yaml \
    && curl -L https://github.com/kubernetes-sigs/cluster-api/releases/download/${CLUSTERCTL}/metadata.yaml -o ${CAPI_REPO}/cluster-api/${CLUSTERCTL}/metadata.yaml \
    && cp ${CAPI_REPO}/cluster-api/${CLUSTERCTL}/metadata.yaml ${CAPI_REPO}/bootstrap-kubeadm/${CLUSTERCTL}/metadata.yaml \
    && cp ${CAPI_REPO}/cluster-api/${CLUSTERCTL}/metadata.yaml ${CAPI_REPO}/control-plane-kubeadm/${CLUSTERCTL}/metadata.yaml

# Establecer el directorio de trabajo
WORKDIR /upgrade

# Copiar archivos necesarios al contenedor (opcional)
COPY ${UPGRADE_DIR}/templates /upgrade/templates
COPY ${UPGRADE_DIR}/upgrade-provisioner.py /upgrade
COPY ${UPGRADE_DIR}/requirements.txt /upgrade

# Instalar dependencias de Python
RUN pip3 install -r requirements.txt

# Especificar el comando por defecto
CMD ["/bin/bash"]
