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

	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/hypex/internal/datastore"
	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/hypex/internal/schema"
	"github.com/spf13/cobra"
)

func init() {
	cmd := &cobra.Command{
		Use:   "validate [file...]",
		Short: "Validate artifact files against their JSON schemas",
		Long: `Validates one or more JSON artifact files against their matching schemas.
If no files are given but --run is set, validates all artifacts in the run.

Schema matching is automatic: H-*.json → hypothesis schema,
H-*.R-*.json → review schema, M-*.json → match schema,
epoch-*.json → ratings schema, citations/H-*.json or citations.json → citation-manifest schema.

Exit code 0 if all files are valid, 1 if any fail.`,
		RunE: runValidate,
	}

	rootCmd.AddCommand(cmd)
}

func runValidate(_ *cobra.Command, args []string) error {
	var files []string

	if len(args) > 0 {
		files = args
	} else if flagRunID != "" {
		// Validate all artifacts in the run.
		runDir, err := datastore.RunPath(flagRunDir, flagRunID)
		if err != nil {
			return fmt.Errorf("invalid run ID: %w", err)
		}
		found, err := findArtifactFiles(runDir)
		if err != nil {
			return fmt.Errorf("scanning run directory: %w", err)
		}
		files = found
	} else {
		return fmt.Errorf("provide file(s) to validate or use --run to validate an entire run")
	}

	if len(files) == 0 {
		fmt.Println("No artifact files found to validate.")
		return nil
	}

	var errs []string
	passed := 0
	for _, f := range files {
		if err := schema.ValidateFile(flagSchemaDir, f); err != nil {
			errs = append(errs, err.Error())
			fmt.Printf("FAIL  %s\n", f)
		} else {
			passed++
			fmt.Printf("PASS  %s\n", f)
		}
	}

	fmt.Printf("\n%d/%d files valid\n", passed, len(files))

	if len(errs) > 0 {
		return fmt.Errorf("validation errors:\n%s", strings.Join(errs, "\n"))
	}
	return nil
}

// findArtifactFiles walks the run directory and returns paths to all JSON
// artifact files in the standard subdirectories.
func findArtifactFiles(runDir string) ([]string, error) {
	dirs := []string{"hypotheses", "reviews", "matches", "ratings", "citations"}
	var files []string

	for _, d := range dirs {
		dir := filepath.Join(runDir, d)
		entries, err := os.ReadDir(dir)
		if err != nil {
			if os.IsNotExist(err) {
				continue
			}
			return nil, err
		}
		for _, e := range entries {
			// Skip symlinks to prevent symlink exploitation (issue #306).
			if e.Type()&os.ModeSymlink != 0 {
				continue
			}
			if !e.IsDir() && strings.HasSuffix(e.Name(), ".json") {
				files = append(files, filepath.Join(dir, e.Name()))
			}
		}
	}

	return files, nil
}
