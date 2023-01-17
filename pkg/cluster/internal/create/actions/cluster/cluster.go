package cluster

import (
	"bytes"
	"embed"
	"os"
	"text/template"

	"gopkg.in/yaml.v3"
)

//go:embed templates/aws.eks.tmpl
var ctel embed.FS

// DescriptorFile represents the YAML structure in the cluster.yaml file
type DescriptorFile struct {
	APIVersion string `yaml:"apiVersion"`
	Kind       string `yaml:"kind"`
	ClusterID  string `yaml:"cluster_id"`

	Bastion Bastion `yaml:"bastion"`

	Credentials struct {
		AccessKey  string `yaml:"access_key"`
		Account    string `yaml:"account"`
		Region     string `yaml:"region"`
		SecretKey  string `yaml:"secret"`
		AssumeRole string `yaml:"assume_role"`
	} `yaml:"credentials"`

	InfraProvider string `yaml:"infra_provider"`

	K8SVersion   string `yaml:"k8s_version"`
	Region       string `yaml:"region"`
	SSHKey       string `yaml:"ssh_key"`
	FullyPrivate bool   `yaml:"fully_private"`

	// Networks struct {
	// 	VPCID   string `yaml:"vpc_id"`
	// 	subnets []struct {
	// 		AvailabilityZone string `yaml:"availability_zone"`
	// 		Name             string `yaml:"name"`
	// 		PrivateCIDR      string `yaml:"private_cidr"`
	// 		PublicCIDR       string `yaml:"public_cidr"`
	// 	} `yaml:"subnets"`
	// }

	ExternalRegistry struct {
		AuthRequired bool   `yaml: auth_required`
		Type         string `yaml: type`
		URL          string `yaml: url`
	} `yaml:"external_registry"`

	Keos struct {
		Domain         string `yaml:"domain"`
		ExternalDomain string `yaml:"external_domain"`
		Flavour        string `yaml:"flavour"`
		Version        string `yaml:"version"`
	} `yaml:"keos"`

	ControlPlane struct {
		Managed         bool   `yaml:"managed"`
		Name            string `yaml:"name"`
		AmiID           string `yaml:"ami_id"`
		HighlyAvailable bool   `yaml:"highly_available"`
		Size            string `yaml:"size"`
		Image           string `yaml:"image"`
	} `yaml:"control_plane"`

	WorkerNodes WorkerNodes `yaml:"worker_nodes"`
}

type WorkerNodes []struct {
	Name             string `yaml:"name"`
	AmiID            string `yaml:"ami_id"`
	Quantity         int    `yaml:"quantity"`
	Size             string `yaml:"size"`
	Image            string `yaml:"image"`
	ZoneDistribution string `default:"balanced" yaml:"zone_distribution"`
	AZ               string `yaml:"az"`
	SSHKey           string `yaml:"ssh_key"`
	Spot             bool   `yaml:"spot"`
	Disks            []struct {
		DeviceName string `yaml:"device_name"`
		Name       string `yaml:"name"`
		Path       string `yaml:"path,omitempty"`
		Size       int    `yaml:"size"`
		Type       string `yaml:"type"`
		Encrypted  bool   `yaml:"encrypted"`
		Volumes    []struct {
			Name string `yaml:"name"`
			Path string `yaml:"path"`
			Size string `yaml:"size"`
		} `yaml:"volumes,omitempty"`
	} `yaml:"disks"`
}

// Bastion represents the bastion VM
type Bastion struct {
	AmiID             string   `yaml:"ami_id"`
	VMSize            string   `yaml:"vm_size"`
	AllowedCIDRBlocks []string `yaml:"allowedCIDRBlocks"`
}

// Read cluster.yaml file
func GetClusterDescriptor() (*DescriptorFile, error) {
	descriptorRAW, err := os.ReadFile("./cluster.yaml")
	if err != nil {
		return nil, err
	}
	descriptorFile := new(DescriptorFile)
	yaml.Unmarshal(descriptorRAW, &descriptorFile)
	return descriptorFile, nil
}

func GetClusterManifest(d DescriptorFile) (string, error) {
	var tpl bytes.Buffer

	t, err := template.ParseFS(ctel, "templates/aws.eks.tmpl")
	if err != nil {
		return "", err
	}
	err = t.Execute(&tpl, d)
	if err != nil {
		return "", err
	}
	return tpl.String(), nil
}
