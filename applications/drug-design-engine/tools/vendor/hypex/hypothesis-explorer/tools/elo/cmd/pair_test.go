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

package cmd

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/elo/internal/pairing"
	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/elo/internal/rating"
)

// TestCollectHypothesisIDs_Epoch0Bootstrap verifies that collectHypothesisIDs
// discovers hypothesis IDs from the hypotheses/ directory even when no ratings
// or match files exist. This is the epoch-0 bootstrap case: without it,
// `elo pair --epoch 0` returns empty pairings and the tournament stalls.
func TestCollectHypothesisIDs_Epoch0Bootstrap(t *testing.T) {
	// Create a temporary run directory with hypothesis files but no ratings/matches.
	runDir := t.TempDir()
	hypDir := filepath.Join(runDir, "hypotheses")
	if err := os.MkdirAll(hypDir, 0o755); err != nil {
		t.Fatal(err)
	}

	// Write minimal hypothesis JSON files (only need to exist with H-NNNN.json naming).
	hypotheses := []string{"H-0001", "H-0002", "H-0003"}
	for _, id := range hypotheses {
		data, _ := json.Marshal(map[string]string{"id": id, "title": "Test " + id})
		if err := os.WriteFile(filepath.Join(hypDir, id+".json"), data, 0o644); err != nil {
			t.Fatal(err)
		}
	}

	// No ratings, no matches — this is the exact epoch-0 scenario.
	ratings := make(map[string]*rating.HypothesisRating)
	var matches []rating.Match

	ids, err := collectHypothesisIDs(runDir, ratings, matches)
	if err != nil {
		t.Fatalf("collectHypothesisIDs returned error: %v", err)
	}

	if len(ids) != 3 {
		t.Fatalf("expected 3 hypothesis IDs, got %d: %v", len(ids), ids)
	}

	// IDs should be sorted.
	for i, want := range hypotheses {
		if ids[i] != want {
			t.Errorf("ids[%d] = %q, want %q", i, ids[i], want)
		}
	}
}

// TestCollectHypothesisIDs_UnionWithRatingsAndMatches verifies that hypotheses
// from the directory are merged (unioned) with those from ratings and matches,
// so a hypothesis that exists on disk but hasn't been rated or matched yet is
// still included.
func TestCollectHypothesisIDs_UnionWithRatingsAndMatches(t *testing.T) {
	runDir := t.TempDir()
	hypDir := filepath.Join(runDir, "hypotheses")
	if err := os.MkdirAll(hypDir, 0o755); err != nil {
		t.Fatal(err)
	}

	// Hypothesis files on disk: H-0001, H-0002, H-0003, H-0004.
	for _, id := range []string{"H-0001", "H-0002", "H-0003", "H-0004"} {
		data, _ := json.Marshal(map[string]string{"id": id})
		if err := os.WriteFile(filepath.Join(hypDir, id+".json"), data, 0o644); err != nil {
			t.Fatal(err)
		}
	}

	// Ratings only know about H-0001 and H-0002.
	ratings := map[string]*rating.HypothesisRating{
		"H-0001": {Elo: 1500, Matches: 1, Wins: 1},
		"H-0002": {Elo: 1500, Matches: 1, Wins: 0},
	}

	// Matches only know about H-0001 vs H-0002.
	matches := []rating.Match{
		{ID: "M-0001", A: "H-0001", B: "H-0002", Epoch: 0, Winner: "H-0001"},
	}

	ids, err := collectHypothesisIDs(runDir, ratings, matches)
	if err != nil {
		t.Fatalf("collectHypothesisIDs returned error: %v", err)
	}

	// Should have all 4 hypotheses — the union of all sources.
	if len(ids) != 4 {
		t.Fatalf("expected 4 hypothesis IDs (union of all sources), got %d: %v", len(ids), ids)
	}

	want := []string{"H-0001", "H-0002", "H-0003", "H-0004"}
	for i, w := range want {
		if ids[i] != w {
			t.Errorf("ids[%d] = %q, want %q", i, ids[i], w)
		}
	}
}

// TestCollectHypothesisIDs_NoHypothesesDir verifies graceful handling when
// the hypotheses/ directory does not exist (falls back to ratings+matches only).
func TestCollectHypothesisIDs_NoHypothesesDir(t *testing.T) {
	runDir := t.TempDir()
	// No hypotheses/ directory — only ratings.
	ratings := map[string]*rating.HypothesisRating{
		"H-0001": {Elo: 1500, Matches: 1, Wins: 1},
	}

	ids, err := collectHypothesisIDs(runDir, ratings, nil)
	if err != nil {
		t.Fatalf("collectHypothesisIDs returned error: %v", err)
	}

	if len(ids) != 1 || ids[0] != "H-0001" {
		t.Errorf("expected [H-0001], got %v", ids)
	}
}

// TestCollectHypothesisIDs_IgnoresNonHypothesisFiles verifies that files in
// the hypotheses/ directory that don't match the H-NNNN.json naming pattern
// are ignored.
func TestCollectHypothesisIDs_IgnoresNonHypothesisFiles(t *testing.T) {
	runDir := t.TempDir()
	hypDir := filepath.Join(runDir, "hypotheses")
	if err := os.MkdirAll(hypDir, 0o755); err != nil {
		t.Fatal(err)
	}

	// Valid hypothesis file.
	data, _ := json.Marshal(map[string]string{"id": "H-0001"})
	os.WriteFile(filepath.Join(hypDir, "H-0001.json"), data, 0o644)

	// Files that should be ignored.
	os.WriteFile(filepath.Join(hypDir, "README.md"), []byte("# notes"), 0o644)
	os.WriteFile(filepath.Join(hypDir, ".DS_Store"), []byte{}, 0o644)
	os.WriteFile(filepath.Join(hypDir, "draft.json"), []byte("{}"), 0o644)

	ids, err := collectHypothesisIDs(runDir, nil, nil)
	if err != nil {
		t.Fatalf("collectHypothesisIDs returned error: %v", err)
	}

	if len(ids) != 1 || ids[0] != "H-0001" {
		t.Errorf("expected [H-0001], got %v", ids)
	}
}

// TestLoadProximityGraph_NotExist verifies that loadProximityGraph returns nil (no error)
// when proximity/clusters.json does not exist, even if proximity/graph.json exists.
func TestLoadProximityGraph_NotExist(t *testing.T) {
	runDir := t.TempDir()

	// 1. Proximity directory doesn't exist at all.
	graph, err := loadProximityGraph(runDir)
	if err != nil {
		t.Fatalf("unexpected error when proximity dir does not exist: %v", err)
	}
	if graph != nil {
		t.Fatalf("expected nil graph, got %v", graph)
	}

	// 2. Proximity directory has graph.json (the old file), but not clusters.json.
	proxDir := filepath.Join(runDir, "proximity")
	if err := os.MkdirAll(proxDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(proxDir, "graph.json"), []byte(`{"nodes":{}}`), 0o644); err != nil {
		t.Fatal(err)
	}

	graph, err = loadProximityGraph(runDir)
	if err != nil {
		t.Fatalf("unexpected error when clusters.json does not exist: %v", err)
	}
	if graph != nil {
		t.Fatalf("expected nil graph when only graph.json exists, got %v", graph)
	}
}

// TestLoadProximityGraph_ValidClusters verifies that loadProximityGraph successfully
// parses proximity/clusters.json shaped as output by the Python prox tool.
func TestLoadProximityGraph_ValidClusters(t *testing.T) {
	runDir := t.TempDir()
	proxDir := filepath.Join(runDir, "proximity")
	if err := os.MkdirAll(proxDir, 0o755); err != nil {
		t.Fatal(err)
	}

	// Real prox-shaped output from prox clusters.
	clustersContent := `{
  "clusters": {
    "C-01": ["H-0001", "H-0011"],
    "C-02": ["H-0002"],
    "C-03": ["H-0003", "H-0004", "H-0010"]
  },
  "algorithm": "louvain",
  "num_clusters": 3
}`
	if err := os.WriteFile(filepath.Join(proxDir, "clusters.json"), []byte(clustersContent), 0o644); err != nil {
		t.Fatal(err)
	}

	graph, err := loadProximityGraph(runDir)
	if err != nil {
		t.Fatalf("loadProximityGraph returned error: %v", err)
	}
	if graph == nil {
		t.Fatal("expected non-nil graph")
	}

	if len(graph.Clusters) != 3 {
		t.Fatalf("expected 3 clusters, got %d", len(graph.Clusters))
	}
	if c := graph.HypothesisCluster("H-0001"); c != "C-01" {
		t.Errorf("expected H-0001 in C-01, got %q", c)
	}
	if c := graph.HypothesisCluster("H-0011"); c != "C-01" {
		t.Errorf("expected H-0011 in C-01, got %q", c)
	}
	if c := graph.HypothesisCluster("H-0010"); c != "C-03" {
		t.Errorf("expected H-0010 in C-03, got %q", c)
	}
	if c := graph.HypothesisCluster("H-9999"); c != "" {
		t.Errorf("expected empty string for unknown hypothesis, got %q", c)
	}
}

// TestLoadProximityGraph_InvalidJSON verifies that invalid JSON in clusters.json returns an error.
func TestLoadProximityGraph_InvalidJSON(t *testing.T) {
	runDir := t.TempDir()
	proxDir := filepath.Join(runDir, "proximity")
	if err := os.MkdirAll(proxDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(proxDir, "clusters.json"), []byte(`{not valid json}`), 0o644); err != nil {
		t.Fatal(err)
	}

	_, err := loadProximityGraph(runDir)
	if err == nil {
		t.Fatal("expected error parsing invalid JSON, got nil")
	}
}

// TestEloPair_Integration_ClusterGuidedPairing verifies that 'elo pair' correctly reads
// proximity/clusters.json (produced by prox) and activates cluster-guided pairing.
//
// Standings:
//
//	H-0001: 1600 (top quartile)
//	H-0002: 1590 (nearest rated overall, diff=10, but in cluster C-02)
//	H-0003: 1580 (in cluster C-01 with H-0001, diff=20)
//	H-0004: 1400 (in cluster C-02)
//
// When clusters.json is present:
//
//	Refinement pairing pairs H-0001 with H-0003 (same cluster C-01).
//
// When clusters.json is absent or when only graph.json is present (the pre-fix bug):
//
//	Refinement pairing pairs H-0001 with H-0002 (nearest rated overall).
func TestEloPair_Integration_ClusterGuidedPairing(t *testing.T) {
	runDir := t.TempDir()

	// 1. Create hypotheses directory.
	hypDir := filepath.Join(runDir, "hypotheses")
	if err := os.MkdirAll(hypDir, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"H-0001", "H-0002", "H-0003", "H-0004"} {
		data, _ := json.Marshal(map[string]string{"id": id, "title": "Hypothesis " + id})
		if err := os.WriteFile(filepath.Join(hypDir, id+".json"), data, 0o644); err != nil {
			t.Fatal(err)
		}
	}

	// 2. Create ratings for epoch 1.
	ratingsDir := filepath.Join(runDir, "ratings")
	if err := os.MkdirAll(ratingsDir, 0o755); err != nil {
		t.Fatal(err)
	}
	ratingsData := rating.RatingsResult{
		Epoch: 1,
		Ratings: map[string]*rating.HypothesisRating{
			"H-0001": {Elo: 1600, Matches: 3, Wins: 3},
			"H-0002": {Elo: 1590, Matches: 3, Wins: 2},
			"H-0003": {Elo: 1580, Matches: 3, Wins: 2},
			"H-0004": {Elo: 1400, Matches: 3, Wins: 0},
		},
		ComputedFrom: "matches/ ledger",
	}
	rBytes, err := json.Marshal(ratingsData)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(ratingsDir, "epoch-1.json"), rBytes, 0o644); err != nil {
		t.Fatal(err)
	}

	// 3. Create proximity directory.
	proxDir := filepath.Join(runDir, "proximity")
	if err := os.MkdirAll(proxDir, 0o755); err != nil {
		t.Fatal(err)
	}

	// Also write graph.json to simulate real prox output containing both files.
	graphContent := `{
  "threshold": 0.3,
  "nodes": {
    "H-0001": {"cluster": "C-01", "edges": [{"target": "H-0003", "similarity": 0.85}]},
    "H-0002": {"cluster": "C-02", "edges": [{"target": "H-0004", "similarity": 0.75}]},
    "H-0003": {"cluster": "C-01", "edges": [{"target": "H-0001", "similarity": 0.85}]},
    "H-0004": {"cluster": "C-02", "edges": [{"target": "H-0002", "similarity": 0.75}]}
  }
}`
	if err := os.WriteFile(filepath.Join(proxDir, "graph.json"), []byte(graphContent), 0o644); err != nil {
		t.Fatal(err)
	}

	// Real prox-shaped clusters.json.
	clustersContent := `{
  "clusters": {
    "C-01": ["H-0001", "H-0003"],
    "C-02": ["H-0002", "H-0004"]
  },
  "algorithm": "louvain",
  "num_clusters": 2
}`
	clustersPath := filepath.Join(proxDir, "clusters.json")
	if err := os.WriteFile(clustersPath, []byte(clustersContent), 0o644); err != nil {
		t.Fatal(err)
	}

	// Run elo pair with clusters.json present.
	runPair := func() ([]pairing.Pair, error) {
		var out bytes.Buffer
		rootCmd.SetOut(&out)
		rootCmd.SetErr(&out)
		rootCmd.SetArgs([]string{"pair", "--run-dir", runDir, "--epoch", "1", "--budget", "1", "--strategy", "default"})
		if err := rootCmd.Execute(); err != nil {
			return nil, err
		}
		var pairs []pairing.Pair
		if err := json.Unmarshal(out.Bytes(), &pairs); err != nil {
			return nil, fmt.Errorf("parsing output %q: %w", out.String(), err)
		}
		return pairs, nil
	}

	pairsWithClusters, err := runPair()
	if err != nil {
		t.Fatalf("elo pair failed: %v", err)
	}
	if len(pairsWithClusters) != 1 {
		t.Fatalf("expected 1 pair, got %d: %v", len(pairsWithClusters), pairsWithClusters)
	}
	// With clusters.json loaded, H-0001 MUST pair with H-0003 (same cluster C-01).
	if pairsWithClusters[0].A != "H-0001" || pairsWithClusters[0].B != "H-0003" {
		t.Errorf("cluster-guided pairing did not activate: got pair %+v, want {A: H-0001, B: H-0003}", pairsWithClusters[0])
	}

	// Now remove clusters.json (leaving only graph.json, simulating the bug).
	if err := os.Remove(clustersPath); err != nil {
		t.Fatal(err)
	}

	pairsWithoutClusters, err := runPair()
	if err != nil {
		t.Fatalf("elo pair failed without clusters.json: %v", err)
	}
	if len(pairsWithoutClusters) != 1 {
		t.Fatalf("expected 1 pair without clusters.json, got %d: %v", len(pairsWithoutClusters), pairsWithoutClusters)
	}
	// Without clusters.json, it falls back to nearest-rated overall: H-0001 vs H-0002.
	if pairsWithoutClusters[0].A != "H-0001" || pairsWithoutClusters[0].B != "H-0002" {
		t.Errorf("expected fallback to nearest-rated without clusters.json: got %+v, want {A: H-0001, B: H-0002}", pairsWithoutClusters[0])
	}
}

// TestLoadReviewScores_ValidAndMissing verifies that loadReviewScores correctly parses
// review files, computes composite axis means per review, averages across multiple
// reviews for a hypothesis, and excludes hypotheses with no reviews.
func TestLoadReviewScores_ValidAndMissing(t *testing.T) {
	runDir := t.TempDir()
	reviewsDir := filepath.Join(runDir, "reviews")
	if err := os.MkdirAll(reviewsDir, 0o755); err != nil {
		t.Fatal(err)
	}

	// H-0001 Review 1: (4 + 3 + 5 + 4) / 4 = 4.0
	r1 := `{
  "id": "H-0001.R-01",
  "hypothesis_id": "H-0001",
  "review_type": "full",
  "scores": {
    "correctness": 4,
    "novelty": 3,
    "testability": 5,
    "safety": 4
  },
  "verdict": "Good",
  "key_criticisms": [],
  "verified_citations": [],
  "contradicting_evidence": [],
  "reviewer": "agent-1",
  "epoch": 0
}`
	// H-0001 Review 2: (5 + 4 + 5 + 4) / 4 = 4.5
	// H-0001 Mean: (4.0 + 4.5) / 2 = 4.25
	r2 := `{
  "id": "H-0001.R-02",
  "hypothesis_id": "H-0001",
  "review_type": "deep",
  "scores": {
    "correctness": 5,
    "novelty": 4,
    "testability": 5,
    "safety": 4
  },
  "verdict": "Very good",
  "key_criticisms": [],
  "verified_citations": [],
  "contradicting_evidence": [],
  "reviewer": "agent-2",
  "epoch": 0
}`
	// H-0002 Review 1: (2 + 2 + 3 + 5) / 4 = 3.0
	// H-0002 Mean: 3.0
	r3 := `{
  "id": "H-0002.R-01",
  "hypothesis_id": "H-0002",
  "review_type": "full",
  "scores": {
    "correctness": 2,
    "novelty": 2,
    "testability": 3,
    "safety": 5
  },
  "verdict": "Average",
  "key_criticisms": [],
  "verified_citations": [],
  "contradicting_evidence": [],
  "reviewer": "agent-1",
  "epoch": 0
}`
	if err := os.WriteFile(filepath.Join(reviewsDir, "H-0001.R-01.json"), []byte(r1), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(reviewsDir, "H-0001.R-02.json"), []byte(r2), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(reviewsDir, "H-0002.R-01.json"), []byte(r3), 0o644); err != nil {
		t.Fatal(err)
	}

	// Non-review files and directories that must be ignored.
	if err := os.WriteFile(filepath.Join(reviewsDir, "README.md"), []byte("# Notes"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(reviewsDir, "subdir"), 0o755); err != nil {
		t.Fatal(err)
	}

	scores, err := loadReviewScores(runDir)
	if err != nil {
		t.Fatalf("loadReviewScores failed: %v", err)
	}

	if len(scores) != 2 {
		t.Fatalf("expected 2 hypotheses in review scores, got %d: %v", len(scores), scores)
	}

	const eps = 1e-6
	if diff := scores["H-0001"] - 4.25; diff < -eps || diff > eps {
		t.Errorf("scores[H-0001] = %v, want 4.25", scores["H-0001"])
	}
	if diff := scores["H-0002"] - 3.0; diff < -eps || diff > eps {
		t.Errorf("scores[H-0002] = %v, want 3.0", scores["H-0002"])
	}
	if _, ok := scores["H-0003"]; ok {
		t.Errorf("expected H-0003 (no review files) to be absent from review scores")
	}
}

// TestLoadReviewScores_NotExist verifies that loadReviewScores returns an empty map
// without error when the reviews directory does not exist.
func TestLoadReviewScores_NotExist(t *testing.T) {
	runDir := t.TempDir()
	scores, err := loadReviewScores(runDir)
	if err != nil {
		t.Fatalf("expected nil error when reviews dir missing, got: %v", err)
	}
	if scores == nil {
		t.Fatal("expected non-nil (empty) scores map")
	}
	if len(scores) != 0 {
		t.Fatalf("expected 0 scores, got %d", len(scores))
	}
}

// TestLoadReviewScores_InvalidJSON verifies that malformed JSON in reviews/ produces an error.
func TestLoadReviewScores_InvalidJSON(t *testing.T) {
	runDir := t.TempDir()
	reviewsDir := filepath.Join(runDir, "reviews")
	if err := os.MkdirAll(reviewsDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(reviewsDir, "H-0001.R-01.json"), []byte(`{not valid`), 0o644); err != nil {
		t.Fatal(err)
	}

	_, err := loadReviewScores(runDir)
	if err == nil {
		t.Fatal("expected error parsing invalid JSON, got nil")
	}
}

// TestEloPair_Integration_SwissReviewScoreSeeding verifies that 'elo pair --strategy swiss'
// loads review scores from reviews/ and uses them to seed Round 1 pairings.
//
// Elo ranking:
//
//	H-0001: 1800 (Elo rank 1)
//	H-0002: 1600 (Elo rank 2)
//	H-0003: 1400 (Elo rank 3)
//	H-0004: 1200 (Elo rank 4)
//
// Review scores:
//
//	H-0004: 5.0 (Review rank 1)
//	H-0003: 4.0 (Review rank 2)
//	H-0002: 3.0 (Review rank 3)
//	H-0001: 2.0 (Review rank 4)
//
// With review scores loaded:
//
//	Seeding order: H-0004, H-0003, H-0002, H-0001
//	Round 1 pairs: (H-0004 vs H-0003), (H-0002 vs H-0001)
//
// Without review scores (pure Elo fallback):
//
//	Seeding order: H-0001, H-0002, H-0003, H-0004
//	Round 1 pairs: (H-0001 vs H-0002), (H-0003 vs H-0004)
func TestEloPair_Integration_SwissReviewScoreSeeding(t *testing.T) {
	runDir := t.TempDir()

	// 1. Create hypotheses directory.
	hypDir := filepath.Join(runDir, "hypotheses")
	if err := os.MkdirAll(hypDir, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"H-0001", "H-0002", "H-0003", "H-0004"} {
		data, _ := json.Marshal(map[string]string{"id": id, "title": "Hypothesis " + id})
		if err := os.WriteFile(filepath.Join(hypDir, id+".json"), data, 0o644); err != nil {
			t.Fatal(err)
		}
	}

	// 2. Create ratings for epoch 1.
	ratingsDir := filepath.Join(runDir, "ratings")
	if err := os.MkdirAll(ratingsDir, 0o755); err != nil {
		t.Fatal(err)
	}
	ratingsData := rating.RatingsResult{
		Epoch: 1,
		Ratings: map[string]*rating.HypothesisRating{
			"H-0001": {Elo: 1800, Matches: 3, Wins: 3},
			"H-0002": {Elo: 1600, Matches: 3, Wins: 2},
			"H-0003": {Elo: 1400, Matches: 3, Wins: 1},
			"H-0004": {Elo: 1200, Matches: 3, Wins: 0},
		},
		ComputedFrom: "matches/ ledger",
	}
	rBytes, err := json.Marshal(ratingsData)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(ratingsDir, "epoch-1.json"), rBytes, 0o644); err != nil {
		t.Fatal(err)
	}

	// 3. Create reviews directory with inverted rankings.
	reviewsDir := filepath.Join(runDir, "reviews")
	if err := os.MkdirAll(reviewsDir, 0o755); err != nil {
		t.Fatal(err)
	}

	makeReviewJSON := func(hypID string, score int) string {
		return fmt.Sprintf(`{
  "id": "%s.R-01",
  "hypothesis_id": "%s",
  "review_type": "full",
  "scores": {
    "correctness": %d,
    "novelty": %d,
    "testability": %d,
    "safety": %d
  },
  "verdict": "Test review",
  "key_criticisms": [],
  "verified_citations": [],
  "contradicting_evidence": [],
  "reviewer": "judge",
  "epoch": 0
}`, hypID, hypID, score, score, score, score)
	}

	// H-0004 gets score 5 -> composite 5.0
	// H-0003 gets score 4 -> composite 4.0
	// H-0002 gets score 3 -> composite 3.0
	// H-0001 gets score 2 -> composite 2.0
	if err := os.WriteFile(filepath.Join(reviewsDir, "H-0004.R-01.json"), []byte(makeReviewJSON("H-0004", 5)), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(reviewsDir, "H-0003.R-01.json"), []byte(makeReviewJSON("H-0003", 4)), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(reviewsDir, "H-0002.R-01.json"), []byte(makeReviewJSON("H-0002", 3)), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(reviewsDir, "H-0001.R-01.json"), []byte(makeReviewJSON("H-0001", 2)), 0o644); err != nil {
		t.Fatal(err)
	}

	runSwissPair := func() ([]pairing.Pair, error) {
		var out bytes.Buffer
		rootCmd.SetOut(&out)
		rootCmd.SetErr(&out)
		rootCmd.SetArgs([]string{"pair", "--run-dir", runDir, "--epoch", "1", "--budget", "2", "--strategy", "swiss"})
		if err := rootCmd.Execute(); err != nil {
			return nil, err
		}
		var pairs []pairing.Pair
		if err := json.Unmarshal(out.Bytes(), &pairs); err != nil {
			return nil, fmt.Errorf("parsing output %q: %w", out.String(), err)
		}
		return pairs, nil
	}

	// Run with review scores present.
	pairsWithReviews, err := runSwissPair()
	if err != nil {
		t.Fatalf("elo pair --strategy swiss failed: %v", err)
	}
	if len(pairsWithReviews) != 2 {
		t.Fatalf("expected 2 pairs, got %d: %v", len(pairsWithReviews), pairsWithReviews)
	}

	// Under review score seeding, Round 1 must pair:
	//   H-0004 vs H-0003 (rank 1 vs rank 2 by review score)
	//   H-0002 vs H-0001 (rank 3 vs rank 4 by review score)
	if pairsWithReviews[0].A != "H-0004" || pairsWithReviews[0].B != "H-0003" {
		t.Errorf("round 1 pair 0 under review seeding: got %+v, want {A: H-0004, B: H-0003}", pairsWithReviews[0])
	}
	if pairsWithReviews[1].A != "H-0002" || pairsWithReviews[1].B != "H-0001" {
		t.Errorf("round 1 pair 1 under review seeding: got %+v, want {A: H-0002, B: H-0001}", pairsWithReviews[1])
	}

	// Now remove the reviews directory to test fallback to pure Elo seeding.
	if err := os.RemoveAll(reviewsDir); err != nil {
		t.Fatal(err)
	}

	pairsWithoutReviews, err := runSwissPair()
	if err != nil {
		t.Fatalf("elo pair --strategy swiss failed without reviews: %v", err)
	}
	if len(pairsWithoutReviews) != 2 {
		t.Fatalf("expected 2 pairs without reviews, got %d: %v", len(pairsWithoutReviews), pairsWithoutReviews)
	}

	// Under pure Elo seeding, Round 1 must pair:
	//   H-0001 vs H-0002 (rank 1 vs rank 2 by Elo)
	//   H-0003 vs H-0004 (rank 3 vs rank 4 by Elo)
	if pairsWithoutReviews[0].A != "H-0001" || pairsWithoutReviews[0].B != "H-0002" {
		t.Errorf("round 1 pair 0 under Elo seeding: got %+v, want {A: H-0001, B: H-0002}", pairsWithoutReviews[0])
	}
	if pairsWithoutReviews[1].A != "H-0003" || pairsWithoutReviews[1].B != "H-0004" {
		t.Errorf("round 1 pair 1 under Elo seeding: got %+v, want {A: H-0003, B: H-0004}", pairsWithoutReviews[1])
	}
}

// TestEloPair_Integration_RematchFlag verifies that --rematch loads forced pairings
// from a JSON file, prepends them to output, and deduplicates against strategy pairs.
func TestEloPair_Integration_RematchFlag(t *testing.T) {
	runDir := t.TempDir()

	// 1. Create hypotheses directory.
	hypDir := filepath.Join(runDir, "hypotheses")
	if err := os.MkdirAll(hypDir, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"H-0001", "H-0002", "H-0003", "H-0004", "H-0055"} {
		data, _ := json.Marshal(map[string]string{"id": id, "title": "Hypothesis " + id})
		if err := os.WriteFile(filepath.Join(hypDir, id+".json"), data, 0o644); err != nil {
			t.Fatal(err)
		}
	}

	// 2. Create ratings for epoch 1.
	ratingsDir := filepath.Join(runDir, "ratings")
	if err := os.MkdirAll(ratingsDir, 0o755); err != nil {
		t.Fatal(err)
	}
	ratingsData := rating.RatingsResult{
		Epoch: 1,
		Ratings: map[string]*rating.HypothesisRating{
			"H-0001": {Elo: 1600, Matches: 3, Wins: 3},
			"H-0002": {Elo: 1550, Matches: 3, Wins: 2},
			"H-0003": {Elo: 1500, Matches: 3, Wins: 2},
			"H-0004": {Elo: 1400, Matches: 3, Wins: 0},
			"H-0055": {Elo: 1450, Matches: 1, Wins: 1},
		},
		ComputedFrom: "matches/ ledger",
	}
	rBytes, err := json.Marshal(ratingsData)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(ratingsDir, "epoch-1.json"), rBytes, 0o644); err != nil {
		t.Fatal(err)
	}

	// 3. Write forced pairings JSON file.
	forcedPairings := []pairing.Pair{
		{A: "H-0003", B: "H-0055"},
	}
	fpBytes, err := json.Marshal(forcedPairings)
	if err != nil {
		t.Fatal(err)
	}
	rematchFile := filepath.Join(runDir, "rematch-pairs.json")
	if err := os.WriteFile(rematchFile, fpBytes, 0o644); err != nil {
		t.Fatal(err)
	}

	// 4. Run elo pair with --rematch flag.
	var out bytes.Buffer
	rootCmd.SetOut(&out)
	rootCmd.SetErr(&out)
	rootCmd.SetArgs([]string{
		"pair", "--run-dir", runDir, "--epoch", "1", "--budget", "5",
		"--rematch", rematchFile,
	})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("elo pair --rematch failed: %v", err)
	}

	var pairs []pairing.Pair
	if err := json.Unmarshal(out.Bytes(), &pairs); err != nil {
		t.Fatalf("parsing output %q: %v", out.String(), err)
	}

	// First pair must be the forced rematch.
	if len(pairs) == 0 {
		t.Fatal("expected at least 1 pair")
	}
	if pairs[0].A != "H-0003" || pairs[0].B != "H-0055" {
		t.Errorf("first pair should be forced rematch, got %+v", pairs[0])
	}

	// Forced pair should not appear again in strategy-generated pairs.
	for i := 1; i < len(pairs); i++ {
		if (pairs[i].A == "H-0003" && pairs[i].B == "H-0055") ||
			(pairs[i].A == "H-0055" && pairs[i].B == "H-0003") {
			t.Errorf("forced pair H-0003 vs H-0055 duplicated at index %d", i)
		}
	}

	// Budget should be respected.
	if len(pairs) > 5 {
		t.Errorf("exceeded budget: got %d pairs, want ≤ 5", len(pairs))
	}
}

// TestEloPair_Integration_RematchConsumesBudget verifies that forced pairings
// consume budget, leaving less for strategy-generated pairings.
func TestEloPair_Integration_RematchConsumesBudget(t *testing.T) {
	runDir := t.TempDir()

	hypDir := filepath.Join(runDir, "hypotheses")
	if err := os.MkdirAll(hypDir, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"H-0001", "H-0002", "H-0003", "H-0055", "H-0056"} {
		data, _ := json.Marshal(map[string]string{"id": id})
		if err := os.WriteFile(filepath.Join(hypDir, id+".json"), data, 0o644); err != nil {
			t.Fatal(err)
		}
	}

	ratingsDir := filepath.Join(runDir, "ratings")
	if err := os.MkdirAll(ratingsDir, 0o755); err != nil {
		t.Fatal(err)
	}
	ratingsData := rating.RatingsResult{
		Epoch: 1,
		Ratings: map[string]*rating.HypothesisRating{
			"H-0001": {Elo: 1600, Matches: 3, Wins: 3},
			"H-0002": {Elo: 1500, Matches: 3, Wins: 2},
			"H-0003": {Elo: 1400, Matches: 3, Wins: 1},
			"H-0055": {Elo: 1450, Matches: 1, Wins: 1},
			"H-0056": {Elo: 1350, Matches: 1, Wins: 0},
		},
		ComputedFrom: "matches/ ledger",
	}
	rBytes, _ := json.Marshal(ratingsData)
	os.WriteFile(filepath.Join(ratingsDir, "epoch-1.json"), rBytes, 0o644)

	// 2 forced pairings with budget=2 → no room for strategy pairs.
	forcedPairings := []pairing.Pair{
		{A: "H-0003", B: "H-0055"},
		{A: "H-0001", B: "H-0056"},
	}
	fpBytes, _ := json.Marshal(forcedPairings)
	rematchFile := filepath.Join(runDir, "rematch-pairs.json")
	os.WriteFile(rematchFile, fpBytes, 0o644)

	var out bytes.Buffer
	rootCmd.SetOut(&out)
	rootCmd.SetErr(&out)
	rootCmd.SetArgs([]string{
		"pair", "--run-dir", runDir, "--epoch", "1", "--budget", "2",
		"--rematch", rematchFile,
	})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("elo pair --rematch failed: %v", err)
	}

	var pairs []pairing.Pair
	if err := json.Unmarshal(out.Bytes(), &pairs); err != nil {
		t.Fatalf("parsing output %q: %v", out.String(), err)
	}

	// Should have exactly 2 pairs (the forced ones).
	if len(pairs) != 2 {
		t.Errorf("expected exactly 2 pairs (budget exhausted by forced), got %d", len(pairs))
	}
	if len(pairs) >= 2 {
		if pairs[0].A != "H-0003" || pairs[0].B != "H-0055" {
			t.Errorf("pair 0: got %+v, want {A: H-0003, B: H-0055}", pairs[0])
		}
		if pairs[1].A != "H-0001" || pairs[1].B != "H-0056" {
			t.Errorf("pair 1: got %+v, want {A: H-0001, B: H-0056}", pairs[1])
		}
	}
}

// TestEloPair_Integration_RematchWindowFlag verifies that the --rematch-window
// flag controls the lookback window for rematch avoidance.
func TestEloPair_Integration_RematchWindowFlag(t *testing.T) {
	runDir := t.TempDir()

	hypDir := filepath.Join(runDir, "hypotheses")
	if err := os.MkdirAll(hypDir, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"H-0001", "H-0002"} {
		data, _ := json.Marshal(map[string]string{"id": id})
		if err := os.WriteFile(filepath.Join(hypDir, id+".json"), data, 0o644); err != nil {
			t.Fatal(err)
		}
	}

	ratingsDir := filepath.Join(runDir, "ratings")
	if err := os.MkdirAll(ratingsDir, 0o755); err != nil {
		t.Fatal(err)
	}
	matchesDir := filepath.Join(runDir, "matches")
	if err := os.MkdirAll(matchesDir, 0o755); err != nil {
		t.Fatal(err)
	}

	// Ratings for epoch 1. Only 2 hypotheses so the only possible pair is H-0001 vs H-0002.
	ratingsData := rating.RatingsResult{
		Epoch: 1,
		Ratings: map[string]*rating.HypothesisRating{
			"H-0001": {Elo: 1600, Matches: 1, Wins: 1},
			"H-0002": {Elo: 1400, Matches: 1, Wins: 0},
		},
		ComputedFrom: "matches/ ledger",
	}
	rBytes, _ := json.Marshal(ratingsData)
	os.WriteFile(filepath.Join(ratingsDir, "epoch-1.json"), rBytes, 0o644)

	// Match history: H-0001 vs H-0002 played in epoch 0.
	matchData := rating.Match{
		ID: "M-0001", A: "H-0001", B: "H-0002", Epoch: 0, Winner: "H-0001",
	}
	mBytes, _ := json.Marshal(matchData)
	os.WriteFile(filepath.Join(matchesDir, "M-0001.json"), mBytes, 0o644)

	runPairWithWindow := func(window int) ([]pairing.Pair, error) {
		var out bytes.Buffer
		rootCmd.SetOut(&out)
		rootCmd.SetErr(&out)
		rootCmd.SetArgs([]string{
			"pair", "--run-dir", runDir, "--epoch", "1", "--budget", "5",
			"--rematch-window", fmt.Sprintf("%d", window),
			"--rematch", "",
		})
		if err := rootCmd.Execute(); err != nil {
			return nil, err
		}
		var pairs []pairing.Pair
		if err := json.Unmarshal(out.Bytes(), &pairs); err != nil {
			return nil, fmt.Errorf("parsing output %q: %w", out.String(), err)
		}
		return pairs, nil
	}

	// window=2 (default): epoch 0 match is in [0,1] → blocked.
	pairs2, err := runPairWithWindow(2)
	if err != nil {
		t.Fatalf("window=2 failed: %v", err)
	}
	if len(pairs2) != 0 {
		t.Errorf("window=2 should block the only possible pair, got %d pairs: %v", len(pairs2), pairs2)
	}

	// window=1: epoch 0 match not in [1,1] → allowed.
	pairs1, err := runPairWithWindow(1)
	if err != nil {
		t.Fatalf("window=1 failed: %v", err)
	}
	if len(pairs1) != 1 {
		t.Errorf("window=1 should allow the pair, got %d pairs: %v", len(pairs1), pairs1)
	}

	// window=0: no constraint → allowed.
	pairs0, err := runPairWithWindow(0)
	if err != nil {
		t.Fatalf("window=0 failed: %v", err)
	}
	if len(pairs0) != 1 {
		t.Errorf("window=0 should allow the pair, got %d pairs: %v", len(pairs0), pairs0)
	}
}
