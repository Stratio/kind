package validation

import (
	"context"
	"encoding/json"
	"errors"
	"regexp"
	"strings"

	"github.com/Azure/azure-sdk-for-go/sdk/azidentity"
	"github.com/Azure/azure-sdk-for-go/sdk/resourcemanager/containerservice/armcontainerservice/v3"
	"golang.org/x/exp/slices"
	"sigs.k8s.io/kind/pkg/commons"
)

var azureInstance *AzureValidator

type AzureValidator struct {
	commonValidator
	managed bool
}

func newAzureValidator(managed bool) *AzureValidator {
	if azureInstance == nil {
		azureInstance = new(AzureValidator)
	}
	azureInstance.managed = managed
	return azureInstance
}

func (v *AzureValidator) DescriptorFile(descriptorFile commons.DescriptorFile) {
	v.descriptor = descriptorFile
}

func (v *AzureValidator) SecretsFile(secrets commons.SecretsFile) {
	v.secrets = secrets
}

func (v *AzureValidator) Validate(fileType string) error {
	switch fileType {
	case "descriptor":
		err := descriptorAzureValidations((*v).descriptor, (*v).secrets, (*v).managed)
		if err != nil {
			return err
		}
	case "secrets":
		err := secretsAzureValidations((*v).secrets)
		if err != nil {
			return err
		}
	default:
		return errors.New("incorrect filetype validation")
	}
	return nil
}

func (v *AzureValidator) CommonsValidations() error {
	err := commonsValidations((*v).descriptor, (*v).secrets)
	if err != nil {
		return err
	}
	return nil
}

func descriptorAzureValidations(descriptorFile commons.DescriptorFile, secretsFile commons.SecretsFile, managed bool) error {
	err := commonsDescriptorValidation(descriptorFile)
	if err != nil {
		return err
	}
	if managed {
		err = aksVersionValidation(descriptorFile, secretsFile)
		if err != nil {
			return err
		}
		err = aksNodesValidation(descriptorFile.WorkerNodes)
		if err != nil {
			return err
		}
	}
	return nil
}

func secretsAzureValidations(secretsFile commons.SecretsFile) error {
	err := commonsSecretsValidations(secretsFile)
	if err != nil {
		return err
	}
	return nil
}

func aksVersionValidation(descriptorFile commons.DescriptorFile, secretsFile commons.SecretsFile) error {
	var availableVersions []string
	var azureSecrets commons.AzureCredentials

	if secretsFile.Secrets.AZURE.Credentials != (commons.AzureCredentials{}) {
		azureSecrets = secretsFile.Secrets.AZURE.Credentials
	} else {
		azureSecrets = descriptorFile.Credentials.AZURE
	}

	creds, err := azidentity.NewClientSecretCredential(azureSecrets.TenantID, azureSecrets.ClientID, azureSecrets.ClientSecret, nil)
	if err != nil {
		return err
	}
	ctx := context.Background()
	clientFactory, err := armcontainerservice.NewClientFactory(azureSecrets.SubscriptionID, creds, nil)
	if err != nil {
		return err
	}
	res, err := clientFactory.NewManagedClustersClient().ListKubernetesVersions(ctx, descriptorFile.Region, nil)
	if err != nil {
		return err
	}
	for _, v := range res.KubernetesVersionListResult.Values {
		for _, p := range v.PatchVersions {
			for _, u := range p.Upgrades {
				availableVersions = append(availableVersions, *u)
			}
		}
	}
	if !slices.Contains(availableVersions, strings.ReplaceAll(descriptorFile.K8SVersion, "v", "")) {
		a, _ := json.Marshal(availableVersions)
		return errors.New("AKS only supports Kubernetes versions: " + string(a))
	}
	return nil
}

func aksNodesValidation(workerNodes commons.WorkerNodes) error {
	var IsLetter = regexp.MustCompile(`^[a-z0-9]+$`).MatchString
	for _, node := range workerNodes {
		if !IsLetter(node.Name) || len(node.Name) >= 9 {
			return errors.New("node name must be 9 characters or less & contain only lowercase alphanumeric characters")
		}
	}
	return nil
}