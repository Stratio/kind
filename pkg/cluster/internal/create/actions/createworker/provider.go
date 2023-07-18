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
	"bytes"
	"embed"
	"encoding/base64"
	"path/filepath"

	"reflect"
	"strings"
	"text/template"

	"sigs.k8s.io/kind/pkg/cluster/nodes"
	"sigs.k8s.io/kind/pkg/commons"
	"sigs.k8s.io/kind/pkg/errors"
	"sigs.k8s.io/kind/pkg/exec"
)

//go:embed templates/*/*
var ctel embed.FS

const (
	CAPICoreProvider         = "cluster-api:v1.4.3"
	CAPIBootstrapProvider    = "kubeadm:v1.4.3"
	CAPIControlPlaneProvider = "kubeadm:v1.4.3"

	scName = "keos"
)

const machineHealthCheckWorkerNodePath = "/kind/manifests/machinehealthcheckworkernode.yaml"
const machineHealthCheckControlPlaneNodePath = "/kind/manifests/machinehealthcheckcontrolplane.yaml"
const defaultScAnnotation = "storageclass.kubernetes.io/is-default-class"

//go:embed files/common/calico-metrics.yaml
var calicoMetrics string

type PBuilder interface {
	setCapx(managed bool)
	setCapxEnvVars(p ProviderParams)
	setSC(p ProviderParams)
	installCSI(n nodes.Node, k string) error
	getProvider() Provider
	configureStorageClass(n nodes.Node, k string) error
	getAzs(networks commons.Networks) ([]string, error)
	internalNginx(networks commons.Networks, credentialsMap map[string]string, clusterID string) (bool, error)
	getOverrideVars(keosCluster commons.KeosCluster, credentialsMap map[string]string) (map[string][]byte, error)
}

type Provider struct {
	capxProvider     string
	capxVersion      string
	capxImageVersion string
	capxManaged      bool
	capxName         string
	capxTemplate     string
	capxEnvVars      []string
	scParameters     commons.SCParameters
	scProvisioner    string
	csiNamespace     string
}

type Node struct {
	AZ      string
	QA      int
	MaxSize int
	MinSize int
}

type Infra struct {
	builder PBuilder
}

type ProviderParams struct {
	Region       string
	Managed      bool
	Credentials  map[string]string
	GithubToken  string
	StorageClass commons.StorageClass
}

type DefaultStorageClass struct {
	APIVersion string `yaml:"apiVersion"`
	Kind       string `yaml:"kind"`
	Metadata   struct {
		Annotations map[string]string `yaml:"annotations,omitempty"`
		Name        string            `yaml:"name"`
	} `yaml:"metadata"`
	AllowVolumeExpansion bool                 `yaml:"allowVolumeExpansion"`
	Provisioner          string               `yaml:"provisioner"`
	Parameters           commons.SCParameters `yaml:"parameters"`
	VolumeBindingMode    string               `yaml:"volumeBindingMode"`
}

var scTemplate = DefaultStorageClass{
	APIVersion: "storage.k8s.io/v1",
	Kind:       "StorageClass",
	Metadata: struct {
		Annotations map[string]string `yaml:"annotations,omitempty"`
		Name        string            `yaml:"name"`
	}{
		Annotations: map[string]string{
			defaultScAnnotation: "true",
		},
		Name: scName,
	},
	AllowVolumeExpansion: true,
	VolumeBindingMode:    "WaitForFirstConsumer",
}

func getBuilder(builderType string) PBuilder {
	if builderType == "aws" {
		return newAWSBuilder()
	}

	if builderType == "gcp" {
		return newGCPBuilder()
	}

	if builderType == "azure" {
		return newAzureBuilder()
	}
	return nil
}

func newInfra(b PBuilder) *Infra {
	return &Infra{
		builder: b,
	}
}

func (i *Infra) buildProvider(p ProviderParams) Provider {
	i.builder.setCapx(p.Managed)
	i.builder.setCapxEnvVars(p)
	i.builder.setSC(p)
	return i.builder.getProvider()
}

func (i *Infra) installCSI(n nodes.Node, k string) error {
	return i.builder.installCSI(n, k)
}

func (i *Infra) configureStorageClass(n nodes.Node, k string) error {
	return i.builder.configureStorageClass(n, k)
}

func (i *Infra) internalNginx(networks commons.Networks, credentialsMap map[string]string, ClusterID string) (bool, error) {
	requiredIntenalNginx, err := i.builder.internalNginx(networks, credentialsMap, ClusterID)
	if err != nil {
		return false, err
	}
	return requiredIntenalNginx, nil
}

func (i *Infra) getOverrideVars(keosCluster commons.KeosCluster, credentialsMap map[string]string) (map[string][]byte, error) {
	overrideVars, err := i.builder.getOverrideVars(keosCluster, credentialsMap)
	if err != nil {
		return nil, err
	}
	return overrideVars, nil
}

func (i *Infra) getAzs(networks commons.Networks) ([]string, error) {
	azs, err := i.builder.getAzs(networks)
	if err != nil {
		return nil, err
	}
	return azs, nil
}

func installCalico(n nodes.Node, k string, keosCluster commons.KeosCluster, allowCommonEgressNetPolPath string) error {
	var c string
	var cmd exec.Cmd
	var err error

	calicoTemplate := "/kind/calico-helm-values.yaml"

	// Generate the calico helm values
	calicoHelmValues, err := getManifest("common", "calico-helm-values.tmpl", keosCluster.Spec)
	if err != nil {
		return errors.Wrap(err, "failed to generate calico helm values")
	}

	c = "echo '" + calicoHelmValues + "' > " + calicoTemplate
	_, err = commons.ExecuteCommand(n, c)
	if err != nil {
		return errors.Wrap(err, "failed to create Calico Helm chart values file")
	}

	c = "helm install calico /stratio/helm/tigera-operator" +
		" --kubeconfig " + k +
		" --namespace tigera-operator" +
		" --create-namespace" +
		" --values " + calicoTemplate
	_, err = commons.ExecuteCommand(n, c)
	if err != nil {
		return errors.Wrap(err, "failed to deploy Calico Helm Chart")
	}

	// Allow egress in tigera-operator namespace
	c = "kubectl --kubeconfig " + kubeconfigPath + " -n tigera-operator apply -f " + allowCommonEgressNetPolPath
	_, err = commons.ExecuteCommand(n, c)
	if err != nil {
		return errors.Wrap(err, "failed to apply tigera-operator egress NetworkPolicy")
	}

	// Wait for calico-system namespace to be created
	c = "timeout 60s bash -c 'until kubectl --kubeconfig " + kubeconfigPath + " get ns calico-system; do sleep 2s ; done'"
	_, err = commons.ExecuteCommand(n, c)
	if err != nil {
		return errors.Wrap(err, "failed to wait for calico-system namespace")
	}

	// Allow egress in calico-system namespace
	c = "kubectl --kubeconfig " + kubeconfigPath + " -n calico-system apply -f " + allowCommonEgressNetPolPath
	_, err = commons.ExecuteCommand(n, c)
	if err != nil {
		return errors.Wrap(err, "failed to apply calico-system egress NetworkPolicy")
	}

	// Create calico metrics services
	cmd = n.Command("kubectl", "--kubeconfig", k, "apply", "-f", "-")
	if err = cmd.SetStdin(strings.NewReader(calicoMetrics)).Run(); err != nil {
		return errors.Wrap(err, "failed to create calico metrics services")
	}

	return nil
}

func prepareCustomCoreDNS(n nodes.Node, keosCluster commons.KeosCluster) error {
	var c string
	var err error

	coreDNSSuffix := ""
	coreDNSTemplate := "/kind/coredns-configmap.yaml"

	if keosCluster.Spec.InfraProvider == "aws" && keosCluster.Spec.ControlPlane.Managed {
		coreDNSConfigmap, err := getManifest(keosCluster.Spec.InfraProvider, "coredns_configmap"+coreDNSSuffix+".tmpl", keosCluster.Spec)
		if err != nil {
			return errors.Wrap(err, "failed to get CoreDNS file")
		}

		c = "echo '" + coreDNSConfigmap + "' > " + coreDNSTemplate
		_, err = commons.ExecuteCommand(n, c)
		if err != nil {
			return errors.Wrap(err, "failed to create CoreDNS configmap file")
		}

	}

	// Generate the coredns configmap
	if keosCluster.Spec.InfraProvider == "azure" && keosCluster.Spec.ControlPlane.Managed {
		coreDNSSuffix = "-aks"
	}

	coreDNSConfigmap, err := getManifest(keosCluster.Spec.InfraProvider, "coredns_configmap"+coreDNSSuffix+".tmpl", keosCluster.Spec)
	if err != nil {
		return errors.Wrap(err, "failed to get CoreDNS file")
	}

	c = "echo '" + coreDNSConfigmap + "' > " + coreDNSTemplate
	_, err = commons.ExecuteCommand(n, c)
	if err != nil {
		return errors.Wrap(err, "failed to create CoreDNS configmap file")
	}
	return nil
}

func customCoreDNS(n nodes.Node, k string, keosCluster commons.KeosCluster) error {
	var c string
	var err error

	coreDNSPatchFile := "coredns"
	coreDNSTemplate := "/kind/coredns-configmap.yaml"
	coreDNSSuffix := ""

	if keosCluster.Spec.InfraProvider == "azure" && keosCluster.Spec.ControlPlane.Managed {
		coreDNSPatchFile = "coredns-custom"
		coreDNSSuffix = "-aks"
	}

	coreDNSConfigmap, err := getManifest(keosCluster.Spec.InfraProvider, "coredns_configmap"+coreDNSSuffix+".tmpl", keosCluster.Spec)
	if err != nil {
		return errors.Wrap(err, "failed to get CoreDNS file")
	}

	c = "echo '" + coreDNSConfigmap + "' > " + coreDNSTemplate
	_, err = commons.ExecuteCommand(n, c)
	if err != nil {
		return errors.Wrap(err, "failed to create CoreDNS configmap file")
	}

	// Patch configmap
	c = "kubectl --kubeconfig " + kubeconfigPath + " -n kube-system patch cm " + coreDNSPatchFile + " --patch-file " + coreDNSTemplate
	_, err = commons.ExecuteCommand(n, c)
	if err != nil {
		return errors.Wrap(err, "failed to customize coreDNS patching ConfigMap")
	}

	// Rollout restart to catch the made changes
	c = "kubectl --kubeconfig " + kubeconfigPath + " -n kube-system rollout restart deploy coredns"
	_, err = commons.ExecuteCommand(n, c)
	if err != nil {
		return errors.Wrap(err, "failed to redeploy coreDNS")
	}

	// Wait until CoreDNS completely rollout
	c = "kubectl --kubeconfig " + kubeconfigPath + " -n kube-system rollout status deploy coredns --timeout=3m"
	_, err = commons.ExecuteCommand(n, c)
	if err != nil {
		return errors.Wrap(err, "failed to wait for the customatization of CoreDNS configmap")
	}

	return nil
}

// installCAPXWorker installs CAPX in the worker cluster
func (p *Provider) installCAPXWorker(n nodes.Node, kubeconfigPath string, allowAllEgressNetPolPath string) error {
	var c string
	var err error

	if p.capxProvider == "azure" {
		// Create capx namespace
		c = "kubectl --kubeconfig " + kubeconfigPath + " create namespace " + p.capxName + "-system"
		_, err = commons.ExecuteCommand(n, c)
		if err != nil {
			return errors.Wrap(err, "failed to create CAPx namespace")
		}

		// Create capx secret
		secret := strings.Split(p.capxEnvVars[0], "AZURE_CLIENT_SECRET=")[1]
		c = "kubectl --kubeconfig " + kubeconfigPath + " -n " + p.capxName + "-system create secret generic cluster-identity-secret --from-literal=clientSecret='" + string(secret) + "'"
		_, err = commons.ExecuteCommand(n, c)
		if err != nil {
			return errors.Wrap(err, "failed to create CAPx secret")
		}
	}

	// Install CAPX in worker cluster
	c = "clusterctl --kubeconfig " + kubeconfigPath + " init --wait-providers" +
		" --core " + CAPICoreProvider +
		" --bootstrap " + CAPIBootstrapProvider +
		" --control-plane " + CAPIControlPlaneProvider +
		" --infrastructure " + p.capxProvider + ":" + p.capxVersion
	_, err = commons.ExecuteCommand(n, c, p.capxEnvVars)
	if err != nil {
		return errors.Wrap(err, "failed to install CAPX in workload cluster")
	}

	// Scale CAPX to 2 replicas
	c = "kubectl --kubeconfig " + kubeconfigPath + " -n " + p.capxName + "-system scale --replicas 2 deploy " + p.capxName + "-controller-manager"
	_, err = commons.ExecuteCommand(n, c)
	if err != nil {
		return errors.Wrap(err, "failed to scale CAPX in workload cluster")
	}

	// Allow egress in CAPX's Namespace
	c = "kubectl --kubeconfig " + kubeconfigPath + " -n " + p.capxName + "-system apply -f " + allowAllEgressNetPolPath
	_, err = commons.ExecuteCommand(n, c)
	if err != nil {
		return errors.Wrap(err, "failed to apply CAPX's NetworkPolicy in workload cluster")
	}

	return nil
}

// installCAPXLocal installs CAPX in the local cluster
func (p *Provider) installCAPXLocal(n nodes.Node) error {
	var c string
	var err error

	if p.capxProvider == "azure" {
		// Create capx namespace
		c = "kubectl create namespace " + p.capxName + "-system"
		_, err = commons.ExecuteCommand(n, c)
		if err != nil {
			return errors.Wrap(err, "failed to create CAPx namespace")
		}

		// Create capx secret
		secret := strings.Split(p.capxEnvVars[0], "AZURE_CLIENT_SECRET=")[1]
		c = "kubectl -n " + p.capxName + "-system create secret generic cluster-identity-secret --from-literal=clientSecret='" + string(secret) + "'"
		_, err = commons.ExecuteCommand(n, c)
		if err != nil {
			return errors.Wrap(err, "failed to create CAPx secret")
		}
	}

	c = "clusterctl init --wait-providers" +
		" --core " + CAPICoreProvider +
		" --bootstrap " + CAPIBootstrapProvider +
		" --control-plane " + CAPIControlPlaneProvider +
		" --infrastructure " + p.capxProvider + ":" + p.capxVersion
	_, err = commons.ExecuteCommand(n, c, p.capxEnvVars)
	if err != nil {
		return errors.Wrap(err, "failed to install CAPX in local cluster")
	}

	return nil
}

func enableSelfHealing(n nodes.Node, keosCluster commons.KeosCluster, namespace string) error {
	var c string
	var err error

	if !keosCluster.Spec.ControlPlane.Managed {
		machineRole := "-control-plane-node"
		generateMHCManifest(n, keosCluster.Metadata.Name, namespace, machineHealthCheckControlPlaneNodePath, machineRole)

		c = "kubectl -n " + namespace + " apply -f " + machineHealthCheckControlPlaneNodePath
		_, err = commons.ExecuteCommand(n, c)
		if err != nil {
			return errors.Wrap(err, "failed to apply the MachineHealthCheck manifest")
		}
	}

	machineRole := "-worker-node"
	generateMHCManifest(n, keosCluster.Metadata.Name, namespace, machineHealthCheckWorkerNodePath, machineRole)

	c = "kubectl -n " + namespace + " apply -f " + machineHealthCheckWorkerNodePath
	_, err = commons.ExecuteCommand(n, c)
	if err != nil {
		return errors.Wrap(err, "failed to apply the MachineHealthCheck manifest")
	}

	return nil
}

func generateMHCManifest(n nodes.Node, clusterID string, namespace string, manifestPath string, machineRole string) error {
	var c string
	var err error
	var machineHealthCheck = `
apiVersion: cluster.x-k8s.io/v1beta1
kind: MachineHealthCheck
metadata:
  name: ` + clusterID + machineRole + `-unhealthy
  namespace: cluster-` + clusterID + `
spec:
  clusterName: ` + clusterID + `
  nodeStartupTimeout: 300s
  selector:
    matchLabels:
      keos.stratio.com/machine-role: ` + clusterID + machineRole + `
  unhealthyConditions:
    - type: Ready
      status: Unknown
      timeout: 60s
    - type: Ready
      status: 'False'
      timeout: 60s`

	c = "echo \"" + machineHealthCheck + "\" > " + manifestPath
	_, err = commons.ExecuteCommand(n, c)
	if err != nil {
		return errors.Wrap(err, "failed to write the MachineHealthCheck manifest")
	}

	return nil
}

func resto(n int, i int, azs int) int {
	var r int
	r = (n % azs) / (i + 1)
	if r > 1 {
		r = 1
	}
	return r
}

func GetClusterManifest(flavor string, params commons.TemplateParams, azs []string) (string, error) {
	funcMap := template.FuncMap{
		"loop": func(az string, zd string, qa int, maxsize int, minsize int) <-chan Node {
			ch := make(chan Node)
			go func() {
				var q int
				var mx int
				var mn int
				if az != "" {
					ch <- Node{AZ: az, QA: qa, MaxSize: maxsize, MinSize: minsize}
				} else {
					for i, a := range azs {
						if zd == "unbalanced" {
							q = qa/len(azs) + resto(qa, i, len(azs))
							mx = maxsize/len(azs) + resto(maxsize, i, len(azs))
							mn = minsize/len(azs) + resto(minsize, i, len(azs))
							ch <- Node{AZ: a, QA: q, MaxSize: mx, MinSize: mn}
						} else {
							ch <- Node{AZ: a, QA: qa / len(azs), MaxSize: maxsize / len(azs), MinSize: minsize / len(azs)}
						}
					}
				}
				close(ch)
			}()
			return ch
		},
		"hostname": func(s string) string {
			return strings.Split(s, "/")[0]
		},
		"checkReference": func(v interface{}) bool {
			defer func() { recover() }()
			return v != nil && !reflect.ValueOf(v).IsNil() && v != "nil" && v != "<nil>"
		},
		"isNotEmpty": func(v interface{}) bool {
			return !reflect.ValueOf(v).IsZero()
		},
		"inc": func(i int) int {
			return i + 1
		},
		"base64": func(s string) string {
			return base64.StdEncoding.EncodeToString([]byte(s))
		},
		"sub":   func(a, b int) int { return a - b },
		"split": strings.Split,
	}
	templatePath := filepath.Join("templates", params.KeosCluster.Spec.InfraProvider, flavor)

	var tpl bytes.Buffer
	t, err := template.New("").Funcs(funcMap).ParseFS(ctel, templatePath)
	if err != nil {
		return "", err
	}

	err = t.ExecuteTemplate(&tpl, flavor, params)
	if err != nil {
		return "", err
	}

	return tpl.String(), nil
}

func getManifest(parentPath string, name string, params interface{}) (string, error) {
	templatePath := filepath.Join("templates", parentPath, name)

	var tpl bytes.Buffer
	t, err := template.New("").ParseFS(ctel, templatePath)
	if err != nil {
		return "", err
	}

	err = t.ExecuteTemplate(&tpl, name, params)
	if err != nil {
		return "", err
	}
	return tpl.String(), nil
}
