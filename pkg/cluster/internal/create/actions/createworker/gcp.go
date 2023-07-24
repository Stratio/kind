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
	"io/ioutil"
	"net/url"
	"strings"

	"google.golang.org/api/compute/v1"
	"google.golang.org/api/option"
	"gopkg.in/yaml.v3"
	"sigs.k8s.io/kind/pkg/cluster/nodes"
	"sigs.k8s.io/kind/pkg/commons"
	"sigs.k8s.io/kind/pkg/errors"
	"sigs.k8s.io/kind/pkg/exec"
)

//go:embed files/gcp-compute-persistent-disk-csi-driver.yaml
var csiManifest string

type GCPBuilder struct {
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
	dataCreds        map[string]interface{}
	region           string
}

func newGCPBuilder() *GCPBuilder {
	return &GCPBuilder{}
}

func (b *GCPBuilder) setCapx(managed bool) {
	b.capxProvider = "gcp"
	b.capxVersion = "v1.3.1"
	b.capxImageVersion = "v1.3.1"
	b.capxName = "capg"

	if managed {
		b.capxManaged = true
		b.capxTemplate = "gcp.gke.tmpl"
		b.csiNamespace = ""
	} else {
		b.capxManaged = false
		b.capxTemplate = "gcp.tmpl"
		b.csiNamespace = ""
	}
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
	b.dataCreds = data
	b.region = p.Region
	jsonData, _ := json.Marshal(data)
	b.capxEnvVars = []string{
		"GCP_B64ENCODED_CREDENTIALS=" + b64.StdEncoding.EncodeToString([]byte(jsonData)),
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
		if p.StorageClass.Class == "premium" {
			b.scParameters.Type = "pd-ssd"
		} else {
			b.scParameters.Type = "pd-standard"
		}
	}

	if p.StorageClass.EncryptionKey != "" {
		b.scParameters.DiskEncryptionKmsKey = p.StorageClass.EncryptionKey
	}
}

func (b *GCPBuilder) getProvider() Provider {
	return Provider{
		capxProvider:     b.capxProvider,
		capxVersion:      b.capxVersion,
		capxImageVersion: b.capxImageVersion,
		capxName:         b.capxName,
		capxTemplate:     b.capxTemplate,
		capxEnvVars:      b.capxEnvVars,
		scParameters:     b.scParameters,
		scProvisioner:    b.scProvisioner,
		csiNamespace:     b.csiNamespace,
	}
}

func (b *GCPBuilder) installCSI(n nodes.Node, k string) error {
	var c string
	var err error
	var cmd exec.Cmd

	// Create CSI secret in CSI namespace
	secret, _ := b64.StdEncoding.DecodeString(strings.Split(b.capxEnvVars[0], "GCP_B64ENCODED_CREDENTIALS=")[1])
	c = "kubectl --kubeconfig " + k + " -n kube-system create secret generic cloud-sa --from-literal=cloud-sa.json='" + string(secret) + "'"
	_, err = commons.ExecuteCommand(n, c)
	if err != nil {
		return errors.Wrap(err, "failed to create CSI secret in CSI namespace")
	}

	// Deploy CSI driver
	cmd = n.Command("kubectl", "--kubeconfig", k, "apply", "-f", "-")
	if err = cmd.SetStdin(strings.NewReader(csiManifest)).Run(); err != nil {
		return errors.Wrap(err, "failed to deploy CSI driver")
	}

	return nil
}

func (b *GCPBuilder) getAzs(networks commons.Networks) ([]string, error) {
	if len(b.dataCreds) == 0 {
		return nil, errors.New("Insufficient credentials.")
	}

	ctx := context.Background()
	jsonDataCreds, _ := json.Marshal(b.dataCreds)
	creds := option.WithCredentialsJSON(jsonDataCreds)
	computeService, err := compute.NewService(ctx, creds)
	if err != nil {
		return nil, err
	}

	project := b.dataCreds["project_id"]
	if project_id, ok := project.(string); ok {
		zones, err := computeService.Zones.List(project_id).Filter("name=" + b.region + "*").Do()
		if err != nil {
			return nil, err
		}
		if len(zones.Items) < 3 {
			return nil, errors.New("Insufficient Availability Zones in this region. Must have at least 3")
		}
		azs := make([]string, 3)
		for i, zone := range zones.Items {
			if i == 3 {
				break
			}
			azs[i] = zone.Name
		}

		return azs, nil
	}

	return nil, errors.New("Error in project id")
}

func (b *GCPBuilder) configureStorageClass(n nodes.Node, k string) error {
	var c string
	var err error
	var cmd exec.Cmd

	if b.capxManaged {
		// Remove annotation from default storage class
		c = "kubectl --kubeconfig " + k + " get sc | grep '(default)' | awk '{print $1}'"
		output, err := commons.ExecuteCommand(n, c)
		if err != nil {
			return errors.Wrap(err, "failed to get default storage class")
		}
		if strings.TrimSpace(output) != "" && strings.TrimSpace(output) != "No resources found" {
			c = "kubectl --kubeconfig " + k + " annotate sc " + strings.TrimSpace(output) + " " + defaultScAnnotation + "-"
			_, err = commons.ExecuteCommand(n, c)
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

func (b *GCPBuilder) internalNginx(networks commons.Networks, credentialsMap map[string]string, ClusterID string) (bool, error) {
	if len(b.dataCreds) == 0 {
		return false, errors.New("Insufficient credentials.")
	}

	ctx := context.Background()
	jsonDataCreds, _ := json.Marshal(b.dataCreds)
	creds := option.WithCredentialsJSON(jsonDataCreds)
	computeService, err := compute.NewService(ctx, creds)
	if err != nil {
		return false, err
	}

	project := b.dataCreds["project_id"].(string)
	region := b.region

	if networks.Subnets != nil {
		for _, subnet := range networks.Subnets {
			publicSubnetID, _ := GCPFilterPublicSubnet(computeService, project, region, subnet.SubnetId)
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

func (b *GCPBuilder) getOverrideVars(keosCluster commons.KeosCluster, credentialsMap map[string]string) (map[string][]byte, error) {
	overrideVars := map[string][]byte{}
	InternalNginxOVPath, InternalNginxOVValue, err := b.getInternalNginxOverrideVars(keosCluster.Spec.Networks, credentialsMap, keosCluster.Metadata.Name)
	if err != nil {
		return nil, err
	}
	overrideVars = addOverrideVar(InternalNginxOVPath, InternalNginxOVValue, overrideVars)

	return overrideVars, nil
}

func (b *GCPBuilder) getInternalNginxOverrideVars(networks commons.Networks, credentialsMap map[string]string, ClusterID string) (string, []byte, error) {
	requiredInternalNginx, err := b.internalNginx(networks, credentialsMap, ClusterID)
	if err != nil {
		return "", nil, err
	}

	if requiredInternalNginx {
		internalIngressFilePath := "files/" + b.capxProvider + "/internal-ingress-nginx.yaml"
		internalIngressFile, err := internalIngressFiles.Open(internalIngressFilePath)
		if err != nil {
			return "", nil, errors.Wrap(err, "error opening the internal ingress nginx file")
		}
		defer internalIngressFile.Close()

		internalIngressContent, err := ioutil.ReadAll(internalIngressFile)
		if err != nil {
			return "", nil, errors.Wrap(err, "error reading the internal ingress nginx file")
		}
		return "ingress-nginx.yaml", internalIngressContent, nil
	}
	return "", []byte(""), nil
}
