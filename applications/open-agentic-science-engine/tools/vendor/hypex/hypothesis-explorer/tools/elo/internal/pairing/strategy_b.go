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
	"math"
	"sort"

	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/elo/internal/rating"
)

// ReviewScores maps hypothesis IDs to their mean review score for Swiss seeding.
type ReviewScores map[string]float64

// StrategyB implements Swiss-system tournament pairing.
// R rounds where R = ceil(log2(N)), each round pairs win-matched hypotheses.
// Round 1 is seeded by mean review score (or Elo rating if no review scores).
func StrategyB(
	ratings map[string]*rating.HypothesisRating,
	history *MatchHistory,
	epoch int,
	budget int,
	reviewScores ReviewScores,
	window int,
) []Pair {
	if budget <= 0 {
		return nil
	}

	ids := rating.SortedHypothesisIDs(ratings)
	n := len(ids)
	if n < 2 {
		return nil
	}

	totalRounds := int(math.Ceil(math.Log2(float64(n))))
	if totalRounds < 1 {
		totalRounds = 1
	}

	var allPairs []Pair

	// Track cumulative wins per hypothesis across Swiss rounds.
	wins := make(map[string]int)
	for _, id := range ids {
		wins[id] = 0
	}

	for round := 0; round < totalRounds && len(allPairs) < budget; round++ {
		var roundPairs []Pair

		if round == 0 {
			// Seed by review scores (or Elo if no review scores).
			roundPairs = swissRound1(ids, ratings, reviewScores, history, epoch, window)
		} else {
			// Subsequent rounds: pair by matching win count.
			roundPairs = swissRoundN(ids, wins, history, epoch, allPairs, window)
		}

		for _, p := range roundPairs {
			if len(allPairs) >= budget {
				break
			}
			allPairs = append(allPairs, p)
		}

		// Update simulated wins for the round (for Swiss pairing purposes,
		// the higher-seeded player is assumed to win).
		for _, p := range roundPairs {
			if ratings[p.A] != nil && ratings[p.B] != nil {
				if ratings[p.A].Elo >= ratings[p.B].Elo {
					wins[p.A]++
				} else {
					wins[p.B]++
				}
			} else {
				wins[p.A]++
			}
		}
	}

	return allPairs
}

// swissRound1 generates first-round pairings seeded by review score or Elo.
func swissRound1(
	ids []string,
	ratings map[string]*rating.HypothesisRating,
	reviewScores ReviewScores,
	history *MatchHistory,
	epoch int,
	window int,
) []Pair {
	// Sort by seeding criterion.
	sorted := make([]string, len(ids))
	copy(sorted, ids)

	sort.SliceStable(sorted, func(i, j int) bool {
		si := seedScore(sorted[i], ratings, reviewScores)
		sj := seedScore(sorted[j], ratings, reviewScores)
		if si != sj {
			return si > sj
		}
		return sorted[i] < sorted[j]
	})

	// Pair adjacent: 1v2, 3v4, etc.
	return pairAdjacent(sorted, history, epoch, window)
}

// swissRoundN generates subsequent-round pairings based on win count.
func swissRoundN(
	ids []string,
	wins map[string]int,
	history *MatchHistory,
	epoch int,
	previousPairs []Pair,
	window int,
) []Pair {
	// Group by win count.
	sorted := make([]string, len(ids))
	copy(sorted, ids)

	sort.SliceStable(sorted, func(i, j int) bool {
		if wins[sorted[i]] != wins[sorted[j]] {
			return wins[sorted[i]] > wins[sorted[j]]
		}
		return sorted[i] < sorted[j]
	})

	// Track already-paired in this Swiss tournament to avoid repeats.
	alreadyPaired := make(map[string]bool)
	for _, p := range previousPairs {
		alreadyPaired[pairKey(p.A, p.B)] = true
	}

	return pairAdjacentAvoidRepeats(sorted, history, epoch, alreadyPaired, window)
}

// seedScore returns the seeding score for a hypothesis.
func seedScore(id string, ratings map[string]*rating.HypothesisRating, reviewScores ReviewScores) float64 {
	if reviewScores != nil {
		if s, ok := reviewScores[id]; ok {
			return s
		}
	}
	if r, ok := ratings[id]; ok {
		return r.Elo
	}
	return rating.DefaultRating
}

// pairAdjacent pairs elements in adjacent order: [0,1], [2,3], etc.
func pairAdjacent(sorted []string, history *MatchHistory, epoch int, window int) []Pair {
	var pairs []Pair
	used := make(map[string]bool)

	for i := 0; i < len(sorted); i++ {
		if used[sorted[i]] {
			continue
		}
		for j := i + 1; j < len(sorted); j++ {
			if used[sorted[j]] {
				continue
			}
			if history.HasPlayedWithin(sorted[i], sorted[j], epoch, window) {
				continue
			}
			pairs = append(pairs, Pair{A: sorted[i], B: sorted[j]})
			used[sorted[i]] = true
			used[sorted[j]] = true
			break
		}
	}
	return pairs
}

// pairAdjacentAvoidRepeats pairs elements adjacently while avoiding Swiss repeats.
func pairAdjacentAvoidRepeats(
	sorted []string,
	history *MatchHistory,
	epoch int,
	alreadyPaired map[string]bool,
	window int,
) []Pair {
	var pairs []Pair
	used := make(map[string]bool)

	for i := 0; i < len(sorted); i++ {
		if used[sorted[i]] {
			continue
		}
		for j := i + 1; j < len(sorted); j++ {
			if used[sorted[j]] {
				continue
			}
			key := pairKey(sorted[i], sorted[j])
			if alreadyPaired[key] {
				continue
			}
			if history.HasPlayedWithin(sorted[i], sorted[j], epoch, window) {
				continue
			}
			pairs = append(pairs, Pair{A: sorted[i], B: sorted[j]})
			used[sorted[i]] = true
			used[sorted[j]] = true
			break
		}
	}
	return pairs
}
