#!/usr/bin/env python3
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

"""Tests for site.py bug fixes (#203, #204).

Covers:
- #203: _strip_code() removes code blocks/spans to prevent SMILES false-positive links
- #204: _dedent_tables() preprocesses indented pipe tables for mistune rendering

Run with:
    PYTHONPATH=tools python3 tests/test_site_bugs.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap — add tools/ to sys.path so dde is importable
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dde.commands.site import _MARKDOWN_LINK_RE, _dedent_tables, _strip_code

# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------

_RESULTS: list[tuple[str, bool, str]] = []


def _run(name: str, fn):
    try:
        fn()
        _RESULTS.append((name, True, ""))
    except Exception as exc:
        _RESULTS.append((name, False, f"{exc}\n{traceback.format_exc()}"))


# ---------------------------------------------------------------------------
# Bug #203 — SMILES false-positive links (_strip_code)
# ---------------------------------------------------------------------------


def test_strip_code_removes_inline_code():
    """_strip_code() removes inline code spans."""
    text = "See `[C@@H](COP(=O))` for details."
    result = _strip_code(text)
    assert "`" not in result, f"Backticks remain: {result!r}"
    assert "[C@@H]" not in result, f"SMILES still present: {result!r}"


def test_strip_code_removes_fenced_code_blocks():
    """_strip_code() removes fenced code blocks."""
    text = "Before\n```\n[link](target.md)\nsome code\n```\nAfter"
    result = _strip_code(text)
    assert "```" not in result, f"Fenced block remains: {result!r}"
    assert "[link](target.md)" not in result, (
        f"Link in fenced block remains: {result!r}"
    )
    assert "Before" in result
    assert "After" in result


def test_strip_code_preserves_real_links():
    """_strip_code() preserves real markdown links outside code."""
    text = "Click [here](docs/readme.md) for more info."
    result = _strip_code(text)
    assert "[here](docs/readme.md)" in result, f"Real link stripped: {result!r}"


def test_smiles_no_false_positive_after_stripping():
    """SMILES notation inside backticks does not produce false-positive link matches."""
    text = "The compound `[C@@H](COP(=O)(O)O)` is interesting.\n"
    text += "See [analysis](findings/analysis.md) for details.\n"
    stripped = _strip_code(text)
    matches = _MARKDOWN_LINK_RE.findall(stripped)
    # Should only find the real link, not the SMILES notation
    assert len(matches) == 1, f"Expected 1 match, got {len(matches)}: {matches}"
    assert matches[0] == "findings/analysis.md", f"Wrong match: {matches[0]}"


def test_validate_links_ignores_smiles_in_code():
    """Integration: _validate_links() does not report broken links for SMILES in code spans.

    Sets up a minimal project with a finding containing SMILES in backticks
    and verifies no broken-link issues are produced for them.
    """
    import tempfile

    from dde.commands.site import _validate_links
    from dde.core.controlstore import ensure_control_dirs, write_record

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        (project / ".dde").mkdir()
        ensure_control_dirs(project)

        # Write an accepted work order
        wo_data = {
            "id": "WO-203",
            "revision": 1,
            "state": "scientifically_accepted",
            "decision_question": "Test SMILES",
            "requested_role": "test",
            "stage": "test",
            "cycle": 1,
            "context": {"summary": "test"},
            "dependencies": [],
            "capabilities": ["test"],
            "deliverables": {
                "layer_1": ["findings/smiles-test.md"],
                "layer_0_classes": [],
            },
            "acceptance_criteria": "pass",
            "alert_policy": {"on_failure": "notify"},
            "priority": "normal",
            "resource_class": "standard",
            "report_to": "test-lead",
            "created_at": "2026-01-01T00:00:00Z",
        }
        write_record(project, "work-order", "WO-203-r1", wo_data)

        # Create finding with SMILES in backticks
        findings_dir = project / "findings"
        findings_dir.mkdir()
        (findings_dir / "smiles-test.md").write_text(
            "# SMILES Analysis\n\n"
            "The compound `[C@@H](COP(=O)(O)O)` was analysed.\n\n"
            "Also `[NH2+](CC)CC` is relevant.\n",
            encoding="utf-8",
        )

        work_orders = [wo_data]
        issues = _validate_links(project, work_orders)

        # Should have no broken-link issues for the SMILES notation
        link_issues = [i for i in issues if "link" in i]
        assert len(link_issues) == 0, (
            f"SMILES produced false-positive link issues: {link_issues}"
        )


# ---------------------------------------------------------------------------
# Bug #204 — Markdown tables (_dedent_tables)
# ---------------------------------------------------------------------------


def test_dedent_tables_indented_pipe_table():
    """_dedent_tables() dedents an indented pipe table."""
    text = "    | A | B |\n    |---|---|\n    | 1 | 2 |"
    result = _dedent_tables(text)
    lines = result.split("\n")
    assert lines[0] == "| A | B |", f"First line not dedented: {lines[0]!r}"
    assert lines[1] == "|---|---|", f"Separator not dedented: {lines[1]!r}"
    assert lines[2] == "| 1 | 2 |", f"Data row not dedented: {lines[2]!r}"


def test_dedent_tables_leaves_non_table_unchanged():
    """_dedent_tables() leaves non-table indented content unchanged."""
    text = "    This is just indented text\n    Not a table at all"
    result = _dedent_tables(text)
    assert result == text, f"Non-table content was modified: {result!r}"


def test_dedent_tables_unindented_passthrough():
    """_dedent_tables() handles already-unindented tables (no change)."""
    text = "| A | B |\n|---|---|\n| 1 | 2 |"
    result = _dedent_tables(text)
    assert result == text, f"Unindented table was modified: {result!r}"


def test_mistune_renders_table_html():
    """Mistune with table plugin renders a simple pipe table to an HTML table."""
    import mistune

    md = mistune.create_markdown(escape=True, plugins=["table", "strikethrough"])
    table_md = "| Header A | Header B |\n|---|---|\n| Cell 1 | Cell 2 |\n"
    html = md(table_md)
    assert "<table>" in html, f"No <table> tag in output: {html!r}"
    assert "<th>Header A</th>" in html, f"Missing header: {html!r}"
    assert "<td>Cell 1</td>" in html, f"Missing cell: {html!r}"


def test_mistune_renders_dedented_table():
    """Mistune with table plugin renders a dedented table correctly."""
    import mistune

    md = mistune.create_markdown(escape=True, plugins=["table", "strikethrough"])
    indented = "    | X | Y |\n    |---|---|\n    | a | b |\n"
    dedented = _dedent_tables(indented)
    html = md(dedented)
    assert "<table>" in html, f"No <table> tag in output: {html!r}"
    assert "<th>X</th>" in html, f"Missing header: {html!r}"
    assert "<td>a</td>" in html, f"Missing cell: {html!r}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _run("strip_code_removes_inline_code", test_strip_code_removes_inline_code)
    _run(
        "strip_code_removes_fenced_code_blocks",
        test_strip_code_removes_fenced_code_blocks,
    )
    _run("strip_code_preserves_real_links", test_strip_code_preserves_real_links)
    _run(
        "smiles_no_false_positive_after_stripping",
        test_smiles_no_false_positive_after_stripping,
    )
    _run(
        "validate_links_ignores_smiles_in_code",
        test_validate_links_ignores_smiles_in_code,
    )
    _run("dedent_tables_indented_pipe_table", test_dedent_tables_indented_pipe_table)
    _run(
        "dedent_tables_leaves_non_table_unchanged",
        test_dedent_tables_leaves_non_table_unchanged,
    )
    _run(
        "dedent_tables_unindented_passthrough",
        test_dedent_tables_unindented_passthrough,
    )
    _run("mistune_renders_table_html", test_mistune_renders_table_html)
    _run("mistune_renders_dedented_table", test_mistune_renders_dedented_table)

    # Report
    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    failed = sum(1 for _, ok, _ in _RESULTS if not ok)
    total = len(_RESULTS)

    print(f"\n{'=' * 60}")
    for name, ok, err in _RESULTS:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
        if err:
            for line in err.strip().splitlines():
                print(f"         {line}")
    print(f"{'=' * 60}")
    print(f"  {passed}/{total} passed, {failed} failed")

    sys.exit(0 if failed == 0 else 1)
