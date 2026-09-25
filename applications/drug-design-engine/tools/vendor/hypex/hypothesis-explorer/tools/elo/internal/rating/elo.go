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

// Package rating implements the Elo rating algorithm with K-factor decay
// for hypothesis tournament evaluation.
package rating

import (
	"fmt"
	"math"
	"sort"
)

// DefaultRating is the initial Elo rating for new hypotheses.
const DefaultRating = 1500.0

// KFactor returns the K-factor for a hypothesis with the given match count.
// Matches 1-5: K=40, matches 6-10: K=24, matches 11+: K=16.
func KFactor(matchCount int) float64 {
	switch {
	case matchCount <= 5:
		return 40.0
	case matchCount <= 10:
		return 24.0
	default:
		return 16.0
	}
}

// MarginMultiplier returns the margin adjustment for the K-factor.
// "decisive" wins use 1.0; "narrow" wins use 0.75.
func MarginMultiplier(margin string) float64 {
	if margin == "narrow" {
		return 0.75
	}
	return 1.0
}

// ExpectedScore computes the expected score for a player with rating rSelf
// against an opponent with rating rOpponent.
// E = 1 / (1 + 10^((R_opponent - R_self) / 400))
func ExpectedScore(rSelf, rOpponent float64) float64 {
	return 1.0 / (1.0 + math.Pow(10.0, (rOpponent-rSelf)/400.0))
}

// RatingDelta computes the rating change for a single match.
// k is the K-factor, s is the actual score (1.0 for win, 0.0 for loss),
// e is the expected score, and marginMul is the margin multiplier.
func RatingDelta(k, s, e, marginMul float64) float64 {
	return k * marginMul * (s - e)
}

// HypothesisRating tracks the current rating state for a hypothesis.
type HypothesisRating struct {
	Elo     float64 `json:"elo"`
	Matches int     `json:"matches"`
	Wins    int     `json:"wins"`
	Draws   int     `json:"draws"`
}

// Match represents a parsed match record relevant to Elo computation.
type Match struct {
	ID        string `json:"id"`
	Epoch     int    `json:"epoch"`
	A         string `json:"a"`
	B         string `json:"b"`
	Winner    string `json:"winner"`
	Margin    string `json:"margin"`
	CreatedAt string `json:"created_at"`
}

// RatingsResult holds the computed ratings for an epoch.
type RatingsResult struct {
	Epoch        int                          `json:"epoch"`
	BaseRating   float64                      `json:"base_rating"`
	Ratings      map[string]*HypothesisRating `json:"ratings"`
	ComputedFrom string                       `json:"computed_from"`
}

// ReplayMatches computes Elo ratings by replaying matches in chronological order.
// Matches must be pre-sorted by CreatedAt then by ID for determinism.
// Returns the resulting ratings map, or an error if a match record has an invalid winner.
func ReplayMatches(matches []Match, epoch int) (*RatingsResult, error) {
	ratings := make(map[string]*HypothesisRating)

	// ensure ensures a hypothesis exists in the ratings map.
	ensure := func(id string) {
		if _, ok := ratings[id]; !ok {
			ratings[id] = &HypothesisRating{Elo: DefaultRating}
		}
	}

	var lastMatchID string
	for _, m := range matches {
		ensure(m.A)
		ensure(m.B)

		rA := ratings[m.A]
		rB := ratings[m.B]

		// Determine scores: winner gets 1.0, loser gets 0.0, draw gets 0.5 each.
		var sA, sB float64
		switch m.Winner {
		case m.A:
			sA, sB = 1.0, 0.0
			rA.Wins++
		case m.B:
			sA, sB = 0.0, 1.0
			rB.Wins++
		case "draw":
			sA, sB = 0.5, 0.5
			rA.Draws++
			rB.Draws++
		default:
			return nil, fmt.Errorf("match %s: invalid winner %q (expected %q, %q, or \"draw\")", m.ID, m.Winner, m.A, m.B)
		}

		// Expected scores.
		eA := ExpectedScore(rA.Elo, rB.Elo)
		eB := ExpectedScore(rB.Elo, rA.Elo)

		// K-factors based on match count before this match.
		kA := KFactor(rA.Matches + 1)
		kB := KFactor(rB.Matches + 1)

		marginMul := MarginMultiplier(m.Margin)

		// Update ratings.
		rA.Elo += RatingDelta(kA, sA, eA, marginMul)
		rB.Elo += RatingDelta(kB, sB, eB, marginMul)

		rA.Matches++
		rB.Matches++

		lastMatchID = m.ID
	}

	provenance := "matches/ ledger (empty)"
	if lastMatchID != "" {
		provenance = "matches/ ledger @ " + lastMatchID
	}

	return &RatingsResult{
		Epoch:        epoch,
		BaseRating:   DefaultRating,
		Ratings:      ratings,
		ComputedFrom: provenance,
	}, nil
}

// Standing represents a single entry in the sorted standings.
type Standing struct {
	HypothesisID string
	Elo          float64
	Matches      int
	Wins         int
	Draws        int
}

// SortedStandings returns the ratings as a sorted list (highest Elo first).
// Ties are broken by hypothesis ID for determinism.
func SortedStandings(ratings map[string]*HypothesisRating) []Standing {
	standings := make([]Standing, 0, len(ratings))
	for id, r := range ratings {
		standings = append(standings, Standing{
			HypothesisID: id,
			Elo:          r.Elo,
			Matches:      r.Matches,
			Wins:         r.Wins,
			Draws:        r.Draws,
		})
	}
	sort.Slice(standings, func(i, j int) bool {
		if standings[i].Elo != standings[j].Elo {
			return standings[i].Elo > standings[j].Elo
		}
		return standings[i].HypothesisID < standings[j].HypothesisID
	})
	return standings
}

// SortedHypothesisIDs returns hypothesis IDs from a ratings map sorted
// alphabetically for deterministic iteration.
func SortedHypothesisIDs(ratings map[string]*HypothesisRating) []string {
	ids := make([]string, 0, len(ratings))
	for id := range ratings {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}
