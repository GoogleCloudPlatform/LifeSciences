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

	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/hypex/internal/datastore"
	"github.com/spf13/cobra"
)

var flagFor string

func init() {
	cmd := &cobra.Command{
		Use:   "next-id <artifact-type>",
		Short: "Atomically allocate the next ID for an artifact type",
		Long: `Allocates the next sequential ID for the given artifact type within
the current run. Uses filesystem locking (flock) to ensure that concurrent
callers from separate OS processes never receive the same ID.

Artifact types: hypothesis (or h), review (or r), match (or m)

For review artifact type, --for <hypothesis-id> is required (e.g. --for H-0042).

Examples:
  hypex next-id --run my-run hypothesis
  # Output: H-0001
  hypex next-id --run my-run review --for H-0042
  # Output: H-0042.R-01`,
		Args: cobra.ExactArgs(1),
		RunE: runNextID,
	}

	cmd.Flags().StringVar(&flagFor, "for", "", "Hypothesis ID that the review is for (required for review)")

	rootCmd.AddCommand(cmd)
}

func runNextID(_ *cobra.Command, args []string) error {
	if flagRunID == "" {
		return fmt.Errorf("--run flag is required")
	}

	at, err := datastore.ParseArtifactType(args[0])
	if err != nil {
		return err
	}

	runDir, err := datastore.RunPath(flagRunDir, flagRunID)
	if err != nil {
		return fmt.Errorf("invalid run ID: %w", err)
	}

	if at == datastore.ArtifactReview {
		if flagFor == "" {
			return fmt.Errorf("--for <hypothesis-id> is required for review artifact type")
		}
		id, _, err := datastore.NextReviewID(runDir, flagFor)
		if err != nil {
			return fmt.Errorf("allocating review ID: %w", err)
		}
		fmt.Println(id)
		return nil
	}

	if flagFor != "" {
		return fmt.Errorf("--for is only valid for review artifact type")
	}

	id, _, err := datastore.NextID(runDir, at)
	if err != nil {
		return fmt.Errorf("allocating ID: %w", err)
	}

	fmt.Println(id)
	return nil
}
