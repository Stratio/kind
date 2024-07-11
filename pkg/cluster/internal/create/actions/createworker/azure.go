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
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"

	"github.com/Azure/azure-sdk-for-go/sdk/azcore/policy"
	"github.com/Azure/azure-sdk-for-go/sdk/resourcemanager/network/armnetwork/v4"
	"gopkg.in/yaml.v3"
	"sigs.k8s.io/kind/pkg/cluster/nodes"
	"sigs.k8s.io/kind/pkg/commons"
	"sigs.k8s.io/kind/pkg/errors"
	"sigs.k8s.io/kind/pkg/exec"
)

//go:embed files/azure/azure-storage-classes.yaml
var azureStorageClasses string

//go:embed files/azure/internal-ingress-nginx.yaml
var azureInternalIngress []byte

type AzureBuilder struct {
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

var azureCharts = ChartsDictionary{
	Charts: map[string]map[string]map[string]commons.ChartEntry{
		"28": {
			"managed": {},
			"unmanaged": {
				"azuredisk-csi-driver": {Repository: "https://raw.githubusercontent.com/kubernetes-sigs/azuredisk-csi-driver/master/charts", Namespace: "kube-system", Version: "v1.28.7", Pull: false},
				"azurefile-csi-driver": {Repository: "https://raw.githubusercontent.com/kubernetes-sigs/azurefile-csi-driver/master/charts", Namespace: "kube-system", Version: "v1.28.7", Pull: false},
				"cloud-provider-azure": {Repository: "https://raw.githubusercontent.com/kubernetes-sigs/cloud-provider-azure/master/helm/repo", Namespace: "kube-system", Version: "v1.28.9", Pull: true},
				"cluster-autoscaler":   {Repository: "https://kubernetes.github.io/autoscaler", Version: "9.34.1", Namespace: "kube-system", Pull: false},
				"tigera-operator":      {Repository: "https://docs.projectcalico.org/charts", Version: "v3.27.3", Namespace: "tigera-operator", Pull: true},
			},
		},
		"29": {
			"managed": {},
			"unmanaged": {
				"azuredisk-csi-driver": {Repository: "https://raw.githubusercontent.com/kubernetes-sigs/azuredisk-csi-driver/master/charts", Namespace: "kube-system", Version: "v1.29.5", Pull: false},
				"azurefile-csi-driver": {Repository: "https://raw.githubusercontent.com/kubernetes-sigs/azurefile-csi-driver/master/charts", Namespace: "kube-system", Version: "v1.29.5", Pull: false},
				"cloud-provider-azure": {Repository: "https://raw.githubusercontent.com/kubernetes-sigs/cloud-provider-azure/master/helm/repo", Namespace: "kube-system", Version: "v1.29.6", Pull: true},
				"cluster-autoscaler":   {Repository: "https://kubernetes.github.io/autoscaler", Version: "9.35.0", Namespace: "kube-system", Pull: false},
				"tigera-operator":      {Repository: "https://docs.projectcalico.org/charts", Version: "v3.27.3", Namespace: "tigera-operator", Pull: true},
			},
		},
		"30": {
			"managed": {},
			"unmanaged": {
				"azuredisk-csi-driver": {Repository: "https://raw.githubusercontent.com/kubernetes-sigs/azuredisk-csi-driver/master/charts", Namespace: "kube-system", Version: "v1.30.0", Pull: false},
				"azurefile-csi-driver": {Repository: "https://raw.githubusercontent.com/kubernetes-sigs/azurefile-csi-driver/master/charts", Namespace: "kube-system", Version: "v1.30.2", Pull: false},
				"cloud-provider-azure": {Repository: "https://raw.githubusercontent.com/kubernetes-sigs/cloud-provider-azure/master/helm/repo", Namespace: "kube-system", Version: "v1.30.2", Pull: true},
				"cluster-autoscaler":   {Repository: "https://kubernetes.github.io/autoscaler", Version: "9.37.0", Namespace: "kube-system", Pull: false},
				"tigera-operator":      {Repository: "https://docs.projectcalico.org/charts", Version: "v3.27.3", Namespace: "tigera-operator", Pull: true},
			},
		},
	},
}

func newAzureBuilder() *AzureBuilder {
	return &AzureBuilder{}
}

func (b *AzureBuilder) setCapx(managed bool) {
	b.capxProvider = "azure"
	b.capxVersion = "v1.11.4"
	b.capxImageVersion = "v1.11.4"
	b.capxName = "capz"
	b.capxManaged = managed
	b.csiNamespace = "kube-system"
}

func (b *AzureBuilder) setSC(p ProviderParams) {
	if (p.StorageClass.Parameters != commons.SCParameters{}) {
		b.scParameters = p.StorageClass.Parameters
	}

	if b.scParameters.Provisioner == "" {
		b.scProvisioner = "disk.csi.azure.com"
	} else {
		b.scProvisioner = b.scParameters.Provisioner
		b.scParameters.Provisioner = ""
	}

	if b.scParameters.SkuName == "" {
		if p.StorageClass.Class == "premium" {
			b.scParameters.SkuName = "Premium_LRS"
		} else {
			b.scParameters.SkuName = "StandardSSD_LRS"
		}
	}

	if p.StorageClass.EncryptionKey != "" {
		b.scParameters.DiskEncryptionSetID = p.StorageClass.EncryptionKey
	}
}

func (b *AzureBuilder) pullProviderCharts(n nodes.Node, clusterConfigSpec *commons.ClusterConfigSpec, keosSpec commons.KeosSpec, clusterType string) error {
	return pullGenericCharts(n, clusterConfigSpec, keosSpec, azureCharts, clusterType)
}

func (b *AzureBuilder) getProviderCharts(clusterConfigSpec *commons.ClusterConfigSpec, keosSpec commons.KeosSpec, clusterType string) map[string]commons.ChartEntry {
	return getGenericCharts(clusterConfigSpec, keosSpec, azureCharts, clusterType)
}

func (b *AzureBuilder) getOverriddenCharts(charts *[]commons.Chart, clusterConfigSpec *commons.ClusterConfigSpec, clusterType string) []commons.Chart {
	providerCharts := ConvertToChart(azureCharts.Charts[majorVersion][clusterType])
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

func (b *AzureBuilder) setCapxEnvVars(p ProviderParams) {
	b.capxEnvVars = []string{
		"AZURE_CLIENT_SECRET_B64=" + base64.StdEncoding.EncodeToString([]byte(p.Credentials["ClientSecret"])),
		"AZURE_CLIENT_ID_B64=" + base64.StdEncoding.EncodeToString([]byte(p.Credentials["ClientID"])),
		"AZURE_SUBSCRIPTION_ID_B64=" + base64.StdEncoding.EncodeToString([]byte(p.Credentials["SubscriptionID"])),
		"AZURE_TENANT_ID_B64=" + base64.StdEncoding.EncodeToString([]byte(p.Credentials["TenantID"])),
	}
	if p.Managed {
		b.capxEnvVars = append(b.capxEnvVars, "EXP_MACHINE_POOL=true")
	}
	if p.GithubToken != "" {
		b.capxEnvVars = append(b.capxEnvVars, "GITHUB_TOKEN="+p.GithubToken)
	}
}

func (b *AzureBuilder) getProvider() Provider {
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

func (b *AzureBuilder) installCloudProvider(n nodes.Node, k string, privateParams PrivateParams) error {
	var podsCidrBlock string
	keosCluster := privateParams.KeosCluster
	if keosCluster.Spec.Networks.PodsCidrBlock != "" {
		podsCidrBlock = keosCluster.Spec.Networks.PodsCidrBlock
	} else {
		podsCidrBlock = "192.168.0.0/16"
	}

	cloudControllerManagerValuesFile := "/kind/cloud-provider-" + keosCluster.Spec.InfraProvider + "-helm-values.yaml"
	cloudControllerManagerHelmParams := cloudControllerHelmParams{
		ClusterName: privateParams.KeosCluster.Metadata.Name,
		Private:     privateParams.Private,
		KeosRegUrl:  privateParams.KeosRegUrl,
		PodsCidr:    podsCidrBlock,
	}

	// Generate the CCM helm values
	cloudControllerManagerHelmValues, err := getManifest(b.capxProvider, "cloud-provider-"+keosCluster.Spec.InfraProvider+"-helm-values.tmpl", majorVersion, cloudControllerManagerHelmParams)
	if err != nil {
		return errors.Wrap(err, "failed to create cloud controller manager Helm chart values file")
	}
	c := "echo '" + cloudControllerManagerHelmValues + "' > " + cloudControllerManagerValuesFile
	_, err = commons.ExecuteCommand(n, c, 5, 3)
	if err != nil {
		return errors.Wrap(err, "failed to create cloud controller manager Helm chart values file")
	}

	c = "helm install cloud-provider-azure /stratio/helm/cloud-provider-azure" +
		" --kubeconfig " + k +
		" --namespace kube-system" +
		" --set cloudControllerManager.replicas=1" +
		" --values " + cloudControllerManagerValuesFile
	_, err = commons.ExecuteCommand(n, c, 5, 3)
	if err != nil {
		return errors.Wrap(err, "failed to deploy cloud-provider-azure Helm Chart")
	}

	return nil
}

func (b *AzureBuilder) installCSI(n nodes.Node, kubeconfigPath string, privateParams PrivateParams, chartsList map[string]commons.ChartEntry) error {
	var c string
	var err error

	for _, csiName := range []string{"azuredisk-csi-driver", "azurefile-csi-driver"} {
		csiValuesFile := "/kind/" + csiName + "-helm-values.yaml"
		csiEntry := chartsList[csiName]
		csiHelmReleaseParams := fluxHelmReleaseParams{
			ChartRepoRef:   "keos",
			ChartName:      csiName,
			ChartNamespace: csiEntry.Namespace,
			ChartVersion:   csiEntry.Version,
		}
		if !privateParams.HelmPrivate {
			csiHelmReleaseParams.ChartRepoRef = csiName
		}
		// Generate the csiName-csi helm values
		csiHelmValues, getManifestErr := getManifest(privateParams.KeosCluster.Spec.InfraProvider, csiName+"-helm-values.tmpl", majorVersion, privateParams)
		if getManifestErr != nil {
			return errors.Wrap(getManifestErr, "failed to generate "+csiName+"-csi helm values")
		}
		c = "echo '" + csiHelmValues + "' > " + csiValuesFile
		_, err = commons.ExecuteCommand(n, c, 5, 3)
		if err != nil {
			return errors.Wrap(err, "failed to create "+csiName+" Helm chart values file")
		}
		if err := configureHelmRelease(n, kubeconfigPath, "flux2_helmrelease.tmpl", csiHelmReleaseParams); err != nil {
			return err
		}
	// Deploy disk CSI driver
	c = "helm install azuredisk-csi-driver /stratio/helm/azuredisk-csi-driver " +
		" --kubeconfig " + k +
		" --namespace " + b.csiNamespace +
		" --set controller.podAnnotations.\"cluster-autoscaler\\.kubernetes\\.io/safe-to-evict-local-volumes=socket-dir\\,azure-cred\""

	if privateParams.Private {
		c += " --set image.baseRepo=" + privateParams.KeosRegUrl
	}

	_, err = commons.ExecuteCommand(n, c, 3, 5)
	if err != nil {
		return errors.Wrap(err, "failed to deploy Azure Disk CSI driver Helm Chart")
	}

	// Deploy file CSI driver
	c = "helm install azurefile-csi-driver /stratio/helm/azurefile-csi-driver " +
		" --kubeconfig " + k +
		" --namespace " + b.csiNamespace +
		" --set controller.podAnnotations.\"cluster-autoscaler\\.kubernetes\\.io/safe-to-evict-local-volumes=socket-dir\\,azure-cred\""

	if privateParams.Private {
		c += " --set image.baseRepo=" + privateParams.KeosRegUrl
	}

	_, err = commons.ExecuteCommand(n, c, 3, 5)
	if err != nil {
		return errors.Wrap(err, "failed to deploy Azure File CSI driver Helm Chart")
	}

	return nil
}

func (b *AzureBuilder) getRegistryCredentials(p ProviderParams, u string) (string, string, error) {
	var registryUser = "00000000-0000-0000-0000-000000000000"
	var registryPass string
	var ctx = context.Background()
	var response map[string]interface{}

	cfg, err := commons.AzureGetConfig(p.Credentials)
	if err != nil {
		return "", "", err
	}
	aadToken, err := cfg.GetToken(ctx, policy.TokenRequestOptions{Scopes: []string{"https://management.azure.com/.default"}})
	if err != nil {
		return "", "", err
	}
	acrService := strings.Split(u, "/")[0]
	formData := url.Values{
		"grant_type":   {"access_token"},
		"service":      {acrService},
		"tenant":       {p.Credentials["TenantID"]},
		"access_token": {aadToken.Token},
	}
	jsonResponse, err := http.PostForm(fmt.Sprintf("https://%s/oauth2/exchange", acrService), formData)
	if err != nil {
		return "", "", err
	} else if jsonResponse.StatusCode == http.StatusUnauthorized {
		return "", "", errors.New("Failed to obtain the ACR token with the provided credentials, please check the roles assigned to the correspondent Azure AD app")
	}
	json.NewDecoder(jsonResponse.Body).Decode(&response)
	if response["access_token"] != nil {
		registryPass = response["access_token"].(string)
	} else if response["refresh_token"] != nil {
		registryPass = response["refresh_token"].(string)
	} else {
		return "", "", errors.New("Failed to obtain the ACR token with the provided credentials, please check the roles assigned to the correspondent Azure AD app")
	}
	return registryUser, registryPass, nil
}

func (b *AzureBuilder) configureStorageClass(n nodes.Node, k string) error {
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

	if !b.capxManaged {
		// Create Azure storage classes
		cmd = n.Command("kubectl", "--kubeconfig", k, "apply", "-f", "-")
		if err := cmd.SetStdin(strings.NewReader(azureStorageClasses)).Run(); err != nil {
			return errors.Wrap(err, "failed to create Azure storage classes")
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

func (b *AzureBuilder) internalNginx(p ProviderParams, networks commons.Networks) (bool, error) {
	var resourceGroup string
	var ctx = context.Background()

	cfg, err := commons.AzureGetConfig(p.Credentials)
	if err != nil {
		return false, err
	}
	networkClientFactory, err := armnetwork.NewClientFactory(p.Credentials["SubscriptionID"], cfg, nil)
	if err != nil {
		return false, err
	}
	subnetsClient := networkClientFactory.NewSubnetsClient()
	if len(networks.Subnets) > 0 {
		if networks.ResourceGroup != "" {
			resourceGroup = networks.ResourceGroup
		} else {
			resourceGroup = p.ClusterName
		}
		for _, s := range networks.Subnets {
			publicSubnetID, err := AzureFilterPublicSubnet(ctx, subnetsClient, resourceGroup, networks.VPCID, s.SubnetId)
			if err != nil || len(publicSubnetID) > 0 {
				return false, err
			}
		}
		return true, nil
	}
	return false, nil
}

func AzureFilterPublicSubnet(ctx context.Context, subnetsClient *armnetwork.SubnetsClient, resourceGroup string, VPCID string, subnetID string) (string, error) {
	subnet, err := subnetsClient.Get(ctx, resourceGroup, VPCID, subnetID, nil)
	if err != nil {
		return "", err
	}

	if subnet.Properties.NatGateway != nil && strings.Contains(*subnet.Properties.NatGateway.ID, "natGateways") {
		return "", nil
	} else {
		return subnetID, nil
	}
}

func (b *AzureBuilder) getOverrideVars(p ProviderParams, networks commons.Networks, clusterConfigSpec commons.ClusterConfigSpec) (map[string][]byte, error) {
	var overrideVars = make(map[string][]byte)

	requiredInternalNginx, err := b.internalNginx(p, networks)
	if err != nil {
		return nil, err
	}
	if requiredInternalNginx {
		overrideVars = addOverrideVar("ingress-nginx.yaml", azureInternalIngress, overrideVars)
	}
	return overrideVars, nil
}

func (b *AzureBuilder) postInstallPhase(n nodes.Node, k string) error {
	var coreDNSPDBName = "coredns"

	if b.capxManaged {
		coreDNSPDBName = "coredns-pdb"

		err := patchDeploy(n, k, "kube-system", "coredns", "{\"spec\": {\"template\": {\"metadata\": {\"annotations\": {\""+postInstallAnnotation+"\": \"tmp\"}}}}}")
		if err != nil {
			return errors.Wrap(err, "failed to add podAnnotation to coredns")
		}
		err = patchDeploy(n, k, "tigera-operator", "tigera-operator", "{\"spec\": {\"template\": {\"metadata\": {\"annotations\": {\""+postInstallAnnotation+"\": \"var-lib-calico\"}}}}}")
		if err != nil {
			return errors.Wrap(err, "failed to add podAnnotation to tigera-operator")
		}
		err = patchDeploy(n, k, "kube-system", "metrics-server", "{\"spec\": {\"template\": {\"metadata\": {\"annotations\": {\""+postInstallAnnotation+"\": \"tmp-dir\"}}}}}")
		if err != nil {
			return errors.Wrap(err, "failed to add podAnnotation to metrics-server")
		}

	} else {
		err := patchDeploy(n, k, "kube-system", "cloud-controller-manager", "{\"spec\": {\"template\": {\"metadata\": {\"annotations\": {\""+postInstallAnnotation+"\": \"etc-kubernetes,ssl-mount,msi\"}}}}}")
		if err != nil {
			return errors.Wrap(err, "failed to add podAnnotation to cloud-controller-manager")
		}
	}

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
