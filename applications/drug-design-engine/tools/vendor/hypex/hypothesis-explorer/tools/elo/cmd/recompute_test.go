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
	"path/filepath"
	"strings"
	"testing"

	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/elo/internal/rating"
)

func TestRecompute_WithDraw(t *testing.T) {
	runDir := t.TempDir()
	matchesDir := filepath.Join(runDir, "matches")
	if err := os.MkdirAll(matchesDir, 0o755); err != nil {
		t.Fatal(err)
	}

	match1 := []byte(`{
		"id": "M-0001",
		"epoch": 0,
		"a": "H-0001",
		"b": "H-0002",
		"format": "single-turn",
		"winner": "H-0001",
		"margin": "decisive",
		"created_at": "2026-09-05T12:00:00Z"
	}`)
	if err := os.WriteFile(filepath.Join(matchesDir, "M-0001.json"), match1, 0o644); err != nil {
		t.Fatal(err)
	}

	match2 := []byte(`{
		"id": "M-0002",
		"epoch": 0,
		"a": "H-0001",
		"b": "H-0002",
		"format": "single-turn",
		"winner": "draw",
		"margin": "narrow",
		"created_at": "2026-09-05T12:05:00Z"
	}`)
	if err := os.WriteFile(filepath.Join(matchesDir, "M-0002.json"), match2, 0o644); err != nil {
		t.Fatal(err)
	}

	rootCmd.SetArgs([]string{"recompute", "--run-dir", runDir, "--epoch", "0"})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("rootCmd.Execute failed: %v", err)
	}

	// Verify ratings file was written
	ratingsFile := filepath.Join(runDir, "ratings", "epoch-0.json")
	data, err := os.ReadFile(ratingsFile)
	if err != nil {
		t.Fatalf("reading ratings file: %v", err)
	}

	var parsed struct {
		Epoch      int     `json:"epoch"`
		BaseRating float64 `json:"base_rating"`
		Ratings    map[string]struct {
			Elo     float64 `json:"elo"`
			Matches int     `json:"matches"`
			Wins    int     `json:"wins"`
			Draws   int     `json:"draws"`
		} `json:"ratings"`
		ComputedFrom string `json:"computed_from"`
	}
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatalf("unmarshaling ratings json: %v", err)
	}

	if parsed.Epoch != 0 {
		t.Errorf("expected epoch 0, got %d", parsed.Epoch)
	}
	if parsed.BaseRating != rating.DefaultRating {
		t.Errorf("expected base_rating %v, got %v", rating.DefaultRating, parsed.BaseRating)
	}
	if parsed.ComputedFrom != "matches/ ledger @ M-0002" {
		t.Errorf("expected computed_from 'matches/ ledger @ M-0002', got %q", parsed.ComputedFrom)
	}

	r1 := parsed.Ratings["H-0001"]
	if r1.Matches != 2 || r1.Wins != 1 || r1.Draws != 1 {
		t.Errorf("H-0001: matches=%d wins=%d draws=%d, want 2,1,1", r1.Matches, r1.Wins, r1.Draws)
	}

	r2 := parsed.Ratings["H-0002"]
	if r2.Matches != 2 || r2.Wins != 0 || r2.Draws != 1 {
		t.Errorf("H-0002: matches=%d wins=%d draws=%d, want 2,0,1", r2.Matches, r2.Wins, r2.Draws)
	}
}

func TestRecompute_InvalidWinner_ReturnsError(t *testing.T) {
	runDir := t.TempDir()
	matchesDir := filepath.Join(runDir, "matches")
	if err := os.MkdirAll(matchesDir, 0o755); err != nil {
		t.Fatal(err)
	}

	badMatch := []byte(`{
		"id": "M-9999",
		"epoch": 0,
		"a": "H-0001",
		"b": "H-0002",
		"winner": "corrupt-winner",
		"margin": "narrow",
		"created_at": "2026-09-05T12:00:00Z"
	}`)
	if err := os.WriteFile(filepath.Join(matchesDir, "M-9999.json"), badMatch, 0o644); err != nil {
		t.Fatal(err)
	}

	rootCmd.SetArgs([]string{"recompute", "--run-dir", runDir, "--epoch", "0"})
	err := rootCmd.Execute()
	if err == nil {
		t.Fatal("expected recompute to fail on invalid winner, got nil")
	}

	errMsg := err.Error()
	if !strings.Contains(errMsg, "M-9999") {
		t.Errorf("expected error message to contain match ID M-9999, got: %s", errMsg)
	}
	if !strings.Contains(errMsg, "corrupt-winner") {
		t.Errorf("expected error message to contain invalid winner corrupt-winner, got: %s", errMsg)
	}

	// Verify no ratings file was written
	ratingsFile := filepath.Join(runDir, "ratings", "epoch-0.json")
	if _, err := os.Stat(ratingsFile); !os.IsNotExist(err) {
		t.Errorf("expected ratings file not to exist on failure, but it does")
	}
}

// TestRecompute_BaseRatingField verifies that 'elo recompute' on a fixture
// produces ratings/epoch-N.json with "base_rating": 1500 present.
func TestRecompute_BaseRatingField(t *testing.T) {
	runDir := t.TempDir()

	matchDir := filepath.Join(runDir, "matches")
	if err := os.MkdirAll(matchDir, 0o755); err != nil {
		t.Fatal(err)
	}

	matchContent := `{
  "id": "M-0001",
  "epoch": 0,
  "a": "H-0001",
  "b": "H-0002",
  "format": "single-turn",
  "winner": "H-0001",
  "margin": "decisive",
  "criterion_scores": {
    "novelty": {"a": 4, "b": 3},
    "plausibility": {"a": 5, "b": 4},
    "testability": {"a": 4, "b": 3}
  },
  "rationale": "H-0001 has stronger mechanistic backing.",
  "judge": "ranking-agent-1",
  "created_at": "2026-09-06T12:00:00Z"
}`
	if err := os.WriteFile(filepath.Join(matchDir, "M-0001.json"), []byte(matchContent), 0o644); err != nil {
		t.Fatal(err)
	}

	// Execute elo recompute.
	var out bytes.Buffer
	rootCmd.SetOut(&out)
	rootCmd.SetErr(&out)
	rootCmd.SetArgs([]string{"recompute", "--run-dir", runDir, "--epoch", "0"})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("elo recompute failed: %v", err)
	}

	ratingsPath := filepath.Join(runDir, "ratings", "epoch-0.json")
	data, err := os.ReadFile(ratingsPath)
	if err != nil {
		t.Fatalf("reading ratings output %s: %v", ratingsPath, err)
	}

	rawJSON := string(data)
	if !strings.Contains(rawJSON, `"base_rating": 1500`) {
		t.Errorf("expected %q in ratings output, got:\n%s", `"base_rating": 1500`, rawJSON)
	}

	var parsed ratingsOutput
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatalf("unmarshaling ratingsOutput: %v", err)
	}

	if parsed.Epoch != 0 {
		t.Errorf("parsed.Epoch = %d, want 0", parsed.Epoch)
	}
	if parsed.BaseRating != rating.DefaultRating {
		t.Errorf("parsed.BaseRating = %v, want %v", parsed.BaseRating, rating.DefaultRating)
	}
	if len(parsed.Ratings) != 2 {
		t.Fatalf("expected 2 ratings entries, got %d", len(parsed.Ratings))
	}
	if parsed.Ratings["H-0001"] == nil || parsed.Ratings["H-0002"] == nil {
		t.Fatalf("missing expected hypothesis ratings: %v", parsed.Ratings)
	}
	if parsed.Ratings["H-0001"].Wins != 1 {
		t.Errorf("H-0001 wins = %d, want 1", parsed.Ratings["H-0001"].Wins)
	}
}

// TestRecompute_EmptyMatches verifies that recomputing with an empty ledger
// still emits base_rating: 1500.
func TestRecompute_EmptyMatches(t *testing.T) {
	runDir := t.TempDir()

	var out bytes.Buffer
	rootCmd.SetOut(&out)
	rootCmd.SetErr(&out)
	rootCmd.SetArgs([]string{"recompute", "--run-dir", runDir, "--epoch", "1"})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("elo recompute failed: %v", err)
	}

	ratingsPath := filepath.Join(runDir, "ratings", "epoch-1.json")
	data, err := os.ReadFile(ratingsPath)
	if err != nil {
		t.Fatalf("reading ratings output %s: %v", ratingsPath, err)
	}

	rawJSON := string(data)
	if !strings.Contains(rawJSON, `"base_rating": 1500`) {
		t.Errorf("expected %q in ratings output, got:\n%s", `"base_rating": 1500`, rawJSON)
	}

	var parsed ratingsOutput
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatalf("unmarshaling ratingsOutput: %v", err)
	}
	if parsed.BaseRating != 1500.0 {
		t.Errorf("parsed.BaseRating = %v, want 1500.0", parsed.BaseRating)
	}
}
