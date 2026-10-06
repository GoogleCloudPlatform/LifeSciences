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

	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/hypex/internal/datastore"
	"github.com/spf13/cobra"
)

func init() {
	cmd := &cobra.Command{
		Use:   "report",
		Short: "Generate a run report (stub)",
		Long: `Generates a report for a run. This is intentionally minimal — real
report generation is the meta-review agent's responsibility in later
phases. This stub checks that the run exists and the report directory
is present, then reports that generation is deferred.`,
		RunE: runReport,
	}

	rootCmd.AddCommand(cmd)
}

func runReport(_ *cobra.Command, _ []string) error {
	if flagRunID == "" {
		return fmt.Errorf("--run flag is required")
	}

	runDir, err := datastore.RunPath(flagRunDir, flagRunID)
	if err != nil {
		return fmt.Errorf("invalid run ID: %w", err)
	}
	reportDir := filepath.Join(runDir, "report")

	if _, err := os.Stat(reportDir); err != nil {
		return fmt.Errorf("run %q not found or missing report/ directory", flagRunID)
	}

	fmt.Printf("Run: %s\n", flagRunID)
	fmt.Println("Report generation is deferred to the meta-review agent (Phase 3).")
	fmt.Printf("Report will be written to: %s/final.md\n", reportDir)
	return nil
}
