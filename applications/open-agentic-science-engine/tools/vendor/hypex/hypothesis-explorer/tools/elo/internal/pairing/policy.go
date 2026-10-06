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

// Package pairing implements match pairing strategies for hypothesis tournaments.
package pairing

import (
	"sort"

	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/elo/internal/rating"
)

// Pair represents a single proposed match pairing.
type Pair struct {
	A string `json:"a"`
	B string `json:"b"`
}

// ProximityGraph represents the cluster membership of hypotheses.
type ProximityGraph struct {
	Clusters map[string][]string `json:"clusters"` // cluster ID -> hypothesis IDs
}

// HypothesisCluster returns the cluster ID for a hypothesis, or "" if not found.
func (pg *ProximityGraph) HypothesisCluster(hypothesisID string) string {
	if pg == nil {
		return ""
	}
	for clusterID, members := range pg.Clusters {
		for _, m := range members {
			if m == hypothesisID {
				return clusterID
			}
		}
	}
	return ""
}

// MatchHistory tracks which hypotheses have already played each other.
type MatchHistory struct {
	// Pairs maps "H-XXXX:H-YYYY" (sorted) to the list of epochs in which they matched.
	Pairs map[string][]int
}

// NewMatchHistory creates a MatchHistory from a slice of matches.
func NewMatchHistory(matches []rating.Match) *MatchHistory {
	mh := &MatchHistory{Pairs: make(map[string][]int)}
	for _, m := range matches {
		key := pairKey(m.A, m.B)
		mh.Pairs[key] = append(mh.Pairs[key], m.Epoch)
	}
	return mh
}

// HasPlayed returns true if a and b have played in the given epoch.
func (mh *MatchHistory) HasPlayed(a, b string, epoch int) bool {
	key := pairKey(a, b)
	for _, e := range mh.Pairs[key] {
		if e == epoch {
			return true
		}
	}
	return false
}

// HasEverPlayed returns true if a and b have played in any epoch.
func (mh *MatchHistory) HasEverPlayed(a, b string) bool {
	key := pairKey(a, b)
	return len(mh.Pairs[key]) > 0
}

// HasPlayedWithin returns true if a and b have played in any epoch
// within [currentEpoch-window+1, currentEpoch] inclusive.
// A window of 0 means no rematch constraint. A window of 1 is same-epoch only.
func (mh *MatchHistory) HasPlayedWithin(a, b string, currentEpoch, window int) bool {
	if window <= 0 {
		return false
	}
	key := pairKey(a, b)
	earliest := currentEpoch - window + 1
	for _, e := range mh.Pairs[key] {
		if e >= earliest && e <= currentEpoch {
			return true
		}
	}
	return false
}

// pairKey returns a canonical key for two hypothesis IDs.
func pairKey(a, b string) string {
	if a > b {
		a, b = b, a
	}
	return a + ":" + b
}

// PlacementPairs generates placement pairings for unrated hypotheses.
// Unrated hypotheses get 3 matches each vs. high, median, low anchors from
// current standings. If fewer than 3 rated hypotheses exist, pair against
// whatever is available.
func PlacementPairs(
	ratings map[string]*rating.HypothesisRating,
	allHypotheses []string,
	history *MatchHistory,
	epoch int,
	budget int,
	window int,
) []Pair {
	if budget <= 0 {
		return nil
	}

	// Find unrated hypotheses.
	var unrated []string
	for _, h := range allHypotheses {
		if _, ok := ratings[h]; !ok {
			unrated = append(unrated, h)
		}
	}
	sort.Strings(unrated) // deterministic order

	if len(unrated) == 0 {
		return nil
	}

	// Get rated standings for anchors.
	standings := rating.SortedStandings(ratings)
	if len(standings) == 0 {
		// No rated hypotheses — pair unrated against each other.
		return pairUnratedAmongThemselves(unrated, history, epoch, budget, window)
	}

	// Select anchors: high, median, low.
	anchors := selectAnchors(standings)

	var pairs []Pair
	for _, h := range unrated {
		for _, anchor := range anchors {
			if len(pairs) >= budget {
				return pairs
			}
			if h == anchor {
				continue
			}
			if history.HasPlayedWithin(h, anchor, epoch, window) {
				continue
			}
			pairs = append(pairs, Pair{A: h, B: anchor})
		}
	}
	return pairs
}

// selectAnchors picks high, median, low anchors from sorted standings.
func selectAnchors(standings []rating.Standing) []string {
	n := len(standings)
	if n == 0 {
		return nil
	}
	if n == 1 {
		return []string{standings[0].HypothesisID}
	}
	if n == 2 {
		return []string{standings[0].HypothesisID, standings[1].HypothesisID}
	}
	high := standings[0].HypothesisID
	low := standings[n-1].HypothesisID
	median := standings[n/2].HypothesisID
	return []string{high, median, low}
}

// pairUnratedAmongThemselves pairs unrated hypotheses with each other when
// no rated hypotheses exist yet.
func pairUnratedAmongThemselves(unrated []string, history *MatchHistory, epoch int, budget int, window int) []Pair {
	var pairs []Pair
	for i := 0; i < len(unrated) && len(pairs) < budget; i++ {
		for j := i + 1; j < len(unrated) && len(pairs) < budget; j++ {
			if !history.HasPlayedWithin(unrated[i], unrated[j], epoch, window) {
				pairs = append(pairs, Pair{A: unrated[i], B: unrated[j]})
			}
		}
	}
	return pairs
}

// RefinementPairs generates refinement pairings for top-quartile hypotheses.
// Pairs nearest-rated hypotheses within the same proximity cluster.
// Falls back to nearest-rated regardless of cluster if no proximity data.
func RefinementPairs(
	ratings map[string]*rating.HypothesisRating,
	graph *ProximityGraph,
	history *MatchHistory,
	epoch int,
	budget int,
	window int,
) []Pair {
	if budget <= 0 {
		return nil
	}

	standings := rating.SortedStandings(ratings)
	if len(standings) < 2 {
		return nil
	}

	// Top quartile.
	topN := len(standings) / 4
	if topN < 1 {
		topN = 1
	}
	topQ := standings[:topN]

	used := make(map[string]bool)
	var pairs []Pair

	for _, s := range topQ {
		if used[s.HypothesisID] || len(pairs) >= budget {
			continue
		}

		// Find nearest-rated in same cluster (or any if no proximity data).
		partner := findNearestPartner(s, standings, graph, history, epoch, used, window)
		if partner == "" {
			continue
		}

		pairs = append(pairs, Pair{A: s.HypothesisID, B: partner})
		used[s.HypothesisID] = true
		used[partner] = true
	}
	return pairs
}

// findNearestPartner finds the nearest-rated partner for a hypothesis.
func findNearestPartner(
	s rating.Standing,
	standings []rating.Standing,
	graph *ProximityGraph,
	history *MatchHistory,
	epoch int,
	used map[string]bool,
	window int,
) string {
	cluster := ""
	if graph != nil {
		cluster = graph.HypothesisCluster(s.HypothesisID)
	}

	var best string
	bestDiff := float64(1e9)

	for _, other := range standings {
		if other.HypothesisID == s.HypothesisID || used[other.HypothesisID] {
			continue
		}
		if history.HasPlayedWithin(s.HypothesisID, other.HypothesisID, epoch, window) {
			continue
		}

		// If we have proximity data, prefer same cluster.
		if graph != nil && cluster != "" {
			otherCluster := graph.HypothesisCluster(other.HypothesisID)
			if otherCluster != cluster {
				continue
			}
		}

		diff := abs(s.Elo - other.Elo)
		if diff < bestDiff {
			bestDiff = diff
			best = other.HypothesisID
		}
	}

	// Fallback: if no same-cluster partner found, try any.
	if best == "" && graph != nil && cluster != "" {
		for _, other := range standings {
			if other.HypothesisID == s.HypothesisID || used[other.HypothesisID] {
				continue
			}
			if history.HasPlayedWithin(s.HypothesisID, other.HypothesisID, epoch, window) {
				continue
			}
			diff := abs(s.Elo - other.Elo)
			if diff < bestDiff {
				bestDiff = diff
				best = other.HypothesisID
			}
		}
	}

	return best
}

// ExplorationPairs generates exploration pairings across different clusters.
// If no proximity data, pairs randomly (deterministically by sorted ID).
func ExplorationPairs(
	ratings map[string]*rating.HypothesisRating,
	graph *ProximityGraph,
	history *MatchHistory,
	epoch int,
	budget int,
	alreadyPaired map[string]bool,
	window int,
) []Pair {
	if budget <= 0 {
		return nil
	}

	standings := rating.SortedStandings(ratings)
	if len(standings) < 2 {
		return nil
	}

	var pairs []Pair
	used := make(map[string]bool)
	for k, v := range alreadyPaired {
		used[k] = v
	}

	for i := 0; i < len(standings) && len(pairs) < budget; i++ {
		a := standings[i]
		if used[a.HypothesisID] {
			continue
		}

		for j := i + 1; j < len(standings) && len(pairs) < budget; j++ {
			b := standings[j]
			if used[b.HypothesisID] {
				continue
			}
			if history.HasPlayedWithin(a.HypothesisID, b.HypothesisID, epoch, window) {
				continue
			}

			// For exploration, prefer cross-cluster pairings.
			if graph != nil {
				cA := graph.HypothesisCluster(a.HypothesisID)
				cB := graph.HypothesisCluster(b.HypothesisID)
				if cA != "" && cB != "" && cA == cB {
					continue // same cluster, skip for exploration
				}
			}

			pairs = append(pairs, Pair{A: a.HypothesisID, B: b.HypothesisID})
			used[a.HypothesisID] = true
			used[b.HypothesisID] = true
			break
		}
	}
	return pairs
}

func abs(x float64) float64 {
	if x < 0 {
		return -x
	}
	return x
}
