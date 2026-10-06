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

// Package cmd implements the Cobra command tree for the hypex CLI.
package cmd

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/spf13/cobra"
)

var (
	flagRunDir    string
	flagRunID     string
	flagSchemaDir string
)

// rootCmd is the top-level command for the hypex CLI.
// SilenceErrors and SilenceUsage are set so that Cobra does not print errors
// or usage on its own. All error output is centralized in Execute(), which
// prints the single authoritative error line to stderr. This avoids doubled
// output while still surfacing unknown-command and flag-validation errors.
var rootCmd = &cobra.Command{
	Use:   "hypex",
	Short: "Hypothesis-explorer datastore lifecycle and integrity CLI",
	Long: `hypex manages the hypothesis-explorer run datastore.

Verbs: init-run, add-hypothesis, add-match, add-review, set-status, next-id, validate, list, status, report

All datastore writes use atomic operations (temp file + rename) to prevent
partial writes. Concurrent ID allocation is safe via filesystem locking.`,
	SilenceErrors: true,
	SilenceUsage:  true,
}

// defaultRunsDir is the default base directory for runs.
const defaultRunsDir = "/scion-volumes/executions"

// defaultSchemaDir resolves schemas installed beside the executable by DDE's
// bootstrapper. The source-tree fallback keeps upstream development commands
// working from the repository root.
func defaultSchemaDir() string {
	if configured := os.Getenv("HYPEX_SCHEMA_DIR"); configured != "" {
		return configured
	}
	if executable, err := os.Executable(); err == nil {
		installed := filepath.Clean(filepath.Join(
			filepath.Dir(executable), "..", "share", "hypex", "schemas",
		))
		if info, statErr := os.Stat(installed); statErr == nil && info.IsDir() {
			return installed
		}
	}
	return "hypothesis-explorer/schemas"
}

func init() {
	rootCmd.PersistentFlags().StringVar(&flagRunDir, "run-dir", defaultRunsDir,
		"Base directory containing runs")
	rootCmd.PersistentFlags().StringVar(&flagRunID, "run", "",
		"Run ID to operate on")
	rootCmd.PersistentFlags().StringVar(&flagSchemaDir, "schema-dir", defaultSchemaDir(),
		"Directory containing JSON schema files")
}

// Execute runs the root command. Called from main.
// This is the single place where errors are printed to stderr. Individual
// RunE functions return errors without printing them; Cobra's own errors
// (unknown command, bad flags) also flow through here.
func Execute() {
	if err := rootCmd.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, "Error:", err)
		os.Exit(1)
	}
}
