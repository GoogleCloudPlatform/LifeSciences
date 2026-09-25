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
	"testing"

	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/hypex/internal/datastore"
)

func TestFindArtifactFiles_IncludesCitations(t *testing.T) {
	tmpDir := t.TempDir()
	runID := "test-run-find-artifacts"

	err := datastore.InitRun(tmpDir, runID, "goal: test\n")
	if err != nil {
		t.Fatalf("InitRun failed: %v", err)
	}

	runDir := filepath.Join(tmpDir, runID)

	// Create dummy artifact files in each subdirectory.
	hypoFile := filepath.Join(runDir, "hypotheses", "H-0001.json")
	if err := os.WriteFile(hypoFile, []byte("{}"), 0o644); err != nil {
		t.Fatal(err)
	}

	citFile := filepath.Join(runDir, "citations", "H-0001.json")
	if err := os.WriteFile(citFile, []byte("{}"), 0o644); err != nil {
		t.Fatal(err)
	}

	found, err := findArtifactFiles(runDir)
	if err != nil {
		t.Fatalf("findArtifactFiles failed: %v", err)
	}

	foundMap := make(map[string]bool)
	for _, f := range found {
		foundMap[f] = true
	}

	if !foundMap[hypoFile] {
		t.Errorf("expected findArtifactFiles to include hypothesis file %s", hypoFile)
	}
	if !foundMap[citFile] {
		t.Errorf("expected findArtifactFiles to include citation manifest file %s", citFile)
	}
}

func TestRunValidate_WithCitationsManifest(t *testing.T) {
	tmpDir := t.TempDir()
	runID := "test-validate-run"

	err := datastore.InitRun(tmpDir, runID, "goal: test\n")
	if err != nil {
		t.Fatalf("InitRun failed: %v", err)
	}

	runDir := filepath.Join(tmpDir, runID)
	schemaDir, err := filepath.Abs("../../../schemas")
	if err != nil {
		t.Fatalf("resolving schemas dir: %v", err)
	}

	validManifest := []byte(`{
		"target_id": "H-0001",
		"target_file": "/scion-volumes/executions/run-01/hypotheses/H-0001.json",
		"verified_at": "2026-09-06T12:00:00Z",
		"verifier": "lit-verify/1.0",
		"summary": {
			"total": 1,
			"verified": 1,
			"phantom": 0,
			"unverified": 0,
			"all_verified": true
		},
		"citations": [
			{
				"id": "PMID:42297600",
				"raw_id": "PMID:42297600",
				"source": "pubmed",
				"status": "verified",
				"reason": "ok",
				"claimed_title": "Efficacy and safety of CAR-T cells in refractory SLE",
				"resolved_title": "Efficacy and safety of CAR-T cells in refractory SLE",
				"title_match": "matched",
				"title_similarity": 1.0,
				"verified_via": "ncbi",
				"url": "https://pubmed.ncbi.nlm.nih.gov/42297600/"
			}
		]
	}`)

	manifestPath := filepath.Join(runDir, "citations", "H-0001.json")
	if err := os.WriteFile(manifestPath, validManifest, 0o644); err != nil {
		t.Fatal(err)
	}

	// Reset flags and run validate command for the run.
	flagRunDir = tmpDir
	flagRunID = runID
	flagSchemaDir = schemaDir

	rootCmd.SetArgs([]string{"validate", "--run", runID, "--run-dir", tmpDir, "--schema-dir", schemaDir})
	if err := rootCmd.Execute(); err != nil {
		t.Errorf("validate --run failed: %v", err)
	}
}
