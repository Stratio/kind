package createworker

import (
	"reflect"
	"testing"

	"gopkg.in/yaml.v3"
)

func TestEKSIAMConfig(t *testing.T) {
	const ebs = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
	const extra = "arn:aws:iam::123456789012:policy/extra"
	const ca = "arn:aws:iam::123456789012:policy/" + clusterAutoscalerPolicyName

	tests := []struct {
		name        string
		extraARN    string
		caARN       string
		wantNodegrp []string
		wantNodes   []string
	}{
		{name: "no extra, no CA policy", wantNodegrp: []string{ebs}, wantNodes: []string{ebs}},
		{name: "CA policy on both roles", caARN: ca, wantNodegrp: []string{ebs, ca}, wantNodes: []string{ebs, ca}},
		{name: "extra ARN only on nodegroup", extraARN: extra, wantNodegrp: []string{ebs, extra}, wantNodes: []string{ebs}},
		{name: "extra ARN and CA policy", extraARN: extra, caARN: ca, wantNodegrp: []string{ebs, extra, ca}, wantNodes: []string{ebs, ca}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var cfg struct {
				Kind string `yaml:"kind"`
				Spec struct {
					EKS struct {
						ManagedMachinePool struct {
							ExtraPolicyAttachments []string `yaml:"extraPolicyAttachments"`
						} `yaml:"managedMachinePool"`
					} `yaml:"eks"`
					Nodes struct {
						ExtraPolicyAttachments []string         `yaml:"extraPolicyAttachments"`
						ExtraStatements        []map[string]any `yaml:"extraStatements"`
					} `yaml:"nodes"`
				} `yaml:"spec"`
			}
			if err := yaml.Unmarshal([]byte(eksIAMConfig(tt.extraARN, tt.caARN)), &cfg); err != nil {
				t.Fatalf("config is not valid YAML: %v", err)
			}
			if cfg.Kind != "AWSIAMConfiguration" {
				t.Errorf("kind = %q", cfg.Kind)
			}
			if got := cfg.Spec.EKS.ManagedMachinePool.ExtraPolicyAttachments; !reflect.DeepEqual(got, tt.wantNodegrp) {
				t.Errorf("managedMachinePool.extraPolicyAttachments = %v, want %v", got, tt.wantNodegrp)
			}
			if got := cfg.Spec.Nodes.ExtraPolicyAttachments; !reflect.DeepEqual(got, tt.wantNodes) {
				t.Errorf("nodes.extraPolicyAttachments = %v, want %v", got, tt.wantNodes)
			}
			if len(cfg.Spec.Nodes.ExtraStatements) != 1 {
				t.Errorf("nodes.extraStatements = %v, want the ECR statement", cfg.Spec.Nodes.ExtraStatements)
			}
		})
	}
}
