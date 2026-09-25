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
	"strconv"
	"strings"

	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/elo/internal/composite"
	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/elo/internal/config"
	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/elo/internal/rating"
	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/elo/internal/reviews"
	"github.com/spf13/cobra"
)

func init() {
	var (
		flagFormat    string
		flagComposite bool
		flagPreset    string
		flagWeights   string
		flagExplain   bool
	)

	cmd := &cobra.Command{
		Use:   "standings",
		Short: "Print current Elo standings",
		Long: `Reads the latest ratings file from the run directory and outputs
sorted standings (highest Elo first).

Formats:
  table  - Human-readable table (default)
  json   - JSON array of standings`,
		RunE: func(cmd *cobra.Command, args []string) (err error) {
			defer func() {
				flagFormat = "table"
				flagComposite = false
				flagPreset = ""
				flagWeights = ""
				flagExplain = false
			}()

			if flagRunDir == "" {
				return fmt.Errorf("--run-dir is required")
			}
			if flagFormat != "table" && flagFormat != "json" {
				return fmt.Errorf("--format must be 'table' or 'json'")
			}

			// Flag validations
			if !flagComposite {
				if flagPreset != "" {
					return fmt.Errorf("--preset requires --composite")
				}
				if flagWeights != "" {
					return fmt.Errorf("--weights requires --composite")
				}
				if flagExplain {
					return fmt.Errorf("--explain requires --composite")
				}
			} else {
				if flagPreset != "" {
					if _, err := composite.PresetWeights(flagPreset); err != nil {
						return err
					}
				}
				if flagWeights != "" {
					pairs := strings.Split(flagWeights, ",")
					for _, pair := range pairs {
						pair = strings.TrimSpace(pair)
						if pair == "" {
							continue
						}
						parts := strings.Split(pair, "=")
						if len(parts) != 2 {
							return fmt.Errorf("invalid weight format %q (expected axis=value)", pair)
						}
						axis := strings.ToLower(strings.TrimSpace(parts[0]))
						valStr := strings.TrimSpace(parts[1])
						val, err := strconv.ParseFloat(valStr, 64)
						if err != nil {
							return fmt.Errorf("invalid weight value for %s %q: %w", axis, valStr, err)
						}
						if val < 0 {
							return fmt.Errorf("weight for %s cannot be negative: %v", axis, val)
						}
						switch axis {
						case "goal", "constraint", "novelty":
						default:
							return fmt.Errorf("unknown weight axis %q (valid: goal, constraint, novelty)", axis)
						}
					}
				}
			}

			// Find the latest ratings file.
			ratings, epoch, err := loadLatestRatings(flagRunDir)
			if err != nil {
				return fmt.Errorf("loading ratings: %w", err)
			}
			if ratings == nil {
				fmt.Fprintln(os.Stderr, "No ratings found.")
				return nil
			}

			// Standard backward-compatible path: no reviews read, no run.yaml read.
			if !flagComposite {
				standings := rating.SortedStandings(ratings)
				switch flagFormat {
				case "table":
					printTable(standings, epoch)
				case "json":
					return printJSON(standings)
				}
				return nil
			}

			// Composite ranking path
			cfg, err := config.LoadRunConfig(flagRunDir)
			if err != nil {
				return err
			}

			// Weight resolution order (§3.5):
			// 1. Built-in balanced (lowest)
			weights, err := composite.PresetWeights("balanced")
			if err != nil {
				return err
			}
			effectivePreset := "balanced"

			// 2. run.yaml: tournament.composite.preset
			if cfg.Tournament.Composite != nil && cfg.Tournament.Composite.Preset != "" {
				w, err := composite.PresetWeights(cfg.Tournament.Composite.Preset)
				if err != nil {
					return err
				}
				weights = w
				effectivePreset = cfg.Tournament.Composite.Preset
			}

			// 3. run.yaml: tournament.composite.weights.<axis>
			if cfg.Tournament.Composite != nil && cfg.Tournament.Composite.Weights != nil {
				if cfg.Tournament.Composite.Weights.Goal != nil {
					weights.Goal = *cfg.Tournament.Composite.Weights.Goal
				}
				if cfg.Tournament.Composite.Weights.Constraint != nil {
					weights.Constraint = *cfg.Tournament.Composite.Weights.Constraint
				}
				if cfg.Tournament.Composite.Weights.Novelty != nil {
					weights.Novelty = *cfg.Tournament.Composite.Weights.Novelty
				}
			}

			// 4. --preset flag
			if flagPreset != "" {
				w, err := composite.PresetWeights(flagPreset)
				if err != nil {
					return err
				}
				weights = w
				effectivePreset = flagPreset
			}

			// 5. --weights flag (highest, per-axis)
			if flagWeights != "" {
				pairs := strings.Split(flagWeights, ",")
				for _, pair := range pairs {
					pair = strings.TrimSpace(pair)
					if pair == "" {
						continue
					}
					parts := strings.Split(pair, "=")
					axis := strings.ToLower(strings.TrimSpace(parts[0]))
					val, _ := strconv.ParseFloat(strings.TrimSpace(parts[1]), 64)
					switch axis {
					case "goal":
						weights.Goal = val
					case "constraint":
						weights.Constraint = val
					case "novelty":
						weights.Novelty = val
					}
				}
			}

			// Load reviews and resolve latest scores per axis
			reviewsList, err := reviews.LoadAll(flagRunDir)
			if err != nil {
				return err
			}
			axesMap := reviews.LatestAxes(reviewsList)

			// Compute composite rating for all hypotheses
			entries := make([]compositeRow, 0, len(ratings))
			for id, r := range ratings {
				axes := axesMap[id]
				res := composite.Apply(r.Elo, axes, weights)
				entries = append(entries, compositeRow{
					Standing: rating.Standing{
						HypothesisID: id,
						Elo:          r.Elo,
						Matches:      r.Matches,
						Wins:         r.Wins,
						Draws:        r.Draws,
					},
					Result: res,
				})
			}

			// Sort by Composite descending, tiebreak by hypothesis ID ascending
			sort.Slice(entries, func(i, j int) bool {
				if entries[i].Result.Composite != entries[j].Result.Composite {
					return entries[i].Result.Composite > entries[j].Result.Composite
				}
				return entries[i].Standing.HypothesisID < entries[j].Standing.HypothesisID
			})

			switch flagFormat {
			case "table":
				printCompositeTable(entries, epoch, effectivePreset, weights, flagExplain)
			case "json":
				return printCompositeJSON(entries, epoch, effectivePreset, weights, flagExplain)
			}

			return nil
		},
	}

	cmd.Flags().StringVar(&flagFormat, "format", "table",
		"Output format: table (default) or json")
	cmd.Flags().BoolVar(&flagComposite, "composite", false,
		"Compute composite Elo rankings anchored by review scores")
	cmd.Flags().StringVar(&flagPreset, "preset", "",
		"Named weight preset (balanced, disable_novelty, focus_on_breakthroughs, strict_constraints, pure_tournament)")
	cmd.Flags().StringVar(&flagWeights, "weights", "",
		"Comma-separated axis=value weight overrides (valid axes: goal, constraint, novelty)")
	cmd.Flags().BoolVar(&flagExplain, "explain", false,
		"Include detailed score component breakdown")
	rootCmd.AddCommand(cmd)
}

// loadLatestRatings finds and loads the highest-epoch ratings file.
func loadLatestRatings(runDir string) (map[string]*rating.HypothesisRating, int, error) {
	ratingsDir := filepath.Join(runDir, "ratings")

	entries, err := os.ReadDir(ratingsDir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, 0, nil
		}
		return nil, 0, fmt.Errorf("reading ratings directory: %w", err)
	}

	// Find all epoch-N.json files and pick the highest N.
	var epochs []int
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		name := entry.Name()
		if !strings.HasPrefix(name, "epoch-") || !strings.HasSuffix(name, ".json") {
			continue
		}
		var n int
		if _, err := fmt.Sscanf(name, "epoch-%d.json", &n); err == nil {
			epochs = append(epochs, n)
		}
	}

	if len(epochs) == 0 {
		return nil, 0, nil
	}

	sort.Ints(epochs)
	latestEpoch := epochs[len(epochs)-1]

	path := filepath.Join(ratingsDir, fmt.Sprintf("epoch-%d.json", latestEpoch))
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, 0, fmt.Errorf("reading ratings file: %w", err)
	}

	var result rating.RatingsResult
	if err := json.Unmarshal(data, &result); err != nil {
		return nil, 0, fmt.Errorf("parsing ratings file: %w", err)
	}

	return result.Ratings, latestEpoch, nil
}

// printTable outputs standings as a human-readable table.
func printTable(standings []rating.Standing, epoch int) {
	fmt.Printf("Standings (epoch %d)\n", epoch)
	fmt.Printf("%-6s  %-10s  %8s  %7s  %4s  %5s\n", "Rank", "Hypothesis", "Elo", "Matches", "Wins", "Draws")
	fmt.Println(strings.Repeat("-", 50))
	for i, s := range standings {
		fmt.Printf("%-6d  %-10s  %8.1f  %7d  %4d  %5d\n",
			i+1, s.HypothesisID, s.Elo, s.Matches, s.Wins, s.Draws)
	}
}

// weightsJSON is the JSON representation of the effective composite weights.
type weightsJSON struct {
	Goal       float64 `json:"goal"`
	Constraint float64 `json:"constraint"`
	Novelty    float64 `json:"novelty"`
}

// componentJSON is the JSON representation of a single composite component.
type componentJSON struct {
	Score        *int    `json:"score"`
	S            float64 `json:"s"`
	Weight       float64 `json:"weight"`
	Contribution float64 `json:"contribution"`
	Source       *string `json:"source"`
}

// componentsJSON groups the three composite components for JSON output.
type componentsJSON struct {
	Goal       componentJSON `json:"goal"`
	Constraint componentJSON `json:"constraint"`
	Novelty    componentJSON `json:"novelty"`
}

// standingJSON is the JSON representation of a standing.
type standingJSON struct {
	Rank             int             `json:"rank"`
	HypothesisID     string          `json:"hypothesis_id"`
	Elo              float64         `json:"elo"`
	EloComposite     *float64        `json:"elo_composite,omitempty"`
	CompositeDelta   *float64        `json:"composite_delta,omitempty"`
	Matches          int             `json:"matches"`
	Wins             int             `json:"wins"`
	Draws            int             `json:"draws"`
	CompositeWeights *weightsJSON    `json:"composite_weights,omitempty"`
	Components       *componentsJSON `json:"components,omitempty"`
}

// printJSON outputs non-composite standings as a JSON array.
func printJSON(standings []rating.Standing) error {
	out := make([]standingJSON, len(standings))
	for i, s := range standings {
		out[i] = standingJSON{
			Rank:         i + 1,
			HypothesisID: s.HypothesisID,
			Elo:          s.Elo,
			Matches:      s.Matches,
			Wins:         s.Wins,
			Draws:        s.Draws,
		}
	}
	data, err := json.MarshalIndent(out, "", "  ")
	if err != nil {
		return fmt.Errorf("marshaling standings: %w", err)
	}
	fmt.Println(string(data))
	return nil
}

type compositeRow struct {
	Standing rating.Standing
	Result   composite.Result
}

// printCompositeTable outputs composite standings as a formatted table.
func printCompositeTable(entries []compositeRow, epoch int, preset string, weights composite.Weights, explain bool) {
	fmt.Printf("Standings (epoch %d, composite: preset=%s W_goal=%.0f W_comp=%.0f W_nov=%.0f)\n",
		epoch, preset, weights.Goal, weights.Constraint, weights.Novelty)
	fmt.Printf("%-8s%-10s  %13s  %8s  %8s  %7s  %4s  %5s\n",
		"Rank", "Hypothesis", "Composite", "Elo", "Delta", "Matches", "Wins", "Draws")
	fmt.Println(strings.Repeat("-", 75))
	for i, e := range entries {
		fmt.Printf("%-8d%-10s  %13.1f  %8.1f  %+8.1f  %7d  %4d  %5d\n",
			i+1, e.Standing.HypothesisID, e.Result.Composite, e.Result.Tournament, e.Result.Delta,
			e.Standing.Matches, e.Standing.Wins, e.Standing.Draws)
		if explain {
			printComponentExplain("goal", e.Result.Goal)
			printComponentExplain("constraint", e.Result.Constraint)
			printComponentExplain("novelty", e.Result.Novelty)
		}
	}
}

func printComponentExplain(axis string, c composite.Component) {
	scoreStr := "-"
	if c.Score != nil {
		scoreStr = fmt.Sprintf("%d/5", *c.Score)
	}
	srcStr := "(not assessed)"
	if c.Source != "" {
		srcStr = fmt.Sprintf("(%s)", c.Source)
	}
	fmt.Printf("          %-12s%3s  S=%.2f  W=%.0f   ->  %+5.1f   %s\n",
		axis, scoreStr, c.S, c.Weight, c.Contribution, srcStr)
}

func makeComponentJSON(c composite.Component) componentJSON {
	var src *string
	if c.Source != "" {
		s := c.Source
		src = &s
	}
	return componentJSON{
		Score:        c.Score,
		S:            c.S,
		Weight:       c.Weight,
		Contribution: c.Contribution,
		Source:       src,
	}
}

// printCompositeJSON outputs composite standings as a JSON array, with provenance written to stderr.
func printCompositeJSON(entries []compositeRow, epoch int, preset string, weights composite.Weights, explain bool) error {
	fmt.Fprintf(os.Stderr, "Standings (epoch %d, composite: preset=%s W_goal=%.0f W_comp=%.0f W_nov=%.0f)\n",
		epoch, preset, weights.Goal, weights.Constraint, weights.Novelty)

	out := make([]standingJSON, len(entries))
	for i, e := range entries {
		eloComp := e.Result.Composite
		delta := e.Result.Delta
		row := standingJSON{
			Rank:           i + 1,
			HypothesisID:   e.Standing.HypothesisID,
			Elo:            e.Standing.Elo,
			EloComposite:   &eloComp,
			CompositeDelta: &delta,
			Matches:        e.Standing.Matches,
			Wins:           e.Standing.Wins,
			Draws:          e.Standing.Draws,
		}
		if explain {
			row.CompositeWeights = &weightsJSON{
				Goal:       weights.Goal,
				Constraint: weights.Constraint,
				Novelty:    weights.Novelty,
			}
			row.Components = &componentsJSON{
				Goal:       makeComponentJSON(e.Result.Goal),
				Constraint: makeComponentJSON(e.Result.Constraint),
				Novelty:    makeComponentJSON(e.Result.Novelty),
			}
		}
		out[i] = row
	}

	data, err := json.MarshalIndent(out, "", "  ")
	if err != nil {
		return fmt.Errorf("marshaling standings: %w", err)
	}
	fmt.Println(string(data))
	return nil
}
