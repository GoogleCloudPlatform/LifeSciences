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

package datastore

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"
)

func TestNextID_Sequential(t *testing.T) {
	dir := t.TempDir()
	for _, sub := range RunSubdirs {
		os.MkdirAll(filepath.Join(dir, sub), 0o755)
	}

	// Allocate 10 IDs sequentially and verify uniqueness and ordering.
	seen := make(map[string]bool)
	for i := 1; i <= 10; i++ {
		id, num, err := NextID(dir, ArtifactHypothesis)
		if err != nil {
			t.Fatalf("NextID #%d: %v", i, err)
		}
		expected := fmt.Sprintf("H-%04d", i)
		if id != expected {
			t.Errorf("NextID #%d: got %q, want %q", i, id, expected)
		}
		if num != i {
			t.Errorf("NextID #%d: got num %d, want %d", i, num, i)
		}
		if seen[id] {
			t.Fatalf("duplicate ID: %s", id)
		}
		seen[id] = true
	}
}

func TestNextID_MultipleTypes(t *testing.T) {
	dir := t.TempDir()
	for _, sub := range RunSubdirs {
		os.MkdirAll(filepath.Join(dir, sub), 0o755)
	}

	// Each type has its own counter.
	hID, _, err := NextID(dir, ArtifactHypothesis)
	if err != nil {
		t.Fatal(err)
	}
	mID, _, err := NextID(dir, ArtifactMatch)
	if err != nil {
		t.Fatal(err)
	}
	hID2, _, err := NextID(dir, ArtifactHypothesis)
	if err != nil {
		t.Fatal(err)
	}

	if hID != "H-0001" {
		t.Errorf("first hypothesis ID: got %q, want H-0001", hID)
	}
	if mID != "M-0001" {
		t.Errorf("first match ID: got %q, want M-0001", mID)
	}
	if hID2 != "H-0002" {
		t.Errorf("second hypothesis ID: got %q, want H-0002", hID2)
	}
}

func TestNextID_ConcurrentGoroutines(t *testing.T) {
	dir := t.TempDir()
	for _, sub := range RunSubdirs {
		os.MkdirAll(filepath.Join(dir, sub), 0o755)
	}

	const n = 50
	var wg sync.WaitGroup
	ids := make(chan string, n)

	for i := 0; i < n; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			id, _, err := NextID(dir, ArtifactHypothesis)
			if err != nil {
				t.Errorf("NextID error: %v", err)
				return
			}
			ids <- id
		}()
	}

	wg.Wait()
	close(ids)

	seen := make(map[string]bool)
	for id := range ids {
		if seen[id] {
			t.Fatalf("DUPLICATE ID from concurrent goroutines: %s", id)
		}
		seen[id] = true
	}

	if len(seen) != n {
		t.Errorf("expected %d unique IDs, got %d", n, len(seen))
	}
}

// TestNextID_ConcurrentProcesses is the acceptance test: it spawns multiple
// separate OS processes that each call NextID via the hypex binary, and
// verifies that no two processes ever receive the same ID.
func TestNextID_ConcurrentProcesses(t *testing.T) {
	// Build the hypex binary for the test.
	binDir := t.TempDir()
	binPath := filepath.Join(binDir, "hypex")
	buildCmd := exec.Command("go", "build", "-o", binPath, "github.com/scion-frontiers/hypex/hypothesis-explorer/tools/hypex")
	buildCmd.Dir = filepath.Join(findModuleRoot(t), "..", "..", "..", "..")
	// Actually, let's build from the module root.
	modRoot := findModuleRoot(t)
	buildCmd = exec.Command("go", "build", "-o", binPath, ".")
	buildCmd.Dir = modRoot
	out, err := buildCmd.CombinedOutput()
	if err != nil {
		t.Fatalf("building hypex: %v\n%s", err, out)
	}

	// Create a temporary run directory.
	runsDir := t.TempDir()
	runID := "concurrent-test"
	initCmd := exec.Command(binPath, "init-run", "--run-dir", runsDir, "--goal", "concurrency test", runID)
	out, err = initCmd.CombinedOutput()
	if err != nil {
		t.Fatalf("init-run: %v\n%s", err, out)
	}

	// Spawn N processes concurrently, each requesting a next-id.
	const n = 30
	var wg sync.WaitGroup
	type result struct {
		id  string
		err error
	}
	results := make(chan result, n)

	for i := 0; i < n; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			cmd := exec.Command(binPath, "next-id", "--run-dir", runsDir, "--run", runID, "hypothesis")
			output, err := cmd.CombinedOutput()
			if err != nil {
				results <- result{err: fmt.Errorf("%v: %s", err, output)}
				return
			}
			results <- result{id: strings.TrimSpace(string(output))}
		}()
	}

	wg.Wait()
	close(results)

	seen := make(map[string]bool)
	for r := range results {
		if r.err != nil {
			t.Fatalf("process error: %v", r.err)
		}
		if seen[r.id] {
			t.Fatalf("DUPLICATE ID from concurrent processes: %s", r.id)
		}
		seen[r.id] = true
	}

	if len(seen) != n {
		t.Errorf("expected %d unique IDs, got %d", n, len(seen))
	}

	t.Logf("All %d concurrent process IDs are unique: %v", n, sortedKeys(seen))
}

func TestValidateHypothesisID(t *testing.T) {
	valid := []string{"H-0001", "H-0042", "H-9999"}
	for _, id := range valid {
		if err := ValidateHypothesisID(id); err != nil {
			t.Errorf("ValidateHypothesisID(%q) unexpected error: %v", id, err)
		}
	}

	invalid := []string{
		"",
		"H-1",
		"H-01",
		"H-001",
		"H-00001",
		"h-0001",
		"H-abcd",
		"H-001a",
		"review",
		"H-0001.R-01",
		"../H-0001",
		"H-0001/foo",
	}
	for _, id := range invalid {
		if err := ValidateHypothesisID(id); err == nil {
			t.Errorf("ValidateHypothesisID(%q) expected error, got nil", id)
		}
	}
}

func TestNextReviewID_Sequential(t *testing.T) {
	dir := t.TempDir()
	for _, sub := range RunSubdirs {
		os.MkdirAll(filepath.Join(dir, sub), 0o755)
	}

	seen := make(map[string]bool)
	for i := 1; i <= 10; i++ {
		id, num, err := NextReviewID(dir, "H-0042")
		if err != nil {
			t.Fatalf("NextReviewID #%d: %v", i, err)
		}
		expected := fmt.Sprintf("H-0042.R-%02d", i)
		if id != expected {
			t.Errorf("NextReviewID #%d: got %q, want %q", i, id, expected)
		}
		if num != i {
			t.Errorf("NextReviewID #%d: got num %d, want %d", i, num, i)
		}
		if seen[id] {
			t.Fatalf("duplicate review ID: %s", id)
		}
		seen[id] = true
	}
}

func TestNextReviewID_PerHypothesisCountersIndependent(t *testing.T) {
	dir := t.TempDir()
	for _, sub := range RunSubdirs {
		os.MkdirAll(filepath.Join(dir, sub), 0o755)
	}

	// Allocations for H-0001 and H-0002 should each start at 1 and advance independently.
	r1_1, num1, err := NextReviewID(dir, "H-0001")
	if err != nil {
		t.Fatal(err)
	}
	r2_1, num2, err := NextReviewID(dir, "H-0002")
	if err != nil {
		t.Fatal(err)
	}
	r1_2, num3, err := NextReviewID(dir, "H-0001")
	if err != nil {
		t.Fatal(err)
	}
	r2_2, num4, err := NextReviewID(dir, "H-0002")
	if err != nil {
		t.Fatal(err)
	}

	if r1_1 != "H-0001.R-01" || num1 != 1 {
		t.Errorf("r1_1: got %s (%d), want H-0001.R-01 (1)", r1_1, num1)
	}
	if r2_1 != "H-0002.R-01" || num2 != 1 {
		t.Errorf("r2_1: got %s (%d), want H-0002.R-01 (1)", r2_1, num2)
	}
	if r1_2 != "H-0001.R-02" || num3 != 2 {
		t.Errorf("r1_2: got %s (%d), want H-0001.R-02 (2)", r1_2, num3)
	}
	if r2_2 != "H-0002.R-02" || num4 != 2 {
		t.Errorf("r2_2: got %s (%d), want H-0002.R-02 (2)", r2_2, num4)
	}

	// Verify counter files exist with the expected names.
	cf1 := filepath.Join(dir, ".counter-review-H-0001")
	cf2 := filepath.Join(dir, ".counter-review-H-0002")
	if _, err := os.Stat(cf1); err != nil {
		t.Errorf("counter file %s missing: %v", cf1, err)
	}
	if _, err := os.Stat(cf2); err != nil {
		t.Errorf("counter file %s missing: %v", cf2, err)
	}
}

func TestNextID_ReviewRequiresHypothesisID(t *testing.T) {
	dir := t.TempDir()
	for _, sub := range RunSubdirs {
		os.MkdirAll(filepath.Join(dir, sub), 0o755)
	}

	_, _, err := NextID(dir, ArtifactReview)
	if err == nil {
		t.Fatal("NextID with ArtifactReview should return error, got nil")
	}
	if !strings.Contains(err.Error(), "NextReviewID") {
		t.Errorf("expected error mentioning NextReviewID, got %v", err)
	}
}

func TestNextID_CountersIndependentAcrossTypes(t *testing.T) {
	dir := t.TempDir()
	for _, sub := range RunSubdirs {
		os.MkdirAll(filepath.Join(dir, sub), 0o755)
	}

	// Interleaved allocations across all types.
	h1, _, err := NextID(dir, ArtifactHypothesis)
	if err != nil {
		t.Fatal(err)
	}
	m1, _, err := NextID(dir, ArtifactMatch)
	if err != nil {
		t.Fatal(err)
	}
	r1, _, err := NextReviewID(dir, "H-0001")
	if err != nil {
		t.Fatal(err)
	}
	h2, _, err := NextID(dir, ArtifactHypothesis)
	if err != nil {
		t.Fatal(err)
	}
	r2, _, err := NextReviewID(dir, "H-0001")
	if err != nil {
		t.Fatal(err)
	}
	m2, _, err := NextID(dir, ArtifactMatch)
	if err != nil {
		t.Fatal(err)
	}

	if h1 != "H-0001" || h2 != "H-0002" {
		t.Errorf("hypothesis IDs: got %s, %s; want H-0001, H-0002", h1, h2)
	}
	if m1 != "M-0001" || m2 != "M-0002" {
		t.Errorf("match IDs: got %s, %s; want M-0001, M-0002", m1, m2)
	}
	if r1 != "H-0001.R-01" || r2 != "H-0001.R-02" {
		t.Errorf("review IDs: got %s, %s; want H-0001.R-01, H-0001.R-02", r1, r2)
	}
}

func TestNextReviewID_ConcurrentGoroutines(t *testing.T) {
	dir := t.TempDir()
	for _, sub := range RunSubdirs {
		os.MkdirAll(filepath.Join(dir, sub), 0o755)
	}

	const n = 50
	var wg sync.WaitGroup
	ids := make(chan string, n)

	for i := 0; i < n; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			id, _, err := NextReviewID(dir, "H-0042")
			if err != nil {
				t.Errorf("NextReviewID error: %v", err)
				return
			}
			ids <- id
		}()
	}

	wg.Wait()
	close(ids)

	seen := make(map[string]bool)
	for id := range ids {
		if seen[id] {
			t.Fatalf("DUPLICATE review ID from concurrent goroutines: %s", id)
		}
		seen[id] = true
	}

	if len(seen) != n {
		t.Errorf("expected %d unique IDs, got %d", n, len(seen))
	}
}

func TestNextReviewID_ConcurrentProcesses(t *testing.T) {
	binDir := t.TempDir()
	binPath := filepath.Join(binDir, "hypex")
	modRoot := findModuleRoot(t)
	buildCmd := exec.Command("go", "build", "-o", binPath, ".")
	buildCmd.Dir = modRoot
	out, err := buildCmd.CombinedOutput()
	if err != nil {
		t.Fatalf("building hypex: %v\n%s", err, out)
	}

	runsDir := t.TempDir()
	runID := "concurrent-review-test"
	initCmd := exec.Command(binPath, "init-run", "--run-dir", runsDir, "--goal", "concurrency test", runID)
	out, err = initCmd.CombinedOutput()
	if err != nil {
		t.Fatalf("init-run: %v\n%s", err, out)
	}

	const n = 30
	var wg sync.WaitGroup
	type result struct {
		id  string
		err error
	}
	results := make(chan result, n)

	for i := 0; i < n; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			cmd := exec.Command(binPath, "next-id", "--run-dir", runsDir, "--run", runID, "review", "--for", "H-0042")
			output, err := cmd.CombinedOutput()
			if err != nil {
				results <- result{err: fmt.Errorf("%v: %s", err, output)}
				return
			}
			results <- result{id: strings.TrimSpace(string(output))}
		}()
	}

	wg.Wait()
	close(results)

	seen := make(map[string]bool)
	for r := range results {
		if r.err != nil {
			t.Fatalf("process error: %v", r.err)
		}
		if seen[r.id] {
			t.Fatalf("DUPLICATE review ID from concurrent processes: %s", r.id)
		}
		seen[r.id] = true
	}

	if len(seen) != n {
		t.Errorf("expected %d unique IDs, got %d", n, len(seen))
	}

	t.Logf("All %d concurrent review process IDs are unique: %v", n, sortedKeys(seen))
}

func TestNextID_CLI_FlagValidation(t *testing.T) {
	binDir := t.TempDir()
	binPath := filepath.Join(binDir, "hypex")
	modRoot := findModuleRoot(t)
	buildCmd := exec.Command("go", "build", "-o", binPath, ".")
	buildCmd.Dir = modRoot
	out, err := buildCmd.CombinedOutput()
	if err != nil {
		t.Fatalf("building hypex: %v\n%s", err, out)
	}

	runsDir := t.TempDir()
	runID := "cli-val-test"
	initCmd := exec.Command(binPath, "init-run", "--run-dir", runsDir, "--goal", "cli val test", runID)
	out, err = initCmd.CombinedOutput()
	if err != nil {
		t.Fatalf("init-run: %v\n%s", err, out)
	}

	tests := []struct {
		name       string
		args       []string
		wantExit0  bool
		wantOutput string
		wantErrSub string
	}{
		{
			name:       "review without --for flag",
			args:       []string{"next-id", "--run-dir", runsDir, "--run", runID, "review"},
			wantExit0:  false,
			wantErrSub: "--for <hypothesis-id> is required for review artifact type",
		},
		{
			name:       "review with malformed hypothesis ID",
			args:       []string{"next-id", "--run-dir", runsDir, "--run", runID, "review", "--for", "bad-id"},
			wantExit0:  false,
			wantErrSub: "invalid hypothesis ID",
		},
		{
			name:       "hypothesis with --for flag",
			args:       []string{"next-id", "--run-dir", runsDir, "--run", runID, "hypothesis", "--for", "H-0001"},
			wantExit0:  false,
			wantErrSub: "--for is only valid for review artifact type",
		},
		{
			name:       "match with --for flag",
			args:       []string{"next-id", "--run-dir", runsDir, "--run", runID, "match", "--for", "H-0001"},
			wantExit0:  false,
			wantErrSub: "--for is only valid for review artifact type",
		},
		{
			name:       "review with valid --for flag",
			args:       []string{"next-id", "--run-dir", runsDir, "--run", runID, "review", "--for", "H-0042"},
			wantExit0:  true,
			wantOutput: "H-0042.R-01",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			cmd := exec.Command(binPath, tc.args...)
			output, err := cmd.CombinedOutput()
			outStr := strings.TrimSpace(string(output))
			if tc.wantExit0 {
				if err != nil {
					t.Fatalf("expected success, got error %v: %s", err, outStr)
				}
				if outStr != tc.wantOutput {
					t.Errorf("got output %q, want %q", outStr, tc.wantOutput)
				}
			} else {
				if err == nil {
					t.Fatalf("expected failure, got exit code 0: %s", outStr)
				}
				if !strings.Contains(outStr, tc.wantErrSub) {
					t.Errorf("expected error containing %q, got: %s", tc.wantErrSub, outStr)
				}
			}
		})
	}
}

func findModuleRoot(t *testing.T) string {
	t.Helper()
	// Walk up from this file to find go.mod.
	dir, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Fatal("could not find module root")
		}
		dir = parent
	}
}

func sortedKeys(m map[string]bool) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}
