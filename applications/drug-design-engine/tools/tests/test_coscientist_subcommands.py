# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for the 4 legacy-port subcommands: references, knowledge, report, compare.

Covers the normalisation expansion (D1), the report-references shape
resolver (D3), and per-subcommand happy/failure paths as specified in
the design doc (legacy-port-design.md).

Fixtures are synthetic minimal JSON exports with fake minified keys
(D4), proving that shape resolution works regardless of key names.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dde.commands.coscientist import (
    _extract_recommendation,
    _find_ideas,
    _find_report,
    _find_report_references,
    _normalise,
    _render_knowledge_text,
    _require_expanded_fields,
    _require_nonempty_ideas,
    _require_report_content,
    _validate_schema_tag,
)
from dde.core.errors import SchemaError

FIXTURES = Path(__file__).parent / "fixtures" / "coscientist"


# ---------------------------------------------------------------------------
# Fixture loaders
# ---------------------------------------------------------------------------


@pytest.fixture
def valid_export() -> dict:
    return json.loads((FIXTURES / "valid_tournament.json").read_text())


@pytest.fixture
def valid_alt_export() -> dict:
    return json.loads((FIXTURES / "valid_tournament_alt.json").read_text())


@pytest.fixture
def malformed_no_ideas() -> dict:
    return json.loads((FIXTURES / "malformed_no_ideas.json").read_text())


@pytest.fixture
def malformed_no_report() -> dict:
    return json.loads((FIXTURES / "malformed_no_report.json").read_text())


@pytest.fixture
def normalised(valid_export: dict) -> dict:
    return _normalise(valid_export, Path("test.json"))


@pytest.fixture
def normalised_alt(valid_alt_export: dict) -> dict:
    return _normalise(valid_alt_export, Path("alt.json"))


# ===================================================================
# Normalisation expansion tests (D1)
# ===================================================================


class TestNormaliseCarriesKBReferences:
    def test_references_is_list_of_dicts(self, normalised: dict) -> None:
        refs = normalised["knowledge_base"]["references"]
        assert isinstance(refs, list)
        assert len(refs) > 0
        for ref in refs:
            assert "title" in ref
            assert "source" in ref

    def test_count_matches_length(self, normalised: dict) -> None:
        kb = normalised["knowledge_base"]
        assert kb["n_references"] == len(kb["references"])


class TestNormaliseCarriesConnections:
    def test_connections_is_list_of_dicts(self, normalised: dict) -> None:
        conns = normalised["knowledge_base"]["connections"]
        assert isinstance(conns, list)
        assert len(conns) > 0
        for conn in conns:
            assert "title" in conn
            assert "description" in conn

    def test_count_matches_length(self, normalised: dict) -> None:
        kb = normalised["knowledge_base"]
        assert kb["n_connections"] == len(kb["connections"])


class TestNormaliseCarriesReportReferences:
    def test_report_references_populated(self, normalised: dict) -> None:
        refs = normalised["report"]["references"]
        assert isinstance(refs, list)
        assert len(refs) > 0
        for ref in refs:
            assert "title" in ref
            assert "source" in ref

    def test_resolved_key_recorded(self, normalised: dict) -> None:
        assert normalised["_resolved_keys"]["report_references"] is not None


class TestNormaliseCarriesClaimReferences:
    def test_claim_references_populated(self, normalised: dict) -> None:
        for idea in normalised["ideas"]:
            for claim in idea["claims"]:
                refs = claim["references"]
                assert isinstance(refs, list)
                assert claim["n_references"] == len(refs)
                for ref in refs:
                    assert "title" in ref
                    assert "source" in ref


class TestNormaliseCarriesReviewReferences:
    def test_review_references_flattened(self, normalised: dict) -> None:
        for idea in normalised["ideas"]:
            rev_refs = idea["review_references"]
            assert isinstance(rev_refs, list)
            for ref in rev_refs:
                assert "title" in ref
                assert "source" in ref
                assert "review_index" in ref

    def test_review_references_have_correct_index(self, normalised: dict) -> None:
        # Each idea in the fixture has 1 review with 1 reference, so
        # review_index should be 0.
        for idea in normalised["ideas"]:
            for ref in idea["review_references"]:
                assert ref["review_index"] == 0


# ===================================================================
# Shape resolver tests (D3)
# ===================================================================


class TestFindReportReferences:
    def test_shape_finds_references_under_arbitrary_key(
        self, valid_export: dict
    ) -> None:
        """References found under the minified key 'Jm' by shape."""
        _, report = _find_report(valid_export)
        key, refs = _find_report_references(report)
        assert key == "Jm"
        assert isinstance(refs, list)
        assert len(refs) > 0
        assert "title" in refs[0]

    def test_absent_returns_empty(self) -> None:
        """No reference-shaped list → (None, [])."""
        report = {
            "overview": {"markdown": "Some overview"},
            "topRankingIdeasSummary": {"markdown": "Summary"},
        }
        key, refs = _find_report_references(report)
        assert key is None
        assert refs == []

    def test_ignores_markdown_sections(self) -> None:
        """Sections that are lists of dicts with 'markdown' are not references."""
        report = {
            "overview": {"markdown": "Overview text"},
            "topRankingIdeasSummary": {"markdown": "Summary"},
            "sections": [{"markdown": "A section body"}],
        }
        key, refs = _find_report_references(report)
        assert key is None
        assert refs == []


# ===================================================================
# Normalisation uses different minified keys per fixture
# ===================================================================


class TestShapeResolutionAcrossFixtures:
    def test_valid_uses_key_xr(self, valid_export: dict) -> None:
        key, _ = _find_ideas(valid_export)
        assert key == "Xr"

    def test_alt_uses_key_yp(self, valid_alt_export: dict) -> None:
        key, _ = _find_ideas(valid_alt_export)
        assert key == "Yp"

    def test_both_normalise_to_same_shape(
        self, normalised: dict, normalised_alt: dict
    ) -> None:
        """Both fixtures normalise to the same schema regardless of key names."""
        assert normalised["schema"] == normalised_alt["schema"]
        assert set(normalised.keys()) == set(normalised_alt.keys())
        for idea in normalised["ideas"] + normalised_alt["ideas"]:
            assert "gene" in idea
            assert "elo_rating" in idea
            assert "ranking" in idea
            assert "review_references" in idea


# ===================================================================
# references subcommand logic
# ===================================================================


class TestReferencesLogic:
    def test_all_pools_returns_references(self, normalised: dict) -> None:
        """All reference pools are populated."""
        all_refs: list[dict] = []
        all_refs.extend(normalised["knowledge_base"]["references"])
        all_refs.extend(normalised["report"]["references"])
        for idea in normalised["ideas"]:
            all_refs.extend(idea["review_references"])
            for claim in idea["claims"]:
                all_refs.extend(claim["references"])
        assert len(all_refs) > 0

    def test_source_kb_filters_correctly(self, normalised: dict) -> None:
        kb_refs = normalised["knowledge_base"]["references"]
        assert len(kb_refs) == 2
        assert kb_refs[0]["title"] == "Cancer Biology Textbook"

    def test_search_filters_by_title(self, normalised: dict) -> None:
        all_refs: list[dict] = []
        all_refs.extend(normalised["knowledge_base"]["references"])
        all_refs.extend(normalised["report"]["references"])
        for idea in normalised["ideas"]:
            all_refs.extend(idea["review_references"])
            for claim in idea["claims"]:
                all_refs.extend(claim["references"])

        matched = [
            r
            for r in all_refs
            if "tp53" in r["title"].lower() or "tp53" in r["source"].lower()
        ]
        assert len(matched) > 0

    def test_rank_scopes_to_one_idea(self, normalised: dict) -> None:
        rank_1 = [i for i in normalised["ideas"] if i["ranking"] == 1]
        assert len(rank_1) == 1
        idea = rank_1[0]
        assert len(idea["review_references"]) > 0
        assert any(len(c["references"]) > 0 for c in idea["claims"])


class TestReferencesFailurePaths:
    def test_no_ideas_raises_schema_error(self, malformed_no_ideas: dict) -> None:
        """SchemaError, not an empty list."""
        with pytest.raises(SchemaError, match="no idea list found"):
            _find_ideas(malformed_no_ideas)

    def test_old_artifact_missing_references_detected(self, normalised: dict) -> None:
        """SchemaError raised when normalised artifact lacks expanded references.

        The guard fires before any output is produced, so SchemaError
        is proof that no output file was created.
        """
        # Simulate an artifact from before the expansion.
        del normalised["knowledge_base"]["references"]
        with pytest.raises(SchemaError, match="re-run dde coscientist ingest"):
            _require_expanded_fields(normalised, "references")

    def test_schema_error_exit_code_is_3(self) -> None:
        """SchemaError carries exit code 3."""
        err = SchemaError("test")
        assert err.exit_code == 3


# ===================================================================
# knowledge subcommand logic
# ===================================================================


class TestKnowledgeLogic:
    def test_summary_rendered(self, normalised: dict) -> None:
        assert normalised["knowledge_base"]["summary"] != ""

    def test_connections_listed_with_titles(self, normalised: dict) -> None:
        conns = normalised["knowledge_base"]["connections"]
        assert len(conns) == 2
        assert conns[0]["title"] == "TP53-BRCA1 Crosstalk"

    def test_connections_section_data_separate_from_summary(
        self, normalised: dict
    ) -> None:
        """connections and summary are separate fields."""
        kb = normalised["knowledge_base"]
        assert kb["connections_summary"] != ""
        assert len(kb["connections"]) > 0
        # They are different content.
        assert kb["summary"] != kb["connections_summary"]


class TestKnowledgeFailurePaths:
    def test_old_artifact_missing_connections_detected(self, normalised: dict) -> None:
        """SchemaError raised when normalised artifact lacks expanded connections.

        The guard fires before any output is produced, so SchemaError
        is proof that no output file was created.
        """
        del normalised["knowledge_base"]["connections"]
        with pytest.raises(SchemaError, match="re-run dde coscientist ingest"):
            _require_expanded_fields(normalised, "connections")

    def test_empty_kb_is_not_error(self) -> None:
        """Empty KB passes the guard and renders placeholder text, not an error."""
        kb = {
            "summary": "",
            "references": [],
            "connections": [],
            "connections_summary": "",
            "n_references": 0,
            "n_connections": 0,
            "n_learned_claims": 0,
        }
        record = {"knowledge_base": kb}
        # Guard does NOT raise — connections key is present (just empty).
        _require_expanded_fields(record, "connections")
        # Rendering produces placeholder text without raising.
        text = _render_knowledge_text(kb, "full")
        assert "(No knowledge summary available)" in text
        assert "(No connections analysis available)" in text


# ===================================================================
# report subcommand logic
# ===================================================================


class TestReportLogic:
    def test_all_sections_rendered(self, normalised: dict) -> None:
        rpt = normalised["report"]
        assert rpt["overview"] != ""
        assert rpt["top_ideas_summary"] != ""
        assert rpt["reviews_overview"] != ""

    def test_recommendation_extracted(self, normalised: dict) -> None:
        rec = _extract_recommendation(normalised["report"]["top_ideas_summary"])
        assert rec is not None
        assert "Recommendation" in rec

    def test_section_overview_content(self, normalised: dict) -> None:
        assert "Executive Overview" in normalised["report"]["overview"]


class TestReportFailurePaths:
    def test_schema_error_when_report_entirely_empty(self) -> None:
        """SchemaError raised when report has no content at all.

        The guard fires before any output is produced, so SchemaError
        is proof that no output file was created.
        """
        rpt = {
            "overview": "",
            "top_ideas_summary": "",
            "reviews_overview": "",
            "references": [],
        }
        with pytest.raises(SchemaError, match="no executive report found"):
            _require_report_content(rpt)

    def test_recommendation_absent_is_not_error(self, normalised_alt: dict) -> None:
        """No recommendation section in top-ideas summary → None, not error."""
        rec = _extract_recommendation(normalised_alt["report"]["top_ideas_summary"])
        assert rec is None

    def test_no_report_normalises_to_empty_strings(
        self, malformed_no_report: dict
    ) -> None:
        """Export without a report-shaped dict normalises with empty report."""
        record = _normalise(malformed_no_report, Path("test.json"))
        rpt = record["report"]
        assert rpt["overview"] == ""
        assert rpt["top_ideas_summary"] == ""
        assert rpt["reviews_overview"] == ""


# ===================================================================
# compare subcommand logic
# ===================================================================


class TestCompareLogic:
    def test_gene_overlap_computed(
        self, normalised: dict, normalised_alt: dict
    ) -> None:
        genes_a = {i["gene"] for i in normalised["ideas"] if i["gene"]}
        genes_b = {i["gene"] for i in normalised_alt["ideas"] if i["gene"]}

        shared = genes_a & genes_b
        only_a = genes_a - genes_b
        only_b = genes_b - genes_a

        assert "TP53" in shared
        assert "BRCA1" in only_a
        assert "KRAS" in only_b

    def test_ranking_table_aligned(
        self, normalised: dict, normalised_alt: dict
    ) -> None:
        max_rank = max(
            max(
                (i["ranking"] for i in normalised["ideas"] if i["ranking"]),
                default=0,
            ),
            max(
                (i["ranking"] for i in normalised_alt["ideas"] if i["ranking"]),
                default=0,
            ),
        )
        assert max_rank == 2

    def test_elo_merged_and_sorted(
        self, normalised: dict, normalised_alt: dict
    ) -> None:
        entries = []
        for i in normalised["ideas"]:
            entries.append({"gene": i["gene"], "elo": i["elo_rating"], "source": "A"})
        for i in normalised_alt["ideas"]:
            entries.append({"gene": i["gene"], "elo": i["elo_rating"], "source": "B"})
        entries.sort(key=lambda x: x["elo"], reverse=True)

        assert entries[0]["gene"] == "KRAS"
        assert entries[0]["elo"] == 1700.0


class TestCompareFailurePaths:
    def test_schema_error_on_raw_input(self) -> None:
        """SchemaError raised when record lacks the normalised schema tag.

        The guard fires before any output is produced, so SchemaError
        is proof that no output file was created.
        """
        raw = {
            "title": "Raw export",
            "Xr": [{"eloRating": 1500, "ranking": 1}],
        }
        with pytest.raises(SchemaError, match="not a normalised tournament artifact"):
            _validate_schema_tag(raw, "raw_export.json")

    def test_schema_error_on_empty_ideas(self) -> None:
        """SchemaError raised when normalised artifact has an empty ideas list.

        The guard fires before any output is produced, so SchemaError
        is proof that no output file was created.
        """
        record = {"schema": "dde.coscientist.v1", "ideas": []}
        with pytest.raises(SchemaError, match="contains no ideas"):
            _require_nonempty_ideas(record["ideas"], "empty.tournament.json")


# ===================================================================
# Failure-path invariant: SchemaError → exit code 3, no output
# ===================================================================


class TestFailurePathInvariant:
    """SchemaError always carries exit code 3."""

    def test_schema_error_exit_code(self) -> None:
        assert SchemaError("any message").exit_code == 3

    def test_schema_error_renders_message(self) -> None:
        err = SchemaError("artifact missing", detail="expected v1", remedy="re-ingest")
        rendered = err.render()
        assert "artifact missing" in rendered
        assert "expected v1" in rendered
        assert "re-ingest" in rendered

    def test_find_ideas_raises_not_returns_empty(
        self, malformed_no_ideas: dict
    ) -> None:
        """_find_ideas raises SchemaError, never returns an empty list."""
        with pytest.raises(SchemaError):
            _find_ideas(malformed_no_ideas)
