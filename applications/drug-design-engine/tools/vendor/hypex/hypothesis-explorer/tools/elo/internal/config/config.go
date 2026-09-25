// Copyright 2026 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package config

import (
	"fmt"
	"os"
	"path/filepath"

	"gopkg.in/yaml.v3"
)

// RunConfig is the subset of run.yaml needed for tournament rating.
type RunConfig struct {
	Tournament TournamentConfig `yaml:"tournament"`
}

type TournamentConfig struct {
	Strategy  string           `yaml:"strategy"`
	Composite *CompositeConfig `yaml:"composite,omitempty"`
}

type CompositeConfig struct {
	Preset  string       `yaml:"preset,omitempty"`
	Weights *CompWeights `yaml:"weights,omitempty"`
}

type CompWeights struct {
	Goal       *float64 `yaml:"goal,omitempty"`
	Constraint *float64 `yaml:"constraint,omitempty"`
	Novelty    *float64 `yaml:"novelty,omitempty"`
}

// DefaultRunConfig returns a RunConfig configured with the balanced preset.
func DefaultRunConfig() *RunConfig {
	return &RunConfig{
		Tournament: TournamentConfig{
			Composite: &CompositeConfig{
				Preset: "balanced",
			},
		},
	}
}

// LoadRunConfig reads <runDir>/run.yaml.
// If runDir is empty or run.yaml does not exist, returns DefaultRunConfig() and nil error.
// If run.yaml exists but has invalid YAML, returns a hard error naming the file path.
func LoadRunConfig(runDir string) (*RunConfig, error) {
	if runDir == "" {
		return DefaultRunConfig(), nil
	}

	path := filepath.Join(runDir, "run.yaml")
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return DefaultRunConfig(), nil
		}
		return nil, fmt.Errorf("reading run config %s: %w", path, err)
	}

	var cfg RunConfig
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("parsing run config %s: %w", path, err)
	}

	if cfg.Tournament.Composite == nil {
		cfg.Tournament.Composite = &CompositeConfig{Preset: "balanced"}
	} else if cfg.Tournament.Composite.Preset == "" && cfg.Tournament.Composite.Weights == nil {
		cfg.Tournament.Composite.Preset = "balanced"
	}

	return &cfg, nil
}
