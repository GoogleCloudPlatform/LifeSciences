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
	"io"
	"os"
	"path/filepath"

	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/hypex/internal/datastore"
	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/hypex/internal/schema"
	"github.com/spf13/cobra"
)

func init() {
	cmd := &cobra.Command{
		Use:   "add-match [file|-]",
		Short: "Validate and add a tournament match to the run",
		Long: `Reads a match JSON from a file (or stdin with -), validates it
against the match schema, assigns the next sequential ID, and writes
it atomically to matches/M-NNNN.json in the run directory.

The input JSON should contain all required fields except 'id', which is
assigned automatically via the next-id allocator. If 'id' is present in
the input, it is overwritten with the allocated ID.

Example:
  echo '{"epoch":0, ...}' | hypex add-match --run my-run -
  hypex add-match --run my-run match-record.json`,
		Args: cobra.ExactArgs(1),
		RunE: runAddMatch,
	}

	rootCmd.AddCommand(cmd)
}

func runAddMatch(_ *cobra.Command, args []string) error {
	if flagRunID == "" {
		return fmt.Errorf("--run flag is required")
	}

	// Read input from file or stdin.
	var data []byte
	var err error
	if args[0] == "-" {
		data, err = io.ReadAll(os.Stdin)
	} else {
		data, err = os.ReadFile(args[0])
	}
	if err != nil {
		return fmt.Errorf("reading input: %w", err)
	}

	// Parse the JSON into a generic map so we can set the id field.
	var match map[string]any
	if err := json.Unmarshal(data, &match); err != nil {
		return fmt.Errorf("parsing match JSON: %w", err)
	}

	// Allocate the next match ID.
	runDir, err := datastore.RunPath(flagRunDir, flagRunID)
	if err != nil {
		return fmt.Errorf("invalid run ID: %w", err)
	}
	id, _, err := datastore.NextID(runDir, datastore.ArtifactMatch)
	if err != nil {
		return fmt.Errorf("allocating match ID: %w", err)
	}

	// Set the allocated ID on the match.
	match["id"] = id

	// Re-marshal with indentation for readability.
	output, err := json.MarshalIndent(match, "", "  ")
	if err != nil {
		return fmt.Errorf("marshalling match: %w", err)
	}
	output = append(output, '\n')

	// Validate against the match schema.
	if err := schema.ValidateBytes(flagSchemaDir, "match.schema.json", output); err != nil {
		return fmt.Errorf("schema validation failed: %w", err)
	}

	// Write atomically to the matches directory.
	outDir := filepath.Join(runDir, "matches")
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return fmt.Errorf("creating matches directory: %w", err)
	}
	outPath := filepath.Join(outDir, id+".json")
	if err := datastore.WriteFileAtomic(outPath, output); err != nil {
		return fmt.Errorf("writing match file: %w", err)
	}

	fmt.Printf("Added match: %s → %s\n", id, outPath)
	return nil
}
