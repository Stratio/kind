package commons

import (
	"encoding/json"
	"errors"
	"testing"
)

func TestReplaceKubeconfigToken(t *testing.T) {
	// shape of the CAPA EKS kubeconfig (secret <cluster>-kubeconfig)
	kc := `apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: Q0E=
    server: https://ABC.gr7.eu-west-1.eks.amazonaws.com
  name: eks-1
contexts:
- context:
    cluster: eks-1
    user: eks-1-capi-admin
  name: eks-1-capi-admin@eks-1
current-context: eks-1-capi-admin@eks-1
kind: Config
users:
- name: eks-1-capi-admin
  user:
    token: k8s-aws-v1.old
`
	got, err := ReplaceKubeconfigToken(kc, "k8s-aws-v1.new")
	if err != nil {
		t.Fatal(err)
	}
	want := kc[:len(kc)-len("k8s-aws-v1.old\n")] + "k8s-aws-v1.new\n"
	if got != want {
		t.Errorf("got:\n%s", got)
	}
	if _, err := ReplaceKubeconfigToken("users:\n- name: a\n  user:\n    client-certificate-data: X\n", "t"); err == nil {
		t.Error("kubeconfig without a token: no error")
	}
}

func TestRefreshWith(t *testing.T) {
	cases := []struct {
		name                    string
		fetchErr                error
		works, fallbackExpected bool
	}{
		{name: "secret kubeconfig works", works: true},
		{name: "stale token in the secret (paused Cluster)", fallbackExpected: true},
		{name: "secret gone (after the pivot)", fetchErr: errors.New("not found"), fallbackExpected: true},
	}
	for _, c := range cases {
		fellBack := false
		refreshWith(
			func() (string, error) { return "kc", c.fetchErr },
			func(string) bool { return c.works },
			func() error { fellBack = true; return nil },
		)
		if fellBack != c.fallbackExpected {
			t.Errorf("%s: fallback=%t", c.name, fellBack)
		}
	}
	refreshWith(func() (string, error) { return "", errors.New("x") }, func(string) bool { return false }, nil)
}

func TestEKSTokenFromExecCredential(t *testing.T) {
	out := `{"kind": "ExecCredential", "apiVersion": "client.authentication.k8s.io/v1beta1", "spec": {}, "status": {"expirationTimestamp": "2026-10-14T11:00:00Z", "token": "k8s-aws-v1.abc"}}`
	if tok, err := eksTokenFromExecCredential(out); err != nil || tok != "k8s-aws-v1.abc" {
		t.Errorf("token=%q err=%v", tok, err)
	}
	if _, err := eksTokenFromExecCredential("An error occurred (UnrecognizedClientException)"); err == nil {
		t.Error("non-JSON output: no error")
	}
}

func TestGCPServiceAccountJSON(t *testing.T) {
	creds := map[string]string{"ProjectID": "p1", "PrivateKeyID": "k1", "PrivateKey": "not-a-pem", "ClientEmail": "sa@p1.iam.gserviceaccount.com", "ClientID": "42"}
	raw, err := gcpServiceAccountJSON(creds)
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]string
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	if got["type"] != "service_account" || got["client_email"] != creds["ClientEmail"] || got["private_key"] != creds["PrivateKey"] || got["project_id"] != "p1" {
		t.Errorf("service account JSON: %v", got)
	}
	// the key is parsed before any request: a broken key fails locally
	if _, err := gcpAccessToken(creds); err == nil {
		t.Error("invalid private key: no error")
	}
}

func TestEKSGetTokenCommand(t *testing.T) {
	base := "aws eks get-token --cluster-name c1 --region eu-west-1 --output json"
	if got := eksGetTokenCommand("c1", "eu-west-1", ""); got != base {
		t.Errorf("no role: got %q", got)
	}
	// eks-4965-r10a 2026-10-19: only the role_arn identity is mapped in a 0.10 EKS cluster
	arn := "arn:aws:iam::123456789012:role/capa-management-components"
	if got := eksGetTokenCommand("c1", "eu-west-1", arn); got != base+" --role-arn "+arn {
		t.Errorf("role: got %q", got)
	}
}
