# Usar la imagen de Golang 1.22 como base
FROM golang:1.22

ARG UPGRADE_DIR=CTS/resources/scripts

# Init feature gates
ENV CLUSTER_TOPOLOGY=true
ENV CLUSTERCTL_DISABLE_VERSIONCHECK=true

# Tools versions
ARG CLUSTERCTL=v1.7.4
ENV CLUSTERAWSADM=v2.5.2
ARG PYTHON_VERSION=3.12
ARG YQ_VERSION=v4.45.1
ARG KUBECTL_VERSION=1.30.1
ARG HELM_VERSION=3.15.2

# Cluster-api artifacts
ENV CAPI_REPO=/root/.cluster-api/local-repository
ARG CAPA=v2.5.2
ARG CAPG=1.6.1-0.3.1
ARG CAPZ=v1.12.4
ENV AWS_CONFIG_FILE=/upgrade/.aws/config
ENV AWS_SHARED_CREDENTIALS_FILE=/upgrade/.aws/credentials
ENV KUBECONFIG=/upgrade/.kube/config


# Upodate packages and install essential tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    curl \
    jq vim python3-pip git \
    ca-certificates \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install yq
RUN wget https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_amd64 -O /usr/bin/yq &&\
    chmod +x /usr/bin/yq

# Install AWS CLI v2.15.60
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64-2.15.60.zip" -o "awscliv2.zip" \
    && unzip awscliv2.zip \
    && ./aws/install \
    && rm -rf awscliv2.zip ./aws

# Install kubectl
RUN curl -LO https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/amd64/kubectl \
    && install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

# Install helm
RUN curl -LO https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz \
    && tar -xzf helm-v${HELM_VERSION}-linux-amd64.tar.gz \
    && mv linux-amd64/helm /usr/local/bin/helm \
    && rm -rf helm-v${HELM_VERSION}-linux-amd64.tar.gz linux-amd64

# Install clusterctl
RUN curl -L https://github.com/kubernetes-sigs/cluster-api/releases/download/${CLUSTERCTL}/clusterctl-linux-amd64 \
    -o /usr/local/bin/clusterctl \
    && chmod +x /usr/local/bin/clusterctl

# Check versions
RUN python3 --version && \
    kubectl version --client --output=yaml && \
    helm version && \
    clusterctl version && \
    jq --version

# Prepare cluster-api private repository
RUN mkdir -p ${CAPI_REPO}/infrastructure-aws/${CAPA} ${CAPI_REPO}/infrastructure-gcp/${CAPG} ${CAPI_REPO}/infrastructure-azure/${CAPZ} ${CAPI_REPO}/cluster-api/${CLUSTERCTL} ${CAPI_REPO}/bootstrap-kubeadm/${CLUSTERCTL} ${CAPI_REPO}/control-plane-kubeadm/${CLUSTERCTL} ${CROSSPLANE_CACHE} \
  && echo "providers:" > /root/.cluster-api/clusterctl.yaml \
  && echo "  - name: aws\n    url: ${CAPI_REPO}/infrastructure-aws/${CAPA}/infrastructure-components.yaml\n    type: InfrastructureProvider" >> /root/.cluster-api/clusterctl.yaml \
  && echo "  - name: gcp\n    url: ${CAPI_REPO}/infrastructure-gcp/${CAPG}/infrastructure-components.yaml\n    type: InfrastructureProvider" >> /root/.cluster-api/clusterctl.yaml \
  && echo "  - name: azure\n    url: ${CAPI_REPO}/infrastructure-azure/${CAPZ}/infrastructure-components.yaml\n    type: InfrastructureProvider" >> /root/.cluster-api/clusterctl.yaml \
  && echo "  - name: kubeadm\n    url: ${CAPI_REPO}/bootstrap-kubeadm/${CLUSTERCTL}/bootstrap-components.yaml\n    type: BootstrapProvider" >> /root/.cluster-api/clusterctl.yaml \
  && echo "  - name: kubeadm\n    url: ${CAPI_REPO}/control-plane-kubeadm/${CLUSTERCTL}/control-plane-components.yaml\n    type: ControlPlaneProvider" >> /root/.cluster-api/clusterctl.yaml \
  && echo "  - name: cluster-api\n    url: ${CAPI_REPO}/cluster-api/${CLUSTERCTL}/core-components.yaml\n    type: CoreProvider" >> /root/.cluster-api/clusterctl.yaml 
  

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

# Set up the working directory
WORKDIR /upgrade

# Add aliases
RUN echo 'alias k="kubectl"' >> ~/.bash_aliases \
    && echo 'alias capi-logs="kubectl -n capi-system logs -f deploy/capi-controller-manager"' >> ~/.bash_aliases \
    && echo 'alias capa-logs="kubectl -n capa-system logs -f deploy/capa-controller-manager"' >> ~/.bash_aliases \
    && echo 'alias capg-logs="kubectl -n capg-system logs -f deploy/capg-controller-manager"' >> ~/.bash_aliases \
    && echo 'alias capz-logs="kubectl -n capz-system logs -f deploy/capz-controller-manager"' >> ~/.bash_aliases \
    && echo 'alias kc-logs="kubectl -n kube-system logs -f deploy/keoscluster-controller-manager"' >> ~/.bash_aliases


# Copy the upgrade scripts and files
COPY ${UPGRADE_DIR}/templates /upgrade/templates
COPY ${UPGRADE_DIR}/files /upgrade/files
COPY ${UPGRADE_DIR}/upgrade-provisioner.py /upgrade
COPY ${UPGRADE_DIR}/requirements.txt /upgrade

# Install Python dependencies
RUN pip3 install --break-system-packages -r requirements.txt

# Set the entrypoint
CMD ["/bin/bash"]
