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

	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/hypex/internal/datastore"
	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/hypex/internal/schema"
	"github.com/spf13/cobra"
)

var validHypothesisStatuses = map[string]bool{
	"proposed":    true,
	"reviewed":    true,
	"active":      true,
	"merged":      true,
	"retired":     true,
	"quarantined": true,
}

func init() {
	cmd := &cobra.Command{
		Use:   "set-status <hypothesis-id> <status>",
		Short: "Update hypothesis lifecycle status and handle transitions",
		Long: `Updates the status field of an existing hypothesis and re-validates it
against the hypothesis schema.

When transitioning to 'quarantined', the hypothesis file is moved atomically
from hypotheses/ to quarantine/ in the run directory.

Valid status values: proposed, reviewed, active, merged, retired, quarantined.

Example:
  hypex set-status --run my-run H-0042 quarantined
  hypex set-status --run my-run H-0042 reviewed`,
		Args: cobra.ExactArgs(2),
		RunE: runSetStatus,
	}

	rootCmd.AddCommand(cmd)
}

func runSetStatus(_ *cobra.Command, args []string) error {
	if flagRunID == "" {
		return fmt.Errorf("--run flag is required")
	}

	hypothesisID := args[0]
	if err := datastore.ValidateHypothesisID(hypothesisID); err != nil {
		return err
	}

	targetStatus := args[1]
	if !validHypothesisStatuses[targetStatus] {
		return fmt.Errorf("invalid status %q: must be one of proposed, reviewed, active, merged, retired, quarantined", targetStatus)
	}

	runDir, err := datastore.RunPath(flagRunDir, flagRunID)
	if err != nil {
		return fmt.Errorf("invalid run ID: %w", err)
	}
	hypoPath := filepath.Join(runDir, "hypotheses", hypothesisID+".json")
	quarPath := filepath.Join(runDir, "quarantine", hypothesisID+".json")

	// Locate existing hypothesis file.
	var srcPath string
	if _, err := os.Stat(hypoPath); err == nil {
		srcPath = hypoPath
	} else if _, err := os.Stat(quarPath); err == nil {
		srcPath = quarPath
	} else {
		return fmt.Errorf("hypothesis file not found: %s", hypoPath)
	}

	// Read and parse hypothesis.
	data, err := os.ReadFile(srcPath)
	if err != nil {
		return fmt.Errorf("reading hypothesis file %s: %w", srcPath, err)
	}

	var hypothesis map[string]any
	if err := json.Unmarshal(data, &hypothesis); err != nil {
		return fmt.Errorf("parsing hypothesis JSON from %s: %w", srcPath, err)
	}

	// Update status field.
	hypothesis["status"] = targetStatus

	// Re-marshal with indentation.
	output, err := json.MarshalIndent(hypothesis, "", "  ")
	if err != nil {
		return fmt.Errorf("marshalling hypothesis: %w", err)
	}
	output = append(output, '\n')

	// Validate against hypothesis schema.
	if err := schema.ValidateBytes(flagSchemaDir, "hypothesis.schema.json", output); err != nil {
		return fmt.Errorf("schema validation failed: %w", err)
	}

	// Determine destination path based on target status.
	var dstPath string
	if targetStatus == "quarantined" {
		dstPath = quarPath
	} else {
		dstPath = hypoPath
	}

	// Ensure destination directory exists.
	if err := os.MkdirAll(filepath.Dir(dstPath), 0o755); err != nil {
		return fmt.Errorf("creating destination directory: %w", err)
	}

	// Write atomically to destination.
	if err := datastore.WriteFileAtomic(dstPath, output); err != nil {
		return fmt.Errorf("writing hypothesis file to %s: %w", dstPath, err)
	}

	// Remove from old location if moved.
	if srcPath != dstPath {
		if err := os.Remove(srcPath); err != nil {
			return fmt.Errorf("removing old hypothesis file %s: %w", srcPath, err)
		}
		fmt.Printf("Updated hypothesis %s status to %q: moved %s → %s\n", hypothesisID, targetStatus, srcPath, dstPath)
	} else {
		fmt.Printf("Updated hypothesis %s status to %q: %s\n", hypothesisID, targetStatus, dstPath)
	}

	return nil
}
