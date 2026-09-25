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
)

var (
	flagArtifactType string
	flagStatus       string
)

func init() {
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List artifacts in a run",
		Long: `Lists artifacts (hypotheses, reviews, or matches) in a run directory.
Supports filtering by status for hypotheses.

Examples:
  hypex list --run my-run --type hypothesis
  hypex list --run my-run --type hypothesis --status active
  hypex list --run my-run --type review`,
		RunE: runList,
	}

	cmd.Flags().StringVar(&flagArtifactType, "type", "hypothesis",
		"Artifact type to list: hypothesis, review, match")
	cmd.Flags().StringVar(&flagStatus, "status", "",
		"Filter by status (hypothesis only)")

	rootCmd.AddCommand(cmd)
}

func runList(_ *cobra.Command, _ []string) error {
	if flagRunID == "" {
		return fmt.Errorf("--run flag is required")
	}

	runDir, err := datastore.RunPath(flagRunDir, flagRunID)
	if err != nil {
		return fmt.Errorf("invalid run ID: %w", err)
	}

	var subdir string
	switch strings.ToLower(flagArtifactType) {
	case "hypothesis", "hypotheses", "h":
		subdir = "hypotheses"
	case "review", "reviews", "r":
		subdir = "reviews"
	case "match", "matches", "m":
		subdir = "matches"
	default:
		return fmt.Errorf("unknown artifact type %q (valid: hypothesis, review, match)", flagArtifactType)
	}

	dir := filepath.Join(runDir, subdir)
	entries, err := os.ReadDir(dir)
	if err != nil {
		if os.IsNotExist(err) {
			fmt.Println("No artifacts found.")
			return nil
		}
		return fmt.Errorf("reading directory %s: %w", dir, err)
	}

	count := 0
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".json") {
			continue
		}

		fpath := filepath.Join(dir, e.Name())

		// If filtering by status, read and check the file.
		if flagStatus != "" && subdir == "hypotheses" {
			data, err := os.ReadFile(fpath)
			if err != nil {
				return fmt.Errorf("reading %s: %w", fpath, err)
			}
			var obj map[string]any
			if err := json.Unmarshal(data, &obj); err != nil {
				return fmt.Errorf("parsing %s: %w", fpath, err)
			}
			status, _ := obj["status"].(string)
			if status != flagStatus {
				continue
			}
		}

		// Print a summary line.
		if subdir == "hypotheses" {
			summary, err := hypothesisSummary(filepath.Join(dir, e.Name()))
			if err != nil {
				fmt.Printf("%-10s  (error reading: %v)\n", e.Name(), err)
			} else {
				fmt.Println(summary)
			}
		} else {
			fmt.Println(e.Name())
		}
		count++
	}

	if count == 0 {
		fmt.Println("No artifacts found.")
	}
	return nil
}

func hypothesisSummary(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	var obj map[string]any
	if err := json.Unmarshal(data, &obj); err != nil {
		return "", err
	}
	id, _ := obj["id"].(string)
	title, _ := obj["title"].(string)
	status, _ := obj["status"].(string)
	return fmt.Sprintf("%-10s [%-12s] %s", id, status, title), nil
}
