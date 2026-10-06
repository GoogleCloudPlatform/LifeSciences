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

func TestAddMatch_ValidFile(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-match"
	initTestRun(t, binPath, runsDir, runID)

	matchData := sampleMatchData()
	matchFile := filepath.Join(t.TempDir(), "match.json")
	if err := os.WriteFile(matchFile, mustMarshalIndent(t, matchData), 0o644); err != nil {
		t.Fatal(err)
	}

	cmd := exec.Command(binPath, "add-match",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		matchFile,
	)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("add-match failed: %v\n%s", err, out)
	}

	outStr := string(out)
	if !strings.Contains(outStr, "Added match: M-0001") {
		t.Errorf("output missing expected string: %s", outStr)
	}

	matchPath := filepath.Join(runsDir, runID, "matches", "M-0001.json")
	if _, err := os.Stat(matchPath); err != nil {
		t.Fatalf("expected match file to exist: %v", err)
	}

	// Validate file against schema.
	if err := schema.ValidateFileAgainstSchema(schemaDir, "match.schema.json", matchPath); err != nil {
		t.Errorf("written match file failed schema validation: %v", err)
	}

	// Verify ID field is M-0001.
	data, err := os.ReadFile(matchPath)
	if err != nil {
		t.Fatal(err)
	}
	var parsed map[string]any
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatal(err)
	}
	if parsed["id"] != "M-0001" {
		t.Errorf("got id %q, want M-0001", parsed["id"])
	}
}

func TestAddMatch_Stdin(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-match-stdin"
	initTestRun(t, binPath, runsDir, runID)

	matchData := sampleMatchData()
	payload := mustMarshalIndent(t, matchData)

	cmd := exec.Command(binPath, "add-match",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		"-",
	)
	cmd.Stdin = bytes.NewReader(payload)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("add-match from stdin failed: %v\n%s", err, out)
	}

	if !strings.Contains(string(out), "Added match: M-0001") {
		t.Errorf("unexpected output: %s", out)
	}

	matchPath := filepath.Join(runsDir, runID, "matches", "M-0001.json")
	if _, err := os.Stat(matchPath); err != nil {
		t.Fatalf("expected match file to exist: %v", err)
	}
}

func TestAddMatch_OverwritesExistingID(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-match-overwrite-id"
	initTestRun(t, binPath, runsDir, runID)

	matchData := sampleMatchData()
	matchData["id"] = "M-9999" // should be overwritten by allocator

	matchFile := filepath.Join(t.TempDir(), "match.json")
	if err := os.WriteFile(matchFile, mustMarshalIndent(t, matchData), 0o644); err != nil {
		t.Fatal(err)
	}

	cmd := exec.Command(binPath, "add-match",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		matchFile,
	)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("add-match failed: %v\n%s", err, out)
	}

	// Verify M-9999 was NOT written, and M-0001 WAS written.
	badPath := filepath.Join(runsDir, runID, "matches", "M-9999.json")
	if _, err := os.Stat(badPath); err == nil {
		t.Errorf("file with unallocated ID M-9999 should not exist")
	}

	goodPath := filepath.Join(runsDir, runID, "matches", "M-0001.json")
	data, err := os.ReadFile(goodPath)
	if err != nil {
		t.Fatalf("reading allocated match file: %v", err)
	}
	var parsed map[string]any
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatal(err)
	}
	if parsed["id"] != "M-0001" {
		t.Errorf("got id %q, want M-0001", parsed["id"])
	}
}

func TestAddMatch_InvalidSchemaRejectedNoFileWritten(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-match-invalid"
	initTestRun(t, binPath, runsDir, runID)

	// Missing required field "judge".
	invalidMatch := sampleMatchData()
	delete(invalidMatch, "judge")

	matchFile := filepath.Join(t.TempDir(), "invalid_match.json")
	if err := os.WriteFile(matchFile, mustMarshalIndent(t, invalidMatch), 0o644); err != nil {
		t.Fatal(err)
	}

	cmd := exec.Command(binPath, "add-match",
		"--run-dir", runsDir,
		"--run", runID,
		"--schema-dir", schemaDir,
		matchFile,
	)
	out, err := cmd.CombinedOutput()
	if err == nil {
		t.Fatalf("expected add-match with invalid schema to fail, but exited 0: %s", out)
	}

	if !strings.Contains(string(out), "schema validation failed") {
		t.Errorf("expected error message to contain 'schema validation failed', got: %s", out)
	}

	// Verify NO files were written to matches/.
	matchesDir := filepath.Join(runsDir, runID, "matches")
	entries, err := os.ReadDir(matchesDir)
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
		t.Errorf("expected 0 match files after rejected validation, found %d", jsonFiles)
	}
}

func TestAddMatch_MissingRunFlag(t *testing.T) {
	binPath := getHypexBinary(t)
	matchFile := filepath.Join(t.TempDir(), "match.json")
	if err := os.WriteFile(matchFile, mustMarshalIndent(t, sampleMatchData()), 0o644); err != nil {
		t.Fatal(err)
	}

	cmd := exec.Command(binPath, "add-match", matchFile)
	out, err := cmd.CombinedOutput()
	if err == nil {
		t.Fatalf("expected failure when --run is missing, got exit 0: %s", out)
	}
	if !strings.Contains(string(out), "--run flag is required") {
		t.Errorf("expected error containing '--run flag is required', got: %s", out)
	}
}

func TestAddMatch_ConcurrentProcesses(t *testing.T) {
	binPath := getHypexBinary(t)
	schemaDir := findSchemaDir(t)
	runsDir := t.TempDir()
	runID := "test-add-match-concurrent"
	initTestRun(t, binPath, runsDir, runID)

	const n = 20
	var wg sync.WaitGroup
	type result struct {
		output string
		err    error
	}
	results := make(chan result, n)

	matchData := sampleMatchData()
	payload := mustMarshalIndent(t, matchData)

	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			cmd := exec.Command(binPath, "add-match",
				"--run-dir", runsDir,
				"--run", runID,
				"--schema-dir", schemaDir,
				"-",
			)
			cmd.Stdin = bytes.NewReader(payload)
			out, err := cmd.CombinedOutput()
			if err != nil {
				results <- result{err: fmt.Errorf("proc %d: %v\n%s", idx, err, out)}
				return
			}
			results <- result{output: strings.TrimSpace(string(out))}
		}(i)
	}

	wg.Wait()
	close(results)

	for r := range results {
		if r.err != nil {
			t.Fatalf("concurrent process error: %v", r.err)
		}
	}

	// Verify all N files exist and are valid.
	matchesDir := filepath.Join(runsDir, runID, "matches")
	entries, err := os.ReadDir(matchesDir)
	if err != nil {
		t.Fatal(err)
	}

	var matchFiles []string
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".json") && strings.HasPrefix(e.Name(), "M-") {
			matchFiles = append(matchFiles, e.Name())
		}
	}

	if len(matchFiles) != n {
		t.Fatalf("expected %d match files, got %d: %v", n, len(matchFiles), matchFiles)
	}

	sort.Strings(matchFiles)
	for i, f := range matchFiles {
		expectedName := fmt.Sprintf("M-%04d.json", i+1)
		if f != expectedName {
			t.Errorf("file #%d: got %s, want %s", i+1, f, expectedName)
		}
		filePath := filepath.Join(matchesDir, f)
		if err := schema.ValidateFileAgainstSchema(schemaDir, "match.schema.json", filePath); err != nil {
			t.Errorf("file %s failed schema validation: %v", f, err)
		}
	}
}
