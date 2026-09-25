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
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// sampleEvolvedHypothesisData returns a hypothesis with lineage pointing to parents.
func sampleEvolvedHypothesisData(operator string, parentIDs []string, evidence []map[string]any) map[string]any {
	h := sampleHypothesisData("", "proposed")
	h["lineage"] = map[string]any{
		"parents":  parentIDs,
		"operator": operator,
	}
	if evidence != nil {
		h["evidence"] = evidence
	}
	return h
}

// addParentHypothesis creates a parent hypothesis via the add-hypothesis CLI
// so that the ID counter is properly advanced. Returns the allocated ID.
func addParentHypothesis(t *testing.T, binPath, runsDir, runID, schemaDir string, evidence []map[string]any) string {
	t.Helper()
	h := sampleHypothesisData("", "proposed")
	if evidence != nil {
		h["evidence"] = evidence
	}
	payload := mustMarshalIndent(t, h)

	cmd := exec.Command(binPath, "add-hypothesis",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"-",
	)
	cmd.Stdin = bytes.NewReader(payload)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("adding parent hypothesis: %v\n%s", err, out)
	}

	// Parse the allocated ID from output like "Added hypothesis: H-0001 → ..."
	outStr := string(out)
	parts := strings.Fields(outStr)
	for i, p := range parts {
		if p == "hypothesis:" && i+1 < len(parts) {
			return parts[i+1]
		}
	}
	t.Fatalf("could not parse hypothesis ID from output: %s", outStr)
	return ""
}

func TestAddHypothesis_NullOperator_NoCitationDelta(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-hyp-null"
	initTestRun(t, binPath, runsDir, runID)

	// First-generation hypothesis with null operator — no citation_delta.
	h := sampleHypothesisData("", "proposed")
	payload := mustMarshalIndent(t, h)

	cmd := exec.Command(binPath, "add-hypothesis",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"-",
	)
	cmd.Stdin = bytes.NewReader(payload)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("add-hypothesis failed: %v\n%s", err, out)
	}

	// Read written hypothesis and verify no citation_delta.
	hypPath := filepath.Join(runsDir, runID, "hypotheses", "H-0001.json")
	data, err := os.ReadFile(hypPath)
	if err != nil {
		t.Fatal(err)
	}
	var parsed map[string]any
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatal(err)
	}
	lin := parsed["lineage"].(map[string]any)
	if _, exists := lin["citation_delta"]; exists {
		t.Error("null operator hypothesis should not have citation_delta")
	}
}

func TestAddHypothesis_Combine_Compliant(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-hyp-combine-ok"
	initTestRun(t, binPath, runsDir, runID)

	// Create parent via CLI with two citations.
	parentEvidence := []map[string]any{
		{"lit_id": "PMID:1001", "role": "supports", "note": "note1"},
		{"lit_id": "PMID:1002", "role": "supports", "note": "note2"},
	}
	parentID := addParentHypothesis(t, binPath, runsDir, runID, schemaDir, parentEvidence)

	// Child inherits all parent citations plus adds a new one — compliant.
	childEvidence := []map[string]any{
		{"lit_id": "PMID:1001", "role": "supports", "note": "inherited1"},
		{"lit_id": "PMID:1002", "role": "supports", "note": "inherited2"},
		{"lit_id": "PMID:2001", "role": "supports", "note": "new evidence"},
	}
	child := sampleEvolvedHypothesisData("combine", []string{parentID}, childEvidence)
	payload := mustMarshalIndent(t, child)

	cmd := exec.Command(binPath, "add-hypothesis",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"-",
	)
	cmd.Stdin = bytes.NewReader(payload)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("add-hypothesis failed: %v\n%s", err, out)
	}

	// Read and verify citation_delta (child gets H-0002).
	hypPath := filepath.Join(runsDir, runID, "hypotheses", "H-0002.json")
	data, err := os.ReadFile(hypPath)
	if err != nil {
		t.Fatal(err)
	}
	var parsed map[string]any
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatal(err)
	}
	lin := parsed["lineage"].(map[string]any)
	delta, ok := lin["citation_delta"].(map[string]any)
	if !ok {
		t.Fatal("expected citation_delta in lineage")
	}
	if delta["compliant"] != true {
		t.Error("expected compliant = true")
	}
	if delta["rule"] != "superset" {
		t.Errorf("expected rule superset, got %v", delta["rule"])
	}
	dropped := delta["dropped"].([]any)
	if len(dropped) != 0 {
		t.Errorf("expected empty dropped, got %v", dropped)
	}
}

func TestAddHypothesis_Combine_NonCompliant_Warning(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-hyp-combine-warn"
	initTestRun(t, binPath, runsDir, runID)

	// Parent with two citations.
	parentEvidence := []map[string]any{
		{"lit_id": "PMID:1001", "role": "supports", "note": "note1"},
		{"lit_id": "PMID:1002", "role": "supports", "note": "note2"},
	}
	parentID := addParentHypothesis(t, binPath, runsDir, runID, schemaDir, parentEvidence)

	// Child drops PMID:1002 — non-compliant.
	childEvidence := []map[string]any{
		{"lit_id": "PMID:1001", "role": "supports", "note": "inherited"},
	}
	child := sampleEvolvedHypothesisData("combine", []string{parentID}, childEvidence)
	payload := mustMarshalIndent(t, child)

	cmd := exec.Command(binPath, "add-hypothesis",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"-",
	)
	cmd.Stdin = bytes.NewReader(payload)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("add-hypothesis should succeed (warn mode): %v\n%s", err, out)
	}

	// Should print a warning to stderr.
	if !strings.Contains(string(out), "Warning") {
		t.Errorf("expected warning in output, got: %s", out)
	}

	// File should be written with compliant: false.
	hypPath := filepath.Join(runsDir, runID, "hypotheses", "H-0002.json")
	data, err := os.ReadFile(hypPath)
	if err != nil {
		t.Fatal(err)
	}
	var parsed map[string]any
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatal(err)
	}
	lin := parsed["lineage"].(map[string]any)
	delta := lin["citation_delta"].(map[string]any)
	if delta["compliant"] != false {
		t.Error("expected compliant = false")
	}
	dropped := delta["dropped"].([]any)
	if len(dropped) != 1 {
		t.Errorf("expected 1 dropped citation, got %d", len(dropped))
	}
}

func TestAddHypothesis_StrictLineage_NonCompliant_Rejected(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-hyp-strict"
	initTestRun(t, binPath, runsDir, runID)

	// Parent.
	parentEvidence := []map[string]any{
		{"lit_id": "PMID:1001", "role": "supports", "note": "note1"},
		{"lit_id": "PMID:1002", "role": "supports", "note": "note2"},
	}
	parentID := addParentHypothesis(t, binPath, runsDir, runID, schemaDir, parentEvidence)

	// Non-compliant child (drops PMID:1002).
	childEvidence := []map[string]any{
		{"lit_id": "PMID:1001", "role": "supports", "note": "inherited"},
	}
	child := sampleEvolvedHypothesisData("combine", []string{parentID}, childEvidence)
	payload := mustMarshalIndent(t, child)

	cmd := exec.Command(binPath, "add-hypothesis",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"--strict-lineage",
		"-",
	)
	cmd.Stdin = bytes.NewReader(payload)
	out, err := cmd.CombinedOutput()
	if err == nil {
		t.Fatalf("expected --strict-lineage to reject non-compliant hypothesis, but exit 0: %s", out)
	}

	if !strings.Contains(string(out), "citation lineage non-compliant") {
		t.Errorf("expected error about non-compliant lineage, got: %s", out)
	}

	// Verify file was NOT written (child would be H-0002).
	hypPath := filepath.Join(runsDir, runID, "hypotheses", "H-0002.json")
	if _, err := os.Stat(hypPath); err == nil {
		t.Error("expected hypothesis file NOT to be written in strict mode")
	}
}

func TestAddHypothesis_Ground_Compliant(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-hyp-ground"
	initTestRun(t, binPath, runsDir, runID)

	parentEvidence := []map[string]any{
		{"lit_id": "PMID:1001", "role": "supports", "note": "note1"},
	}
	parentID := addParentHypothesis(t, binPath, runsDir, runID, schemaDir, parentEvidence)

	// Ground child keeps parent citation and adds new evidence — compliant.
	childEvidence := []map[string]any{
		{"lit_id": "PMID:1001", "role": "supports", "note": "inherited"},
		{"lit_id": "PMID:2001", "role": "supports", "note": "new grounding evidence"},
	}
	child := sampleEvolvedHypothesisData("ground", []string{parentID}, childEvidence)
	payload := mustMarshalIndent(t, child)

	cmd := exec.Command(binPath, "add-hypothesis",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"-",
	)
	cmd.Stdin = bytes.NewReader(payload)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("add-hypothesis failed: %v\n%s", err, out)
	}

	hypPath := filepath.Join(runsDir, runID, "hypotheses", "H-0002.json")
	data, err := os.ReadFile(hypPath)
	if err != nil {
		t.Fatal(err)
	}
	var parsed map[string]any
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatal(err)
	}
	delta := parsed["lineage"].(map[string]any)["citation_delta"].(map[string]any)
	if delta["compliant"] != true {
		t.Error("expected compliant = true for ground superset")
	}
	if delta["rule"] != "superset" {
		t.Errorf("expected rule superset, got %v", delta["rule"])
	}
}

func TestAddHypothesis_Simplify_Compliant(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-hyp-simplify"
	initTestRun(t, binPath, runsDir, runID)

	parentEvidence := []map[string]any{
		{"lit_id": "PMID:1001", "role": "supports", "note": "note1"},
		{"lit_id": "PMID:1002", "role": "supports", "note": "note2"},
		{"lit_id": "PMID:1003", "role": "supports", "note": "note3"},
	}
	parentID := addParentHypothesis(t, binPath, runsDir, runID, schemaDir, parentEvidence)

	// Simplify child uses subset of parent citations — compliant.
	childEvidence := []map[string]any{
		{"lit_id": "PMID:1001", "role": "supports", "note": "kept"},
		{"lit_id": "PMID:1003", "role": "supports", "note": "kept"},
	}
	child := sampleEvolvedHypothesisData("simplify", []string{parentID}, childEvidence)
	payload := mustMarshalIndent(t, child)

	cmd := exec.Command(binPath, "add-hypothesis",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"-",
	)
	cmd.Stdin = bytes.NewReader(payload)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("add-hypothesis failed: %v\n%s", err, out)
	}

	hypPath := filepath.Join(runsDir, runID, "hypotheses", "H-0002.json")
	data, err := os.ReadFile(hypPath)
	if err != nil {
		t.Fatal(err)
	}
	var parsed map[string]any
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatal(err)
	}
	delta := parsed["lineage"].(map[string]any)["citation_delta"].(map[string]any)
	if delta["compliant"] != true {
		t.Error("expected compliant = true for simplify subset")
	}
	if delta["rule"] != "subset" {
		t.Errorf("expected rule subset, got %v", delta["rule"])
	}
	added := delta["added"].([]any)
	if len(added) != 0 {
		t.Errorf("expected no added citations for compliant simplify, got %v", added)
	}
}

func TestAddHypothesis_Simplify_NonCompliant(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-hyp-simplify-bad"
	initTestRun(t, binPath, runsDir, runID)

	parentEvidence := []map[string]any{
		{"lit_id": "PMID:1001", "role": "supports", "note": "note1"},
	}
	parentID := addParentHypothesis(t, binPath, runsDir, runID, schemaDir, parentEvidence)

	// Simplify child adds new citation — non-compliant.
	childEvidence := []map[string]any{
		{"lit_id": "PMID:1001", "role": "supports", "note": "kept"},
		{"lit_id": "PMID:3001", "role": "supports", "note": "new — not allowed"},
	}
	child := sampleEvolvedHypothesisData("simplify", []string{parentID}, childEvidence)
	payload := mustMarshalIndent(t, child)

	cmd := exec.Command(binPath, "add-hypothesis",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"-",
	)
	cmd.Stdin = bytes.NewReader(payload)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("expected warn mode to succeed: %v\n%s", err, out)
	}

	hypPath := filepath.Join(runsDir, runID, "hypotheses", "H-0002.json")
	data, err := os.ReadFile(hypPath)
	if err != nil {
		t.Fatal(err)
	}
	var parsed map[string]any
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatal(err)
	}
	delta := parsed["lineage"].(map[string]any)["citation_delta"].(map[string]any)
	if delta["compliant"] != false {
		t.Error("expected compliant = false for simplify with added citations")
	}
}

func TestAddHypothesis_OOB_AlwaysCompliant(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-hyp-oob"
	initTestRun(t, binPath, runsDir, runID)

	parentEvidence := []map[string]any{
		{"lit_id": "PMID:1001", "role": "supports", "note": "note1"},
	}
	parentID := addParentHypothesis(t, binPath, runsDir, runID, schemaDir, parentEvidence)

	// OOB child with completely different citations — always compliant.
	childEvidence := []map[string]any{
		{"lit_id": "PMID:9999", "role": "supports", "note": "totally new"},
	}
	child := sampleEvolvedHypothesisData("oob", []string{parentID}, childEvidence)
	payload := mustMarshalIndent(t, child)

	cmd := exec.Command(binPath, "add-hypothesis",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"-",
	)
	cmd.Stdin = bytes.NewReader(payload)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("add-hypothesis failed: %v\n%s", err, out)
	}

	hypPath := filepath.Join(runsDir, runID, "hypotheses", "H-0002.json")
	data, err := os.ReadFile(hypPath)
	if err != nil {
		t.Fatal(err)
	}
	var parsed map[string]any
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatal(err)
	}
	delta := parsed["lineage"].(map[string]any)["citation_delta"].(map[string]any)
	if delta["compliant"] != true {
		t.Error("expected compliant = true for oob (free rule)")
	}
	if delta["rule"] != "free" {
		t.Errorf("expected rule free, got %v", delta["rule"])
	}
}

func TestAddHypothesis_MissingParent_Warning(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-hyp-missing-parent"
	initTestRun(t, binPath, runsDir, runID)

	// No parent file written — should warn but still write.
	childEvidence := []map[string]any{
		{"lit_id": "PMID:1001", "role": "supports", "note": "note"},
	}
	child := sampleEvolvedHypothesisData("combine", []string{"H-0099"}, childEvidence)
	payload := mustMarshalIndent(t, child)

	cmd := exec.Command(binPath, "add-hypothesis",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"-",
	)
	cmd.Stdin = bytes.NewReader(payload)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("expected success with warning: %v\n%s", err, out)
	}

	if !strings.Contains(string(out), "Warning") {
		t.Errorf("expected warning about missing parent, got: %s", out)
	}

	// Hypothesis should be written with compliant: false (gets H-0001).
	hypPath := filepath.Join(runsDir, runID, "hypotheses", "H-0001.json")
	data, err := os.ReadFile(hypPath)
	if err != nil {
		t.Fatal(err)
	}
	var parsed map[string]any
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatal(err)
	}
	delta := parsed["lineage"].(map[string]any)["citation_delta"].(map[string]any)
	if delta["compliant"] != false {
		t.Error("expected compliant = false when parent is missing")
	}
}

func TestAddHypothesis_EmptyParents_NoCitationDelta(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-hyp-empty-parents"
	initTestRun(t, binPath, runsDir, runID)

	// Hypothesis with operator but empty parents array — no delta.
	h := sampleHypothesisData("", "proposed")
	h["lineage"] = map[string]any{
		"parents":  []string{},
		"operator": "combine",
	}
	payload := mustMarshalIndent(t, h)

	cmd := exec.Command(binPath, "add-hypothesis",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"-",
	)
	cmd.Stdin = bytes.NewReader(payload)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("add-hypothesis failed: %v\n%s", err, out)
	}

	hypPath := filepath.Join(runsDir, runID, "hypotheses", "H-0001.json")
	data, err := os.ReadFile(hypPath)
	if err != nil {
		t.Fatal(err)
	}
	var parsed map[string]any
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatal(err)
	}
	lin := parsed["lineage"].(map[string]any)
	if _, exists := lin["citation_delta"]; exists {
		t.Error("empty parents should not produce citation_delta")
	}
}

func TestAddHypothesis_CitationDelta_PassesSchemaValidation(t *testing.T) {
	// Verify that a hypothesis with citation_delta validates against the updated schema.
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-hyp-schema-validation"
	initTestRun(t, binPath, runsDir, runID)

	parentEvidence := []map[string]any{
		{"lit_id": "PMID:1001", "role": "supports", "note": "note1"},
	}
	parentID := addParentHypothesis(t, binPath, runsDir, runID, schemaDir, parentEvidence)

	childEvidence := []map[string]any{
		{"lit_id": "PMID:1001", "role": "supports", "note": "inherited"},
		{"lit_id": "PMID:2001", "role": "supports", "note": "new"},
	}
	child := sampleEvolvedHypothesisData("combine", []string{parentID}, childEvidence)
	payload := mustMarshalIndent(t, child)

	cmd := exec.Command(binPath, "add-hypothesis",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"-",
	)
	cmd.Stdin = bytes.NewReader(payload)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("add-hypothesis failed: %v\n%s", err, out)
	}

	// Now validate the written file using the validate command.
	hypPath := filepath.Join(runsDir, runID, "hypotheses", "H-0002.json")
	cmd = exec.Command(binPath, "validate",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		hypPath,
	)
	out, err = cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("validate should pass for hypothesis with citation_delta: %v\n%s", err, out)
	}
}
