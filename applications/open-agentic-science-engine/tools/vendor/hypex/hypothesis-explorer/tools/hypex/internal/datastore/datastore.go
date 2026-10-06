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

// Package datastore implements the file-based hypothesis-explorer datastore
// operations: run lifecycle, atomic file writes, and concurrent-safe ID
// allocation via filesystem locking.
package datastore

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// RunSubdirs is the list of subdirectories created inside each run.
var RunSubdirs = []string{
	"hypotheses",
	"reviews",
	"matches",
	"ratings",
	"proximity",
	"meta",
	"quarantine",
	"report",
	"citations",
}

// ConfinePath validates that the joined path of baseDir and untrusted
// stays within baseDir. Returns the cleaned absolute path or an error
// if the path escapes.
func ConfinePath(baseDir, untrusted string) (string, error) {
	// Reject absolute untrusted paths outright — a run ID should never be
	// an absolute path regardless of how filepath.Join would handle it.
	if filepath.IsAbs(untrusted) {
		return "", fmt.Errorf("path %q escapes base directory %q", untrusted, baseDir)
	}
	base := filepath.Clean(baseDir)
	joined := filepath.Join(base, untrusted)
	cleaned := filepath.Clean(joined)
	// Ensure cleaned path is within base directory.
	// Add separator to avoid prefix false positives (e.g., /tmp/runs vs /tmp/runs-evil).
	if cleaned != base && !strings.HasPrefix(cleaned+string(os.PathSeparator), base+string(os.PathSeparator)) {
		return "", fmt.Errorf("path %q escapes base directory %q", untrusted, baseDir)
	}
	return cleaned, nil
}

// RunPath returns the full path to a run directory.
func RunPath(baseDir, runID string) (string, error) {
	return ConfinePath(baseDir, runID)
}

// InitRun creates the run directory structure and writes run.yaml.
func InitRun(baseDir, runID, runYAML string) error {
	runDir, err := RunPath(baseDir, runID)
	if err != nil {
		return fmt.Errorf("invalid run ID: %w", err)
	}

	if _, err := os.Stat(runDir); err == nil {
		return fmt.Errorf("run directory already exists: %s", runDir)
	}

	if err := os.MkdirAll(runDir, 0o755); err != nil {
		return fmt.Errorf("creating run directory: %w", err)
	}

	for _, sub := range RunSubdirs {
		if err := os.MkdirAll(filepath.Join(runDir, sub), 0o755); err != nil {
			return fmt.Errorf("creating subdirectory %s: %w", sub, err)
		}
	}

	// Write run.yaml atomically.
	if err := WriteFileAtomic(filepath.Join(runDir, "run.yaml"), []byte(runYAML)); err != nil {
		return fmt.Errorf("writing run.yaml: %w", err)
	}

	return nil
}

// WriteFileAtomic writes data to a file using the temp-file-then-rename
// pattern to ensure atomicity. The file is either fully written or not
// present — never partially written.
func WriteFileAtomic(path string, data []byte) error {
	dir := filepath.Dir(path)

	// Create a temp file in the same directory so rename is atomic
	// (same filesystem).
	tmp, err := os.CreateTemp(dir, ".hypex-tmp-*")
	if err != nil {
		return fmt.Errorf("creating temp file: %w", err)
	}
	tmpName := tmp.Name()

	// Clean up the temp file on any error path.
	success := false
	defer func() {
		if !success {
			os.Remove(tmpName)
		}
	}()

	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return fmt.Errorf("writing temp file: %w", err)
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return fmt.Errorf("syncing temp file: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("closing temp file: %w", err)
	}

	if err := os.Rename(tmpName, path); err != nil {
		return fmt.Errorf("renaming temp file to %s: %w", path, err)
	}

	success = true
	return nil
}
