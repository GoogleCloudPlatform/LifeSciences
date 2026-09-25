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

package pairing

import (
	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/elo/internal/rating"
)

// StrategyA implements proximity-guided Elo pairing (the default strategy).
// It fills the budget in order: placement, refinement, exploration.
func StrategyA(
	ratings map[string]*rating.HypothesisRating,
	allHypotheses []string,
	graph *ProximityGraph,
	history *MatchHistory,
	epoch int,
	budget int,
	window int,
) []Pair {
	if budget <= 0 {
		return nil
	}

	var allPairs []Pair
	remaining := budget

	// 1. Placement: unrated hypotheses vs. anchors.
	placement := PlacementPairs(ratings, allHypotheses, history, epoch, remaining, window)
	allPairs = append(allPairs, placement...)
	remaining -= len(placement)
	if remaining <= 0 {
		return allPairs[:budget]
	}

	// 2. Refinement: top quartile, nearest-rated in same cluster.
	refinement := RefinementPairs(ratings, graph, history, epoch, remaining, window)
	allPairs = append(allPairs, refinement...)
	remaining -= len(refinement)
	if remaining <= 0 {
		return allPairs[:budget]
	}

	// 3. Exploration: 15% of remaining budget across clusters.
	explorationBudget := max(1, remaining*15/100)
	if explorationBudget > remaining {
		explorationBudget = remaining
	}

	alreadyPaired := make(map[string]bool)
	for _, p := range allPairs {
		alreadyPaired[p.A] = true
		alreadyPaired[p.B] = true
	}

	exploration := ExplorationPairs(ratings, graph, history, epoch, explorationBudget, alreadyPaired, window)
	allPairs = append(allPairs, exploration...)

	return allPairs
}
