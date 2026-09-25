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
	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/hypex/internal/lineage"
	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/hypex/internal/schema"
	"github.com/spf13/cobra"
)

var flagStrictLineage bool

func init() {
	cmd := &cobra.Command{
		Use:   "add-hypothesis [file|-]",
		Short: "Validate and add a hypothesis to the run",
		Long: `Reads a hypothesis JSON from a file (or stdin with -), validates it
against the hypothesis schema, assigns the next sequential ID, and writes
it atomically to hypotheses/H-NNNN.json in the run directory.

The input JSON should contain all required fields except 'id', which is
assigned automatically via the next-id allocator. If 'id' is present in
the input, it is overwritten with the allocated ID.

Example:
  echo '{"title":"...", ...}' | hypex add-hypothesis --run my-run -
  hypex add-hypothesis --run my-run hypothesis-draft.json`,
		Args: cobra.ExactArgs(1),
		RunE: runAddHypothesis,
	}

	cmd.Flags().BoolVar(&flagStrictLineage, "strict-lineage", false,
		"Reject hypotheses with non-compliant citation lineage instead of warning")

	rootCmd.AddCommand(cmd)
}

func runAddHypothesis(_ *cobra.Command, args []string) error {
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
	var hypothesis map[string]any
	if err := json.Unmarshal(data, &hypothesis); err != nil {
		return fmt.Errorf("parsing hypothesis JSON: %w", err)
	}

	// Allocate the next hypothesis ID.
	runDir, err := datastore.RunPath(flagRunDir, flagRunID)
	if err != nil {
		return fmt.Errorf("invalid run ID: %w", err)
	}
	id, _, err := datastore.NextID(runDir, datastore.ArtifactHypothesis)
	if err != nil {
		return fmt.Errorf("allocating hypothesis ID: %w", err)
	}

	// Set the allocated ID on the hypothesis.
	hypothesis["id"] = id

	// Re-marshal with indentation for readability.
	output, err := json.MarshalIndent(hypothesis, "", "  ")
	if err != nil {
		return fmt.Errorf("marshalling hypothesis: %w", err)
	}
	output = append(output, '\n')

	// Validate against the hypothesis schema.
	if err := schema.ValidateBytes(flagSchemaDir, "hypothesis.schema.json", output); err != nil {
		return fmt.Errorf("schema validation failed: %w", err)
	}

	// Compute citation delta for evolved hypotheses.
	if err := injectCitationDelta(hypothesis, runDir); err != nil {
		return err
	}

	// Re-marshal after potential citation_delta injection.
	output, err = json.MarshalIndent(hypothesis, "", "  ")
	if err != nil {
		return fmt.Errorf("marshalling hypothesis: %w", err)
	}
	output = append(output, '\n')

	// Write atomically to the hypotheses directory.
	outPath := filepath.Join(runDir, "hypotheses", id+".json")
	if err := datastore.WriteFileAtomic(outPath, output); err != nil {
		return fmt.Errorf("writing hypothesis file: %w", err)
	}

	fmt.Printf("Added hypothesis: %s → %s\n", id, outPath)
	return nil
}

// injectCitationDelta computes and injects citation_delta into the hypothesis
// lineage when it is an evolved hypothesis (non-null operator with parents).
// Returns an error only when --strict-lineage is set and the delta is non-compliant.
func injectCitationDelta(hypothesis map[string]any, runDir string) error {
	lin, ok := hypothesis["lineage"].(map[string]any)
	if !ok {
		return nil
	}

	operator, _ := lin["operator"].(string)
	if operator == "" || operator == "null" {
		return nil
	}

	parentsRaw, _ := lin["parents"].([]any)
	if len(parentsRaw) == 0 {
		return nil
	}

	// Convert parent IDs to strings.
	parentIDs := make([]string, 0, len(parentsRaw))
	for _, p := range parentsRaw {
		if s, ok := p.(string); ok {
			parentIDs = append(parentIDs, s)
		}
	}
	if len(parentIDs) == 0 {
		return nil
	}

	// Load parent evidence sets.
	parentSets, loadWarnings := loadParentEvidenceSets(runDir, parentIDs)

	// Print any warnings about missing parents.
	for _, w := range loadWarnings {
		fmt.Fprintf(os.Stderr, "Warning: %s\n", w)
	}

	// Extract child evidence lit_ids.
	childSet := extractLitIDs(hypothesis)

	// Compute the citation delta.
	delta := lineage.ComputeCitationDelta(operator, parentSets, childSet)

	// If any parents were missing, force non-compliant.
	if len(loadWarnings) > 0 {
		delta.Compliant = false
	}

	// Inject citation_delta into lineage.
	lin["citation_delta"] = map[string]any{
		"parent_lit_ids": delta.ParentLitIDs,
		"inherited":      delta.Inherited,
		"added":          delta.Added,
		"dropped":        delta.Dropped,
		"rule":           delta.Rule,
		"compliant":      delta.Compliant,
	}

	// Handle non-compliance.
	if !delta.Compliant {
		printComplianceWarning(operator, delta)

		if flagStrictLineage {
			return fmt.Errorf("citation lineage non-compliant for operator %q (--strict-lineage is set): dropped=%v, added=%v",
				operator, delta.Dropped, delta.Added)
		}
	}

	return nil
}

// loadParentEvidenceSets loads evidence lit_ids from each parent hypothesis file.
// Returns the evidence sets and any warnings for missing parents.
func loadParentEvidenceSets(runDir string, parentIDs []string) ([][]string, []string) {
	var parentSets [][]string
	var warnings []string

	for _, pid := range parentIDs {
		parentPath := filepath.Join(runDir, "hypotheses", pid+".json")
		data, err := os.ReadFile(parentPath)
		if err != nil {
			warnings = append(warnings, fmt.Sprintf("parent %s not found: %v", pid, err))
			continue
		}

		var parent map[string]any
		if err := json.Unmarshal(data, &parent); err != nil {
			warnings = append(warnings, fmt.Sprintf("parent %s: invalid JSON: %v", pid, err))
			continue
		}

		parentSets = append(parentSets, extractLitIDs(parent))
	}

	return parentSets, warnings
}

// extractLitIDs extracts lit_id strings from a hypothesis's evidence array.
// Evidence items without a lit_id field are skipped.
func extractLitIDs(hypothesis map[string]any) []string {
	evidenceRaw, ok := hypothesis["evidence"].([]any)
	if !ok {
		return []string{}
	}

	var litIDs []string
	for _, item := range evidenceRaw {
		ev, ok := item.(map[string]any)
		if !ok {
			continue
		}
		litID, ok := ev["lit_id"].(string)
		if !ok || litID == "" {
			continue
		}
		litIDs = append(litIDs, litID)
	}

	if litIDs == nil {
		return []string{}
	}
	return litIDs
}

// printComplianceWarning prints a human-readable warning about citation
// lineage non-compliance to stderr.
func printComplianceWarning(operator string, delta lineage.CitationDelta) {
	fmt.Fprintf(os.Stderr, "Warning: citation lineage non-compliant for operator %q (rule: %s)\n", operator, delta.Rule)
	if len(delta.Dropped) > 0 {
		fmt.Fprintf(os.Stderr, "  Dropped parent citations: %v\n", delta.Dropped)
	}
	if len(delta.Added) > 0 && delta.Rule == "subset" {
		fmt.Fprintf(os.Stderr, "  Added citations not in parents: %v\n", delta.Added)
	}
}
