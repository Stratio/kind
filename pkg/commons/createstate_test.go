package commons

import (
	"strings"
	"testing"
)

func TestCreateStateRoundTrip(t *testing.T) {
	t.Chdir(t.TempDir())
	if s, err := LoadCreateState(); s != nil || err != nil {
		t.Fatalf("no file: got %v, %v", s, err)
	}
	if err := UpdateCreateState(func(*CreateState) {}); err == nil {
		t.Fatal("update without a state file must fail")
	}
	if err := (&CreateState{ClusterName: "c1", Version: "0.10.0-SNAPSHOT.1+abc"}).Save(); err != nil {
		t.Fatal(err)
	}
	if err := UpdateCreateState(func(s *CreateState) {
		s.LastCompletedStep = "move"
		s.Pivoted = true
		s.CloudResources = true
	}); err != nil {
		t.Fatal(err)
	}
	s, err := LoadCreateState()
	if err != nil {
		t.Fatal(err)
	}
	if s.ClusterName != "c1" || !s.Pivoted || !s.CloudResources || s.LastCompletedStep != "move" || s.UpdatedAt.IsZero() {
		t.Fatalf("got %+v", s)
	}
}

func TestCheckResumable(t *testing.T) {
	s := &CreateState{ClusterName: "c1", Version: "v1"}
	if err := s.CheckResumable("c1", "v1"); err != nil {
		t.Fatal(err)
	}
	if err := s.CheckResumable("c2", "v1"); err == nil || !strings.Contains(err.Error(), `"c1"`) {
		t.Fatalf("other cluster: %v", err)
	}
	if err := s.CheckResumable("c1", "v2"); err == nil || !strings.Contains(err.Error(), "same binary") {
		t.Fatalf("other binary: %v", err)
	}
}
