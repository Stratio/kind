# Recovery: `<cluster>-kubeconfig` secret — Azure VMs (CAPZ)

## Context

When the `<cluster>-kubeconfig` secret is accidentally deleted from the cluster namespace, CAPI controllers fail in cascade with:

```
failed to retrieve kubeconfig secret for Cluster <ns>/<cluster>: Secret "<cluster>-kubeconfig" not found
```

This affects: `MachineHealthCheck`, `MachinePool`, and any controller that needs to connect to the workload cluster.

## Which controller manages this secret

For Azure VM clusters (non-managed), the kubeconfig is **not managed by CAPZ**. It is managed by **CAPI core** via the `KubeadmControlPlane` (KCP) controller:

```
capi-kubeadm-control-plane-controller-manager
```

Restarting `capz-controller-manager` has **no effect** on the kubeconfig for Azure VM clusters.

## How KubeadmControlPlane works internally

Unlike GKE (token-based) and EKS (presigned token), Azure VMs use a **client certificate** signed by the cluster CA (`kubernetes-admin`, `system:masters`). The cert has a validity of ~1 year and KCP rotates it automatically when it approaches expiry.

KCP does **not** regenerate the kubeconfig on every reconciliation — it creates it once and rotates when the cert is near expiry. This means a deleted secret is not recreated until the next reconciliation is triggered.

`reconcileKubeconfig()` only runs if **all** the following conditions are met:

| Condition | If it fails... |
|---|---|
| Cluster not paused | exits immediately |
| `Cluster.Status.InfrastructureReady == true` | requeue, kubeconfig not reconciled |
| `Cluster.Spec.ControlPlaneEndpoint.IsValid()` | returns without creating the secret |
| Secret `<cluster>-ca` must exist | requeue after 2 minutes |

### Secret markers

| Marker | Expected value |
|---|---|
| Namespace | `cluster-<name>` |
| Type | `cluster.x-k8s.io/secret` |
| Data key | `value` |
| OwnerRef kind | `KubeadmControlPlane` |

## Step 1 — Verify the `<cluster>-ca` secret exists

This is the critical dependency. Without it, KCP cannot regenerate the kubeconfig.

```bash
kubectl get secret <cluster>-ca -n cluster-<cluster> \
  -o jsonpath='{.data}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('CA keys:', list(d.keys()))"
# Expected: CA keys: ['tls.crt', 'tls.key']
```

- OK → proceed to **Step 2**
- Not found → **critical situation** — see section below

## Step 2 — Force KCP reconciliation

```bash
KCP_NAME=$(kubectl get kubeadmcontrolplane -n cluster-<cluster> \
  -o jsonpath='{.items[0].metadata.name}')

kubectl annotate kubeadmcontrolplane $KCP_NAME -n cluster-<cluster> \
  reconcile.cluster.x-k8s.io/requestedAt="$(date -u +%Y-%m-%dT%H:%M:%SZ)" --overwrite
```

KCP calls `generateKubeconfig()`, reads the `<cluster>-ca` secret, generates a new client certificate, and creates the secret. Recovery time is approximately **4 seconds**.

> **Note (validated in live cluster `azure-k8s135`, 2026-06-24):** The controller has no watch on secrets — without the annotate, the secret is not regenerated automatically. The annotate triggers reconciliation immediately and the secret is regenerated in ~4 seconds. The OwnerRef of the regenerated secret keeps the same KCP UID.

## Step 3 — Verify recovery

```bash
# KCP logs — should show reconciliation
kubectl logs -n capi-kubeadm-control-plane-system \
  deploy/capi-kubeadm-control-plane-controller-manager --since=2m \
  | grep -E "<cluster>|kubeconfig|error" | tail -20

# Confirm the secret exists with key "value"
kubectl get secret <cluster>-kubeconfig -n cluster-<cluster> \
  -o jsonpath='{.data}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('OK - key:', list(d.keys()))"
# Expected: OK - key: ['value']

# CAPI logs — should return 0
kubectl logs -n capi-system deploy/capi-controller-manager --since=2m \
  | grep "<cluster>-kubeconfig.*not found" | wc -l
```

## If `<cluster>-ca` also disappears

This is a different and more critical scenario:

- Without the CA private key, no new client certificates can be generated for the kubeconfig.
- The workload cluster itself continues to function (the CA lives inside the cluster's API server).
- Options: restore from backup, use `clusterctl move` to re-import state, or — if the workload cluster API server is reachable — extract the CA from inside using `kubeadm certs certificate-authority`.

This scenario is outside the scope of this standard recovery procedure.

## Rollout restart as an alternative

```bash
kubectl rollout restart deploy/capi-kubeadm-control-plane-controller-manager \
  -n capi-kubeadm-control-plane-system
```

Works with the same gates as the annotate and affects **all clusters** managed by that KCP instance. Slower than the annotate (pod startup time). The `InfrastructureReady` and `ControlPlaneEndpoint` gates still apply.
