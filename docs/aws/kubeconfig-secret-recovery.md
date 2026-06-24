# Recovery: `<cluster>-kubeconfig` secret — EKS (CAPA)

## Context

When the `<cluster>-kubeconfig` secret is accidentally deleted from the cluster namespace, CAPI controllers fail in cascade with:

```
failed to retrieve kubeconfig secret for Cluster <ns>/<cluster>: Secret "<cluster>-kubeconfig" not found
```

This affects: `MachineHealthCheck`, `MachinePool`, and any controller that needs to connect to the workload cluster.

## Which controller manages this secret

**`capi-controller-manager` does NOT regenerate this secret.** The owner is the `AWSManagedControlPlane`, managed exclusively by `capa-controller-manager`. Restarting CAPI core has no effect.

## How CAPA works internally

`capa-controller-manager` regenerates the secret on every reconciliation via `reconcileKubeconfig()` in `pkg/cloud/services/eks/config.go`:

- Secret **does not exist** → `createCAPIKubeconfigSecret()`: generates a presigned AWS STS token and creates the secret
- Secret **exists** → `updateCAPIKubeconfigSecret()`: regenerates the token and overwrites

The presigned STS token is short-lived and CAPA updates it automatically on every reconciliation cycle.

The secret contains two data keys:
- `value` — kubeconfig with inline token (used by CAPI controllers)
- `relative` — kubeconfig with `tokenFile` for relative path usage

`reconcileKubeconfig()` only runs if **all** the following conditions are met:

| Condition | If it fails... |
|---|---|
| Cluster not paused | exits immediately |
| `Cluster.Status.InfrastructureReady == true` | requeue with period |
| `ControlPlane.Status.Ready == true` (EKS ACTIVE) | return nil, kubeconfig not reconciled |

### Secret markers

| Marker | Expected value |
|---|---|
| Namespace | `cluster-<name>` |
| Type | `cluster.x-k8s.io/secret` |
| Data key | `value` (and `relative`) |
| OwnerRef kind | `AWSManagedControlPlane` |

## Step 1 — Verify the secret state

```bash
kubectl get secret <cluster>-kubeconfig -n cluster-<cluster> \
  -o jsonpath='{.data}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('OK - keys:', list(d.keys()))"
```

- Result includes `value` → go to **Step 2**
- Not found → go to **Step 2** as well (CAPA will create it on next reconciliation)

## Step 2 — Force CAPA reconciliation

```bash
AMCP_NAME=$(kubectl get awsmanagedcontrolplane -n cluster-<cluster> \
  -o jsonpath='{.items[0].metadata.name}')

kubectl annotate awsmanagedcontrolplane $AMCP_NAME -n cluster-<cluster> \
  reconcile.cluster.x-k8s.io/requestedAt="$(date -u +%Y-%m-%dT%H:%M:%SZ)" --overwrite
```

CAPA will regenerate the STS token and create/update the secret on the next reconciliation cycle.

> **Note (validated in live cluster `eks-cl01`, 2026-06-24):** The controller has no watch on secrets — without the annotate, the secret is not regenerated automatically. The annotate triggers reconciliation immediately and the secret is regenerated in ~5 seconds with 3 keys: `value`, `relative`, `token-file`. The OwnerRef of the regenerated secret keeps the same `AWSManagedControlPlane` UID.

> **Pre-flight:** Verify that the `AWSManagedControlPlane` is in `Ready=true` state before annotating:
> ```bash
> kubectl get awsmanagedcontrolplane -n cluster-<cluster>
> ```
> If `Ready=false`, the EKS cluster has an underlying issue that must be resolved first.

## Step 3 — Verify recovery

```bash
# CAPA logs — should show "Reconciling EKS kubeconfigs"
kubectl logs -n capa-system deploy/capa-controller-manager --since=2m \
  | grep -E "<cluster>|kubeconfig|error" | tail -20

# Confirm secret exists with key "value"
kubectl get secret <cluster>-kubeconfig -n cluster-<cluster> \
  -o jsonpath='{.data}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('OK - keys:', list(d.keys()))"
# Expected: OK - keys: ['value', 'relative', 'token-file']

# CAPI logs — should return 0
kubectl logs -n capi-system deploy/capi-controller-manager --since=2m \
  | grep "<cluster>-kubeconfig.*not found" | wc -l
```

## Additional notes

- There is a second secret `<cluster>-user-kubeconfig` with an exec plugin (`aws-iam-authenticator` or `aws eks get-token`) for user access. That secret is created once and not updated — do not confuse it with this one.
- CAPA handles the `No updates are to be performed` error from CloudFormation as a non-fatal condition — the IAM stack is already up to date.
