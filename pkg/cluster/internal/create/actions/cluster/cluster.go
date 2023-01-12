package cluster

import (
	"embed"
	"fmt"
	"os"
	"text/template"

	"gopkg.in/yaml.v3"
)

//go:embed templates/cluster-template-eks.tmpl
var ctel embed.FS

// DescriptorFile represents the YAML structure in the cluster.yaml file
type DescriptorFile struct {
	ClusterID string `yaml:"cluster_id"`

	Credentials struct {
		AccessKey  string `yaml:"access_key"`
		Account    string `yaml:"account"`
		Region     string `yaml:"region"`
		SecretKey  string `yaml:"secret"`
		AssumeRole string `yaml:"assume_role"`
	} `yaml:"credentials"`

	InfraProvider string `yaml:"infra_provider"`

	Keos struct {
		Domain         string `yaml:"domain"`
		ExternalDomain string `yaml:"external_domain"`
		Flavour        string `yaml:"flavour"`
		Version        string `yaml:"version"`
	} `yaml:"keos"`

	K8SVersion   string  `yaml:"k8s_version"`
	Region       string  `yaml:"region"`
	SSHKey       string  `default:"juan" yaml:"ssh_key"`
	FullyPrivate bool    `yaml:"fully_private"`
	Bastion      Bastion `yaml:"bastion"`

	Networks struct {
		VPCID   string `yaml:"vpc_id"`
		subnets []struct {
			AvailabilityZone string `yaml:"availability_zone"`
			Name             string `yaml:"name"`
			PrivateCIDR      string `yaml:"private_cidr"`
			PublicCIDR       string `yaml:"public_cidr"`
		} `yaml:"subnets"`
	}

	ExternalRegistry struct {
		AuthRequired bool   `yaml: auth_required`
		Type         string `yaml: type`
		URL          string `yaml: url`
	} `yaml:"external_registry"`

	ControlPlane struct {
		Managed         bool   `yaml:"managed"`
		Name            string `yaml:"name"`
		AmiID           string `yaml:"ami_id"`
		HighlyAvailable bool   `yaml:"highly_available"`
		Size            string `yaml:"size"`
		Image           string `yaml:"image"`
	} `yaml:"control_plane"`

	WorkerNodes []struct {
		Name             string `yaml:"name"`
		AmiID            string `yaml:"ami_id"`
		Quantity         int    `yaml:"quantity"`
		Size             string `yaml:"size"`
		Image            string `yaml:"image"`
		ZoneDistribution string `yaml:"zone_distribution"`
		AZ               string `yaml:"az"`
		SSHKey           string `yaml:"ssh_key"`
		Spot             bool   `yaml:"spot"`

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
			Spot     bool   `yaml:"spot"`
		} `yaml:"kube_node"`
	} `yaml:"worker_nodes"`
}

// Bastion represents the bastion VM
type Bastion struct {
	AmiID             string   `yaml:"ami_id"`
	VMSize            string   `yaml:"vm_size"`
	AllowedCIDRBlocks []string `yaml:"allowedCIDRBlocks"`
}

type Eks struct {
	ClusterName string
	Region      string
}

// Read cluster.yaml file
func GetClusterDescriptor() DescriptorFile {
	descriptorRAW, err := os.ReadFile("./cluster.yaml")
	if err != nil {
		panic(err)
	}
	var descriptorFile DescriptorFile
	yaml.Unmarshal(descriptorRAW, &descriptorFile)
	return descriptorFile
}

func GetClusterConfig() error {

	d := GetClusterDescriptor()
	fmt.Print("Descriptor:", d)
	tmpl, err := template.ParseFS(ctel, "templates/cluster-template-eks.tmpl")
	if err != nil {
		return err
	}
	err2 := tmpl.Execute(os.Stdout, d)
	if err2 != nil {
		return err2
	}
	return nil
}
