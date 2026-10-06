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
	"os"
	"path/filepath"
	"testing"
)

func TestInitRun_CreatesAllSubdirectoriesIncludingCitations(t *testing.T) {
	baseDir := t.TempDir()
	runID := "test-run-citations"
	runYAML := "# Test Run Configuration\ngoal: test citation directory initialization\n"

	err := InitRun(baseDir, runID, runYAML)
	if err != nil {
		t.Fatalf("InitRun failed: %v", err)
	}

	runDir := filepath.Join(baseDir, runID)

	// Verify all subdirectories in RunSubdirs exist.
	for _, sub := range RunSubdirs {
		subPath := filepath.Join(runDir, sub)
		info, err := os.Stat(subPath)
		if err != nil {
			t.Errorf("expected subdirectory %q to exist at %s: %v", sub, subPath, err)
			continue
		}
		if !info.IsDir() {
			t.Errorf("expected %s to be a directory", subPath)
		}
	}

	// Specifically verify citations/ exists.
	citationsPath := filepath.Join(runDir, "citations")
	info, err := os.Stat(citationsPath)
	if err != nil {
		t.Fatalf("citations directory missing: %v", err)
	}
	if !info.IsDir() {
		t.Fatalf("expected citations path %s to be a directory", citationsPath)
	}

	// Verify run.yaml was written.
	yamlPath := filepath.Join(runDir, "run.yaml")
	data, err := os.ReadFile(yamlPath)
	if err != nil {
		t.Fatalf("reading run.yaml: %v", err)
	}
	if string(data) != runYAML {
		t.Errorf("run.yaml content = %q, want %q", string(data), runYAML)
	}
}

func TestInitRun_AlreadyExistsReturnsError(t *testing.T) {
	baseDir := t.TempDir()
	runID := "existing-run"

	if err := InitRun(baseDir, runID, "goal: test\n"); err != nil {
		t.Fatalf("initial InitRun failed: %v", err)
	}

	// Second initialization should fail.
	err := InitRun(baseDir, runID, "goal: duplicate\n")
	if err == nil {
		t.Fatalf("expected error when initializing existing run, got nil")
	}
}

func TestWriteFileAtomic(t *testing.T) {
	dir := t.TempDir()
	targetPath := filepath.Join(dir, "atomic.txt")
	content := []byte("hello atomic world")

	if err := WriteFileAtomic(targetPath, content); err != nil {
		t.Fatalf("WriteFileAtomic failed: %v", err)
	}

	data, err := os.ReadFile(targetPath)
	if err != nil {
		t.Fatalf("reading target file: %v", err)
	}
	if string(data) != string(content) {
		t.Errorf("got content %q, want %q", string(data), string(content))
	}

	// Check that no temporary files were left behind.
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("reading dir: %v", err)
	}
	if len(entries) != 1 {
		t.Errorf("expected 1 file in directory, found %d", len(entries))
	}
}

func TestRunPath(t *testing.T) {
	base := "/tmp/runs"
	id := "run-01"
	got, err := RunPath(base, id)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	want := filepath.Join(base, id)
	if got != want {
		t.Errorf("RunPath(%q, %q) = %q, want %q", base, id, got, want)
	}
}

func TestConfinePath_Valid(t *testing.T) {
	base := t.TempDir()
	got, err := ConfinePath(base, "my-run")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	want := filepath.Join(base, "my-run")
	if got != want {
		t.Errorf("ConfinePath(%q, %q) = %q, want %q", base, "my-run", got, want)
	}
}

func TestConfinePath_TraversalBlocked(t *testing.T) {
	base := t.TempDir()
	_, err := ConfinePath(base, "../../../etc/shadow")
	if err == nil {
		t.Fatal("expected error for traversal path, got nil")
	}
}

func TestConfinePath_AbsolutePathBlocked(t *testing.T) {
	base := t.TempDir()
	_, err := ConfinePath(base, "/etc/passwd")
	if err == nil {
		t.Fatal("expected error for absolute path, got nil")
	}
}

func TestInitRun_TraversalBlocked(t *testing.T) {
	base := t.TempDir()
	err := InitRun(base, "../../escape", "goal: evil\n")
	if err == nil {
		t.Fatal("expected error for traversal runID, got nil")
	}
}
