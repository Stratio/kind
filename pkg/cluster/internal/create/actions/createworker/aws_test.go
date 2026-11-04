package createworker

import (
	"reflect"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

func TestEKSIAMConfig(t *testing.T) {
	const ebs = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
	const extra = "arn:aws:iam::123456789012:policy/extra"

	tests := []struct {
		name        string
		extraARN    string
		wantNodegrp []string
		wantNodes   []string
	}{
		{name: "no extra ARN", wantNodegrp: []string{ebs}, wantNodes: []string{ebs}},
		{name: "extra ARN only on nodegroup", extraARN: extra, wantNodegrp: []string{ebs, extra}, wantNodes: []string{ebs}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			config := eksIAMConfig(tt.extraARN)
			// The CA policy is attached outside the stack: a template that lists it detaches it on the next update without it
			if strings.Contains(config, clusterAutoscalerPolicyName) {
				t.Errorf("config lists the cluster-autoscaler policy:\n%s", config)
			}
			var cfg struct {
				Kind string `yaml:"kind"`
				Spec struct {
					EKS struct {
						ManagedMachinePool struct {
							Disable                bool     `yaml:"disable"`
							ExtraPolicyAttachments []string `yaml:"extraPolicyAttachments"`
						} `yaml:"managedMachinePool"`
					} `yaml:"eks"`
					Nodes struct {
						ExtraPolicyAttachments []string         `yaml:"extraPolicyAttachments"`
						ExtraStatements        []map[string]any `yaml:"extraStatements"`
					} `yaml:"nodes"`
				} `yaml:"spec"`
			}
			if err := yaml.Unmarshal([]byte(config), &cfg); err != nil {
				t.Fatalf("config is not valid YAML: %v", err)
			}
			if cfg.Kind != "AWSIAMConfiguration" {
				t.Errorf("kind = %q", cfg.Kind)
			}
			if cfg.Spec.EKS.ManagedMachinePool.Disable {
				t.Errorf("managedMachinePool.disable = true: the stack would drop the eks-nodegroup role")
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
