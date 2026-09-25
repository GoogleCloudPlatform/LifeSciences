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

package composite

import (
	"math"
	"testing"
)

func intPtr(i int) *int {
	return &i
}

func TestNormalize(t *testing.T) {
	tests := []struct {
		name string
		raw  *int
		want float64
	}{
		{"nil is neutral 0.5", nil, 0.5},
		{"score 1 is 0.0", intPtr(1), 0.0},
		{"score 2 is 0.25", intPtr(2), 0.25},
		{"score 3 is neutral 0.5", intPtr(3), 0.5},
		{"score 4 is 0.75", intPtr(4), 0.75},
		{"score 5 is 1.0", intPtr(5), 1.0},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := Normalize(tt.raw)
			if math.Abs(got-tt.want) > 1e-9 {
				t.Errorf("Normalize(%v) = %v, want %v", tt.raw, got, tt.want)
			}
		})
	}
}

func TestPresetWeights(t *testing.T) {
	tests := []struct {
		name       string
		wantGoal   float64
		wantComp   float64
		wantNov    float64
		wantSum    float64
		degenerate bool
	}{
		{"balanced", 50, 50, 20, 120, false},
		{"disable_novelty", 60, 60, 0, 120, false},
		{"focus_on_breakthroughs", 15, 15, 90, 120, false},
		{"strict_constraints", 35, 70, 15, 120, false},
		{"pure_tournament", 0, 0, 0, 0, true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			w, err := PresetWeights(tt.name)
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if w.Goal != tt.wantGoal || w.Constraint != tt.wantComp || w.Novelty != tt.wantNov {
				t.Errorf("PresetWeights(%q) = %+v, want Goal=%.0f Comp=%.0f Nov=%.0f",
					tt.name, w, tt.wantGoal, tt.wantComp, tt.wantNov)
			}
			sum := w.Goal + w.Constraint + w.Novelty
			if sum != tt.wantSum {
				t.Errorf("sum of weights = %v, want %v", sum, tt.wantSum)
			}
			if !tt.degenerate && sum != 120 {
				t.Errorf("non-degenerate preset %s violated sum-120 invariant: sum = %v", tt.name, sum)
			}
		})
	}

	t.Run("unknown preset returns error listing valid presets", func(t *testing.T) {
		_, err := PresetWeights("invalid_preset")
		if err == nil {
			t.Fatal("expected error for unknown preset, got nil")
		}
		errMsg := err.Error()
		expectedKeywords := []string{"balanced", "disable_novelty", "focus_on_breakthroughs", "strict_constraints", "pure_tournament"}
		for _, kw := range expectedKeywords {
			if !testing.Short() && !contains(errMsg, kw) {
				t.Errorf("error message %q should contain valid preset %q", errMsg, kw)
			}
		}
	})
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || searchString(s, substr))
}

func searchString(s, substr string) bool {
	for i := 0; i+len(substr) <= len(s); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}

func TestApply_PureTournament(t *testing.T) {
	w, err := PresetWeights("pure_tournament")
	if err != nil {
		t.Fatal(err)
	}

	axes := AxisScores{
		Goal:          intPtr(5),
		Constraint:    intPtr(1),
		Novelty:       intPtr(4),
		GoalSrc:       "H-0001.R-01",
		ConstraintSrc: "H-0001.R-01",
		NoveltySrc:    "H-0001.R-01",
	}

	rTourn := 1542.5
	res := Apply(rTourn, axes, w)

	if res.Tournament != rTourn {
		t.Errorf("res.Tournament = %v, want %v", res.Tournament, rTourn)
	}
	if res.Delta != 0.0 {
		t.Errorf("res.Delta = %v, want 0.0", res.Delta)
	}
	if res.Composite != rTourn {
		t.Errorf("res.Composite = %v, want %v exactly", res.Composite, rTourn)
	}
}

func TestApply_BalancedHandComputed(t *testing.T) {
	w, err := PresetWeights("balanced")
	if err != nil {
		t.Fatal(err)
	}

	// Goal=5 (S=1.0, contrib=+25.0)
	// Constraint=nil (S=0.5, contrib=0.0)
	// Novelty=5 (S=1.0, contrib=+10.0)
	// Delta = +35.0
	// Tournament = 1487.5 => Composite = 1522.5
	axes := AxisScores{
		Goal:       intPtr(5),
		Constraint: nil,
		Novelty:    intPtr(5),
		GoalSrc:    "H-0007.R-02",
		NoveltySrc: "H-0007.R-02",
	}

	res := Apply(1487.5, axes, w)
	if res.Goal.Contribution != 25.0 {
		t.Errorf("Goal.Contribution = %v, want 25.0", res.Goal.Contribution)
	}
	if res.Constraint.Contribution != 0.0 {
		t.Errorf("Constraint.Contribution = %v, want 0.0", res.Constraint.Contribution)
	}
	if res.Novelty.Contribution != 10.0 {
		t.Errorf("Novelty.Contribution = %v, want 10.0", res.Novelty.Contribution)
	}
	if res.Delta != 35.0 {
		t.Errorf("Delta = %v, want 35.0", res.Delta)
	}
	if res.Composite != 1522.5 {
		t.Errorf("Composite = %v, want 1522.5", res.Composite)
	}

	// Test Goal scores 1, 3, 5, and nil under balanced
	checkGoal := func(score *int, wantContrib float64) {
		a := AxisScores{Goal: score}
		r := Apply(1500.0, a, w)
		if r.Goal.Contribution != wantContrib {
			t.Errorf("Goal score %v contrib = %v, want %v", score, r.Goal.Contribution, wantContrib)
		}
	}
	checkGoal(intPtr(5), 25.0)
	checkGoal(intPtr(1), -25.0)
	checkGoal(intPtr(3), 0.0)
	checkGoal(nil, 0.0)
}
