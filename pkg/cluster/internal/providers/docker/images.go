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

package docker

import (
	_ "embed"
	"fmt"
	"os"
	"regexp"
	"strings"
	"time"

	"sigs.k8s.io/kind/pkg/errors"
	"sigs.k8s.io/kind/pkg/exec"
	"sigs.k8s.io/kind/pkg/log"

	"sigs.k8s.io/kind/pkg/cluster/internal/create"
	"sigs.k8s.io/kind/pkg/cluster/internal/providers/common"
	"sigs.k8s.io/kind/pkg/internal/apis/config"
	"sigs.k8s.io/kind/pkg/internal/cli"
)

//go:embed stratio/Dockerfile
var stratioDockerfile []byte

// ensureNodeImages ensures that the node images used by the create
// configuration are present
func ensureNodeImages(logger log.Logger, status *cli.Status, cfg *config.Cluster, dockerRegUrl string, useLocalStratioImage bool, localStratioImageVersion string, buildStratioImage bool) error {
	// pull each required image
	for _, image := range common.RequiredNodeImages(cfg).List() {
		// prints user friendly message
		friendlyImageName, _ := sanitizeImage(image)
		if useLocalStratioImage {
			if localStratioImageVersion != "" {
				friendlyImageName = changeImageVersion(friendlyImageName, localStratioImageVersion)
			}
			status.Start(fmt.Sprintf("Using local Stratio image (%s) 🖼", friendlyImageName))
		} else if buildStratioImage {
			// build the stratio image
			status.Start(fmt.Sprintf("Building Stratio image (%s) 📸", friendlyImageName))

			dockerfileDir, err := ensureStratioImageFiles(logger)
			if err != nil {
				status.End(false)
				return err
			}

			err = buildStratioImageFromDockerfile(logger, friendlyImageName, dockerfileDir)
			if err != nil {
				status.End(false)
				return err
			}
		} else {
			if dockerRegUrl != "" {
				friendlyImageName = strings.Join([]string{dockerRegUrl, friendlyImageName}, "/stratio/")
			}
			// NOTE: In our CICD pipeline, when releasing a prerelease version, the compiled binary is simply retagged and
			// the release artifact contains the prerelease version (with hash) instead of the final release version.
			// To ensure we always reference the release image when pulling from the registry, we remove the prerelease hash.
			// If you want to test a prerelease image with a hash, use the --build-stratio-image or --use-local-stratio-image flags.
			friendlyImageName = removePrereleaseHash(friendlyImageName)
			status.Start(fmt.Sprintf("Ensuring node image (%s) 🖼", friendlyImageName))
			if _, err := pullIfNotPresent(logger, friendlyImageName, 4); err != nil {
				status.End(false)
				return err
			}
			err := tag(logger, friendlyImageName, image)
			if err != nil {
				status.End(false)
				return err
			}
		}
	}
	return nil
}

// pullIfNotPresent will pull an image if it is not present locally
// retrying up to retries times
// it returns true if it attempted to pull, and any errors from pulling
func pullIfNotPresent(logger log.Logger, image string, retries int) (pulled bool, err error) {
	// TODO(bentheelder): switch most (all) of the logging here to debug level
	// once we have configurable log levels
	// if this did not return an error, then the image exists locally
	cmd := exec.Command("docker", "inspect", "--type=image", image)
	if err := cmd.Run(); err == nil {
		logger.V(1).Infof("Image: %s present locally", image)
		return false, nil
	}
	// otherwise try to pull it
	return true, pull(logger, image, retries)
}

// ensureStratioImageFiles creates a temporary directory
// with the Dockerfile required to build the Stratio image
func ensureStratioImageFiles(logger log.Logger) (dir string, err error) {
	dir, err = os.MkdirTemp("", "stratio-")
	if err != nil {
		return "", errors.Wrapf(err, "failed to create the temp directory")
	}

	err = os.WriteFile(dir+"/Dockerfile", stratioDockerfile, 0644)
	if err != nil {
		return "", errors.Wrapf(err, "failed to create the Stratio Dockerfile")
	}
	return dir, nil
}

// buildStratioImageFromDockerfile builds the stratio image
func buildStratioImageFromDockerfile(logger log.Logger, image string, path string) error {
	logger.V(1).Infof("Building image: %s ...", image)
	capx_opts := create.Capx_opts
	cmd := exec.Command("docker", "build",
		"--build-arg", "CLUSTERCTL="+capx_opts.CAPI_Version,
		"--build-arg", "CAPA="+capx_opts.CAPA_Version,
		"--build-arg", "CAPG="+capx_opts.CAPG_Version,
		"--build-arg", "CAPZ="+capx_opts.CAPZ_Version,
		"--tag="+image, path)
	if err := cmd.Run(); err != nil {
		return errors.Wrapf(err, "failed to build image %q", image)
	}
	return nil
}

// tag tags an image
func tag(logger log.Logger, image string, tag string) error {
	logger.V(1).Infof("Tagging image %s to %s ...", image, tag)
	cmd := exec.Command("docker", "tag", image, tag)
	if err := cmd.Run(); err != nil {
		return errors.Wrapf(err, "failed to tag image %s to %s", image, tag)
	}
	return nil
}


// pull pulls an image, retrying up to retries times
func pull(logger log.Logger, image string, retries int) error {
	logger.V(1).Infof("Pulling image: %s ...", image)
	err := exec.Command("docker", "pull", image).Run()
	// retry pulling up to retries times if necessary
	if err != nil {
		for i := 0; i < retries; i++ {
			time.Sleep(time.Second * time.Duration(i+1))
			logger.V(1).Infof("Trying again to pull image: %q ... %v", image, err)
			// TODO(bentheelder): add some backoff / sleep?
			err = exec.Command("docker", "pull", image).Run()
			if err == nil {
				break
			}
		}
	}
	return errors.Wrapf(err, "failed to pull image %q", image)
}

// sanitizeImage is a helper to return human readable image name and
// the docker pullable image name from the provided image
func sanitizeImage(image string) (string, string) {
	if strings.Contains(image, "@sha256:") {
		return strings.Split(image, "@sha256:")[0], image
	}
	return image, image
}

// changeImageVersion replaces the version tag of a sanitized image string with the provided version.
// Assumes the image string always contains a tag (e.g., repo/name:tag).
func changeImageVersion(image, version string) string {
	lastColon := strings.LastIndex(image, ":")
	return image[:lastColon+1] + version
}

// removePrereleaseHash removes a final dash+hash (e.g., -8214d23) from the image tag,
// leaving e.g. cloud-provisioner:0.17.0-0.7.0-8214d23 -> cloud-provisioner:0.17.0-0.7.0
func removePrereleaseHash(image string) string {
    // Matches something like :0.17.0-0.7.0-8214d23 at the end and removes the -hash part
    re := regexp.MustCompile(`^(.+:\d+\.\d+\.\d+-\d+\.\d+\.\d+)-[a-fA-F0-9]{7,}$`)
    if matches := re.FindStringSubmatch(image); len(matches) == 2 {
        return matches[1]
    }
    return image
}