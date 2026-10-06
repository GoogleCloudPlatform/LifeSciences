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
	"sort"
	"strings"

	"github.com/scion-frontiers/hypex/hypothesis-explorer/tools/elo/internal/rating"
	"github.com/spf13/cobra"
)

func init() {
	var flagEpoch int

	cmd := &cobra.Command{
		Use:   "recompute",
		Short: "Replay match ledger to compute Elo ratings",
		Long: `Reads all matches/M-*.json files from the run directory, sorts them
by creation time, replays Elo updates sequentially, and writes the resulting
ratings to ratings/epoch-N.json.

The output is deterministic: the same match ledger always produces
byte-identical output.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if flagRunDir == "" {
				return fmt.Errorf("--run-dir is required")
			}

			matches, err := loadMatches(flagRunDir)
			if err != nil {
				return fmt.Errorf("loading matches: %w", err)
			}

			result, err := rating.ReplayMatches(matches, flagEpoch)
			if err != nil {
				return fmt.Errorf("replaying matches: %w", err)
			}

			return writeRatings(flagRunDir, result)
		},
	}

	cmd.Flags().IntVar(&flagEpoch, "epoch", 0, "Epoch number for the ratings output")
	rootCmd.AddCommand(cmd)
}

// matchJSON represents the full match JSON structure for deserialization.
type matchJSON struct {
	ID              string          `json:"id"`
	Epoch           int             `json:"epoch"`
	A               string          `json:"a"`
	B               string          `json:"b"`
	Format          string          `json:"format"`
	Winner          string          `json:"winner"`
	Margin          string          `json:"margin"`
	CriterionScores json.RawMessage `json:"criterion_scores"`
	Rationale       string          `json:"rationale"`
	TranscriptPath  string          `json:"transcript_path,omitempty"`
	Judge           string          `json:"judge"`
	CreatedAt       string          `json:"created_at"`
}

// loadMatches reads and parses all match files from the run directory,
// sorted by created_at then by ID for determinism.
func loadMatches(runDir string) ([]rating.Match, error) {
	matchDir := filepath.Join(runDir, "matches")

	entries, err := os.ReadDir(matchDir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("reading matches directory: %w", err)
	}

	var matches []rating.Match
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		name := entry.Name()
		if !strings.HasPrefix(name, "M-") || !strings.HasSuffix(name, ".json") {
			continue
		}

		data, err := os.ReadFile(filepath.Join(matchDir, name))
		if err != nil {
			return nil, fmt.Errorf("reading match file %s: %w", name, err)
		}

		var mj matchJSON
		if err := json.Unmarshal(data, &mj); err != nil {
			return nil, fmt.Errorf("parsing match file %s: %w", name, err)
		}

		matches = append(matches, rating.Match{
			ID:        mj.ID,
			Epoch:     mj.Epoch,
			A:         mj.A,
			B:         mj.B,
			Winner:    mj.Winner,
			Margin:    mj.Margin,
			CreatedAt: mj.CreatedAt,
		})
	}

	// Sort by created_at, then by ID for determinism.
	sort.Slice(matches, func(i, j int) bool {
		if matches[i].CreatedAt != matches[j].CreatedAt {
			return matches[i].CreatedAt < matches[j].CreatedAt
		}
		return matches[i].ID < matches[j].ID
	})

	return matches, nil
}

// ratingsOutput is the JSON-serializable ratings structure with sorted keys.
type ratingsOutput struct {
	Epoch        int                                 `json:"epoch"`
	BaseRating   float64                             `json:"base_rating"`
	Ratings      map[string]*rating.HypothesisRating `json:"ratings"`
	ComputedFrom string                              `json:"computed_from"`
}

// writeRatings writes the ratings result to ratings/epoch-N.json with
// deterministic JSON serialization (sorted keys).
func writeRatings(runDir string, result *rating.RatingsResult) error {
	ratingsDir := filepath.Join(runDir, "ratings")
	if err := os.MkdirAll(ratingsDir, 0o755); err != nil {
		return fmt.Errorf("creating ratings directory: %w", err)
	}

	outPath := filepath.Join(ratingsDir, fmt.Sprintf("epoch-%d.json", result.Epoch))

	// Build deterministic JSON with sorted hypothesis keys.
	data, err := marshalDeterministic(result)
	if err != nil {
		return fmt.Errorf("marshaling ratings: %w", err)
	}

	// Atomic write: temp file in same directory + fsync + rename.
	// Follows the WriteFileAtomic pattern from hypex/internal/datastore.
	if err := writeFileAtomic(outPath, data); err != nil {
		return err
	}

	fmt.Fprintf(os.Stderr, "Wrote %s\n", outPath)
	return nil
}

// writeFileAtomic writes data to a file using the temp-file-then-rename
// pattern to ensure atomicity. The temp file is created in the same directory
// (same filesystem) so rename is atomic. Fsync is called before rename to
// ensure data reaches stable storage. The temp file is cleaned up on error.
func writeFileAtomic(path string, data []byte) error {
	dir := filepath.Dir(path)

	tmp, err := os.CreateTemp(dir, ".elo-tmp-*")
	if err != nil {
		return fmt.Errorf("creating temp file: %w", err)
	}
	tmpName := tmp.Name()

	// Clean up the temp file on any error path.
	success := false
	defer func() {
		if !success {
			os.Remove(tmpName)
		}
	}()

	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return fmt.Errorf("writing temp file: %w", err)
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return fmt.Errorf("syncing temp file: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("closing temp file: %w", err)
	}

	if err := os.Rename(tmpName, path); err != nil {
		return fmt.Errorf("renaming temp file to %s: %w", path, err)
	}

	success = true
	return nil
}

// marshalDeterministic produces JSON with sorted map keys and consistent formatting.
func marshalDeterministic(result *rating.RatingsResult) ([]byte, error) {
	// Build an ordered representation.
	type ratingEntry struct {
		Elo     float64 `json:"elo"`
		Matches int     `json:"matches"`
		Wins    int     `json:"wins"`
		Draws   int     `json:"draws"`
	}

	// Use a custom structure that will produce sorted keys.
	ids := rating.SortedHypothesisIDs(result.Ratings)

	baseRating := result.BaseRating
	if baseRating == 0 {
		baseRating = rating.DefaultRating
	}

	// Build ordered map as a list of key-value pairs, then serialize manually.
	// We use json.Marshal on individual entries and assemble the object.
	var buf strings.Builder
	buf.WriteString("{\n")
	buf.WriteString("  \"epoch\": ")
	epochBytes, _ := json.Marshal(result.Epoch)
	buf.Write(epochBytes)
	buf.WriteString(",\n")
	buf.WriteString("  \"base_rating\": ")
	baseBytes, _ := json.Marshal(baseRating)
	buf.Write(baseBytes)
	buf.WriteString(",\n")
	buf.WriteString("  \"ratings\": {")

	for i, id := range ids {
		r := result.Ratings[id]
		entry := ratingEntry{
			Elo:     r.Elo,
			Matches: r.Matches,
			Wins:    r.Wins,
			Draws:   r.Draws,
		}
		entryBytes, err := json.Marshal(entry)
		if err != nil {
			return nil, err
		}
		if i > 0 {
			buf.WriteString(",")
		}
		buf.WriteString("\n    ")
		keyBytes, _ := json.Marshal(id)
		buf.Write(keyBytes)
		buf.WriteString(": ")
		buf.Write(entryBytes)
	}

	if len(ids) > 0 {
		buf.WriteString("\n  ")
	}
	buf.WriteString("},\n")
	buf.WriteString("  \"computed_from\": ")
	cfBytes, _ := json.Marshal(result.ComputedFrom)
	buf.Write(cfBytes)
	buf.WriteString("\n}\n")

	return []byte(buf.String()), nil
}
