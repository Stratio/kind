package validate

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/iam"
	iamtypes "github.com/aws/aws-sdk-go-v2/service/iam/types"
	"sigs.k8s.io/kind/pkg/commons"
)

type fakeAttachedPolicies struct {
	pages [][]string
	err   error
	calls int
	role  string
}

func (f *fakeAttachedPolicies) ListAttachedRolePolicies(_ context.Context, in *iam.ListAttachedRolePoliciesInput, _ ...func(*iam.Options)) (*iam.ListAttachedRolePoliciesOutput, error) {
	f.calls++
	f.role = aws.ToString(in.RoleName)
	if f.err != nil {
		return nil, f.err
	}
	page := f.calls - 1
	out := &iam.ListAttachedRolePoliciesOutput{}
	for _, name := range f.pages[page] {
		out.AttachedPolicies = append(out.AttachedPolicies, iamtypes.AttachedPolicy{PolicyName: aws.String(name)})
	}
	if page < len(f.pages)-1 {
		out.IsTruncated = true
		out.Marker = aws.String("next")
	}
	return out, nil
}

func TestValidateEKSAutoscalerIAM(t *testing.T) {
	ca, ebs := commons.EKSClusterAutoscalerPolicyName, "AmazonEBSCSIDriverPolicy"
	tests := []struct {
		name      string
		mutate    func(*commons.KeosSpec)
		client    *fakeAttachedPolicies
		wantCalls int
		wantErr   string
	}{
		{name: "create_iam true: not checked", mutate: func(s *commons.KeosSpec) {}, client: &fakeAttachedPolicies{}, wantCalls: 0},
		{name: "no autoscaler: not checked", mutate: func(s *commons.KeosSpec) { s.Security.AWS.CreateIAM = false; s.DeployAutoscaler = false }, client: &fakeAttachedPolicies{}, wantCalls: 0},
		{name: "unmanaged aws: not checked", mutate: func(s *commons.KeosSpec) { s.Security.AWS.CreateIAM = false; s.ControlPlane.Managed = false }, client: &fakeAttachedPolicies{}, wantCalls: 0},
		{name: "both policies attached", mutate: func(s *commons.KeosSpec) { s.Security.AWS.CreateIAM = false }, client: &fakeAttachedPolicies{pages: [][]string{{"AmazonEKSWorkerNodePolicy", ca, ebs}}}, wantCalls: 1},
		{name: "policies across two pages", mutate: func(s *commons.KeosSpec) { s.Security.AWS.CreateIAM = false }, client: &fakeAttachedPolicies{pages: [][]string{{ca}, {ebs}}}, wantCalls: 2},
		{name: "cluster-autoscaler policy missing", mutate: func(s *commons.KeosSpec) { s.Security.AWS.CreateIAM = false }, client: &fakeAttachedPolicies{pages: [][]string{{ebs}}}, wantCalls: 1, wantErr: "these policies attached: " + ca + ";"},
		{name: "both missing", mutate: func(s *commons.KeosSpec) { s.Security.AWS.CreateIAM = false }, client: &fakeAttachedPolicies{pages: [][]string{{}}}, wantCalls: 1, wantErr: ca + ", " + ebs},
		{name: "role missing or access denied", mutate: func(s *commons.KeosSpec) { s.Security.AWS.CreateIAM = false }, client: &fakeAttachedPolicies{err: errors.New("NoSuchEntity: role not found")}, wantCalls: 1, wantErr: "NoSuchEntity"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			spec := eksSpec()
			spec.DeployAutoscaler = true
			tt.mutate(&spec)
			err := validateEKSAutoscalerIAM(context.Background(), tt.client, spec)
			if tt.client.calls != tt.wantCalls {
				t.Fatalf("ListAttachedRolePolicies calls = %d, want %d", tt.client.calls, tt.wantCalls)
			}
			if tt.wantCalls > 0 && tt.client.role != commons.EKSNodegroupRoleName {
				t.Fatalf("role = %q, want %q", tt.client.role, commons.EKSNodegroupRoleName)
			}
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
