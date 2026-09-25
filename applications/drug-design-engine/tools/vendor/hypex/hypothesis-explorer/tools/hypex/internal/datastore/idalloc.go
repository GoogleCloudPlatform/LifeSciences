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

package datastore

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"
)

// ArtifactType is a recognized artifact type for ID allocation.
type ArtifactType string

const (
	ArtifactHypothesis ArtifactType = "hypothesis"
	ArtifactReview     ArtifactType = "review"
	ArtifactMatch      ArtifactType = "match"
)

// ArtifactPrefix maps artifact types to their ID prefixes.
var ArtifactPrefix = map[ArtifactType]string{
	ArtifactHypothesis: "H",
	ArtifactReview:     "R",
	ArtifactMatch:      "M",
}

var hypothesisIDRegex = regexp.MustCompile(`^H-[0-9]{4}$`)

// ValidateHypothesisID validates that the hypothesis ID matches the schema convention ^H-[0-9]{4}$.
func ValidateHypothesisID(id string) error {
	if !hypothesisIDRegex.MatchString(id) {
		return fmt.Errorf("invalid hypothesis ID %q: must match ^H-[0-9]{4}$ (e.g. H-0001)", id)
	}
	return nil
}

// ParseArtifactType parses a string into an ArtifactType, returning an error
// if the string is not recognized.
func ParseArtifactType(s string) (ArtifactType, error) {
	switch strings.ToLower(s) {
	case "hypothesis", "h":
		return ArtifactHypothesis, nil
	case "review", "r":
		return ArtifactReview, nil
	case "match", "m":
		return ArtifactMatch, nil
	default:
		return "", fmt.Errorf("unknown artifact type %q (valid: hypothesis, review, match)", s)
	}
}

// counterFile returns the path to the counter file for a given artifact type
// within a run directory. The counter file stores the next ID to allocate.
func counterFile(runDir string, at ArtifactType) string {
	return filepath.Join(runDir, fmt.Sprintf(".counter-%s", at))
}

// reviewCounterFile returns the path to the counter file for reviews of a
// specific hypothesis within a run directory.
func reviewCounterFile(runDir string, hypothesisID string) string {
	return filepath.Join(runDir, fmt.Sprintf(".counter-review-%s", hypothesisID))
}

// lockFile returns the path to the lockfile used for ID allocation within a
// run directory. A single lockfile per run serializes all ID allocations.
func lockFile(runDir string) string {
	return filepath.Join(runDir, ".hypex.lock")
}

// incrementCounter atomically reads and increments the integer counter at
// counterPath, serialized by flock on the run lockfile.
func incrementCounter(runDir, counterPath string) (int, error) {
	// Open (or create) the lockfile.
	lf, err := os.OpenFile(lockFile(runDir), os.O_CREATE|os.O_RDWR, 0o644)
	if err != nil {
		return 0, fmt.Errorf("opening lockfile: %w", err)
	}
	defer lf.Close()

	// Acquire an exclusive filesystem lock. This blocks until the lock is
	// available, ensuring serialization across separate OS processes.
	if err := syscall.Flock(int(lf.Fd()), syscall.LOCK_EX); err != nil {
		return 0, fmt.Errorf("acquiring lock: %w", err)
	}
	defer syscall.Flock(int(lf.Fd()), syscall.LOCK_UN)

	// Read the current counter value.
	next := 1
	data, err := os.ReadFile(counterPath)
	if err == nil {
		val, parseErr := strconv.Atoi(strings.TrimSpace(string(data)))
		if parseErr != nil {
			return 0, fmt.Errorf("parsing counter file %s: %w", counterPath, parseErr)
		}
		next = val
	} else if !os.IsNotExist(err) {
		return 0, fmt.Errorf("reading counter file %s: %w", counterPath, err)
	}

	// Write the incremented counter atomically.
	if err := WriteFileAtomic(counterPath, []byte(strconv.Itoa(next+1))); err != nil {
		return 0, fmt.Errorf("writing counter file: %w", err)
	}

	return next, nil
}

// NextID atomically allocates and returns the next ID for the given artifact
// type within a run. It uses flock(2) on a per-run lockfile to ensure that
// concurrent callers (separate OS processes) never receive the same ID.
//
// For reviews, use NextReviewID with the target hypothesis ID instead.
// Returns the full ID string (e.g. "H-0042") and the numeric value.
func NextID(runDir string, at ArtifactType) (string, int, error) {
	if at == ArtifactReview {
		return "", 0, fmt.Errorf("review IDs require a hypothesis ID; use NextReviewID")
	}

	prefix, ok := ArtifactPrefix[at]
	if !ok {
		return "", 0, fmt.Errorf("unknown artifact type: %s", at)
	}

	next, err := incrementCounter(runDir, counterFile(runDir, at))
	if err != nil {
		return "", 0, err
	}

	id := fmt.Sprintf("%s-%04d", prefix, next)
	return id, next, nil
}

// NextReviewID atomically allocates and returns the next review ID for a given
// hypothesis within a run. It uses flock(2) on the per-run lockfile to ensure that
// concurrent callers (separate OS processes) never receive the same ID.
//
// The hypothesisID must match ^H-[0-9]{4}$.
// Returns the full review ID (e.g. "H-0042.R-01") and the numeric value.
func NextReviewID(runDir string, hypothesisID string) (string, int, error) {
	if err := ValidateHypothesisID(hypothesisID); err != nil {
		return "", 0, err
	}

	next, err := incrementCounter(runDir, reviewCounterFile(runDir, hypothesisID))
	if err != nil {
		return "", 0, err
	}

	id := fmt.Sprintf("%s.R-%02d", hypothesisID, next)
	return id, next, nil
}
