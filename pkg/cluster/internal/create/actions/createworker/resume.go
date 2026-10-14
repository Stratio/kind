package createworker

import (
	"strings"

	"sigs.k8s.io/kind/pkg/cluster/nodes"
	"sigs.k8s.io/kind/pkg/commons"
	"sigs.k8s.io/kind/pkg/errors"
)

// Helpers that make createworker safe to run again on the same bootstrap container (create --resume, PLT-4965).

const privateCNIMarker = "/kind/.private-cni-done"

// stopLeftovers kills the CLIs a killed cloud-provisioner left running in the container (exact process names only)
const stopLeftovers = "pkill -9 -x 'clusterctl|helm|kubectl' || true"

// applyCreated turns a `kubectl create <kind>` into create-or-update, so a resume does not fail with AlreadyExists.
// Server-side: a client-side apply would copy the whole object (secret data included) into last-applied-configuration.
const applyCreated = " --dry-run=client -o yaml | kubectl apply --server-side --force-conflicts -f -"

// applyCreatedIn is applyCreated for a command run against another cluster.
func applyCreatedIn(kubeconfig string) string {
	return " --dry-run=client -o yaml | kubectl --kubeconfig " + kubeconfig + " apply --server-side --force-conflicts -f -"
}

// stepDone records the last completed step in the host state file; it is only an index, live checks decide.
func stepDone(step string, change ...func(*commons.CreateState)) {
	_ = commons.UpdateCreateState(func(s *commons.CreateState) {
		s.LastCompletedStep = step
		for _, c := range change {
			c(s)
		}
	})
}

func kindFileExists(n nodes.Node, path string) bool {
	out, err := commons.ExecuteCommand(n, "test -f "+path+" && echo yes || echo no", 1, 1)
	return err == nil && strings.TrimSpace(out) == "yes"
}

// checkWorkloadAccess fails early when the workload kubeconfig in the bootstrap container no longer works: after the
// pivot nothing refreshes its static EKS token, and minting one in-process is not implemented yet.
func checkWorkloadAccess(n nodes.Node) error {
	if _, err := commons.ExecuteCommand(n, "kubectl --kubeconfig "+kubeconfigPath+" get ns kube-system -o name", 5, 3); err != nil {
		return errors.Wrap(err, "cannot reach the workload cluster with "+kubeconfigPath+" (its token may have expired after the pivot); resuming a pivoted create needs a fresh workload kubeconfig")
	}
	return nil
}

// clusterMoveState reads "<name>/<spec.paused>" of a CAPI Cluster; empty when the Cluster does not exist.
func clusterMoveState(n nodes.Node, kubeconfig, namespace, name string) (exists bool, paused bool, err error) {
	k := ""
	if kubeconfig != "" {
		k = " --kubeconfig " + kubeconfig
	}
	out, err := commons.ExecuteCommand(n, "kubectl"+k+" -n "+namespace+" get cluster "+name+" --ignore-not-found -o jsonpath='{.metadata.name}/{.spec.paused}'", 5, 3)
	if err != nil {
		return false, false, err
	}
	out = strings.TrimSpace(out)
	return out != "", strings.HasSuffix(out, "/true"), nil
}

// movePrecheck returns true when the pivot already happened. A move killed midway leaves the source Cluster paused and
// later moves are refused; unpausing it lets the same move complete. A move onto a workload cluster that already
// holds the Cluster while the source is not paused is refused: it would overwrite the fields CAPA wrote there.
func movePrecheck(n nodes.Node, namespace, name string) (bool, error) {
	srcExists, srcPaused, err := clusterMoveState(n, "", namespace, name)
	if err != nil {
		return false, err
	}
	dstExists, _, err := clusterMoveState(n, kubeconfigPath, namespace, name)
	if err != nil {
		return false, err
	}
	moved, unpause, err := moveDecision(srcExists, srcPaused, dstExists)
	if err != nil {
		return false, errors.Wrapf(err, "Cluster %s/%s", namespace, name)
	}
	if unpause {
		c := "kubectl -n " + namespace + " patch cluster " + name + ` --type merge -p '{"spec":{"paused":null}}'`
		if _, err := commons.ExecuteCommand(n, c, 5, 3); err != nil {
			return false, errors.Wrap(err, "failed to unpause the source Cluster of an interrupted move")
		}
	}
	return moved, nil
}

// bootstrapMoveState tells a resume, from the bootstrap only, whether the move already happened and whether the operator
// may be installed there again. The workload kubeconfig file only exists once the Cluster did, and only the move
// removes the Cluster from the bootstrap; a paused Cluster is a move killed midway.
func bootstrapMoveState(n nodes.Node, namespace, name string) (pivoted bool, installOperator bool, err error) {
	exists, paused, err := clusterMoveState(n, "", namespace, name)
	if err != nil {
		return false, false, err
	}
	if !exists {
		return kindFileExists(n, kubeconfigPath), !kindFileExists(n, kubeconfigPath), nil
	}
	return false, !paused, nil
}

// bootstrapHas reports whether the bootstrap cluster still holds the object.
func bootstrapHas(n nodes.Node, namespace, kind, name string) bool {
	out, err := commons.ExecuteCommand(n, "kubectl -n "+namespace+" get "+kind+" "+name+" --ignore-not-found -o name", 5, 3)
	return err == nil && strings.TrimSpace(out) != ""
}

// resumePivoted tells whether the CAPI objects already live in the workload cluster: a move started by a killed CLI can
// finish in the container before the state file records it.
func resumePivoted(statePivoted, srcExists, dstExists bool) bool {
	return statePivoted || (!srcExists && dstExists)
}

func moveDecision(srcExists, srcPaused, dstExists bool) (moved bool, unpause bool, err error) {
	switch {
	case !srcExists && dstExists:
		return true, false, nil
	case !srcExists:
		return false, false, errors.New("is neither in the bootstrap nor in the workload cluster")
	case srcPaused:
		return false, true, nil
	case dstExists:
		return false, false, errors.New("exists in both clusters and the bootstrap copy is not paused; moving again would overwrite what CAPA wrote in the workload cluster, check it by hand")
	}
	return false, false, nil
}
