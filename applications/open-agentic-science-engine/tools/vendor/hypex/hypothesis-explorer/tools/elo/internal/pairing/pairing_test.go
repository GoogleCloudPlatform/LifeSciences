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
	"testing"

	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/elo/internal/rating"
)

func makeRatings(entries map[string]float64) map[string]*rating.HypothesisRating {
	m := make(map[string]*rating.HypothesisRating)
	for id, elo := range entries {
		m[id] = &rating.HypothesisRating{Elo: elo, Matches: 3, Wins: 1}
	}
	return m
}

func TestPairKey(t *testing.T) {
	if pairKey("H-0002", "H-0001") != "H-0001:H-0002" {
		t.Error("pairKey should sort IDs")
	}
	if pairKey("H-0001", "H-0002") != "H-0001:H-0002" {
		t.Error("pairKey should be canonical")
	}
}

func TestMatchHistory(t *testing.T) {
	matches := []rating.Match{
		{ID: "M-0001", A: "H-0001", B: "H-0002", Epoch: 0, Winner: "H-0001"},
		{ID: "M-0002", A: "H-0001", B: "H-0003", Epoch: 1, Winner: "H-0003"},
	}
	mh := NewMatchHistory(matches)

	if !mh.HasPlayed("H-0001", "H-0002", 0) {
		t.Error("H-0001 and H-0002 should have played in epoch 0")
	}
	if mh.HasPlayed("H-0001", "H-0002", 1) {
		t.Error("H-0001 and H-0002 should not have played in epoch 1")
	}
	if !mh.HasEverPlayed("H-0001", "H-0003") {
		t.Error("H-0001 and H-0003 should have played")
	}
	if mh.HasEverPlayed("H-0002", "H-0003") {
		t.Error("H-0002 and H-0003 should not have played")
	}
	// Reversed order should also work.
	if !mh.HasPlayed("H-0002", "H-0001", 0) {
		t.Error("reversed order should also find the match")
	}
}

func TestPlacementPairs_NoRated(t *testing.T) {
	ratings := make(map[string]*rating.HypothesisRating)
	all := []string{"H-0001", "H-0002", "H-0003"}
	mh := NewMatchHistory(nil)

	pairs := PlacementPairs(ratings, all, mh, 0, 10, 1)
	// With no rated hypotheses, unrated should pair among themselves.
	if len(pairs) == 0 {
		t.Error("expected some placement pairs")
	}
	// Should get 3 pairs: (1,2), (1,3), (2,3)
	if len(pairs) != 3 {
		t.Errorf("expected 3 pairs, got %d", len(pairs))
	}
}

func TestPlacementPairs_WithAnchors(t *testing.T) {
	ratings := makeRatings(map[string]float64{
		"H-0001": 1600,
		"H-0002": 1500,
		"H-0003": 1400,
	})
	all := []string{"H-0001", "H-0002", "H-0003", "H-0004"} // H-0004 is unrated
	mh := NewMatchHistory(nil)

	pairs := PlacementPairs(ratings, all, mh, 0, 10, 1)
	// H-0004 should get 3 matches vs. anchors (high=H-0001, median=H-0002, low=H-0003).
	if len(pairs) != 3 {
		t.Errorf("expected 3 placement pairs, got %d", len(pairs))
	}
	for _, p := range pairs {
		if p.A != "H-0004" {
			t.Errorf("expected unrated hypothesis H-0004 as A, got %s", p.A)
		}
	}
}

func TestPlacementPairs_Budget(t *testing.T) {
	ratings := makeRatings(map[string]float64{
		"H-0001": 1600,
		"H-0002": 1500,
		"H-0003": 1400,
	})
	all := []string{"H-0001", "H-0002", "H-0003", "H-0004", "H-0005"}
	mh := NewMatchHistory(nil)

	pairs := PlacementPairs(ratings, all, mh, 0, 2, 1)
	if len(pairs) != 2 {
		t.Errorf("expected 2 pairs (budget limit), got %d", len(pairs))
	}
}

func TestPlacementPairs_RematchConstraint(t *testing.T) {
	ratings := makeRatings(map[string]float64{
		"H-0001": 1600,
		"H-0002": 1500,
		"H-0003": 1400,
	})
	all := []string{"H-0001", "H-0002", "H-0003", "H-0004"}
	matches := []rating.Match{
		{ID: "M-0001", A: "H-0004", B: "H-0001", Epoch: 0, Winner: "H-0001"},
	}
	mh := NewMatchHistory(matches)

	pairs := PlacementPairs(ratings, all, mh, 0, 10, 1)
	// H-0004 already played H-0001 in epoch 0, so should only get 2 pairs.
	for _, p := range pairs {
		if (p.A == "H-0004" && p.B == "H-0001") || (p.A == "H-0001" && p.B == "H-0004") {
			t.Error("should not rematch H-0004 vs H-0001 in same epoch")
		}
	}
	if len(pairs) != 2 {
		t.Errorf("expected 2 pairs, got %d", len(pairs))
	}
}

func TestPlacementPairs_SingleRatedHypothesis(t *testing.T) {
	ratings := makeRatings(map[string]float64{
		"H-0001": 1500,
	})
	all := []string{"H-0001", "H-0002"}
	mh := NewMatchHistory(nil)

	pairs := PlacementPairs(ratings, all, mh, 0, 10, 1)
	if len(pairs) != 1 {
		t.Errorf("expected 1 pair, got %d", len(pairs))
	}
}

func TestRefinementPairs_Basic(t *testing.T) {
	ratings := makeRatings(map[string]float64{
		"H-0001": 1600,
		"H-0002": 1590,
		"H-0003": 1500,
		"H-0004": 1400,
	})
	mh := NewMatchHistory(nil)

	pairs := RefinementPairs(ratings, nil, mh, 0, 10, 1)
	// Top quartile of 4 = 1 hypothesis (H-0001).
	// Should pair with nearest-rated (H-0002).
	if len(pairs) != 1 {
		t.Errorf("expected 1 refinement pair, got %d", len(pairs))
	}
	if len(pairs) > 0 {
		if pairs[0].A != "H-0001" || pairs[0].B != "H-0002" {
			t.Errorf("expected H-0001 vs H-0002, got %s vs %s", pairs[0].A, pairs[0].B)
		}
	}
}

func TestRefinementPairs_WithProximity(t *testing.T) {
	ratings := makeRatings(map[string]float64{
		"H-0001": 1600,
		"H-0002": 1590,
		"H-0003": 1580,
		"H-0004": 1400,
	})
	graph := &ProximityGraph{
		Clusters: map[string][]string{
			"C-01": {"H-0001", "H-0003"},
			"C-02": {"H-0002", "H-0004"},
		},
	}
	mh := NewMatchHistory(nil)

	pairs := RefinementPairs(ratings, graph, mh, 0, 10, 1)
	// H-0001 is top quartile, same cluster as H-0003.
	// Should pair H-0001 with H-0003 (same cluster, nearest rated in that cluster).
	if len(pairs) != 1 {
		t.Errorf("expected 1 refinement pair, got %d", len(pairs))
	}
	if len(pairs) > 0 && pairs[0].B != "H-0003" {
		t.Errorf("expected H-0001 vs H-0003 (same cluster), got %s vs %s", pairs[0].A, pairs[0].B)
	}
}

func TestRefinementPairs_TooFew(t *testing.T) {
	ratings := makeRatings(map[string]float64{
		"H-0001": 1500,
	})
	mh := NewMatchHistory(nil)

	pairs := RefinementPairs(ratings, nil, mh, 0, 10, 1)
	if len(pairs) != 0 {
		t.Errorf("expected 0 refinement pairs with 1 hypothesis, got %d", len(pairs))
	}
}

func TestExplorationPairs_NoClusters(t *testing.T) {
	ratings := makeRatings(map[string]float64{
		"H-0001": 1600,
		"H-0002": 1500,
		"H-0003": 1400,
		"H-0004": 1300,
	})
	mh := NewMatchHistory(nil)

	pairs := ExplorationPairs(ratings, nil, mh, 0, 2, nil, 1)
	if len(pairs) == 0 {
		t.Error("expected some exploration pairs")
	}
	if len(pairs) > 2 {
		t.Errorf("expected at most 2 pairs, got %d", len(pairs))
	}
}

func TestExplorationPairs_CrossCluster(t *testing.T) {
	ratings := makeRatings(map[string]float64{
		"H-0001": 1600,
		"H-0002": 1500,
		"H-0003": 1400,
		"H-0004": 1300,
	})
	graph := &ProximityGraph{
		Clusters: map[string][]string{
			"C-01": {"H-0001", "H-0002"},
			"C-02": {"H-0003", "H-0004"},
		},
	}
	mh := NewMatchHistory(nil)

	pairs := ExplorationPairs(ratings, graph, mh, 0, 5, nil, 1)
	// Should prefer cross-cluster pairings.
	for _, p := range pairs {
		cA := graph.HypothesisCluster(p.A)
		cB := graph.HypothesisCluster(p.B)
		if cA == cB {
			t.Errorf("exploration pair %s vs %s are in the same cluster %s", p.A, p.B, cA)
		}
	}
}

func TestStrategyA_EmptyRatings(t *testing.T) {
	ratings := make(map[string]*rating.HypothesisRating)
	mh := NewMatchHistory(nil)

	pairs := StrategyA(ratings, []string{"H-0001", "H-0002"}, nil, mh, 0, 10, 1)
	// No rated hypotheses, but 2 unrated — should get placement pairs.
	if len(pairs) != 1 {
		t.Errorf("expected 1 pair, got %d", len(pairs))
	}
}

func TestStrategyA_Full(t *testing.T) {
	ratings := makeRatings(map[string]float64{
		"H-0001": 1600,
		"H-0002": 1550,
		"H-0003": 1500,
		"H-0004": 1450,
	})
	all := []string{"H-0001", "H-0002", "H-0003", "H-0004", "H-0005"}
	mh := NewMatchHistory(nil)

	pairs := StrategyA(ratings, all, nil, mh, 0, 10, 1)
	if len(pairs) == 0 {
		t.Error("expected some pairs")
	}
	// Budget of 10 should not be exceeded.
	if len(pairs) > 10 {
		t.Errorf("exceeded budget: got %d pairs", len(pairs))
	}
}

func TestStrategyA_ZeroBudget(t *testing.T) {
	ratings := makeRatings(map[string]float64{"H-0001": 1500})
	mh := NewMatchHistory(nil)

	pairs := StrategyA(ratings, []string{"H-0001"}, nil, mh, 0, 0, 1)
	if len(pairs) != 0 {
		t.Errorf("expected 0 pairs with zero budget, got %d", len(pairs))
	}
}

func TestStrategyB_Basic(t *testing.T) {
	ratings := makeRatings(map[string]float64{
		"H-0001": 1600,
		"H-0002": 1550,
		"H-0003": 1500,
		"H-0004": 1450,
	})
	mh := NewMatchHistory(nil)

	pairs := StrategyB(ratings, mh, 0, 10, nil, 1)
	if len(pairs) == 0 {
		t.Error("expected some Swiss pairs")
	}
	// 4 hypotheses → ceil(log2(4)) = 2 rounds, each round has 2 pairs → up to 4 pairs.
	if len(pairs) > 4 {
		t.Errorf("expected at most 4 pairs for 4 hypotheses, got %d", len(pairs))
	}
}

func TestStrategyB_WithReviewScores(t *testing.T) {
	ratings := makeRatings(map[string]float64{
		"H-0001": 1500,
		"H-0002": 1500,
		"H-0003": 1500,
		"H-0004": 1500,
	})
	scores := ReviewScores{
		"H-0001": 4.5,
		"H-0002": 3.5,
		"H-0003": 2.5,
		"H-0004": 1.5,
	}
	mh := NewMatchHistory(nil)

	pairs := StrategyB(ratings, mh, 0, 10, scores, 1)
	if len(pairs) == 0 {
		t.Error("expected some Swiss pairs")
	}
	// First round should pair by seed: H-0001 vs H-0002, H-0003 vs H-0004.
	if len(pairs) >= 1 {
		if pairs[0].A != "H-0001" || pairs[0].B != "H-0002" {
			t.Errorf("round 1 pair 1: got %s vs %s, want H-0001 vs H-0002", pairs[0].A, pairs[0].B)
		}
	}
	if len(pairs) >= 2 {
		if pairs[1].A != "H-0003" || pairs[1].B != "H-0004" {
			t.Errorf("round 1 pair 2: got %s vs %s, want H-0003 vs H-0004", pairs[1].A, pairs[1].B)
		}
	}
}

func TestStrategyB_TwoHypotheses(t *testing.T) {
	ratings := makeRatings(map[string]float64{
		"H-0001": 1500,
		"H-0002": 1500,
	})
	mh := NewMatchHistory(nil)

	pairs := StrategyB(ratings, mh, 0, 10, nil, 1)
	// 2 hypotheses → ceil(log2(2)) = 1 round, 1 pair.
	if len(pairs) != 1 {
		t.Errorf("expected 1 pair, got %d", len(pairs))
	}
}

func TestStrategyB_SingleHypothesis(t *testing.T) {
	ratings := makeRatings(map[string]float64{
		"H-0001": 1500,
	})
	mh := NewMatchHistory(nil)

	pairs := StrategyB(ratings, mh, 0, 10, nil, 1)
	if len(pairs) != 0 {
		t.Errorf("expected 0 pairs with single hypothesis, got %d", len(pairs))
	}
}

func TestStrategyB_ZeroBudget(t *testing.T) {
	ratings := makeRatings(map[string]float64{
		"H-0001": 1500,
		"H-0002": 1500,
	})
	mh := NewMatchHistory(nil)

	pairs := StrategyB(ratings, mh, 0, 0, nil, 1)
	if len(pairs) != 0 {
		t.Errorf("expected 0 pairs with zero budget, got %d", len(pairs))
	}
}

func TestProximityGraph_HypothesisCluster(t *testing.T) {
	graph := &ProximityGraph{
		Clusters: map[string][]string{
			"C-01": {"H-0001", "H-0002"},
			"C-02": {"H-0003"},
		},
	}

	if c := graph.HypothesisCluster("H-0001"); c != "C-01" {
		t.Errorf("expected C-01, got %s", c)
	}
	if c := graph.HypothesisCluster("H-0003"); c != "C-02" {
		t.Errorf("expected C-02, got %s", c)
	}
	if c := graph.HypothesisCluster("H-9999"); c != "" {
		t.Errorf("expected empty, got %s", c)
	}

	var nilGraph *ProximityGraph
	if c := nilGraph.HypothesisCluster("H-0001"); c != "" {
		t.Errorf("nil graph should return empty, got %s", c)
	}
}

func TestSelectAnchors(t *testing.T) {
	tests := []struct {
		name      string
		standings []rating.Standing
		wantLen   int
	}{
		{"empty", nil, 0},
		{"single", []rating.Standing{{HypothesisID: "H-0001"}}, 1},
		{"two", []rating.Standing{{HypothesisID: "H-0001"}, {HypothesisID: "H-0002"}}, 2},
		{"three", []rating.Standing{
			{HypothesisID: "H-0001", Elo: 1600},
			{HypothesisID: "H-0002", Elo: 1500},
			{HypothesisID: "H-0003", Elo: 1400},
		}, 3},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			anchors := selectAnchors(tt.standings)
			if len(anchors) != tt.wantLen {
				t.Errorf("selectAnchors() returned %d anchors, want %d", len(anchors), tt.wantLen)
			}
		})
	}
}

func TestHasPlayedWithin(t *testing.T) {
	matches := []rating.Match{
		{ID: "M-0001", A: "H-0001", B: "H-0002", Epoch: 0, Winner: "H-0001"},
		{ID: "M-0002", A: "H-0001", B: "H-0003", Epoch: 1, Winner: "H-0003"},
		{ID: "M-0003", A: "H-0002", B: "H-0003", Epoch: 3, Winner: "H-0002"},
	}
	mh := NewMatchHistory(matches)

	tests := []struct {
		name         string
		a, b         string
		currentEpoch int
		window       int
		want         bool
	}{
		// window=0 means no constraint — always returns false.
		{"window_0_no_constraint", "H-0001", "H-0002", 0, 0, false},
		{"window_0_played_same_epoch", "H-0001", "H-0002", 0, 0, false},

		// window=1 is same-epoch only (equivalent to old HasPlayed).
		{"window_1_same_epoch", "H-0001", "H-0002", 0, 1, true},
		{"window_1_different_epoch", "H-0001", "H-0002", 1, 1, false},
		{"window_1_exact_epoch", "H-0001", "H-0003", 1, 1, true},

		// window=2: 2-epoch lookback.
		{"window_2_in_range", "H-0001", "H-0002", 1, 2, true},      // epoch 0 in [0,1]
		{"window_2_out_of_range", "H-0001", "H-0002", 2, 2, false}, // epoch 0 not in [1,2]
		{"window_2_boundary", "H-0001", "H-0003", 2, 2, true},      // epoch 1 in [1,2]
		{"window_2_too_old", "H-0001", "H-0003", 3, 2, false},      // epoch 1 not in [2,3]

		// Edge: play at epoch 0, window=2, currentEpoch=1.
		// Range is [0, 1], epoch 0 is in range.
		{"epoch0_window2_at_epoch1", "H-0001", "H-0002", 1, 2, true},

		// Pair that never played.
		{"never_played", "H-0001", "H-0004", 0, 2, false},

		// Reversed order should also work.
		{"reversed_order", "H-0002", "H-0001", 0, 1, true},

		// Negative window behaves like 0.
		{"negative_window", "H-0001", "H-0002", 0, -1, false},

		// Large window covers everything.
		{"large_window", "H-0001", "H-0002", 5, 100, true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := mh.HasPlayedWithin(tt.a, tt.b, tt.currentEpoch, tt.window)
			if got != tt.want {
				t.Errorf("HasPlayedWithin(%s, %s, %d, %d) = %v, want %v",
					tt.a, tt.b, tt.currentEpoch, tt.window, got, tt.want)
			}
		})
	}
}

func TestPlacementPairs_Window2(t *testing.T) {
	// With window=2, a match in epoch 0 should block pairing in epoch 1 but not epoch 2.
	ratings := makeRatings(map[string]float64{
		"H-0001": 1600,
		"H-0002": 1500,
		"H-0003": 1400,
	})
	all := []string{"H-0001", "H-0002", "H-0003", "H-0004"}
	matches := []rating.Match{
		{ID: "M-0001", A: "H-0004", B: "H-0001", Epoch: 0, Winner: "H-0001"},
	}
	mh := NewMatchHistory(matches)

	// Epoch 1, window 2: range [0,1]. H-0004 vs H-0001 played epoch 0 → blocked.
	pairs1 := PlacementPairs(ratings, all, mh, 1, 10, 2)
	for _, p := range pairs1 {
		if (p.A == "H-0004" && p.B == "H-0001") || (p.A == "H-0001" && p.B == "H-0004") {
			t.Error("window=2 should block H-0004 vs H-0001 in epoch 1 (played epoch 0)")
		}
	}

	// Epoch 2, window 2: range [1,2]. H-0004 vs H-0001 played epoch 0 → NOT blocked.
	pairs2 := PlacementPairs(ratings, all, mh, 2, 10, 2)
	found := false
	for _, p := range pairs2 {
		if (p.A == "H-0004" && p.B == "H-0001") || (p.A == "H-0001" && p.B == "H-0004") {
			found = true
		}
	}
	if !found {
		t.Error("window=2 should allow H-0004 vs H-0001 in epoch 2 (played epoch 0, outside window)")
	}
}

func TestStrategyA_Window2_BlocksRematch(t *testing.T) {
	ratings := makeRatings(map[string]float64{
		"H-0001": 1600,
		"H-0002": 1590,
		"H-0003": 1500,
		"H-0004": 1400,
	})
	all := []string{"H-0001", "H-0002", "H-0003", "H-0004"}

	// H-0001 and H-0002 played in epoch 0.
	matches := []rating.Match{
		{ID: "M-0001", A: "H-0001", B: "H-0002", Epoch: 0, Winner: "H-0001"},
	}
	mh := NewMatchHistory(matches)

	// With window=2 at epoch 1: range [0,1], so H-0001 vs H-0002 is blocked.
	pairs := StrategyA(ratings, all, nil, mh, 1, 10, 2)
	for _, p := range pairs {
		if (p.A == "H-0001" && p.B == "H-0002") || (p.A == "H-0002" && p.B == "H-0001") {
			t.Error("window=2 should block H-0001 vs H-0002 rematch in epoch 1")
		}
	}
}

func TestStrategyB_Window2(t *testing.T) {
	ratings := makeRatings(map[string]float64{
		"H-0001": 1600,
		"H-0002": 1550,
		"H-0003": 1500,
		"H-0004": 1450,
	})

	// H-0001 and H-0002 played in epoch 0.
	matches := []rating.Match{
		{ID: "M-0001", A: "H-0001", B: "H-0002", Epoch: 0, Winner: "H-0001"},
	}
	mh := NewMatchHistory(matches)

	// With window=2 at epoch 1, H-0001 vs H-0002 should be blocked.
	pairs := StrategyB(ratings, mh, 1, 10, nil, 2)
	for _, p := range pairs {
		if (p.A == "H-0001" && p.B == "H-0002") || (p.A == "H-0002" && p.B == "H-0001") {
			t.Error("window=2 should block H-0001 vs H-0002 in Swiss pairing at epoch 1")
		}
	}
	// Ensure we still get some pairs.
	if len(pairs) == 0 {
		t.Error("expected some Swiss pairs even with window constraint")
	}
}
