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

func StartKubeconfigRefresher(n nodes.Node, namespace, clusterName, kubeconfigPath string, stopChan <-chan struct{}) {
	ticker := time.NewTicker(2 * time.Minute)
	defer ticker.Stop()

	// Perform an initial kubeconfig refresh
	refreshKubeconfig(n, namespace, clusterName, kubeconfigPath)

	for {
		select {
		case <-ticker.C:
			refreshKubeconfig(n, namespace, clusterName, kubeconfigPath)
		case <-stopChan:
			return
		}
	}
}

func refreshKubeconfig(n nodes.Node, namespace, clusterName, kubeconfigPath string) {
	kubeconfig, err := GetKubeconfigFromSecret(n, namespace, clusterName)
	if err != nil {
		return
	}

	// Extract the directory from the path
	kubeconfigDir := filepath.Dir(kubeconfigPath)

	// Create the directory inside the container
	createDirCmd := fmt.Sprintf("mkdir -p %s", kubeconfigDir)
	_, err = ExecuteCommand(n, createDirCmd, 5, 3)
	if err != nil {
		return
	}

	// Write the kubeconfig to the specified path inside the container
	writeFileCmd := fmt.Sprintf("echo '%s' > %s", kubeconfig, kubeconfigPath)
	_, err = ExecuteCommand(n, writeFileCmd, 5, 3)
	if err != nil {
		return
	}
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

// RefreshEKSKubeconfigToken mints a new EKS token with the deploy user credentials inside the bootstrap container and
// writes it into kubeconfigPath: after the pivot nothing refreshes the CAPA token saved there (~15 min lifetime).
func RefreshEKSKubeconfigToken(n nodes.Node, kubeconfigPath, clusterName, region string, credentials map[string]string) error {
	env := []string{"AWS_ACCESS_KEY_ID=" + credentials["AccessKey"], "AWS_SECRET_ACCESS_KEY=" + credentials["SecretKey"], "AWS_REGION=" + region}
	out, err := ExecuteCommand(n, "aws eks get-token --cluster-name "+clusterName+" --region "+region+" --output json 2>/dev/null", 5, 3, env)
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
	if _, err := ExecuteCommand(n, fmt.Sprintf("echo '%s' > %s", kubeconfig, kubeconfigPath), 5, 3); err != nil {
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
