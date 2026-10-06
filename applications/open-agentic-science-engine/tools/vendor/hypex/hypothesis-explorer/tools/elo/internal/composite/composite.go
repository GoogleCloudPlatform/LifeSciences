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

import "fmt"

// Weights are in Elo points. Zero is meaningful.
type Weights struct {
	Goal       float64
	Constraint float64
	Novelty    float64
}

// AxisScores holds the review-derived raw 1-5 scores for one hypothesis.
// A nil pointer means the axis was not assessed by any review.
type AxisScores struct {
	Goal          *int
	Constraint    *int
	Novelty       *int
	GoalSrc       string
	ConstraintSrc string
	NoveltySrc    string
}

// Component is one weighted term, retained for --explain.
type Component struct {
	Score        *int    // nil when unassessed
	S            float64 // Normalize(Score)
	Weight       float64
	Contribution float64 // Weight * (S - 0.5)
	Source       string  // review ID, "" when unassessed
}

// Result holds the computed composite rating and its constituent components.
type Result struct {
	Tournament float64 // R_tourn, verbatim from ratings/epoch-N.json
	Goal       Component
	Constraint Component
	Novelty    Component
	Delta      float64 // sum of the three contributions in fixed order: Goal + Constraint + Novelty
	Composite  float64 // Tournament + Delta
}

// Normalize maps a raw 1-5 score to S in [0,1]; nil maps to the neutral 0.5.
func Normalize(raw *int) float64 {
	if raw == nil {
		return 0.5
	}
	return (float64(*raw) - 1.0) / 4.0
}

// PresetWeights returns the weight configuration for a named preset.
// All non-degenerate presets maintain the invariant Goal + Constraint + Novelty = 120.
func PresetWeights(name string) (Weights, error) {
	switch name {
	case "balanced":
		return Weights{Goal: 50, Constraint: 50, Novelty: 20}, nil
	case "disable_novelty":
		return Weights{Goal: 60, Constraint: 60, Novelty: 0}, nil
	case "focus_on_breakthroughs":
		return Weights{Goal: 15, Constraint: 15, Novelty: 90}, nil
	case "strict_constraints":
		return Weights{Goal: 35, Constraint: 70, Novelty: 15}, nil
	case "pure_tournament":
		return Weights{Goal: 0, Constraint: 0, Novelty: 0}, nil
	default:
		return Weights{}, fmt.Errorf("unknown preset %q (valid: balanced, disable_novelty, focus_on_breakthroughs, strict_constraints, pure_tournament)", name)
	}
}

// Apply computes the composite rating for a single hypothesis.
// Summation order is strictly: Goal + Constraint + Novelty, then added to Tournament.
func Apply(rTourn float64, a AxisScores, w Weights) Result {
	sGoal := Normalize(a.Goal)
	cGoal := w.Goal * (sGoal - 0.5)
	compGoal := Component{
		Score:        a.Goal,
		S:            sGoal,
		Weight:       w.Goal,
		Contribution: cGoal,
		Source:       a.GoalSrc,
	}

	sConstraint := Normalize(a.Constraint)
	cConstraint := w.Constraint * (sConstraint - 0.5)
	compConstraint := Component{
		Score:        a.Constraint,
		S:            sConstraint,
		Weight:       w.Constraint,
		Contribution: cConstraint,
		Source:       a.ConstraintSrc,
	}

	sNovelty := Normalize(a.Novelty)
	cNovelty := w.Novelty * (sNovelty - 0.5)
	compNovelty := Component{
		Score:        a.Novelty,
		S:            sNovelty,
		Weight:       w.Novelty,
		Contribution: cNovelty,
		Source:       a.NoveltySrc,
	}

	delta := cGoal + cConstraint + cNovelty
	composite := rTourn + delta

	return Result{
		Tournament: rTourn,
		Goal:       compGoal,
		Constraint: compConstraint,
		Novelty:    compNovelty,
		Delta:      delta,
		Composite:  composite,
	}
}
