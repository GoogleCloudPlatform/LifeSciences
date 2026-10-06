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

package cmd

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"gopkg.in/yaml.v3"
)

func executeTestInitRun(t *testing.T, runDir string, args ...string) error {
	t.Helper()

	// Reset global flags before each run to avoid cross-test pollution.
	flagRunDir = runDir
	flagGoal = ""
	flagConstraints = nil
	flagCompositePreset = "balanced"
	flagMaxHypotheses = 50
	flagMaxMatches = 200
	flagMaxEpochs = 5
	flagTournamentStrat = "proximity-elo"
	flagRetrospectiveMode = false

	rootCmd.SetArgs(append([]string{"--run-dir", runDir, "init-run"}, args...))
	return rootCmd.Execute()
}

func TestBuildRunYAML_DefaultOutputsBalancedAndOmitsConstraints(t *testing.T) {
	// When compositePreset is empty or "balanced", and constraints is nil or empty,
	// buildRunYAML should output preset: balanced and omit constraints.
	testCases := []struct {
		name        string
		constraints []string
		preset      string
	}{
		{
			name:        "nil constraints and empty preset",
			constraints: nil,
			preset:      "",
		},
		{
			name:        "nil constraints and balanced preset",
			constraints: nil,
			preset:      "balanced",
		},
		{
			name:        "empty slice constraints and balanced preset",
			constraints: []string{},
			preset:      "balanced",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			yamlStr := buildRunYAML("test-run", "Test goal", tc.constraints, 50, 200, 5, "proximity-elo", tc.preset, false)

			if strings.Contains(yamlStr, "constraints:") {
				t.Errorf("expected output to omit constraints, but got:\n%s", yamlStr)
			}

			if !strings.Contains(yamlStr, "preset: balanced") {
				t.Errorf("expected output to contain 'preset: balanced', but got:\n%s", yamlStr)
			}

			if strings.Contains(yamlStr, "weights") {
				t.Errorf("expected output to omit weights, but got:\n%s", yamlStr)
			}
		})
	}
}

func TestInitRunCmd_ConstraintFlagIncludesConstraintsInYAML(t *testing.T) {
	tmpDir := t.TempDir()
	runID := "run-with-constraints"

	err := executeTestInitRun(t, tmpDir,
		"--goal", "Identify novel therapeutic targets",
		"--constraint", "Targets must be druggable",
		"--constraint", "Validation feasible at BSL-2",
		"--constraint", "No germline editing",
		runID,
	)
	if err != nil {
		t.Fatalf("executeTestInitRun failed: %v", err)
	}

	yamlPath := filepath.Join(tmpDir, runID, "run.yaml")
	data, err := os.ReadFile(yamlPath)
	if err != nil {
		t.Fatalf("reading run.yaml: %v", err)
	}
	content := string(data)

	expectedConstraints := []string{
		"Targets must be druggable",
		"Validation feasible at BSL-2",
		"No germline editing",
	}

	for _, c := range expectedConstraints {
		if !strings.Contains(content, c) {
			t.Errorf("expected run.yaml to contain constraint %q, got:\n%s", c, content)
		}
	}

	if !strings.Contains(content, "constraints:") {
		t.Errorf("expected run.yaml to contain 'constraints:', got:\n%s", content)
	}

	if !strings.Contains(content, "preset: balanced") {
		t.Errorf("expected run.yaml to contain 'preset: balanced', got:\n%s", content)
	}

	// Verify unmarshaling extracts the exact slice in order
	var cfg runYAMLConfig
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		t.Fatalf("unmarshaling run.yaml: %v", err)
	}

	if !reflect.DeepEqual(cfg.Constraints, expectedConstraints) {
		t.Errorf("parsed constraints = %v, want %v", cfg.Constraints, expectedConstraints)
	}
}

func TestInitRunCmd_OmittedConstraintOmitsConstraintsKey(t *testing.T) {
	tmpDir := t.TempDir()
	runID := "run-no-constraints"

	err := executeTestInitRun(t, tmpDir,
		"--goal", "Goal with no extra conditions",
		runID,
	)
	if err != nil {
		t.Fatalf("executeTestInitRun failed: %v", err)
	}

	yamlPath := filepath.Join(tmpDir, runID, "run.yaml")
	data, err := os.ReadFile(yamlPath)
	if err != nil {
		t.Fatalf("reading run.yaml: %v", err)
	}
	content := string(data)

	if strings.Contains(content, "constraints:") {
		t.Errorf("expected run.yaml to omit constraints:, got:\n%s", content)
	}

	var cfg runYAMLConfig
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		t.Fatalf("unmarshaling run.yaml: %v", err)
	}

	if len(cfg.Constraints) != 0 {
		t.Errorf("expected nil/empty constraints, got: %v", cfg.Constraints)
	}
}

func TestInitRunCmd_CompositePresetFlag(t *testing.T) {
	presets := []string{
		"balanced",
		"disable_novelty",
		"focus_on_breakthroughs",
		"strict_constraints",
		"pure_tournament",
	}

	for _, preset := range presets {
		t.Run(preset, func(t *testing.T) {
			tmpDir := t.TempDir()
			runID := "run-preset-" + preset

			err := executeTestInitRun(t, tmpDir,
				"--goal", "Testing preset "+preset,
				"--composite-preset", preset,
				runID,
			)
			if err != nil {
				t.Fatalf("executeTestInitRun failed for preset %s: %v", preset, err)
			}

			yamlPath := filepath.Join(tmpDir, runID, "run.yaml")
			data, err := os.ReadFile(yamlPath)
			if err != nil {
				t.Fatalf("reading run.yaml: %v", err)
			}
			content := string(data)

			expected := "preset: " + preset
			if !strings.Contains(content, expected) {
				t.Errorf("expected run.yaml to contain %q, got:\n%s", expected, content)
			}

			var cfg runYAMLConfig
			if err := yaml.Unmarshal(data, &cfg); err != nil {
				t.Fatalf("unmarshaling run.yaml: %v", err)
			}

			if cfg.Tournament.Composite == nil || cfg.Tournament.Composite.Preset != preset {
				t.Errorf("parsed composite preset = %v, want %s", cfg.Tournament.Composite, preset)
			}
		})
	}
}

func TestInitRunCmd_InvalidCompositePresetReturnsErrorListingAllPresets(t *testing.T) {
	tmpDir := t.TempDir()
	runID := "run-invalid-preset"

	err := executeTestInitRun(t, tmpDir,
		"--goal", "Testing invalid preset",
		"--composite-preset", "unsupported_preset",
		runID,
	)
	if err == nil {
		t.Fatalf("expected error for invalid composite-preset, but got nil")
	}

	errMsg := err.Error()

	// Must mention the invalid value
	if !strings.Contains(errMsg, "unsupported_preset") {
		t.Errorf("expected error message to mention 'unsupported_preset', got: %s", errMsg)
	}

	// Must list all 5 valid presets
	expectedPresets := []string{
		"balanced",
		"disable_novelty",
		"focus_on_breakthroughs",
		"strict_constraints",
		"pure_tournament",
	}

	for _, p := range expectedPresets {
		if !strings.Contains(errMsg, p) {
			t.Errorf("expected error message to list preset %q, got: %s", p, errMsg)
		}
	}
}

func TestBuildRunYAML_RoundTripAndValidation(t *testing.T) {
	runID := "lupus-roundtrip-01"
	goal := "Identify novel therapeutic targets for refractory SLE"
	constraints := []string{
		"Targets must be druggable by small molecule or approved biologic",
		"Experimental validation must be feasible at BSL-2",
		"No germline editing",
	}
	maxH := 40
	maxM := 150
	maxE := 3
	strategy := "swiss"
	compositePreset := "focus_on_breakthroughs"
	retroMode := true

	yamlStr := buildRunYAML(runID, goal, constraints, maxH, maxM, maxE, strategy, compositePreset, retroMode)

	// Validate header
	if !strings.HasPrefix(yamlStr, "# Hypothesis-Explorer Run Configuration\n# Generated by: hypex init-run\n\n") {
		t.Errorf("expected standard header in yaml, got:\n%s", yamlStr)
	}

	var cfg runYAMLConfig
	if err := yaml.Unmarshal([]byte(yamlStr), &cfg); err != nil {
		t.Fatalf("failed to unmarshal generated YAML: %v\nYAML content:\n%s", err, yamlStr)
	}

	if cfg.RunID != runID {
		t.Errorf("RunID = %q, want %q", cfg.RunID, runID)
	}
	if cfg.Goal != goal {
		t.Errorf("Goal = %q, want %q", cfg.Goal, goal)
	}
	if !reflect.DeepEqual(cfg.Constraints, constraints) {
		t.Errorf("Constraints = %v, want %v", cfg.Constraints, constraints)
	}
	if cfg.Budgets.MaxHypotheses != maxH {
		t.Errorf("Budgets.MaxHypotheses = %d, want %d", cfg.Budgets.MaxHypotheses, maxH)
	}
	if cfg.Budgets.MaxMatches != maxM {
		t.Errorf("Budgets.MaxMatches = %d, want %d", cfg.Budgets.MaxMatches, maxM)
	}
	if cfg.Budgets.MaxEpochs != maxE {
		t.Errorf("Budgets.MaxEpochs = %d, want %d", cfg.Budgets.MaxEpochs, maxE)
	}
	if cfg.Tournament.Strategy != strategy {
		t.Errorf("Tournament.Strategy = %q, want %q", cfg.Tournament.Strategy, strategy)
	}
	if cfg.Tournament.Composite == nil {
		t.Fatalf("Tournament.Composite is nil")
	}
	if cfg.Tournament.Composite.Preset != compositePreset {
		t.Errorf("Tournament.Composite.Preset = %q, want %q", cfg.Tournament.Composite.Preset, compositePreset)
	}
	if cfg.Tournament.Composite.Weights != nil {
		t.Errorf("Tournament.Composite.Weights = %v, want nil", cfg.Tournament.Composite.Weights)
	}
	if cfg.RetrospectiveMode != retroMode {
		t.Errorf("RetrospectiveMode = %v, want %v", cfg.RetrospectiveMode, retroMode)
	}

	// Validate CreatedAt parses as RFC3339
	parsedTime, err := time.Parse(time.RFC3339, cfg.CreatedAt)
	if err != nil {
		t.Errorf("CreatedAt %q failed to parse as RFC3339: %v", cfg.CreatedAt, err)
	}
	if time.Since(parsedTime) > 5*time.Minute {
		t.Errorf("CreatedAt %v is unexpectedly old", parsedTime)
	}
}

func TestInitRunCmd_CreatesCitationsDirectory(t *testing.T) {
	tmpDir := t.TempDir()
	runID := "run-with-citations"

	err := executeTestInitRun(t, tmpDir,
		"--goal", "Test that citations directory is created",
		runID,
	)
	if err != nil {
		t.Fatalf("executeTestInitRun failed: %v", err)
	}

	citationsDir := filepath.Join(tmpDir, runID, "citations")
	info, err := os.Stat(citationsDir)
	if err != nil {
		t.Fatalf("expected citations directory %s to exist: %v", citationsDir, err)
	}
	if !info.IsDir() {
		t.Errorf("expected %s to be a directory", citationsDir)
	}
}
