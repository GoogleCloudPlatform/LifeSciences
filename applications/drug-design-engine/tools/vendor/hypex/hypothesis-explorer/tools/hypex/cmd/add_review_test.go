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
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"

	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/hypex/internal/schema"
)

func TestAddReview_ValidFile(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-review"
	initTestRun(t, binPath, runsDir, runID)

	reviewData := sampleReviewData()
	reviewFile := filepath.Join(t.TempDir(), "review.json")
	if err := os.WriteFile(reviewFile, mustMarshalIndent(t, reviewData), 0o644); err != nil {
		t.Fatal(err)
	}

	cmd := exec.Command(binPath, "add-review",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"--for", "H-0042",
		reviewFile,
	)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("add-review failed: %v\n%s", err, out)
	}

	outStr := string(out)
	if !strings.Contains(outStr, "Added review: H-0042.R-01") {
		t.Errorf("output missing expected review ID: %s", outStr)
	}

	reviewPath := filepath.Join(runsDir, runID, "reviews", "H-0042.R-01.json")
	if _, err := os.Stat(reviewPath); err != nil {
		t.Fatalf("expected review file to exist: %v", err)
	}

	// Validate file against schema.
	if err := schema.ValidateFileAgainstSchema(schemaDir, "review.schema.json", reviewPath); err != nil {
		t.Errorf("written review file failed schema validation: %v", err)
	}

	// Verify ID and hypothesis_id.
	data, err := os.ReadFile(reviewPath)
	if err != nil {
		t.Fatal(err)
	}
	var parsed map[string]any
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatal(err)
	}
	if parsed["id"] != "H-0042.R-01" {
		t.Errorf("got id %q, want H-0042.R-01", parsed["id"])
	}
	if parsed["hypothesis_id"] != "H-0042" {
		t.Errorf("got hypothesis_id %q, want H-0042", parsed["hypothesis_id"])
	}
}

func TestAddReview_Stdin(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-review-stdin"
	initTestRun(t, binPath, runsDir, runID)

	reviewData := sampleReviewData()
	payload := mustMarshalIndent(t, reviewData)

	cmd := exec.Command(binPath, "add-review",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"--for", "H-0042",
		"-",
	)
	cmd.Stdin = bytes.NewReader(payload)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("add-review from stdin failed: %v\n%s", err, out)
	}

	if !strings.Contains(string(out), "Added review: H-0042.R-01") {
		t.Errorf("unexpected output: %s", out)
	}

	reviewPath := filepath.Join(runsDir, runID, "reviews", "H-0042.R-01.json")
	if _, err := os.Stat(reviewPath); err != nil {
		t.Fatalf("expected review file to exist: %v", err)
	}
}

func TestAddReview_MissingForFlag(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-review-no-for"
	initTestRun(t, binPath, runsDir, runID)

	reviewFile := filepath.Join(t.TempDir(), "review.json")
	if err := os.WriteFile(reviewFile, mustMarshalIndent(t, sampleReviewData()), 0o644); err != nil {
		t.Fatal(err)
	}

	cmd := exec.Command(binPath, "add-review",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		reviewFile,
	)
	out, err := cmd.CombinedOutput()
	if err == nil {
		t.Fatalf("expected failure when --for is missing, got exit 0: %s", out)
	}
	if !strings.Contains(string(out), "--for <hypothesis-id> is required") {
		t.Errorf("expected error containing '--for <hypothesis-id> is required', got: %s", out)
	}
}

func TestAddReview_InvalidHypothesisID(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-review-bad-id"
	initTestRun(t, binPath, runsDir, runID)

	reviewFile := filepath.Join(t.TempDir(), "review.json")
	if err := os.WriteFile(reviewFile, mustMarshalIndent(t, sampleReviewData()), 0o644); err != nil {
		t.Fatal(err)
	}

	cmd := exec.Command(binPath, "add-review",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"--for", "bad-id",
		reviewFile,
	)
	out, err := cmd.CombinedOutput()
	if err == nil {
		t.Fatalf("expected failure with bad hypothesis ID, got exit 0: %s", out)
	}
	if !strings.Contains(string(out), "invalid hypothesis ID") {
		t.Errorf("expected error containing 'invalid hypothesis ID', got: %s", out)
	}
}

func TestAddReview_MismatchedHypothesisID(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-review-mismatch"
	initTestRun(t, binPath, runsDir, runID)

	reviewData := sampleReviewData()
	reviewData["hypothesis_id"] = "H-0001" // differs from --for H-0042
	reviewFile := filepath.Join(t.TempDir(), "review.json")
	if err := os.WriteFile(reviewFile, mustMarshalIndent(t, reviewData), 0o644); err != nil {
		t.Fatal(err)
	}

	cmd := exec.Command(binPath, "add-review",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"--for", "H-0042",
		reviewFile,
	)
	out, err := cmd.CombinedOutput()
	if err == nil {
		t.Fatalf("expected failure with mismatched hypothesis ID, got exit 0: %s", out)
	}
	if !strings.Contains(string(out), "does not match --for flag") {
		t.Errorf("expected error containing 'does not match --for flag', got: %s", out)
	}
}

func TestAddReview_MissingHypothesisIDPopulated(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-review-missing-hid"
	initTestRun(t, binPath, runsDir, runID)

	reviewData := sampleReviewData()
	delete(reviewData, "hypothesis_id") // omitted in input, should be set from --for
	reviewFile := filepath.Join(t.TempDir(), "review.json")
	if err := os.WriteFile(reviewFile, mustMarshalIndent(t, reviewData), 0o644); err != nil {
		t.Fatal(err)
	}

	cmd := exec.Command(binPath, "add-review",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"--for", "H-0042",
		reviewFile,
	)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("expected success with missing hypothesis_id in input, got: %v\n%s", err, out)
	}

	reviewPath := filepath.Join(runsDir, runID, "reviews", "H-0042.R-01.json")
	if err := schema.ValidateFileAgainstSchema(schemaDir, "review.schema.json", reviewPath); err != nil {
		t.Errorf("written review failed schema validation: %v", err)
	}
}

func TestAddReview_InvalidSchemaRejectedNoFileWritten(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-review-invalid"
	initTestRun(t, binPath, runsDir, runID)

	// Invalid scores: correctness is 10 (allowed is 1-5).
	invalidReview := sampleReviewData()
	invalidReview["scores"] = map[string]any{
		"correctness": 10,
		"novelty":     3,
		"testability": 5,
		"safety":      5,
	}

	reviewFile := filepath.Join(t.TempDir(), "invalid_review.json")
	if err := os.WriteFile(reviewFile, mustMarshalIndent(t, invalidReview), 0o644); err != nil {
		t.Fatal(err)
	}

	cmd := exec.Command(binPath, "add-review",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"--for", "H-0042",
		reviewFile,
	)
	out, err := cmd.CombinedOutput()
	if err == nil {
		t.Fatalf("expected add-review with invalid score to fail, but exited 0: %s", out)
	}

	if !strings.Contains(string(out), "schema validation failed") {
		t.Errorf("expected error message to contain 'schema validation failed', got: %s", out)
	}

	// Verify NO files were written to reviews/.
	reviewsDir := filepath.Join(runsDir, runID, "reviews")
	entries, err := os.ReadDir(reviewsDir)
	if err != nil && !os.IsNotExist(err) {
		t.Fatal(err)
	}
	jsonFiles := 0
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".json") {
			jsonFiles++
		}
	}
	if jsonFiles != 0 {
		t.Errorf("expected 0 review files after rejected validation, found %d", jsonFiles)
	}
}

func TestAddReview_ConcurrentProcesses(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-review-concurrent"
	initTestRun(t, binPath, runsDir, runID)

	const nPerHypo = 10
	hypotheses := []string{"H-0042", "H-0043"}
	total := len(hypotheses) * nPerHypo

	var wg sync.WaitGroup
	type result struct {
		output string
		err    error
	}
	results := make(chan result, total)

	for _, hID := range hypotheses {
		reviewData := sampleReviewData()
		reviewData["hypothesis_id"] = hID
		payload := mustMarshalIndent(t, reviewData)

		for i := 0; i < nPerHypo; i++ {
			wg.Add(1)
			go func(targetID string, idx int) {
				defer wg.Done()
				cmd := exec.Command(binPath, "add-review",
					"--run-dir", runsDir,
					"--run", runID,
					"--schema-dir", schemaDir,
					"--for", targetID,
					"-",
				)
				cmd.Stdin = bytes.NewReader(payload)
				out, err := cmd.CombinedOutput()
				if err != nil {
					results <- result{err: fmt.Errorf("proc %s-%d: %v\n%s", targetID, idx, err, out)}
					return
				}
				results <- result{output: strings.TrimSpace(string(out))}
			}(hID, i)
		}
	}

	wg.Wait()
	close(results)

	for r := range results {
		if r.err != nil {
			t.Fatalf("concurrent process error: %v", r.err)
		}
	}

	reviewsDir := filepath.Join(runsDir, runID, "reviews")
	entries, err := os.ReadDir(reviewsDir)
	if err != nil {
		t.Fatal(err)
	}

	var reviewFiles []string
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".json") && strings.HasPrefix(e.Name(), "H-") {
			reviewFiles = append(reviewFiles, e.Name())
		}
	}

	if len(reviewFiles) != total {
		t.Fatalf("expected %d review files, got %d: %v", total, len(reviewFiles), reviewFiles)
	}

	for _, hID := range hypotheses {
		var scoped []string
		for _, f := range reviewFiles {
			if strings.HasPrefix(f, hID) {
				scoped = append(scoped, f)
			}
		}
		if len(scoped) != nPerHypo {
			t.Errorf("expected %d reviews for %s, got %d", nPerHypo, hID, len(scoped))
		}
		sort.Strings(scoped)
		for i, f := range scoped {
			expectedName := fmt.Sprintf("%s.R-%02d.json", hID, i+1)
			if f != expectedName {
				t.Errorf("file #%d for %s: got %s, want %s", i+1, hID, f, expectedName)
			}
			filePath := filepath.Join(reviewsDir, f)
			if err := schema.ValidateFileAgainstSchema(schemaDir, "review.schema.json", filePath); err != nil {
				t.Errorf("file %s failed schema validation: %v", f, err)
			}
		}
	}
}
