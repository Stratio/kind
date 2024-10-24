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
	"regexp"
	"strconv"
	"strings"

	"github.com/aws/aws-sdk-go-v2/service/ec2"
	"github.com/aws/aws-sdk-go-v2/service/ecr"
	"gopkg.in/yaml.v3"
	"sigs.k8s.io/kind/pkg/cluster/nodes"
	"sigs.k8s.io/kind/pkg/commons"
	"sigs.k8s.io/kind/pkg/errors"
	"sigs.k8s.io/kind/pkg/exec"
)

//go:embed files/aws/internal-ingress-nginx.yaml
var awsInternalIngress []byte

//go:embed files/aws/public-ingress-nginx.yaml
var awsPublicIngress []byte

type AWSBuilder struct {
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

type lbControllerHelmParams struct {
	ClusterName string
	Private     bool
	KeosRegUrl  string
	AccountID   string
	RoleName    string
}

func newAWSBuilder() *AWSBuilder {
	return &AWSBuilder{}
}

func (b *AWSBuilder) setCapx(managed bool, capx commons.CAPX) {
	b.capxProvider = "aws"
	b.capxVersion = capx.CAPA_Version
	b.capxImageVersion = capx.CAPA_Image_version
	b.capxName = "capa"
	b.capxManaged = managed
	b.csiNamespace = "kube-system"
}

func (b *AWSBuilder) setCapxEnvVars(p ProviderParams) {
	awsCredentials := "[default]\naws_access_key_id = " + p.Credentials["AccessKey"] + "\naws_secret_access_key = " + p.Credentials["SecretKey"] + "\nregion = " + p.Region + "\n"
	b.capxEnvVars = []string{
		"AWS_REGION=" + p.Region,
		"AWS_ACCESS_KEY_ID=" + p.Credentials["AccessKey"],
		"AWS_SECRET_ACCESS_KEY=" + p.Credentials["SecretKey"],
		"AWS_B64ENCODED_CREDENTIALS=" + base64.StdEncoding.EncodeToString([]byte(awsCredentials)),
		"CAPA_EKS_IAM=true",
	}
	if p.GithubToken != "" {
		b.capxEnvVars = append(b.capxEnvVars, "GITHUB_TOKEN="+p.GithubToken)
	}
}

func (b *AWSBuilder) setSC(p ProviderParams) {
	if (p.StorageClass.Parameters != commons.SCParameters{}) {
		b.scParameters = p.StorageClass.Parameters
	}

	b.scProvisioner = "ebs.csi.aws.com"

	if b.scParameters.Type == "" {
		if p.StorageClass.Class == "premium" {
			b.scParameters.Type = "io2"
			b.scParameters.IopsPerGB = "64000"
		} else {
			b.scParameters.Type = "gp3"
		}
	}

	if p.StorageClass.EncryptionKey != "" {
		b.scParameters.Encrypted = "true"
		b.scParameters.KmsKeyId = p.StorageClass.EncryptionKey
	}
}

var awsCharts = ChartsDictionary{
	Charts: map[string]map[string]map[string]commons.ChartEntry{
		"28": {
			"managed": {
				"aws-load-balancer-controller": {Repository: "https://aws.github.io/eks-charts", Version: "1.8.0", Namespace: "kube-system", Pull: false, Reconcile: false},
				"cluster-autoscaler":           {Repository: "https://kubernetes.github.io/autoscaler", Version: "9.34.1", Namespace: "kube-system", Pull: false, Reconcile: false},
				"tigera-operator":              {Repository: "https://docs.projectcalico.org/charts", Version: "v3.28.2", Namespace: "tigera-operator", Pull: true, Reconcile: true},
			},
			"unmanaged": {
				"aws-cloud-controller-manager": {Repository: "https://kubernetes.github.io/cloud-provider-aws", Version: "0.0.8", Namespace: "kube-system", Pull: true, Reconcile: true},
				"aws-ebs-csi-driver":           {Repository: "https://kubernetes-sigs.github.io/aws-ebs-csi-driver", Version: "2.31.0", Namespace: "kube-system", Pull: false, Reconcile: false},
				"cluster-autoscaler":           {Repository: "https://kubernetes.github.io/autoscaler", Version: "9.34.1", Namespace: "kube-system", Pull: false, Reconcile: false},
				"tigera-operator":              {Repository: "https://docs.projectcalico.org/charts", Version: "v3.28.2", Namespace: "tigera-operator", Pull: true, Reconcile: true},
			},
		},
		"29": {
			"managed": {
				"aws-load-balancer-controller": {Repository: "https://aws.github.io/eks-charts", Version: "1.8.0", Namespace: "kube-system", Pull: false, Reconcile: false},
				"cluster-autoscaler":           {Repository: "https://kubernetes.github.io/autoscaler", Version: "9.35.0", Namespace: "kube-system", Pull: false, Reconcile: false},
				"tigera-operator":              {Repository: "https://docs.projectcalico.org/charts", Version: "v3.28.2", Namespace: "tigera-operator", Pull: true, Reconcile: true},
			},
			"unmanaged": {
				"aws-cloud-controller-manager": {Repository: "https://kubernetes.github.io/cloud-provider-aws", Version: "0.0.8", Namespace: "kube-system", Pull: true, Reconcile: true},
				"aws-ebs-csi-driver":           {Repository: "https://kubernetes-sigs.github.io/aws-ebs-csi-driver", Version: "2.31.0", Namespace: "kube-system", Pull: false, Reconcile: false},
				"cluster-autoscaler":           {Repository: "https://kubernetes.github.io/autoscaler", Version: "9.35.0", Namespace: "kube-system", Pull: false, Reconcile: false},
				"tigera-operator":              {Repository: "https://docs.projectcalico.org/charts", Version: "v3.28.2", Namespace: "tigera-operator", Pull: true, Reconcile: true},
			},
		},
		"30": {
			"managed": {
				"aws-load-balancer-controller": {Repository: "https://aws.github.io/eks-charts", Version: "1.8.1", Namespace: "kube-system", Pull: false, Reconcile: false},
				"cluster-autoscaler":           {Repository: "https://kubernetes.github.io/autoscaler", Version: "9.37.0", Namespace: "kube-system", Pull: false, Reconcile: false},
				"tigera-operator":              {Repository: "https://docs.projectcalico.org/charts", Version: "v3.28.0", Namespace: "tigera-operator", Pull: true, Reconcile: true},
			},
			"unmanaged": {
				"aws-cloud-controller-manager": {Repository: "https://kubernetes.github.io/cloud-provider-aws", Version: "0.0.8", Namespace: "kube-system", Pull: true, Reconcile: true},
				"aws-ebs-csi-driver":           {Repository: "https://kubernetes-sigs.github.io/aws-ebs-csi-driver", Version: "2.31.0", Namespace: "kube-system", Pull: false, Reconcile: false},
				"cluster-autoscaler":           {Repository: "https://kubernetes.github.io/autoscaler", Version: "9.37.0", Namespace: "kube-system", Pull: false, Reconcile: false},
				"tigera-operator":              {Repository: "https://docs.projectcalico.org/charts", Version: "v3.28.0", Namespace: "tigera-operator", Pull: true, Reconcile: true},
			},
		},
	},
}

func (b *AWSBuilder) pullProviderCharts(n nodes.Node, clusterConfigSpec *commons.ClusterConfigSpec, keosSpec commons.KeosSpec, clusterCredentials commons.ClusterCredentials, clusterType string) error {
	if clusterConfigSpec.EKSLBController && clusterType == "managed" {
		for name, chart := range awsCharts.Charts[majorVersion][clusterType] {
			if name == "aws-load-balancer-controller" {
				chart.Pull = true
				awsCharts.Charts[majorVersion][clusterType][name] = chart
			}
		}
	}
	return pullGenericCharts(n, clusterConfigSpec, keosSpec, clusterCredentials, awsCharts, clusterType)

}

func (b *AWSBuilder) getProviderCharts(clusterConfigSpec *commons.ClusterConfigSpec, keosSpec commons.KeosSpec, clusterType string) map[string]commons.ChartEntry {
	return getGenericCharts(clusterConfigSpec, keosSpec, awsCharts, clusterType)
}

func (b *AWSBuilder) getOverriddenCharts(charts *[]commons.Chart, clusterConfigSpec *commons.ClusterConfigSpec, clusterType string) []commons.Chart {
	providerCharts := ConvertToChart(awsCharts.Charts[majorVersion][clusterType])
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

func (b *AWSBuilder) getProvider() Provider {
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

func (b *AWSBuilder) installCloudProvider(n nodes.Node, k string, privateParams PrivateParams) error {
	var podsCidrBlock string
	keosCluster := privateParams.KeosCluster
	if keosCluster.Spec.Networks.PodsCidrBlock != "" {
		podsCidrBlock = keosCluster.Spec.Networks.PodsCidrBlock
	} else {
		podsCidrBlock = "192.168.0.0/16"
	}

	cloudControllerManagerValuesFile := "/kind/aws-cloud-controller-manager-helm-values.yaml"
	cloudControllerManagerHelmParams := cloudControllerHelmParams{
		ClusterName: privateParams.KeosCluster.Metadata.Name,
		Private:     privateParams.Private,
		KeosRegUrl:  privateParams.KeosRegUrl,
		PodsCidr:    podsCidrBlock,
	}

	// Generate the CCM helm values
	cloudControllerManagerHelmValues, err := getManifest(b.capxProvider, "aws-cloud-controller-manager-helm-values.tmpl", majorVersion, cloudControllerManagerHelmParams)
	if err != nil {
		return errors.Wrap(err, "failed to create cloud controller manager Helm chart values file")
	}
	c := "echo '" + cloudControllerManagerHelmValues + "' > " + cloudControllerManagerValuesFile
	_, err = commons.ExecuteCommand(n, c, 5, 3)
	if err != nil {
		return errors.Wrap(err, "failed to create cloud controller manager Helm chart values file")
	}

	c = "helm install aws-cloud-controller-manager /stratio/helm/aws-cloud-controller-manager" +
		" --kubeconfig " + k +
		" --namespace kube-system" +
		" --values " + cloudControllerManagerValuesFile
	_, err = commons.ExecuteCommand(n, c, 5, 3)
	if err != nil {
		return errors.Wrap(err, "failed to deploy aws-cloud-controller-manager Helm Chart")
	}

	return nil
}

func (b *AWSBuilder) installCSI(n nodes.Node, k string, privateParams PrivateParams, providerParams ProviderParams, chartsList map[string]commons.ChartEntry) error {
	csiName := "aws-ebs-csi-driver"
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

	c := "echo '" + csiHelmValues + "' > " + csiValuesFile
	_, err := commons.ExecuteCommand(n, c, 5, 3)
	if err != nil {
		return errors.Wrap(err, "failed to create "+csiName+" Helm chart values file")
	}
	if err := configureHelmRelease(n, kubeconfigPath, "flux2_helmrelease.tmpl", csiHelmReleaseParams, privateParams.KeosCluster.Spec.HelmRepository); err != nil {
		return err
	}
	return nil
}

func installLBController(n nodes.Node, k string, privateParams PrivateParams, p ProviderParams, chartsList map[string]commons.ChartEntry) error {
	lbControllerName := "aws-load-balancer-controller"
	lbControllerValuesFile := "/kind/" + lbControllerName + "-helm-values.yaml"
	lbControllerEntry := chartsList[lbControllerName]
	clusterName := p.ClusterName
	roleName := clusterName + "-lb-controller-manager"
	accountID := p.Credentials["AccountID"]

	lbControllerManagerHelmParams := lbControllerHelmParams{
		ClusterName: privateParams.KeosCluster.Metadata.Name,
		Private:     privateParams.Private,
		KeosRegUrl:  privateParams.KeosRegUrl,
		AccountID:   accountID,
		RoleName:    roleName,
	}

	lbControllerHelmReleaseParams := fluxHelmReleaseParams{
		ChartRepoRef:   "keos",
		ChartName:      lbControllerName,
		ChartNamespace: lbControllerEntry.Namespace,
		ChartVersion:   lbControllerEntry.Version,
	}
	if !privateParams.HelmPrivate {
		lbControllerHelmReleaseParams.ChartRepoRef = lbControllerName
	}
	// Generate the aws lb controller helm values
	lbControllerHelmValues, getManifestErr := getManifest(privateParams.KeosCluster.Spec.InfraProvider, lbControllerName+"-helm-values.tmpl", majorVersion, lbControllerManagerHelmParams)
	if getManifestErr != nil {
		return errors.Wrap(getManifestErr, "failed to generate "+lbControllerName+"-csi helm values")
	}
	c := "echo '" + lbControllerHelmValues + "' > " + lbControllerValuesFile
	_, err := commons.ExecuteCommand(n, c, 5, 3)
	if err != nil {
		return errors.Wrap(err, "failed to create "+lbControllerName+" Helm chart values file")
	}
	if err := configureHelmRelease(n, kubeconfigPath, "flux2_helmrelease.tmpl", lbControllerHelmReleaseParams, privateParams.KeosCluster.Spec.HelmRepository); err != nil {
		return err
	}
	return nil
}

func createCloudFormationStack(n nodes.Node, envVars []string) error {
	var c string
	var err error

	eksConfigData := `
apiVersion: bootstrap.aws.infrastructure.cluster.x-k8s.io/v1beta1
kind: AWSIAMConfiguration
spec:
  bootstrapUser:
    enable: false
  eks:
    enable: true
    iamRoleCreation: false
    defaultControlPlaneRole:
        disable: false
  controlPlane:
    enableCSIPolicy: true
  nodes:
    extraPolicyAttachments:
    - arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy`

	// Create the eks.config file in the container
	eksConfigPath := "/kind/eks.config"
	c = "echo \"" + eksConfigData + "\" > " + eksConfigPath

	_, err = commons.ExecuteCommand(n, c, 5, 3)
	if err != nil {
		return errors.Wrap(err, "failed to create eks.config")
	}

	// Run clusterawsadm with the eks.config file previously created (this will create or update the CloudFormation stack in AWS)
	c = "clusterawsadm bootstrap iam create-cloudformation-stack --config " + eksConfigPath

	_, err = commons.ExecuteCommand(n, c, 5, 3, envVars)
	if err != nil {
		return errors.Wrap(err, "failed to run clusterawsadm")
	}
	return nil
}

func (b *AWSBuilder) internalNginx(p ProviderParams, networks commons.Networks) (bool, error) {
	var err error
	var ctx = context.TODO()

	cfg, err := commons.AWSGetConfig(ctx, p.Credentials, p.Region)
	if err != nil {
		return false, err
	}
	svc := ec2.NewFromConfig(cfg)
	if len(networks.Subnets) > 0 {
		for _, s := range networks.Subnets {
			isPrivate, err := commons.AWSIsPrivateSubnet(ctx, svc, &s.SubnetId)
			if err != nil {
				return false, err
			}
			if !isPrivate {
				return false, nil
			}
		}
		return true, nil
	}
	return false, nil
}

func (b *AWSBuilder) getRegistryCredentials(p ProviderParams, u string) (string, string, error) {
	var registryUser = "AWS"
	var registryPass string
	var ctx = context.Background()

	region := strings.Split(u, ".")[3]
	cfg, err := commons.AWSGetConfig(ctx, p.Credentials, region)
	if err != nil {
		return "", "", err
	}
	svc := ecr.NewFromConfig(cfg)
	token, err := svc.GetAuthorizationToken(ctx, &ecr.GetAuthorizationTokenInput{})
	if err != nil {
		return "", "", err
	}
	authData := token.AuthorizationData[0].AuthorizationToken
	data, err := base64.StdEncoding.DecodeString(*authData)
	if err != nil {
		return "", "", err
	}
	registryPass = strings.SplitN(string(data), ":", 2)[1]
	return registryUser, registryPass, nil
}

func (b *AWSBuilder) configureStorageClass(n nodes.Node, k string) error {
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

	if b.scParameters.Labels != "" {
		var tags string
		re := regexp.MustCompile(`\s*labels: (.*,?)`)
		labels := re.FindStringSubmatch(storageClass)[1]
		for i, label := range strings.Split(labels, ",") {
			tags += "\n    tagSpecification_" + strconv.Itoa(i+1) + ": \"" + strings.TrimSpace(label) + "\""
		}
		storageClass = re.ReplaceAllString(storageClass, tags)
	}

	cmd = n.Command("kubectl", "--kubeconfig", k, "apply", "-f", "-")
	if err = cmd.SetStdin(strings.NewReader(storageClass)).Run(); err != nil {
		return errors.Wrap(err, "failed to create default storage class")
	}

	return nil
}

func (b *AWSBuilder) getOverrideVars(p ProviderParams, networks commons.Networks, clusterConfigSpec commons.ClusterConfigSpec) (map[string][]byte, error) {
	var overrideVars = make(map[string][]byte)

	// Add override vars internal nginx
	requiredInternalNginx, err := b.internalNginx(p, networks)
	if err != nil {
		return nil, err
	}
	if requiredInternalNginx {
		overrideVars = addOverrideVar("ingress-nginx.yaml", awsInternalIngress, overrideVars)
	} else if !requiredInternalNginx && p.Managed && clusterConfigSpec.EKSLBController {
		overrideVars = addOverrideVar("ingress-nginx.yaml", awsPublicIngress, overrideVars)
	}
	// Add override vars for storage class
	if commons.Contains([]string{"io1", "io2"}, b.scParameters.Type) {
		overrideVars = addOverrideVar("storage-class.yaml", []byte("storage_class_pvc_size: 4Gi"), overrideVars)
	}
	if commons.Contains([]string{"st1", "sc1"}, b.scParameters.Type) {
		overrideVars = addOverrideVar("storage-class.yaml", []byte("storage_class_pvc_size: 125Gi"), overrideVars)
	}
	return overrideVars, nil
}

func (b *AWSBuilder) postInstallPhase(n nodes.Node, k string) error {
	var coreDNSPDBName = "coredns"

	c := "kubectl --kubeconfig " + kubeconfigPath + " get pdb " + coreDNSPDBName + " -n kube-system"

	_, err := commons.ExecuteCommand(n, c, 5, 3)
	if err != nil {
		err = installCorednsPdb(n)
		if err != nil {
			return errors.Wrap(err, "failed to add core dns PDB")
		}
	}
	if b.capxManaged {
		err := patchDeploy(n, k, "kube-system", "coredns", "{\"spec\": {\"template\": {\"metadata\": {\"annotations\": {\""+postInstallAnnotation+"\": \"tmp\"}}}}}")
		if err != nil {
			return errors.Wrap(err, "failed to add podAnnotation to coredns")
		}

		err = patchDeploy(n, k, "kube-system", "ebs-csi-controller", "{\"spec\": {\"template\": {\"metadata\": {\"annotations\": {\""+postInstallAnnotation+"\": \"socket-dir\"}}}}}")
		if err != nil {
			return errors.Wrap(err, "failed to add podAnnotation to ebs-csi-controller")
		}
	}

	return nil
}
