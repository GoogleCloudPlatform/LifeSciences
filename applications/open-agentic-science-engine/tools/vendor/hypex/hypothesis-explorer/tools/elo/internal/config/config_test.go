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
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLoadRunConfig_MissingFileOrDir(t *testing.T) {
	// Missing dir
	cfg, err := LoadRunConfig("")
	if err != nil {
		t.Fatalf("expected nil error for empty runDir, got %v", err)
	}
	if cfg.Tournament.Composite == nil || cfg.Tournament.Composite.Preset != "balanced" {
		t.Errorf("expected balanced preset, got %+v", cfg.Tournament.Composite)
	}

	// Missing run.yaml in existing dir
	runDir := t.TempDir()
	cfg2, err := LoadRunConfig(runDir)
	if err != nil {
		t.Fatalf("expected nil error for missing run.yaml, got %v", err)
	}
	if cfg2.Tournament.Composite == nil || cfg2.Tournament.Composite.Preset != "balanced" {
		t.Errorf("expected balanced preset, got %+v", cfg2.Tournament.Composite)
	}
}

func TestLoadRunConfig_ValidYAML(t *testing.T) {
	runDir := t.TempDir()
	yamlContent := `
tournament:
  strategy: proximity-elo
  composite:
    preset: strict_constraints
    weights:
      novelty: 0
`
	if err := os.WriteFile(filepath.Join(runDir, "run.yaml"), []byte(yamlContent), 0o644); err != nil {
		t.Fatal(err)
	}

	cfg, err := LoadRunConfig(runDir)
	if err != nil {
		t.Fatalf("LoadRunConfig failed: %v", err)
	}
	if cfg.Tournament.Composite == nil {
		t.Fatal("expected non-nil composite config")
	}
	if cfg.Tournament.Composite.Preset != "strict_constraints" {
		t.Errorf("preset = %q, want strict_constraints", cfg.Tournament.Composite.Preset)
	}
	if cfg.Tournament.Composite.Weights == nil || cfg.Tournament.Composite.Weights.Novelty == nil {
		t.Fatal("expected novelty weight override")
	}
	if *cfg.Tournament.Composite.Weights.Novelty != 0 {
		t.Errorf("novelty weight = %v, want 0", *cfg.Tournament.Composite.Weights.Novelty)
	}
}

func TestLoadRunConfig_MalformedYAML(t *testing.T) {
	runDir := t.TempDir()
	path := filepath.Join(runDir, "run.yaml")
	if err := os.WriteFile(path, []byte(`tournament: [unclosed`), 0o644); err != nil {
		t.Fatal(err)
	}

	_, err := LoadRunConfig(runDir)
	if err == nil {
		t.Fatal("expected error for malformed YAML, got nil")
	}
	if !strings.Contains(err.Error(), path) {
		t.Errorf("error %q should name file path %q", err.Error(), path)
	}
}
