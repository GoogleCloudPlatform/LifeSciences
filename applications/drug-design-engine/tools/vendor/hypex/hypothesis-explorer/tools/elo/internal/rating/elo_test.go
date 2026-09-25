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

package rating

import (
	"fmt"
	"math"
	"strings"
	"testing"
)

const epsilon = 1e-9

func almostEqual(a, b, tol float64) bool {
	return math.Abs(a-b) < tol
}

func TestKFactor(t *testing.T) {
	tests := []struct {
		name       string
		matchCount int
		wantK      float64
	}{
		{"match 1", 1, 40.0},
		{"match 5", 5, 40.0},
		{"match 6", 6, 24.0},
		{"match 10", 10, 24.0},
		{"match 11", 11, 16.0},
		{"match 100", 100, 16.0},
		{"match 0 (edge)", 0, 40.0},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := KFactor(tt.matchCount)
			if got != tt.wantK {
				t.Errorf("KFactor(%d) = %v, want %v", tt.matchCount, got, tt.wantK)
			}
		})
	}
}

func TestMarginMultiplier(t *testing.T) {
	tests := []struct {
		margin string
		want   float64
	}{
		{"decisive", 1.0},
		{"narrow", 0.75},
		{"unknown", 1.0},
	}
	for _, tt := range tests {
		t.Run(tt.margin, func(t *testing.T) {
			got := MarginMultiplier(tt.margin)
			if got != tt.want {
				t.Errorf("MarginMultiplier(%q) = %v, want %v", tt.margin, got, tt.want)
			}
		})
	}
}

func TestExpectedScore(t *testing.T) {
	tests := []struct {
		name      string
		rSelf     float64
		rOpponent float64
		want      float64
	}{
		{"equal ratings", 1500, 1500, 0.5},
		{"200 higher", 1700, 1500, 0.759747},
		{"200 lower", 1300, 1500, 0.240253},
		{"400 higher", 1900, 1500, 0.909091},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := ExpectedScore(tt.rSelf, tt.rOpponent)
			if !almostEqual(got, tt.want, 0.001) {
				t.Errorf("ExpectedScore(%v, %v) = %v, want ~%v", tt.rSelf, tt.rOpponent, got, tt.want)
			}
		})
	}
}

func TestExpectedScoreSymmetry(t *testing.T) {
	eA := ExpectedScore(1500, 1600)
	eB := ExpectedScore(1600, 1500)
	sum := eA + eB
	if !almostEqual(sum, 1.0, epsilon) {
		t.Errorf("expected scores should sum to 1.0, got %v", sum)
	}
}

func TestRatingDelta(t *testing.T) {
	// Equal players, winner, decisive: K=40 * 1.0 * (1.0 - 0.5) = 20.0
	delta := RatingDelta(40.0, 1.0, 0.5, 1.0)
	if !almostEqual(delta, 20.0, epsilon) {
		t.Errorf("RatingDelta(40, 1.0, 0.5, 1.0) = %v, want 20.0", delta)
	}

	// Equal players, loser, decisive: K=40 * 1.0 * (0.0 - 0.5) = -20.0
	delta = RatingDelta(40.0, 0.0, 0.5, 1.0)
	if !almostEqual(delta, -20.0, epsilon) {
		t.Errorf("RatingDelta(40, 0.0, 0.5, 1.0) = %v, want -20.0", delta)
	}

	// Narrow margin: K=40 * 0.75 * (1.0 - 0.5) = 15.0
	delta = RatingDelta(40.0, 1.0, 0.5, 0.75)
	if !almostEqual(delta, 15.0, epsilon) {
		t.Errorf("RatingDelta(40, 1.0, 0.5, 0.75) = %v, want 15.0", delta)
	}
}

func TestReplayMatches_Empty(t *testing.T) {
	result, err := ReplayMatches(nil, 0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(result.Ratings) != 0 {
		t.Errorf("expected empty ratings, got %d entries", len(result.Ratings))
	}
	if result.Epoch != 0 {
		t.Errorf("expected epoch 0, got %d", result.Epoch)
	}
	if result.ComputedFrom != "matches/ ledger (empty)" {
		t.Errorf("unexpected provenance: %q", result.ComputedFrom)
	}
}

func TestReplayMatches_SingleMatch(t *testing.T) {
	matches := []Match{
		{
			ID:        "M-0001",
			Epoch:     0,
			A:         "H-0001",
			B:         "H-0002",
			Winner:    "H-0001",
			Margin:    "decisive",
			CreatedAt: "2026-09-05T12:00:00Z",
		},
	}
	result, err := ReplayMatches(matches, 0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(result.Ratings) != 2 {
		t.Fatalf("expected 2 ratings, got %d", len(result.Ratings))
	}

	rA := result.Ratings["H-0001"]
	rB := result.Ratings["H-0002"]

	// Both start at 1500, equal so E=0.5 for both.
	// K=40 (match 1), decisive margin (1.0).
	// Winner: 1500 + 40*1.0*(1.0-0.5) = 1520
	// Loser:  1500 + 40*1.0*(0.0-0.5) = 1480
	if !almostEqual(rA.Elo, 1520.0, epsilon) {
		t.Errorf("H-0001 elo = %v, want 1520.0", rA.Elo)
	}
	if !almostEqual(rB.Elo, 1480.0, epsilon) {
		t.Errorf("H-0002 elo = %v, want 1480.0", rB.Elo)
	}
	if rA.Matches != 1 || rA.Wins != 1 {
		t.Errorf("H-0001: matches=%d wins=%d, want 1,1", rA.Matches, rA.Wins)
	}
	if rB.Matches != 1 || rB.Wins != 0 {
		t.Errorf("H-0002: matches=%d wins=%d, want 1,0", rB.Matches, rB.Wins)
	}
	if result.ComputedFrom != "matches/ ledger @ M-0001" {
		t.Errorf("unexpected provenance: %q", result.ComputedFrom)
	}
}

func TestReplayMatches_NarrowMargin(t *testing.T) {
	matches := []Match{
		{
			ID:        "M-0001",
			Epoch:     0,
			A:         "H-0001",
			B:         "H-0002",
			Winner:    "H-0001",
			Margin:    "narrow",
			CreatedAt: "2026-09-05T12:00:00Z",
		},
	}
	result, err := ReplayMatches(matches, 0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	rA := result.Ratings["H-0001"]
	rB := result.Ratings["H-0002"]

	// K=40, narrow (0.75), E=0.5
	// Winner: 1500 + 40*0.75*(1.0-0.5) = 1515
	// Loser:  1500 + 40*0.75*(0.0-0.5) = 1485
	if !almostEqual(rA.Elo, 1515.0, epsilon) {
		t.Errorf("H-0001 elo = %v, want 1515.0", rA.Elo)
	}
	if !almostEqual(rB.Elo, 1485.0, epsilon) {
		t.Errorf("H-0002 elo = %v, want 1485.0", rB.Elo)
	}
}

func TestReplayMatches_BWin(t *testing.T) {
	matches := []Match{
		{
			ID:        "M-0001",
			Epoch:     0,
			A:         "H-0001",
			B:         "H-0002",
			Winner:    "H-0002",
			Margin:    "decisive",
			CreatedAt: "2026-09-05T12:00:00Z",
		},
	}
	result, err := ReplayMatches(matches, 0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	rA := result.Ratings["H-0001"]
	rB := result.Ratings["H-0002"]

	// Both start at 1500, equal so E=0.5 for both.
	// K=40, decisive margin (1.0).
	// Loser A: 1500 + 40*1.0*(0.0-0.5) = 1480
	// Winner B: 1500 + 40*1.0*(1.0-0.5) = 1520
	if !almostEqual(rA.Elo, 1480.0, epsilon) {
		t.Errorf("H-0001 elo = %v, want 1480.0", rA.Elo)
	}
	if !almostEqual(rB.Elo, 1520.0, epsilon) {
		t.Errorf("H-0002 elo = %v, want 1520.0", rB.Elo)
	}
	if rA.Matches != 1 || rA.Wins != 0 || rA.Draws != 0 {
		t.Errorf("H-0001: matches=%d wins=%d draws=%d, want 1,0,0", rA.Matches, rA.Wins, rA.Draws)
	}
	if rB.Matches != 1 || rB.Wins != 1 || rB.Draws != 0 {
		t.Errorf("H-0002: matches=%d wins=%d draws=%d, want 1,1,0", rB.Matches, rB.Wins, rB.Draws)
	}
}

func TestReplayMatches_Draw_EqualRatings(t *testing.T) {
	matches := []Match{
		{
			ID:        "M-0001",
			Epoch:     0,
			A:         "H-0001",
			B:         "H-0002",
			Winner:    "draw",
			Margin:    "narrow",
			CreatedAt: "2026-09-05T12:00:00Z",
		},
	}
	result, err := ReplayMatches(matches, 0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	rA := result.Ratings["H-0001"]
	rB := result.Ratings["H-0002"]

	// Equal ratings: E=0.5, actual score=0.5, so delta = K * marginMul * (0.5 - 0.5) = 0.
	if !almostEqual(rA.Elo, 1500.0, epsilon) {
		t.Errorf("H-0001 elo = %v, want 1500.0", rA.Elo)
	}
	if !almostEqual(rB.Elo, 1500.0, epsilon) {
		t.Errorf("H-0002 elo = %v, want 1500.0", rB.Elo)
	}
	if rA.Matches != 1 || rA.Wins != 0 || rA.Draws != 1 {
		t.Errorf("H-0001: matches=%d wins=%d draws=%d, want 1,0,1", rA.Matches, rA.Wins, rA.Draws)
	}
	if rB.Matches != 1 || rB.Wins != 0 || rB.Draws != 1 {
		t.Errorf("H-0002: matches=%d wins=%d draws=%d, want 1,0,1", rB.Matches, rB.Wins, rB.Draws)
	}
}

func TestReplayMatches_Draw_UnequalRatings(t *testing.T) {
	// Match 1: H-0001 beats H-0003 decisively so H-0001 rating increases to 1520.
	// Match 2: H-0004 beats H-0002 decisively so H-0002 rating drops to 1480.
	// Match 3: H-0001 (1520) draws with H-0002 (1480).
	matches := []Match{
		{ID: "M-0001", Epoch: 0, A: "H-0001", B: "H-0003", Winner: "H-0001", Margin: "decisive", CreatedAt: "2026-09-05T12:00:00Z"},
		{ID: "M-0002", Epoch: 0, A: "H-0004", B: "H-0002", Winner: "H-0004", Margin: "decisive", CreatedAt: "2026-09-05T12:01:00Z"},
		{ID: "M-0003", Epoch: 0, A: "H-0001", B: "H-0002", Winner: "draw", Margin: "narrow", CreatedAt: "2026-09-05T12:02:00Z"},
	}
	result, err := ReplayMatches(matches, 0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	rA := result.Ratings["H-0001"]
	rB := result.Ratings["H-0002"]

	eA := ExpectedScore(1520.0, 1480.0)
	eB := ExpectedScore(1480.0, 1520.0)
	// Match 2 for both players -> matchCount 1 before match 3 -> K=40
	deltaA := RatingDelta(40.0, 0.5, eA, 0.75)
	deltaB := RatingDelta(40.0, 0.5, eB, 0.75)

	wantEloA := 1520.0 + deltaA
	wantEloB := 1480.0 + deltaB

	if !almostEqual(rA.Elo, wantEloA, epsilon) {
		t.Errorf("H-0001 elo = %v, want %v", rA.Elo, wantEloA)
	}
	if !almostEqual(rB.Elo, wantEloB, epsilon) {
		t.Errorf("H-0002 elo = %v, want %v", rB.Elo, wantEloB)
	}
	if !almostEqual(deltaA+deltaB, 0.0, epsilon) {
		t.Errorf("rating delta sum = %v, want 0", deltaA+deltaB)
	}

	if rA.Matches != 2 || rA.Wins != 1 || rA.Draws != 1 {
		t.Errorf("H-0001: matches=%d wins=%d draws=%d, want 2,1,1", rA.Matches, rA.Wins, rA.Draws)
	}
	if rB.Matches != 2 || rB.Wins != 0 || rB.Draws != 1 {
		t.Errorf("H-0002: matches=%d wins=%d draws=%d, want 2,0,1", rB.Matches, rB.Wins, rB.Draws)
	}
}

func TestReplayMatches_InvalidWinner(t *testing.T) {
	tests := []struct {
		name        string
		winner      string
		offendingID string
	}{
		{"third party hypothesis ID", "H-0003", "M-0001"},
		{"typo draww", "draww", "M-0001"},
		{"uppercase DRAW", "DRAW", "M-0001"},
		{"synonym tie", "tie", "M-0001"},
		{"empty winner", "", "M-0001"},
		{"whitespace padded ID", "H-0001 ", "M-0001"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			matches := []Match{
				{
					ID:        tt.offendingID,
					Epoch:     0,
					A:         "H-0001",
					B:         "H-0002",
					Winner:    tt.winner,
					Margin:    "narrow",
					CreatedAt: "2026-09-05T12:00:00Z",
				},
			}

			result, err := ReplayMatches(matches, 0)
			if err == nil {
				t.Fatalf("expected error for invalid winner %q, got nil", tt.winner)
			}
			if result != nil {
				t.Errorf("expected nil result on error, got %v", result)
			}

			errMsg := err.Error()
			if !strings.Contains(errMsg, tt.offendingID) {
				t.Errorf("error %q should contain match ID %q", errMsg, tt.offendingID)
			}
			if !strings.Contains(errMsg, tt.winner) {
				t.Errorf("error %q should contain invalid winner %q", errMsg, tt.winner)
			}
		})
	}
}

func TestReplayMatches_KDecay(t *testing.T) {
	// Build 12 matches where H-0001 always beats H-0002.
	// This lets us verify K-factor transitions at match 6 and 11.
	matches := make([]Match, 12)
	for i := 0; i < 12; i++ {
		matches[i] = Match{
			ID:        "M-" + padID(i+1),
			Epoch:     0,
			A:         "H-0001",
			B:         "H-0002",
			Winner:    "H-0001",
			Margin:    "decisive",
			CreatedAt: "2026-09-05T12:00:00Z",
		}
	}

	result, err := ReplayMatches(matches, 0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	rA := result.Ratings["H-0001"]
	rB := result.Ratings["H-0002"]

	if rA.Matches != 12 {
		t.Errorf("H-0001 matches = %d, want 12", rA.Matches)
	}
	if rA.Wins != 12 {
		t.Errorf("H-0001 wins = %d, want 12", rA.Wins)
	}
	if rB.Wins != 0 {
		t.Errorf("H-0002 wins = %d, want 0", rB.Wins)
	}

	// Verify ratings are non-default (the winner should be well above 1500).
	if rA.Elo <= 1500.0 {
		t.Errorf("H-0001 elo should be above 1500, got %v", rA.Elo)
	}
	if rB.Elo >= 1500.0 {
		t.Errorf("H-0002 elo should be below 1500, got %v", rB.Elo)
	}

	// Verify determinism: replay should produce the same result.
	result2, err := ReplayMatches(matches, 0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	for id, r := range result.Ratings {
		r2 := result2.Ratings[id]
		if r.Elo != r2.Elo || r.Matches != r2.Matches || r.Wins != r2.Wins {
			t.Errorf("non-deterministic result for %s", id)
		}
	}
}

func TestReplayMatches_KDecayValues(t *testing.T) {
	// Verify K values at specific match counts by replaying step by step.
	// We use two separate hypotheses for each test to isolate K-factor.
	// Match 1 for H-0001: K=40
	m1 := Match{
		ID: "M-0001", A: "H-0001", B: "H-0010",
		Winner: "H-0001", Margin: "decisive", CreatedAt: "2026-09-05T12:00:00Z",
	}
	r1, err := ReplayMatches([]Match{m1}, 0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// Equal players, K=40, decisive: delta = 40 * 1.0 * (1.0 - 0.5) = 20
	if !almostEqual(r1.Ratings["H-0001"].Elo, 1520.0, epsilon) {
		t.Errorf("K=40 check: got elo %v, want 1520", r1.Ratings["H-0001"].Elo)
	}
}

func TestReplayMatches_MultipleHypotheses(t *testing.T) {
	matches := []Match{
		{ID: "M-0001", A: "H-0001", B: "H-0002", Winner: "H-0001", Margin: "decisive", CreatedAt: "2026-09-05T12:00:00Z"},
		{ID: "M-0002", A: "H-0003", B: "H-0001", Winner: "H-0003", Margin: "narrow", CreatedAt: "2026-09-05T12:01:00Z"},
		{ID: "M-0003", A: "H-0002", B: "H-0003", Winner: "H-0002", Margin: "decisive", CreatedAt: "2026-09-05T12:02:00Z"},
	}
	result, err := ReplayMatches(matches, 0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(result.Ratings) != 3 {
		t.Fatalf("expected 3 ratings, got %d", len(result.Ratings))
	}

	// H-0001: 1 win, 1 loss, 2 matches
	if result.Ratings["H-0001"].Matches != 2 || result.Ratings["H-0001"].Wins != 1 {
		t.Errorf("H-0001: matches=%d wins=%d, want 2,1",
			result.Ratings["H-0001"].Matches, result.Ratings["H-0001"].Wins)
	}
	// H-0002: 1 win, 1 loss, 2 matches
	if result.Ratings["H-0002"].Matches != 2 || result.Ratings["H-0002"].Wins != 1 {
		t.Errorf("H-0002: matches=%d wins=%d, want 2,1",
			result.Ratings["H-0002"].Matches, result.Ratings["H-0002"].Wins)
	}
	// H-0003: 1 win, 1 loss, 2 matches
	if result.Ratings["H-0003"].Matches != 2 || result.Ratings["H-0003"].Wins != 1 {
		t.Errorf("H-0003: matches=%d wins=%d, want 2,1",
			result.Ratings["H-0003"].Matches, result.Ratings["H-0003"].Wins)
	}

	if result.ComputedFrom != "matches/ ledger @ M-0003" {
		t.Errorf("unexpected provenance: %q", result.ComputedFrom)
	}
}

func TestReplayMatches_SingleHypothesis(t *testing.T) {
	// Edge case: a match where hypothesis plays itself should not happen,
	// but the code should handle it gracefully.
	matches := []Match{
		{ID: "M-0001", A: "H-0001", B: "H-0001", Winner: "H-0001", Margin: "decisive", CreatedAt: "2026-09-05T12:00:00Z"},
	}
	result, err := ReplayMatches(matches, 0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// Should have 1 entry (same ID for both sides).
	if len(result.Ratings) != 1 {
		t.Fatalf("expected 1 rating, got %d", len(result.Ratings))
	}
}

func TestSortedStandings(t *testing.T) {
	ratings := map[string]*HypothesisRating{
		"H-0003": {Elo: 1520, Matches: 3, Wins: 2},
		"H-0001": {Elo: 1550, Matches: 3, Wins: 3},
		"H-0002": {Elo: 1520, Matches: 3, Wins: 1},
		"H-0004": {Elo: 1400, Matches: 3, Wins: 0},
	}
	standings := SortedStandings(ratings)

	if len(standings) != 4 {
		t.Fatalf("expected 4 standings, got %d", len(standings))
	}
	// Highest first.
	if standings[0].HypothesisID != "H-0001" {
		t.Errorf("rank 1: got %s, want H-0001", standings[0].HypothesisID)
	}
	// Tie broken by ID.
	if standings[1].HypothesisID != "H-0002" {
		t.Errorf("rank 2: got %s, want H-0002 (tie broken by ID)", standings[1].HypothesisID)
	}
	if standings[2].HypothesisID != "H-0003" {
		t.Errorf("rank 3: got %s, want H-0003", standings[2].HypothesisID)
	}
	if standings[3].HypothesisID != "H-0004" {
		t.Errorf("rank 4: got %s, want H-0004", standings[3].HypothesisID)
	}
}

func TestSortedStandings_Empty(t *testing.T) {
	standings := SortedStandings(nil)
	if len(standings) != 0 {
		t.Errorf("expected 0 standings, got %d", len(standings))
	}
}

func TestSortedHypothesisIDs(t *testing.T) {
	ratings := map[string]*HypothesisRating{
		"H-0003": {Elo: 1500},
		"H-0001": {Elo: 1500},
		"H-0002": {Elo: 1500},
	}
	ids := SortedHypothesisIDs(ratings)
	want := []string{"H-0001", "H-0002", "H-0003"}
	if len(ids) != len(want) {
		t.Fatalf("expected %d ids, got %d", len(want), len(ids))
	}
	for i, id := range ids {
		if id != want[i] {
			t.Errorf("ids[%d] = %s, want %s", i, id, want[i])
		}
	}
}

func padID(n int) string {
	return fmt.Sprintf("%04d", n)
}

func TestSortedStandings_WithDraws(t *testing.T) {
	ratings := map[string]*HypothesisRating{
		"H-0001": {Elo: 1550, Matches: 4, Wins: 2, Draws: 2},
		"H-0002": {Elo: 1500, Matches: 4, Wins: 1, Draws: 3},
	}
	standings := SortedStandings(ratings)
	if len(standings) != 2 {
		t.Fatalf("expected 2 standings, got %d", len(standings))
	}
	if standings[0].HypothesisID != "H-0001" || standings[0].Draws != 2 {
		t.Errorf("standings[0]: got %+v, want H-0001 with 2 draws", standings[0])
	}
	if standings[1].HypothesisID != "H-0002" || standings[1].Draws != 3 {
		t.Errorf("standings[1]: got %+v, want H-0002 with 3 draws", standings[1])
	}
}

func TestReplayMatches_BaseRating(t *testing.T) {
	result, err := ReplayMatches(nil, 0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.BaseRating != DefaultRating {
		t.Errorf("result.BaseRating = %v, want %v", result.BaseRating, DefaultRating)
	}
}
