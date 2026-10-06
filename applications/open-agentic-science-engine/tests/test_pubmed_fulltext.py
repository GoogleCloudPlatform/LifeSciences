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

"""Tests for the pubmed fulltext subcommand.

Covers:
  1. Valid PMCID retrieves full text -- correct schema, fields present
  2. Invalid PMCID format -> usage error
  3. Article not in OA subset -> fires pubmed.fulltext_unavailable
  4. Relay registration check
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.core import provenance

# ---------------------------------------------------------------------------
# Helper: project setup and canned PMC responses
# ---------------------------------------------------------------------------


def _make_project(base: Path) -> Path:
    """Create a minimal dde project directory."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    (project / "raw" / "literature").mkdir(parents=True, exist_ok=True)
    return project


def _pmc_fulltext_xml(
    pmcid: str = "PMC1234567",
    title: str = "Test Article Title",
    authors: list[tuple[str, str]] | None = None,
    abstract: str = "This is the abstract.",
    sections: list[tuple[str, str]] | None = None,
    doi: str | None = "10.1234/test.001",
    pmid: str | None = "12345678",
    references: list[str] | None = None,
) -> bytes:
    """Build a canned PMC JATS XML response."""
    if authors is None:
        authors = [("Smith", "John"), ("Jones", "Alice")]
    if sections is None:
        sections = [
            ("Introduction", "This is the introduction paragraph."),
            ("Methods", "We used these methods."),
            ("Results", "Here are the results."),
        ]
    if references is None:
        references = [
            "Smith J. A first reference. J Test. 2020;1:1-10.",
            "Jones A. A second reference. J Test. 2021;2:20-30.",
        ]

    pmcid_num = pmcid.replace("PMC", "")

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE pmc-articleset PUBLIC "-//NLM//DTD ARTICLE SET 2.0//EN" '
        '"https://dtd.nlm.nih.gov/ncbi/pmc/articleset/nlm-articleset-2.0.dtd">',
        "<pmc-articleset>",
        '<article article-type="research-article">',
        "  <front>",
        "    <article-meta>",
    ]

    # Article IDs
    parts.append(f'      <article-id pub-id-type="pmc">{pmcid_num}</article-id>')
    if pmid:
        parts.append(f'      <article-id pub-id-type="pmid">{pmid}</article-id>')
    if doi:
        parts.append(f'      <article-id pub-id-type="doi">{doi}</article-id>')

    # Title
    parts.append("      <title-group>")
    parts.append(f"        <article-title>{title}</article-title>")
    parts.append("      </title-group>")

    # Authors
    parts.append("      <contrib-group>")
    for surname, given in authors:
        parts.append('        <contrib contrib-type="author">')
        parts.append("          <name>")
        parts.append(f"            <surname>{surname}</surname>")
        parts.append(f"            <given-names>{given}</given-names>")
        parts.append("          </name>")
        parts.append("        </contrib>")
    parts.append("      </contrib-group>")

    # Abstract
    parts.append("      <abstract>")
    parts.append(f"        <p>{abstract}</p>")
    parts.append("      </abstract>")

    parts.append("    </article-meta>")
    parts.append("  </front>")

    # Body
    parts.append("  <body>")
    for heading, text in sections:
        parts.append("    <sec>")
        parts.append(f"      <title>{heading}</title>")
        parts.append(f"      <p>{text}</p>")
        parts.append("    </sec>")
    parts.append("  </body>")

    # References
    parts.append("  <back>")
    parts.append("    <ref-list>")
    for i, ref in enumerate(references, 1):
        parts.append(f'      <ref id="ref{i}">')
        parts.append(f"        <mixed-citation>{ref}</mixed-citation>")
        parts.append("      </ref>")
    parts.append("    </ref-list>")
    parts.append("  </back>")

    parts.append("</article>")
    parts.append("</pmc-articleset>")

    return "\n".join(parts).encode("utf-8")


def _empty_pmc_response() -> bytes:
    """Build a PMC response with no article content (not in OA subset)."""
    return (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<!DOCTYPE pmc-articleset PUBLIC "-//NLM//DTD ARTICLE SET 2.0//EN" '
        b'"https://dtd.nlm.nih.gov/ncbi/pmc/articleset/nlm-articleset-2.0.dtd">\n'
        b"<pmc-articleset>\n"
        b"</pmc-articleset>\n"
    )


def _mock_http_response(content: bytes, status_code: int = 200) -> mock.Mock:
    """Build a mock HTTP response that returns raw bytes."""
    resp = mock.Mock()
    resp.status_code = status_code
    resp.content = content
    resp.text = content.decode("utf-8")
    return resp


# ---------------------------------------------------------------------------
# 1. Valid PMCID retrieves full text -- correct schema, fields present
# ---------------------------------------------------------------------------


def test_fulltext_valid_pmcid() -> None:
    """A valid PMCID retrieves full text with correct schema and fields."""
    from click.testing import CliRunner
    from dde.cli import cli

    xml_bytes = _pmc_fulltext_xml(
        pmcid="PMC1234567",
        title="Machine Learning for Drug Discovery",
        authors=[("Smith", "John"), ("Jones", "Alice")],
        abstract="We present a novel approach.",
        sections=[
            ("Introduction", "Background on the topic."),
            ("Methods", "We used deep learning."),
            ("Results", "Our model achieved high accuracy."),
        ],
        doi="10.1234/ml-drug.001",
        pmid="98765432",
        references=[
            "Ref 1: First reference text.",
            "Ref 2: Second reference text.",
        ],
    )

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.pubmed.http.get_bytes") as mock_get:
            mock_get.return_value = xml_bytes
            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "pubmed",
                    "fulltext",
                    "PMC1234567",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        # Check the artifact
        lit_dir = project / "raw" / "literature"
        artifact_path = lit_dir / "pmc1234567.fulltext.json"
        assert artifact_path.is_file(), f"Artifact not found: {artifact_path}"

        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert artifact["schema"] == "dde.pubmed-fulltext.v1"
        assert artifact["pmcid"] == "PMC1234567"
        assert artifact["title"] == "Machine Learning for Drug Discovery"
        assert len(artifact["authors"]) == 2
        assert artifact["abstract"] is not None
        assert len(artifact["sections"]) == 3
        assert len(artifact["references"]) == 2
        assert artifact["doi"] == "10.1234/ml-drug.001"
        assert artifact["pmid"] == "98765432"
        assert "fetched_at" in artifact

        # Check sections structure
        for section in artifact["sections"]:
            assert "heading" in section
            assert "text" in section

        # Check the raw XML was saved
        xml_path = lit_dir / "pmc1234567.fulltext.xml"
        assert xml_path.is_file(), f"Raw XML not saved: {xml_path}"

        # Check the sidecar
        meta_path = lit_dir / "pmc1234567.fulltext.meta.json"
        assert meta_path.is_file(), f"Sidecar not found: {meta_path}"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["tool"] == "pubmed"
        assert meta["subcommand"] == "fulltext"
        assert meta["parameters"]["pmcid"] == "PMC1234567"

    print("  PASS: valid PMCID retrieves full text with correct schema")


# ---------------------------------------------------------------------------
# 2. Invalid PMCID format -> usage error
# ---------------------------------------------------------------------------


def test_fulltext_invalid_pmcid_format() -> None:
    """Invalid PMCID format raises a usage error."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        # No "PMC" prefix
        result = runner.invoke(
            cli,
            ["--project", str(project), "pubmed", "fulltext", "1234567"],
        )
        assert result.exit_code != 0, (
            f"Expected non-zero exit for invalid PMCID, got {result.exit_code}"
        )

        # Alphabetic after PMC
        result = runner.invoke(
            cli,
            ["--project", str(project), "pubmed", "fulltext", "PMCabc"],
        )
        assert result.exit_code != 0

        # Empty string
        result = runner.invoke(
            cli,
            ["--project", str(project), "pubmed", "fulltext", ""],
        )
        assert result.exit_code != 0

        # Just "PMC" with no digits
        result = runner.invoke(
            cli,
            ["--project", str(project), "pubmed", "fulltext", "PMC"],
        )
        assert result.exit_code != 0

    print("  PASS: invalid PMCID format raises usage error")


# ---------------------------------------------------------------------------
# 3. Article not in OA subset -> fires pubmed.fulltext_unavailable
# ---------------------------------------------------------------------------


def test_fulltext_unavailable() -> None:
    """Article not in OA subset fires pubmed.fulltext_unavailable."""
    from click.testing import CliRunner
    from dde.cli import cli

    xml_bytes = _empty_pmc_response()

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        runner = CliRunner()

        with mock.patch("dde.commands.pubmed.http.get_bytes") as mock_get:
            mock_get.return_value = xml_bytes
            result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "pubmed",
                    "fulltext",
                    "PMC9999999",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"

        # Check the sidecar for the relay
        lit_dir = project / "raw" / "literature"
        meta_path = lit_dir / "pmc9999999.fulltext.meta.json"
        assert meta_path.is_file(), f"Sidecar not found: {meta_path}"

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "pubmed.fulltext_unavailable" in relay_codes, (
            f"pubmed.fulltext_unavailable not fired; relays: {relay_codes}"
        )

        # Should also be in warnings
        assert any("not available" in w for w in meta.get("warnings", [])), (
            f"Expected unavailable warning in warnings: {meta.get('warnings', [])}"
        )

    print("  PASS: article not in OA subset fires pubmed.fulltext_unavailable")


# ---------------------------------------------------------------------------
# 4. Relay registration check
# ---------------------------------------------------------------------------


def test_relay_code_registered() -> None:
    """pubmed.fulltext_unavailable is in provenance.RELAY_CODES."""
    assert "pubmed.fulltext_unavailable" in provenance.RELAY_CODES, (
        "pubmed.fulltext_unavailable not registered in RELAY_CODES"
    )
    print("  PASS: pubmed.fulltext_unavailable registered in RELAY_CODES")


# ---------------------------------------------------------------------------
# 5. Nested <sec> elements are captured
# ---------------------------------------------------------------------------


def test_fulltext_nested_sections() -> None:
    """Nested <sec> elements produce separate section entries."""
    from dde.commands.pubmed import _parse_fulltext_xml

    xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b"<pmc-articleset>"
        b'<article article-type="research-article">'
        b"  <front><article-meta>"
        b'    <article-id pub-id-type="doi">10.1234/nested</article-id>'
        b"    <title-group><article-title>Nested Test</article-title></title-group>"
        b"    <contrib-group>"
        b'      <contrib contrib-type="author"><name>'
        b"        <surname>Doe</surname><given-names>Jane</given-names>"
        b"      </name></contrib>"
        b"    </contrib-group>"
        b"    <abstract><p>Abstract.</p></abstract>"
        b"  </article-meta></front>"
        b"  <body>"
        b"    <sec><title>Introduction</title><p>Intro text.</p>"
        b"      <sec><title>Background</title><p>Background text.</p></sec>"
        b"    </sec>"
        b"  </body>"
        b"</article></pmc-articleset>"
    )

    result = _parse_fulltext_xml(xml)
    headings = [s["heading"] for s in result["sections"]]
    assert "Introduction" in headings, f"Missing 'Introduction' in {headings}"
    assert "Background" in headings, f"Missing 'Background' in {headings}"
    assert len(result["sections"]) >= 2, (
        f"Expected at least 2 sections, got {len(result['sections'])}"
    )

    print("  PASS: nested <sec> elements produce separate section entries")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        ("test_fulltext_valid_pmcid", test_fulltext_valid_pmcid),
        ("test_fulltext_invalid_pmcid_format", test_fulltext_invalid_pmcid_format),
        ("test_fulltext_unavailable", test_fulltext_unavailable),
        ("test_relay_code_registered", test_relay_code_registered),
        ("test_fulltext_nested_sections", test_fulltext_nested_sections),
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
