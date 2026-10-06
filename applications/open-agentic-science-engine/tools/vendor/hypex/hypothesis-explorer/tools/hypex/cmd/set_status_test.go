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
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/hypex/internal/schema"
)

func TestSetStatus_Quarantine(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-set-status-quarantine"
	initTestRun(t, binPath, runsDir, runID)

	hID := "H-0001"
	hypoPath := filepath.Join(runsDir, runID, "hypotheses", hID+".json")
	quarPath := filepath.Join(runsDir, runID, "quarantine", hID+".json")

	initialData := sampleHypothesisData(hID, "proposed")
	if err := os.WriteFile(hypoPath, mustMarshalIndent(t, initialData), 0o644); err != nil {
		t.Fatal(err)
	}

	cmd := exec.Command(binPath, "set-status",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		hID,
		"quarantined",
	)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("set-status failed: %v\n%s", err, out)
	}

	outStr := string(out)
	if !strings.Contains(outStr, "quarantined") || !strings.Contains(outStr, "moved") {
		t.Errorf("expected output to mention quarantined and moved: %s", outStr)
	}

	// Verify old file is gone.
	if _, err := os.Stat(hypoPath); !os.IsNotExist(err) {
		t.Errorf("old hypothesis file %s should have been removed", hypoPath)
	}

	// Verify new file exists in quarantine.
	if _, err := os.Stat(quarPath); err != nil {
		t.Fatalf("quarantined hypothesis file %s should exist: %v", quarPath, err)
	}

	// Validate content.
	data, err := os.ReadFile(quarPath)
	if err != nil {
		t.Fatal(err)
	}
	var parsed map[string]any
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatal(err)
	}
	if parsed["status"] != "quarantined" {
		t.Errorf("got status %q, want 'quarantined'", parsed["status"])
	}

	// Validate against hypothesis schema.
	if err := schema.ValidateFileAgainstSchema(schemaDir, "hypothesis.schema.json", quarPath); err != nil {
		t.Errorf("quarantined file failed schema validation: %v", err)
	}
}

func TestSetStatus_OtherValidStatus(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-set-status-other"
	initTestRun(t, binPath, runsDir, runID)

	hID := "H-0001"
	hypoPath := filepath.Join(runsDir, runID, "hypotheses", hID+".json")

	initialData := sampleHypothesisData(hID, "proposed")
	if err := os.WriteFile(hypoPath, mustMarshalIndent(t, initialData), 0o644); err != nil {
		t.Fatal(err)
	}

	transitions := []string{"reviewed", "active", "retired"}
	for _, target := range transitions {
		cmd := exec.Command(binPath, "set-status",
			"--run-dir", runsDir,
			"--run", runID,
			"--schema-dir", schemaDir,
			hID,
			target,
		)
		out, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("set-status %s failed: %v\n%s", target, err, out)
		}

		// Verify file remains in hypotheses/.
		data, err := os.ReadFile(hypoPath)
		if err != nil {
			t.Fatalf("reading hypothesis file %s: %v", hypoPath, err)
		}
		var parsed map[string]any
		if err := json.Unmarshal(data, &parsed); err != nil {
			t.Fatal(err)
		}
		if parsed["status"] != target {
			t.Errorf("got status %q, want %q", parsed["status"], target)
		}

		if err := schema.ValidateFileAgainstSchema(schemaDir, "hypothesis.schema.json", hypoPath); err != nil {
			t.Errorf("file failed schema validation for status %s: %v", target, err)
		}
	}
}

func TestSetStatus_NonexistentHypothesis(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-set-status-nonexistent"
	initTestRun(t, binPath, runsDir, runID)

	cmd := exec.Command(binPath, "set-status",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"H-9999",
		"quarantined",
	)
	out, err := cmd.CombinedOutput()
	if err == nil {
		t.Fatalf("expected failure for nonexistent hypothesis, got exit 0: %s", out)
	}
	if !strings.Contains(string(out), "hypothesis file not found") {
		t.Errorf("expected error containing 'hypothesis file not found', got: %s", out)
	}
}

func TestSetStatus_InvalidStatusEnum(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-set-status-bad-status"
	initTestRun(t, binPath, runsDir, runID)

	hID := "H-0001"
	hypoPath := filepath.Join(runsDir, runID, "hypotheses", hID+".json")

	initialData := sampleHypothesisData(hID, "proposed")
	if err := os.WriteFile(hypoPath, mustMarshalIndent(t, initialData), 0o644); err != nil {
		t.Fatal(err)
	}

	cmd := exec.Command(binPath, "set-status",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		hID,
		"bogus-status",
	)
	out, err := cmd.CombinedOutput()
	if err == nil {
		t.Fatalf("expected failure for invalid status enum, got exit 0: %s", out)
	}
	if !strings.Contains(string(out), "invalid status") {
		t.Errorf("expected error containing 'invalid status', got: %s", out)
	}

	// Verify original file is unchanged.
	data, err := os.ReadFile(hypoPath)
	if err != nil {
		t.Fatal(err)
	}
	var parsed map[string]any
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatal(err)
	}
	if parsed["status"] != "proposed" {
		t.Errorf("status was modified despite error: got %q", parsed["status"])
	}
}

func TestSetStatus_InvalidHypothesisID(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-set-status-bad-id"
	initTestRun(t, binPath, runsDir, runID)

	cmd := exec.Command(binPath, "set-status",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"bad-id",
		"quarantined",
	)
	out, err := cmd.CombinedOutput()
	if err == nil {
		t.Fatalf("expected failure for bad hypothesis ID, got exit 0: %s", out)
	}
	if !strings.Contains(string(out), "invalid hypothesis ID") {
		t.Errorf("expected error containing 'invalid hypothesis ID', got: %s", out)
	}
}

func TestSetStatus_UnquarantineRoundTrip(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-set-status-unquarantine"
	initTestRun(t, binPath, runsDir, runID)

	hID := "H-0001"
	hypoPath := filepath.Join(runsDir, runID, "hypotheses", hID+".json")
	quarPath := filepath.Join(runsDir, runID, "quarantine", hID+".json")

	initialData := sampleHypothesisData(hID, "proposed")
	if err := os.WriteFile(hypoPath, mustMarshalIndent(t, initialData), 0o644); err != nil {
		t.Fatal(err)
	}

	// Step 1: Move to quarantine.
	cmd := exec.Command(binPath, "set-status",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		hID,
		"quarantined",
	)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("set-status quarantined failed: %v\n%s", err, out)
	}

	if _, err := os.Stat(hypoPath); !os.IsNotExist(err) {
		t.Errorf("hypotheses file should not exist after quarantine")
	}
	if _, err := os.Stat(quarPath); err != nil {
		t.Fatalf("quarantine file should exist: %v", err)
	}

	// Step 2: Un-quarantine back to active.
	cmd = exec.Command(binPath, "set-status",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		hID,
		"active",
	)
	out, err = cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("set-status active (unquarantine) failed: %v\n%s", err, out)
	}

	outStr := string(out)
	if !strings.Contains(outStr, "moved") {
		t.Errorf("expected output to mention moved: %s", outStr)
	}

	// Verify quarantine file is gone.
	if _, err := os.Stat(quarPath); !os.IsNotExist(err) {
		t.Errorf("quarantine file should have been removed after un-quarantine")
	}

	// Verify hypotheses file is restored.
	if _, err := os.Stat(hypoPath); err != nil {
		t.Fatalf("hypotheses file should exist after un-quarantine: %v", err)
	}

	// Validate content and schema.
	data, err := os.ReadFile(hypoPath)
	if err != nil {
		t.Fatal(err)
	}
	var parsed map[string]any
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatal(err)
	}
	if parsed["status"] != "active" {
		t.Errorf("got status %q, want active", parsed["status"])
	}

	if err := schema.ValidateFileAgainstSchema(schemaDir, "hypothesis.schema.json", hypoPath); err != nil {
		t.Errorf("unquarantined file failed schema validation: %v", err)
	}
}

func TestSetStatus_SchemaRejectionNoModification(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-set-status-schema-rejection"
	initTestRun(t, binPath, runsDir, runID)

	hID := "H-0001"
	hypoPath := filepath.Join(runsDir, runID, "hypotheses", hID+".json")
	quarPath := filepath.Join(runsDir, runID, "quarantine", hID+".json")

	// Create an invalid hypothesis (missing required "mechanism" field).
	invalidData := sampleHypothesisData(hID, "proposed")
	delete(invalidData, "mechanism")
	originalBytes := mustMarshalIndent(t, invalidData)
	if err := os.WriteFile(hypoPath, originalBytes, 0o644); err != nil {
		t.Fatal(err)
	}

	// Attempt to set status to quarantined.
	cmd := exec.Command(binPath, "set-status",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		hID,
		"quarantined",
	)
	out, err := cmd.CombinedOutput()
	if err == nil {
		t.Fatalf("expected set-status with invalid schema to fail, but exited 0: %s", out)
	}

	if !strings.Contains(string(out), "schema validation failed") {
		t.Errorf("expected error to contain 'schema validation failed', got: %s", out)
	}

	// Verify quarantine file was NOT created.
	if _, err := os.Stat(quarPath); !os.IsNotExist(err) {
		t.Errorf("quarantine file should not have been created on schema validation failure")
	}

	// Verify source hypothesis file was NOT modified or deleted.
	currentBytes, err := os.ReadFile(hypoPath)
	if err != nil {
		t.Fatalf("reading source hypothesis file: %v", err)
	}
	if string(currentBytes) != string(originalBytes) {
		t.Errorf("source hypothesis file was modified despite schema validation failure")
	}
}
