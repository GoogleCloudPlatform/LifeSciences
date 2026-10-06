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
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"testing"
)

var (
	builtBinPath string
	buildOnce    sync.Once
	buildErr     error
)

func findSchemaDir(t *testing.T) string {
	t.Helper()
	candidates := []string{
		filepath.Join("..", "..", "..", "schemas"),
		filepath.Join("..", "..", "schemas"),
		filepath.Join("hypothesis-explorer", "schemas"),
	}
	for _, c := range candidates {
		if _, err := os.Stat(filepath.Join(c, "match.schema.json")); err == nil {
			abs, err := filepath.Abs(c)
			if err == nil {
				return abs
			}
			return c
		}
	}
	t.Fatal("could not locate schemas directory")
	return ""
}

func findModuleRoot(t *testing.T) string {
	t.Helper()
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

func getHypexBinary(t *testing.T) string {
	t.Helper()
	buildOnce.Do(func() {
		binDir, err := os.MkdirTemp("", "hypex-bin-*")
		if err != nil {
			buildErr = err
			return
		}
		binPath := filepath.Join(binDir, "hypex")
		modRoot := findModuleRoot(t)
		cmd := exec.Command("go", "build", "-o", binPath, ".")
		cmd.Dir = modRoot
		out, err := cmd.CombinedOutput()
		if err != nil {
			buildErr = fmt.Errorf("building hypex: %v\n%s", err, out)
			return
		}
		builtBinPath = binPath
	})
	if buildErr != nil {
		t.Fatalf("getHypexBinary: %v", buildErr)
	}
	return builtBinPath
}

func initTestRun(t *testing.T, binPath, runsDir, runID string) {
	t.Helper()
	cmd := exec.Command(binPath, "init-run", "--run-dir", runsDir, "--goal", "test run", runID)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("init-run: %v\n%s", err, out)
	}
}

func sampleMatchData() map[string]any {
	return map[string]any{
		"epoch":  0,
		"a":      "H-0001",
		"b":      "H-0002",
		"format": "single-turn",
		"winner": "H-0001",
		"margin": "decisive",
		"criterion_scores": map[string]any{
			"novelty":      map[string]any{"a": 4, "b": 3},
			"plausibility": map[string]any{"a": 5, "b": 4},
			"testability":  map[string]any{"a": 4, "b": 3},
		},
		"rationale":  "Contestant A proposed a more testable and plausible mechanism.",
		"judge":      "ranking-agent-1",
		"created_at": "2026-09-06T12:00:00Z",
	}
}

func sampleReviewData() map[string]any {
	return map[string]any{
		"hypothesis_id": "H-0042",
		"review_type":   "full",
		"scores": map[string]any{
			"correctness": 4,
			"novelty":     3,
			"testability": 5,
			"safety":      5,
		},
		"verdict": "Strong hypothesis with clear experimental path.",
		"key_criticisms": []string{
			"Primary supporting evidence is correlational.",
		},
		"verified_citations": []string{
			"PMID:38012345",
		},
		"contradicting_evidence": []string{},
		"reviewer":               "reflection-agent-1",
		"epoch":                  0,
	}
}

func sampleHypothesisData(id string, status string) map[string]any {
	return map[string]any{
		"id":        id,
		"title":     "Novel cognitive resilience mechanism",
		"statement": "Elevated expression of protein X confers cognitive resilience against beta-amyloid toxicity.",
		"mechanism": "Protein X stabilizes synaptic terminals and blocks receptor binding of oligomers.",
		"predictions": []string{
			"Overexpression of X in mouse models prevents synaptic spine loss.",
		},
		"experiments": []map[string]any{
			{
				"design":         "Transgenic expression in APP/PS1 mice with behavioral assays.",
				"readout":        "Morris water maze latency and spine density quantification.",
				"est_difficulty": "med",
			},
		},
		"evidence": []map[string]any{
			{
				"lit_id": "PMID:38012345",
				"role":   "supports",
				"note":   "Demonstrates correlational resilience.",
			},
		},
		"focus_area": "synaptic plasticity",
		"lineage": map[string]any{
			"parents":  []string{},
			"operator": "null",
		},
		"status":     status,
		"created_by": "gen-agent-1",
		"epoch":      0,
		"created_at": "2026-09-06T12:00:00Z",
	}
}

func mustMarshalIndent(t *testing.T, v any) []byte {
	t.Helper()
	data, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		t.Fatalf("mustMarshalIndent: %v", err)
	}
	return append(data, '\n')
}
