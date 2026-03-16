# Cloud-Provisioner Upgrade Guide

**Version Migration:** `0.7.X` → `0.8.0`

---

## 📋 Table of Contents

1. [Scope](#1️⃣-scope)
2. [Requirements](#2️⃣-requirements)
3. [Preparation](#3️⃣-preparation)
4. [Running the Container](#4️⃣-running-the-container)
5. [Executing the Upgrade](#5️⃣-executing-the-upgrade)
6. [Upgrade Process Overview](#6️⃣-upgrade-process-overview)
7. [Monitoring During Upgrade](#7️⃣-monitoring-during-upgrade)
8. [Final Verification](#8️⃣-final-verification)
9. [Troubleshooting](#9️⃣-troubleshooting)
10. [Important Notes](#🔟-important-notes)

---

## 1️⃣ Scope

### What Gets Updated

#### Common Components (All Platforms)
- **cluster-operator:** `0.6.0`
- **cert-manager:** `v1.19.1`
- **flux2:** `2.17.2`
- **tigera-operator:** `v3.30.2`
- **cluster-autoscaler:** `9.52.1`

#### Cluster API Core
- **cluster-api (CAPI):** `v1.10.8`
- **bootstrap-kubeadm:** `v1.10.8`
- **control-plane-kubeadm:** `v1.10.8`

#### Infrastructure Providers (Platform-Specific)
- **CAPA (AWS):** `v2.9.2`
- **CAPZ (Azure):** `v1.21.1`
- **CAPG (GCP):** `1.6.1-0.4.0` _(not yet tested)_

#### Platform-Specific Charts

**EKS (if installed):**
- **aws-load-balancer-controller:** `1.14.1`

**Azure VMs:**
- **azuredisk-csi-driver:** `1.33.5`
- **azurefile-csi-driver:** `1.34.1`
- **cloud-provider-azure:** `1.34.2`

### Supported Platforms

| Platform | Type |
|----------|------|
| **EKS** | Managed |
| **Azure VMs** | Unmanaged |

---

## 2️⃣ Requirements

### 2.1 Technical Requirements

- ✅ Docker installed
- ✅ Cluster access via kubeconfig
- ✅ Valid `secrets.yml` (Ansible Vault)

### 2.2 Functional Requirements

Before executing the upgrade, verify the cluster meets these conditions:

#### ✅ KeosCluster Ready

```bash
kubectl get keosclusters.installer.stratio.com -A
```

**Expected Output:**
- `READY = true`
- `PHASE = Provisioned`

#### ✅ Nodes Healthy

```bash
kubectl get nodes
```

**Expected:** All nodes with `STATUS = Ready`

#### ✅ Machines Running

```bash
kubectl get machines -A
```

**Expected:** All with `PHASE = Running`

#### ✅ MachineDeployments Healthy

```bash
kubectl get machinedeployments -A
```

**Expected:**
- `READY = REPLICAS`
- `UNAVAILABLE = 0`

#### ✅ Cluster API Pods Healthy

```bash
kubectl get pods -n capi-system
kubectl get pods -n capa-system   # or capz-system / capg-system
```

**Expected:** All pods in `Running` state and logs without errors

#### ✅ Cluster-Operator Ready

```bash
kubectl get helmrelease cluster-operator -n kube-system
```

**Expected:** `Ready = True`

---

## 3️⃣ Preparation

Create a local backup directory:

```bash
mkdir -p backup
```

> **Note:** This directory must be mounted inside the container at `/upgrade/backup`

---

## 4️⃣ Running the Container

### For EKS

```bash
docker run \
  --name cloud-provisioner-upgrade-0.8.0 \
  --net host \
  -it \
  -v <path>/secrets.yml:/upgrade/secrets.yml \
  -v <path>/.kube/config:/upgrade/.kube/config \
  -v <path>/backup:/upgrade/backup \
  cloud-provisioner-upgrade:0.17.0-0.8.0
```

### For Azure

```bash
docker run \
  --name cloud-provisioner-upgrade-0.8.0 \
  --net host \
  -it \
  -v <path>/secrets.yml:/upgrade/secrets.yml \
  -v <path>/.kube/config:/upgrade/.kube/config \
  -v <path>/backup:/upgrade/backup \
  cloud-provisioner-upgrade:0.17.0-0.8.0
```

---

## 5️⃣ Important Notes

### Registry Configuration

| Scenario | Action Required |
|----------|----------------|
| **Without `--private` flag** | Clusterctl uses `registry.k8s.io` images |
| **Private registry only** | **MUST** use `--private` flag |

> ⚠️ **Warning:** If your cluster can only access a private registry, the `--private` flag is **mandatory**.

---

## 6️⃣ Executing the Upgrade

Inside the container, run:

```bash
python3 upgrade-provisioner.py -p <vault-password>
```

### Optional Flags

In order to check available flags and their usage, run:

```bash
python3 upgrade-provisioner.py --help
```

---

## 7️⃣ Upgrade Process Overview

The script executes the following workflow:

1. **Validation**
   - Validates kubeconfig and secrets
   - Configures cloud credentials

2. **Pre-Upgrade**
   - **Scales cluster-autoscaler to 0** (prevents node scaling during upgrade)

3. **Backup**
   - CAPX components (using `clusterctl move`)
   - Capsule webhook configurations

4. **Capsule Preparation**
   - **Modifies capsule webhooks** to exclude critical namespaces from validation/mutation
   - Ensures tenant isolation doesn't block component upgrades

5. **Chart Updates**
   - Updates base charts (cert-manager, flux2, tigera-operator, etc.)
   - Updates provider-specific charts (aws-load-balancer-controller, Azure CSI drivers)
   - **Restores capsule webhooks** to original configuration

6. **Cluster-Operator Preparation**
   - **Suspends cluster-operator HelmRelease**
   - **Stops keoscluster-controller deployment**
   - **Disables keoscluster validating/mutating webhooks**
   - Updates ClusterConfig with new component versions

7. **CAPI Upgrade**
   ```bash
   clusterctl upgrade apply \
     --core cluster-api:v1.10.8 \
     --infrastructure <provider>:<version> \
     --wait-providers
   ```

8. **Post-Upgrade**
   - **Restores keoscluster webhooks**
   - **Starts keoscluster-controller deployment**
   - **Unsuspends cluster-operator HelmRelease**
   - Waits for cluster-operator to be ready
   - Waits for KeosCluster ready state
   - **Restores cluster-autoscaler replicas to 2**

---

## 8️⃣ Monitoring During Upgrade

### Watch Autoscaler

```bash
watch -n2 kubectl -n kube-system get deploy cluster-autoscaler-clusterapi-cluster-autoscaler
```

### Monitor Critical Pods

```bash
watch -n2 'kubectl get pods -A | grep -E "cluster-operator|capi|cap.|autoscaler|tigera"'
```

### Track HelmReleases

```bash
watch -n2 kubectl get helmreleases -A
```

---

## 9️⃣ Final Verification

### Verify Container Images

```bash
kubectl get pods -A -o json \
| jq -r '
  .items[]
  | (
      [.spec.containers[]?.image] +
      [.spec.initContainers[]?.image]
    )
  | .[]
' \
| sort -u
```

**Expected Versions:**
- CAPI → `v1.10.8`
- CAPA → `v2.9.2`
- CAPG → `1.6.1-0.4.0`
- CAPZ → `v1.21.1`

### Verify Chart Versions

```bash
kubectl get hr -A -o json \
| jq -r '
  .items[]
  | "\(.metadata.namespace)/\(.metadata.name) -> \(.spec.chart.spec.chart)@\(.spec.chart.spec.version)"
' \
| sort -u
```

### Final State Check

```bash
kubectl get keoscluster -A
kubectl get machines -A
kubectl get machinedeployments -A
kubectl get nodes
```

**Expected State:**
- ✅ All resources: `Ready`
- ✅ All machines: `Running`
- ✅ No `Unavailable` replicas

---

## 📞 Support

For additional assistance or issues not covered in this guide, please contact the platform team or refer to the project documentation.
