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

package createworker

import (
	_ "embed"

	"sigs.k8s.io/kind/pkg/cluster/nodes"
	"sigs.k8s.io/kind/pkg/errors"
	"sigs.k8s.io/kind/pkg/cluster/internal/create/actions/cluster"
)

const (
	CAPICoreProvider         = "cluster-api:v1.3.2"
	CAPIBootstrapProvider    = "kubeadm:v1.3.2"
	CAPIControlPlaneProvider = "kubeadm:v1.3.2"

	CalicoName      = "calico"
	CalicoNamespace = "calico-system"
	CalicoHelmChart = "/stratio/helm/tigera-operator"
	CalicoTemplate  = "/kind/calico-helm-values.yaml"
)

type PBuilder interface {
	setCapx(managed bool)
	setCapxEnvVars(p ProviderParams)
	installCSI(n nodes.Node, k string) error
	getProvider() Provider
}

type Provider struct {
	capxProvider string
	capxName     string
	capxTemplate string
	capxEnvVars  []string
	stClassName  string
	csiNamespace string
}

type ProviderParams struct {
	region      string
	managed     bool
	credentials map[string]string
	githubToken string
}

type Infra struct {
	builder PBuilder
}

func getBuilder(builderType string) PBuilder {
	if builderType == "aws" {
		return newAWSBuilder()
	}

	if builderType == "gcp" {
		return newGCPBuilder()
	}
	return nil
}

func newInfra(b PBuilder) *Infra {
	return &Infra{
		builder: b,
	}
}

func (i *Infra) buildProvider(p ProviderParams) Provider {
	i.builder.setCapx(p.managed)
	i.builder.setCapxEnvVars(p)
	return i.builder.getProvider()
}

func (i *Infra) installCSI(n nodes.Node, k string) error {
	return i.builder.installCSI(n, k)
}

func installCalico(n nodes.Node, k string, descriptorFile cluster.DescriptorFile) error {
	var c string
	var err error

	// Generate the calico helm values
	calicoHelmValues, err := getCalicoManifest(descriptorFile)
	if err != nil {
		return errors.Wrap(err, "failed to generate calico helm values")
	}

	c = "kubectl --kubeconfig " + k + " create namespace " + CalicoNamespace
	err = executeCommand(n, c)
	if err != nil {
		return errors.Wrap(err, "failed to create Calico namespace")
	}

	c = "echo '" + calicoHelmValues + "' > " + CalicoTemplate
	err = executeCommand(n, c)
	if err != nil {
		return errors.Wrap(err, "failed to create Calico Helm chart values file")
	}

	c = "helm install --kubeconfig " + k + " " + CalicoName + " " + CalicoHelmChart +
		" --namespace " + CalicoNamespace + " --values " + CalicoTemplate
	err = executeCommand(n, c)
	if err != nil {
		return errors.Wrap(err, "failed to deploy Calico Helm Chart")
	}

	return nil
}

// installCAPXWorker installs CAPX in the worker cluster
func (p *Provider) installCAPXWorker(node nodes.Node, kubeconfigPath string, allowAllEgressNetPolPath string) error {
	var command string
	var err error

	// Install CAPX in worker cluster
	command = "clusterctl --kubeconfig " + kubeconfigPath + " init --wait-providers" +
		" --core " + CAPICoreProvider +
		" --bootstrap " + CAPIBootstrapProvider +
		" --control-plane " + CAPIControlPlaneProvider +
		" --infrastructure " + p.capxProvider
	err = executeCommand(node, command, p.capxEnvVars)
	if err != nil {
		return errors.Wrap(err, "failed to install CAPX in workload cluster")
	}

	// Scale CAPX to 2 replicas
	command = "kubectl --kubeconfig " + kubeconfigPath + " -n " + p.capxName + "-system scale --replicas 2 deploy " + p.capxName + "-controller-manager"
	err = executeCommand(node, command)
	if err != nil {
		return errors.Wrap(err, "failed to scale CAPX in workload cluster")
	}

	// Allow egress in CAPX's Namespace
	command = "kubectl --kubeconfig " + kubeconfigPath + " -n " + p.capxName + "-system apply -f " + allowAllEgressNetPolPath
	err = executeCommand(node, command)
	if err != nil {
		return errors.Wrap(err, "failed to apply CAPX's NetworkPolicy in workload cluster")
	}

	return nil
}

// installCAPXLocal installs CAPX in the local cluster
func (p *Provider) installCAPXLocal(node nodes.Node) error {
	var command string
	var err error

	command = "clusterctl init --wait-providers" +
		" --core " + CAPICoreProvider +
		" --bootstrap " + CAPIBootstrapProvider +
		" --control-plane " + CAPIControlPlaneProvider +
		" --infrastructure " + p.capxProvider
	err = executeCommand(node, command, p.capxEnvVars)
	if err != nil {
		return errors.Wrap(err, "failed to install CAPX in local cluster")
	}

	return nil
}
