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
	"strings"

	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/hypex/internal/datastore"
	"github.com/spf13/cobra"
	"gopkg.in/yaml.v3"
)

func init() {
	cmd := &cobra.Command{
		Use:   "status",
		Short: "Show run dashboard with counts and budget usage",
		Long: `Displays a summary dashboard for a run, including:
- Hypothesis counts by status
- Review count
- Match count
- Budget usage (if run.yaml defines budgets)`,
		RunE: runStatus,
	}

	rootCmd.AddCommand(cmd)
}

// runConfig represents the subset of run.yaml we parse for budget display.
type runConfig struct {
	RunID   string `yaml:"run_id"`
	Goal    string `yaml:"goal"`
	Budgets struct {
		MaxHypotheses int `yaml:"max_hypotheses"`
		MaxMatches    int `yaml:"max_matches"`
		MaxEpochs     int `yaml:"max_epochs"`
	} `yaml:"budgets"`
}

func runStatus(_ *cobra.Command, _ []string) error {
	if flagRunID == "" {
		return fmt.Errorf("--run flag is required")
	}

	runDir, err := datastore.RunPath(flagRunDir, flagRunID)
	if err != nil {
		return fmt.Errorf("invalid run ID: %w", err)
	}

	// Read run.yaml for budget info.
	var cfg runConfig
	yamlData, err := os.ReadFile(filepath.Join(runDir, "run.yaml"))
	if err != nil {
		return fmt.Errorf("reading run.yaml: %w", err)
	}
	if err := yaml.Unmarshal(yamlData, &cfg); err != nil {
		return fmt.Errorf("parsing run.yaml: %w", err)
	}

	// Count hypotheses by status.
	statusCounts := make(map[string]int)
	totalHypotheses := 0
	hDir := filepath.Join(runDir, "hypotheses")
	if entries, err := os.ReadDir(hDir); err == nil {
		for _, e := range entries {
			if e.IsDir() || !strings.HasSuffix(e.Name(), ".json") {
				continue
			}
			totalHypotheses++
			data, err := os.ReadFile(filepath.Join(hDir, e.Name()))
			if err != nil {
				continue
			}
			var obj map[string]any
			if json.Unmarshal(data, &obj) == nil {
				if s, ok := obj["status"].(string); ok {
					statusCounts[s]++
				}
			}
		}
	}

	// Count reviews.
	totalReviews := countJSONFiles(filepath.Join(runDir, "reviews"))

	// Count matches.
	totalMatches := countJSONFiles(filepath.Join(runDir, "matches"))

	// Display dashboard.
	fmt.Printf("Run: %s\n", flagRunID)
	if cfg.Goal != "" {
		fmt.Printf("Goal: %s\n", cfg.Goal)
	}
	fmt.Println(strings.Repeat("─", 50))

	fmt.Printf("\nHypotheses: %d", totalHypotheses)
	if cfg.Budgets.MaxHypotheses > 0 {
		fmt.Printf(" / %d", cfg.Budgets.MaxHypotheses)
	}
	fmt.Println()

	if len(statusCounts) > 0 {
		statuses := []string{"proposed", "reviewed", "active", "merged", "retired", "quarantined"}
		for _, s := range statuses {
			if c, ok := statusCounts[s]; ok {
				fmt.Printf("  %-14s %d\n", s, c)
			}
		}
	}

	fmt.Printf("\nReviews:    %d\n", totalReviews)

	fmt.Printf("Matches:    %d", totalMatches)
	if cfg.Budgets.MaxMatches > 0 {
		fmt.Printf(" / %d", cfg.Budgets.MaxMatches)
	}
	fmt.Println()

	if cfg.Budgets.MaxEpochs > 0 {
		fmt.Printf("\nMax epochs: %d\n", cfg.Budgets.MaxEpochs)
	}

	return nil
}

func countJSONFiles(dir string) int {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return 0
	}
	count := 0
	for _, e := range entries {
		if !e.IsDir() && strings.HasSuffix(e.Name(), ".json") {
			count++
		}
	}
	return count
}
