/*
Copyright 2026 The Kubernetes Authors.

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
	"testing"
)

func Test_removeReleaseCandidateSuffix(t *testing.T) {
	cases := []struct {
		image    string
		expected string
	}{
		// semver: release candidate suffix is stripped
		{
			image:    "registry.example.com/cloud-provisioner:0.9.0-rc.1",
			expected: "registry.example.com/cloud-provisioner:0.9.0",
		},
		{
			image:    "registry.example.com/cloud-provisioner:0.9.0-rc.12",
			expected: "registry.example.com/cloud-provisioner:0.9.0",
		},
		// no registry prefix
		{
			image:    "cloud-provisioner:0.9.0-rc.1",
			expected: "cloud-provisioner:0.9.0",
		},
		// registry with a port
		{
			image:    "registry.example.com:5000/cloud-provisioner:0.9.0-rc.1",
			expected: "registry.example.com:5000/cloud-provisioner:0.9.0",
		},
		// the old -<githash> prerelease format is no longer produced by the CI
		// and must be left untouched
		{
			image:    "registry.example.com/cloud-provisioner:0.9.0-8214d23",
			expected: "registry.example.com/cloud-provisioner:0.9.0-8214d23",
		},
		// semver: other pre-release identifiers must NOT be stripped
		{
			image:    "registry.example.com/cloud-provisioner:0.9.0-SNAPSHOT",
			expected: "registry.example.com/cloud-provisioner:0.9.0-SNAPSHOT",
		},
		{
			image:    "registry.example.com/cloud-provisioner:0.9.0-SNAPSHOT.2",
			expected: "registry.example.com/cloud-provisioner:0.9.0-SNAPSHOT.2",
		},
		{
			image:    "registry.example.com/cloud-provisioner:0.9.0-alpha.1",
			expected: "registry.example.com/cloud-provisioner:0.9.0-alpha.1",
		},
		{
			image:    "registry.example.com/cloud-provisioner:0.9.0-m.9",
			expected: "registry.example.com/cloud-provisioner:0.9.0-m.9",
		},
		{
			image:    "registry.example.com/cloud-provisioner:0.9.0-PR42-SNAPSHOT",
			expected: "registry.example.com/cloud-provisioner:0.9.0-PR42-SNAPSHOT",
		},
		// release tag: nothing to strip
		{
			image:    "registry.example.com/cloud-provisioner:0.9.0",
			expected: "registry.example.com/cloud-provisioner:0.9.0",
		},
		// no tag at all
		{
			image:    "registry.example.com/cloud-provisioner",
			expected: "registry.example.com/cloud-provisioner",
		},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.image, func(t *testing.T) {
			t.Parallel()
			got := removeReleaseCandidateSuffix(tc.image)
			if got != tc.expected {
				t.Errorf("removeReleaseCandidateSuffix(%q) = %q, want %q", tc.image, got, tc.expected)
			}
		})
	}
}
