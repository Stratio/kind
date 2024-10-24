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
	"context"
	_ "embed"
	b64 "encoding/base64"
	"encoding/json"
	"net/url"
	"strings"

	"golang.org/x/oauth2/google"
	"google.golang.org/api/compute/v1"
	"google.golang.org/api/option"
	"gopkg.in/yaml.v3"
	"sigs.k8s.io/kind/pkg/cluster/nodes"
	"sigs.k8s.io/kind/pkg/commons"
	"sigs.k8s.io/kind/pkg/errors"
	"sigs.k8s.io/kind/pkg/exec"
)

//go:embed files/gcp/internal-ingress-nginx.yaml
var gcpInternalIngress []byte

type GCPBuilder struct {
	capxProvider     string
	capxVersion      string
	capxImageVersion string
	capxManaged      bool
	capxName         string
	capxEnvVars      []string
	scParameters     commons.SCParameters
	scProvisioner    string
	csiNamespace     string
}

var googleCharts = ChartsDictionary{
	Charts: map[string]map[string]map[string]commons.ChartEntry{
		"28": {
			"managed": {
				"tigera-operator": {Repository: "https://docs.projectcalico.org/charts", Version: "v3.28.2", Namespace: "tigera-operator", Pull: false, Reconcile: false},
			},
			"unmanaged": {
				"cluster-autoscaler": {Repository: "https://kubernetes.github.io/autoscaler", Version: "9.34.1", Namespace: "kube-system", Pull: false, Reconcile: false},
				"tigera-operator":    {Repository: "https://docs.projectcalico.org/charts", Version: "v3.28.2", Namespace: "tigera-operator", Pull: true, Reconcile: true},
			},
		},
		"29": {
			"managed": {
				"tigera-operator": {Repository: "https://docs.projectcalico.org/charts", Version: "v3.28.2", Namespace: "tigera-operator", Pull: false, Reconcile: false},
			},
			"unmanaged": {
				"cluster-autoscaler": {Repository: "https://kubernetes.github.io/autoscaler", Version: "9.35.0", Namespace: "kube-system", Pull: false},
				"tigera-operator":    {Repository: "https://docs.projectcalico.org/charts", Version: "v3.28.2", Namespace: "tigera-operator", Pull: true, Reconcile: true},
			},
		},
		"30": {
			"managed": {
				"tigera-operator": {Repository: "https://docs.projectcalico.org/charts", Version: "v3.28.2", Namespace: "tigera-operator", Pull: false, Reconcile: false},
			},
			"unmanaged": {
				// "default" repository defaults to the descriptor Helm repository
				"gcp-cloud-controller-manager": {Repository: "default", Version: "1.30.0", Namespace: "kube-system", Pull: true, Reconcile: true},
				"cluster-autoscaler":           {Repository: "https://kubernetes.github.io/autoscaler", Version: "9.37.0", Namespace: "kube-system", Pull: false, Reconcile: false},
				"tigera-operator":              {Repository: "https://docs.projectcalico.org/charts", Version: "v3.28.2", Namespace: "tigera-operator", Pull: true, Reconcile: true},
			},
		},
	},
}

func newGCPBuilder() *GCPBuilder {
	return &GCPBuilder{}
}

func (b *GCPBuilder) setCapx(managed bool, capx commons.CAPX) {
	b.capxProvider = "gcp"
	b.capxVersion = capx.CAPG_Version
	b.capxImageVersion = capx.CAPG_Image_version
	b.capxName = "capg"
	b.capxManaged = managed
	b.csiNamespace = "kube-system"
}

func (b *GCPBuilder) setCapxEnvVars(p ProviderParams) {
	data := map[string]interface{}{
		"type":                        "service_account",
		"project_id":                  p.Credentials["ProjectID"],
		"private_key_id":              p.Credentials["PrivateKeyID"],
		"private_key":                 p.Credentials["PrivateKey"],
		"client_email":                p.Credentials["ClientEmail"],
		"client_id":                   p.Credentials["ClientID"],
		"auth_uri":                    "https://accounts.google.com/o/oauth2/auth",
		"token_uri":                   "https://accounts.google.com/o/oauth2/token",
		"auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
		"client_x509_cert_url":        "https://www.googleapis.com/robot/v1/metadata/x509/" + url.QueryEscape(p.Credentials["ClientEmail"]),
	}
	jsonData, _ := json.Marshal(data)
	b.capxEnvVars = []string{
		"GCP_B64ENCODED_CREDENTIALS=" + b64.StdEncoding.EncodeToString([]byte(jsonData)),
	}
	if p.Managed {
		b.capxEnvVars = append(b.capxEnvVars, "EXP_MACHINE_POOL=true")
		b.capxEnvVars = append(b.capxEnvVars, "EXP_CAPG_GKE=true")
	}
	if p.GithubToken != "" {
		b.capxEnvVars = append(b.capxEnvVars, "GITHUB_TOKEN="+p.GithubToken)
	}
}

func (b *GCPBuilder) setSC(p ProviderParams) {
	if (p.StorageClass.Parameters != commons.SCParameters{}) {
		b.scParameters = p.StorageClass.Parameters
	}

	b.scProvisioner = "pd.csi.storage.gke.io"

	if b.scParameters.Type == "" {
		b.scParameters.Type = "pd-ssd"
	}

	if p.StorageClass.EncryptionKey != "" {
		b.scParameters.DiskEncryptionKmsKey = p.StorageClass.EncryptionKey
	}
}

func (b *GCPBuilder) pullProviderCharts(n nodes.Node, clusterConfigSpec *commons.ClusterConfigSpec, keosSpec commons.KeosSpec, clusterCredentials commons.ClusterCredentials, clusterType string) error {
	return pullGenericCharts(n, clusterConfigSpec, keosSpec, clusterCredentials, googleCharts, clusterType)
}

func (b *GCPBuilder) getProviderCharts(clusterConfigSpec *commons.ClusterConfigSpec, keosSpec commons.KeosSpec, clusterType string) map[string]commons.ChartEntry {
	return getGenericCharts(clusterConfigSpec, keosSpec, googleCharts, clusterType)
}

func (b *GCPBuilder) getOverriddenCharts(charts *[]commons.Chart, clusterConfigSpec *commons.ClusterConfigSpec, clusterType string) []commons.Chart {
	providerCharts := ConvertToChart(googleCharts.Charts[majorVersion][clusterType])
	for _, ovChart := range clusterConfigSpec.Charts {
		for _, chart := range *providerCharts {
			if chart.Name == ovChart.Name {
				chart.Version = ovChart.Version
			}
		}
	}
	*charts = append(*charts, *providerCharts...)
	return *charts
}

func (b *GCPBuilder) getProvider() Provider {
	return Provider{
		capxProvider:     b.capxProvider,
		capxVersion:      b.capxVersion,
		capxImageVersion: b.capxImageVersion,
		capxManaged:      b.capxManaged,
		capxName:         b.capxName,
		capxEnvVars:      b.capxEnvVars,
		scParameters:     b.scParameters,
		scProvisioner:    b.scProvisioner,
		csiNamespace:     b.csiNamespace,
	}
}

func (b *GCPBuilder) installCloudProvider(n nodes.Node, k string, privateParams PrivateParams) error {
	var podsCidrBlock string
	keosCluster := privateParams.KeosCluster
	if keosCluster.Spec.Networks.PodsCidrBlock != "" {
		podsCidrBlock = keosCluster.Spec.Networks.PodsCidrBlock
	} else {
		podsCidrBlock = "192.168.0.0/16"
	}

	cloudControllerManagerValuesFile := "/kind/gcp-cloud-controller-manager-helm-values.yaml"
	cloudControllerManagerHelmParams := cloudControllerHelmParams{
		ClusterName: privateParams.KeosCluster.Metadata.Name,
		Private:     privateParams.Private,
		KeosRegUrl:  privateParams.KeosRegUrl,
		PodsCidr:    podsCidrBlock,
	}

	// Generate the CCM helm values
	cloudControllerManagerHelmValues, err := getManifest(b.capxProvider, "gcp-cloud-controller-manager-helm-values.tmpl", majorVersion, cloudControllerManagerHelmParams)
	if err != nil {
		return errors.Wrap(err, "failed to create cloud controller manager Helm chart values file")
	}
	c := "echo '" + cloudControllerManagerHelmValues + "' > " + cloudControllerManagerValuesFile
	_, err = commons.ExecuteCommand(n, c, 5, 3)
	if err != nil {
		return errors.Wrap(err, "failed to create cloud controller manager Helm chart values file")
	}

	c = "helm install gcp-cloud-controller-manager /stratio/helm/gcp-cloud-controller-manager" +
		" --kubeconfig " + k +
		" --namespace kube-system" +
		" --values " + cloudControllerManagerValuesFile
	_, err = commons.ExecuteCommand(n, c, 5, 3)
	if err != nil {
		return errors.Wrap(err, "failed to deploy gcp-cloud-controller-manager Helm Chart")
	}

	return nil
}

func (b *GCPBuilder) installCSI(n nodes.Node, k string, privateParams PrivateParams, providerParams ProviderParams, charstList map[string]commons.ChartEntry) error {
	var c string
	var err error
	var cmd exec.Cmd

	// Create CSI secret in CSI namespace
	secret, _ := b64.StdEncoding.DecodeString(strings.Split(b.capxEnvVars[0], "GCP_B64ENCODED_CREDENTIALS=")[1])
	c = "kubectl --kubeconfig " + k + " -n " + b.csiNamespace + " create secret generic cloud-sa --from-literal=cloud-sa.json='" + string(secret) + "'"
	_, err = commons.ExecuteCommand(n, c, 5, 3)
	if err != nil {
		return errors.Wrap(err, "failed to create CSI secret in CSI namespace")
	}

	csiManifests, err := getManifest(privateParams.KeosCluster.Spec.InfraProvider, "gcp-compute-persistent-disk-csi-driver.tmpl", majorVersion, privateParams)
	if err != nil {
		return errors.Wrap(err, "failed to get CSI driver manifests")
	}

	// Deploy CSI driver
	cmd = n.Command("kubectl", "--kubeconfig", k, "apply", "-f", "-")
	if err = cmd.SetStdin(strings.NewReader(csiManifests)).Run(); err != nil {
		return errors.Wrap(err, "failed to deploy CSI driver")
	}

	return nil
}

func (b *GCPBuilder) getRegistryCredentials(p ProviderParams, u string) (string, string, error) {
	var registryUser = "oauth2accesstoken"
	var ctx = context.Background()
	scope := "https://www.googleapis.com/auth/cloud-platform"
	key, _ := b64.StdEncoding.DecodeString(strings.Split(b.capxEnvVars[0], "GCP_B64ENCODED_CREDENTIALS=")[1])
	creds, err := google.CredentialsFromJSON(ctx, key, scope)
	if err != nil {
		return "", "", err
	}
	token, err := creds.TokenSource.Token()
	if err != nil {
		return "", "", err
	}
	return registryUser, token.AccessToken, nil
}

func (b *GCPBuilder) configureStorageClass(n nodes.Node, k string) error {
	var c string
	var err error
	var cmd exec.Cmd

	if b.capxManaged {
		// Remove annotation from default storage class
		c = "kubectl --kubeconfig " + k + ` get sc -o jsonpath='{.items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")].metadata.name}'`
		output, err := commons.ExecuteCommand(n, c, 5, 3)
		if err != nil {
			return errors.Wrap(err, "failed to get default storage class")
		}
		if strings.TrimSpace(output) != "" && strings.TrimSpace(output) != "No resources found" {
			c = "kubectl --kubeconfig " + k + " annotate sc " + strings.TrimSpace(output) + " " + defaultScAnnotation + "-"
			_, err = commons.ExecuteCommand(n, c, 5, 3)
			if err != nil {
				return errors.Wrap(err, "failed to remove annotation from default storage class")
			}
		}
	}

	scTemplate.Parameters = b.scParameters
	scTemplate.Provisioner = b.scProvisioner

	scBytes, err := yaml.Marshal(scTemplate)
	if err != nil {
		return err
	}
	storageClass := strings.Replace(string(scBytes), "fsType", "csi.storage.k8s.io/fstype", -1)

	cmd = n.Command("kubectl", "--kubeconfig", k, "apply", "-f", "-")
	if err = cmd.SetStdin(strings.NewReader(storageClass)).Run(); err != nil {
		return errors.Wrap(err, "failed to create default storage class")
	}

	return nil
}

func (b *GCPBuilder) internalNginx(p ProviderParams, networks commons.Networks) (bool, error) {
	var ctx = context.Background()

	secrets, _ := b64.StdEncoding.DecodeString(strings.Split(b.capxEnvVars[0], "GCP_B64ENCODED_CREDENTIALS=")[1])
	cfg := option.WithCredentialsJSON(secrets)
	computeService, err := compute.NewService(ctx, cfg)
	if err != nil {
		return false, err
	}
	if len(networks.Subnets) > 0 {
		for _, s := range networks.Subnets {
			publicSubnetID, _ := GCPFilterPublicSubnet(computeService, p.Credentials["ProjectID"], p.Region, s.SubnetId)
			if len(publicSubnetID) > 0 {
				return false, nil
			}
		}
		return true, nil
	}
	return false, nil
}

func GCPFilterPublicSubnet(computeService *compute.Service, projectID string, region string, subnetID string) (string, error) {
	subnet, err := computeService.Subnetworks.Get(projectID, region, subnetID).Do()
	if err != nil {
		return "", err
	}
	if subnet.PrivateIpGoogleAccess {
		return "", nil
	} else {
		return subnetID, nil
	}
}

func (b *GCPBuilder) getOverrideVars(p ProviderParams, networks commons.Networks, clusterConfigSpec commons.ClusterConfigSpec) (map[string][]byte, error) {
	var overrideVars = make(map[string][]byte)

	requiredInternalNginx, err := b.internalNginx(p, networks)
	if err != nil {
		return nil, err
	}
	if requiredInternalNginx {
		overrideVars = addOverrideVar("ingress-nginx.yaml", gcpInternalIngress, overrideVars)
	}
	return overrideVars, nil
}

func (b *GCPBuilder) postInstallPhase(n nodes.Node, k string) error {
	var coreDNSPDBName = "coredns"

	c := "kubectl --kubeconfig " + kubeconfigPath + " get pdb " + coreDNSPDBName + " -n kube-system"
	_, err := commons.ExecuteCommand(n, c, 5, 3)
	if err != nil {
		err = installCorednsPdb(n)
		if err != nil {
			return errors.Wrap(err, "failed to add core dns PDB")
		}
	}

	return nil
}
