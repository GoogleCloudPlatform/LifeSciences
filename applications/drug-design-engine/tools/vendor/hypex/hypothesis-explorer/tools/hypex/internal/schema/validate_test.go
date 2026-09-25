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

package schema

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestMatchSchema_DrawAndWinnerValidation(t *testing.T) {
	schemaDir, err := filepath.Abs("../../../../schemas")
	if err != nil {
		t.Fatalf("resolving schema dir: %v", err)
	}

	validMatchA := []byte(`{
		"id": "M-0001",
		"epoch": 0,
		"a": "H-0001",
		"b": "H-0002",
		"format": "single-turn",
		"winner": "H-0001",
		"margin": "decisive",
		"criterion_scores": {
			"novelty": {"a": 4, "b": 3},
			"plausibility": {"a": 5, "b": 4},
			"testability": {"a": 3, "b": 4}
		},
		"rationale": "Clear winner.",
		"judge": "agent-1",
		"created_at": "2026-09-06T12:00:00Z"
	}`)

	validMatchDraw := []byte(`{
		"id": "M-0002",
		"epoch": 0,
		"a": "H-0001",
		"b": "H-0002",
		"format": "single-turn",
		"winner": "draw",
		"margin": "narrow",
		"criterion_scores": {
			"novelty": {"a": 4, "b": 4},
			"plausibility": {"a": 4, "b": 4},
			"testability": {"a": 3, "b": 3}
		},
		"rationale": "Evenly matched.",
		"judge": "agent-1",
		"created_at": "2026-09-06T12:05:00Z"
	}`)

	invalidWinner := []byte(`{
		"id": "M-0003",
		"epoch": 0,
		"a": "H-0001",
		"b": "H-0002",
		"format": "single-turn",
		"winner": "draww",
		"margin": "narrow",
		"criterion_scores": {
			"novelty": {"a": 4, "b": 4},
			"plausibility": {"a": 4, "b": 4},
			"testability": {"a": 3, "b": 3}
		},
		"rationale": "Invalid winner typo.",
		"judge": "agent-1",
		"created_at": "2026-09-06T12:05:00Z"
	}`)

	if err := ValidateBytes(schemaDir, "match.schema.json", validMatchA); err != nil {
		t.Errorf("expected validMatchA to pass: %v", err)
	}

	if err := ValidateBytes(schemaDir, "match.schema.json", validMatchDraw); err != nil {
		t.Errorf("expected validMatchDraw to pass: %v", err)
	}

	if err := ValidateBytes(schemaDir, "match.schema.json", invalidWinner); err == nil {
		t.Errorf("expected invalidWinner to fail validation")
	}
}

func TestRatingsSchema_DrawsValidation(t *testing.T) {
	schemaDir, err := filepath.Abs("../../../../schemas")
	if err != nil {
		t.Fatalf("resolving schema dir: %v", err)
	}

	validRatings := []byte(`{
		"epoch": 0,
		"base_rating": 1500,
		"ratings": {
			"H-0001": {
				"elo": 1520.0,
				"matches": 2,
				"wins": 1,
				"draws": 1
			}
		},
		"computed_from": "matches/ ledger @ M-0002"
	}`)

	invalidRatingsMissingDraws := []byte(`{
		"epoch": 0,
		"base_rating": 1500,
		"ratings": {
			"H-0001": {
				"elo": 1520.0,
				"matches": 2,
				"wins": 1
			}
		},
		"computed_from": "matches/ ledger @ M-0002"
	}`)

	if err := ValidateBytes(schemaDir, "ratings.schema.json", validRatings); err != nil {
		t.Errorf("expected validRatings to pass: %v", err)
	}

	if err := ValidateBytes(schemaDir, "ratings.schema.json", invalidRatingsMissingDraws); err == nil {
		t.Errorf("expected invalidRatingsMissingDraws to fail validation")
	}
}

func findSchemaDir(t *testing.T) string {
	t.Helper()
	candidates := []string{
		filepath.Join("..", "..", "..", "..", "schemas"),
		filepath.Join("..", "..", "..", "schemas"),
		filepath.Join("hypothesis-explorer", "schemas"),
	}
	for _, c := range candidates {
		if _, err := os.Stat(filepath.Join(c, "review.schema.json")); err == nil {
			abs, err := filepath.Abs(c)
			if err == nil {
				return abs
			}
			return c
		}
	}
	t.Fatal("could not locate schemas directory")
	return ""
}

func baseReviewMap() map[string]any {
	return map[string]any{
		"id":            "H-0042.R-01",
		"hypothesis_id": "H-0042",
		"review_type":   "full",
		"scores": map[string]any{
			"correctness": 4,
			"novelty":     3,
			"testability": 5,
			"safety":      5,
		},
		"verdict":                "Strong hypothesis with clear experimental path.",
		"key_criticisms":         []string{"Citation is correlational, not causal"},
		"verified_citations":     []string{"PMID:38012345"},
		"contradicting_evidence": []string{},
		"reviewer":               "reflection-agent-1",
		"epoch":                  0,
	}
}

func validRecurrentNotes() map[string]any {
	return map[string]any{
		"parent_id":              "H-0003",
		"operator":               "ground",
		"criticisms_addressed":   []string{"Addressed weak evidence with new citations"},
		"criticisms_unaddressed": []string{},
		"new_weaknesses":         []string{"New citation is a preprint"},
		"net_quality_delta":      "improved",
	}
}

func TestSchemaForFile(t *testing.T) {
	tests := []struct {
		filename string
		want     string
		wantErr  bool
	}{
		{"H-0001.json", "hypothesis.schema.json", false},
		{"H-0042.R-01.json", "review.schema.json", false},
		{"M-0001.json", "match.schema.json", false},
		{"epoch-0.json", "ratings.schema.json", false},
		{"citations/H-0001.json", "citation-manifest.schema.json", false},
		{"/path/to/run/citations/H-0001.json", "citation-manifest.schema.json", false},
		{"/scion-volumes/executions/lupus-therapeutics-02/citations/H-0001.json", "citation-manifest.schema.json", false},
		{"citations.json", "citation-manifest.schema.json", false},
		{"report/citations.json", "citation-manifest.schema.json", false},
		{"/path/to/run/report/citations.json", "citation-manifest.schema.json", false},
		{"unknown.txt", "", true},
		{"random.json", "", true},
	}

	for _, tt := range tests {
		t.Run(tt.filename, func(t *testing.T) {
			got, err := SchemaForFile(tt.filename)
			if (err != nil) != tt.wantErr {
				t.Fatalf("SchemaForFile(%q) error = %v, wantErr %v", tt.filename, err, tt.wantErr)
			}
			if got != tt.want {
				t.Errorf("SchemaForFile(%q) = %q, want %q", tt.filename, got, tt.want)
			}
		})
	}
}

func TestValidate_StandardReview(t *testing.T) {
	schemaDir := findSchemaDir(t)
	review := baseReviewMap()

	data, err := json.Marshal(review)
	if err != nil {
		t.Fatal(err)
	}

	if err := ValidateBytes(schemaDir, "review.schema.json", data); err != nil {
		t.Errorf("expected standard review to be valid, got: %v", err)
	}
}

func TestValidate_RecurrentReview_Valid(t *testing.T) {
	schemaDir := findSchemaDir(t)

	operators := []string{"combine", "ground", "simplify", "oob"}
	deltas := []string{"improved", "unchanged", "degraded"}

	for _, op := range operators {
		for _, delta := range deltas {
			t.Run(op+"_"+delta, func(t *testing.T) {
				review := baseReviewMap()
				rn := validRecurrentNotes()
				rn["operator"] = op
				rn["net_quality_delta"] = delta
				review["recurrent_notes"] = rn

				data, err := json.Marshal(review)
				if err != nil {
					t.Fatal(err)
				}

				if err := ValidateBytes(schemaDir, "review.schema.json", data); err != nil {
					t.Errorf("expected recurrent review with op=%s delta=%s to be valid, got: %v", op, delta, err)
				}
			})
		}
	}
}

func TestValidate_RecurrentReview_Invalid(t *testing.T) {
	schemaDir := findSchemaDir(t)

	tests := []struct {
		name      string
		modifyFn  func(rn map[string]any)
		wantError string
	}{
		{
			name: "invalid parent_id format",
			modifyFn: func(rn map[string]any) {
				rn["parent_id"] = "H-12"
			},
		},
		{
			name: "invalid parent_id prefix",
			modifyFn: func(rn map[string]any) {
				rn["parent_id"] = "X-0001"
			},
		},
		{
			name: "invalid operator",
			modifyFn: func(rn map[string]any) {
				rn["operator"] = "mutate"
			},
		},
		{
			name: "null operator not allowed in recurrent_notes",
			modifyFn: func(rn map[string]any) {
				rn["operator"] = "null"
			},
		},
		{
			name: "invalid net_quality_delta",
			modifyFn: func(rn map[string]any) {
				rn["net_quality_delta"] = "regressed"
			},
		},
		{
			name: "missing parent_id",
			modifyFn: func(rn map[string]any) {
				delete(rn, "parent_id")
			},
		},
		{
			name: "missing operator",
			modifyFn: func(rn map[string]any) {
				delete(rn, "operator")
			},
		},
		{
			name: "missing criticisms_addressed",
			modifyFn: func(rn map[string]any) {
				delete(rn, "criticisms_addressed")
			},
		},
		{
			name: "missing criticisms_unaddressed",
			modifyFn: func(rn map[string]any) {
				delete(rn, "criticisms_unaddressed")
			},
		},
		{
			name: "missing new_weaknesses",
			modifyFn: func(rn map[string]any) {
				delete(rn, "new_weaknesses")
			},
		},
		{
			name: "missing net_quality_delta",
			modifyFn: func(rn map[string]any) {
				delete(rn, "net_quality_delta")
			},
		},
		{
			name: "extra field in recurrent_notes",
			modifyFn: func(rn map[string]any) {
				rn["extra_field"] = "not_allowed"
			},
		},
		{
			name: "criticisms_addressed wrong type",
			modifyFn: func(rn map[string]any) {
				rn["criticisms_addressed"] = "should be array"
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			review := baseReviewMap()
			rn := validRecurrentNotes()
			tt.modifyFn(rn)
			review["recurrent_notes"] = rn

			data, err := json.Marshal(review)
			if err != nil {
				t.Fatal(err)
			}

			if err := ValidateBytes(schemaDir, "review.schema.json", data); err == nil {
				t.Errorf("expected validation error for %s, but got nil", tt.name)
			}
		})
	}
}

func TestValidate_Review_GoalAlignmentAndConstraintCompliance(t *testing.T) {
	schemaDir := findSchemaDir(t)

	// Baseline review without the new axes must pass.
	t.Run("baseline legacy review without new axes passes", func(t *testing.T) {
		review := baseReviewMap()
		data, err := json.Marshal(review)
		if err != nil {
			t.Fatal(err)
		}
		if err := ValidateBytes(schemaDir, "review.schema.json", data); err != nil {
			t.Errorf("expected baseline review without new axes to pass, got: %v", err)
		}
	})

	// Review with goal_alignment (1-5) passes.
	for score := 1; score <= 5; score++ {
		t.Run(fmt.Sprintf("goal_alignment=%d passes", score), func(t *testing.T) {
			review := baseReviewMap()
			scores := review["scores"].(map[string]any)
			scores["goal_alignment"] = score
			data, err := json.Marshal(review)
			if err != nil {
				t.Fatal(err)
			}
			if err := ValidateBytes(schemaDir, "review.schema.json", data); err != nil {
				t.Errorf("expected goal_alignment=%d to pass, got: %v", score, err)
			}
		})
	}

	// Review with constraint_compliance (1-5) passes.
	for score := 1; score <= 5; score++ {
		t.Run(fmt.Sprintf("constraint_compliance=%d passes", score), func(t *testing.T) {
			review := baseReviewMap()
			scores := review["scores"].(map[string]any)
			scores["constraint_compliance"] = score
			data, err := json.Marshal(review)
			if err != nil {
				t.Fatal(err)
			}
			if err := ValidateBytes(schemaDir, "review.schema.json", data); err != nil {
				t.Errorf("expected constraint_compliance=%d to pass, got: %v", score, err)
			}
		})
	}

	// Review with both new axes passes.
	t.Run("both new axes pass", func(t *testing.T) {
		review := baseReviewMap()
		scores := review["scores"].(map[string]any)
		scores["goal_alignment"] = 5
		scores["constraint_compliance"] = 4
		data, err := json.Marshal(review)
		if err != nil {
			t.Fatal(err)
		}
		if err := ValidateBytes(schemaDir, "review.schema.json", data); err != nil {
			t.Errorf("expected review with both new axes to pass, got: %v", err)
		}
	})

	// Scores outside 1-5 (e.g. 0, 6, float 3.5) fail validation.
	invalidScores := []struct {
		name  string
		field string
		val   any
	}{
		{"goal_alignment 0 (too low)", "goal_alignment", 0},
		{"goal_alignment 6 (too high)", "goal_alignment", 6},
		{"goal_alignment -1 (negative)", "goal_alignment", -1},
		{"goal_alignment float 3.5", "goal_alignment", 3.5},
		{"goal_alignment string \"3\"", "goal_alignment", "3"},
		{"constraint_compliance 0 (too low)", "constraint_compliance", 0},
		{"constraint_compliance 6 (too high)", "constraint_compliance", 6},
		{"constraint_compliance -1 (negative)", "constraint_compliance", -1},
		{"constraint_compliance float 3.5", "constraint_compliance", 3.5},
		{"constraint_compliance string \"3\"", "constraint_compliance", "3"},
	}

	for _, tt := range invalidScores {
		t.Run(tt.name, func(t *testing.T) {
			review := baseReviewMap()
			scores := review["scores"].(map[string]any)
			scores[tt.field] = tt.val
			data, err := json.Marshal(review)
			if err != nil {
				t.Fatal(err)
			}
			if err := ValidateBytes(schemaDir, "review.schema.json", data); err == nil {
				t.Errorf("expected %s to fail validation, but it passed", tt.name)
			}
		})
	}

	// Unrecognized axis in scores (e.g. feasibility) fails validation via additionalProperties: false.
	t.Run("unrecognized axis in scores fails via additionalProperties false", func(t *testing.T) {
		review := baseReviewMap()
		scores := review["scores"].(map[string]any)
		scores["feasibility"] = 4
		data, err := json.Marshal(review)
		if err != nil {
			t.Fatal(err)
		}
		if err := ValidateBytes(schemaDir, "review.schema.json", data); err == nil {
			t.Errorf("expected unrecognized axis 'feasibility' in scores to fail validation, but it passed")
		}
	})
}

func TestValidate_ExistingExecutionReviews(t *testing.T) {
	schemaDir := findSchemaDir(t)

	runDirs := []string{
		"/scion-volumes/executions/lupus-therapeutics-01/reviews",
		"/scion-volumes/executions/lupus-therapeutics-02/reviews",
	}

	for _, dir := range runDirs {
		t.Run(filepath.Base(filepath.Dir(dir)), func(t *testing.T) {
			entries, err := os.ReadDir(dir)
			if err != nil {
				if os.IsNotExist(err) {
					t.Skipf("reviews directory %s not present in this environment", dir)
				}
				t.Fatalf("reading reviews dir %s: %v", dir, err)
			}

			count := 0
			for _, e := range entries {
				if e.IsDir() || !strings.HasSuffix(e.Name(), ".json") {
					continue
				}
				filePath := filepath.Join(dir, e.Name())
				if err := ValidateFile(schemaDir, filePath); err != nil {
					t.Errorf("existing review %s failed validation: %v", filePath, err)
				}
				count++
			}

			if count == 0 {
				t.Errorf("expected to find reviews in %s, found none", dir)
			}
		})
	}
}

func TestCitationManifestSchema(t *testing.T) {
	schemaDir, err := filepath.Abs("../../../../schemas")
	if err != nil {
		t.Fatalf("resolving schema dir: %v", err)
	}

	validManifest := []byte(`{
		"target_id": "H-0001",
		"target_file": "/scion-volumes/executions/run-01/hypotheses/H-0001.json",
		"verified_at": "2026-09-06T12:00:00Z",
		"verifier": "lit-verify/1.0",
		"summary": {
			"total": 1,
			"verified": 1,
			"phantom": 0,
			"unverified": 0,
			"all_verified": true
		},
		"citations": [
			{
				"id": "PMID:42297600",
				"raw_id": "PMID:42297600",
				"source": "pubmed",
				"status": "verified",
				"reason": "ok",
				"claimed_title": "Efficacy and safety of CAR-T cells in refractory SLE",
				"resolved_title": "Efficacy and safety of CAR-T cells in refractory SLE",
				"title_match": "matched",
				"title_similarity": 1.0,
				"verified_via": "ncbi",
				"url": "https://pubmed.ncbi.nlm.nih.gov/42297600/"
			}
		]
	}`)

	if err := ValidateBytes(schemaDir, "citation-manifest.schema.json", validManifest); err != nil {
		t.Fatalf("expected valid manifest to pass schema validation: %v", err)
	}

	invalidManifest := []byte(`{
		"target_id": "H-0001",
		"target_file": "H-0001.json",
		"verified_at": "2026-09-06T12:00:00Z",
		"verifier": "lit-verify/1.0",
		"summary": {
			"total": 1
		},
		"citations": []
	}`)

	if err := ValidateBytes(schemaDir, "citation-manifest.schema.json", invalidManifest); err == nil {
		t.Errorf("expected invalid manifest to fail schema validation, but got nil")
	}
}

func TestValidate_Review_CitationVerificationFields(t *testing.T) {
	schemaDir := findSchemaDir(t)

	// 1. Legacy review without phantom_citations or citation_manifest passes.
	t.Run("legacy review without phantom_citations or citation_manifest passes", func(t *testing.T) {
		review := baseReviewMap()
		data, err := json.Marshal(review)
		if err != nil {
			t.Fatal(err)
		}
		if err := ValidateBytes(schemaDir, "review.schema.json", data); err != nil {
			t.Errorf("expected legacy review to pass, got: %v", err)
		}
	})

	// 2. Review with both phantom_citations and citation_manifest passes.
	t.Run("review with phantom_citations and citation_manifest passes", func(t *testing.T) {
		review := baseReviewMap()
		review["phantom_citations"] = []string{"PMID:99999999", "DOI:10.1234/fabricated"}
		review["citation_manifest"] = "citations/H-0042.json"
		data, err := json.Marshal(review)
		if err != nil {
			t.Fatal(err)
		}
		if err := ValidateBytes(schemaDir, "review.schema.json", data); err != nil {
			t.Errorf("expected review with citations verification fields to pass, got: %v", err)
		}
	})

	// 3. Review with empty phantom_citations array passes.
	t.Run("review with empty phantom_citations array passes", func(t *testing.T) {
		review := baseReviewMap()
		review["phantom_citations"] = []string{}
		review["citation_manifest"] = "citations/H-0042.json"
		data, err := json.Marshal(review)
		if err != nil {
			t.Fatal(err)
		}
		if err := ValidateBytes(schemaDir, "review.schema.json", data); err != nil {
			t.Errorf("expected review with empty phantom_citations to pass, got: %v", err)
		}
	})

	// 4. Review with only phantom_citations passes.
	t.Run("review with only phantom_citations passes", func(t *testing.T) {
		review := baseReviewMap()
		review["phantom_citations"] = []string{"PMID:99999999"}
		data, err := json.Marshal(review)
		if err != nil {
			t.Fatal(err)
		}
		if err := ValidateBytes(schemaDir, "review.schema.json", data); err != nil {
			t.Errorf("expected review with only phantom_citations to pass, got: %v", err)
		}
	})

	// 5. Review with only citation_manifest passes.
	t.Run("review with only citation_manifest passes", func(t *testing.T) {
		review := baseReviewMap()
		review["citation_manifest"] = "citations/H-0042.json"
		data, err := json.Marshal(review)
		if err != nil {
			t.Fatal(err)
		}
		if err := ValidateBytes(schemaDir, "review.schema.json", data); err != nil {
			t.Errorf("expected review with only citation_manifest to pass, got: %v", err)
		}
	})

	// 6. Invalid types fail validation.
	invalidCases := []struct {
		name     string
		modifyFn func(r map[string]any)
	}{
		{
			name: "phantom_citations is string instead of array",
			modifyFn: func(r map[string]any) {
				r["phantom_citations"] = "PMID:99999999"
			},
		},
		{
			name: "phantom_citations is number instead of array",
			modifyFn: func(r map[string]any) {
				r["phantom_citations"] = 12345
			},
		},
		{
			name: "phantom_citations array contains non-string integer",
			modifyFn: func(r map[string]any) {
				r["phantom_citations"] = []any{12345}
			},
		},
		{
			name: "phantom_citations array contains non-string boolean",
			modifyFn: func(r map[string]any) {
				r["phantom_citations"] = []any{true}
			},
		},
		{
			name: "citation_manifest is integer instead of string",
			modifyFn: func(r map[string]any) {
				r["citation_manifest"] = 12345
			},
		},
		{
			name: "citation_manifest is boolean instead of string",
			modifyFn: func(r map[string]any) {
				r["citation_manifest"] = true
			},
		},
		{
			name: "citation_manifest is array instead of string",
			modifyFn: func(r map[string]any) {
				r["citation_manifest"] = []string{"citations/H-0042.json"}
			},
		},
		{
			name: "citation_manifest is object instead of string",
			modifyFn: func(r map[string]any) {
				r["citation_manifest"] = map[string]any{"path": "citations/H-0042.json"}
			},
		},
	}

	for _, tc := range invalidCases {
		t.Run(tc.name, func(t *testing.T) {
			review := baseReviewMap()
			tc.modifyFn(review)
			data, err := json.Marshal(review)
			if err != nil {
				t.Fatal(err)
			}
			if err := ValidateBytes(schemaDir, "review.schema.json", data); err == nil {
				t.Errorf("expected validation error for %s, but got nil", tc.name)
			}
		})
	}
}

func baseRatingsMap() map[string]any {
	return map[string]any{
		"epoch":       0,
		"base_rating": 1500,
		"ratings": map[string]any{
			"H-0001": map[string]any{
				"elo":     1520.0,
				"matches": 1,
				"wins":    1,
				"draws":   0,
			},
		},
		"computed_from": "matches/ ledger @ M-0001",
	}
}

func TestValidate_Ratings_Valid(t *testing.T) {
	schemaDir := findSchemaDir(t)
	ratings := baseRatingsMap()

	data, err := json.Marshal(ratings)
	if err != nil {
		t.Fatal(err)
	}

	if err := ValidateBytes(schemaDir, "ratings.schema.json", data); err != nil {
		t.Errorf("expected standard ratings to be valid, got: %v", err)
	}

	// Also verify ValidateFile correctly matches epoch-0.json and validates.
	tmpDir := t.TempDir()
	ratingsFile := filepath.Join(tmpDir, "epoch-0.json")
	if err := os.WriteFile(ratingsFile, data, 0o644); err != nil {
		t.Fatal(err)
	}

	if err := ValidateFile(schemaDir, ratingsFile); err != nil {
		t.Errorf("expected ValidateFile on %s to succeed, got: %v", ratingsFile, err)
	}
}

func TestValidate_Ratings_Invalid(t *testing.T) {
	schemaDir := findSchemaDir(t)

	tests := []struct {
		name     string
		modifyFn func(r map[string]any)
	}{
		{
			name: "missing base_rating",
			modifyFn: func(r map[string]any) {
				delete(r, "base_rating")
			},
		},
		{
			name: "base_rating wrong type",
			modifyFn: func(r map[string]any) {
				r["base_rating"] = "1500"
			},
		},
		{
			name: "base_rating negative",
			modifyFn: func(r map[string]any) {
				r["base_rating"] = -100
			},
		},
		{
			name: "missing epoch",
			modifyFn: func(r map[string]any) {
				delete(r, "epoch")
			},
		},
		{
			name: "missing ratings",
			modifyFn: func(r map[string]any) {
				delete(r, "ratings")
			},
		},
		{
			name: "missing computed_from",
			modifyFn: func(r map[string]any) {
				delete(r, "computed_from")
			},
		},
		{
			name: "extra field not allowed",
			modifyFn: func(r map[string]any) {
				r["extra_field"] = "unexpected"
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			r := baseRatingsMap()
			tt.modifyFn(r)

			data, err := json.Marshal(r)
			if err != nil {
				t.Fatal(err)
			}

			if err := ValidateBytes(schemaDir, "ratings.schema.json", data); err == nil {
				t.Errorf("expected validation error for %s, but got nil", tt.name)
			}
		})
	}
}
