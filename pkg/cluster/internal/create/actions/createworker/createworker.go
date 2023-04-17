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
	_ "embed"
	"os"
	"strings"

	"sigs.k8s.io/kind/pkg/cluster/internal/create/actions"
	"sigs.k8s.io/kind/pkg/cluster/internal/create/actions/cluster"
	"sigs.k8s.io/kind/pkg/errors"
)

type action struct {
	vaultPassword  string
	descriptorPath string
	moveManagement bool
	avoidCreation  bool
}

type AWS struct {
	Credentials cluster.AWSCredentials `yaml:"credentials"`
}

type GCP struct {
	Credentials cluster.GCPCredentials `yaml:"credentials"`
}

// SecretsFile represents the YAML structure in the secrets.yml file
type SecretsFile struct {
	Secrets Secrets `yaml:"secrets"`
}

type Secrets struct {
	AWS              AWS                                 `yaml:"aws"`
	GCP              GCP                                 `yaml:"gcp"`
	GithubToken      string                              `yaml:"github_token"`
	ExternalRegistry cluster.DockerRegistryCredentials   `yaml:"external_registry"`
	DockerRegistries []cluster.DockerRegistryCredentials `yaml:"docker_registries"`
}

//go:embed files/allow-all-egress_netpol.yaml
var allowCommonEgressNetPol string

// In common with keos installer
//
//go:embed files/deny-all-egress-imds_gnetpol.yaml
var denyallEgressIMDSGNetPol string

//go:embed files/allow-capa-egress-imds_gnetpol.yaml
var allowCAPAEgressIMDSGNetPol string

const kubeconfigPath = "/kind/worker-cluster.kubeconfig"
const workKubeconfigPath = ".kube/config"
const secretsFile = "secrets.yml"
const CAPILocalRepository = "/root/.cluster-api/local-repository"

// NewAction returns a new action for installing default CAPI
func NewAction(vaultPassword string, descriptorPath string, moveManagement bool, avoidCreation bool) actions.Action {
	return &action{
		vaultPassword:  vaultPassword,
		descriptorPath: descriptorPath,
		moveManagement: moveManagement,
		avoidCreation:  avoidCreation,
	}
}

// Execute runs the action
func (a *action) Execute(ctx *actions.ActionContext) error {

	// Get the target node
	node, err := getNode(ctx)
	if err != nil {
		return err
	}

	// Parse the cluster descriptor
	descriptorFile, err := cluster.GetClusterDescriptor(a.descriptorPath)
	if err != nil {
		return errors.Wrap(err, "failed to parse cluster descriptor")
	}

	// Get the secrets
	credentialsMap, _, githubToken, dockerRegistries, err := getSecrets(*descriptorFile, a.vaultPassword)
	if err != nil {
		return err
	}

	providerParams := ProviderParams{
		region:      descriptorFile.Region,
		managed:     descriptorFile.ControlPlane.Managed,
		credentials: credentialsMap,
		githubToken: githubToken,
	}

	providerBuilder := getBuilder(descriptorFile.InfraProvider)
	infra := newInfra(providerBuilder)
	provider := infra.buildProvider(providerParams)

	ctx.Status.Start("Installing CAPx 🎖️")
	defer ctx.Status.End(false)

	if provider.capxVersion != provider.capxImageVersion {
		var command string
		var registryUrl string
		var registryUser string
		var registryPass string

		if descriptorFile.DockerRegistries[0].Type == "ecr" {
			ecrToken, err := getEcrAuthToken(providerParams)
			if err != nil {
				return errors.Wrap(err, "failed to get ECR auth token")
			}
			registryUrl = descriptorFile.DockerRegistries[0].URL
			registryUser = "AWS"
			registryPass = ecrToken
		}

		// Change image in infrastructure-components.yaml
		infraComponents := CAPILocalRepository + "/infrastructure-" + provider.capxProvider + "/" + provider.capxVersion + "/infrastructure-components.yaml"
		infraImage := registryUrl + "/stratio/cluster-api-provider-" + provider.capxProvider + ":" + provider.capxImageVersion
		command = "sed -i 's%image:.*%image: " + infraImage + "%' " + infraComponents
		err = executeCommand(node, command)
		if err != nil {
			return errors.Wrap(err, "failed to change image in infrastructure-components.yaml")
		}

		// Create provider-system namespace
		command = "kubectl create namespace " + provider.capxName + "-system"
		err = executeCommand(node, command)
		if err != nil {
			return errors.Wrap(err, "failed to create "+provider.capxName+"-system namespace")
		}

		// Create docker-registry secret
		command = "kubectl create secret docker-registry regcred" +
			" --docker-server=" + registryUrl +
			" --docker-username=" + registryUser +
			" --docker-password=" + registryPass +
			" --namespace=" + provider.capxName + "-system"
		err = executeCommand(node, command)
		if err != nil {
			return errors.Wrap(err, "failed to create docker-registry secret")
		}

		// Add imagePullSecrets to infrastructure-components.yaml
		command = "sed -i '/securityContext:/i\\      imagePullSecrets:\\n      - name: regcred' " + infraComponents
		err = executeCommand(node, command)
		if err != nil {
			return errors.Wrap(err, "failed to add imagePullSecrets to infrastructure-components.yaml")
		}
	}

	err = provider.installCAPXLocal(node)
	if err != nil {
		return err
	}

	ctx.Status.End(true) // End Installing CAPx

	ctx.Status.Start("Generating workload cluster manifests 📝")
	defer ctx.Status.End(false)

	capiClustersNamespace := "cluster-" + descriptorFile.ClusterID

	templateParams := cluster.TemplateParams{
		Descriptor:       *descriptorFile,
		Credentials:      credentialsMap,
		DockerRegistries: dockerRegistries,
	}

	azs, err := infra.getAzs()
	if err != nil {
		return errors.Wrap(err, "failed to get AZs")
	}
	// Generate the cluster manifest
	descriptorData, err := cluster.GetClusterManifest(provider.capxTemplate, templateParams, azs)
	if err != nil {
		return errors.Wrap(err, "failed to generate cluster manifests")
	}

	// Create the cluster manifests file in the container
	descriptorPath := "/kind/manifests/cluster_" + descriptorFile.ClusterID + ".yaml"
	raw := bytes.Buffer{}
	cmd := node.Command("sh", "-c", "echo \""+descriptorData+"\" > "+descriptorPath)
	if err := cmd.SetStdout(&raw).Run(); err != nil {
		return errors.Wrap(err, "failed to write the cluster manifests")
	}

	ctx.Status.End(true) // End Generating worker cluster manifests

	ctx.Status.Start("Generating secrets file 📝🗝️")
	defer ctx.Status.End(false)

	ensureSecretsFile(*descriptorFile, a.vaultPassword)

	rewriteDescriptorFile(a.descriptorPath)

	defer ctx.Status.End(true) // End Generating secrets file

	// Create namespace for CAPI clusters (it must exists)
	raw = bytes.Buffer{}
	cmd = node.Command("kubectl", "create", "ns", capiClustersNamespace)
	if err := cmd.SetStdout(&raw).Run(); err != nil {
		return errors.Wrap(err, "failed to create cluster's Namespace")
	}

	// Create the allow-all-egress network policy file in the container
	allowCommonEgressNetPolPath := "/kind/allow-all-egress_netpol.yaml"
	raw = bytes.Buffer{}
	cmd = node.Command("sh", "-c", "echo \""+allowCommonEgressNetPol+"\" > "+allowCommonEgressNetPolPath)
	if err := cmd.SetStdout(&raw).Run(); err != nil {
		return errors.Wrap(err, "failed to write the allow-all-egress network policy")
	}

	if !a.avoidCreation {

		if descriptorFile.InfraProvider == "aws" {
			ctx.Status.Start("[CAPA] Ensuring IAM security 👮")
			defer ctx.Status.End(false)

			createCloudFormationStack(node, provider.capxEnvVars)
			ctx.Status.End(true) // End Ensuring CAPx requirements
		}

		ctx.Status.Start("Creating the workload cluster 💥")
		defer ctx.Status.End(false)

		// Apply cluster manifests
		raw = bytes.Buffer{}
		cmd = node.Command("kubectl", "create", "-n", capiClustersNamespace, "-f", descriptorPath)
		if err := cmd.SetStdout(&raw).Run(); err != nil {
			return errors.Wrap(err, "failed to apply manifests")
		}

		// Wait for the worker cluster creation
		raw = bytes.Buffer{}
		cmd = node.Command("kubectl", "-n", capiClustersNamespace, "wait", "--for=condition=ready", "--timeout", "25m", "cluster", descriptorFile.ClusterID)
		if err := cmd.SetStdout(&raw).Run(); err != nil {
			return errors.Wrap(err, "failed to create the worker Cluster")
		}

		// Get the workload cluster kubeconfig
		raw = bytes.Buffer{}
		cmd = node.Command("sh", "-c", "clusterctl -n "+capiClustersNamespace+" get kubeconfig "+descriptorFile.ClusterID+" | tee "+kubeconfigPath)
		if err := cmd.SetStdout(&raw).Run(); err != nil {
			return errors.Wrap(err, "failed to get workload cluster kubeconfig")
		}
		kubeconfig := raw.String()

		ctx.Status.End(true) // End Creating the workload cluster

		ctx.Status.Start("Saving the workload cluster kubeconfig 📝")
		defer ctx.Status.End(false)

		workKubeconfigBasePath := strings.Split(workKubeconfigPath, "/")[0]
		_, err = os.Stat(workKubeconfigBasePath)
		if err != nil {
			err := os.Mkdir(workKubeconfigBasePath, os.ModePerm)
			if err != nil {
				return err
			}
		}
		err = os.WriteFile(workKubeconfigPath, []byte(kubeconfig), 0600)
		if err != nil {
			return errors.Wrap(err, "failed to save the workload cluster kubeconfig")
		}

		ctx.Status.End(true) // End Saving the workload cluster kubeconfig

		// Install unmanaged cluster addons
		if !descriptorFile.ControlPlane.Managed {
			ctx.Status.Start("Installing Calico in workload cluster 🔌")
			defer ctx.Status.End(false)

			err = installCalico(node, kubeconfigPath, *descriptorFile)
			if err != nil {
				return errors.Wrap(err, "failed to install Calico in workload cluster")
			}
			ctx.Status.End(true) // End Installing Calico in workload cluster

			ctx.Status.Start("Installing StorageClass in workload cluster 💾")
			defer ctx.Status.End(false)

			err = infra.installCSI(node, kubeconfigPath)
			if err != nil {
				return errors.Wrap(err, "failed to install StorageClass in workload cluster")
			}
			ctx.Status.End(true) // End Installing StorageClass in workload cluster
		}

		ctx.Status.Start("Preparing nodes in workload cluster 📦")
		defer ctx.Status.End(false)

		// Wait for the worker cluster creation
		raw = bytes.Buffer{}
		cmd = node.Command("kubectl", "-n", capiClustersNamespace, "wait", "--for=condition=ready", "--timeout", "15m", "--all", "md")
		if err := cmd.SetStdout(&raw).Run(); err != nil {
			return errors.Wrap(err, "failed to create the worker Cluster")
		}

		if !descriptorFile.ControlPlane.Managed {
			// Wait for the control plane creation
			raw = bytes.Buffer{}
			cmd = node.Command("sh", "-c", "kubectl -n "+capiClustersNamespace+" wait --for=jsonpath=\"{.status.unavailableReplicas}\"=0 --timeout 10m --all kubeadmcontrolplanes")
			if err := cmd.SetStdout(&raw).Run(); err != nil {
				return errors.Wrap(err, "failed to create the worker Cluster")
			}
		}
		ctx.Status.End(true) // End Preparing nodes in workload cluster

		ctx.Status.Start("Enabling workload cluster's self-healing 🏥")
		defer ctx.Status.End(false)

		err = enableSelfHealing(node, *descriptorFile, capiClustersNamespace)
		if err := cmd.SetStdout(&raw).Run(); err != nil {
			return errors.Wrap(err, "failed to enable workload cluster's self-healing")
		}

		ctx.Status.End(true) // End Enabling workload cluster's self-healing

		ctx.Status.Start("Installing CAPx in workload cluster 🎖️")
		defer ctx.Status.End(false)

		err = provider.installCAPXWorker(node, kubeconfigPath, allowCommonEgressNetPolPath)
		if err != nil {
			return err
		}

		// Scale CAPI to 2 replicas
		raw = bytes.Buffer{}
		cmd = node.Command("kubectl", "--kubeconfig", kubeconfigPath, "-n", "capi-system", "scale", "--replicas", "2", "deploy", "capi-controller-manager")
		if err := cmd.SetStdout(&raw).Run(); err != nil {
			return errors.Wrap(err, "failed to scale the CAPI Deployment")
		}

		// Allow egress in CAPI's Namespaces
		raw = bytes.Buffer{}
		cmd = node.Command("kubectl", "--kubeconfig", kubeconfigPath, "-n", "capi-system", "apply", "-f", allowCommonEgressNetPolPath)
		if err := cmd.SetStdout(&raw).Run(); err != nil {
			return errors.Wrap(err, "failed to apply CAPI's egress NetworkPolicy")
		}
		raw = bytes.Buffer{}
		cmd = node.Command("kubectl", "--kubeconfig", kubeconfigPath, "-n", "capi-kubeadm-bootstrap-system", "apply", "-f", allowCommonEgressNetPolPath)
		if err := cmd.SetStdout(&raw).Run(); err != nil {
			return errors.Wrap(err, "failed to apply CAPI's egress NetworkPolicy")
		}
		raw = bytes.Buffer{}
		cmd = node.Command("kubectl", "--kubeconfig", kubeconfigPath, "-n", "capi-kubeadm-control-plane-system", "apply", "-f", allowCommonEgressNetPolPath)
		if err := cmd.SetStdout(&raw).Run(); err != nil {
			return errors.Wrap(err, "failed to apply CAPI's egress NetworkPolicy")
		}

		// Allow egress in cert-manager Namespace
		raw = bytes.Buffer{}
		cmd = node.Command("kubectl", "--kubeconfig", kubeconfigPath, "-n", "cert-manager", "apply", "-f", allowCommonEgressNetPolPath)
		if err := cmd.SetStdout(&raw).Run(); err != nil {
			return errors.Wrap(err, "failed to apply cert-manager's NetworkPolicy")
		}

		ctx.Status.End(true) // End Installing CAPx in workload cluster

		// Use Calico as network policy engine in managed systems
		if descriptorFile.ControlPlane.Managed {
			ctx.Status.Start("Installing Network Policy Engine in workload cluster 🚧")
			defer ctx.Status.End(false)

			err = installCalico(node, kubeconfigPath, *descriptorFile)
			if err != nil {
				return errors.Wrap(err, "failed to install Network Policy Engine in workload cluster")
			}

			// Create the allow and deny (global) network policy file in the container
			if descriptorFile.InfraProvider == "aws" {
				denyallEgressIMDSGNetPolPath := "/kind/deny-all-egress-imds_gnetpol.yaml"
				allowCAPAEgressIMDSGNetPolPath := "/kind/allow-capa-egress-imds_gnetpol.yaml"

				// Allow egress in kube-system Namespace
				raw = bytes.Buffer{}
				cmd = node.Command("kubectl", "--kubeconfig", kubeconfigPath, "-n", "kube-system", "apply", "-f", allowCommonEgressNetPolPath)
				if err := cmd.SetStdout(&raw).Run(); err != nil {
					return errors.Wrap(err, "failed to apply kube-system egress NetworkPolicy")
				}

				raw = bytes.Buffer{}
				cmd = node.Command("sh", "-c", "echo \""+denyallEgressIMDSGNetPol+"\" > "+denyallEgressIMDSGNetPolPath)
				if err := cmd.SetStdout(&raw).Run(); err != nil {
					return errors.Wrap(err, "failed to write the deny-all-traffic-to-aws-imds global network policy")
				}
				raw = bytes.Buffer{}
				cmd = node.Command("sh", "-c", "echo \""+allowCAPAEgressIMDSGNetPol+"\" > "+allowCAPAEgressIMDSGNetPolPath)
				if err := cmd.SetStdout(&raw).Run(); err != nil {
					return errors.Wrap(err, "failed to write the allow-traffic-to-aws-imds-capa global network policy")
				}

				// Deny CAPA egress to AWS IMDS
				raw = bytes.Buffer{}
				cmd = node.Command("kubectl", "--kubeconfig", kubeconfigPath, "apply", "-f", denyallEgressIMDSGNetPolPath)
				if err := cmd.SetStdout(&raw).Run(); err != nil {
					return errors.Wrap(err, "failed to apply deny IMDS traffic GlobalNetworkPolicy")
				}

				// Allow CAPA egress to AWS IMDS
				raw = bytes.Buffer{}
				cmd = node.Command("kubectl", "--kubeconfig", kubeconfigPath, "apply", "-f", allowCAPAEgressIMDSGNetPolPath)
				if err := cmd.SetStdout(&raw).Run(); err != nil {
					return errors.Wrap(err, "failed to apply allow CAPA as egress GlobalNetworkPolicy")
				}
			}
		}

		ctx.Status.End(true) // End Installing Network Policy Engine in workload cluster

		if descriptorFile.DeployAutoscaler {
			ctx.Status.Start("Adding Cluster-Autoescaler 🗚")
			defer ctx.Status.End(false)

			raw = bytes.Buffer{}
			cmd = integrateClusterAutoscaler(node, kubeconfigPath, descriptorFile.ClusterID, "clusterapi")
			if err := cmd.SetStdout(&raw).Run(); err != nil {
				return errors.Wrap(err, "failed to install chart cluster-autoscaler")
			}

			ctx.Status.End(true)
		}

		if !a.moveManagement {
			ctx.Status.Start("Moving the management role 🗝️")
			defer ctx.Status.End(false)

			// Create namespace for CAPI clusters (it must exists) in worker cluster
			raw = bytes.Buffer{}
			cmd = node.Command("kubectl", "--kubeconfig", kubeconfigPath, "create", "ns", capiClustersNamespace)
			if err := cmd.SetStdout(&raw).Run(); err != nil {
				return errors.Wrap(err, "failed to create manifests Namespace")
			}

			// Pivot management role to worker cluster
			raw = bytes.Buffer{}
			cmd = node.Command("sh", "-c", "clusterctl move -n "+capiClustersNamespace+" --to-kubeconfig "+kubeconfigPath)

			if err := cmd.SetStdout(&raw).Run(); err != nil {
				return errors.Wrap(err, "failed to pivot management role to worker cluster")
			}

			ctx.Status.End(true)
		}

	}

	ctx.Status.Start("Generating the KEOS descriptor 📝")
	defer ctx.Status.End(false)

	err = createKEOSDescriptor(*descriptorFile, provider.stClassName)
	if err != nil {
		return err
	}
	ctx.Status.End(true) // End Generating KEOS descriptor

	return nil
}
