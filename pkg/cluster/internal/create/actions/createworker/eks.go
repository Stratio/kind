/*
Copyright 2019 The Kubernetes Authors.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

// Package createworker implements the create worker action
package createworker

import (
	"bytes"
	"os"
	"strconv"

	"gopkg.in/yaml.v3"

	"sigs.k8s.io/kind/pkg/cluster/internal/create/actions"
	"sigs.k8s.io/kind/pkg/cluster/nodeutils"
	"sigs.k8s.io/kind/pkg/errors"
)

type action struct{}

// DescriptorFile represents the YAML structure in the cluster.yaml file
type DescriptorFile struct {
	ClusterID string `yaml:"cluster_id"`
	Nodes     struct {
		KubeNode struct {
			AmiID string `yaml:"ami_id"`
			Disks []struct {
				DeviceName string `yaml:"device_name"`
				Name       string `yaml:"name"`
				Path       string `yaml:"path,omitempty"`
				Size       int    `yaml:"size"`
				Type       string `yaml:"type"`
				Volumes    []struct {
					Name string `yaml:"name"`
					Path string `yaml:"path"`
					Size string `yaml:"size"`
				} `yaml:"volumes,omitempty"`
			} `yaml:"disks"`
			NodeType string `yaml:"node_type"`
			Quantity int    `yaml:"quantity"`
			VMSize   string `yaml:"vm_size"`
			Subnet   string `yaml:"subnet"`
			SSHKey   string `yaml:"ssh_key"`
		} `yaml:"kube_node"`
	} `yaml:"nodes"`
}

// SecretsFile represents the YAML structure in the secrets.yaml file
type SecretsFile struct {
	Secrets struct {
		AWS struct {
			Credentials struct {
				ClientID     string `yaml:"client_id"`
				ClientSecret string `yaml:"client_secret"`
				Region       string `yaml:"region"`
				Account      string `yaml:"account"`
			} `yaml:"credentials"`
			B64Credentials string `yaml:"b64_credentials"`
		} `yaml:"aws"`
		GithubToken string `yaml:"github_token"`
	} `yaml:"secrets"`
}

// NewAction returns a new action for installing default CAPI
func NewAction() actions.Action {
	return &action{}
}

// Execute runs the action
func (a *action) Execute(ctx *actions.ActionContext) error {
	ctx.Status.Start("Generating worker cluster manifests 📝")
	defer ctx.Status.End(false)

	allNodes, err := ctx.Nodes()
	if err != nil {
		return err
	}

	// get the target node for this task
	controlPlanes, err := nodeutils.ControlPlaneNodes(allNodes)
	if err != nil {
		return err
	}
	node := controlPlanes[0] // kind expects at least one always

	// Read secrets.yaml file

	secretRAW, err := os.ReadFile("./secrets.yaml.clear")
	if err != nil {
		return err
	}

	var secretsFile SecretsFile
	err = yaml.Unmarshal(secretRAW, &secretsFile)
	if err != nil {
		return err
	}

	// Read cluster.yaml file

	descriptorRAW, err := os.ReadFile("./cluster.yaml")
	if err != nil {
		return err
	}

	var descriptorFile DescriptorFile
	err = yaml.Unmarshal(descriptorRAW, &descriptorFile)
	if err != nil {
		return err
	}

	// Generate the manifest for EKS (eks-cluster.yaml)
	raw := bytes.Buffer{}
	cmd := node.Command("clusterctl", "generate", "cluster", descriptorFile.ClusterID,
		"--kubernetes-version", "v1.23.0", "--worker-machine-count="+strconv.Itoa(descriptorFile.Nodes.KubeNode.Quantity),
		"--flavor", "eks", "--infrastructure", "aws")
	cmd.SetEnv("AWS_REGION="+secretsFile.Secrets.AWS.Credentials.Region,
		"AWS_ACCESS_KEY_ID="+secretsFile.Secrets.AWS.Credentials.ClientID,
		"AWS_SECRET_ACCESS_KEY="+secretsFile.Secrets.AWS.Credentials.ClientSecret,
		"AWS_SSH_KEY_NAME="+descriptorFile.Nodes.KubeNode.SSHKey,
		"AWS_NODE_MACHINE_TYPE="+descriptorFile.Nodes.KubeNode.VMSize,
		"GITHUB_TOKEN="+secretsFile.Secrets.GithubToken)
	if err := cmd.SetStdout(&raw).Run(); err != nil {
		return errors.Wrap(err, "failed to generate EKS manifests")
	}
	eksDescriptorData := raw.String()
	// fmt.Println("RAW STRING: " + raw.String())
	// b64Credentials := strings.TrimSuffix(raw.String(), "\n")

	// Create the eks-cluster.yaml file in the container
	descriptorPath := "/kind/eks-cluster.yaml"
	raw = bytes.Buffer{}
	cmd = node.Command("sh", "-c", "echo \""+eksDescriptorData+"\" > "+descriptorPath)
	if err = cmd.SetStdout(&raw).Run(); err != nil {
		return errors.Wrap(err, "failed to write the eks-cluster.yaml")
	}

	ctx.Status.End(true) // End Generating worker cluster manifests

	ctx.Status.Start("Creating the worker cluster 💥")
	defer ctx.Status.End(false)

	// Apply manifests
	raw = bytes.Buffer{}
	cmd = node.Command("kubectl", "create", "-f", "/kind/eks-cluster.yaml")
	if err := cmd.SetStdout(&raw).Run(); err != nil {
		return errors.Wrap(err, "failed to apply manifests")
	}
	// fmt.Println("RAW STRING: " + raw.String())

	// Wait for EKS cluster creation
	raw = bytes.Buffer{}
	cmd = node.Command("kubectl", "wait", "--for=condition=ready", "--timeout", "25m", "cluster", descriptorFile.ClusterID)
	if err := cmd.SetStdout(&raw).Run(); err != nil {
		return errors.Wrap(err, "failed to create the EKS cluster")
	}
	// fmt.Println("RAW STRING: " + raw.String())

	// Get EKS kubeconfig file (with 10m token)
	raw = bytes.Buffer{}
	cmd = node.Command("sh", "-c", "clusterctl get kubeconfig "+descriptorFile.ClusterID+" > /kind/eks-cluster.kubeconfig")
	if err := cmd.SetStdout(&raw).Run(); err != nil {
		return errors.Wrap(err, "failed to get the kubeconfig file")
	}

	ctx.Status.End(true) // End Creating the worker cluster

	ctx.Status.Start("Installing CAPx in EKS 🎖️")
	defer ctx.Status.End(false)

	// Install CAPA in EKS
	raw = bytes.Buffer{}
	cmd = node.Command("sh", "-c", "clusterctl --kubeconfig /kind/eks-cluster.kubeconfig init --infrastructure aws --wait-providers")
	cmd.SetEnv("AWS_REGION="+secretsFile.Secrets.AWS.Credentials.Region,
		"AWS_ACCESS_KEY_ID="+secretsFile.Secrets.AWS.Credentials.ClientID,
		"AWS_SECRET_ACCESS_KEY="+secretsFile.Secrets.AWS.Credentials.ClientSecret,
		"AWS_B64ENCODED_CREDENTIALS="+secretsFile.Secrets.AWS.B64Credentials,
		"GITHUB_TOKEN="+secretsFile.Secrets.GithubToken)
	if err := cmd.SetStdout(&raw).Run(); err != nil {
		return errors.Wrap(err, "failed to install CAPA")
	}

	ctx.Status.End(true) // End Installing CAPx in EKS

	ctx.Status.Start("Transfering the management role 🗝️")
	defer ctx.Status.End(false)

	// Pivot management role to EKS
	// raw = bytes.Buffer{}
	// cmd = node.Command("sh", "-c", "clusterctl move --to-kubeconfig /kind/eks-cluster.kubeconfig")
	// cmd.SetEnv("AWS_REGION="+secretsFile.Secrets.AWS.Credentials.Region,
	// 	"AWS_ACCESS_KEY_ID="+secretsFile.Secrets.AWS.Credentials.ClientID,
	// 	"AWS_SECRET_ACCESS_KEY="+secretsFile.Secrets.AWS.Credentials.ClientSecret,
	// 	"AWS_B64ENCODED_CREDENTIALS="+secretsFile.Secrets.AWS.B64Credentials,
	// 	"GITHUB_TOKEN="+secretsFile.Secrets.GithubToken)
	// if err := cmd.SetStdout(&raw).Run(); err != nil {
	// 	return errors.Wrap(err, "failed to pivot management role to EKS")
	// }

	// Get kubeconfig for aws

	ctx.Status.End(true) // End Transfering the management role

	return nil
}
