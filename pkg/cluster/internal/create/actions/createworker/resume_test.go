package createworker

import "testing"

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
