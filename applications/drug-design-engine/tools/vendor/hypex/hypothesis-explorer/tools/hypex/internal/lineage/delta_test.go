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

package lineage

import (
	"sort"
	"testing"
)

func TestOperatorRule(t *testing.T) {
	tests := []struct {
		operator string
		want     string
	}{
		{"combine", "superset"},
		{"ground", "superset"},
		{"simplify", "subset"},
		{"oob", "free"},
		{"null", "skip"},
		{"unknown", "skip"},
		{"", "skip"},
	}
	for _, tt := range tests {
		t.Run(tt.operator, func(t *testing.T) {
			got := OperatorRule(tt.operator)
			if got != tt.want {
				t.Errorf("OperatorRule(%q) = %q, want %q", tt.operator, got, tt.want)
			}
		})
	}
}

func TestComputeCitationDelta_Combine_Compliant(t *testing.T) {
	// Child is a superset of parents — inherits all, adds new.
	parentSets := [][]string{
		{"PMID:1001", "PMID:1002"},
		{"PMID:1002", "PMID:1003"},
	}
	childSet := []string{"PMID:1001", "PMID:1002", "PMID:1003", "PMID:2001"}

	delta := ComputeCitationDelta("combine", parentSets, childSet)

	if delta.Rule != "superset" {
		t.Errorf("Rule = %q, want %q", delta.Rule, "superset")
	}
	if !delta.Compliant {
		t.Error("expected compliant = true for superset child")
	}
	assertSorted(t, "parent_lit_ids", delta.ParentLitIDs)
	assertSorted(t, "inherited", delta.Inherited)
	assertSorted(t, "added", delta.Added)
	assertSorted(t, "dropped", delta.Dropped)

	assertSliceEqual(t, "parent_lit_ids", delta.ParentLitIDs, []string{"PMID:1001", "PMID:1002", "PMID:1003"})
	assertSliceEqual(t, "inherited", delta.Inherited, []string{"PMID:1001", "PMID:1002", "PMID:1003"})
	assertSliceEqual(t, "added", delta.Added, []string{"PMID:2001"})
	assertSliceEqual(t, "dropped", delta.Dropped, []string{})
}

func TestComputeCitationDelta_Combine_NonCompliant(t *testing.T) {
	// Child drops parent citations — non-compliant for combine.
	parentSets := [][]string{
		{"PMID:1001", "PMID:1002", "PMID:1003"},
	}
	childSet := []string{"PMID:1001", "PMID:2001"}

	delta := ComputeCitationDelta("combine", parentSets, childSet)

	if delta.Rule != "superset" {
		t.Errorf("Rule = %q, want %q", delta.Rule, "superset")
	}
	if delta.Compliant {
		t.Error("expected compliant = false when child drops parent citations")
	}
	assertSliceEqual(t, "inherited", delta.Inherited, []string{"PMID:1001"})
	assertSliceEqual(t, "added", delta.Added, []string{"PMID:2001"})
	assertSliceEqual(t, "dropped", delta.Dropped, []string{"PMID:1002", "PMID:1003"})
}

func TestComputeCitationDelta_Ground_Compliant(t *testing.T) {
	// Ground: child is superset with additions — compliant.
	parentSets := [][]string{
		{"PMID:1001"},
	}
	childSet := []string{"PMID:1001", "PMID:2001", "PMID:2002"}

	delta := ComputeCitationDelta("ground", parentSets, childSet)

	if delta.Rule != "superset" {
		t.Errorf("Rule = %q, want %q", delta.Rule, "superset")
	}
	if !delta.Compliant {
		t.Error("expected compliant = true")
	}
	assertSliceEqual(t, "inherited", delta.Inherited, []string{"PMID:1001"})
	assertSliceEqual(t, "added", delta.Added, []string{"PMID:2001", "PMID:2002"})
	assertSliceEqual(t, "dropped", delta.Dropped, []string{})
}

func TestComputeCitationDelta_Simplify_Compliant(t *testing.T) {
	// Simplify: child is a subset of parent — compliant.
	parentSets := [][]string{
		{"PMID:1001", "PMID:1002", "PMID:1003"},
	}
	childSet := []string{"PMID:1001", "PMID:1003"}

	delta := ComputeCitationDelta("simplify", parentSets, childSet)

	if delta.Rule != "subset" {
		t.Errorf("Rule = %q, want %q", delta.Rule, "subset")
	}
	if !delta.Compliant {
		t.Error("expected compliant = true for subset child")
	}
	assertSliceEqual(t, "inherited", delta.Inherited, []string{"PMID:1001", "PMID:1003"})
	assertSliceEqual(t, "added", delta.Added, []string{})
	assertSliceEqual(t, "dropped", delta.Dropped, []string{"PMID:1002"})
}

func TestComputeCitationDelta_Simplify_NonCompliant(t *testing.T) {
	// Simplify: child adds new citations — non-compliant.
	parentSets := [][]string{
		{"PMID:1001", "PMID:1002"},
	}
	childSet := []string{"PMID:1001", "PMID:3001"}

	delta := ComputeCitationDelta("simplify", parentSets, childSet)

	if delta.Rule != "subset" {
		t.Errorf("Rule = %q, want %q", delta.Rule, "subset")
	}
	if delta.Compliant {
		t.Error("expected compliant = false when simplify child adds citations")
	}
	assertSliceEqual(t, "added", delta.Added, []string{"PMID:3001"})
}

func TestComputeCitationDelta_OOB(t *testing.T) {
	// OOB: always compliant regardless of delta.
	parentSets := [][]string{
		{"PMID:1001", "PMID:1002"},
	}
	childSet := []string{"PMID:9999"}

	delta := ComputeCitationDelta("oob", parentSets, childSet)

	if delta.Rule != "free" {
		t.Errorf("Rule = %q, want %q", delta.Rule, "free")
	}
	if !delta.Compliant {
		t.Error("expected compliant = true for oob (free rule)")
	}
	assertSliceEqual(t, "dropped", delta.Dropped, []string{"PMID:1001", "PMID:1002"})
	assertSliceEqual(t, "added", delta.Added, []string{"PMID:9999"})
}

func TestComputeCitationDelta_EmptyParents(t *testing.T) {
	// No parent citations — child adds everything, nothing inherited.
	parentSets := [][]string{}
	childSet := []string{"PMID:1001", "PMID:1002"}

	delta := ComputeCitationDelta("combine", parentSets, childSet)

	assertSliceEqual(t, "parent_lit_ids", delta.ParentLitIDs, []string{})
	assertSliceEqual(t, "inherited", delta.Inherited, []string{})
	assertSliceEqual(t, "added", delta.Added, []string{"PMID:1001", "PMID:1002"})
	assertSliceEqual(t, "dropped", delta.Dropped, []string{})
	if !delta.Compliant {
		t.Error("expected compliant = true (nothing to drop)")
	}
}

func TestComputeCitationDelta_EmptyChild(t *testing.T) {
	// Child has no citations — all parent citations dropped.
	parentSets := [][]string{
		{"PMID:1001", "PMID:1002"},
	}
	childSet := []string{}

	delta := ComputeCitationDelta("combine", parentSets, childSet)

	assertSliceEqual(t, "inherited", delta.Inherited, []string{})
	assertSliceEqual(t, "added", delta.Added, []string{})
	assertSliceEqual(t, "dropped", delta.Dropped, []string{"PMID:1001", "PMID:1002"})
	if delta.Compliant {
		t.Error("expected compliant = false (all parent citations dropped)")
	}
}

func TestComputeCitationDelta_Deterministic(t *testing.T) {
	// Run multiple times and verify arrays are always sorted the same way.
	parentSets := [][]string{
		{"PMID:1003", "PMID:1001", "PMID:1002"},
		{"PMID:1005", "PMID:1004", "PMID:1001"},
	}
	childSet := []string{"PMID:1005", "PMID:1001", "PMID:2002", "PMID:2001"}

	for i := 0; i < 10; i++ {
		delta := ComputeCitationDelta("combine", parentSets, childSet)

		assertSorted(t, "parent_lit_ids", delta.ParentLitIDs)
		assertSorted(t, "inherited", delta.Inherited)
		assertSorted(t, "added", delta.Added)
		assertSorted(t, "dropped", delta.Dropped)

		// Verify exact values each time.
		assertSliceEqual(t, "parent_lit_ids", delta.ParentLitIDs,
			[]string{"PMID:1001", "PMID:1002", "PMID:1003", "PMID:1004", "PMID:1005"})
		assertSliceEqual(t, "inherited", delta.Inherited,
			[]string{"PMID:1001", "PMID:1005"})
		assertSliceEqual(t, "added", delta.Added,
			[]string{"PMID:2001", "PMID:2002"})
		assertSliceEqual(t, "dropped", delta.Dropped,
			[]string{"PMID:1002", "PMID:1003", "PMID:1004"})
	}
}

// --- helpers ---

func assertSorted(t *testing.T, name string, s []string) {
	t.Helper()
	if !sort.StringsAreSorted(s) {
		t.Errorf("%s is not sorted: %v", name, s)
	}
}

func assertSliceEqual(t *testing.T, name string, got, want []string) {
	t.Helper()
	if len(got) != len(want) {
		t.Errorf("%s: len = %d, want %d\n  got:  %v\n  want: %v", name, len(got), len(want), got, want)
		return
	}
	for i := range got {
		if got[i] != want[i] {
			t.Errorf("%s[%d] = %q, want %q", name, i, got[i], want[i])
		}
	}
}
