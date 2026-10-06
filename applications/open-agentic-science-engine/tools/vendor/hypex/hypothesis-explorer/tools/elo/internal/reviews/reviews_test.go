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

package reviews

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func intPtr(i int) *int {
	return &i
}

func TestLoadAll_NotExist(t *testing.T) {
	runDir := t.TempDir()
	rs, err := LoadAll(runDir)
	if err != nil {
		t.Fatalf("expected nil error for missing reviews dir, got: %v", err)
	}
	if rs == nil {
		t.Fatal("expected non-nil slice")
	}
	if len(rs) != 0 {
		t.Fatalf("expected 0 reviews, got %d", len(rs))
	}
}

func TestLoadAll_ValidAndFallbacks(t *testing.T) {
	runDir := t.TempDir()
	reviewsDir := filepath.Join(runDir, "reviews")
	if err := os.MkdirAll(reviewsDir, 0o755); err != nil {
		t.Fatal(err)
	}

	r1 := `{
  "id": "H-0001.R-01",
  "hypothesis_id": "H-0001",
  "review_type": "full",
  "scores": {
    "correctness": 4,
    "novelty": 3,
    "testability": 5,
    "safety": 4,
    "goal_alignment": 5,
    "constraint_compliance": 4
  },
  "epoch": 0
}`
	// r2 omits id and hypothesis_id -> should fall back to filename
	r2 := `{
  "review_type": "deep",
  "scores": {
    "correctness": 5,
    "novelty": 4,
    "testability": 5,
    "safety": 4
  },
  "epoch": 1
}`

	if err := os.WriteFile(filepath.Join(reviewsDir, "H-0001.R-01.json"), []byte(r1), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(reviewsDir, "H-0002.R-01.json"), []byte(r2), 0o644); err != nil {
		t.Fatal(err)
	}
	// Ignored non-review file and subdir
	if err := os.WriteFile(filepath.Join(reviewsDir, "notes.txt"), []byte("skip"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(reviewsDir, "sub"), 0o755); err != nil {
		t.Fatal(err)
	}

	rs, err := LoadAll(runDir)
	if err != nil {
		t.Fatalf("LoadAll failed: %v", err)
	}
	if len(rs) != 2 {
		t.Fatalf("expected 2 reviews, got %d", len(rs))
	}

	for _, r := range rs {
		if r.HypothesisID == "H-0001" {
			if r.ID != "H-0001.R-01" {
				t.Errorf("r1 ID = %q, want H-0001.R-01", r.ID)
			}
			if r.Scores.GoalAlignment == nil || *r.Scores.GoalAlignment != 5 {
				t.Errorf("r1 GoalAlignment = %v, want 5", r.Scores.GoalAlignment)
			}
		} else if r.HypothesisID == "H-0002" {
			if r.ID != "H-0002.R-01" {
				t.Errorf("r2 ID fallback = %q, want H-0002.R-01", r.ID)
			}
		} else {
			t.Errorf("unexpected hypothesis ID %q", r.HypothesisID)
		}
	}
}

func TestLoadAll_OutOfRange(t *testing.T) {
	runDir := t.TempDir()
	reviewsDir := filepath.Join(runDir, "reviews")
	if err := os.MkdirAll(reviewsDir, 0o755); err != nil {
		t.Fatal(err)
	}

	badScoreJSON := `{
  "id": "H-0007.R-02",
  "hypothesis_id": "H-0007",
  "scores": {
    "goal_alignment": 7
  }
}`
	if err := os.WriteFile(filepath.Join(reviewsDir, "H-0007.R-02.json"), []byte(badScoreJSON), 0o644); err != nil {
		t.Fatal(err)
	}

	_, err := LoadAll(runDir)
	if err == nil {
		t.Fatal("expected error for out of range score 7, got nil")
	}
	if !strings.Contains(err.Error(), "H-0007.R-02.json") {
		t.Errorf("error %q should name file H-0007.R-02.json", err.Error())
	}
	if !strings.Contains(err.Error(), "scores.goal_alignment = 7 (must be 1-5)") {
		t.Errorf("error %q should specify score out of range", err.Error())
	}
}

func TestLoadAll_MalformedJSON(t *testing.T) {
	runDir := t.TempDir()
	reviewsDir := filepath.Join(runDir, "reviews")
	if err := os.MkdirAll(reviewsDir, 0o755); err != nil {
		t.Fatal(err)
	}

	if err := os.WriteFile(filepath.Join(reviewsDir, "H-0001.R-01.json"), []byte("{not json"), 0o644); err != nil {
		t.Fatal(err)
	}

	_, err := LoadAll(runDir)
	if err == nil {
		t.Fatal("expected error for malformed json, got nil")
	}
	if !strings.Contains(err.Error(), "H-0001.R-01.json") {
		t.Errorf("error %q should name the malformed file", err.Error())
	}
}

func TestLatestAxes_PerAxisIndependenceAndOrdering(t *testing.T) {
	// Scenario:
	// H-0001 has two reviews:
	// R-01 (epoch 0): has GoalAlignment = 5, Novelty = 3
	// R-02 (epoch 1, e.g. safety review): has GoalAlignment = nil, Novelty = 4
	// GoalAlignment must NOT be shadowed by R-02!
	// Novelty must resolve to R-02 (epoch 1 > epoch 0).
	// ConstraintCompliance has no review -> nil, source "".
	rs := []Review{
		{
			ID:           "H-0001.R-01",
			HypothesisID: "H-0001",
			Epoch:        0,
			Scores: Scores{
				GoalAlignment: intPtr(5),
				Novelty:       intPtr(3),
			},
		},
		{
			ID:           "H-0001.R-02",
			HypothesisID: "H-0001",
			Epoch:        1,
			Scores: Scores{
				GoalAlignment: nil, // unassessed
				Novelty:       intPtr(4),
			},
		},
	}

	axesMap := LatestAxes(rs)
	a, ok := axesMap["H-0001"]
	if !ok {
		t.Fatal("expected H-0001 in axesMap")
	}

	// Goal should come from R-01
	if a.Goal == nil || *a.Goal != 5 {
		t.Errorf("Goal = %v, want 5", a.Goal)
	}
	if a.GoalSrc != "H-0001.R-01" {
		t.Errorf("GoalSrc = %q, want H-0001.R-01", a.GoalSrc)
	}

	// Novelty should come from R-02
	if a.Novelty == nil || *a.Novelty != 4 {
		t.Errorf("Novelty = %v, want 4", a.Novelty)
	}
	if a.NoveltySrc != "H-0001.R-02" {
		t.Errorf("NoveltySrc = %q, want H-0001.R-02", a.NoveltySrc)
	}

	// Constraint should be nil
	if a.Constraint != nil {
		t.Errorf("Constraint = %v, want nil", a.Constraint)
	}
	if a.ConstraintSrc != "" {
		t.Errorf("ConstraintSrc = %q, want empty", a.ConstraintSrc)
	}
}

func TestLatestAxes_SequenceAndTiebreakOrdering(t *testing.T) {
	// Same epoch, different NN: R-02 should beat R-01
	rs := []Review{
		{
			ID:           "H-0002.R-01",
			HypothesisID: "H-0002",
			Epoch:        0,
			Scores:       Scores{GoalAlignment: intPtr(3)},
		},
		{
			ID:           "H-0002.R-02",
			HypothesisID: "H-0002",
			Epoch:        0,
			Scores:       Scores{GoalAlignment: intPtr(5)},
		},
	}

	axesMap := LatestAxes(rs)
	a := axesMap["H-0002"]
	if a.Goal == nil || *a.Goal != 5 {
		t.Errorf("Goal = %v, want 5", a.Goal)
	}
	if a.GoalSrc != "H-0002.R-02" {
		t.Errorf("GoalSrc = %q, want H-0002.R-02", a.GoalSrc)
	}
}

func TestMeanScores(t *testing.T) {
	rs := []Review{
		{
			ID:           "H-0001.R-01",
			HypothesisID: "H-0001",
			Scores: Scores{
				Correctness: intPtr(4),
				Novelty:     intPtr(3),
				Testability: intPtr(5),
				Safety:      intPtr(4),
			},
		},
		{
			ID:           "H-0001.R-02",
			HypothesisID: "H-0001",
			Scores: Scores{
				Correctness: intPtr(5),
				Novelty:     intPtr(4),
				Testability: intPtr(5),
				Safety:      intPtr(4),
			},
		},
	}

	scores := MeanScores(rs)
	if diff := scores["H-0001"] - 4.25; diff < -1e-6 || diff > 1e-6 {
		t.Errorf("scores[H-0001] = %v, want 4.25", scores["H-0001"])
	}
}
