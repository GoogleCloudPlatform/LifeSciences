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

"""Tests for PubMed zero-result per-term hit count diagnostics.

Covers:
  1. Per-term count reporting when search returns zero results
  2. Artifact distinguishes `no_results` vs `no_results_query_may_be_overconstrained`
  3. No per-term counts when search returns results
  4. Per-term counts handle API errors gracefully
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(base: Path) -> Path:
    """Create a minimal dde project directory."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    (project / "raw" / "literature").mkdir(parents=True, exist_ok=True)
    return project


def _esearch_json(count: int = 0, id_list: list[str] | None = None) -> dict[str, Any]:
    """Build a canned esearch JSON response."""
    return {
        "esearchresult": {
            "count": str(count),
            "idlist": id_list or [],
            "querytranslation": "test query",
        }
    }


def _esearch_count_json(count: int) -> dict[str, Any]:
    """Build a canned esearch count-only JSON response."""
    return {
        "esearchresult": {
            "count": str(count),
        }
    }


def _efetch_xml_with_articles() -> bytes:
    """Build minimal efetch XML with one article."""
    return (
        b'<?xml version="1.0" ?>\n'
        b"<PubmedArticleSet>\n"
        b"  <PubmedArticle>\n"
        b"    <MedlineCitation>\n"
        b"      <PMID>12345</PMID>\n"
        b"      <Article>\n"
        b"        <ArticleTitle>Test Article</ArticleTitle>\n"
        b"        <Journal><ISOAbbreviation>J Test</ISOAbbreviation>\n"
        b"          <JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue>\n"
        b"        </Journal>\n"
        b"      </Article>\n"
        b"    </MedlineCitation>\n"
        b"  </PubmedArticle>\n"
        b"</PubmedArticleSet>\n"
    )


# ---------------------------------------------------------------------------
# 1. Per-term counts reported on zero results (overconstrained)
# ---------------------------------------------------------------------------


def test_zero_results_per_term_counts() -> None:
    """When search returns 0 results, per-term counts are reported."""
    from click.testing import CliRunner
    from dde.cli import cli

    # Main search returns 0 results; per-term counts show individual terms have hits
    term_counts = {
        "OSMR": 1234,
        "keratinocyte": 5678,
    }

    def mock_get_json(url: str, **kwargs: Any) -> Any:
        if "rettype=count" not in url:
            # Main esearch
            return _esearch_json(count=0, id_list=[])
        # Per-term count requests
        for term, count in term_counts.items():
            if f"term={term}" in url or f"term={term.lower()}" in url:
                return _esearch_count_json(count)
        return _esearch_count_json(0)

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.pubmed.http.get_json", side_effect=mock_get_json):
            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "pubmed",
                    "search",
                    "OSMR keratinocyte",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        # Check that per-term counts appear in output
        assert "Per-term hit counts:" in result.output
        assert "OSMR" in result.output
        assert "keratinocyte" in result.output

        # Check the artifact
        lit_dir = project / "raw" / "literature"
        artifact_files = list(lit_dir.glob("*.pubmed-search.json"))
        assert artifact_files, "No artifact file found"

        artifact = json.loads(artifact_files[0].read_text(encoding="utf-8"))
        assert "per_term_counts" in artifact
        assert len(artifact["per_term_counts"]) == 2
        assert (
            artifact["zero_result_reason"] == "no_results_query_may_be_overconstrained"
        )

    print("  PASS: per-term counts reported on zero results")


# ---------------------------------------------------------------------------
# 2. Artifact distinguishes no_results vs overconstrained
# ---------------------------------------------------------------------------


def test_zero_results_genuinely_empty() -> None:
    """When all individual terms also have 0 results, reason is 'no_results'."""
    from click.testing import CliRunner
    from dde.cli import cli

    def mock_get_json(url: str, **kwargs: Any) -> Any:
        if "rettype=count" not in url:
            return _esearch_json(count=0, id_list=[])
        return _esearch_count_json(0)

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.pubmed.http.get_json", side_effect=mock_get_json):
            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "pubmed",
                    "search",
                    "xyznonexistentterm",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        lit_dir = project / "raw" / "literature"
        artifact_files = list(lit_dir.glob("*.pubmed-search.json"))
        assert artifact_files, "No artifact file found"

        artifact = json.loads(artifact_files[0].read_text(encoding="utf-8"))
        assert artifact["zero_result_reason"] == "no_results"

    print("  PASS: genuinely empty search has reason 'no_results'")


# ---------------------------------------------------------------------------
# 3. No per-term counts when search returns results
# ---------------------------------------------------------------------------


def test_results_found_no_per_term_counts() -> None:
    """When search returns results, no per-term diagnostics are added."""
    from click.testing import CliRunner
    from dde.cli import cli

    def mock_get_json(url: str, **kwargs: Any) -> Any:
        return _esearch_json(count=1, id_list=["12345"])

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with (
            mock.patch("dde.commands.pubmed.http.get_json", side_effect=mock_get_json),
            mock.patch(
                "dde.commands.pubmed.http.get_bytes",
                return_value=_efetch_xml_with_articles(),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "pubmed",
                    "search",
                    "OSMR",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        lit_dir = project / "raw" / "literature"
        artifact_files = list(lit_dir.glob("*.pubmed-search.json"))
        assert artifact_files, "No artifact file found"

        artifact = json.loads(artifact_files[0].read_text(encoding="utf-8"))
        assert "per_term_counts" not in artifact
        assert "zero_result_reason" not in artifact

    print("  PASS: no per-term counts when results found")


# ---------------------------------------------------------------------------
# 4. Per-term count handles API errors gracefully
# ---------------------------------------------------------------------------


def test_per_term_count_api_error() -> None:
    """API errors during per-term count produce count=-1, not a crash."""
    from click.testing import CliRunner
    from dde.cli import cli

    call_count = 0

    def mock_get_json(url: str, **kwargs: Any) -> Any:
        nonlocal call_count
        if "rettype=count" not in url:
            return _esearch_json(count=0, id_list=[])
        call_count += 1
        if call_count == 1:
            raise ConnectionError("simulated network failure")
        return _esearch_count_json(42)

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.pubmed.http.get_json", side_effect=mock_get_json):
            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "pubmed",
                    "search",
                    "failterm okterm",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        lit_dir = project / "raw" / "literature"
        artifact_files = list(lit_dir.glob("*.pubmed-search.json"))
        artifact = json.loads(artifact_files[0].read_text(encoding="utf-8"))

        counts = artifact["per_term_counts"]
        # First term should have count=-1 (error)
        assert counts[0]["count"] == -1
        # Second term should have count=42
        assert counts[1]["count"] == 42

    print("  PASS: per-term count API error handled gracefully")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        ("test_zero_results_per_term_counts", test_zero_results_per_term_counts),
        ("test_zero_results_genuinely_empty", test_zero_results_genuinely_empty),
        (
            "test_results_found_no_per_term_counts",
            test_results_found_no_per_term_counts,
        ),
        ("test_per_term_count_api_error", test_per_term_count_api_error),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:
            print(f"  FAIL: {name} -- {exc}")
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
