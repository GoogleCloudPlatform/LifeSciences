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
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/hypex/internal/datastore"
	"github.com/spf13/cobra"
	"gopkg.in/yaml.v3"
)

var (
	flagGoal              string
	flagConstraints       []string
	flagCompositePreset   string
	flagMaxHypotheses     int
	flagMaxMatches        int
	flagMaxEpochs         int
	flagTournamentStrat   string
	flagRetrospectiveMode bool
)

var validCompositePresets = []string{
	"balanced",
	"disable_novelty",
	"focus_on_breakthroughs",
	"strict_constraints",
	"pure_tournament",
}

func isValidCompositePreset(preset string) bool {
	for _, p := range validCompositePresets {
		if preset == p {
			return true
		}
	}
	return false
}

func init() {
	cmd := &cobra.Command{
		Use:   "init-run [run-id]",
		Short: "Create a new run directory with the standard layout",
		Long: `Creates a new run directory under --run-dir with all required
subdirectories (hypotheses/, reviews/, matches/, ratings/, proximity/,
meta/, quarantine/, report/, citations/) and a run.yaml configuration file.

The run.yaml captures the research goal, configuration, and budget
constraints for the run.`,
		Args: cobra.ExactArgs(1),
		RunE: runInitRun,
	}

	cmd.Flags().StringVar(&flagGoal, "goal", "",
		"Research goal for this run (required)")
	cmd.Flags().StringArrayVar(&flagConstraints, "constraint", nil,
		"Hard constraint that hypotheses must satisfy (repeatable)")
	cmd.Flags().StringVar(&flagCompositePreset, "composite-preset", "balanced",
		"Composite Elo ranking preset: balanced, disable_novelty, focus_on_breakthroughs, strict_constraints, pure_tournament")
	cmd.Flags().IntVar(&flagMaxHypotheses, "max-hypotheses", 50,
		"Maximum number of hypotheses to generate")
	cmd.Flags().IntVar(&flagMaxMatches, "max-matches", 200,
		"Maximum number of tournament matches")
	cmd.Flags().IntVar(&flagMaxEpochs, "max-epochs", 5,
		"Maximum number of epochs")
	cmd.Flags().StringVar(&flagTournamentStrat, "tournament-strategy", "proximity-elo",
		"Tournament strategy: proximity-elo or swiss")
	cmd.Flags().BoolVar(&flagRetrospectiveMode, "retrospective-mode", false,
		"Enable friction logging, retrospectives, and GitHub issue filing")

	cmd.MarkFlagRequired("goal")

	rootCmd.AddCommand(cmd)
}

func runInitRun(cmd *cobra.Command, args []string) error {
	runID := args[0]

	strat := flagTournamentStrat
	if cmd != nil && cmd.Flags().Changed("tournament-strategy") {
		var err error
		strat, err = cmd.Flags().GetString("tournament-strategy")
		if err != nil {
			return err
		}
	}

	// Validate tournament strategy.
	switch strat {
	case "proximity-elo", "swiss":
		// valid
	default:
		return fmt.Errorf("invalid --tournament-strategy %q: must be \"proximity-elo\" or \"swiss\"", strat)
	}

	// Validate composite preset.
	preset := flagCompositePreset
	if cmd != nil && cmd.Flags().Changed("composite-preset") {
		var err error
		preset, err = cmd.Flags().GetString("composite-preset")
		if err != nil {
			return err
		}
	}

	if !isValidCompositePreset(preset) {
		return fmt.Errorf("invalid --composite-preset %q: valid presets are %s", preset, strings.Join(validCompositePresets, ", "))
	}

	var constraints []string
	if cmd != nil && cmd.Flags().Changed("constraint") {
		var err error
		constraints, err = cmd.Flags().GetStringArray("constraint")
		if err != nil {
			return err
		}
	} else if len(flagConstraints) > 0 {
		constraints = flagConstraints
	}

	goal := flagGoal
	if cmd != nil && cmd.Flags().Changed("goal") {
		var err error
		goal, err = cmd.Flags().GetString("goal")
		if err != nil {
			return err
		}
	}

	maxH := flagMaxHypotheses
	if cmd != nil && cmd.Flags().Changed("max-hypotheses") {
		var err error
		maxH, err = cmd.Flags().GetInt("max-hypotheses")
		if err != nil {
			return err
		}
	}

	maxM := flagMaxMatches
	if cmd != nil && cmd.Flags().Changed("max-matches") {
		var err error
		maxM, err = cmd.Flags().GetInt("max-matches")
		if err != nil {
			return err
		}
	}

	maxE := flagMaxEpochs
	if cmd != nil && cmd.Flags().Changed("max-epochs") {
		var err error
		maxE, err = cmd.Flags().GetInt("max-epochs")
		if err != nil {
			return err
		}
	}

	retroMode := flagRetrospectiveMode
	if cmd != nil && cmd.Flags().Changed("retrospective-mode") {
		var err error
		retroMode, err = cmd.Flags().GetBool("retrospective-mode")
		if err != nil {
			return err
		}
	}

	// Build run.yaml content.
	yaml := buildRunYAML(runID, goal, constraints, maxH, maxM, maxE, strat, preset, retroMode)

	if err := datastore.InitRun(flagRunDir, runID, yaml); err != nil {
		return err
	}

	// Create retro/ subdirectory only when retrospective mode is enabled.
	if retroMode {
		runDir, err := datastore.RunPath(flagRunDir, runID)
		if err != nil {
			return fmt.Errorf("invalid run ID: %w", err)
		}
		retroDir := filepath.Join(runDir, "retro")
		if err := os.MkdirAll(retroDir, 0o755); err != nil {
			return fmt.Errorf("creating retro subdirectory: %w", err)
		}
	}

	fmt.Printf("Initialized run: %s/%s\n", flagRunDir, runID)
	return nil
}

// runYAMLConfig is the structured representation of run.yaml, marshalled
// via yaml.Marshal to avoid YAML injection from untrusted field values
// (e.g. run IDs containing ':', '#', or '{').
type runYAMLConfig struct {
	RunID             string            `yaml:"run_id"`
	Goal              string            `yaml:"goal"`
	Constraints       []string          `yaml:"constraints,omitempty"`
	CreatedAt         string            `yaml:"created_at"`
	Budgets           runYAMLBudgets    `yaml:"budgets"`
	Tournament        runYAMLTournament `yaml:"tournament"`
	RetrospectiveMode bool              `yaml:"retrospective_mode"`
}

type runYAMLBudgets struct {
	MaxHypotheses int `yaml:"max_hypotheses"`
	MaxMatches    int `yaml:"max_matches"`
	MaxEpochs     int `yaml:"max_epochs"`
}

type runYAMLTournament struct {
	Strategy  string            `yaml:"strategy"`
	Composite *runYAMLComposite `yaml:"composite,omitempty"`
}

type runYAMLComposite struct {
	Preset  string              `yaml:"preset,omitempty"`
	Weights *runYAMLCompWeights `yaml:"weights,omitempty"`
}

type runYAMLCompWeights struct {
	Goal       *float64 `yaml:"goal,omitempty"`
	Constraint *float64 `yaml:"constraint,omitempty"`
	Novelty    *float64 `yaml:"novelty,omitempty"`
}

func buildRunYAML(runID, goal string, constraints []string, maxH, maxM, maxE int, strategy, compositePreset string, retroMode bool) string {
	if compositePreset == "" {
		compositePreset = "balanced"
	}
	var cList []string
	if len(constraints) > 0 {
		cList = constraints
	}
	cfg := runYAMLConfig{
		RunID:       runID,
		Goal:        goal,
		Constraints: cList,
		CreatedAt:   time.Now().UTC().Format(time.RFC3339),
		Budgets: runYAMLBudgets{
			MaxHypotheses: maxH,
			MaxMatches:    maxM,
			MaxEpochs:     maxE,
		},
		Tournament: runYAMLTournament{
			Strategy: strategy,
			Composite: &runYAMLComposite{
				Preset: compositePreset,
			},
		},
		RetrospectiveMode: retroMode,
	}

	out, err := yaml.Marshal(&cfg)
	if err != nil {
		// Should never happen with these types; panic is appropriate here
		// since this is a programming error, not a user-input error.
		panic(fmt.Sprintf("marshalling run.yaml: %v", err))
	}

	header := "# Hypothesis-Explorer Run Configuration\n# Generated by: hypex init-run\n\n"
	return header + string(out)
}
