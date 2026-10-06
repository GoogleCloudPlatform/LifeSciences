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

// Package lineage computes citation provenance deltas for evolved hypotheses.
//
// When a hypothesis evolves from parents (combine, ground, simplify, oob),
// the citation delta tracks which parent literature IDs were inherited,
// which were added new, and which were dropped. Each evolution operator
// implies a rule about what constitutes a compliant citation set.
package lineage

import "sort"

// CitationDelta records the relationship between a child hypothesis's
// evidence set and the union of its parents' evidence sets.
type CitationDelta struct {
	ParentLitIDs []string `json:"parent_lit_ids"`
	Inherited    []string `json:"inherited"`
	Added        []string `json:"added"`
	Dropped      []string `json:"dropped"`
	Rule         string   `json:"rule"`
	Compliant    bool     `json:"compliant"`
}

// OperatorRule maps an evolution operator to its citation rule.
//   - combine, ground → "superset" (child must include all parent citations)
//   - simplify → "subset" (child may only use parent citations, no new ones)
//   - oob → "free" (no constraints)
//   - anything else (including "null") → "skip" (no delta computed)
func OperatorRule(operator string) string {
	switch operator {
	case "combine", "ground":
		return "superset"
	case "simplify":
		return "subset"
	case "oob":
		return "free"
	default:
		return "skip"
	}
}

// ComputeCitationDelta computes the citation delta between a child's
// evidence set and the union of its parents' evidence sets.
//
// parentEvidenceSets contains the lit_id slices extracted from each parent's
// evidence array. childEvidenceSet contains the lit_ids from the child.
// All output arrays are sorted for deterministic output.
func ComputeCitationDelta(operator string, parentEvidenceSets [][]string, childEvidenceSet []string) CitationDelta {
	// 1. Union + deduplicate all parent lit_ids.
	parentSet := make(map[string]struct{})
	for _, pset := range parentEvidenceSets {
		for _, id := range pset {
			parentSet[id] = struct{}{}
		}
	}

	parentLitIDs := sortedKeys(parentSet)

	// Build child set for lookups.
	childSet := make(map[string]struct{}, len(childEvidenceSet))
	for _, id := range childEvidenceSet {
		childSet[id] = struct{}{}
	}

	// 2. Compute inherited = parent_union ∩ child_set.
	var inherited []string
	for id := range parentSet {
		if _, ok := childSet[id]; ok {
			inherited = append(inherited, id)
		}
	}

	// 3. Compute added = child_set − parent_union.
	var added []string
	for id := range childSet {
		if _, ok := parentSet[id]; !ok {
			added = append(added, id)
		}
	}

	// 4. Compute dropped = parent_union − child_set.
	var dropped []string
	for id := range parentSet {
		if _, ok := childSet[id]; !ok {
			dropped = append(dropped, id)
		}
	}

	// 5. Sort all arrays for deterministic output.
	sort.Strings(inherited)
	sort.Strings(added)
	sort.Strings(dropped)

	// 6. Apply rule per operator.
	rule := OperatorRule(operator)
	compliant := evaluateCompliance(rule, added, dropped)

	// Ensure non-nil slices for JSON serialization.
	if parentLitIDs == nil {
		parentLitIDs = []string{}
	}
	if inherited == nil {
		inherited = []string{}
	}
	if added == nil {
		added = []string{}
	}
	if dropped == nil {
		dropped = []string{}
	}

	return CitationDelta{
		ParentLitIDs: parentLitIDs,
		Inherited:    inherited,
		Added:        added,
		Dropped:      dropped,
		Rule:         rule,
		Compliant:    compliant,
	}
}

// evaluateCompliance checks whether the delta satisfies the given rule.
func evaluateCompliance(rule string, added, dropped []string) bool {
	switch rule {
	case "superset":
		// Compliant iff no parent citations were dropped.
		return len(dropped) == 0
	case "subset":
		// Compliant iff no new citations were added.
		return len(added) == 0
	case "free":
		// Always compliant.
		return true
	default:
		// "skip" — should not reach here in normal flow.
		return true
	}
}

// sortedKeys returns sorted keys from a string set.
func sortedKeys(m map[string]struct{}) []string {
	if len(m) == 0 {
		return nil
	}
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}
