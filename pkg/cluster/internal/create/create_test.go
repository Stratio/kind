package create

import (
	"testing"
	"time"
)

func TestWaitUntil(t *testing.T) {
	calls := 0
	upOnThird := func() bool { calls++; return calls == 3 }
	if err := waitUntil(upOnThird, time.Second, time.Millisecond); err != nil || calls != 3 {
		t.Errorf("up on the third poll: err=%v calls=%d", err, calls)
	}
	if err := waitUntil(func() bool { return false }, 20*time.Millisecond, time.Millisecond); err == nil {
		t.Error("never up: no error")
	}
}
