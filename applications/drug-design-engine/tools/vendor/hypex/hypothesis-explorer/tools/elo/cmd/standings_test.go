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
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestStandings_TableAndJSONWithDraws(t *testing.T) {
	runDir := t.TempDir()
	ratingsDir := filepath.Join(runDir, "ratings")
	if err := os.MkdirAll(ratingsDir, 0o755); err != nil {
		t.Fatal(err)
	}

	ratingsJSON := []byte(`{
		"epoch": 1,
		"ratings": {
			"H-0001": {
				"elo": 1525.5,
				"matches": 3,
				"wins": 1,
				"draws": 2
			},
			"H-0002": {
				"elo": 1474.5,
				"matches": 3,
				"wins": 0,
				"draws": 2
			}
		},
		"computed_from": "matches/ ledger @ M-0003"
	}`)
	if err := os.WriteFile(filepath.Join(ratingsDir, "epoch-1.json"), ratingsJSON, 0o644); err != nil {
		t.Fatal(err)
	}

	// Test JSON output format
	{
		oldStdout := os.Stdout
		r, w, _ := os.Pipe()
		os.Stdout = w

		rootCmd.SetArgs([]string{"standings", "--run-dir", runDir, "--format", "json"})
		err := rootCmd.Execute()
		w.Close()
		os.Stdout = oldStdout

		if err != nil {
			t.Fatalf("standings JSON failed: %v", err)
		}

		var buf bytes.Buffer
		io.Copy(&buf, r)
		output := buf.String()

		var standings []standingJSON
		if err := json.Unmarshal([]byte(output), &standings); err != nil {
			t.Fatalf("unmarshaling standings output: %v\nOutput was: %s", err, output)
		}

		if len(standings) != 2 {
			t.Fatalf("expected 2 standings, got %d", len(standings))
		}
		if standings[0].HypothesisID != "H-0001" || standings[0].Draws != 2 || standings[0].Wins != 1 {
			t.Errorf("standings[0]: %+v, want H-0001 with wins=1, draws=2", standings[0])
		}
		if standings[1].HypothesisID != "H-0002" || standings[1].Draws != 2 || standings[1].Wins != 0 {
			t.Errorf("standings[1]: %+v, want H-0002 with wins=0, draws=2", standings[1])
		}
	}

	// Test Table output format
	{
		oldStdout := os.Stdout
		r, w, _ := os.Pipe()
		os.Stdout = w

		rootCmd.SetArgs([]string{"standings", "--run-dir", runDir, "--format", "table"})
		err := rootCmd.Execute()
		w.Close()
		os.Stdout = oldStdout

		if err != nil {
			t.Fatalf("standings table failed: %v", err)
		}

		var buf bytes.Buffer
		io.Copy(&buf, r)
		output := buf.String()

		if !strings.Contains(output, "Draws") {
			t.Errorf("expected table header to contain 'Draws', got:\n%s", output)
		}
		if !strings.Contains(output, "H-0001") || !strings.Contains(output, "H-0002") {
			t.Errorf("expected table output to contain hypotheses, got:\n%s", output)
		}
	}
}

func TestStandings_NonComposite_ByteIdentical(t *testing.T) {
	runDir := t.TempDir()
	ratingsDir := filepath.Join(runDir, "ratings")
	if err := os.MkdirAll(ratingsDir, 0o755); err != nil {
		t.Fatal(err)
	}

	ratingsJSON := []byte(`{
  "epoch": 1,
  "ratings": {
    "H-0001": {
      "elo": 1525.5,
      "matches": 3,
      "wins": 1,
      "draws": 2
    }
  },
  "computed_from": "matches/ ledger @ M-0001"
}`)
	if err := os.WriteFile(filepath.Join(ratingsDir, "epoch-1.json"), ratingsJSON, 0o644); err != nil {
		t.Fatal(err)
	}

	// Capture output
	oldStdout := os.Stdout
	r, w, _ := os.Pipe()
	os.Stdout = w

	rootCmd.SetArgs([]string{"standings", "--run-dir", runDir, "--format", "json"})
	err := rootCmd.Execute()
	w.Close()
	os.Stdout = oldStdout

	if err != nil {
		t.Fatalf("execute failed: %v", err)
	}

	var buf bytes.Buffer
	io.Copy(&buf, r)
	output := buf.String()

	expected := "[\n  {\n    \"rank\": 1,\n    \"hypothesis_id\": \"H-0001\",\n    \"elo\": 1525.5,\n    \"matches\": 3,\n    \"wins\": 1,\n    \"draws\": 2\n  }\n]\n"
	if output != expected {
		t.Errorf("output mismatch:\nGOT:\n%s\nWANT:\n%s", output, expected)
	}
}

func TestStandings_FlagValidations(t *testing.T) {
	runDir := t.TempDir()

	tests := []struct {
		name    string
		args    []string
		wantErr string
	}{
		{
			name:    "preset without composite",
			args:    []string{"standings", "--run-dir", runDir, "--preset", "balanced"},
			wantErr: "--preset requires --composite",
		},
		{
			name:    "weights without composite",
			args:    []string{"standings", "--run-dir", runDir, "--weights", "goal=50"},
			wantErr: "--weights requires --composite",
		},
		{
			name:    "explain without composite",
			args:    []string{"standings", "--run-dir", runDir, "--explain"},
			wantErr: "--explain requires --composite",
		},
		{
			name:    "unknown preset",
			args:    []string{"standings", "--run-dir", runDir, "--composite", "--preset", "foo"},
			wantErr: "unknown preset \"foo\"",
		},
		{
			name:    "unknown weight axis",
			args:    []string{"standings", "--run-dir", runDir, "--composite", "--weights", "invalid_axis=10"},
			wantErr: "unknown weight axis \"invalid_axis\"",
		},
		{
			name:    "non numeric weight",
			args:    []string{"standings", "--run-dir", runDir, "--composite", "--weights", "goal=abc"},
			wantErr: "invalid weight value for goal \"abc\"",
		},
		{
			name:    "negative weight",
			args:    []string{"standings", "--run-dir", runDir, "--composite", "--weights", "goal=-10"},
			wantErr: "weight for goal cannot be negative",
		},
		{
			name:    "malformed weight format",
			args:    []string{"standings", "--run-dir", runDir, "--composite", "--weights", "justgoal"},
			wantErr: "invalid weight format",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			rootCmd.SetArgs(tt.args)
			err := rootCmd.Execute()
			if err == nil {
				t.Fatalf("expected error containing %q, got nil", tt.wantErr)
			}
			if !strings.Contains(err.Error(), tt.wantErr) {
				t.Errorf("error %q does not contain %q", err.Error(), tt.wantErr)
			}
		})
	}
}

func TestStandings_CompositeTableRankInversionAndExplain(t *testing.T) {
	runDir := t.TempDir()
	ratingsDir := filepath.Join(runDir, "ratings")
	reviewsDir := filepath.Join(runDir, "reviews")
	if err := os.MkdirAll(ratingsDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(reviewsDir, 0o755); err != nil {
		t.Fatal(err)
	}

	// H-0002 has higher raw Elo (1520 vs 1500)
	ratingsJSON := []byte(`{
  "epoch": 2,
  "ratings": {
    "H-0001": { "elo": 1500.0, "matches": 6, "wins": 3, "draws": 0 },
    "H-0002": { "elo": 1520.0, "matches": 6, "wins": 5, "draws": 0 }
  }
}`)
	if err := os.WriteFile(filepath.Join(ratingsDir, "epoch-2.json"), ratingsJSON, 0o644); err != nil {
		t.Fatal(err)
	}

	// H-0001 has high goal alignment (5 -> +25.0 under balanced) -> composite 1525.0
	// H-0002 has low goal alignment (1 -> -25.0 under balanced) -> composite 1495.0
	r1 := `{"id": "H-0001.R-01", "hypothesis_id": "H-0001", "scores": {"goal_alignment": 5}}`
	r2 := `{"id": "H-0002.R-01", "hypothesis_id": "H-0002", "scores": {"goal_alignment": 1}}`
	if err := os.WriteFile(filepath.Join(reviewsDir, "H-0001.R-01.json"), []byte(r1), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(reviewsDir, "H-0002.R-01.json"), []byte(r2), 0o644); err != nil {
		t.Fatal(err)
	}

	oldStdout := os.Stdout
	r, w, _ := os.Pipe()
	os.Stdout = w

	rootCmd.SetArgs([]string{"standings", "--run-dir", runDir, "--composite", "--explain"})
	err := rootCmd.Execute()
	w.Close()
	os.Stdout = oldStdout

	if err != nil {
		t.Fatalf("execute failed: %v", err)
	}

	var buf bytes.Buffer
	io.Copy(&buf, r)
	out := buf.String()

	// Verify header
	expectedHeader := "Standings (epoch 2, composite: preset=balanced W_goal=50 W_comp=50 W_nov=20)"
	if !strings.Contains(out, expectedHeader) {
		t.Errorf("output missing expected header %q:\n%s", expectedHeader, out)
	}

	// Verify column header
	expectedCols := "Rank    Hypothesis      Composite       Elo     Delta  Matches  Wins  Draws"
	if !strings.Contains(out, expectedCols) {
		t.Errorf("output missing expected columns:\n%s", out)
	}

	// Verify separator length 75
	expectedSep := strings.Repeat("-", 75)
	if !strings.Contains(out, expectedSep) {
		t.Errorf("output missing 75-char separator:\n%s", out)
	}

	// Verify rank inversion: H-0001 is rank 1, H-0002 is rank 2
	idxH1 := strings.Index(out, "H-0001")
	idxH2 := strings.Index(out, "H-0002")
	if idxH1 == -1 || idxH2 == -1 || idxH1 > idxH2 {
		t.Errorf("expected H-0001 before H-0002 due to composite ranking inversion:\n%s", out)
	}

	// Verify explain lines
	if !strings.Contains(out, "goal        5/5  S=1.00  W=50   ->  +25.0   (H-0001.R-01)") {
		t.Errorf("expected explain line for H-0001 goal, got:\n%s", out)
	}
	if !strings.Contains(out, "goal        1/5  S=0.00  W=50   ->  -25.0   (H-0002.R-01)") {
		t.Errorf("expected explain line for H-0002 goal, got:\n%s", out)
	}
	if !strings.Contains(out, "constraint    -  S=0.50  W=50   ->   +0.0   (not assessed)") {
		t.Errorf("expected explain line for unassessed constraint, got:\n%s", out)
	}
}

func TestStandings_CompositeJSON(t *testing.T) {
	runDir := t.TempDir()
	ratingsDir := filepath.Join(runDir, "ratings")
	if err := os.MkdirAll(ratingsDir, 0o755); err != nil {
		t.Fatal(err)
	}

	ratingsJSON := []byte(`{
  "epoch": 1,
  "ratings": {
    "H-0001": { "elo": 1500.0, "matches": 2, "wins": 1, "draws": 0 }
  }
}`)
	if err := os.WriteFile(filepath.Join(ratingsDir, "epoch-1.json"), ratingsJSON, 0o644); err != nil {
		t.Fatal(err)
	}

	oldStdout := os.Stdout
	oldStderr := os.Stderr
	rOut, wOut, _ := os.Pipe()
	rErr, wErr, _ := os.Pipe()
	os.Stdout = wOut
	os.Stderr = wErr

	rootCmd.SetArgs([]string{"standings", "--run-dir", runDir, "--composite", "--explain", "--format", "json"})
	err := rootCmd.Execute()
	wOut.Close()
	wErr.Close()
	os.Stdout = oldStdout
	os.Stderr = oldStderr

	if err != nil {
		t.Fatalf("execute failed: %v", err)
	}

	var bufOut bytes.Buffer
	var bufErr bytes.Buffer
	io.Copy(&bufOut, rOut)
	io.Copy(&bufErr, rErr)

	// Check stderr header
	if !strings.Contains(bufErr.String(), "Standings (epoch 1, composite: preset=balanced") {
		t.Errorf("stderr missing header: %q", bufErr.String())
	}

	// Check stdout JSON
	var parsed []standingJSON
	if err := json.Unmarshal(bufOut.Bytes(), &parsed); err != nil {
		t.Fatalf("unmarshaling JSON failed: %v\nJSON was: %s", err, bufOut.String())
	}
	if len(parsed) != 1 {
		t.Fatalf("expected 1 item, got %d", len(parsed))
	}
	item := parsed[0]
	if item.EloComposite == nil || *item.EloComposite != 1500.0 {
		t.Errorf("EloComposite = %v, want 1500.0", item.EloComposite)
	}
	if item.CompositeWeights == nil || item.CompositeWeights.Goal != 50 {
		t.Errorf("CompositeWeights.Goal = %v, want 50", item.CompositeWeights)
	}
	if item.Components == nil || item.Components.Goal.Source != nil {
		t.Errorf("Components.Goal.Source = %v, want nil", item.Components.Goal.Source)
	}
}

func TestStandings_PrecedenceAndOverrides(t *testing.T) {
	runDir := t.TempDir()
	ratingsDir := filepath.Join(runDir, "ratings")
	if err := os.MkdirAll(ratingsDir, 0o755); err != nil {
		t.Fatal(err)
	}

	ratingsJSON := []byte(`{
  "epoch": 1,
  "ratings": {
    "H-0001": { "elo": 1500.0, "matches": 1, "wins": 1, "draws": 0 }
  }
}`)
	if err := os.WriteFile(filepath.Join(ratingsDir, "epoch-1.json"), ratingsJSON, 0o644); err != nil {
		t.Fatal(err)
	}

	// run.yaml specifies strict_constraints (35, 70, 15)
	runYaml := `tournament:
  composite:
    preset: strict_constraints
`
	if err := os.WriteFile(filepath.Join(runDir, "run.yaml"), []byte(runYaml), 0o644); err != nil {
		t.Fatal(err)
	}

	// Override novelty=0 via CLI -> (35, 70, 0)
	oldStdout := os.Stdout
	oldStderr := os.Stderr
	_, wOut, _ := os.Pipe()
	rErr, wErr, _ := os.Pipe()
	os.Stdout = wOut
	os.Stderr = wErr

	rootCmd.SetArgs([]string{"standings", "--run-dir", runDir, "--composite", "--weights", "novelty=0", "--format", "json"})
	err := rootCmd.Execute()
	wOut.Close()
	wErr.Close()
	os.Stdout = oldStdout
	os.Stderr = oldStderr

	if err != nil {
		t.Fatalf("execute failed: %v", err)
	}

	var buf bytes.Buffer
	io.Copy(&buf, rErr)
	stderrStr := buf.String()

	expectedHeader := "W_goal=35 W_comp=70 W_nov=0"
	if !strings.Contains(stderrStr, expectedHeader) {
		t.Errorf("expected %q in stderr, got %q", expectedHeader, stderrStr)
	}
}

func TestStandings_CorruptedRunYaml(t *testing.T) {
	runDir := t.TempDir()
	ratingsDir := filepath.Join(runDir, "ratings")
	if err := os.MkdirAll(ratingsDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(ratingsDir, "epoch-1.json"), []byte(`{"epoch":1,"ratings":{"H-1":{"elo":1500}}}`), 0o644); err != nil {
		t.Fatal(err)
	}

	yamlPath := filepath.Join(runDir, "run.yaml")
	if err := os.WriteFile(yamlPath, []byte(`invalid: [yaml`), 0o644); err != nil {
		t.Fatal(err)
	}

	rootCmd.SetArgs([]string{"standings", "--run-dir", runDir, "--composite"})
	err := rootCmd.Execute()
	if err == nil {
		t.Fatal("expected error for corrupted run.yaml, got nil")
	}
	if !strings.Contains(err.Error(), yamlPath) {
		t.Errorf("error %q should name file %q", err.Error(), yamlPath)
	}
}

func TestStandings_OutOfRangeReview(t *testing.T) {
	runDir := t.TempDir()
	ratingsDir := filepath.Join(runDir, "ratings")
	reviewsDir := filepath.Join(runDir, "reviews")
	if err := os.MkdirAll(ratingsDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(reviewsDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(ratingsDir, "epoch-1.json"), []byte(`{"epoch":1,"ratings":{"H-1":{"elo":1500}}}`), 0o644); err != nil {
		t.Fatal(err)
	}

	badReview := `{"id":"H-0007.R-02","hypothesis_id":"H-0007","scores":{"goal_alignment":7}}`
	if err := os.WriteFile(filepath.Join(reviewsDir, "H-0007.R-02.json"), []byte(badReview), 0o644); err != nil {
		t.Fatal(err)
	}

	rootCmd.SetArgs([]string{"standings", "--run-dir", runDir, "--composite"})
	err := rootCmd.Execute()
	if err == nil {
		t.Fatal("expected error for score 7, got nil")
	}
	if !strings.Contains(err.Error(), "H-0007.R-02.json") {
		t.Errorf("error %q should name review file", err.Error())
	}
	if !strings.Contains(err.Error(), "scores.goal_alignment = 7 (must be 1-5)") {
		t.Errorf("error %q should mention scores.goal_alignment = 7 (must be 1-5)", err.Error())
	}
}
