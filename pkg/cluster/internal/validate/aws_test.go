package validate

import (
	"strings"
	"testing"

	"sigs.k8s.io/kind/pkg/commons"
)

func eksSpec() commons.KeosSpec {
	spec := commons.KeosSpec{}.Init()
	spec.InfraProvider = "aws"
	spec.ControlPlane.Managed = true
	spec.Security.AWS.CreateIAM = true
	spec.WorkerNodes = commons.WorkerNodes{{Name: "workers"}}
	return spec
}

func TestValidateEKSCreate(t *testing.T) {
	tests := []struct {
		name    string
		mutate  func(*commons.KeosSpec)
		wantErr string
	}{
		{name: "machinepools with create_iam", mutate: func(s *commons.KeosSpec) {}},
		{name: "node_image rejected on EKS", mutate: func(s *commons.KeosSpec) { s.WorkerNodes[0].NodeImage = "ami-0123456789abcdef0" }, wantErr: "\"node_image\""},
		{name: "deploy_autoscaler without create_iam", mutate: func(s *commons.KeosSpec) { s.Security.AWS.CreateIAM = false }, wantErr: "requires spec.security.aws.create_iam"},
		{name: "no autoscaler, no create_iam", mutate: func(s *commons.KeosSpec) {
			s.DeployAutoscaler = false
			s.Security.AWS.CreateIAM = false
		}},
		{name: "mp_role_name eks-nodegroup", mutate: func(s *commons.KeosSpec) { s.ControlPlane.AWS.MPRoleName = commons.EKSNodegroupRoleName }},
		{name: "mp_role_name other role", mutate: func(s *commons.KeosSpec) { s.ControlPlane.AWS.MPRoleName = "my-nodes-role" }, wantErr: "mp_role_name"},
		{name: "unmanaged aws is not checked", mutate: func(s *commons.KeosSpec) {
			s.ControlPlane.Managed = false
			s.Security.AWS.CreateIAM = false
			s.WorkerNodes[0].NodeImage = "ami-0123456789abcdef0"
		}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			spec := eksSpec()
			tt.mutate(&spec)
			err := validateEKSCreate(spec)
			if tt.wantErr == "" {
				if err != nil {
					t.Fatalf("unexpected error: %v", err)
				}
				return
			}
			if err == nil || !strings.Contains(err.Error(), tt.wantErr) {
				t.Fatalf("expected error containing %q, got %v", tt.wantErr, err)
			}
		})
	}
}
