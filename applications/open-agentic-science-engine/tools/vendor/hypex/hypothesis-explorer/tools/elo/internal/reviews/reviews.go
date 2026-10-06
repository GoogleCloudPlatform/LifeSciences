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
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/elo/internal/composite"
)

// Review represents the subset of review schema data required for
// composite rating and Swiss seeding.
type Review struct {
	ID           string `json:"id"`
	HypothesisID string `json:"hypothesis_id"`
	ReviewType   string `json:"review_type"`
	Epoch        int    `json:"epoch"`
	Scores       Scores `json:"scores"`
}

// Scores holds the assessment scores on each axis.
// Pointers distinguish absent/unassessed axes from explicit scores.
type Scores struct {
	Correctness          *int `json:"correctness"`
	Novelty              *int `json:"novelty"`
	Testability          *int `json:"testability"`
	Safety               *int `json:"safety"`
	GoalAlignment        *int `json:"goal_alignment"`
	ConstraintCompliance *int `json:"constraint_compliance"`
}

// LoadAll reads every review JSON file in <runDir>/reviews/.
// Subdirectories and non-review files are ignored.
// Returns an empty slice and nil error if reviews/ does not exist.
// Returns a hard error naming the file if unreadable, malformed, or scores are out of 1-5 range.
func LoadAll(runDir string) ([]Review, error) {
	reviewDir := filepath.Join(runDir, "reviews")
	entries, err := os.ReadDir(reviewDir)
	if err != nil {
		if os.IsNotExist(err) {
			return []Review{}, nil
		}
		return nil, fmt.Errorf("reading reviews directory: %w", err)
	}

	var reviews []Review
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		name := entry.Name()
		if !strings.HasPrefix(name, "H-") || !strings.HasSuffix(name, ".json") {
			continue
		}

		filePath := filepath.Join(reviewDir, name)
		relPath := filepath.Join("reviews", name)

		data, err := os.ReadFile(filePath)
		if err != nil {
			return nil, fmt.Errorf("reading review file %s: %w", relPath, err)
		}

		var r Review
		if err := json.Unmarshal(data, &r); err != nil {
			return nil, fmt.Errorf("parsing review file %s: %w", relPath, err)
		}

		// Fallback for HypothesisID if omitted in JSON
		if r.HypothesisID == "" {
			parts := strings.Split(name, ".")
			if len(parts) > 0 && strings.HasPrefix(parts[0], "H-") {
				r.HypothesisID = parts[0]
			}
		}

		// Fallback for ID if omitted in JSON
		if r.ID == "" {
			r.ID = strings.TrimSuffix(name, ".json")
		}

		// Validate all present scores are between 1 and 5
		if err := validateScores(r.Scores, relPath); err != nil {
			return nil, err
		}

		reviews = append(reviews, r)
	}

	return reviews, nil
}

func validateScores(s Scores, relPath string) error {
	check := func(axis string, val *int) error {
		if val != nil && (*val < 1 || *val > 5) {
			return fmt.Errorf("loading reviews: %s: scores.%s = %d (must be 1-5)", relPath, axis, *val)
		}
		return nil
	}

	if err := check("correctness", s.Correctness); err != nil {
		return err
	}
	if err := check("novelty", s.Novelty); err != nil {
		return err
	}
	if err := check("testability", s.Testability); err != nil {
		return err
	}
	if err := check("safety", s.Safety); err != nil {
		return err
	}
	if err := check("goal_alignment", s.GoalAlignment); err != nil {
		return err
	}
	if err := check("constraint_compliance", s.ConstraintCompliance); err != nil {
		return err
	}
	return nil
}

// reviewSeq extracts the sequence number NN from review IDs of the form H-NNNN.R-NN.
func reviewSeq(id string) int {
	idx := strings.Index(id, ".R-")
	if idx >= 0 {
		var n int
		if _, err := fmt.Sscanf(id[idx+3:], "%d", &n); err == nil {
			return n
		}
	}
	return 0
}

// LatestAxes selects the latest assessed score for each axis independently per hypothesis.
// Order: descending Epoch, descending review NN sequence, descending review ID.
func LatestAxes(rs []Review) map[string]composite.AxisScores {
	res := make(map[string]composite.AxisScores)

	// Collect unique hypotheses
	hypReviews := make(map[string][]Review)
	for _, r := range rs {
		if r.HypothesisID == "" {
			continue
		}
		hypReviews[r.HypothesisID] = append(hypReviews[r.HypothesisID], r)
	}

	// Comparator for sorting reviews latest-first
	sortReviews := func(list []Review) {
		sort.Slice(list, func(i, j int) bool {
			if list[i].Epoch != list[j].Epoch {
				return list[i].Epoch > list[j].Epoch
			}
			seqI := reviewSeq(list[i].ID)
			seqJ := reviewSeq(list[j].ID)
			if seqI != seqJ {
				return seqI > seqJ
			}
			return list[i].ID > list[j].ID
		})
	}

	for hypID, reviews := range hypReviews {
		var goalScore, constraintScore, noveltyScore *int
		var goalSrc, constraintSrc, noveltySrc string

		// 1. GoalAlignment
		var goalList []Review
		for _, r := range reviews {
			if r.Scores.GoalAlignment != nil {
				goalList = append(goalList, r)
			}
		}
		if len(goalList) > 0 {
			sortReviews(goalList)
			goalScore = goalList[0].Scores.GoalAlignment
			goalSrc = goalList[0].ID
		}

		// 2. ConstraintCompliance
		var constraintList []Review
		for _, r := range reviews {
			if r.Scores.ConstraintCompliance != nil {
				constraintList = append(constraintList, r)
			}
		}
		if len(constraintList) > 0 {
			sortReviews(constraintList)
			constraintScore = constraintList[0].Scores.ConstraintCompliance
			constraintSrc = constraintList[0].ID
		}

		// 3. Novelty
		var noveltyList []Review
		for _, r := range reviews {
			if r.Scores.Novelty != nil {
				noveltyList = append(noveltyList, r)
			}
		}
		if len(noveltyList) > 0 {
			sortReviews(noveltyList)
			noveltyScore = noveltyList[0].Scores.Novelty
			noveltySrc = noveltyList[0].ID
		}

		res[hypID] = composite.AxisScores{
			Goal:          goalScore,
			Constraint:    constraintScore,
			Novelty:       noveltyScore,
			GoalSrc:       goalSrc,
			ConstraintSrc: constraintSrc,
			NoveltySrc:    noveltySrc,
		}
	}

	return res
}

// MeanScores computes the mean review score per hypothesis across the 4 core axes:
// (correctness + novelty + testability + safety) / 4.
// For hypotheses with multiple reviews, scores are averaged across all reviews.
// Used for Swiss pairing seeding (GH #37).
func MeanScores(rs []Review) map[string]float64 {
	type scoreAccum struct {
		sum   float64
		count int
	}
	accum := make(map[string]*scoreAccum)

	for _, r := range rs {
		if r.HypothesisID == "" {
			continue
		}

		var c, n, t, s float64
		if r.Scores.Correctness != nil {
			c = float64(*r.Scores.Correctness)
		}
		if r.Scores.Novelty != nil {
			n = float64(*r.Scores.Novelty)
		}
		if r.Scores.Testability != nil {
			t = float64(*r.Scores.Testability)
		}
		if r.Scores.Safety != nil {
			s = float64(*r.Scores.Safety)
		}

		mean := (c + n + t + s) / 4.0

		a, ok := accum[r.HypothesisID]
		if !ok {
			a = &scoreAccum{}
			accum[r.HypothesisID] = a
		}
		a.sum += mean
		a.count++
	}

	scores := make(map[string]float64)
	for id, a := range accum {
		if a.count > 0 {
			scores[id] = a.sum / float64(a.count)
		}
	}
	return scores
}
