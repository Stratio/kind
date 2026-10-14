package commons

import "testing"

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

func TestEKSTokenFromExecCredential(t *testing.T) {
	out := `{"kind": "ExecCredential", "apiVersion": "client.authentication.k8s.io/v1beta1", "spec": {}, "status": {"expirationTimestamp": "2026-10-14T11:00:00Z", "token": "k8s-aws-v1.abc"}}`
	if tok, err := eksTokenFromExecCredential(out); err != nil || tok != "k8s-aws-v1.abc" {
		t.Errorf("token=%q err=%v", tok, err)
	}
	if _, err := eksTokenFromExecCredential("An error occurred (UnrecognizedClientException)"); err == nil {
		t.Error("non-JSON output: no error")
	}
}
