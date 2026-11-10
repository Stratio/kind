package createworker

import (
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
	"sigs.k8s.io/kind/pkg/commons"
)

func TestClusterAutoscalerValuesPerMinor(t *testing.T) {
	const reg = "123456789012.dkr.ecr.eu-west-1.amazonaws.com"
	eks := func() commons.KeosCluster {
		var k commons.KeosCluster
		k.Metadata.Name = "c1"
		k.Spec.InfraProvider = "aws"
		k.Spec.ControlPlane.Managed = true
		k.Spec.Region = "eu-west-1"
		return k
	}
	azure := func() commons.KeosCluster {
		var k commons.KeosCluster
		k.Metadata.Name = "c1"
		k.Spec.InfraProvider = "azure"
		return k
	}
	tests := []struct {
		minor, name string
		params      PrivateParams
		wantRepo    string
		wantTag     string
		wantCloud   string
	}{
		{"32", "EKS public", PrivateParams{KeosCluster: eks(), KeosRegUrl: reg}, "registry.k8s.io/autoscaling/cluster-autoscaler", "v1.32.7", "aws"},
		{"33", "EKS public", PrivateParams{KeosCluster: eks(), KeosRegUrl: reg}, "registry.k8s.io/autoscaling/cluster-autoscaler", "v1.33.6", "aws"},
		{"34", "EKS public", PrivateParams{KeosCluster: eks(), KeosRegUrl: reg}, "registry.k8s.io/autoscaling/cluster-autoscaler", "v1.34.5", "aws"},
		{"34", "EKS private", PrivateParams{KeosCluster: eks(), KeosRegUrl: reg, Private: true}, reg + "/autoscaling/cluster-autoscaler", "v1.34.5", "aws"},
		{"34", "EKS private, ECR pull-through", PrivateParams{KeosCluster: eks(), KeosRegUrl: reg, Private: true, CentralECR: true}, reg + "/k8s/autoscaling/cluster-autoscaler", "v1.34.5", "aws"},
		{"32", "Azure public", PrivateParams{KeosCluster: azure(), KeosRegUrl: "acr.example.io"}, "registry.k8s.io/autoscaling/cluster-autoscaler", "v1.32.7", "clusterapi"},
		{"34", "Azure private", PrivateParams{KeosCluster: azure(), KeosRegUrl: "acr.example.io", Private: true}, "acr.example.io/autoscaling/cluster-autoscaler", "v1.34.5", "clusterapi"},
		{"35", "EKS public", PrivateParams{KeosCluster: eks(), KeosRegUrl: reg}, "registry.k8s.io/autoscaling/cluster-autoscaler", "v1.35.3", "aws"},
		{"35", "EKS private, ECR pull-through", PrivateParams{KeosCluster: eks(), KeosRegUrl: reg, Private: true, CentralECR: true}, reg + "/autoscaling/cluster-autoscaler", "v1.35.3", "aws"},
		{"35", "Azure private", PrivateParams{KeosCluster: azure(), KeosRegUrl: "acr.example.io", Private: true}, "acr.example.io/autoscaling/cluster-autoscaler", "v1.35.3", "clusterapi"},
		{"35", "Azure public", PrivateParams{KeosCluster: azure(), KeosRegUrl: "acr.example.io"}, "registry.k8s.io/autoscaling/cluster-autoscaler", "v1.35.3", "clusterapi"},
	}
	for _, tt := range tests {
		t.Run(tt.minor+" "+tt.name, func(t *testing.T) {
			out, err := getManifest("common", "cluster-autoscaler-helm-values.tmpl", tt.minor, tt.params)
			if err != nil {
				t.Fatalf("render: %v", err)
			}
			if strings.Contains(out, "'") {
				t.Fatalf("rendered values contain a single quote, which breaks the echo '...' that writes them to the node:\n%s", out)
			}
			var v struct {
				CloudProvider string `yaml:"cloudProvider"`
				Image         struct {
					Repository string `yaml:"repository"`
					Tag        string `yaml:"tag"`
				} `yaml:"image"`
			}
			if err := yaml.Unmarshal([]byte(out), &v); err != nil {
				t.Fatalf("rendered values are not valid YAML: %v\n%s", err, out)
			}
			if v.Image.Repository != tt.wantRepo || v.Image.Tag != tt.wantTag {
				t.Errorf("image = %s:%s, want %s:%s", v.Image.Repository, v.Image.Tag, tt.wantRepo, tt.wantTag)
			}
			if v.CloudProvider != tt.wantCloud {
				t.Errorf("cloudProvider = %q, want %q", v.CloudProvider, tt.wantCloud)
			}
		})
	}
}
