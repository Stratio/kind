package commons

import (
	"os"
	"path/filepath"
	"time"

	"gopkg.in/yaml.v3"

	"sigs.k8s.io/kind/pkg/errors"
)

// CreateStateDir holds what a resumed create needs on the host, next to secrets.yml and the descriptor (PLT-4965).
const CreateStateDir = ".cloud-provisioner"

var (
	CreateStatePath       = filepath.Join(CreateStateDir, "state.yaml")
	KeosClusterValuesPath = filepath.Join(CreateStateDir, "keos-cluster-values.yaml")
)

// CreateState is an index of a create in progress; live checks, not this file, decide whether a step runs again.
type CreateState struct {
	ClusterName             string    `yaml:"clusterName"`
	Version                 string    `yaml:"version"`
	KeosClusterChartVersion string    `yaml:"keosClusterChartVersion,omitempty"`
	LastCompletedStep       string    `yaml:"lastCompletedStep,omitempty"`
	CloudResources          bool      `yaml:"cloudResources"` // the keos-cluster chart started: AWS resources may exist
	Pivoted                 bool      `yaml:"pivoted"`
	UpdatedAt               time.Time `yaml:"updatedAt"`
}

// LoadCreateState returns nil, nil when there is no state file.
func LoadCreateState() (*CreateState, error) {
	raw, err := os.ReadFile(CreateStatePath)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	s := &CreateState{}
	if err := yaml.Unmarshal(raw, s); err != nil {
		return nil, errors.Wrap(err, "failed to parse "+CreateStatePath)
	}
	return s, nil
}

func (s *CreateState) Save() error {
	if err := os.MkdirAll(CreateStateDir, 0755); err != nil {
		return err
	}
	s.UpdatedAt = time.Now().UTC()
	raw, err := yaml.Marshal(s)
	if err != nil {
		return err
	}
	tmp := CreateStatePath + ".tmp"
	if err := os.WriteFile(tmp, raw, 0644); err != nil {
		return err
	}
	return os.Rename(tmp, CreateStatePath)
}

// UpdateCreateState applies change to the state file of the create in progress.
func UpdateCreateState(change func(*CreateState)) error {
	s, err := LoadCreateState()
	if err != nil {
		return err
	}
	if s == nil {
		return errors.New("no create state file " + CreateStatePath)
	}
	change(s)
	return s.Save()
}

// CheckResumable refuses a resume of another cluster or with another binary (no mixed-version resume).
func (s *CreateState) CheckResumable(clusterName, version string) error {
	if s.ClusterName != clusterName {
		return errors.Errorf("%s belongs to cluster %q, not %q", CreateStatePath, s.ClusterName, clusterName)
	}
	if s.Version != version {
		return errors.Errorf("the create was started with cloud-provisioner %s; resume it with that same binary (this one is %s)", s.Version, version)
	}
	return nil
}
