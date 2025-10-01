package createworker

import (
	"fmt"

	"sigs.k8s.io/kind/pkg/cluster/nodes"
	"sigs.k8s.io/kind/pkg/commons"
)

func configureFluxWorkloadIdentity(n nodes.Node, kubeconfig string, spec commons.KeosSpec) error {
	if len(spec.Security.WorkloadIdentity.Subjects) == 0 || spec.Security.WorkloadIdentity.ClientID == "" {
		return nil
	}
	return applyWorkloadIdentity(n, kubeconfig, spec)
}

// Dispatcher: selecciona cloud provider y llama a su implementación
func applyWorkloadIdentity(n nodes.Node, kubeconfig string, spec commons.KeosSpec) error {
	switch spec.InfraProvider {
	case "azure":
		return applyAzureWorkloadIdentity(n, kubeconfig, spec)
	case "gcp":
		return applyGCPWorkloadIdentity(n, kubeconfig, spec)
	case "aws":
		return applyAWSWorkloadIdentity(n, kubeconfig, spec)
	default:
		return nil // Unknown provider → sin acción
	}
}

// Azure-specific: patch SA con clientID
func applyAzureWorkloadIdentity(n nodes.Node, kubeconfig string, spec commons.KeosSpec) error {
	subjects := spec.Security.WorkloadIdentity.Subjects
	clientID := spec.Security.WorkloadIdentity.ClientID

	if clientID == "" || len(subjects) == 0 {
		return nil
	}

	for _, subject := range subjects {
		patch := fmt.Sprintf(`{"metadata":{"annotations":{"azure.workload.identity/client-id":"%s"}}}`, clientID)
		cmd := fmt.Sprintf("kubectl --kubeconfig %s -n %s patch sa %s -p '%s'", kubeconfig, subject.Namespace, subject.Name, patch)

		_, err := commons.ExecuteCommand(n, cmd, 5, 3)
		if err != nil {
			return fmt.Errorf("failed to patch ServiceAccount %s/%s with Azure Workload Identity: %w", subject.Namespace, subject.Name, err)
		}
	}

	return nil
}

// GCP stub (preparado para federación OIDC)
func applyGCPWorkloadIdentity(n nodes.Node, kubeconfig string, spec commons.KeosSpec) error {
	// TODO: Implement GCP-specific workload identity binding
	return nil
}

// AWS stub (para trust-policy y annotación sa/eks.amazonaws.com/role-arn)
func applyAWSWorkloadIdentity(n nodes.Node, kubeconfig string, spec commons.KeosSpec) error {
	// TODO: Implement AWS-specific workload identity binding
	return nil
}
