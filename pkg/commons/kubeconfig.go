package commons

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"sigs.k8s.io/kind/pkg/cluster/nodes"
	"sigs.k8s.io/kind/pkg/errors"
)

// StartKubeconfigRefresher rewrites kubeconfigPath from the CAPI kubeconfig secret every 2 minutes. fallback (may be
// nil) runs when that kubeconfig does not reach the cluster, e.g. a paused Cluster whose EKS token CAPA stopped renewing.
func StartKubeconfigRefresher(n nodes.Node, namespace, clusterName, kubeconfigPath string, stopChan <-chan struct{}, fallback func() error) {
	ticker := time.NewTicker(2 * time.Minute)
	defer ticker.Stop()

	// Perform an initial kubeconfig refresh
	refreshKubeconfig(n, namespace, clusterName, kubeconfigPath, fallback)

	for {
		select {
		case <-ticker.C:
			refreshKubeconfig(n, namespace, clusterName, kubeconfigPath, fallback)
		case <-stopChan:
			return
		}
	}
}

func refreshKubeconfig(n nodes.Node, namespace, clusterName, kubeconfigPath string, fallback func() error) {
	refreshWith(
		func() (string, error) { return GetKubeconfigFromSecret(n, namespace, clusterName) },
		func(kubeconfig string) bool { return writeWorkingKubeconfig(n, kubeconfig, kubeconfigPath) },
		fallback,
	)
}

// refreshWith writes the fetched kubeconfig only if it works; otherwise the current one stays and fallback runs
func refreshWith(fetch func() (string, error), writeIfWorking func(string) bool, fallback func() error) {
	if kubeconfig, err := fetch(); err == nil && writeIfWorking(kubeconfig) {
		return
	}
	if fallback != nil {
		_ = fallback()
	}
}

// writeWorkingKubeconfig puts kubeconfig at kubeconfigPath only when it reaches the cluster
func writeWorkingKubeconfig(n nodes.Node, kubeconfig, kubeconfigPath string) bool {
	tmp := kubeconfigPath + ".new"
	if _, err := ExecuteCommand(n, fmt.Sprintf("mkdir -p %s && echo '%s' > %s", filepath.Dir(kubeconfigPath), kubeconfig, tmp), 5, 3); err != nil {
		return false
	}
	if _, err := ExecuteCommand(n, "kubectl --kubeconfig "+tmp+" get ns kube-system -o name", 1, 1); err != nil {
		return false
	}
	_, err := ExecuteCommand(n, "mv "+tmp+" "+kubeconfigPath, 5, 3)
	return err == nil
}

var kubeconfigTokenLine = regexp.MustCompile(`(?m)^([ \t]*token:[ \t]*)\S+[ \t]*$`)

// ReplaceKubeconfigToken swaps the bearer token of the kubeconfig's only token user
func ReplaceKubeconfigToken(kubeconfig, token string) (string, error) {
	if n := len(kubeconfigTokenLine.FindAllString(kubeconfig, -1)); n != 1 {
		return "", errors.Errorf("expected one token in the kubeconfig, found %d", n)
	}
	return kubeconfigTokenLine.ReplaceAllStringFunc(kubeconfig, func(line string) string {
		return kubeconfigTokenLine.FindStringSubmatch(line)[1] + token
	}), nil
}

// eksTokenFromExecCredential reads status.token from the ExecCredential printed by `aws eks get-token`
func eksTokenFromExecCredential(out string) (string, error) {
	var cred struct {
		Status struct {
			Token string `json:"token"`
		} `json:"status"`
	}
	if err := json.Unmarshal([]byte(out), &cred); err != nil || cred.Status.Token == "" {
		return "", errors.Errorf("unexpected aws eks get-token output: %v", err)
	}
	return cred.Status.Token, nil
}

// EnsureKubeconfigAccess checks that kubeconfigPath reaches the cluster; if not, it runs refresh (may be nil) and checks again
func EnsureKubeconfigAccess(n nodes.Node, kubeconfigPath string, refresh func() error) error {
	check := "kubectl --kubeconfig " + kubeconfigPath + " get ns kube-system -o name"
	_, err := ExecuteCommand(n, check, 5, 3)
	if err == nil || refresh == nil {
		return err
	}
	if rerr := refresh(); rerr != nil {
		return errors.Wrap(rerr, "the workload kubeconfig does not work and refreshing its token failed")
	}
	_, err = ExecuteCommand(n, check, 5, 3)
	return err
}

// RefreshEKSKubeconfigToken mints a new EKS token with the deploy user credentials inside the bootstrap container and
// writes it into kubeconfigPath: after the pivot nothing refreshes the CAPA token saved there (~15 min lifetime).
func RefreshEKSKubeconfigToken(n nodes.Node, kubeconfigPath, clusterName, region string, credentials map[string]string) error {
	env := []string{"AWS_ACCESS_KEY_ID=" + credentials["AccessKey"], "AWS_SECRET_ACCESS_KEY=" + credentials["SecretKey"], "AWS_REGION=" + region}
	c := "aws eks get-token --cluster-name " + clusterName + " --region " + region + " --output json"
	// With a role ARN, CAPA creates the cluster as that role and only it is mapped in the cluster
	if roleARN := credentials["RoleARN"]; roleARN != "" {
		c += " --role-arn " + roleARN
	}
	out, err := ExecuteCommand(n, c+" 2>/dev/null", 5, 3, env)
	if err != nil {
		return errors.Wrap(err, "failed to get a new EKS token")
	}
	token, err := eksTokenFromExecCredential(out)
	if err != nil {
		return err
	}
	kubeconfig, err := ExecuteCommand(n, "cat "+kubeconfigPath, 5, 3)
	if err != nil {
		return errors.Wrap(err, "failed to read "+kubeconfigPath)
	}
	kubeconfig, err = ReplaceKubeconfigToken(kubeconfig, token)
	if err != nil {
		return err
	}
	if _, err := ExecuteCommand(n, fmt.Sprintf("echo '%s' > %s.tok && mv %s.tok %s", kubeconfig, kubeconfigPath, kubeconfigPath, kubeconfigPath), 5, 3); err != nil {
		return errors.Wrap(err, "failed to write "+kubeconfigPath)
	}
	return nil
}

func GetKubeconfigFromSecret(n nodes.Node, namespace, clusterName string) (string, error) {
	const (
		retries = 6                  // 6 retries
		delay   = 10 * time.Second // 10 seconds delay
	)
	secretName := clusterName + "-kubeconfig"

	// Command to read the kubeconfig secret
	cmd := "kubectl -n " + namespace + " get secret " + secretName + " -o jsonpath='{.data.value}'"

	var output string
	var err error

	for i := 0; i < retries; i++ {
		output, err = ExecuteCommand(n, cmd, 5, 3)
		if err == nil {
			// Success, the secret was found
			break
		}
		time.Sleep(delay)
	}

	if err != nil {
		return "", errors.Wrap(err, "failed to read kubeconfig secret after multiple retries")
	}

	output = strings.Trim(output, "'") // remove quotes

	decoded, err := base64.StdEncoding.DecodeString(output)
	if err != nil {
		return "", errors.Wrap(err, "failed to decode kubeconfig secret")
	}

	return string(decoded), nil
}
