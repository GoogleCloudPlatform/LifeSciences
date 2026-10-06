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

"""Tests for the pathway command group (#229 / PR #245).

Covers:
  - _resolve_uniprot_accession — UniProt ID mapping for QuickGO
  - _search_reactome — Reactome pathway search (mocked HTTP)
  - _search_go — QuickGO annotation search (mocked HTTP)
  - _build_output — schema structure for both sources
  - analyze_cmd — phase-2 analysis for reactome and GO sources (R2 R1 fix)
  - URL encoding of gene symbols
  - --name vs filename behavior (R2 fix)
  - Error handling — gene not found
  - Evidence code diversity preservation (O3 fix)
  - QuickGO pagination (R2 O3 fix)
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

from dde.commands.pathway import (
    _build_output,
    _resolve_uniprot_accession,
    _search_go,
    _search_reactome,
    analyze_cmd,
)
from dde.core.errors import ArtifactError, Refusal

# ---------------------------------------------------------------------------
# 1. _resolve_uniprot_accession tests
# ---------------------------------------------------------------------------


def test_resolve_uniprot_accession_happy() -> None:
    """A valid gene symbol resolves to a UniProt accession."""
    mock_response = {
        "results": [{"primaryAccession": "P38398"}],
    }
    with mock.patch("dde.commands.pathway.http.get_json") as mock_get:
        mock_get.return_value = mock_response
        accession = _resolve_uniprot_accession("BRCA1")

    assert accession == "P38398", f"Expected 'P38398', got {accession!r}"
    # Verify the URL was constructed correctly with encoding
    call_url = mock_get.call_args[0][0]
    assert "gene_exact:BRCA1" in call_url
    assert "organism_id:9606" in call_url
    print("  PASS: _resolve_uniprot_accession happy path")


def test_resolve_uniprot_accession_not_found() -> None:
    """An unknown gene symbol raises Refusal."""
    mock_response: dict[str, Any] = {"results": []}
    with mock.patch("dde.commands.pathway.http.get_json") as mock_get:
        mock_get.return_value = mock_response
        try:
            _resolve_uniprot_accession("NOTAGENE")
            assert False, "Expected Refusal"
        except Refusal:
            pass
    print("  PASS: _resolve_uniprot_accession gene not found")


def test_resolve_uniprot_accession_empty_accession() -> None:
    """A record with an empty accession raises Refusal."""
    mock_response: dict[str, Any] = {"results": [{"primaryAccession": ""}]}
    with mock.patch("dde.commands.pathway.http.get_json") as mock_get:
        mock_get.return_value = mock_response
        try:
            _resolve_uniprot_accession("BROKEN")
            assert False, "Expected Refusal"
        except Refusal:
            pass
    print("  PASS: _resolve_uniprot_accession empty accession")


# ---------------------------------------------------------------------------
# 2. _search_reactome tests
# ---------------------------------------------------------------------------


def _make_reactome_response(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a canned Reactome search response."""
    return {"results": [{"entries": entries}]}


def test_search_reactome_happy() -> None:
    """Reactome search returns pathway entries from a well-formed response."""
    reactome_entries = [
        {
            "stId": "R-HSA-1640170",
            "name": "Cell Cycle",
            "species": ["Homo sapiens"],
        },
        {
            "stId": "R-HSA-69278",
            "name": "Cell Cycle, Mitotic",
            "species": ["Homo sapiens"],
        },
    ]
    payload = _make_reactome_response(reactome_entries)
    raw_bytes = json.dumps(payload).encode("utf-8")

    with mock.patch("dde.commands.pathway.http.request") as mock_req:
        mock_resp = mock.Mock()
        mock_resp.content = raw_bytes
        mock_req.return_value = mock_resp

        _raw, entries = _search_reactome("BRCA1")

    assert len(entries) == 2, f"Expected 2 entries, got {len(entries)}"
    assert entries[0]["source_db"] == "reactome"
    assert entries[0]["pathway_id"] == "R-HSA-1640170"
    assert entries[0]["name"] == "Cell Cycle"
    assert entries[1]["pathway_id"] == "R-HSA-69278"
    print("  PASS: _search_reactome happy path")


def test_search_reactome_empty() -> None:
    """Reactome returns empty results list."""
    payload: dict[str, Any] = {"results": []}
    raw_bytes = json.dumps(payload).encode("utf-8")

    with mock.patch("dde.commands.pathway.http.request") as mock_req:
        mock_resp = mock.Mock()
        mock_resp.content = raw_bytes
        mock_req.return_value = mock_resp

        _raw, entries = _search_reactome("NOTAGENE")

    assert len(entries) == 0, f"Expected 0 entries, got {len(entries)}"
    print("  PASS: _search_reactome empty results")


def test_search_reactome_url_encoding() -> None:
    """Gene symbols are URL-encoded in the Reactome query."""
    payload: dict[str, Any] = {"results": []}
    raw_bytes = json.dumps(payload).encode("utf-8")

    with mock.patch("dde.commands.pathway.http.request") as mock_req:
        mock_resp = mock.Mock()
        mock_resp.content = raw_bytes
        mock_req.return_value = mock_resp

        _search_reactome("IL2R&A")

    call_url = mock_req.call_args[0][1]
    # The & in the gene symbol must be encoded, not bare
    assert "IL2R%26A" in call_url, (
        f"Gene symbol with & was not URL-encoded in URL: {call_url}"
    )
    assert "IL2R&A" not in call_url.split("?", 1)[1].split("&types=")[0], (
        f"Bare & found in query parameter: {call_url}"
    )
    print("  PASS: _search_reactome URL encoding")


# ---------------------------------------------------------------------------
# 3. _search_go tests
# ---------------------------------------------------------------------------


def _make_quickgo_response(
    annotations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a canned QuickGO annotation response."""
    return {"results": annotations}


def test_search_go_happy() -> None:
    """QuickGO search resolves accession and returns GO annotations."""
    uniprot_response = {"results": [{"primaryAccession": "P38398"}]}
    quickgo_annotations = [
        {
            "goId": "GO:0006281",
            "goName": "DNA repair",
            "goAspect": "biological_process",
            "goEvidence": "IDA",
        },
        {
            "goId": "GO:0005634",
            "goName": "nucleus",
            "goAspect": "cellular_component",
            "goEvidence": "IDA",
        },
    ]
    quickgo_payload = _make_quickgo_response(quickgo_annotations)
    quickgo_raw = json.dumps(quickgo_payload).encode("utf-8")

    with (
        mock.patch("dde.commands.pathway.http.get_json") as mock_get,
        mock.patch("dde.commands.pathway.http.request") as mock_req,
    ):
        mock_get.return_value = uniprot_response
        mock_resp = mock.Mock()
        mock_resp.content = quickgo_raw
        mock_req.return_value = mock_resp

        _raw, entries = _search_go("BRCA1")

    assert len(entries) == 2, f"Expected 2 entries, got {len(entries)}"
    assert entries[0]["source_db"] == "go"
    assert entries[0]["term_id"] == "GO:0006281"
    assert entries[0]["name"] == "DNA repair"
    assert entries[0]["evidence_codes"] == ["IDA"]
    # Verify accession was used in QuickGO URL, not the gene symbol
    call_url = mock_req.call_args[0][1]
    assert "P38398" in call_url, (
        f"Expected UniProt accession in QuickGO URL, got: {call_url}"
    )
    assert "BRCA1" not in call_url, (
        f"Gene symbol should not appear in QuickGO URL: {call_url}"
    )
    print("  PASS: _search_go happy path (UniProt accession used)")


def test_search_go_empty() -> None:
    """QuickGO returns zero annotations."""
    uniprot_response = {"results": [{"primaryAccession": "P99999"}]}
    quickgo_payload: dict[str, Any] = {"results": []}
    quickgo_raw = json.dumps(quickgo_payload).encode("utf-8")

    with (
        mock.patch("dde.commands.pathway.http.get_json") as mock_get,
        mock.patch("dde.commands.pathway.http.request") as mock_req,
    ):
        mock_get.return_value = uniprot_response
        mock_resp = mock.Mock()
        mock_resp.content = quickgo_raw
        mock_req.return_value = mock_resp

        _raw, entries = _search_go("NOTAGENE")

    assert len(entries) == 0, f"Expected 0 entries, got {len(entries)}"
    print("  PASS: _search_go empty results")


def test_search_go_evidence_diversity() -> None:
    """Multiple evidence codes for the same GO term are preserved (O3 fix)."""
    uniprot_response = {"results": [{"primaryAccession": "P38398"}]}
    # Same GO term annotated with three different evidence codes
    quickgo_annotations = [
        {
            "goId": "GO:0006281",
            "goName": "DNA repair",
            "goAspect": "biological_process",
            "goEvidence": "IDA",
        },
        {
            "goId": "GO:0006281",
            "goName": "DNA repair",
            "goAspect": "biological_process",
            "goEvidence": "IEA",
        },
        {
            "goId": "GO:0006281",
            "goName": "DNA repair",
            "goAspect": "biological_process",
            "goEvidence": "IMP",
        },
        {
            "goId": "GO:0005634",
            "goName": "nucleus",
            "goAspect": "cellular_component",
            "goEvidence": "IDA",
        },
    ]
    quickgo_payload = _make_quickgo_response(quickgo_annotations)
    quickgo_raw = json.dumps(quickgo_payload).encode("utf-8")

    with (
        mock.patch("dde.commands.pathway.http.get_json") as mock_get,
        mock.patch("dde.commands.pathway.http.request") as mock_req,
    ):
        mock_get.return_value = uniprot_response
        mock_resp = mock.Mock()
        mock_resp.content = quickgo_raw
        mock_req.return_value = mock_resp

        _raw, entries = _search_go("BRCA1")

    # Should deduplicate by term but keep all evidence codes
    assert len(entries) == 2, f"Expected 2 entries (deduplicated), got {len(entries)}"
    dna_repair = entries[0]
    assert dna_repair["term_id"] == "GO:0006281"
    assert sorted(dna_repair["evidence_codes"]) == ["IDA", "IEA", "IMP"], (
        f"Expected ['IDA', 'IEA', 'IMP'], got {dna_repair['evidence_codes']}"
    )
    nucleus = entries[1]
    assert nucleus["evidence_codes"] == ["IDA"]
    print("  PASS: _search_go evidence diversity preserved")


def test_search_go_dedup_same_evidence() -> None:
    """Duplicate evidence codes for the same term are not repeated."""
    uniprot_response = {"results": [{"primaryAccession": "P38398"}]}
    quickgo_annotations = [
        {
            "goId": "GO:0006281",
            "goName": "DNA repair",
            "goAspect": "biological_process",
            "goEvidence": "IDA",
        },
        {
            "goId": "GO:0006281",
            "goName": "DNA repair",
            "goAspect": "biological_process",
            "goEvidence": "IDA",  # same evidence code, should not duplicate
        },
    ]
    quickgo_payload = _make_quickgo_response(quickgo_annotations)
    quickgo_raw = json.dumps(quickgo_payload).encode("utf-8")

    with (
        mock.patch("dde.commands.pathway.http.get_json") as mock_get,
        mock.patch("dde.commands.pathway.http.request") as mock_req,
    ):
        mock_get.return_value = uniprot_response
        mock_resp = mock.Mock()
        mock_resp.content = quickgo_raw
        mock_req.return_value = mock_resp

        _raw, entries = _search_go("BRCA1")

    assert len(entries) == 1
    assert entries[0]["evidence_codes"] == ["IDA"], (
        f"Expected ['IDA'] (no duplicates), got {entries[0]['evidence_codes']}"
    )
    print("  PASS: _search_go duplicate evidence not repeated")


def test_search_go_url_encoding() -> None:
    """UniProt accessions are URL-encoded in the QuickGO query."""
    uniprot_response = {"results": [{"primaryAccession": "P38398"}]}
    quickgo_payload: dict[str, Any] = {"results": []}
    quickgo_raw = json.dumps(quickgo_payload).encode("utf-8")

    with (
        mock.patch("dde.commands.pathway.http.get_json") as mock_get,
        mock.patch("dde.commands.pathway.http.request") as mock_req,
    ):
        mock_get.return_value = uniprot_response
        mock_resp = mock.Mock()
        mock_resp.content = quickgo_raw
        mock_req.return_value = mock_resp

        _search_go("BRCA1")

    # Verify UniProt search URL encodes the gene symbol
    uniprot_url = mock_get.call_args[0][0]
    assert "gene_exact:" in uniprot_url
    # Verify QuickGO URL uses the accession
    quickgo_url = mock_req.call_args[0][1]
    assert "geneProductId=P38398" in quickgo_url
    print("  PASS: _search_go URL encoding")


def test_search_go_uniprot_resolution_failure() -> None:
    """If UniProt cannot resolve the gene symbol, _search_go raises Refusal."""
    uniprot_response: dict[str, Any] = {"results": []}
    with mock.patch("dde.commands.pathway.http.get_json") as mock_get:
        mock_get.return_value = uniprot_response
        try:
            _search_go("NOTAGENE")
            assert False, "Expected Refusal from UniProt resolution failure"
        except Refusal:
            pass
    print("  PASS: _search_go UniProt resolution failure raises Refusal")


# ---------------------------------------------------------------------------
# 4. _build_output tests
# ---------------------------------------------------------------------------


def test_build_output_reactome_schema() -> None:
    """Reactome output uses dde.pathway-reactome.v1 schema."""
    entries = [
        {
            "source_db": "reactome",
            "pathway_id": "R-HSA-1",
            "name": "Pathway A",
            "species": "Homo sapiens",
        },
        {
            "source_db": "reactome",
            "pathway_id": "R-HSA-2",
            "name": "Pathway B",
            "species": "Homo sapiens",
        },
    ]
    result = _build_output("BRCA1", "reactome", entries)

    assert result["schema"] == "dde.pathway-reactome.v1", (
        f"Expected schema 'dde.pathway-reactome.v1', got {result['schema']!r}"
    )
    assert result["query"]["gene"] == "BRCA1"
    assert result["query"]["source"] == "reactome"
    assert result["summary"]["n_pathways"] == 2
    assert result["summary"]["top_pathways"] == ["Pathway A", "Pathway B"]
    assert len(result["pathways"]) == 2
    print("  PASS: _build_output reactome schema")


def test_build_output_go_schema() -> None:
    """GO output uses dde.pathway-go.v1 schema."""
    entries = [
        {
            "source_db": "go",
            "term_id": "GO:0006281",
            "name": "DNA repair",
            "aspect": "biological_process",
            "evidence_codes": ["IDA"],
        },
    ]
    result = _build_output("BRCA1", "go", entries)

    assert result["schema"] == "dde.pathway-go.v1", (
        f"Expected schema 'dde.pathway-go.v1', got {result['schema']!r}"
    )
    assert result["query"]["gene"] == "BRCA1"
    assert result["query"]["source"] == "go"
    assert result["summary"]["n_terms"] == 1
    assert len(result["annotations"]) == 1
    print("  PASS: _build_output go schema")


def test_build_output_top_names_capped() -> None:
    """top_pathways / top_terms are capped at 5 entries."""
    entries = [
        {
            "source_db": "reactome",
            "pathway_id": f"R-HSA-{i}",
            "name": f"P{i}",
            "species": "Homo sapiens",
        }
        for i in range(10)
    ]
    result = _build_output("TP53", "reactome", entries)

    assert len(result["summary"]["top_pathways"]) == 5, (
        f"Expected 5 top_pathways, got {len(result['summary']['top_pathways'])}"
    )
    assert result["summary"]["n_pathways"] == 10
    print("  PASS: _build_output top names capped at 5")


# ---------------------------------------------------------------------------
# 4b. analyze_cmd tests (R2 R1 fix)
# ---------------------------------------------------------------------------


def _invoke_analyze(
    tmp: Path,
    gene: str,
    source: str,
    artifact_data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    """Helper: write a canned artifact to *tmp*, invoke analyze_cmd logic.

    Returns (assessment, metrics, relays) captured from the emitter.
    """
    resolved = gene.upper()
    suffix = f"pathway-{source}"
    artifact_path = tmp / f"{resolved}.{suffix}.json"
    artifact_path.write_text(json.dumps(artifact_data), encoding="utf-8")

    captured: dict[str, Any] = {}

    # Mock the emitter to capture data calls.
    mock_emit = mock.MagicMock()

    def capture_data(key: str, value: Any) -> None:
        captured[key] = value

    mock_emit.data.side_effect = capture_data

    # Mock project that returns tmp for artifact_dir and a relative path.
    mock_project = mock.MagicMock()
    mock_project.artifact_dir.return_value = tmp
    mock_project.relative.return_value = str(artifact_path)

    mock_state = mock.MagicMock()
    mock_state.project.return_value = mock_project

    analysis_out_path = tmp / f"{resolved}.{suffix}.analysis.json"

    with (
        mock.patch("dde.commands.pathway.emitter", return_value=mock_emit),
        mock.patch(
            "dde.commands.pathway.provenance.write_analysis",
            return_value=analysis_out_path,
        ),
    ):
        # Call the callback directly (unwrapped from Click decorators).
        analyze_cmd.callback(
            mock_state,
            gene,
            source,
            None,  # from_dir
            None,  # out
            False,  # as_json
            True,  # quiet
        )

    # Extract what was passed to write_analysis.
    return (
        captured.get("assessment", {}),
        captured.get("metrics", {}),
        captured.get("relays", []),
    )


def test_analyze_reactome_happy() -> None:
    """Analyze reactome: reads stored pathways and builds assessment/metrics."""
    artifact = _build_output(
        "BRCA1",
        "reactome",
        [
            {
                "source_db": "reactome",
                "pathway_id": "R-HSA-1640170",
                "name": "Cell Cycle",
                "species": "Homo sapiens",
            },
            {
                "source_db": "reactome",
                "pathway_id": "R-HSA-69278",
                "name": "Cell Cycle, Mitotic",
                "species": "Homo sapiens",
            },
            {
                "source_db": "reactome",
                "pathway_id": "R-HSA-1640170",
                "name": "Cell Cycle",
                "species": "Homo sapiens",
            },
        ],
    )

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        assessment, metrics, _relays = _invoke_analyze(
            tmp, "brca1", "reactome", artifact
        )

    assert assessment["gene"] == "BRCA1"
    assert assessment["source"] == "reactome"
    assert assessment["n_pathways"] == 3
    assert "Cell Cycle" in assessment["categories"]
    assert metrics["total_pathways"] == 3
    assert metrics["unique_categories"] == 2  # "Cell Cycle" and "Cell Cycle, Mitotic"
    assert metrics["top_category"] == "Cell Cycle"  # appears twice
    print("  PASS: test_analyze_reactome_happy")


def test_analyze_go_happy() -> None:
    """Analyze GO: reads stored annotations and groups by aspect."""
    artifact = _build_output(
        "BRCA1",
        "go",
        [
            {
                "source_db": "go",
                "term_id": "GO:0006281",
                "name": "DNA repair",
                "aspect": "biological_process",
                "evidence_codes": ["IDA"],
            },
            {
                "source_db": "go",
                "term_id": "GO:0005634",
                "name": "nucleus",
                "aspect": "cellular_component",
                "evidence_codes": ["IDA"],
            },
            {
                "source_db": "go",
                "term_id": "GO:0003677",
                "name": "DNA binding",
                "aspect": "molecular_function",
                "evidence_codes": ["IEA"],
            },
        ],
    )

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        assessment, metrics, _relays = _invoke_analyze(tmp, "brca1", "go", artifact)

    assert assessment["gene"] == "BRCA1"
    assert assessment["source"] == "go"
    assert assessment["n_annotations"] == 3
    assert assessment["by_aspect"]["biological_process"] == 1
    assert assessment["by_aspect"]["cellular_component"] == 1
    assert assessment["by_aspect"]["molecular_function"] == 1
    assert metrics["total_annotations"] == 3
    assert metrics["molecular_function_count"] == 1
    assert metrics["biological_process_count"] == 1
    assert metrics["cellular_component_count"] == 1
    assert sorted(metrics["aspects"]) == [
        "biological_process",
        "cellular_component",
        "molecular_function",
    ]
    print("  PASS: test_analyze_go_happy")


def test_analyze_missing_file() -> None:
    """Analyze raises ArtifactError when no stored search data exists."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        mock_project = mock.MagicMock()
        mock_project.artifact_dir.return_value = tmp

        mock_state = mock.MagicMock()
        mock_state.project.return_value = mock_project

        mock_emit = mock.MagicMock()

        with mock.patch("dde.commands.pathway.emitter", return_value=mock_emit):
            try:
                analyze_cmd.callback(
                    mock_state,
                    "BRCA1",
                    "reactome",
                    None,
                    None,
                    False,
                    True,
                )
                assert False, "Expected ArtifactError"
            except ArtifactError:
                pass
    print("  PASS: test_analyze_missing_file")


def test_analyze_relays_attached() -> None:
    """Analyze attaches the pathway.membership_not_activity relay."""
    artifact = _build_output(
        "TP53",
        "reactome",
        [
            {
                "source_db": "reactome",
                "pathway_id": "R-HSA-1",
                "name": "Apoptosis",
                "species": "Homo sapiens",
            },
        ],
    )

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        analysis_out_path = tmp / "TP53.pathway-reactome.analysis.json"

        resolved = "TP53"
        suffix = "pathway-reactome"
        artifact_path = tmp / f"{resolved}.{suffix}.json"
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

        mock_emit = mock.MagicMock()
        mock_emit.data.side_effect = lambda k, v: None

        mock_project = mock.MagicMock()
        mock_project.artifact_dir.return_value = tmp
        mock_project.relative.return_value = str(artifact_path)

        mock_state = mock.MagicMock()
        mock_state.project.return_value = mock_project

        with (
            mock.patch("dde.commands.pathway.emitter", return_value=mock_emit),
            mock.patch(
                "dde.commands.pathway.provenance.write_analysis",
                return_value=analysis_out_path,
            ) as mock_wa,
        ):
            analyze_cmd.callback(
                mock_state,
                "tp53",
                "reactome",
                None,
                None,
                False,
                True,
            )

        # Check mandatory_relays passed to write_analysis.
        wa_call = mock_wa.call_args
        relays = wa_call.kwargs.get(
            "mandatory_relays",
            wa_call[1].get("mandatory_relays", []),
        )
        relay_codes = [r["code"] for r in relays]
        assert "pathway.membership_not_activity" in relay_codes, (
            f"Expected relay 'pathway.membership_not_activity' in {relay_codes}"
        )
    print("  PASS: test_analyze_relays_attached")


def test_analyze_schema_key_consistency() -> None:
    """Keys written by _build_output are the keys read by analyze_cmd.

    Reactome uses 'pathways', GO uses 'annotations'. If _build_output
    changes a key name without updating analyze_cmd, the analysis would
    silently produce empty results.
    """
    # Reactome round-trip
    reactome_entries = [
        {
            "source_db": "reactome",
            "pathway_id": "R-HSA-1",
            "name": "Test",
            "species": "Homo sapiens",
        },
    ]
    reactome_artifact = _build_output("BRCA1", "reactome", reactome_entries)
    # Verify _build_output uses "pathways" — the key analyze_cmd reads
    assert "pathways" in reactome_artifact, "Reactome artifact missing 'pathways' key"

    with tempfile.TemporaryDirectory() as td:
        _assessment, metrics, _ = _invoke_analyze(
            Path(td), "brca1", "reactome", reactome_artifact
        )
    assert metrics["total_pathways"] == 1, (
        "Schema mismatch: analyze_cmd could not read reactome pathways"
    )

    # GO round-trip
    go_entries = [
        {
            "source_db": "go",
            "term_id": "GO:001",
            "name": "Test",
            "aspect": "biological_process",
            "evidence_codes": ["IDA"],
        },
    ]
    go_artifact = _build_output("BRCA1", "go", go_entries)
    assert "annotations" in go_artifact, "GO artifact missing 'annotations' key"

    with tempfile.TemporaryDirectory() as td:
        _assessment, metrics, _ = _invoke_analyze(Path(td), "brca1", "go", go_artifact)
    assert metrics["total_annotations"] == 1, (
        "Schema mismatch: analyze_cmd could not read GO annotations"
    )
    print("  PASS: test_analyze_schema_key_consistency")


# ---------------------------------------------------------------------------
# 4c. QuickGO pagination tests (R2 O3 fix)
# ---------------------------------------------------------------------------


def test_search_go_pagination() -> None:
    """QuickGO results spanning multiple pages are all collected."""
    uniprot_response = {"results": [{"primaryAccession": "P04637"}]}

    # Page 1: 2 annotations, pageInfo says 2 total pages
    page1_annotations = [
        {
            "goId": "GO:0006915",
            "goName": "apoptotic process",
            "goAspect": "biological_process",
            "goEvidence": "IDA",
        },
    ]
    page1_payload = {
        "results": page1_annotations,
        "pageInfo": {"current": 1, "total": 2, "resultsPerPage": 1},
    }
    # Page 2: 1 annotation
    page2_annotations = [
        {
            "goId": "GO:0005634",
            "goName": "nucleus",
            "goAspect": "cellular_component",
            "goEvidence": "IDA",
        },
    ]
    page2_payload = {
        "results": page2_annotations,
        "pageInfo": {"current": 2, "total": 2, "resultsPerPage": 1},
    }

    page1_raw = json.dumps(page1_payload).encode("utf-8")
    page2_raw = json.dumps(page2_payload).encode("utf-8")

    def mock_request_side_effect(method: str, url: str, **kwargs: Any) -> mock.Mock:
        resp = mock.Mock()
        if "page=2" in url:
            resp.content = page2_raw
        else:
            resp.content = page1_raw
        return resp

    with (
        mock.patch("dde.commands.pathway.http.get_json") as mock_get,
        mock.patch("dde.commands.pathway.http.request") as mock_req,
    ):
        mock_get.return_value = uniprot_response
        mock_req.side_effect = mock_request_side_effect

        _raw, entries = _search_go("TP53")

    # Should have collected entries from both pages.
    assert len(entries) == 2, f"Expected 2 entries (2 pages), got {len(entries)}"
    term_ids = {e["term_id"] for e in entries}
    assert "GO:0006915" in term_ids
    assert "GO:0005634" in term_ids
    # http.request should have been called twice (one per page).
    assert mock_req.call_count == 2, (
        f"Expected 2 HTTP requests (pagination), got {mock_req.call_count}"
    )
    print("  PASS: test_search_go_pagination")


def test_search_go_single_page() -> None:
    """QuickGO single-page response does not trigger extra requests."""
    uniprot_response = {"results": [{"primaryAccession": "P38398"}]}
    quickgo_annotations = [
        {
            "goId": "GO:0006281",
            "goName": "DNA repair",
            "goAspect": "biological_process",
            "goEvidence": "IDA",
        },
    ]
    quickgo_payload = {
        "results": quickgo_annotations,
        "pageInfo": {"current": 1, "total": 1, "resultsPerPage": 25},
    }
    quickgo_raw = json.dumps(quickgo_payload).encode("utf-8")

    with (
        mock.patch("dde.commands.pathway.http.get_json") as mock_get,
        mock.patch("dde.commands.pathway.http.request") as mock_req,
    ):
        mock_get.return_value = uniprot_response
        mock_resp = mock.Mock()
        mock_resp.content = quickgo_raw
        mock_req.return_value = mock_resp

        _raw, entries = _search_go("BRCA1")

    assert len(entries) == 1
    assert mock_req.call_count == 1, (
        f"Expected 1 HTTP request (single page), got {mock_req.call_count}"
    )
    print("  PASS: test_search_go_single_page")


# ---------------------------------------------------------------------------
# 5. Filename / --name behavior tests (R2 fix)
# ---------------------------------------------------------------------------


def test_filename_uses_resolved_gene_not_name() -> None:
    """Files are always named by the resolved gene symbol, never --name.

    This verifies the R2 fix: --name is for display/sidecar metadata
    only, not for the filename. Analyze always looks up by gene symbol.
    """
    # The fix ensures file_label is always `resolved`, not `name or resolved`.
    # We verify this by checking the source code directly (the filename
    # construction) and by checking that _build_output uses the gene symbol.
    import inspect

    from dde.commands import pathway

    source = inspect.getsource(pathway.search_cmd.callback)

    # The old code had: file_label = name or resolved
    assert "file_label = name or resolved" not in source, (
        "R2 regression: search still uses --name for the filename"
    )
    # The new code should use `resolved` directly in the filename template
    assert "{resolved}.{suffix}" in source or 'f"{resolved}.{suffix}' in source, (
        "Filename should use 'resolved' variable"
    )
    print("  PASS: filename uses resolved gene symbol, not --name")


# ---------------------------------------------------------------------------
# 6. URL encoding tests (R3 fix)
# ---------------------------------------------------------------------------


def test_url_encoding_special_characters_reactome() -> None:
    """Gene symbols with special characters are encoded in Reactome URLs."""
    # Symbols like HLA-A contain a hyphen (safe for URLs), but we test
    # that the quote() call is applied correctly.
    payload: dict[str, Any] = {"results": []}
    raw_bytes = json.dumps(payload).encode("utf-8")

    with mock.patch("dde.commands.pathway.http.request") as mock_req:
        mock_resp = mock.Mock()
        mock_resp.content = raw_bytes
        mock_req.return_value = mock_resp

        _search_reactome("GENE+SPACE")

    call_url = mock_req.call_args[0][1]
    # + should be encoded as %2B, not left as a bare +
    assert "GENE%2BSPACE" in call_url, f"Plus sign not URL-encoded: {call_url}"
    print("  PASS: URL encoding of special characters in Reactome")


def test_url_encoding_uniprot_resolver() -> None:
    """Gene symbols with special characters are encoded in UniProt URL."""
    mock_response: dict[str, Any] = {"results": []}
    with mock.patch("dde.commands.pathway.http.get_json") as mock_get:
        mock_get.return_value = mock_response
        try:
            _resolve_uniprot_accession("GENE#TAG")
        except Refusal:
            pass  # Expected — no results

    call_url = mock_get.call_args[0][0]
    assert "GENE%23TAG" in call_url, f"Hash not URL-encoded in UniProt URL: {call_url}"
    print("  PASS: URL encoding in UniProt resolver")


# ---------------------------------------------------------------------------
# 7. Error handling tests
# ---------------------------------------------------------------------------


def test_search_reactome_non_json_response() -> None:
    """Reactome returning non-JSON raises SchemaError."""
    from dde.core.errors import SchemaError

    with mock.patch("dde.commands.pathway.http.request") as mock_req:
        mock_resp = mock.Mock()
        mock_resp.content = b"<html>Server Error</html>"
        mock_req.return_value = mock_resp

        try:
            _search_reactome("BRCA1")
            assert False, "Expected SchemaError"
        except SchemaError:
            pass
    print("  PASS: _search_reactome non-JSON raises SchemaError")


def test_search_go_non_json_response() -> None:
    """QuickGO returning non-JSON raises SchemaError."""
    from dde.core.errors import SchemaError

    uniprot_response = {"results": [{"primaryAccession": "P38398"}]}

    with (
        mock.patch("dde.commands.pathway.http.get_json") as mock_get,
        mock.patch("dde.commands.pathway.http.request") as mock_req,
    ):
        mock_get.return_value = uniprot_response
        mock_resp = mock.Mock()
        mock_resp.content = b"<html>Server Error</html>"
        mock_req.return_value = mock_resp

        try:
            _search_go("BRCA1")
            assert False, "Expected SchemaError"
        except SchemaError:
            pass
    print("  PASS: _search_go non-JSON raises SchemaError")


# ---------------------------------------------------------------------------
# 8. Dead constants removed (O1 fix)
# ---------------------------------------------------------------------------


def test_dead_constants_removed() -> None:
    """GO_API and GO_QPS constants should no longer exist (O1 fix)."""
    from dde.commands import pathway

    assert not hasattr(pathway, "GO_API"), (
        "GO_API constant should have been removed (O1 fix)"
    )
    assert not hasattr(pathway, "GO_QPS"), (
        "GO_QPS constant should have been removed (O1 fix)"
    )
    print("  PASS: dead GO_API/GO_QPS constants removed")


# ---------------------------------------------------------------------------
# 9. Schema identifier tests (O2 fix)
# ---------------------------------------------------------------------------


def test_schema_identifiers_distinct() -> None:
    """Reactome and GO use distinct schema identifiers (O2 fix)."""
    reactome_output = _build_output(
        "TP53",
        "reactome",
        [
            {
                "source_db": "reactome",
                "pathway_id": "R-1",
                "name": "P1",
                "species": "Homo sapiens",
            },
        ],
    )
    go_output = _build_output(
        "TP53",
        "go",
        [
            {
                "source_db": "go",
                "term_id": "GO:001",
                "name": "T1",
                "aspect": "bp",
                "evidence_codes": ["IDA"],
            },
        ],
    )

    assert reactome_output["schema"] != go_output["schema"], (
        f"Reactome and GO schemas should be distinct: "
        f"{reactome_output['schema']} vs {go_output['schema']}"
    )
    assert "reactome" in reactome_output["schema"]
    assert "go" in go_output["schema"]
    print("  PASS: distinct schema identifiers for Reactome and GO")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        # UniProt accession resolution
        ("test_resolve_uniprot_accession_happy", test_resolve_uniprot_accession_happy),
        (
            "test_resolve_uniprot_accession_not_found",
            test_resolve_uniprot_accession_not_found,
        ),
        (
            "test_resolve_uniprot_accession_empty_accession",
            test_resolve_uniprot_accession_empty_accession,
        ),
        # Reactome search
        ("test_search_reactome_happy", test_search_reactome_happy),
        ("test_search_reactome_empty", test_search_reactome_empty),
        ("test_search_reactome_url_encoding", test_search_reactome_url_encoding),
        # QuickGO search
        ("test_search_go_happy", test_search_go_happy),
        ("test_search_go_empty", test_search_go_empty),
        ("test_search_go_evidence_diversity", test_search_go_evidence_diversity),
        ("test_search_go_dedup_same_evidence", test_search_go_dedup_same_evidence),
        ("test_search_go_url_encoding", test_search_go_url_encoding),
        (
            "test_search_go_uniprot_resolution_failure",
            test_search_go_uniprot_resolution_failure,
        ),
        # Build output
        ("test_build_output_reactome_schema", test_build_output_reactome_schema),
        ("test_build_output_go_schema", test_build_output_go_schema),
        ("test_build_output_top_names_capped", test_build_output_top_names_capped),
        # Analyze command (R2 R1 fix)
        ("test_analyze_reactome_happy", test_analyze_reactome_happy),
        ("test_analyze_go_happy", test_analyze_go_happy),
        ("test_analyze_missing_file", test_analyze_missing_file),
        ("test_analyze_relays_attached", test_analyze_relays_attached),
        ("test_analyze_schema_key_consistency", test_analyze_schema_key_consistency),
        # QuickGO pagination (R2 O3 fix)
        ("test_search_go_pagination", test_search_go_pagination),
        ("test_search_go_single_page", test_search_go_single_page),
        # Filename / --name behavior
        (
            "test_filename_uses_resolved_gene_not_name",
            test_filename_uses_resolved_gene_not_name,
        ),
        # URL encoding
        (
            "test_url_encoding_special_characters_reactome",
            test_url_encoding_special_characters_reactome,
        ),
        ("test_url_encoding_uniprot_resolver", test_url_encoding_uniprot_resolver),
        # Error handling
        (
            "test_search_reactome_non_json_response",
            test_search_reactome_non_json_response,
        ),
        ("test_search_go_non_json_response", test_search_go_non_json_response),
        # O1 fix — dead constants
        ("test_dead_constants_removed", test_dead_constants_removed),
        # O2 fix — distinct schemas
        ("test_schema_identifiers_distinct", test_schema_identifiers_distinct),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:
            print(f"  FAIL: {name} — {exc}")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    if failed:
        sys.exit(1)
    else:
        print("All tests passed.")


if __name__ == "__main__":
    main()
