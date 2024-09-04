package createworker

import (
	"context"
	_ "embed"
	"fmt"
	"time"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/clientcmd"
	"sigs.k8s.io/kind/pkg/cluster/nodes"
	"sigs.k8s.io/kind/pkg/commons"
	"sigs.k8s.io/kind/pkg/errors"
)

var (
	crossplane_folder_path              = "/kind/cache"
	crossplane_directoy_path            = "/kind/crossplane"
	crossplane_provider_creds_file_base = "/kind/crossplane/crossplane-"
	crossplane_provider_config_file     = "/kind/crossplane/crossplane-provider-creds.yaml"
	crossplane_custom_creds_file        = "/kind/crossplane/crossplane-custom-creds.yaml"
	crossplane_crs_file_local_base      = "/kind/crossplane/crossplane-crs-local"
	crossplane_crs_file_workload_base   = "/kind/crossplane/crossplane-crs-workload"
)

type CrossplaneProviderParams struct {
	Provider string
	Package  string
	Image    string
	Private  bool
	Version  string
}

type CrossplaneProviderConfigParams struct {
	Addon  string
	Secret string
}

func configureCrossPlaneProviders(n nodes.Node, kubeconfigpath string, keosRegUrl string, privateRegistry bool, infraProvider string, addons []string) error {
	providers, version := infra.GetCrossplaneProviders(addons)
	for _, provider := range providers {
		providerFile := "/kind/" + provider + ".yaml"

		params := CrossplaneProviderParams{
			Provider: provider,
			Package:  provider,
			Image:    keosRegUrl + "/upbound/" + provider + ":" + version,
			Private:  privateRegistry,
			Version:  version,
		}
		providerManifest, err := getManifest(infraProvider, "crossplane-provider.tmpl", params)
		if err != nil {
			return errors.Wrap(err, "failed to generate provider manifest "+provider)
		}
		c := "echo '" + providerManifest + "' > " + providerFile
		_, err = commons.ExecuteCommand(n, c, 3, 5)
		if err != nil {
			return errors.Wrap(err, "failed to create provider manifest "+provider+" file")
		}

		c = "kubectl create -f " + providerFile
		if kubeconfigpath != "" {
			c += " --kubeconfig " + kubeconfigpath
		}
		_, err = commons.ExecuteCommand(n, c, 3, 5)
		if err != nil {
			return errors.Wrap(err, "failed to create provider "+provider)
		}

		c = "kubectl wait providers.pkg.crossplane.io/" + provider + " --for=condition=healthy=False --timeout=3m"
		if kubeconfigpath != "" {
			c += " --kubeconfig " + kubeconfigpath
		}
		_, err = commons.ExecuteCommand(n, c, 3, 5)
		if err != nil {
			return errors.Wrap(err, "failed to wait provider "+provider)
		}

		//TODO: Check if this is necessary
		if privateRegistry {
			time.Sleep(40 * time.Second)

			c = "kubectl patch deploy -n crossplane-system " + provider + " -p '{\"spec\": {\"template\": {\"spec\":{\"containers\":[{\"name\":\"package-runtime\",\"imagePullPolicy\":\"IfNotPresent\"}]}}}}'"
			if kubeconfigpath != "" {
				c += " --kubeconfig " + kubeconfigpath
			}
			_, err = commons.ExecuteCommand(n, c, 3, 5)
			if err != nil {
				return errors.Wrap(err, "failed to patch deployment provider "+provider)
			}
		}

	}
	return nil
}

func installCrossplane(n nodes.Node, kubeconfigpath string, keosRegUrl string, credentials map[string]*map[string]string, infra *Infra, privateParams PrivateParams, workloadClusterInstallation bool, allowAllEgressNetPolPath string, customParams *map[string]string, addons []string) (commons.KeosCluster, error) {

	kubeconfigString := ""
	// addons := []string{"external-dns"}

	c := "mkdir -p " + crossplane_directoy_path + " && chmod -R 0755 " + crossplane_directoy_path
	_, err := commons.ExecuteCommand(n, c, 3, 5)
	if err != nil {
		return privateParams.KeosCluster, errors.Wrap(err, "failed to create crossplane directory")
	}

	c = "kubectl create ns crossplane-system"
	if kubeconfigpath != "" {
		c += " --kubeconfig " + kubeconfigpath
	}
	_, err = commons.ExecuteCommand(n, c, 3, 5)
	if err != nil {
		return privateParams.KeosCluster, errors.Wrap(err, "failed to create ns crossplane-system")
	}

	if workloadClusterInstallation {
		// Allow egress in CAPX's Namespace
		c = "kubectl --kubeconfig " + kubeconfigPath + " -n crossplane-system apply -f " + allowAllEgressNetPolPath
		_, err = commons.ExecuteCommand(n, c, 3, 5)
		if err != nil {
			return privateParams.KeosCluster, errors.Wrap(err, "failed to apply CAPX's NetworkPolicy in workload cluster")
		}
	}

	c = "helm install crossplane /stratio/helm/crossplane" +
		" --namespace crossplane-system"

	if privateParams.Private {
		c += " --set image.repository=" + keosRegUrl + "/crossplane/crossplane"
	}

	if kubeconfigpath != "" {
		c += " --kubeconfig " + kubeconfigpath +
			" --set replicas=2" +
			" --set rbacManager.replicas=2"
	}

	_, err = commons.ExecuteCommand(n, c, 3, 5)
	if err != nil {
		return privateParams.KeosCluster, errors.Wrap(err, "failed to deploy crossplane Helm Chart")
	}

	c = "kubectl -n crossplane-system rollout status deploy crossplane --timeout=3m"
	if kubeconfigpath != "" {
		c += " --kubeconfig " + kubeconfigpath
	}
	_, err = commons.ExecuteCommand(n, c, 3, 5)
	if err != nil {
		return privateParams.KeosCluster, errors.Wrap(err, "failed to wait for the crossplane deployment")
	}

	c = "kubectl -n crossplane-system rollout status deploy crossplane-rbac-manager --timeout=3m"
	if kubeconfigpath != "" {
		c += " --kubeconfig " + kubeconfigpath
	}
	_, err = commons.ExecuteCommand(n, c, 3, 5)
	if err != nil {
		return privateParams.KeosCluster, errors.Wrap(err, "failed to wait for the crossplane-rbac-manager deployment")
	}

	// Crea los providers de Crossplane
	err = configureCrossPlaneProviders(n, kubeconfigpath, keosRegUrl, privateParams.Private, privateParams.KeosCluster.Spec.InfraProvider, addons)
	if err != nil {
		return privateParams.KeosCluster, errors.Wrap(err, "failed to configure Crossplane Provider")
	}
	if kubeconfigpath != "" {

		c = "cat " + kubeconfigPath
		kubeconfigString, err = commons.ExecuteCommand(n, c, 3, 5)
		if err != nil {
			return privateParams.KeosCluster, errors.Wrap(err, "failed to get kubeconfig")
		}
	}

	keosCluster := privateParams.KeosCluster
	for _, addon := range addons {
		credsAddonContent, credentialsFound, err := infra.getCrossplaneProviderConfigContent(credentials, addon, keosCluster.Metadata.Name, kubeconfigString) // EN LOCAL SIEMPRE FALSE SI ENTRAMOS AQUI
		if err != nil {
			return privateParams.KeosCluster, errors.Wrap(err, "failed to get Crossplane Provider config content")
		}

		c = "echo '" + credsAddonContent + "' > " + crossplane_provider_creds_file_base + addon + "-provider-creds.txt"
		_, err = commons.ExecuteCommand(n, c, 3, 5)
		if err != nil {
			return privateParams.KeosCluster, errors.Wrap(err, "failed to create Crossplane Provider config file")
		}

		params := CrossplaneProviderConfigParams{
			Addon:  addon + "-provider",
			Secret: infra.builder.getProvider().capxProvider + "-" + addon + "-secret",
		}

		if !credentialsFound {
			params.Secret = infra.builder.getProvider().capxProvider + "-crossplane-secret"
			config, err := clientcmd.RESTConfigFromKubeConfig([]byte(kubeconfigString))
			if err != nil {
				return privateParams.KeosCluster, errors.Wrap(err, "failed to get workload kubeconfig")
			}
			clientset, err := kubernetes.NewForConfig(config)
			if err != nil {
				return privateParams.KeosCluster, errors.Wrap(err, "failed to create clientset for workload kubeconfig")
			}
			_, err = clientset.CoreV1().Secrets("crossplane-system").Get(context.TODO(), infra.builder.getProvider().capxProvider+"-crossplane-secret", metav1.GetOptions{})
			if err != nil {
				if apierrors.IsNotFound(err) {
					c = "kubectl create secret generic " + infra.builder.getProvider().capxProvider + "-crossplane-secret -n crossplane-system --from-file=creds=" + crossplane_provider_creds_file_base + addon + "-provider-creds.txt"
					if kubeconfigpath != "" {
						c += " --kubeconfig " + kubeconfigpath
					}
					_, err = commons.ExecuteCommand(n, c, 3, 5)
					if err != nil {
						return privateParams.KeosCluster, errors.Wrap(err, "failed to create Crossplane Provider config secret: "+infra.builder.getProvider().capxProvider+"-secret")
					}
				} else {
					return privateParams.KeosCluster, errors.Wrap(err, "failed to get secret "+infra.builder.getProvider().capxProvider+"-crossplane-secret")
				}
			}
		} else {
			c = "kubectl create secret generic " + infra.builder.getProvider().capxProvider + "-" + addon + "-secret -n crossplane-system --from-file=creds=" + crossplane_provider_creds_file_base + addon + "-provider-creds.txt"
			if kubeconfigpath != "" {
				c += " --kubeconfig " + kubeconfigpath
			}
			_, err = commons.ExecuteCommand(n, c, 3, 5)
			if err != nil {
				return privateParams.KeosCluster, errors.Wrap(err, "failed to create Crossplane Provider config secret: "+infra.builder.getProvider().capxProvider+"-secret")
			}
		}

		providerConfigManifest, err := getManifest("aws", "crossplane-provider-config.tmpl", params)
		if err != nil {
			return privateParams.KeosCluster, errors.Wrap(err, "failed to generate provider config manifest ")
		}

		c = "echo '" + providerConfigManifest + "' > " + crossplane_provider_config_file
		_, err = commons.ExecuteCommand(n, c, 3, 5)
		if err != nil {
			return privateParams.KeosCluster, errors.Wrap(err, "failed to create provider config manifest file")
		}

		c = "kubectl create -f " + crossplane_provider_config_file
		if kubeconfigpath != "" {
			c += " --kubeconfig " + kubeconfigpath
		}
		_, err = commons.ExecuteCommand(n, c, 3, 5)
		if err != nil {
			return privateParams.KeosCluster, errors.Wrap(err, "failed to create provider config ")
		}

		keosCluster, err = createCrossplaneCustomResources(n, kubeconfigpath, *credentials["provisioner"], infra, privateParams, workloadClusterInstallation, credentialsFound, addon)
		if err != nil {
			return privateParams.KeosCluster, err
		}
	}

	return keosCluster, nil

}

func createCrossplaneCustomResources(n nodes.Node, kubeconfigpath string, credentials map[string]string, infra *Infra, privateParams PrivateParams, workloadClusterInstallation bool, credentialsFound bool, addon string) (commons.KeosCluster, error) {
	crossplaneCRManifests, compositionsToWait, err := infra.getCrossplaneCRManifests(privateParams.KeosCluster, credentials, workloadClusterInstallation, credentialsFound, addon)
	if err != nil {
		return privateParams.KeosCluster, err
	}
	for i, manifest := range crossplaneCRManifests {
		crossplane_crs_file := crossplane_crs_file_local_base + fmt.Sprintf("-%d.yaml", i)
		if workloadClusterInstallation {
			crossplane_crs_file = crossplane_crs_file_workload_base + fmt.Sprintf("-%d.yaml", i)
		}

		c := "echo '" + manifest + "' > " + crossplane_crs_file
		_, err = commons.ExecuteCommand(n, c, 3, 5)
		if err != nil {
			return privateParams.KeosCluster, errors.Wrap(err, "failed to create crossplane crs file")
		}

		c = "kubectl create -f " + crossplane_crs_file
		if kubeconfigpath != "" {
			c += " --kubeconfig " + kubeconfigpath
		}
		_, err = commons.ExecuteCommand(n, c, 3, 5)
		if err != nil {
			return privateParams.KeosCluster, errors.Wrap(err, "failed to create crossplane crs ")
		}

	}

	for kind, name := range compositionsToWait {
		c := "kubectl wait " + kind + "/" + name + " --for=condition=ready --timeout=10m"
		if kubeconfigpath != "" {
			c += " --kubeconfig " + kubeconfigpath
		}
		_, err = commons.ExecuteCommand(n, c, 3, 5)
		if err != nil {
			return privateParams.KeosCluster, errors.Wrap(err, "failed to wait for the composition "+name)
		}
	}

	return privateParams.KeosCluster, nil
}
