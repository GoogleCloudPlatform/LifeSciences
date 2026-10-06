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

// Package cmd implements the Cobra command tree for the elo CLI.
package cmd

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"
)

var (
	flagRunDir string
)

// rootCmd is the top-level command for the elo CLI.
var rootCmd = &cobra.Command{
	Use:   "elo",
	Short: "Elo rating engine for hypothesis-explorer tournaments",
	Long: `elo computes Elo ratings from match records and generates optimal
pairings for hypothesis-vs-hypothesis tournament matches.

Verbs: recompute, pair, standings

The match ledger is the single source of truth. Ratings are derived state,
deterministically recomputed from matches via 'elo recompute'.`,
	SilenceErrors: true,
	SilenceUsage:  true,
}

func init() {
	rootCmd.PersistentFlags().StringVar(&flagRunDir, "run-dir", "",
		"Path to the run directory (required)")
}

// Execute runs the root command. Called from main.
func Execute() {
	if err := rootCmd.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, "Error:", err)
		os.Exit(1)
	}
}
