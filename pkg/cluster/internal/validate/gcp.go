/*
Copyright 2019 The Kubernetes Authors.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package validate

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"reflect"
	"regexp"
	"strconv"
	"strings"

	b64 "encoding/base64"

	"google.golang.org/api/compute/v1"
	"google.golang.org/api/option"
	"sigs.k8s.io/kind/pkg/commons"
	"sigs.k8s.io/kind/pkg/errors"
)

var GCPVolumes = []string{"pd-balanced", "pd-ssd", "pd-standard", "pd-extreme"}
var isGCPNodeImage = regexp.MustCompile(`^projects/[\w-]+/global/images/[\w-]+$`).MatchString
var GCPNodeImageFormat = "projects/[PROJECT_ID]/global/images/[IMAGE_NAME]"

// Regex for private CIDR Control Plane and Master Authorized Networks
var GCPCPPrivatePattern = `^(10\.\d{1,3}\.\d{1,3}\.\d{1,3}\/28)$|^(172\.(1[6-9]|2[0-9]|3[0-1])\.\d{1,3}\.\d{1,3}\/28)$|^(192\.168\.\d{1,3}\.\d{1,3}\/28)$`
var GCPPrivatePattern = `^(10\.\d{1,3}\.\d{1,3}\.\d{1,3}\/(3[0-2]|[12]\d|[0-9]))$|^(172\.(1[6-9]|2[0-9]|3[0-1])\.\d{1,3}\.\d{1,3}\/(3[0-2]|[12]\d|[0-9]))$|^(192\.168\.\d{1,3}\.\d{1,3}\/(3[0-2]|[12]\d|[0-9]))$`
var GCPMANPattern = `^(25[0-5]|2[0-4]\d|[01]?\d?\d)(\.(25[0-5]|2[0-4]\d|[01]?\d?\d)){3}\/(3[0-2]|[12]\d|[0-9])$`
var GCPControlPlaneCidrBlock = regexp.MustCompile(GCPCPPrivatePattern).MatchString
var GCPPrivateCIDRBlock = regexp.MustCompile(GCPPrivatePattern).MatchString
var GCPMANCIDRBlock = regexp.MustCompile(GCPMANPattern).MatchString
var GCPReleaseChannels = []string{"unspecified", "extended"}

// Regex for security scopes
var GCPScopesPattern = `^https:\/\/www\.googleapis\.com\/auth\/.*$`
var GCPScopes = regexp.MustCompile(GCPScopesPattern).MatchString

// Regex for GCP encryption key
var isKeyValid = regexp.MustCompile(`^projects/[a-zA-Z0-9-]+/locations/[a-zA-Z0-9-]+/keyRings/[a-zA-Z0-9-]+/cryptoKeys/[a-zA-Z0-9-]+$`).MatchString

func GCPPublicCIDRBlock(cidr string) bool {
	return !GCPPrivateCIDRBlock(cidr) && GCPMANCIDRBlock(cidr)
}

func validateGCP(spec commons.KeosSpec, providerSecrets map[string]string) error {
	var err error

	credentialsJson := getGCPCreds(providerSecrets)

	regions, err := getGCPRegions(credentialsJson)
	if err != nil {
		return err
	}
	if !commons.Contains(regions, spec.Region) {
		return errors.New("spec.region: " + spec.Region + " region does not exist")
	}

	azs, err := getGoogleAZs(credentialsJson, spec.Region)
	if err != nil {
		return err
	}
	if (spec.StorageClass != commons.StorageClass{}) {
		if err = validateGCPStorageClass(spec); err != nil {
			return errors.Wrap(err, "spec.storageclass: Invalid value")
		}
	}

	if !reflect.ValueOf(spec.Networks).IsZero() {
		if err = validateGCPNetwork(spec.Networks, credentialsJson, spec.Region); err != nil {
			return errors.Wrap(err, "spec.networks: Invalid value")
		}
	}

	for i, dr := range spec.DockerRegistries {
		if dr.Type != "gar" && dr.Type != "gcr" && spec.ControlPlane.Managed {
			return errors.New("spec.docker_registries[" + strconv.Itoa(i) + "]: Invalid value: \"type\": only 'gar' and 'gcr' are supported in gcp managed clusters")
		}
		if dr.Type != "gar" && dr.Type != "gcr" && dr.Type != "generic" {
			return errors.New("spec.docker_registries[" + strconv.Itoa(i) + "]: Invalid value: \"type\": only 'gar', 'gcr' and 'generic' are supported in gcp unmanaged clusters")
		}
	}

	if !spec.ControlPlane.Managed {
		if spec.ControlPlane.NodeImage == "" || !isGCPNodeImage(spec.ControlPlane.NodeImage) {
			return errors.New("spec.control_plane: Invalid value: \"node_image\": is required and have the format " + GCPNodeImageFormat)
		}
		if err := validateGCPInstanceType(spec.ControlPlane.Size, credentialsJson, spec.Region, azs, ""); err != nil {
			return errors.New("spec.control_plane.size: " + spec.ControlPlane.Size + " does not exist as a GCP instance types in region " + spec.Region)
		}
		if err := validateVolumeType(spec.ControlPlane.RootVolume.Type, GCPVolumes); err != nil {
			return errors.Wrap(err, "spec.control_plane.root_volume: Invalid value: \"type\"")
		}
		if err := validateVolumeType(spec.ControlPlane.CRIVolume.Type, GCPVolumes); err != nil {
			return errors.Wrap(err, "spec.control_plane.cri_volume: Invalid value: \"type\"")
		}
		if err := validateVolumeType(spec.ControlPlane.ETCDVolume.Type, GCPVolumes); err != nil {
			return errors.Wrap(err, "spec.control_plane.etcd_volume: Invalid value: \"type\"")
		}

		for i, ev := range spec.ControlPlane.ExtraVolumes {
			if err := validateVolumeType(ev.Type, GCPVolumes); err != nil {
				return errors.Wrap(err, "spec.control_plane.extra_volumes["+strconv.Itoa(i)+"]: Invalid value: \"type\"")
			}
		}
		for _, wn := range spec.WorkerNodes {
			if wn.NodeImage == "" || !isGCPNodeImage(wn.NodeImage) {
				return errors.New("spec.worker_nodes." + wn.Name + ": \"node_image\": is required and have the format " + GCPNodeImageFormat)
			}
			if err := validateVolumeType(wn.RootVolume.Type, GCPVolumes); err != nil {
				return errors.Wrap(err, "spec.worker_nodes."+wn.Name+".root_volume: Invalid value: \"type\"")
			}
			if err := validateVolumeType(wn.CRIVolume.Type, GCPVolumes); err != nil {
				return errors.Wrap(err, "spec.worker_nodes."+wn.Name+".cri_volume: Invalid value: \"type\"")
			}
			for i, ev := range wn.ExtraVolumes {
				if err := validateVolumeType(ev.Type, GCPVolumes); err != nil {
					return errors.Wrap(err, "spec.worker_nodes."+wn.Name+".extra_volumes["+strconv.Itoa(i)+"]: Invalid value: \"type\"")
				}
			}
		}
	}

	if spec.ControlPlane.Managed {
		// Check scopes regex
		for _, scope := range spec.Security.GCP.Scopes {
			if !GCPScopes(scope) {
				return errors.New("spec.security.gcp.scopes: 'Invalid scope: " + scope + " must begin with " + GCPScopesPattern)
			}
		}

		// Check RelesesChannel
		if !commons.Contains(GCPReleaseChannels, spec.ControlPlane.Gcp.ReleaseChannel) {
			return errors.New("spec.control_plane.gcp.release_channel: Invalid value: " + spec.ControlPlane.Gcp.ReleaseChannel + " supported values are " + fmt.Sprint(strings.Join(GCPReleaseChannels, ", ")))
		}

		// Cluster Network validation
		if spec.ControlPlane.Gcp.ClusterNetwork != nil {
			if spec.ControlPlane.Gcp.ClusterNetwork.PrivateCluster != nil {
				// If enablePrivateEndpoint is true, then ControlPlaneCidrBlock is required
				if spec.ControlPlane.Gcp.ClusterNetwork.PrivateCluster.EnablePrivateEndpoint != nil && *spec.ControlPlane.Gcp.ClusterNetwork.PrivateCluster.EnablePrivateEndpoint && spec.ControlPlane.Gcp.ClusterNetwork.PrivateCluster.ControlPlaneCidrBlock == "" {
					return errors.New("ControlPlaneCidrBlock is required when EnablePrivateEndpoint is true")
				}
			} else {
				// If privateCluster is enabled, then privateCluster is required
				return errors.New("spec.control_plane.gcp.cluster_network.private_cluster: is required")
			}
			if spec.ControlPlane.Gcp.ClusterNetwork.PrivateCluster.ControlPlaneCidrBlock != "" {
				// Validate the format with regex
				if !GCPControlPlaneCidrBlock(spec.ControlPlane.Gcp.ClusterNetwork.PrivateCluster.ControlPlaneCidrBlock) {
					return errors.New("ControlPlaneCidrBlock invalid format.\nIt must be a Private CIDR with format: " + GCPCPPrivatePattern)
				}
			}
			// Check if GCPPublicCIDRsAccessEnabled is enabled when private endpoint is enabled
			if *spec.ControlPlane.Gcp.ClusterNetwork.PrivateCluster.EnablePrivateEndpoint && *spec.ControlPlane.Gcp.MasterAuthorizedNetworksConfig.GCPPublicCIDRsAccessEnabled {
				return errors.New("Invalid value for 'master_authorized_networks_config': 'master_authorized_networks_config.gcp_public_cidrs_access_enabled' cannot be enabled if private endpoint is enabled")
			}
		} else {
			// if clusterNetwork is enabled, then clusterNetwork is required
			return errors.New("spec.control_plane.gcp.cluster_network: is required")
		}

		// MasterAuthorizedNetworksConfig validation
		if spec.ControlPlane.Gcp.MasterAuthorizedNetworksConfig == nil || spec.ControlPlane.Gcp.MasterAuthorizedNetworksConfig.GCPPublicCIDRsAccessEnabled == nil {
			return errors.New("If master_authorized_networks_config is provided, configuration must be set")
		}

		if spec.ControlPlane.Gcp.MasterAuthorizedNetworksConfig != nil && spec.ControlPlane.Gcp.MasterAuthorizedNetworksConfig.CIDRBlocks != nil {
			for _, block := range spec.ControlPlane.Gcp.MasterAuthorizedNetworksConfig.CIDRBlocks {
				//Validate block.CIDRBlock is not empty or nil
				if block.CIDRBlock == "" {
					return errors.New("CIDRBlock is required")
				}
				// Validate the format with regex
				if !GCPMANCIDRBlock(block.CIDRBlock) && !GCPPrivateCIDRBlock(block.CIDRBlock) {
					return errors.New("CIDRBlock invalid format.\nIt must be a CIDR with format " + GCPMANPattern + " or " + GCPPrivatePattern)
				}
				// Check if GCPPublicCIDRsAccessEnabled is false and public endpoint is disabled (EnablePrivateEndpoint is true) and CIDR block is public
				if !*spec.ControlPlane.Gcp.MasterAuthorizedNetworksConfig.GCPPublicCIDRsAccessEnabled && *spec.ControlPlane.Gcp.ClusterNetwork.PrivateCluster.EnablePrivateEndpoint {
					if GCPPublicCIDRBlock(block.CIDRBlock) {
						return errors.New("Invalid master authorized networks: network " + block.CIDRBlock + " is not a reserved network, which is required for private endpoints.\nCheck if enable_private_endpoint is true (default) and gcp_public_cidrs_access_enabled is false (that could be the reason)")
					}
				}
			}
		}

		// Monitoring Config validation
		// If monitoringConfig is enabled, then monitoringConfig is required
		if spec.ControlPlane.Gcp.MonitoringConfig == nil {
			// Errror Provide monitoring config enable_managed_prometheus or do not include monitoring_config so we can set default value
			return errors.New("spec.control_plane.gcp.monitoring_config: 'If monitoring_config is provided, enable_managed_prometheus must be set'")
		}

		// Logging Config validation
		// If loggingConfig is enabled, then loggingConfig is required
		if spec.ControlPlane.Gcp.LoggingConfig == nil {
			// Errror Provide logging config enable_managed_logging or do not include logging_config so we can set default value
			return errors.New("spec.control_plane.gcp.logging_config: 'If logging_config is provided, at least system_components or workloads must be set'")
		} else {
			if spec.ControlPlane.Gcp.LoggingConfig.SystemComponents == nil {
				// Error system_components is required
				return errors.New("spec.control_plane.gcp.logging_config.system_components: 'if system_components is provided, it must be set'")
			}
			if spec.ControlPlane.Gcp.LoggingConfig.Workloads == nil {
				// Error workloads is required
				return errors.New("spec.control_plane.gcp.logging_config.workloads: 'if workloads is provided, it must be set'")
			}
		}

		// ipAllocationPolicy validation
		ipPolicy := spec.ControlPlane.Gcp.IPAllocationPolicy
		if ipPolicy == (commons.IPAllocationPolicy{}) {
			// ip policy fields are empty return nil
			return nil
		} else {
			if (ipPolicy.ClusterSecondaryRangeName != "" && ipPolicy.ServicesSecondaryRangeName != "") &&
				(ipPolicy.ClusterIpv4CidrBlock != "" || ipPolicy.ServicesIpv4CidrBlock != "") {
				return errors.New("spec.control_plane.gcp.ip_allocation_policy: 'if cluster_secondary_range_name and services_secondary_range_name are provided, cluster_ipv4_cidr_block and services_ipv4_cidr_block must not be set'")
			}
			if (ipPolicy.ClusterIpv4CidrBlock != "" && ipPolicy.ServicesIpv4CidrBlock != "") &&
				(ipPolicy.ClusterSecondaryRangeName != "" || ipPolicy.ServicesSecondaryRangeName != "") {
				return errors.New("spec.control_plane.gcp.ip_allocation_policy: 'if cluster_ipv4_cidr_block and services_ipv4_cidr_block are provided, cluster_secondary_range_name and services_secondary_range_name must not be set'")
			}
			if (ipPolicy.ClusterSecondaryRangeName == "" || ipPolicy.ServicesSecondaryRangeName == "") &&
				(ipPolicy.ClusterIpv4CidrBlock == "" || ipPolicy.ServicesIpv4CidrBlock == "") {
				return errors.New("spec.control_plane.gcp.ip_allocation_policy: 'either cluster_secondary_range_name and services_secondary_range_name or cluster_ipv4_cidr_block and services_ipv4_cidr_block must be set'")
			}
		}

		// CMEK Config validation
		// Validate encryptionKey for managed clusters root volume
		for _, wn := range spec.WorkerNodes {
			if wn.RootVolume.Encrypted {
				if wn.RootVolume.EncryptionKey == "" {
					return errors.New("spec.control_plane.root_volume: \"encryption_key\": is required when \"encrypted\" is set to true")
				}
				if !isKeyValid(wn.RootVolume.EncryptionKey) {
					return errors.New("spec.control_plane.root_volume: \"encryption_key\": it must have the format projects/[PROJECT_ID]/locations/[REGION]/keyRings/[RING_NAME]/cryptoKeys/[KEY_NAME]")
				}
			}
		}
		// Validate encryptionKey for managed clusters root volume
		isKeyValid := regexp.MustCompile(`^projects/[a-zA-Z0-9-]+/locations/[a-zA-Z0-9-]+/keyRings/[a-zA-Z0-9-]+/cryptoKeys/[a-zA-Z0-9-]+$`).MatchString
		for _, wn := range spec.WorkerNodes {
			if wn.RootVolume.Encrypted {
				if wn.RootVolume.EncryptionKey == "" {
					return errors.New("spec.control_plane.root_volume: \"encryption_key\": is required when \"encrypted\" is set to true")
				}
				if !isKeyValid(wn.RootVolume.EncryptionKey) {
					return errors.New("spec.control_plane.root_volume: \"encryption_key\": it must have the format projects/[PROJECT_ID]/locations/[REGION]/keyRings/[RING_NAME]/cryptoKeys/[KEY_NAME]")
				}
			}
		}
	}

	for _, wn := range spec.WorkerNodes {
		if wn.AZ != "" {
			if len(azs) > 0 {
				if !commons.Contains(azs, wn.AZ) {
					return errors.New(wn.AZ + " does not exist in this region, azs: " + fmt.Sprint(azs))
				}
			}
		}
		if wn.Size != "" {
			if err := validateGCPInstanceType(wn.Size, credentialsJson, spec.Region, azs, wn.AZ); err != nil {
				return errors.New("spec.worker_nodes." + wn.Name + ".size: " + wn.Size + " does not exist as a GCP instance types in region " + spec.Region)
			}
		}
	}

	return nil
}

func validateGCPInstanceType(instanceType string, credentialsJson string, region string, azs []string, azWorker string) error {
	var iterateAZs []string
	var ctx = context.Background()

	gcpCreds := map[string]string{}
	err := json.Unmarshal([]byte(credentialsJson), &gcpCreds)
	if err != nil {
		return err
	}

	cfg := option.WithCredentialsJSON([]byte(credentialsJson))
	computeService, err := compute.NewService(ctx, cfg)
	if err != nil {
		return err
	}

	if azWorker != "" {
		iterateAZs = append(iterateAZs, azWorker)
	} else {
		iterateAZs = azs
	}

	instanceTypeListCall := computeService.MachineTypes.AggregatedList(string(gcpCreds["project_id"])).Filter("(zone eq " + region + ".*) (name eq " + instanceType + ")")
	instanceTypes, err := instanceTypeListCall.Do()
	if err != nil {
		return err
	}

	for _, iterateAZ := range iterateAZs {
		// Check if instance type exists
		for zonesAZ, machineTypesScopedList := range instanceTypes.Items {
			az := strings.Split(zonesAZ, "/")[1]
			if iterateAZ == az && len(machineTypesScopedList.MachineTypes) == 0 {
				return errors.New("nonexistent instance type: " + instanceType + " in region " + region)
			}
		}
	}

	return nil

}

func validateGCPStorageClass(spec commons.KeosSpec) error {
	var err error
	var sc = spec.StorageClass
	var GCPFSTypes = []string{"xfs", "ext3", "ext4", "ext2"}
	var GCPSCFields = []string{"Type", "FsType", "Labels", "DiskEncryptionKmsKey", "ProvisionedIopsOnCreate", "ProvisionedThroughputOnCreate", "ReplicationType"}
	var GCPYamlFields = []string{"type", "fsType", "labels", "disk-encryption-kms-key", "provisioned-iops-on-create", "provisioned-throughput-on-create", "replication-type"}

	// Validate fields
	fields := getFieldNames(sc.Parameters)
	for _, f := range fields {
		if !commons.Contains(GCPSCFields, f) {
			return errors.New("\"parameters\": unsupported " + f + ", supported fields: " + fmt.Sprint(strings.Join(GCPYamlFields, ", ")))
		}
	}
	// Validate class
	if sc.Class != "" && sc.Parameters != (commons.SCParameters{}) {
		return errors.New("\"class\": cannot be set when \"parameters\" is set")
	}
	// Validate type
	if sc.Parameters.Type != "" && !commons.Contains(GCPVolumes, sc.Parameters.Type) {
		return errors.New("\"type\": unsupported " + sc.Parameters.Type + ", supported types: " + fmt.Sprint(strings.Join(GCPVolumes, ", ")))
	}
	// Validate encryptionKey format
	if sc.EncryptionKey != "" {
		if sc.Parameters != (commons.SCParameters{}) {
			return errors.New("\"encryptionKey\": cannot be set when \"parameters\" is set")
		}
		if !isKeyValid(sc.EncryptionKey) {
			return errors.New("\"encryptionKey\": it must have the format projects/[PROJECT_ID]/locations/[REGION]/keyRings/[RING_NAME]/cryptoKeys/[KEY_NAME]")
		}
	}
	// Validate disk-encryption-kms-key format
	if sc.Parameters.DiskEncryptionKmsKey != "" {
		if !isKeyValid(sc.Parameters.DiskEncryptionKmsKey) {
			return errors.New("\"disk-encryption-kms-key\": it must have the format projects/[PROJECT_ID]/locations/[REGION]/keyRings/[RING_NAME]/cryptoKeys/[KEY_NAME]")
		}
	}
	// Validate fsType
	if sc.Parameters.FsType != "" && !commons.Contains(GCPFSTypes, sc.Parameters.FsType) {
		return errors.New("\fsType\": unsupported " + sc.Parameters.FsType + ", supported types: " + fmt.Sprint(strings.Join(GCPFSTypes, ", ")))
	}

	if spec.ControlPlane.Managed {
		version, _ := strconv.ParseFloat(regexp.MustCompile(".[0-9]+$").Split(strings.ReplaceAll(spec.K8SVersion, "v", ""), -1)[0], 64)
		if sc.Parameters.Type == "pd-extreme" && version < 1.26 {
			return errors.New("\"pd-extreme\": is only supported in GKE 1.26 or later")
		}
	}
	// Validate provisioned-iops-on-create
	if sc.Parameters.ProvisionedIopsOnCreate != "" {
		if sc.Parameters.Type != "pd-extreme" {
			return errors.New("\"provisioned-iops-on-create\": is only supported for pd-extreme type")
		}
		if _, err = strconv.Atoi(sc.Parameters.ProvisionedIopsOnCreate); err != nil {
			return errors.New("\"provisioned-iops-on-create\": must be an integer")
		}
	}
	// Validate replication-type
	if sc.Parameters.ReplicationType != "" && !regexp.MustCompile(`^(none|regional-pd)$`).MatchString(sc.Parameters.ReplicationType) {
		return errors.New("\"replication-type\": supported values are 'none' or 'regional-pd'")
	}
	// Validate labels
	if sc.Parameters.Labels != "" {
		if err = validateGCPLabel(sc.Parameters.Labels); err != nil {
			return errors.Wrap(err, "invalid labels")
		}
	}
	return nil
}

func validateGCPLabel(l string) error {
	// Keys must start with a lowercase character and contain only hyphens (-), underscores (_), lowercase characters, and numbers.
	var isLabel = regexp.MustCompile(`^([a-z][a-z\d_-]*=[a-z\d_-]+)(\s?,\s?[a-z][a-z\d_-]*=[a-z\d_-]+)*$`).MatchString
	if !isLabel(l) {
		return errors.New("incorrect format. Must have the format 'key1=value1,key2=value2'")
	}
	return nil
}

func validateGCPNetwork(network commons.Networks, credentialsJson string, region string) error {
	if network.VPCID != "" {
		vpcs, _ := getGoogleVPCs(credentialsJson)
		if len(vpcs) > 0 && !commons.Contains(vpcs, network.VPCID) {
			return errors.New("\"vpc_id\": " + network.VPCID + " does not exist")
		}
		if len(network.Subnets) != 1 {
			return errors.New("\"subnet\": when \"vpc_id\" is set, one subnet must be specified")
		}
		if network.Subnets[0].SubnetId == "" {
			return errors.New("\"subnet_id\": required")
		}
		subnets, _ := getGoogleSubnets(credentialsJson, region, network.VPCID)
		if !commons.Contains(subnets, network.Subnets[0].SubnetId) {
			return errors.New("\"subnets\": " + network.Subnets[0].SubnetId + " does not belong to vpc with id: " + network.VPCID)
		}
	} else {
		if len(network.Subnets) > 0 {
			return errors.New("\"vpc_id\": is required when \"subnets\" is set")
		}
	}
	if len(network.Subnets) > 0 {
		if len(network.Subnets) > 1 {
			return errors.New("\"subnet\": only one subnet is supported")
		}
		if network.Subnets[0].SubnetId == "" {
			return errors.New("\"subnet_id\": required")
		}
	}
	if network.VPCCIDRBlock != "" {
		return errors.New("\"vpc_cidr\": is not supported")
	}

	return nil
}

func getGCPRegions(credentialsJson string) ([]string, error) {
	var regions_names []string
	var ctx = context.Background()

	gcpCreds := map[string]string{}
	err := json.Unmarshal([]byte(credentialsJson), &gcpCreds)
	if err != nil {
		return []string{}, err
	}

	cfg := option.WithCredentialsJSON([]byte(credentialsJson))
	computeService, err := compute.NewService(ctx, cfg)

	if err != nil {
		return []string{}, err
	}

	regions, err := computeService.Regions.List(string(gcpCreds["project_id"])).Do()
	if err != nil {
		return []string{}, err
	}

	for _, region := range regions.Items {
		if !commons.Contains(regions_names, region.Name) {
			regions_names = append(regions_names, region.Name)
		}
	}

	return regions_names, nil

}

func getGoogleVPCs(credentialsJson string) ([]string, error) {
	var network_names []string
	var ctx = context.Background()

	gcpCreds := map[string]string{}
	err := json.Unmarshal([]byte(credentialsJson), &gcpCreds)
	if err != nil {
		return []string{}, err
	}

	cfg := option.WithCredentialsJSON([]byte(credentialsJson))
	computeService, err := compute.NewService(ctx, cfg)

	if err != nil {
		return []string{}, err
	}

	networks, err := computeService.Networks.List(string(gcpCreds["project_id"])).Do()
	if err != nil {
		return []string{}, err
	}

	for _, network := range networks.Items {
		network_names = append(network_names, network.Name)
	}

	return network_names, nil

}

func getGoogleSubnets(credentialsJson string, region string, vpcId string) ([]string, error) {
	var subnetwork_names []string
	var ctx = context.Background()

	gcpCreds := map[string]string{}
	err := json.Unmarshal([]byte(credentialsJson), &gcpCreds)
	if err != nil {
		return []string{}, err
	}

	cfg := option.WithCredentialsJSON([]byte(credentialsJson))
	computeService, err := compute.NewService(ctx, cfg)

	if err != nil {
		return []string{}, err
	}

	subnetworks, err := computeService.Subnetworks.List(string(gcpCreds["project_id"]), region).Do()
	if err != nil {
		return []string{}, err
	}

	for _, subnetwork := range subnetworks.Items {
		networkParts := strings.Split(subnetwork.Network, "/")
		networkId := networkParts[len(networkParts)-1]
		if networkId == vpcId {
			subnetwork_names = append(subnetwork_names, subnetwork.Name)
		}
	}

	return subnetwork_names, nil

}

func getGoogleAZs(credentialsJson string, region string) ([]string, error) {
	var zones_names []string
	var ctx = context.Background()

	gcpCreds := map[string]string{}
	err := json.Unmarshal([]byte(credentialsJson), &gcpCreds)
	if err != nil {
		return []string{}, err
	}

	cfg := option.WithCredentialsJSON([]byte(credentialsJson))
	computeService, err := compute.NewService(ctx, cfg)

	if err != nil {
		return []string{}, err
	}

	zones, err := computeService.Zones.List(string(gcpCreds["project_id"])).Filter("name=" + region + "*").Do()
	if err != nil {
		return []string{}, err
	}

	for _, zone := range zones.Items {
		zones_names = append(zones_names, zone.Name)
	}

	return zones_names, nil
}

func getGCPCreds(providerSecrets map[string]string) string {
	data := map[string]interface{}{
		"type":                        "service_account",
		"project_id":                  providerSecrets["ProjectID"],
		"private_key_id":              providerSecrets["PrivateKeyID"],
		"private_key":                 providerSecrets["PrivateKey"],
		"client_email":                providerSecrets["ClientEmail"],
		"client_id":                   providerSecrets["ClientID"],
		"auth_uri":                    "https://accounts.google.com/o/oauth2/auth",
		"token_uri":                   "https://accounts.google.com/o/oauth2/token",
		"auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
		"client_x509_cert_url":        "https://www.googleapis.com/robot/v1/metadata/x509/" + url.QueryEscape(providerSecrets["ClientEmail"]),
	}
	jsonData, _ := json.Marshal(data)
	credentials := b64.StdEncoding.EncodeToString([]byte(jsonData))
	credentialsJson, _ := b64.StdEncoding.DecodeString(credentials)
	return string(credentialsJson)
}
