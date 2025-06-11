package commons

import (
	"encoding/base64"
	"fmt"
	"path/filepath"
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

func GetKubeconfigFromSecret(n nodes.Node, namespace, clusterName string) (string, error) {
	secretName := clusterName + "-kubeconfig"

	// Command to read the kubeconfig secret
	cmd := "kubectl -n " + namespace + " get secret " + secretName + " -o jsonpath='{.data.value}'"
	output, err := ExecuteCommand(n, cmd, 5, 3)
	if err != nil {
		return "", errors.Wrap(err, "failed to read kubeconfig secret")
	}

	output = strings.Trim(output, "'") // remove quotes

	decoded, err := base64.StdEncoding.DecodeString(output)
	if err != nil {
		return "", errors.Wrap(err, "failed to decode kubeconfig secret")
	}

	return string(decoded), nil
}
