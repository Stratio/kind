package createworker

import (
	"encoding/json"
	"os"
	"strings"
	"time"

	"sigs.k8s.io/kind/pkg/cluster/nodes"
	"sigs.k8s.io/kind/pkg/commons"
	"sigs.k8s.io/kind/pkg/errors"
)

// Helpers that make createworker safe to run again on the same bootstrap container (create --resume, PLT-4965).

const privateCNIMarker = "/kind/.private-cni-done"

// stopLeftovers kills the CLIs a killed cloud-provisioner left running in the container (exact process names only) and
// prints "<pid> <name>" for each one
const stopLeftovers = "pgrep -l -x 'clusterctl|helm|kubectl'; pkill -9 -x 'clusterctl|helm|kubectl'; true"

// moveLeftoverWait bounds the wait for a clusterctl move the killed CLI left running; past it the move is stopped
const moveLeftoverWait = 10 * time.Minute

// waitInFlightMove lets a clusterctl move that a killed CLI left running finish: stopped midway it leaves the Cluster
// paused, in both clusters or only in the workload
func waitInFlightMove(n nodes.Node, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		out, err := commons.ExecuteCommand(n, "pgrep -f '^clusterctl move' || true", 1, 1)
		if err != nil {
			return errors.Wrap(err, "failed to look for a clusterctl move left running")
		}
		if strings.TrimSpace(out) == "" {
			return nil
		}
		time.Sleep(5 * time.Second)
	}
	return nil
}

// unpauseMovedCommands resumes the moved Cluster and its ClusterClasses in the workload, the last thing clusterctl move
// does (cluster-api v1.13.6 cmd/clusterctl/client/cluster/mover.go:409-418); both commands are no-ops when not paused
func unpauseMovedCommands(kubeconfig, namespace, name string) []string {
	k := "kubectl --kubeconfig " + kubeconfig + " -n " + namespace
	return []string{
		k + " patch cluster " + name + ` --type merge -p '{"spec":{"paused":null}}'`,
		k + " get clusterclasses -o name | xargs -r -I{} " + k + " annotate {} cluster.x-k8s.io/paused-",
	}
}

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

type helmReleaseRecord struct {
	namespace, name, secret string
}

// fluxPendingGrace outlasts the default HelmRelease timeout (5m): a pending release Flux owns may be its own operation
const fluxPendingGrace = 6 * time.Minute

// pendingReleaseRecords returns the record secret of each release a killed helm left pending (`helm list -A --pending -o
// json`): dropping the pending revision unlocks the next upgrade --install, which adopts the objects already created.
func pendingReleaseRecords(helmListJSON string) ([]helmReleaseRecord, error) {
	var releases []struct {
		Name      string `json:"name"`
		Namespace string `json:"namespace"`
		Revision  string `json:"revision"`
		Status    string `json:"status"`
	}
	if err := json.Unmarshal([]byte(helmListJSON), &releases); err != nil {
		return nil, errors.Wrap(err, "failed to parse the Helm release list")
	}
	var records []helmReleaseRecord
	for _, r := range releases {
		if strings.HasPrefix(r.Status, "pending-") {
			records = append(records, helmReleaseRecord{namespace: r.Namespace, name: r.Name, secret: "sh.helm.release.v1." + r.Name + ".v" + r.Revision})
		}
	}
	return records, nil
}

// fluxOwnedReleases returns "<namespace>/<release>" of every Helm release a Flux HelmRelease manages
// (`kubectl get helmreleases.helm.toolkit.fluxcd.io -A -o json`)
func fluxOwnedReleases(helmReleasesJSON string) (map[string]bool, error) {
	var list struct {
		Items []struct {
			Metadata struct{ Name, Namespace string }
			Spec     struct{ ReleaseName, TargetNamespace, StorageNamespace string }
			Status   struct {
				History []struct{ Name, Namespace string }
			}
		}
	}
	if err := json.Unmarshal([]byte(helmReleasesJSON), &list); err != nil {
		return nil, errors.Wrap(err, "failed to parse the HelmRelease list")
	}
	owned := map[string]bool{}
	for _, hr := range list.Items {
		if h := hr.Status.History; len(h) > 0 && h[0].Name != "" {
			owned[h[0].Namespace+"/"+h[0].Name] = true
			continue
		}
		// Flux defaults: release <targetNamespace>-<name> when it targets another namespace, stored in its own namespace
		name, ns := hr.Spec.ReleaseName, hr.Spec.StorageNamespace
		if name == "" {
			name = hr.Metadata.Name
			if hr.Spec.TargetNamespace != "" {
				name = hr.Spec.TargetNamespace + "-" + hr.Metadata.Name
			}
		}
		if ns == "" {
			ns = hr.Metadata.Namespace
		}
		owned[ns+"/"+name] = true
	}
	return owned, nil
}

// replaySuspendedAnnotation marks the HelmReleases a resume suspended, so it only resumes those
const replaySuspendedAnnotation = "cloud-provisioner.stratio.com/suspended-for-resume"

// helmReleasesForReplay returns "<namespace> <name>" of the HelmReleases to suspend (not suspended yet) or to resume
// (suspended by a resume) from `kubectl get helmreleases.helm.toolkit.fluxcd.io -A -o json`
func helmReleasesForReplay(helmReleasesJSON string, suspend bool) ([]string, error) {
	var list struct {
		Items []struct {
			Metadata struct {
				Name, Namespace string
				Annotations     map[string]string
			}
			Spec struct{ Suspend bool }
		}
	}
	if err := json.Unmarshal([]byte(helmReleasesJSON), &list); err != nil {
		return nil, errors.Wrap(err, "failed to parse the HelmRelease list")
	}
	var out []string
	for _, hr := range list.Items {
		_, ours := hr.Metadata.Annotations[replaySuspendedAnnotation]
		if (suspend && !hr.Spec.Suspend) || (!suspend && ours) {
			out = append(out, hr.Metadata.Namespace+" "+hr.Metadata.Name)
		}
	}
	return out, nil
}

// setFluxSuspendedForReplay suspends the workload HelmReleases while a resume replays the steps that run helm there
// (Flux and the replay upgrading the same release race: "release: already exists", "another operation is in
// progress"), and resumes them afterwards with a reconcile request. Before Flux is installed there is nothing to do.
func setFluxSuspendedForReplay(n nodes.Node, kubeconfig string, suspend bool) error {
	k := "kubectl --kubeconfig " + kubeconfig
	out, err := commons.ExecuteCommand(n, k+" get helmreleases.helm.toolkit.fluxcd.io -A -o json", 5, 3)
	if err != nil {
		return nil
	}
	hrs, err := helmReleasesForReplay(out, suspend)
	if err != nil {
		return err
	}
	patch := `{"spec":{"suspend":true},"metadata":{"annotations":{"` + replaySuspendedAnnotation + `":"true"}}}`
	if !suspend {
		patch = `{"spec":{"suspend":null},"metadata":{"annotations":{"` + replaySuspendedAnnotation + `":null,"reconcile.fluxcd.io/requestedAt":"` + time.Now().UTC().Format(time.RFC3339) + `"}}}`
	}
	for _, hr := range hrs {
		nsName := strings.Fields(hr)
		c := k + " -n " + nsName[0] + " patch helmreleases.helm.toolkit.fluxcd.io " + nsName[1] + " --type merge -p '" + patch + "'"
		if _, err := commons.ExecuteCommand(n, c, 5, 3); err != nil {
			return errors.Wrap(err, "failed to set suspend on HelmRelease "+nsName[0]+"/"+nsName[1])
		}
	}
	return nil
}

// releasePendingHelm drops the pending revisions a killed helm left (kubeconfig "" = bootstrap); otherwise every later
// helm upgrade of that release fails with "another operation is in progress". In the workload a release Flux manages
// gets fluxPendingGrace to leave its pending state on its own before it is treated as a leftover.
func releasePendingHelm(n nodes.Node, kubeconfig string) error {
	k := ""
	if kubeconfig != "" {
		k = " --kubeconfig " + kubeconfig
	}
	deadline := time.Now().Add(fluxPendingGrace)
	for {
		out, err := commons.ExecuteCommand(n, "helm"+k+" list -A --pending -o json", 5, 3)
		if err != nil {
			return errors.Wrap(err, "failed to list the pending Helm releases")
		}
		records, err := pendingReleaseRecords(out)
		if err != nil {
			return err
		}
		owned := map[string]bool{}
		if k != "" && len(records) > 0 && time.Now().Before(deadline) {
			// before Flux is installed the HelmRelease kind does not exist: nothing is owned
			if hrs, err := commons.ExecuteCommand(n, "kubectl"+k+" get helmreleases.helm.toolkit.fluxcd.io -A -o json", 5, 3); err == nil {
				if owned, err = fluxOwnedReleases(hrs); err != nil {
					return err
				}
			}
		}
		waiting := false
		for _, r := range records {
			if owned[r.namespace+"/"+r.name] {
				waiting = true
				continue
			}
			c := "kubectl" + k + " -n " + r.namespace + " delete secret " + r.secret + " --ignore-not-found"
			if _, err := commons.ExecuteCommand(n, c, 5, 3); err != nil {
				return errors.Wrap(err, "failed to drop the pending Helm revision "+r.namespace+"/"+r.secret)
			}
		}
		if !waiting {
			return nil
		}
		time.Sleep(15 * time.Second)
	}
}

// skipBackup is true for a resume after a move killed midway: clusterctl refuses to back up a paused Cluster, the move
// step completes that move and the previous backup is kept.
func skipBackup(resume, srcPaused bool) bool {
	return resume && srcPaused
}

// replaceDir puts src in place of dst, leaving dst untouched when src does not exist
func replaceDir(src, dst string) error {
	if _, err := os.Stat(src); err != nil {
		return err
	}
	if err := os.RemoveAll(dst); err != nil {
		return err
	}
	return os.Rename(src, dst)
}

// resumePivoted tells whether the CAPI objects already live in the workload cluster: a move started by a killed CLI can
// finish in the container before the state file records it. The Cluster only leaves the bootstrap through the move.
func resumePivoted(statePivoted, srcExists, kubeconfigSaved bool) bool {
	return statePivoted || (!srcExists && kubeconfigSaved)
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
