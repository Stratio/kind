# Recovery: `<cluster>-kubeconfig` secret — GKE (CAPG)

## Context

When the `<cluster>-kubeconfig` secret is accidentally deleted from the cluster namespace, CAPI controllers fail in cascade with:

```
failed to retrieve kubeconfig secret for Cluster <ns>/<cluster>: Secret "<cluster>-kubeconfig" not found
```

This affects: `MachineHealthCheck`, `MachinePool`, and any controller that needs to connect to the workload cluster.

## Which controller manages this secret

**`capi-controller-manager` does NOT regenerate this secret.** The owner is the `GCPManagedControlPlane`, managed exclusively by `capg-controller-manager`. Restarting CAPI core has no effect.

## How CAPG works internally

`capg-controller-manager` regenerates the secret on every reconciliation via `reconcileKubeconfig()` in `cloud/services/container/clusters/kubeconfig.go`:

- Secret **does not exist** → `createCAPIKubeconfigSecret()`: generates a GCP token and creates the secret
- Secret **exists** → `updateCAPIKubeconfigSecret()`: regenerates the token and overwrites

The GCP token lasts **1 hour** but CAPG rotates it automatically on every reconciliation cycle.

`reconcileKubeconfig()` only runs if **all** the following conditions are met:

| Condition | If it fails... |
|---|---|
| Cluster not paused | exits immediately |
| `GCPManagedCluster.Status.Ready == true` | requeue, kubeconfig not reconciled |
| GKE cluster status == `RUNNING` | requeue or error depending on state |
| No pending updates on the GKE cluster | exits after the update, kubeconfig not reconciled |

### Secret markers

| Marker | Expected value |
|---|---|
| Namespace | `cluster-<name>` |
| Type | `cluster.x-k8s.io/secret` |
| Data key | `value` |
| OwnerRef kind | `GCPManagedControlPlane` |

## Pre-flight: verify the GKE cluster has no pending operations

```bash
gcloud container clusters describe <cluster> \
  --zone=<zone> --project=<project> \
  --format='table(name,status,currentNodeVersion)' | cat
```

- `status: RUNNING` → no pending operations, proceed
- `status: RECONCILING` / `PROVISIONING` / `UPGRADING` → operation in progress; the annotate will not work until it completes

## Step 1 — Verify the secret state

```bash
kubectl get secret <cluster>-kubeconfig -n cluster-<cluster> \
  -o jsonpath='{.data}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('OK - key:', list(d.keys()))"
```

- Result: `OK - key: ['value']` → go to **Step 2a**
- Not found or wrong key → go to **Step 2b**

## Step 2a — Force CAPG reconciliation (secret exists with key `value`)

```bash
GCP_MCP_NAME=$(kubectl get gcpmanagedcontrolplane -n cluster-<cluster> \
  -o jsonpath='{.items[0].metadata.name}')

kubectl annotate gcpmanagedcontrolplane $GCP_MCP_NAME -n cluster-<cluster> \
  reconcile.cluster.x-k8s.io/requestedAt="$(date -u +%Y-%m-%dT%H:%M:%SZ)" --overwrite
```

CAPG calls `updateCAPIKubeconfigSecret()`, generates a fresh token, and writes it to the secret. The rotation cycle is active from this point.

> **Note:** The controller has no watch on secrets — without the annotate, the secret is not regenerated automatically even if the controller is running.

## Step 2b — Recreate the secret from scratch (secret does not exist)

```bash
CLUSTER=<cluster>
NAMESPACE=cluster-${CLUSTER}

PROJECT=$(kubectl get gcpmanagedcontrolplane $CLUSTER -n $NAMESPACE \
  -o jsonpath='{.spec.project}')
LOCATION=$(kubectl get gcpmanagedcontrolplane $CLUSTER -n $NAMESPACE \
  -o jsonpath='{.spec.location}')

kubectl get secret capg-manager-bootstrap-credentials -n capg-system \
  -o jsonpath='{.data.credentials}' | base64 -d > /tmp/capg-sa.json
gcloud auth activate-service-account --key-file=/tmp/capg-sa.json
TOKEN=$(gcloud auth print-access-token)

ENDPOINT=$(gcloud container clusters describe $CLUSTER \
  --zone=$LOCATION --project=$PROJECT --format='value(endpoint)')
CA_CERT=$(gcloud container clusters describe $CLUSTER \
  --zone=$LOCATION --project=$PROJECT \
  --format='value(masterAuth.clusterCaCertificate)')

CONTEXT="gke_${PROJECT}_${LOCATION}_${CLUSTER}"

kubectl create secret generic ${CLUSTER}-kubeconfig \
  -n $NAMESPACE \
  --from-literal=value="$(cat <<EOF
apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: ${CA_CERT}
    server: https://${ENDPOINT}
  name: ${CONTEXT}
contexts:
- context:
    cluster: ${CONTEXT}
    user: ${CONTEXT}
  name: ${CONTEXT}
current-context: ${CONTEXT}
kind: Config
preferences: {}
users:
- name: ${CONTEXT}
  user:
    token: ${TOKEN}
EOF
)"

rm /tmp/capg-sa.json
```

After creating the secret, also run the annotate from **Step 2a** so CAPG takes ownership.

## Step 3 — Verify recovery

```bash
# CAPG logs — should show successful reconciliation
kubectl logs -n capg-system deploy/capg-controller-manager --since=2m \
  | grep -E "<cluster>|kubeconfig|error" | tail -20

# CAPI logs — should return 0
kubectl logs -n capi-system deploy/capi-controller-manager --since=2m \
  | grep "<cluster>-kubeconfig.*not found" | wc -l
```

## Known issues

### Loop on `resourceLabels` causing stuck "no pending operations" gate

CAPG does a **replace-all** of `resourceLabels` (not merge): if GKE automatically adds managed labels (e.g. `goog-gke-node-pool-provisioning-model: on-demand`), CAPG detects them as drift on every cycle and re-applies, which GKE translates into a rolling node pool replacement. Logs show `"Cluster is currently undergoing another operation"` in a loop.

**Fix:** add those labels to `additional_labels` in the cluster descriptor so the state converges.

### Silent error in `updateCAPIKubeconfigSecret`

In `kubeconfig.go`, when the secret exists but `updateCAPIKubeconfigSecret()` fails internally, the function returns `nil` instead of the real error — the reconciliation appears successful but the update was silently skipped. Only affects the update path (secret exists), not the creation path (secret does not exist). Diagnose via logs.
