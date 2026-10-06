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

var flagReviewFor string

func init() {
	cmd := &cobra.Command{
		Use:   "add-review [file|-]",
		Short: "Validate and add a review to the run",
		Long: `Reads a review JSON from a file (or stdin with -), validates it
against the review schema, assigns the next sequential scoped review ID
for the specified hypothesis, and writes it atomically to
reviews/H-XXXX.R-NN.json in the run directory.

The --for <hypothesis-id> flag specifies the target hypothesis (e.g. H-0042).

The input JSON should contain all required fields except 'id', which is
assigned automatically via the next-id allocator. If 'id' is present in
the input, it is overwritten with the allocated ID.

Example:
  echo '{"review_type":"full", ...}' | hypex add-review --run my-run --for H-0042 -
  hypex add-review --run my-run --for H-0042 review.json`,
		Args: cobra.ExactArgs(1),
		RunE: runAddReview,
	}

	cmd.Flags().StringVar(&flagReviewFor, "for", "", "Hypothesis ID that the review is for (required)")

	rootCmd.AddCommand(cmd)
}

func runAddReview(_ *cobra.Command, args []string) error {
	if flagRunID == "" {
		return fmt.Errorf("--run flag is required")
	}

	if flagReviewFor == "" {
		return fmt.Errorf("--for <hypothesis-id> is required")
	}

	if err := datastore.ValidateHypothesisID(flagReviewFor); err != nil {
		return err
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

	// Parse the JSON into a generic map so we can set fields.
	var review map[string]any
	if err := json.Unmarshal(data, &review); err != nil {
		return fmt.Errorf("parsing review JSON: %w", err)
	}

	// Verify hypothesis_id consistency if present in JSON.
	if hID, ok := review["hypothesis_id"].(string); ok && hID != "" && hID != flagReviewFor {
		return fmt.Errorf("hypothesis_id in JSON (%s) does not match --for flag (%s)", hID, flagReviewFor)
	}
	review["hypothesis_id"] = flagReviewFor

	// Allocate the next review ID scoped to the target hypothesis.
	runDir, err := datastore.RunPath(flagRunDir, flagRunID)
	if err != nil {
		return fmt.Errorf("invalid run ID: %w", err)
	}
	id, _, err := datastore.NextReviewID(runDir, flagReviewFor)
	if err != nil {
		return fmt.Errorf("allocating review ID: %w", err)
	}

	// Set the allocated ID on the review.
	review["id"] = id

	// Re-marshal with indentation for readability.
	output, err := json.MarshalIndent(review, "", "  ")
	if err != nil {
		return fmt.Errorf("marshalling review: %w", err)
	}
	output = append(output, '\n')

	// Validate against the review schema.
	if err := schema.ValidateBytes(flagSchemaDir, "review.schema.json", output); err != nil {
		return fmt.Errorf("schema validation failed: %w", err)
	}

	// Write atomically to the reviews directory.
	outDir := filepath.Join(runDir, "reviews")
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return fmt.Errorf("creating reviews directory: %w", err)
	}
	outPath := filepath.Join(outDir, id+".json")
	if err := datastore.WriteFileAtomic(outPath, output); err != nil {
		return fmt.Errorf("writing review file: %w", err)
	}

	fmt.Printf("Added review: %s → %s\n", id, outPath)
	return nil
}
