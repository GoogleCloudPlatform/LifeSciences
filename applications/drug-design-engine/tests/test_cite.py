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

"""Tests for the cite command group (citation verification).

Covers:
  - Citation extraction (structured JSON, regex fallback, mixed)
  - Title similarity classification (verified, suspect, phantom)
  - Manifest schema correctness
  - §3.3 regression: all_verified requires suspect == 0
  - --tolerance flag changes citation status
  - No extractable references → exit 0, no-citations-found verdict
  - Phase-2 contract: analyze with network raises PhaseContractError
  - Overwrite guard on analyze
  - dde validate sees new sidecars
  - Registration checks: relay codes and threshold set
  - Relay guards: each relay fires conditionally, not unconditionally
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

from dde.commands.cite import (
    _classify,
    _extract_citations,
    _resolve_citation,
    _slug,
    _title_similarity,
)
from dde.core import provenance

# ---------------------------------------------------------------------------
# Helper: project setup and mock HTTP
# ---------------------------------------------------------------------------


def _make_project(base: Path) -> Path:
    """Create a minimal dde project directory."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    (project / "raw" / "literature").mkdir(parents=True, exist_ok=True)
    return project


def _mock_http_response(body: dict[str, Any], status_code: int = 200) -> mock.Mock:
    """Build a mock HTTP response."""
    resp = mock.Mock()
    resp.status_code = status_code
    resp.content = json.dumps(body).encode("utf-8")
    resp.json = lambda: body
    resp.text = json.dumps(body)
    return resp


def _write_document(project: Path, name: str, content: str) -> Path:
    """Write a document file into the project directory."""
    path = project / name
    path.write_text(content, encoding="utf-8")
    return path


def _write_json_document(project: Path, name: str, data: dict[str, Any]) -> Path:
    path = project / name
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _crossref_found(title: str) -> dict[str, Any]:
    """Canned CrossRef found response."""
    return {
        "status": "ok",
        "message": {
            "title": [title],
            "DOI": "10.1234/test",
        },
    }


def _crossref_not_found() -> dict[str, Any]:
    return {"status": "ok", "message": {}}


def _epmc_found(title: str, pmid: str = "12345678") -> dict[str, Any]:
    return {
        "resultList": {
            "result": [
                {"title": title, "id": pmid, "pmid": pmid},
            ]
        },
        "hitCount": 1,
    }


def _epmc_not_found() -> dict[str, Any]:
    return {"resultList": {"result": []}, "hitCount": 0}


def _ctgov_found(title: str, nct_id: str = "NCT01234567") -> dict[str, Any]:
    return {
        "protocolSection": {
            "identificationModule": {
                "nctId": nct_id,
                "briefTitle": title,
            },
            "statusModule": {"overallStatus": "COMPLETED"},
        }
    }


def _ctgov_not_found() -> dict[str, Any]:
    return {"_not_found": True, "http_status": 404}


# ---------------------------------------------------------------------------
# Build a side_effect that routes by URL
# ---------------------------------------------------------------------------


def _verify_side_effect(
    doi_responses: dict[str, dict[str, Any]] | None = None,
    pmid_responses: dict[str, dict[str, Any]] | None = None,
    nct_responses: dict[str, dict[str, Any]] | None = None,
    default_response: dict[str, Any] | None = None,
    default_status: int = 200,
) -> Any:
    """Build a flexible side_effect function for http.request."""
    doi_responses = doi_responses or {}
    pmid_responses = pmid_responses or {}
    nct_responses = nct_responses or {}
    if default_response is None:
        default_response = _epmc_not_found()

    def side_effect(method: str, url: str, **kwargs: Any) -> mock.Mock:
        # CrossRef DOI lookup
        if "api.crossref.org" in url:
            for doi, resp in doi_responses.items():
                if doi in url:
                    return _mock_http_response(resp)
            return _mock_http_response(_crossref_not_found(), 404)

        # Europe PMC
        if "europepmc" in url:
            for pmid, resp in pmid_responses.items():
                if pmid in url:
                    return _mock_http_response(resp)
            return _mock_http_response(default_response, default_status)

        # ClinicalTrials.gov
        if "clinicaltrials.gov" in url:
            for nct, resp in nct_responses.items():
                if nct in url:
                    return _mock_http_response(resp)
            return _mock_http_response(_ctgov_not_found(), 404)

        return _mock_http_response(default_response, default_status)

    return side_effect


# ---------------------------------------------------------------------------
# 1. Extraction tests
# ---------------------------------------------------------------------------


def test_classify_doi() -> None:
    kind, value = _classify("10.1056/NEJMoa1505270")
    assert kind == "doi" and value == "10.1056/NEJMoa1505270"
    print("  PASS: classify DOI")


def test_classify_pmid() -> None:
    kind, value = _classify("PMID:12345678")
    assert kind == "pmid" and value == "12345678"
    print("  PASS: classify PMID")


def test_classify_pmcid() -> None:
    kind, value = _classify("PMC1234567")
    assert kind == "pmcid" and value == "PMC1234567"
    print("  PASS: classify PMCID")


def test_classify_nct() -> None:
    kind, value = _classify("NCT01234567")
    assert kind == "nct" and value == "NCT01234567"
    print("  PASS: classify NCT")


def test_classify_title() -> None:
    kind, _value = _classify("Some paper about proteins")
    assert kind == "title"
    print("  PASS: classify title")


def test_extract_structured_json() -> None:
    """Structured JSON with a citations array extracts correctly."""
    with tempfile.TemporaryDirectory() as td:
        doc_path = Path(td) / "doc.json"
        doc = {
            "citations": [
                {"doi": "10.1056/NEJMoa1505270", "title": "PALOMA-3 Trial"},
                "PMID:25332249",
                "NCT01942135",
            ]
        }
        doc_path.write_text(json.dumps(doc), encoding="utf-8")
        citations, basis = _extract_citations(doc_path)
        assert basis == "structured"
        assert len(citations) == 3
        assert citations[0]["kind"] == "doi"
        assert citations[1]["kind"] == "pmid"
        assert citations[2]["kind"] == "nct"
    print("  PASS: extract structured JSON")


def test_extract_hypex_evidence_json() -> None:
    """Hypex evidence[].lit_id is a first-class structured citation source."""
    with tempfile.TemporaryDirectory() as td:
        doc_path = Path(td) / "H-0001.json"
        doc = {
            "evidence": [
                {
                    "lit_id": "PMID:25332249",
                    "role": "supports",
                    "note": "Supports the mechanism.",
                },
                {
                    "lit_id": "10.1056/NEJMoa1505270",
                    "role": "constrains",
                    "note": "Constrains the population.",
                },
            ]
        }
        doc_path.write_text(json.dumps(doc), encoding="utf-8")
        citations, basis = _extract_citations(doc_path)
        assert basis == "structured"
        assert [citation["kind"] for citation in citations] == ["pmid", "doi"]
        assert [citation["raw_id"] for citation in citations] == [
            "PMID:25332249",
            "10.1056/NEJMoa1505270",
        ]
    print("  PASS: extract Hypex evidence JSON")


def test_extract_regex_fallback() -> None:
    """Plain text with identifiers uses regex fallback."""
    with tempfile.TemporaryDirectory() as td:
        doc_path = Path(td) / "doc.md"
        doc_path.write_text(
            "See 10.1056/NEJMoa1505270 and also PMID:25332249.\n"
            "Trial NCT01942135 is relevant.\n",
            encoding="utf-8",
        )
        citations, basis = _extract_citations(doc_path)
        assert basis == "regex-fallback"
        assert len(citations) >= 3
    print("  PASS: extract regex fallback")


def test_extract_no_citations() -> None:
    """A document with no citations returns empty list."""
    with tempfile.TemporaryDirectory() as td:
        doc_path = Path(td) / "doc.txt"
        doc_path.write_text("Hello world, no citations here.", encoding="utf-8")
        citations, basis = _extract_citations(doc_path)
        assert len(citations) == 0
        assert basis == "regex-fallback"
    print("  PASS: extract no citations")


# ---------------------------------------------------------------------------
# 2. Title similarity tests
# ---------------------------------------------------------------------------


def test_title_similarity_exact() -> None:
    sim = _title_similarity("Exact Title", "Exact Title")
    assert sim == 1.0, f"Expected 1.0, got {sim}"
    print("  PASS: title similarity exact")


def test_title_similarity_none() -> None:
    sim = _title_similarity(None, "Some Title")
    assert sim == 0.0
    print("  PASS: title similarity None")


# ---------------------------------------------------------------------------
# 3. §3.3 regression: suspect title match is NOT verified
# ---------------------------------------------------------------------------


def test_suspect_not_verified_regression() -> None:
    """A document with a known-good, phantom, and suspect reference yields
    correct counts and all_verified == false. §3.3 defect test."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        doc = {
            "citations": [
                {"doi": "10.1056/good-doi", "title": "Exact matching title here"},
                {"doi": "10.9999/phantom-doi", "title": "A phantom paper"},
                {"doi": "10.5555/suspect-doi", "title": "Slightly different title"},
            ]
        }
        doc_path = _write_json_document(project, "test-doc.json", doc)

        side_effect = _verify_side_effect(
            doi_responses={
                "good-doi": _crossref_found("Exact matching title here"),
                "phantom-doi": _crossref_not_found(),
                "suspect-doi": _crossref_found(
                    "A quite different title that partially matches"
                ),
            },
        )

        runner = CliRunner()
        with mock.patch("dde.commands.cite.http.request") as mock_req:
            mock_req.side_effect = side_effect
            result = runner.invoke(
                cli,
                ["--project", str(project), "cite", "verify", str(doc_path)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        manifest_path = project / "raw" / "literature" / "test-doc.citations.json"
        assert manifest_path.is_file(), "Manifest not written"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        summary = manifest["summary"]
        assert summary["phantom"] >= 1, f"Expected >=1 phantom, got {summary}"
        # §3.3 CRITICAL: all_verified must be false when suspect > 0
        assert summary["all_verified"] is False, (
            f"CRITICAL §3.3: all_verified should be False, got {summary}"
        )

        # No citation with suspect-title-match should have status "verified"
        for c in manifest["citations"]:
            if c["status"] == "suspect-title-match":
                assert c["status"] != "verified", (
                    f"§3.3 REGRESSION: suspect citation has status 'verified': {c}"
                )
    print("  PASS: §3.3 regression — suspect is NOT verified")


# ---------------------------------------------------------------------------
# 3b. §3.3 isolation: suspect-only fixture (no phantoms, no network errors)
# ---------------------------------------------------------------------------


def test_suspect_only_not_verified() -> None:
    """With ONLY resolvable citations (no phantoms, no network errors), a
    suspect title match must still prevent all_verified from being True.
    This isolates the §3.3 invariant from confounding phantom/error counts."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        doc = {
            "citations": [
                {"doi": "10.1056/verified-ok", "title": "Exact matching title here"},
                {
                    "doi": "10.5555/suspect-range",
                    "title": "A study on therapeutic protein binding efficacy",
                },
            ]
        }
        doc_path = _write_json_document(project, "suspect-only.json", doc)

        # The suspect DOI resolves, but with a title that is only partially
        # similar (in the suspect range: >= 0.45 and < 0.75).
        side_effect = _verify_side_effect(
            doi_responses={
                "verified-ok": _crossref_found("Exact matching title here"),
                "suspect-range": _crossref_found(
                    "A review of therapeutic protein binding studies"
                ),
            },
        )

        runner = CliRunner()
        with mock.patch("dde.commands.cite.http.request") as mock_req:
            mock_req.side_effect = side_effect
            result = runner.invoke(
                cli,
                ["--project", str(project), "cite", "verify", str(doc_path)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        manifest_path = project / "raw" / "literature" / "suspect-only.citations.json"
        assert manifest_path.is_file(), "Manifest not written"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        summary = manifest["summary"]
        # No phantoms, no unverified — only verified and suspect
        assert summary["phantom"] == 0, f"Expected 0 phantoms, got {summary}"
        assert summary["unverified"] == 0, f"Expected 0 unverified, got {summary}"
        assert summary["suspect"] >= 1, f"Expected >=1 suspect, got {summary}"

        # §3.3 CRITICAL: all_verified must be False when suspect > 0
        assert summary["all_verified"] is False, (
            f"CRITICAL §3.3: all_verified should be False with suspect > 0, "
            f"got {summary}"
        )

        # No citation in the suspect similarity range should have status "verified"
        for c in manifest["citations"]:
            sim = c.get("title_similarity", 0.0)
            if 0.45 <= sim < 0.75:
                assert c["status"] != "verified", (
                    f"§3.3 REGRESSION: citation with title_similarity {sim} "
                    f"has status 'verified': {c}"
                )
    print("  PASS: §3.3 isolation — suspect-only prevents all_verified")


# ---------------------------------------------------------------------------
# 4. --tolerance flag changes status
# ---------------------------------------------------------------------------


def test_tolerance_flag_changes_status() -> None:
    """Raising --tolerance from 0.75 to 0.95 changes at least one status."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        # A citation whose title similarity is ~0.8 — verified at 0.75 default,
        # but suspect at 0.95.
        doc = {
            "citations": [
                {
                    "doi": "10.1056/tolerance-test",
                    "title": "Study of drug effects in patients with cancer treatment",
                },
            ]
        }
        doc_path = _write_json_document(project, "tol-doc.json", doc)

        similar_title = (
            "Study of drug effects in patients undergoing cancer treatment today"
        )
        side_effect = _verify_side_effect(
            doi_responses={
                "tolerance-test": _crossref_found(similar_title),
            },
        )

        runner = CliRunner()

        # Run at default tolerance (0.75)
        with mock.patch("dde.commands.cite.http.request") as mock_req:
            mock_req.side_effect = side_effect
            result1 = runner.invoke(
                cli,
                ["--project", str(project), "cite", "verify", str(doc_path)],
                catch_exceptions=False,
            )
        assert result1.exit_code == 0

        m1 = json.loads(
            (project / "raw" / "literature" / "tol-doc.citations.json").read_text(
                encoding="utf-8"
            )
        )
        status1 = m1["citations"][0]["status"]

        # Remove outputs to re-run
        for f in (project / "raw" / "literature").glob("tol-doc.*"):
            f.unlink()

        # Run at strict tolerance (0.95)
        with mock.patch("dde.commands.cite.http.request") as mock_req:
            mock_req.side_effect = side_effect
            result2 = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "cite",
                    "verify",
                    str(doc_path),
                    "--tolerance",
                    "0.95",
                ],
                catch_exceptions=False,
            )
        assert result2.exit_code == 0

        m2 = json.loads(
            (project / "raw" / "literature" / "tol-doc.citations.json").read_text(
                encoding="utf-8"
            )
        )
        status2 = m2["citations"][0]["status"]

        assert status1 != status2, (
            f"Expected different status at different tolerance: "
            f"default={status1}, strict={status2}"
        )
    print("  PASS: --tolerance flag changes status")


# ---------------------------------------------------------------------------
# 5. No citations → exit 0, no-citations-found
# ---------------------------------------------------------------------------


def test_no_citations_exit_zero() -> None:
    """A document with no extractable references exits 0, verdict no-citations-found."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        doc_path = _write_document(project, "empty.txt", "No references at all.")

        runner = CliRunner()
        with mock.patch("dde.commands.cite.http.request"):
            result = runner.invoke(
                cli,
                ["--project", str(project), "cite", "verify", str(doc_path)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        manifest_path = project / "raw" / "literature" / "empty.citations.json"
        assert manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["summary"]["total"] == 0

        # Now run analyze
        result2 = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "cite",
                "analyze",
                "--from",
                str(project / "raw" / "literature"),
            ],
            catch_exceptions=False,
        )
        assert result2.exit_code == 0

        # Find the analysis file
        analysis_files = list((project / "raw" / "literature").glob("*.analysis.json"))
        assert len(analysis_files) >= 1
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))
        assert analysis["assessment"]["verdict"] == "no-citations-found"

        # Check extraction_incomplete relay fires exactly once (not double-fired)
        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "cite.extraction_incomplete" in relay_codes
        extraction_incomplete_count = relay_codes.count("cite.extraction_incomplete")
        assert extraction_incomplete_count == 1, (
            f"cite.extraction_incomplete should fire exactly once, "
            f"fired {extraction_incomplete_count} times"
        )
    print("  PASS: no citations → exit 0, no-citations-found")


# ---------------------------------------------------------------------------
# 6. Phase-2 contract: analyze raises PhaseContractError with network
# ---------------------------------------------------------------------------


def test_analyze_phase_two_contract() -> None:
    """dde cite analyze with network available raises PhaseContractError.

    The enforce_phase_two guard on the CLI forbids network access during
    analyze. We verify this by checking the guard is wired.
    """
    import click
    from dde.cli import cli

    # Find the analyze command
    cite_group = cli.commands.get("cite")
    assert cite_group is not None, "cite command not registered"
    assert isinstance(cite_group, click.Group)
    analyze = cite_group.commands.get("analyze")
    assert analyze is not None, "analyze subcommand not registered"

    # Check that the phase-two guard is applied
    callback = analyze.callback
    assert callback is not None
    assert getattr(callback, "_phase_two_guarded", False), (
        "analyze command is not phase-two guarded"
    )
    print("  PASS: analyze is phase-two guarded")


# ---------------------------------------------------------------------------
# 7. Overwrite guard on analyze
# ---------------------------------------------------------------------------


def test_analyze_overwrite_guard() -> None:
    """Re-running analyze with different threshold refuses without --overwrite."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        lit_dir = project / "raw" / "literature"

        # Write a canned manifest
        manifest = {
            "schema": "dde.citation-manifest.v1",
            "target_file": "test.json",
            "verified_at": "2026-09-08T00:00:00Z",
            "verifier": "dde-cite/0.3.0",
            "summary": {
                "total": 2,
                "verified": 1,
                "suspect": 1,
                "phantom": 0,
                "unverified": 0,
                "all_verified": False,
            },
            "extraction_basis": "structured",
            "citations": [
                {
                    "id": "10.1056/good",
                    "raw_id": "10.1056/good",
                    "source": "crossref",
                    "status": "verified",
                    "reason": "ok",
                    "claimed_title": "Title",
                    "resolved_title": "Title",
                    "title_similarity": 1.0,
                    "verified_via": "https://example.com",
                    "url": "https://doi.org/10.1056/good",
                    "error": None,
                },
                {
                    "id": "10.1056/suspect",
                    "raw_id": "10.1056/suspect",
                    "source": "crossref",
                    "status": "suspect-title-match",
                    "reason": "title_mismatch",
                    "claimed_title": "Claimed",
                    "resolved_title": "Different",
                    "title_similarity": 0.55,
                    "verified_via": "https://example.com",
                    "url": "https://doi.org/10.1056/suspect",
                    "error": None,
                },
            ],
        }
        (lit_dir / "test.citations.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

        runner = CliRunner()

        # First run — should succeed
        result1 = runner.invoke(
            cli,
            ["--project", str(project), "cite", "analyze", "--from", str(lit_dir)],
            catch_exceptions=False,
        )
        assert result1.exit_code == 0, f"First run failed: {result1.output}"
        analysis_path = lit_dir / "test.analysis.json"
        assert analysis_path.is_file()

        # Second identical run — should succeed (same verdict, no change)
        result2 = runner.invoke(
            cli,
            ["--project", str(project), "cite", "analyze", "--from", str(lit_dir)],
            catch_exceptions=False,
        )
        assert result2.exit_code == 0, f"Second identical run failed: {result2.output}"

        # Verify written_by is preserved (not overwritten by re-run)
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        original_writer = analysis.get("written_by")
        assert original_writer is not None
    print("  PASS: analyze overwrite guard")


# ---------------------------------------------------------------------------
# 7b. Overwrite guard — refusal path
# ---------------------------------------------------------------------------


def test_analyze_overwrite_refusal() -> None:
    """Re-running analyze after modifying the manifest (different verdict)
    refuses without --overwrite."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        lit_dir = project / "raw" / "literature"

        # Write a canned manifest — 1 suspect, 0 phantom
        manifest = {
            "schema": "dde.citation-manifest.v1",
            "target_file": "refusal-test.json",
            "verified_at": "2026-09-08T00:00:00Z",
            "verifier": "dde-cite/0.3.0",
            "summary": {
                "total": 2,
                "verified": 1,
                "suspect": 1,
                "phantom": 0,
                "unverified": 0,
                "all_verified": False,
            },
            "extraction_basis": "structured",
            "citations": [
                {
                    "id": "10.1056/good",
                    "raw_id": "10.1056/good",
                    "source": "crossref",
                    "status": "verified",
                    "reason": "ok",
                    "claimed_title": "Title",
                    "resolved_title": "Title",
                    "title_similarity": 1.0,
                    "verified_via": "https://example.com",
                    "url": "https://doi.org/10.1056/good",
                    "error": None,
                },
                {
                    "id": "10.1056/suspect",
                    "raw_id": "10.1056/suspect",
                    "source": "crossref",
                    "status": "suspect-title-match",
                    "reason": "title_mismatch",
                    "claimed_title": "Claimed",
                    "resolved_title": "Different",
                    "title_similarity": 0.55,
                    "verified_via": "https://example.com",
                    "url": "https://doi.org/10.1056/suspect",
                    "error": None,
                },
            ],
        }
        manifest_path = lit_dir / "refusal-test.citations.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

        runner = CliRunner()

        # First run — should succeed
        result1 = runner.invoke(
            cli,
            ["--project", str(project), "cite", "analyze", "--from", str(lit_dir)],
            catch_exceptions=False,
        )
        assert result1.exit_code == 0, f"First run failed: {result1.output}"
        analysis_path = lit_dir / "refusal-test.analysis.json"
        assert analysis_path.is_file()

        # Modify the manifest to produce a DIFFERENT verdict — add a phantom
        manifest["summary"]["phantom"] = 2
        manifest["summary"]["total"] = 4
        manifest["citations"].append(
            {
                "id": "10.9999/phantom1",
                "raw_id": "10.9999/phantom1",
                "source": "crossref",
                "status": "phantom",
                "reason": "not_found",
                "claimed_title": "Phantom Paper",
                "resolved_title": None,
                "title_similarity": 0.0,
                "verified_via": "https://example.com",
                "url": None,
                "error": None,
            }
        )
        manifest["citations"].append(
            {
                "id": "10.9999/phantom2",
                "raw_id": "10.9999/phantom2",
                "source": "crossref",
                "status": "phantom",
                "reason": "not_found",
                "claimed_title": "Another Phantom",
                "resolved_title": None,
                "title_similarity": 0.0,
                "verified_via": "https://example.com",
                "url": None,
                "error": None,
            }
        )
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

        # Second run WITHOUT --overwrite — should fail (Refusal)
        result2 = runner.invoke(
            cli,
            ["--project", str(project), "cite", "analyze", "--from", str(lit_dir)],
            catch_exceptions=True,
        )
        assert result2.exit_code != 0, (
            f"Expected non-zero exit on conflicting re-run without --overwrite, "
            f"got exit {result2.exit_code}\n{result2.output}"
        )

        # Third run WITH --overwrite — should succeed
        result3 = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "cite",
                "analyze",
                "--from",
                str(lit_dir),
                "--overwrite",
            ],
            catch_exceptions=False,
        )
        assert result3.exit_code == 0, (
            f"Re-run with --overwrite should succeed, "
            f"got exit {result3.exit_code}\n{result3.output}"
        )
    print("  PASS: analyze overwrite refusal path")


# ---------------------------------------------------------------------------
# 8. Registration checks
# ---------------------------------------------------------------------------


def test_relay_codes_registered() -> None:
    """All four cite.* relay codes are in provenance.RELAY_CODES."""
    expected = [
        "cite.phantom_citation",
        "cite.suspect_title_match",
        "cite.unresolved_offline",
        "cite.extraction_incomplete",
    ]
    for code in expected:
        assert code in provenance.RELAY_CODES, f"{code} not registered in RELAY_CODES"
    print("  PASS: all cite.* relay codes registered")


def test_threshold_set_registered() -> None:
    """Threshold set citation-verification is in declared_sets()."""
    from dde.core.thresholds import UNRESOLVED, declared_sets

    sets = declared_sets()
    assert "citation-verification" in sets, (
        f"citation-verification not in declared sets: {sorted(sets.keys())}"
    )
    ts = sets["citation-verification"]
    assert ts.version == "1.0"
    assert ts.values["title_match_tolerance"] == 0.75
    assert ts.values["title_suspect_floor"] == 0.45
    assert ts.values["max_phantom_citations"] == 0
    assert ts.values["max_suspect_citations"] is UNRESOLVED
    print("  PASS: threshold set citation-verification registered")


# ---------------------------------------------------------------------------
# 9. dde validate sees the new sidecars
# ---------------------------------------------------------------------------


def test_validate_recognises_sidecars() -> None:
    """dde validate's _is_sidecar and _is_analysis recognise cite files."""
    from dde.commands.validate import _is_analysis, _is_sidecar

    assert _is_sidecar("test-doc.meta.json")
    assert _is_analysis("test-doc.analysis.json")
    # citations.json is not a sidecar or analysis — it's the artifact
    assert not _is_sidecar("test-doc.citations.json")
    assert not _is_analysis("test-doc.citations.json")
    print("  PASS: validate recognises cite sidecars")


# ---------------------------------------------------------------------------
# 10. Relay guard: each code fires conditionally (criterion 27)
# ---------------------------------------------------------------------------


def test_relay_phantom_does_not_fire_unconditionally() -> None:
    """cite.phantom_citation does NOT fire when there are no phantoms."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        doc = {
            "citations": [{"doi": "10.1056/verified-doi", "title": "A correct title"}]
        }
        doc_path = _write_json_document(project, "no-phantom.json", doc)

        side_effect = _verify_side_effect(
            doi_responses={
                "verified-doi": _crossref_found("A correct title"),
            },
        )

        runner = CliRunner()
        with mock.patch("dde.commands.cite.http.request") as mock_req:
            mock_req.side_effect = side_effect
            result = runner.invoke(
                cli,
                ["--project", str(project), "cite", "verify", str(doc_path)],
                catch_exceptions=False,
            )
        assert result.exit_code == 0

        meta_path = project / "raw" / "literature" / "no-phantom.meta.json"
        assert meta_path.is_file()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "cite.phantom_citation" not in relay_codes, (
            "phantom_citation fired unconditionally"
        )
    print("  PASS: cite.phantom_citation does NOT fire unconditionally")


def test_relay_suspect_does_not_fire_unconditionally() -> None:
    """cite.suspect_title_match does NOT fire when there are no suspects."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        doc = {"citations": [{"doi": "10.1056/good-title", "title": "Exact title"}]}
        doc_path = _write_json_document(project, "no-suspect.json", doc)

        side_effect = _verify_side_effect(
            doi_responses={
                "good-title": _crossref_found("Exact title"),
            },
        )

        runner = CliRunner()
        with mock.patch("dde.commands.cite.http.request") as mock_req:
            mock_req.side_effect = side_effect
            result = runner.invoke(
                cli,
                ["--project", str(project), "cite", "verify", str(doc_path)],
                catch_exceptions=False,
            )
        assert result.exit_code == 0

        meta_path = project / "raw" / "literature" / "no-suspect.meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "cite.suspect_title_match" not in relay_codes
    print("  PASS: cite.suspect_title_match does NOT fire unconditionally")


def test_relay_unresolved_does_not_fire_unconditionally() -> None:
    """cite.unresolved_offline does NOT fire when everything resolves."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        doc = {"citations": [{"doi": "10.1056/ok-doi", "title": "Fine title"}]}
        doc_path = _write_json_document(project, "no-unresolved.json", doc)

        side_effect = _verify_side_effect(
            doi_responses={
                "ok-doi": _crossref_found("Fine title"),
            },
        )

        runner = CliRunner()
        with mock.patch("dde.commands.cite.http.request") as mock_req:
            mock_req.side_effect = side_effect
            result = runner.invoke(
                cli,
                ["--project", str(project), "cite", "verify", str(doc_path)],
                catch_exceptions=False,
            )
        assert result.exit_code == 0

        meta_path = project / "raw" / "literature" / "no-unresolved.meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "cite.unresolved_offline" not in relay_codes
    print("  PASS: cite.unresolved_offline does NOT fire unconditionally")


def test_relay_extraction_does_not_fire_on_structured() -> None:
    """cite.extraction_incomplete does NOT fire when extraction_basis is structured."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        doc = {
            "citations": [{"doi": "10.1056/struct-doi", "title": "Structured title"}]
        }
        doc_path = _write_json_document(project, "structured-only.json", doc)

        side_effect = _verify_side_effect(
            doi_responses={
                "struct-doi": _crossref_found("Structured title"),
            },
        )

        runner = CliRunner()
        with mock.patch("dde.commands.cite.http.request") as mock_req:
            mock_req.side_effect = side_effect
            result = runner.invoke(
                cli,
                ["--project", str(project), "cite", "verify", str(doc_path)],
                catch_exceptions=False,
            )
        assert result.exit_code == 0

        meta_path = project / "raw" / "literature" / "structured-only.meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "cite.extraction_incomplete" not in relay_codes, (
            "extraction_incomplete should not fire on structured extraction"
        )
    print("  PASS: cite.extraction_incomplete does NOT fire on structured")


# ---------------------------------------------------------------------------
# 11. Manifest schema correctness
# ---------------------------------------------------------------------------


def test_manifest_schema() -> None:
    """Verify manifest has correct schema and all required fields."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        doc = {"citations": [{"doi": "10.1056/schema-test", "title": "Schema Test"}]}
        doc_path = _write_json_document(project, "schema-test.json", doc)

        side_effect = _verify_side_effect(
            doi_responses={"schema-test": _crossref_found("Schema Test")},
        )

        runner = CliRunner()
        with mock.patch("dde.commands.cite.http.request") as mock_req:
            mock_req.side_effect = side_effect
            result = runner.invoke(
                cli,
                ["--project", str(project), "cite", "verify", str(doc_path)],
                catch_exceptions=False,
            )
        assert result.exit_code == 0

        manifest_path = project / "raw" / "literature" / "schema-test.citations.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        # Required top-level fields
        assert manifest["schema"] == "dde.citation-manifest.v1"
        assert "target_file" in manifest
        assert "verified_at" in manifest
        assert "verifier" in manifest
        assert "summary" in manifest
        assert "extraction_basis" in manifest
        assert "citations" in manifest

        # Summary fields
        summary = manifest["summary"]
        for field in (
            "total",
            "verified",
            "suspect",
            "phantom",
            "unverified",
            "all_verified",
        ):
            assert field in summary, f"Missing summary field: {field}"

        # Citation record fields
        if manifest["citations"]:
            c = manifest["citations"][0]
            for field in (
                "id",
                "raw_id",
                "source",
                "status",
                "reason",
                "claimed_title",
                "resolved_title",
                "title_similarity",
                "verified_via",
                "url",
                "error",
            ):
                assert field in c, f"Missing citation field: {field}"
    print("  PASS: manifest schema correctness")


# ---------------------------------------------------------------------------
# 12. Slug generation
# ---------------------------------------------------------------------------


def test_slug() -> None:
    assert _slug("test-document") == "test-document"
    result = _slug("My Paper (2024)")
    assert result.startswith("my-paper-"), f"Got {result}"
    assert len(_slug("a" * 200)) <= 80
    assert _slug("") == "cite"
    print("  PASS: slug generation")


# ---------------------------------------------------------------------------
# 13. Regression: #179 — Title query injection / double-quote sanitization
# ---------------------------------------------------------------------------


def test_title_double_quotes_sanitized() -> None:
    """Double quotes in a title must be sanitized before querying ePMC.

    Before the fix, internal double quotes in a title were passed
    unescaped to Europe PMC's query parser, causing HTTP 400 errors
    that were misclassified as network_error/timeout.
    """
    citation = {
        "kind": "title",
        "normalised": 'Study on "target" binding and efficacy',
        "raw_id": 'Study on "target" binding and efficacy',
        "claimed_title": 'Study on "target" binding and efficacy',
    }

    # Mock _resolve_epmc to capture the query string
    captured_queries: list[str] = []
    original_resolve_epmc = None

    import dde.commands.cite as cite_module

    original_resolve_epmc = cite_module._resolve_epmc

    def mock_resolve_epmc(query: str, source_label: str) -> dict[str, Any]:
        captured_queries.append(query)
        return {"found": False, "source": "epmc"}

    try:
        cite_module._resolve_epmc = mock_resolve_epmc
        _resolve_citation(citation, 0.85, 0.60)
    finally:
        cite_module._resolve_epmc = original_resolve_epmc

    assert len(captured_queries) == 1, (
        f"Expected exactly one ePMC query, got {len(captured_queries)}"
    )
    query = captured_queries[0]
    # The query must not contain unescaped internal double quotes
    # that would break the ePMC query parser
    inner = query.split('TITLE:"', 1)[1].rsplit('"', 1)[0]
    assert '"' not in inner, f"Double quotes in title were not sanitized: {query!r}"
    print("  PASS: title double quotes sanitized (#179)")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        # Extraction
        ("test_classify_doi", test_classify_doi),
        ("test_classify_pmid", test_classify_pmid),
        ("test_classify_pmcid", test_classify_pmcid),
        ("test_classify_nct", test_classify_nct),
        ("test_classify_title", test_classify_title),
        ("test_extract_structured_json", test_extract_structured_json),
        ("test_extract_hypex_evidence_json", test_extract_hypex_evidence_json),
        ("test_extract_regex_fallback", test_extract_regex_fallback),
        ("test_extract_no_citations", test_extract_no_citations),
        # Title similarity
        ("test_title_similarity_exact", test_title_similarity_exact),
        ("test_title_similarity_none", test_title_similarity_none),
        # §3.3 regression (most important test)
        ("test_suspect_not_verified_regression", test_suspect_not_verified_regression),
        # §3.3 isolation — suspect-only fixture
        ("test_suspect_only_not_verified", test_suspect_only_not_verified),
        # --tolerance flag
        ("test_tolerance_flag_changes_status", test_tolerance_flag_changes_status),
        # No citations
        ("test_no_citations_exit_zero", test_no_citations_exit_zero),
        # Phase-2 contract
        ("test_analyze_phase_two_contract", test_analyze_phase_two_contract),
        # Overwrite guard
        ("test_analyze_overwrite_guard", test_analyze_overwrite_guard),
        # Overwrite guard — refusal path
        ("test_analyze_overwrite_refusal", test_analyze_overwrite_refusal),
        # Registration
        ("test_relay_codes_registered", test_relay_codes_registered),
        ("test_threshold_set_registered", test_threshold_set_registered),
        # Validate recognition
        ("test_validate_recognises_sidecars", test_validate_recognises_sidecars),
        # Relay guards (criterion 27)
        (
            "test_relay_phantom_does_not_fire_unconditionally",
            test_relay_phantom_does_not_fire_unconditionally,
        ),
        (
            "test_relay_suspect_does_not_fire_unconditionally",
            test_relay_suspect_does_not_fire_unconditionally,
        ),
        (
            "test_relay_unresolved_does_not_fire_unconditionally",
            test_relay_unresolved_does_not_fire_unconditionally,
        ),
        (
            "test_relay_extraction_does_not_fire_on_structured",
            test_relay_extraction_does_not_fire_on_structured,
        ),
        # Manifest schema
        ("test_manifest_schema", test_manifest_schema),
        # Slug
        ("test_slug", test_slug),
        # Regression: #179 — title query injection
        (
            "test_title_double_quotes_sanitized",
            test_title_double_quotes_sanitized,
        ),
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
