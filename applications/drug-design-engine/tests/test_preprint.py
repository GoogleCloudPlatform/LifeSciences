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

"""Tests for the preprint command group (arXiv preprint search).

Covers:
  - A query returning 3 results produces correct schema and count
  - A query returning 0 results: exit 0, fires preprint.no_results
  - Phase-2 contract: analyze is phase-two guarded
  - Registration checks: relay codes and threshold set
  - Relay guards: preprint.no_results does NOT fire when results exist
  - Manifest schema: all required fields present in results
  - Slug generation from query string
  - preprint.query_truncated fires when results are truncated
  - preprint.query_truncated does NOT fire when results fit within max
  - End-to-end: search then analyze produces correct analysis
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path
from typing import Any

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.commands.preprint import (
    _BIORXIV_MAX_PAGES,
    _BIORXIV_PAGE_SIZE,
    _parse_arxiv_entries,
    _parse_biorxiv_collection,
    _slugify,
)
from dde.core import provenance

# ---------------------------------------------------------------------------
# Helper: project setup and canned arXiv responses
# ---------------------------------------------------------------------------


def _make_project(base: Path) -> Path:
    """Create a minimal dde project directory."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    (project / "raw" / "literature").mkdir(parents=True, exist_ok=True)
    return project


def _arxiv_atom_response(
    entries: list[dict[str, Any]], total: int | None = None
) -> bytes:
    """Build a canned arXiv Atom XML response.

    Each entry dict should have: id, title, authors (list of str),
    abstract, published, updated, categories (list of str), doi (or None).
    """
    if total is None:
        total = len(entries)

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom"'
        ' xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"'
        ' xmlns:arxiv="http://arxiv.org/schemas/atom">',
        f"  <opensearch:totalResults>{total}</opensearch:totalResults>",
        "  <opensearch:startIndex>0</opensearch:startIndex>",
        f"  <opensearch:itemsPerPage>{len(entries)}</opensearch:itemsPerPage>",
    ]

    for e in entries:
        arxiv_id = e.get("id", "2301.00001")
        title = e.get("title", "Untitled")
        abstract = e.get("abstract", "No abstract.")
        published = e.get("published", "2023-01-15T00:00:00Z")
        updated = e.get("updated", "2023-01-16T00:00:00Z")
        authors = e.get("authors", ["Author One"])
        categories = e.get("categories", ["cs.AI"])
        doi = e.get("doi")

        parts.append("  <entry>")
        parts.append(f"    <id>http://arxiv.org/abs/{arxiv_id}v1</id>")
        parts.append(f"    <title>{title}</title>")
        parts.append(f"    <summary>{abstract}</summary>")
        parts.append(f"    <published>{published}</published>")
        parts.append(f"    <updated>{updated}</updated>")
        for author in authors:
            parts.append(f"    <author><name>{author}</name></author>")
        for cat in categories:
            parts.append(f'    <category term="{cat}"/>')
        parts.append(
            f'    <link href="http://arxiv.org/abs/{arxiv_id}v1" rel="alternate" type="text/html"/>'
        )
        parts.append(
            f'    <link href="http://arxiv.org/pdf/{arxiv_id}v1" title="pdf" rel="related" type="application/pdf"/>'
        )
        if doi:
            parts.append(f"    <arxiv:doi>{doi}</arxiv:doi>")
        parts.append("  </entry>")

    parts.append("</feed>")
    return "\n".join(parts).encode("utf-8")


def _mock_http_response(content: bytes, status_code: int = 200) -> mock.Mock:
    """Build a mock HTTP response that returns raw bytes."""
    resp = mock.Mock()
    resp.status_code = status_code
    resp.content = content
    resp.text = content.decode("utf-8")
    return resp


# ---------------------------------------------------------------------------
# 1. Query returning 3 results — correct schema and count
# ---------------------------------------------------------------------------


def test_search_three_results() -> None:
    """A query returning 3 results produces correct schema and count."""
    from click.testing import CliRunner
    from dde.cli import cli

    entries = [
        {
            "id": "2301.12345",
            "title": "Machine Learning for Drug Discovery",
            "authors": ["Alice Smith", "Bob Jones"],
            "abstract": "We present a method for drug discovery.",
            "published": "2023-01-15T00:00:00Z",
            "updated": "2023-01-16T00:00:00Z",
            "categories": ["cs.AI", "q-bio.BM"],
            "doi": "10.1234/test.001",
        },
        {
            "id": "2302.54321",
            "title": "Deep Learning in Bioinformatics",
            "authors": ["Carol Davis"],
            "abstract": "A survey of deep learning approaches.",
            "published": "2023-02-01T00:00:00Z",
            "updated": "2023-02-02T00:00:00Z",
            "categories": ["cs.LG"],
            "doi": None,
        },
        {
            "id": "2303.99999",
            "title": "Protein Structure Prediction",
            "authors": ["Eve White", "Frank Black"],
            "abstract": "Novel approaches to protein folding.",
            "published": "2023-03-10T00:00:00Z",
            "updated": "2023-03-11T00:00:00Z",
            "categories": ["q-bio.BM", "cs.AI"],
            "doi": "10.5678/test.002",
        },
    ]
    xml_bytes = _arxiv_atom_response(entries, total=3)

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.preprint.http.request") as mock_req:
            mock_req.return_value = _mock_http_response(xml_bytes)
            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "preprint",
                    "search",
                    "--source",
                    "arxiv",
                    "drug discovery machine learning",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        # Check the artifact
        lit_dir = project / "raw" / "literature"
        artifact_files = list(lit_dir.glob("*.preprint-search.json"))
        assert len(artifact_files) == 1, f"Expected 1 artifact, got {artifact_files}"

        artifact = json.loads(artifact_files[0].read_text(encoding="utf-8"))
        assert artifact["schema"] == "dde.preprint-search.v1"
        assert artifact["source"] == "arxiv"
        assert len(artifact["results"]) == 3
        assert artifact["total_results"] == 3

    print("  PASS: search with 3 results — correct schema and count")


# ---------------------------------------------------------------------------
# 2. Query returning 0 results — exit 0, fires preprint.no_results
# ---------------------------------------------------------------------------


def test_search_zero_results() -> None:
    """A query returning 0 results: exit 0, fires preprint.no_results."""
    from click.testing import CliRunner
    from dde.cli import cli

    xml_bytes = _arxiv_atom_response([], total=0)

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.preprint.http.request") as mock_req:
            mock_req.return_value = _mock_http_response(xml_bytes)
            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "preprint",
                    "search",
                    "--source",
                    "arxiv",
                    "xyznonexistentquery42",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        lit_dir = project / "raw" / "literature"

        # Check the meta file for relay codes
        meta_files = list(lit_dir.glob("*.meta.json"))
        assert len(meta_files) >= 1
        meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "preprint.no_results" in relay_codes, (
            f"preprint.no_results not fired; relays: {relay_codes}"
        )

    print("  PASS: 0 results — exit 0, fires preprint.no_results")


# ---------------------------------------------------------------------------
# 3. Phase-2 contract: analyze is phase-two guarded
# ---------------------------------------------------------------------------


def test_analyze_phase_two_contract() -> None:
    """analyze subcommand is phase-two guarded (offline, --overwrite injected)."""
    import click as click_mod
    from dde.cli import cli

    preprint_group = cli.commands.get("preprint")
    assert preprint_group is not None, "preprint command not registered"
    assert isinstance(preprint_group, click_mod.Group)
    analyze = preprint_group.commands.get("analyze")
    assert analyze is not None, "analyze subcommand not registered"

    callback = analyze.callback
    assert callback is not None
    assert getattr(callback, "_phase_two_guarded", False), (
        "analyze command is not phase-two guarded"
    )
    print("  PASS: analyze is phase-two guarded")


# ---------------------------------------------------------------------------
# 4. Registration checks: relay codes and threshold set
# ---------------------------------------------------------------------------


def test_relay_codes_registered() -> None:
    """Both preprint.* relay codes are in provenance.RELAY_CODES."""
    expected = [
        "preprint.no_results",
        "preprint.query_truncated",
    ]
    for code in expected:
        assert code in provenance.RELAY_CODES, f"{code} not registered in RELAY_CODES"
    print("  PASS: all preprint.* relay codes registered")


def test_threshold_set_registered() -> None:
    """Threshold set preprint-search is in declared_sets()."""
    from dde.core.thresholds import declared_sets

    sets = declared_sets()
    assert "preprint-search" in sets, (
        f"preprint-search not in declared sets: {sorted(sets.keys())}"
    )
    ts = sets["preprint-search"]
    assert ts.version == "1.0"
    assert ts.values["max_results_default"] == 20
    print("  PASS: threshold set preprint-search registered")


# ---------------------------------------------------------------------------
# 5. Relay guards: preprint.no_results does NOT fire when results exist
# ---------------------------------------------------------------------------


def test_relay_no_results_does_not_fire_when_results_exist() -> None:
    """preprint.no_results does NOT fire when there are results."""
    from click.testing import CliRunner
    from dde.cli import cli

    entries = [
        {
            "id": "2301.11111",
            "title": "A Paper With Results",
            "authors": ["Test Author"],
            "abstract": "Abstract text.",
            "published": "2023-01-15T00:00:00Z",
            "updated": "2023-01-16T00:00:00Z",
            "categories": ["cs.AI"],
            "doi": None,
        },
    ]
    xml_bytes = _arxiv_atom_response(entries, total=1)

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.preprint.http.request") as mock_req:
            mock_req.return_value = _mock_http_response(xml_bytes)
            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "preprint",
                    "search",
                    "--source",
                    "arxiv",
                    "test query",
                ],
                catch_exceptions=False,
            )
        assert result.exit_code == 0

        lit_dir = project / "raw" / "literature"
        meta_files = list(lit_dir.glob("*.meta.json"))
        assert len(meta_files) >= 1
        meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "preprint.no_results" not in relay_codes, (
            "preprint.no_results fired when results exist"
        )

    print("  PASS: preprint.no_results does NOT fire when results exist")


# ---------------------------------------------------------------------------
# 6. Manifest schema: all required fields present in results
# ---------------------------------------------------------------------------


def test_manifest_schema() -> None:
    """All required fields are present in the preprint search artifact."""
    from click.testing import CliRunner
    from dde.cli import cli

    entries = [
        {
            "id": "2301.55555",
            "title": "Schema Test Paper",
            "authors": ["Schema Author"],
            "abstract": "Test abstract for schema validation.",
            "published": "2023-01-20T00:00:00Z",
            "updated": "2023-01-21T00:00:00Z",
            "categories": ["cs.AI", "q-bio.BM"],
            "doi": "10.9999/schema-test",
        },
    ]
    xml_bytes = _arxiv_atom_response(entries, total=1)

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.preprint.http.request") as mock_req:
            mock_req.return_value = _mock_http_response(xml_bytes)
            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "preprint",
                    "search",
                    "--source",
                    "arxiv",
                    "schema test",
                ],
                catch_exceptions=False,
            )
        assert result.exit_code == 0

        lit_dir = project / "raw" / "literature"
        artifact_files = list(lit_dir.glob("*.preprint-search.json"))
        assert len(artifact_files) == 1

        artifact = json.loads(artifact_files[0].read_text(encoding="utf-8"))

        # Top-level required fields
        assert artifact["schema"] == "dde.preprint-search.v1"
        assert "source" in artifact
        assert "query" in artifact
        assert "searched_at" in artifact
        assert "total_results" in artifact
        assert "results" in artifact

        # Result record required fields
        r = artifact["results"][0]
        for field in (
            "id",
            "title",
            "authors",
            "abstract",
            "published",
            "updated",
            "categories",
            "pdf_url",
            "doi",
            "source",
        ):
            assert field in r, f"Missing result field: {field}"

        # Check specific values
        assert r["id"] == "2301.55555"
        assert r["doi"] == "10.9999/schema-test"
        assert r["source"] == "arxiv"
        assert isinstance(r["authors"], list)
        assert isinstance(r["categories"], list)

    print("  PASS: manifest schema — all required fields present")


# ---------------------------------------------------------------------------
# 7. Slug generation from query string
# ---------------------------------------------------------------------------


def test_slugify() -> None:
    """Slug generation produces filesystem-safe names."""
    assert _slugify("drug discovery") == "drug-discovery"
    assert _slugify("Machine Learning (2024)") == "machine-learning-2024"
    assert len(_slugify("a" * 200)) <= 80
    assert _slugify("") == "preprint-search"
    # Special characters stripped
    result = _slugify("BRCA1 AND cancer")
    assert "/" not in result
    assert " " not in result
    print("  PASS: slug generation")


# ---------------------------------------------------------------------------
# 8. _parse_arxiv_entries unit test
# ---------------------------------------------------------------------------


def test_parse_arxiv_entries() -> None:
    """_parse_arxiv_entries correctly parses Atom XML."""
    entries = [
        {
            "id": "2301.12345",
            "title": "Test Paper One",
            "authors": ["Alice", "Bob"],
            "abstract": "Abstract one.",
            "published": "2023-01-15T00:00:00Z",
            "updated": "2023-01-16T00:00:00Z",
            "categories": ["cs.AI"],
            "doi": "10.1234/test",
        },
        {
            "id": "2302.67890",
            "title": "Test Paper Two",
            "authors": ["Carol"],
            "abstract": "Abstract two.",
            "published": "2023-02-01T00:00:00Z",
            "updated": "2023-02-02T00:00:00Z",
            "categories": ["q-bio.BM", "cs.LG"],
            "doi": None,
        },
    ]
    xml_bytes = _arxiv_atom_response(entries, total=100)

    results, total = _parse_arxiv_entries(xml_bytes)
    assert total == 100
    assert len(results) == 2
    assert results[0]["id"] == "2301.12345"
    assert results[0]["doi"] == "10.1234/test"
    assert results[0]["authors"] == ["Alice", "Bob"]
    assert results[1]["id"] == "2302.67890"
    assert results[1]["doi"] is None
    assert "q-bio.BM" in results[1]["categories"]

    print("  PASS: _parse_arxiv_entries parses correctly")


# ---------------------------------------------------------------------------
# 9. Relay: preprint.query_truncated fires when results are truncated
# ---------------------------------------------------------------------------


def test_query_truncated_fires() -> None:
    """preprint.query_truncated fires when results are truncated."""
    from click.testing import CliRunner
    from dde.cli import cli

    entries = [
        {
            "id": "2301.00001",
            "title": "Paper One",
            "authors": ["Author A"],
            "abstract": "Abstract one.",
            "published": "2023-01-01T00:00:00Z",
            "updated": "2023-01-02T00:00:00Z",
            "categories": ["cs.AI"],
            "doi": None,
        },
        {
            "id": "2301.00002",
            "title": "Paper Two",
            "authors": ["Author B"],
            "abstract": "Abstract two.",
            "published": "2023-01-03T00:00:00Z",
            "updated": "2023-01-04T00:00:00Z",
            "categories": ["cs.LG"],
            "doi": None,
        },
    ]
    # 2 entries returned, but 10 total — triggers truncation with --max-results 2
    xml_bytes = _arxiv_atom_response(entries, total=10)

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.preprint.http.request") as mock_req:
            mock_req.return_value = _mock_http_response(xml_bytes)
            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "preprint",
                    "search",
                    "--source",
                    "arxiv",
                    "--max-results",
                    "2",
                    "truncation test query",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        lit_dir = project / "raw" / "literature"
        meta_files = list(lit_dir.glob("*.meta.json"))
        assert len(meta_files) >= 1
        meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "preprint.query_truncated" in relay_codes, (
            f"preprint.query_truncated not fired; relays: {relay_codes}"
        )

        # The warning message should mention truncation
        relay_messages = {
            r["code"]: r.get("message", "") for r in meta.get("mandatory_relays", [])
        }
        msg = relay_messages.get("preprint.query_truncated", "")
        assert "10" in msg or "truncat" in msg.lower(), (
            f"Truncation warning message does not mention total or truncation: {msg!r}"
        )

    print("  PASS: preprint.query_truncated fires when results are truncated")


# ---------------------------------------------------------------------------
# 10. Relay: preprint.query_truncated does NOT fire when not truncated
# ---------------------------------------------------------------------------


def test_query_truncated_no_fire() -> None:
    """preprint.query_truncated does NOT fire when results are not truncated."""
    from click.testing import CliRunner
    from dde.cli import cli

    entries = [
        {
            "id": "2301.10001",
            "title": "Paper Alpha",
            "authors": ["Author X"],
            "abstract": "Abstract alpha.",
            "published": "2023-01-01T00:00:00Z",
            "updated": "2023-01-02T00:00:00Z",
            "categories": ["cs.AI"],
            "doi": None,
        },
        {
            "id": "2301.10002",
            "title": "Paper Beta",
            "authors": ["Author Y"],
            "abstract": "Abstract beta.",
            "published": "2023-01-03T00:00:00Z",
            "updated": "2023-01-04T00:00:00Z",
            "categories": ["cs.LG"],
            "doi": None,
        },
        {
            "id": "2301.10003",
            "title": "Paper Gamma",
            "authors": ["Author Z"],
            "abstract": "Abstract gamma.",
            "published": "2023-01-05T00:00:00Z",
            "updated": "2023-01-06T00:00:00Z",
            "categories": ["q-bio.BM"],
            "doi": None,
        },
    ]
    # 3 entries returned, 3 total, max_results=5 — no truncation
    xml_bytes = _arxiv_atom_response(entries, total=3)

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.preprint.http.request") as mock_req:
            mock_req.return_value = _mock_http_response(xml_bytes)
            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "preprint",
                    "search",
                    "--source",
                    "arxiv",
                    "--max-results",
                    "5",
                    "non truncated query",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        lit_dir = project / "raw" / "literature"
        meta_files = list(lit_dir.glob("*.meta.json"))
        assert len(meta_files) >= 1
        meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "preprint.query_truncated" not in relay_codes, (
            "preprint.query_truncated fired when results are not truncated"
        )

    print("  PASS: preprint.query_truncated does NOT fire when not truncated")


# ---------------------------------------------------------------------------
# 11. End-to-end: search then analyze
# ---------------------------------------------------------------------------


def test_analyze_end_to_end() -> None:
    """analyze subcommand reads search output and produces analysis."""
    from click.testing import CliRunner
    from dde.cli import cli

    entries = [
        {
            "id": "2301.20001",
            "title": "Analysis Paper One",
            "authors": ["Author P"],
            "abstract": "Abstract for analysis test.",
            "published": "2023-01-10T00:00:00Z",
            "updated": "2023-01-11T00:00:00Z",
            "categories": ["cs.AI"],
            "doi": "10.1234/analysis.001",
        },
        {
            "id": "2301.20002",
            "title": "Analysis Paper Two",
            "authors": ["Author Q"],
            "abstract": "Second abstract for analysis.",
            "published": "2023-01-12T00:00:00Z",
            "updated": "2023-01-13T00:00:00Z",
            "categories": ["cs.LG", "q-bio.BM"],
            "doi": None,
        },
        {
            "id": "2301.20003",
            "title": "Analysis Paper Three",
            "authors": ["Author R", "Author S"],
            "abstract": "Third abstract for analysis.",
            "published": "2023-01-14T00:00:00Z",
            "updated": "2023-01-15T00:00:00Z",
            "categories": ["q-bio.BM"],
            "doi": "10.5678/analysis.003",
        },
    ]
    xml_bytes = _arxiv_atom_response(entries, total=3)

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        # Phase 1: search
        with mock.patch("dde.commands.preprint.http.request") as mock_req:
            mock_req.return_value = _mock_http_response(xml_bytes)
            search_result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "preprint",
                    "search",
                    "--source",
                    "arxiv",
                    "analysis end to end test",
                ],
                catch_exceptions=False,
            )

        assert search_result.exit_code == 0, (
            f"Search exit {search_result.exit_code}\n{search_result.output}"
        )

        # Find the artifact written by search
        lit_dir = project / "raw" / "literature"
        artifact_files = list(lit_dir.glob("*.preprint-search.json"))
        assert len(artifact_files) == 1, f"Expected 1 artifact, got {artifact_files}"
        artifact_name = artifact_files[0].name

        # Phase 2: analyze (reads from same directory, no network)
        analyze_result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "preprint",
                "analyze",
                artifact_name,
                "--from",
                str(lit_dir),
            ],
            catch_exceptions=False,
        )

        assert analyze_result.exit_code == 0, (
            f"Analyze exit {analyze_result.exit_code}\n{analyze_result.output}"
        )

        # Check analysis output file exists
        analysis_files = list(lit_dir.glob("*.preprint-search.analysis.json"))
        assert len(analysis_files) == 1, (
            f"Expected 1 analysis file, got {analysis_files}"
        )

        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        # Outcome
        assert analysis["assessment"]["outcome"] == "results-found", (
            f"Expected outcome 'results-found', got {analysis['assessment']['outcome']!r}"
        )

        # Source breakdown: all 3 are arxiv
        source_breakdown = analysis["assessment"]["source_breakdown"]
        assert "arxiv" in source_breakdown, (
            f"arxiv not in source_breakdown: {source_breakdown}"
        )
        assert source_breakdown["arxiv"] == 3, (
            f"Expected 3 arxiv results, got {source_breakdown['arxiv']}"
        )

        # Threshold set
        assert analysis["threshold_set"] == "preprint-search", (
            f"Expected threshold_set 'preprint-search', got {analysis['threshold_set']!r}"
        )

    print("  PASS: analyze end-to-end — search then analyze produces correct analysis")


# ---------------------------------------------------------------------------
# bioRxiv helpers
# ---------------------------------------------------------------------------


def _biorxiv_api_response(
    items: list[dict[str, Any]],
    total: int | None = None,
) -> dict[str, Any]:
    """Build a canned bioRxiv API JSON response.

    Each item dict should have: doi, title, authors, abstract, date,
    category, version, server (defaults provided where missing).
    """
    collection = []
    for item in items:
        collection.append(
            {
                "doi": item.get("doi", "10.1101/2023.01.01.000001"),
                "title": item.get("title", "Untitled"),
                "authors": item.get("authors", "Author One; Author Two"),
                "author_corresponding": item.get("author_corresponding", "Author One"),
                "author_corresponding_institution": item.get(
                    "author_corresponding_institution", "Test University"
                ),
                "date": item.get("date", "2023-01-15"),
                "version": item.get("version", "1"),
                "type": item.get("type", "new results"),
                "license": item.get("license", "cc_by_nc_nd"),
                "category": item.get("category", "bioinformatics"),
                "jatsxml": item.get("jatsxml", ""),
                "abstract": item.get("abstract", "No abstract."),
                "published": item.get("published", "NA"),
                "server": item.get("server", "bioRxiv"),
            }
        )

    if total is None:
        total = len(collection)

    return {
        "messages": [
            {
                "status": "ok",
                "count": len(collection),
                "total": str(total),
            }
        ],
        "collection": collection,
    }


# ---------------------------------------------------------------------------
# 12. bioRxiv: query returning results — correct schema, source="biorxiv"
# ---------------------------------------------------------------------------


def test_biorxiv_search_results() -> None:
    """A bioRxiv query returning results produces correct schema and source."""
    from click.testing import CliRunner
    from dde.cli import cli

    items = [
        {
            "doi": "10.1101/2023.01.15.000001",
            "title": "CRISPR gene editing in zebrafish",
            "authors": "Alice Smith; Bob Jones",
            "abstract": "We present a CRISPR method for gene editing.",
            "date": "2023-01-15",
            "version": "1",
            "category": "genetics",
            "server": "bioRxiv",
        },
        {
            "doi": "10.1101/2023.02.01.000002",
            "title": "Gene editing approaches in model organisms",
            "authors": "Carol Davis",
            "abstract": "A survey of gene editing approaches.",
            "date": "2023-02-01",
            "version": "2",
            "category": "genomics",
            "server": "bioRxiv",
        },
        {
            "doi": "10.1101/2023.03.10.000003",
            "title": "Gene therapy for rare diseases",
            "authors": "Eve White; Frank Black",
            "abstract": "Novel gene editing approaches to rare diseases.",
            "date": "2023-03-10",
            "version": "1",
            "category": "genetics",
            "server": "bioRxiv",
        },
    ]
    api_response = _biorxiv_api_response(items, total=3)

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.preprint.http.get_json") as mock_get:
            mock_get.return_value = api_response
            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "preprint",
                    "search",
                    "--source",
                    "biorxiv",
                    "gene editing",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        # Check the artifact
        lit_dir = project / "raw" / "literature"
        artifact_files = list(lit_dir.glob("*.preprint-search.json"))
        assert len(artifact_files) == 1, f"Expected 1 artifact, got {artifact_files}"

        artifact = json.loads(artifact_files[0].read_text(encoding="utf-8"))
        assert artifact["schema"] == "dde.preprint-search.v1"
        assert artifact["source"] == "biorxiv"
        assert len(artifact["results"]) == 3
        for r in artifact["results"]:
            assert r["source"] == "biorxiv"
            assert r["doi"] is not None
            assert isinstance(r["authors"], list)

    print("  PASS: bioRxiv search with results — correct schema and source")


# ---------------------------------------------------------------------------
# 13. bioRxiv: query returning 0 results — fires preprint.no_results
# ---------------------------------------------------------------------------


def test_biorxiv_search_zero_results() -> None:
    """A bioRxiv query returning 0 results: exit 0, fires preprint.no_results."""
    from click.testing import CliRunner
    from dde.cli import cli

    # Empty collection — no items match the query.
    api_response = _biorxiv_api_response([], total=0)

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.preprint.http.get_json") as mock_get:
            mock_get.return_value = api_response
            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "preprint",
                    "search",
                    "--source",
                    "biorxiv",
                    "xyznonexistentquery42",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        lit_dir = project / "raw" / "literature"

        # Check the meta file for relay codes
        meta_files = list(lit_dir.glob("*.meta.json"))
        assert len(meta_files) >= 1
        meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "preprint.no_results" in relay_codes, (
            f"preprint.no_results not fired; relays: {relay_codes}"
        )

    print("  PASS: bioRxiv 0 results — exit 0, fires preprint.no_results")


# ---------------------------------------------------------------------------
# 14. bioRxiv: source validation — --source biorxiv is accepted
# ---------------------------------------------------------------------------


def test_biorxiv_source_accepted() -> None:
    """--source biorxiv is accepted by the CLI."""
    from click.testing import CliRunner
    from dde.cli import cli

    api_response = _biorxiv_api_response(
        [
            {
                "doi": "10.1101/2023.05.01.000010",
                "title": "Test paper for source validation",
                "authors": "Test Author",
                "abstract": "Source validation abstract.",
                "date": "2023-05-01",
                "version": "1",
                "category": "bioinformatics",
                "server": "bioRxiv",
            },
        ],
        total=1,
    )

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.preprint.http.get_json") as mock_get:
            mock_get.return_value = api_response
            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "preprint",
                    "search",
                    "--source",
                    "biorxiv",
                    "test",
                ],
                catch_exceptions=False,
            )

        # The key assertion: biorxiv is accepted as a source.
        assert result.exit_code == 0, (
            f"--source biorxiv rejected: exit {result.exit_code}\n{result.output}"
        )

    print("  PASS: --source biorxiv is accepted")


# ---------------------------------------------------------------------------
# 15. _parse_biorxiv_collection unit test
# ---------------------------------------------------------------------------


def test_parse_biorxiv_collection() -> None:
    """_parse_biorxiv_collection correctly parses bioRxiv JSON items."""
    items = [
        {
            "doi": "10.1101/2023.01.15.000001",
            "title": "  CRISPR  gene  editing  ",
            "authors": "Alice Smith; Bob Jones",
            "abstract": "  Abstract  with  extra  spaces.  ",
            "date": "2023-01-15",
            "version": "1",
            "category": "genetics",
            "server": "bioRxiv",
        },
        {
            "doi": "10.1101/2023.02.01.000002",
            "title": "Protein folding dynamics",
            "authors": "Carol Davis",
            "abstract": "A study of protein folding.",
            "date": "2023-02-01",
            "version": "2",
            "category": "biophysics",
            "server": "bioRxiv",
        },
    ]

    # Without query filter — return all items.
    results = _parse_biorxiv_collection(items)
    assert len(results) == 2
    assert results[0]["id"] == "10.1101/2023.01.15.000001"
    assert results[0]["doi"] == "10.1101/2023.01.15.000001"
    assert results[0]["authors"] == ["Alice Smith", "Bob Jones"]
    assert results[0]["source"] == "biorxiv"
    # Whitespace cleaned up
    assert "  " not in results[0]["title"]
    assert "  " not in results[0]["abstract"]

    assert results[1]["id"] == "10.1101/2023.02.01.000002"
    assert results[1]["categories"] == ["biophysics"]

    # With query filter — only matching items returned.
    filtered = _parse_biorxiv_collection(items, query="CRISPR")
    assert len(filtered) == 1
    assert filtered[0]["doi"] == "10.1101/2023.01.15.000001"

    # Query that matches nothing
    empty = _parse_biorxiv_collection(items, query="xyznonexistent42")
    assert len(empty) == 0

    print("  PASS: _parse_biorxiv_collection parses correctly")


# ---------------------------------------------------------------------------
# 16. bioRxiv: pagination cap limits API calls
# ---------------------------------------------------------------------------


def test_biorxiv_pagination_cap() -> None:
    """bioRxiv search respects _BIORXIV_MAX_PAGES and notes cap in sidecar."""
    from click.testing import CliRunner
    from dde.cli import cli

    # Build a full page of 100 items that do NOT match the search query.
    # This keeps len(results) == 0 so the loop continues until the cap fires.
    non_matching_items = [
        {
            "doi": f"10.1101/2023.01.01.{i:06d}",
            "title": f"Unrelated paper number {i}",
            "authors": "Author A; Author B",
            "abstract": f"Study about something completely different #{i}.",
            "date": "2023-01-15",
            "version": "1",
            "category": "genetics",
            "server": "bioRxiv",
        }
        for i in range(_BIORXIV_PAGE_SIZE)
    ]
    full_page = _biorxiv_api_response(non_matching_items, total=_BIORXIV_PAGE_SIZE)

    call_count = 0

    def mock_get_json(url: str, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        # Always return a full page so the loop would run forever without the cap.
        return full_page

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch(
            "dde.commands.preprint.http.get_json", side_effect=mock_get_json
        ):
            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "preprint",
                    "search",
                    "--source",
                    "biorxiv",
                    "--max-results",
                    "20",
                    "xyzuniquequerythatwontmatch42",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        # The loop should have stopped at _BIORXIV_MAX_PAGES (not run indefinitely).
        # We add 1 because the cap check happens after the cursor increment, so
        # the last allowed page fires the cap.
        assert call_count <= _BIORXIV_MAX_PAGES + 1, (
            f"Expected at most {_BIORXIV_MAX_PAGES + 1} API calls, got {call_count}"
        )

        # Verify the sidecar records that the scan was capped.
        # sidecar.note() stores values as top-level keys in the meta dict.
        lit_dir = project / "raw" / "literature"
        meta_files = list(lit_dir.glob("*.meta.json"))
        assert len(meta_files) >= 1
        meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
        assert meta.get("biorxiv_scan_capped") is True, (
            f"Expected biorxiv_scan_capped=True in sidecar, got {meta.get('biorxiv_scan_capped')}"
        )

    print("  PASS: bioRxiv pagination cap limits API calls and notes in sidecar")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        ("test_search_three_results", test_search_three_results),
        ("test_search_zero_results", test_search_zero_results),
        ("test_analyze_phase_two_contract", test_analyze_phase_two_contract),
        ("test_relay_codes_registered", test_relay_codes_registered),
        ("test_threshold_set_registered", test_threshold_set_registered),
        (
            "test_relay_no_results_does_not_fire_when_results_exist",
            test_relay_no_results_does_not_fire_when_results_exist,
        ),
        ("test_manifest_schema", test_manifest_schema),
        ("test_slugify", test_slugify),
        ("test_parse_arxiv_entries", test_parse_arxiv_entries),
        ("test_query_truncated_fires", test_query_truncated_fires),
        ("test_query_truncated_no_fire", test_query_truncated_no_fire),
        ("test_analyze_end_to_end", test_analyze_end_to_end),
        ("test_biorxiv_search_results", test_biorxiv_search_results),
        ("test_biorxiv_search_zero_results", test_biorxiv_search_zero_results),
        ("test_biorxiv_source_accepted", test_biorxiv_source_accepted),
        ("test_parse_biorxiv_collection", test_parse_biorxiv_collection),
        ("test_biorxiv_pagination_cap", test_biorxiv_pagination_cap),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:
            print(f"  FAIL: {name} — {exc}")
            import traceback

            traceback.print_exc()
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    if failed:
        sys.exit(1)
    else:
        print("All tests passed.")


if __name__ == "__main__":
    main()
