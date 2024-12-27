# How to create a base VM for deploying GKE Private Clusters

## Base VM creation for the bastion (Do not regenerate if not necessary)

### Delete the VM if it already exists
```bash
gcloud compute instances delete gke-vm-base-bastion \
  --project=clusterapi-369611 \
  --zone=europe-west4-a \
  --quiet
```

### Create the VM with the following command
```bash
gcloud compute instances create gke-vm-base-bastion \
  --project=clusterapi-369611 \
  --zone=europe-west4-a \
  --machine-type=n2-standard-8 \
  --subnet=default \
  --tags=bastion \
  --image-family=ubuntu-2004-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=200GB \
  --scopes=https://www.googleapis.com/auth/cloud-platform \
  --metadata=startup-script-url=gs://gke-vm-base-bastion/scripts/gke-vm-base-bastion.sh 2>/dev/null
```

### Fixed issues
WARNING: You have selected a disk size of under [200GB]. This may result in poor I/O performance. For more information, see: https://developers.google.com/compute/docs/disks#performance.
WARNING: Some requests generated warnings:
 - Disk size: '50 GB' is larger than image size: '10 GB'. You might need to resize the root repartition manually if the operating system does not support automatic resizing. See https://cloud.google.com/compute/docs/disks/add-persistent-disk#resize_pd for details.
WARNING:
To increase the performance of the tunnel, consider installing NumPy. For instructions,
please see https://cloud.google.com/iap/docs/using-tcp-forwarding#increasing_the_tcp_upload_bandwidth

### How to SSH into the Base VM
```bash
gcloud compute ssh --zone "europe-west4-a" "gke-vm-base-bastion" --tunnel-through-iap --project "clusterapi-369611"
```
Note: *--tunnel-through-iap* is used to connect to the VM through the IAP tunnel [Identity-Aware Proxy](https://cloud.google.com/security/products/iap?hl=en)

### How to check the status of the startup script
```bash
sudo journalctl -xfu google-startup-scripts.service
```

### Script de instalación de software en la VM base
Note: *--metadata=startup-script-url=gs://gke-vm-base-bastion/scripts/gke-vm-base-bastion.sh* [GCP Bucket](https://console.cloud.google.com/storage/browser/gke-vm-base-bastion?project=clusterapi-369611)


```bash
#!/bin/bash
# Update and install necessary tools
sudo apt-get update
sudo apt-get install -y apt-transport-https ca-certificates curl software-properties-common gnupg2 dnsutils jq

# Update CA certificates (Avoid issues with certificates)
sudo update-ca-certificates

# Install Docker and specific versions
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo apt-key add -
sudo add-apt-repository "deb [arch=amd64] https://download.docker.com/linux/ubuntu focal stable"
sudo apt-get update
sudo apt-get install -y \
  docker-ce=5:27.0.3-1~ubuntu.20.04~focal \
  docker-ce-cli=5:27.0.3-1~ubuntu.20.04~focal \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin

# Start Docker service
sudo systemctl start docker
sudo systemctl enable docker

# Install Curl and nc
sudo apt-get install -y curl netcat

# Install kubectl
curl -LO "https://dl.k8s.io/release/v1.29.0/bin/linux/amd64/kubectl"
chmod +x kubectl
sudo mv kubectl /usr/local/bin/
echo 'source <(kubectl completion bash)' >>~/.bashrc
echo 'alias k=kubectl' >>~/.bashrc
echo 'complete -o default -F __start_kubectl k' >>~/.bashrc

# Install clusterctl
curl -L https://github.com/kubernetes-sigs/cluster-api/releases/download/v1.5.3/clusterctl-linux-amd64 -o clusterctl
chmod +x clusterctl
sudo mv clusterctl /usr/local/bin/
echo 'source <(clusterctl completion bash)' >>~/.bashrc

# Install Helm
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
echo 'source <(helm completion bash)' >>~/.bashrc

# Install Google Cloud CLI
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] http://packages.cloud.google.com/apt cloud-sdk main" | sudo tee -a /etc/apt/sources.list.d/google-cloud-sdk.list
curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo apt-key --keyring /usr/share/keyrings/cloud.google.gpg add -
sudo apt-get update
sudo apt-get install -y google-cloud-sdk google-cloud-cli google-cloud-sdk-gke-gcloud-auth-plugin
echo 'source /snap/google-cloud-cli/current/completion.bash.inc' >>~/.bashrc

# Install pip3
sudo apt-get install -y python3-pip

# Pip upgrade
sudo -H pip3 install --upgrade pip

# Install numpy (IAP tunneling performance improvement)
sudo -H pip3 install numpy --root-user-action=ignore

# Make folder structure
sudo mkdir -p /deployments/ # For storing deployment files
sudo mkdir -p /resources # For storing resources (cloud-provisioner binaries (copy them to /usr/local/bin/), deployment files (autscaler testings), etc.)

# Add permissions to the resources folder
sudo chmod -R 777 /deployments
sudo chmod -R 777 /resources

# Copy [autoscaler-trigger-deployment.yaml](https://console.cloud.google.com/storage/browser/gke-vm-base-bastion/resources?pageState=(%22StorageObjectListTable%22:(%22f%22:%22%255B%255D%22))&project=clusterapi-369611) file from bucket to resources folder
sudo gsutil cp gs://gke-vm-base-bastion/resources/autoscaler-trigger-deployment.yaml /resources/

# Authenticate with Google Cloud
gcloud auth configure-docker europe-docker.pkg.dev

# Pull images
sudo docker pull stratio-releases.repo.stratio.com/stratio/stratio-capi-image:0.17.0-0.5.8


# Tag image to stratio-capi-image:v1.27.0
sudo docker tag stratio-releases.repo.stratio.com/stratio/stratio-capi-image:0.17.0-0.5.8 stratio-capi-image:v1.27.0

# Add CLOUDSDK_PYTHON_SITEPACKAGES=1 to .bashrc and .profile (avoid numpy issues)
echo 'export CLOUDSDK_PYTHON_SITEPACKAGES=1' >> ~/.bashrc
echo 'export CLOUDSDK_PYTHON_SITEPACKAGES=1' >> ~/.profile
```

### Brief of tools installed in alphabetical order
- apt-transport-https: Allows the use of the https protocol for APT repositories.
- ca-certificates: Contains the set of CA certificates chosen by the Mozilla Foundation for use with the Internet PKI.
- clusterctl: Command-line tool that helps you create, scale, upgrade, and manage Kubernetes clusters.
- containerd.io: Industry-standard core container runtime.
- curl: Command-line tool for transferring data with URL syntax.
- dnsutils: Provides basic DNS tools for the Linux operating system.
- docker-buildx-plugin: CLI plugin that extends the docker command with full support of the features provided by Moby BuildKit builder toolkit.
- docker-ce: Open platform for developing, shipping, and running applications.
- docker-ce-cli: Provides command-line interface functionalities for Docker.
- docker-compose-plugin: Tool for defining and running multi-container Docker applications.
- gnupg2: Provides the GNU Privacy Guard suite of programs.
- google-cloud-sdk: Set of tools for managing resources and applications hosted on Google Cloud.
- helm: Package manager for Kubernetes that helps you define, install, and upgrade complex Kubernetes applications.
- jq: Lightweight and flexible command-line JSON processor.
- kubectl: Command-line tool for controlling Kubernetes clusters.
- netcat: Unix utility that reads and writes data across network connections, using TCP or UDP protocols.
- numpy: Package for scientific computing with Python. (IAP tunneling performance improvement)
- software-properties-common: Provides an abstraction of the used apt repositories.

## How to generate the image from the VM

### Stop the VM
```bash
gcloud compute instances stop gke-vm-base-bastion \
  --project=clusterapi-369611 \
  --zone=europe-west4-a
```

### Remove image if already exists
```bash
gcloud compute images delete gke-vm-base-bastion-image \
  --project=clusterapi-369611 \
  --quiet
```

### Create the image from the disk

```bash
gcloud compute images create gke-vm-base-bastion-image \
  --project=clusterapi-369611 \
  --source-disk=gke-vm-base-bastion \
  --source-disk-zone=europe-west4-a \
  --force
```

## Create a new VM with the image
```bash
gcloud compute instances create gke-vm-janr \
    --project=clusterapi-369611 \
    --zone=europe-west4-a \
    --machine-type=n2-standard-8 \
    --network=hsbc-demo \
    --subnet=hsbc-demo-cb \
    --no-address \
    --tags=bastion \
    --image=gke-vm-base-bastion-image \
    --scopes=https://www.googleapis.com/auth/cloud-platform \
    --boot-disk-device-name=gke-vm-janr
```
> changes needed:
>> Instance Name: Replace gke-vm-janr with the desired instance name.
>> Machine Type: Adjust n2-standard-8 according to the required performance and workload needs.
>> Network and Subnet: Replace hsbc-demo and default with the specific network and subnet configurations as required for your environment.
>> Image: Confirm that the image gke-vm-base-bastion-image exists and is the correct base image for the VM. Adjust if using a different image.
>> Boot Disk Name: Ensure the disk device name gke-vm-janr matches the instance name or is appropriately set for organizational standards.

## How to SSH into the new VM (ui can be used too)
> requirements:
>> role: iap.tunnelResourceAccessor on service account
```bash
gcloud compute ssh --zone "europe-west4-a" "gke-vm-janr" --tunnel-through-iap --project "clusterapi-369611"
```

## How to upload files to the VM
```bash
gcloud compute scp Descargas/cloud-provisioner-0.17.0-0.6.0-5c007c2.tar.gz gke-vm-janr:/resources/
```
```bash
ls -lrth /resources/
```