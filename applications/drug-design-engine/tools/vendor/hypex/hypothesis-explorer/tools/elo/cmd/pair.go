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
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/elo/internal/pairing"
	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/elo/internal/rating"
	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/elo/internal/reviews"
	"github.com/spf13/cobra"
)

func init() {
	var (
		flagEpoch         int
		flagBudget        int
		flagStrategy      string
		flagRematchWindow int
		flagRematch       string
	)

	cmd := &cobra.Command{
		Use:   "pair",
		Short: "Generate match pairings for the next tournament round",
		Long: `Reads current ratings, proximity graph, and match history to emit
optimal pairings for the next set of tournament matches.

Pairing modes (applied in order, filling the budget):
  1. Placement: unrated hypotheses vs. high/median/low anchors
  2. Refinement: top quartile, nearest-rated in same cluster
  3. Exploration: 15% of remaining budget across clusters

Strategies:
  default  - Proximity-guided Elo pairing (Strategy A)
  swiss    - Swiss-system rounds, seeded by mean review score`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if flagRunDir == "" {
				return fmt.Errorf("--run-dir is required")
			}
			if flagBudget <= 0 {
				return fmt.Errorf("--budget must be a positive integer")
			}

			// Load ratings for the specified epoch.
			ratings, err := loadRatings(flagRunDir, flagEpoch)
			if err != nil {
				return fmt.Errorf("loading ratings: %w", err)
			}

			// Load match history.
			matches, err := loadMatches(flagRunDir)
			if err != nil {
				return fmt.Errorf("loading matches: %w", err)
			}
			history := pairing.NewMatchHistory(matches)

			// Collect all known hypothesis IDs (from ratings + matches + hypotheses/ dir).
			allHypotheses, err := collectHypothesisIDs(flagRunDir, ratings, matches)
			if err != nil {
				return fmt.Errorf("collecting hypothesis IDs: %w", err)
			}

			pairs := make([]pairing.Pair, 0)
			remaining := flagBudget

			// Load forced rematch pairings if --rematch is provided.
			if flagRematch != "" {
				forced, err := loadForcedPairings(flagRematch)
				if err != nil {
					return fmt.Errorf("loading forced pairings: %w", err)
				}
				// Prepend forced pairings (they consume budget first).
				for _, p := range forced {
					if remaining <= 0 {
						break
					}
					pairs = append(pairs, p)
					remaining--
				}
			}

			if remaining > 0 {
				// Build the set of hypotheses already paired by forced rematches
				// so the strategy avoids re-pairing them.
				alreadyForced := make(map[string]bool)
				for _, p := range pairs {
					alreadyForced[pairKey(p.A, p.B)] = true
				}

				var strategyPairs []pairing.Pair

				switch flagStrategy {
				case "default", "":
					// Load proximity graph (optional).
					graph, err := loadProximityGraph(flagRunDir)
					if err != nil {
						return fmt.Errorf("loading proximity graph: %w", err)
					}

					strategyPairs = pairing.StrategyA(ratings, allHypotheses, graph, history, flagEpoch, remaining, flagRematchWindow)

				case "swiss":
					reviewScores, err := loadReviewScores(flagRunDir)
					if err != nil {
						return fmt.Errorf("loading review scores: %w", err)
					}

					strategyPairs = pairing.StrategyB(ratings, history, flagEpoch, remaining, reviewScores, flagRematchWindow)

				default:
					return fmt.Errorf("unknown strategy: %q (valid: default, swiss)", flagStrategy)
				}

				// Append strategy pairs, skipping any that duplicate forced pairings.
				for _, p := range strategyPairs {
					key := pairKey(p.A, p.B)
					if alreadyForced[key] {
						continue
					}
					pairs = append(pairs, p)
				}
			}

			// Ensure non-nil for JSON output.
			if pairs == nil {
				pairs = make([]pairing.Pair, 0)
			}

			// Output as JSON array to stdout.
			data, err := json.MarshalIndent(pairs, "", "  ")
			if err != nil {
				return fmt.Errorf("marshaling pairings: %w", err)
			}
			fmt.Fprintln(cmd.OutOrStdout(), string(data))
			return nil
		},
	}

	cmd.Flags().IntVar(&flagEpoch, "epoch", 0, "Epoch number to read ratings from")
	cmd.Flags().IntVar(&flagBudget, "budget", 10, "Maximum number of pairings to emit")
	cmd.Flags().StringVar(&flagStrategy, "strategy", "default",
		"Pairing strategy: default (proximity-guided) or swiss")
	cmd.Flags().IntVar(&flagRematchWindow, "rematch-window", 2,
		"Number of epochs to look back for rematch avoidance (0 = no constraint, 1 = same epoch only)")
	cmd.Flags().StringVar(&flagRematch, "rematch", "",
		"Path to JSON file with forced pairings (array of {a, b} objects)")
	rootCmd.AddCommand(cmd)
}

// loadRatings reads the ratings file for the given epoch.
// Returns an empty map if no ratings file exists (e.g., epoch 0 before first recompute).
func loadRatings(runDir string, epoch int) (map[string]*rating.HypothesisRating, error) {
	path := filepath.Join(runDir, "ratings", fmt.Sprintf("epoch-%d.json", epoch))

	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return make(map[string]*rating.HypothesisRating), nil
		}
		return nil, fmt.Errorf("reading ratings file: %w", err)
	}

	var result rating.RatingsResult
	if err := json.Unmarshal(data, &result); err != nil {
		return nil, fmt.Errorf("parsing ratings file: %w", err)
	}

	return result.Ratings, nil
}

// loadProximityGraph reads the proximity clusters if they exist.
// Returns nil (not an error) if the file doesn't exist.
func loadProximityGraph(runDir string) (*pairing.ProximityGraph, error) {
	path := filepath.Join(runDir, "proximity", "clusters.json")

	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("reading proximity clusters: %w", err)
	}

	var graph pairing.ProximityGraph
	if err := json.Unmarshal(data, &graph); err != nil {
		return nil, fmt.Errorf("parsing proximity clusters: %w", err)
	}

	return &graph, nil
}

// loadReviewScores reads all review files in <runDir>/reviews/ and computes the
// mean composite review score per hypothesis using internal/reviews.
//
// Hypotheses with no review files are absent from the returned map.
// Returns an empty map (not nil, no error) if the reviews directory does not exist.
func loadReviewScores(runDir string) (pairing.ReviewScores, error) {
	rs, err := reviews.LoadAll(runDir)
	if err != nil {
		return nil, err
	}
	return reviews.MeanScores(rs), nil
}

// collectHypothesisIDs gathers all unique hypothesis IDs from three sources:
//  1. The ratings map (hypotheses that have been rated).
//  2. Match records (hypotheses that have competed).
//  3. The hypotheses/ directory in runDir (hypothesis JSON files on disk).
//
// Source 3 is essential for epoch 0, when no ratings or matches exist yet —
// without it, the function returns zero IDs and pairing produces no output.
func collectHypothesisIDs(runDir string, ratings map[string]*rating.HypothesisRating, matches []rating.Match) ([]string, error) {
	seen := make(map[string]bool)
	for id := range ratings {
		seen[id] = true
	}
	for _, m := range matches {
		seen[m.A] = true
		seen[m.B] = true
	}

	// Scan <runDir>/hypotheses/*.json for hypothesis IDs on disk.
	// This ensures epoch-0 bootstrap works even when no ratings/matches exist.
	hypDir := filepath.Join(runDir, "hypotheses")
	entries, err := os.ReadDir(hypDir)
	if err != nil && !os.IsNotExist(err) {
		return nil, fmt.Errorf("reading hypotheses directory: %w", err)
	}
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		name := entry.Name()
		if !strings.HasPrefix(name, "H-") || !strings.HasSuffix(name, ".json") {
			continue
		}
		id := strings.TrimSuffix(name, ".json")
		seen[id] = true
	}

	ids := make([]string, 0, len(seen))
	for id := range seen {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids, nil
}

// loadForcedPairings reads a JSON file containing an array of forced pairings.
// Each element must have "a" and "b" string fields (hypothesis IDs).
func loadForcedPairings(path string) ([]pairing.Pair, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("reading forced pairings file: %w", err)
	}
	var pairs []pairing.Pair
	if err := json.Unmarshal(data, &pairs); err != nil {
		return nil, fmt.Errorf("parsing forced pairings file: %w", err)
	}
	return pairs, nil
}

// pairKey returns a canonical key for two hypothesis IDs (sorted order).
func pairKey(a, b string) string {
	if a > b {
		a, b = b, a
	}
	return a + ":" + b
}
