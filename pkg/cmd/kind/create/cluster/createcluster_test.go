package cluster

import (
	"os"
	"strings"
	"testing"

	"sigs.k8s.io/kind/pkg/commons"
)

func TestValidateFlagsResume(t *testing.T) {
	for _, f := range []flagpole{
		{Resume: true, ForceDelete: true},
		{Resume: true, AvoidCreation: true},
		{Resume: true, ValidateOnly: true},
	} {
		if err := validateFlags(&f); err == nil {
			t.Errorf("%+v: expected an error", f)
		}
	}
	for _, f := range []flagpole{{Resume: true}, {Resume: true, MoveManagement: true}, {Resume: true, Retain: true}} {
		if err := validateFlags(&f); err != nil {
			t.Errorf("%+v: %v", f, err)
		}
	}
}

func TestPrepareCreateState(t *testing.T) {
	t.Chdir(t.TempDir())
	if err := prepareCreateState(&flagpole{Name: "c1", Resume: true}, "c1", "v1"); err == nil || !strings.Contains(err.Error(), "nothing to resume") {
		t.Fatalf("resume without state: %v", err)
	}
	if err := prepareCreateState(&flagpole{Name: "c1"}, "c1", "v1"); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(commons.CreateStatePath); err != nil {
		t.Fatal("a fresh create must write the state file")
	}
	if err := prepareCreateState(&flagpole{Name: "c1"}, "c1", "v1"); err == nil || !strings.Contains(err.Error(), "use --resume") {
		t.Fatalf("second plain create: %v", err)
	}
	if err := prepareCreateState(&flagpole{Name: "c1", Resume: true}, "c1", "v1"); err != nil {
		t.Fatalf("resume: %v", err)
	}
	if err := prepareCreateState(&flagpole{Name: "c1", Resume: true}, "c1", "v2"); err == nil {
		t.Fatal("resume with another binary must fail")
	}
	// --name unset: the descriptor name identifies the cluster
	if err := prepareCreateState(&flagpole{Resume: true}, "c1", "v1"); err != nil {
		t.Fatalf("resume by descriptor name: %v", err)
	}
}
