package createworker

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestPendingReleaseRecords(t *testing.T) {
	// shape of `helm list -A --pending -o json` (helm v3.19)
	list := `[{"name":"cert-manager","namespace":"cert-manager","revision":"1","status":"pending-install","chart":"cert-manager-v1.21.2"},
	{"name":"flux","namespace":"kube-system","revision":"4","status":"pending-upgrade","chart":"flux2-2.17.2"},
	{"name":"tigera-operator","namespace":"tigera-operator","revision":"2","status":"deployed","chart":"tigera-operator-v3.31.4"}]`
	records, err := pendingReleaseRecords(list)
	if err != nil {
		t.Fatal(err)
	}
	want := []helmReleaseRecord{
		{namespace: "cert-manager", name: "cert-manager", secret: "sh.helm.release.v1.cert-manager.v1"},
		{namespace: "kube-system", name: "flux", secret: "sh.helm.release.v1.flux.v4"},
	}
	if !reflect.DeepEqual(records, want) {
		t.Errorf("records=%v, want %v", records, want)
	}
	if records, err := pendingReleaseRecords("[]"); err != nil || len(records) != 0 {
		t.Errorf("empty list: records=%v err=%v", records, err)
	}
	if _, err := pendingReleaseRecords("Error: Kubernetes cluster unreachable"); err == nil {
		t.Error("non-JSON output: no error")
	}
}

func TestFluxOwnedReleases(t *testing.T) {
	// eks-4965-r3 workload, 2026-10-14: every release the create installs has a HelmRelease with status.history
	list := `{"items":[
	{"metadata":{"name":"flux","namespace":"kube-system"},"spec":{"releaseName":"flux"},"status":{"history":[{"name":"flux","namespace":"kube-system"}]}},
	{"metadata":{"name":"cert-manager","namespace":"cert-manager"},"spec":{}},
	{"metadata":{"name":"app","namespace":"flux-system"},"spec":{"targetNamespace":"apps"}},
	{"metadata":{"name":"db","namespace":"flux-system"},"spec":{"releaseName":"pg","storageNamespace":"data"}}]}`
	owned, err := fluxOwnedReleases(list)
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]bool{"kube-system/flux": true, "cert-manager/cert-manager": true, "flux-system/apps-app": true, "data/pg": true}
	if !reflect.DeepEqual(owned, want) {
		t.Errorf("owned=%v, want %v", owned, want)
	}
}

func TestUnpauseMovedCommands(t *testing.T) {
	want := []string{
		`kubectl --kubeconfig /kind/worker-cluster.kubeconfig -n cluster-eks-1 patch cluster eks-1 --type merge -p '{"spec":{"paused":null}}'`,
		"kubectl --kubeconfig /kind/worker-cluster.kubeconfig -n cluster-eks-1 get clusterclasses -o name | xargs -r -I{} kubectl --kubeconfig /kind/worker-cluster.kubeconfig -n cluster-eks-1 annotate {} cluster.x-k8s.io/paused-",
	}
	if got := unpauseMovedCommands(kubeconfigPath, "cluster-eks-1", "eks-1"); !reflect.DeepEqual(got, want) {
		t.Errorf("got %q", got)
	}
}

func TestHelmReleasesForReplay(t *testing.T) {
	list := `{"items":[
	{"metadata":{"name":"flux","namespace":"kube-system"},"spec":{}},
	{"metadata":{"name":"cert-manager","namespace":"cert-manager","annotations":{"cloud-provisioner.stratio.com/suspended-for-resume":"true"}},"spec":{"suspend":true}},
	{"metadata":{"name":"app","namespace":"apps"},"spec":{"suspend":true}}]}`
	toSuspend, err := helmReleasesForReplay(list, true)
	if err != nil {
		t.Fatal(err)
	}
	if want := []string{"kube-system flux"}; !reflect.DeepEqual(toSuspend, want) {
		t.Errorf("suspend: got %v, want %v", toSuspend, want)
	}
	// a HelmRelease suspended by hand (no annotation) stays suspended
	toResume, _ := helmReleasesForReplay(list, false)
	if want := []string{"cert-manager cert-manager"}; !reflect.DeepEqual(toResume, want) {
		t.Errorf("resume: got %v, want %v", toResume, want)
	}
}

func TestSkipBackup(t *testing.T) {
	if !skipBackup(true, true) {
		t.Error("resume after a move killed midway: backup not skipped")
	}
	if skipBackup(true, false) || skipBackup(false, false) {
		t.Error("source not paused: backup skipped")
	}
}

func TestReplaceDir(t *testing.T) {
	d := t.TempDir()
	dst, src := filepath.Join(d, "backup"), filepath.Join(d, "backup.new")
	for _, f := range []string{filepath.Join(dst, "old"), filepath.Join(src, "new")} {
		if err := os.MkdirAll(filepath.Dir(f), 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(f, nil, 0644); err != nil {
			t.Fatal(err)
		}
	}
	if err := replaceDir(filepath.Join(d, "missing"), dst); err == nil {
		t.Error("missing new backup: no error")
	}
	if _, err := os.Stat(filepath.Join(dst, "old")); err != nil {
		t.Errorf("missing new backup: previous backup lost: %v", err)
	}
	if err := replaceDir(src, dst); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(dst, "new")); err != nil {
		t.Errorf("new backup not in place: %v", err)
	}
	if _, err := os.Stat(filepath.Join(dst, "old")); !os.IsNotExist(err) {
		t.Errorf("previous backup files kept: %v", err)
	}
}

func TestResumePivoted(t *testing.T) {
	cases := []struct {
		name                                     string
		statePivoted, srcExists, kubeconfigSaved bool
		pivoted                                  bool
	}{
		{name: "state says pivoted", statePivoted: true, kubeconfigSaved: true, pivoted: true},
		{name: "not moved", srcExists: true, kubeconfigSaved: true},
		{name: "move finished after the CLI was killed", kubeconfigSaved: true, pivoted: true},
		{name: "killed before the chart created the Cluster"},
		{name: "workload not created yet", srcExists: true},
	}
	for _, c := range cases {
		if got := resumePivoted(c.statePivoted, c.srcExists, c.kubeconfigSaved); got != c.pivoted {
			t.Errorf("%s: pivoted=%t, want %t", c.name, got, c.pivoted)
		}
	}
}

func TestMoveDecision(t *testing.T) {
	cases := []struct {
		name                           string
		srcExists, srcPaused, dstExist bool
		moved, unpause, fails          bool
	}{
		{name: "not moved yet", srcExists: true},
		{name: "moved", dstExist: true, moved: true},
		{name: "killed in the create phase", srcExists: true, srcPaused: true, unpause: true},
		{name: "killed in the delete phase", srcExists: true, srcPaused: true, dstExist: true, unpause: true},
		// prechecks b2: moving again onto existing objects wipes what CAPA wrote there
		{name: "both copies, source not paused", srcExists: true, dstExist: true, fails: true},
		{name: "Cluster nowhere", fails: true},
	}
	for _, c := range cases {
		moved, unpause, err := moveDecision(c.srcExists, c.srcPaused, c.dstExist)
		if moved != c.moved || unpause != c.unpause || (err != nil) != c.fails {
			t.Errorf("%s: moved=%t unpause=%t err=%v", c.name, moved, unpause, err)
		}
	}
}

func TestResumeMoveState(t *testing.T) {
	cases := []struct {
		name                                  string
		srcExists, srcPaused, kubeconfigSaved bool
		pivoted, installOperator              bool
	}{
		{"before the workload cluster exists", false, false, false, false, true},
		{"Cluster in the bootstrap, not moved", true, false, true, false, true},
		// eks-4965-r10b 2026-10-19: half move, the operator was already uninstalled
		{"half move, source paused", true, true, true, false, false},
		{"move finished after the CLI was killed", false, false, true, true, false},
	}
	for _, c := range cases {
		pivoted, install := resumeMoveState(c.srcExists, c.srcPaused, c.kubeconfigSaved)
		if pivoted != c.pivoted || install != c.installOperator {
			t.Errorf("%s: pivoted=%t installOperator=%t", c.name, pivoted, install)
		}
	}
}

func TestCopyIfAbsentIn(t *testing.T) {
	// gke-5084b 2026-10-19: hand-over interrupted after the KeosCluster copy, before its delete in the bootstrap
	got := copyIfAbsentIn("/kind/worker-cluster.kubeconfig", "cluster-c1", "keoscluster", "c1", "COPY")
	want := "kubectl --kubeconfig /kind/worker-cluster.kubeconfig -n cluster-c1 get keoscluster c1 >/dev/null 2>&1 || { COPY; }"
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestApplyIfAbsent(t *testing.T) {
	want := "kubectl get -f /kind/manifests/clusterconfig.yaml >/dev/null 2>&1 || kubectl apply -f /kind/manifests/clusterconfig.yaml"
	if got := applyIfAbsent("/kind/manifests/clusterconfig.yaml"); got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}
