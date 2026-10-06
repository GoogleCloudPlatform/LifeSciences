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

"""Tests for split WO metadata acceptance in _check_report_headings (#149).

Verifies that _check_report_headings accepts multiple equivalent forms
of work-order identification:

- Concatenated: WO-004-r1
- Split fields: Work Order: WO-004 + Revision: 1
- Inline: WO-004 r1
- Parenthesised: WO-004 (rev 1)
- Comma-separated: WO-004, Revision 1

Also verifies improved error messages and severity adjustment:
- Non-standard form -> warn (not fail)
- Missing identifier entirely -> fail with helpful error message
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.commands.validate import _check_report_headings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    """Write a file, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_report(root: Path, body: str) -> dict:
    """Create a findings report and return the deliverables dict."""
    finding = root / "findings" / "report.md"
    _write(finding, body)
    return {"layer_1": ["findings/report.md"]}


# ===========================================================================
# Item 1: Accept split metadata forms
# ===========================================================================


def test_concatenated_form_passes() -> None:
    """Canonical WO-004-r1 form passes with status ok."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        deliverables = _make_report(
            root,
            (
                "# Report WO-004-r1\n\n"
                "## Summary\n\nSummary text.\n\n"
                "## Key Findings\n\nFindings text.\n"
            ),
        )
        result = _check_report_headings(root, deliverables, "WO-004", 1)
        assert result["result"] == "pass", f"expected pass, got {result}"
        assert result["status"] == "ok"
        assert "files_missing_reference" not in result["detail"]
        assert "nonstandard_reference" not in result["detail"]
        print("  PASS: concatenated form WO-004-r1 -> pass/ok")


def test_split_form_passes() -> None:
    """Split metadata (Work Order: WO-004 + Revision: 1) passes with warn."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        deliverables = _make_report(
            root,
            (
                "# Report\n\n"
                "Work Order: WO-004\n"
                "Revision: 1\n\n"
                "## Summary\n\nSummary text.\n\n"
                "## Key Findings\n\nFindings text.\n"
            ),
        )
        result = _check_report_headings(root, deliverables, "WO-004", 1)
        assert result["result"] == "pass", f"expected pass, got {result}"
        assert result["status"] == "warn"
        assert result["kind"] == "CONVENTION"
        assert "nonstandard_reference" in result["detail"]
        assert "files_missing_reference" not in result["detail"]
        print("  PASS: split form (Work Order + Revision) -> pass/warn")


def test_inline_r_form_passes() -> None:
    """Inline form WO-004 r1 passes with warn."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        deliverables = _make_report(
            root,
            (
                "# Report WO-004 r1\n\n"
                "## Summary\n\nSummary text.\n\n"
                "## Key Findings\n\nFindings text.\n"
            ),
        )
        result = _check_report_headings(root, deliverables, "WO-004", 1)
        assert result["result"] == "pass", f"expected pass, got {result}"
        assert result["status"] == "warn"
        assert result["kind"] == "CONVENTION"
        assert "nonstandard_reference" in result["detail"]
        print("  PASS: inline form WO-004 r1 -> pass/warn")


def test_paren_rev_form_passes() -> None:
    """Parenthesised form WO-004 (rev 1) passes with warn."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        deliverables = _make_report(
            root,
            (
                "# Report WO-004 (rev 1)\n\n"
                "## Summary\n\nSummary text.\n\n"
                "## Key Findings\n\nFindings text.\n"
            ),
        )
        result = _check_report_headings(root, deliverables, "WO-004", 1)
        assert result["result"] == "pass", f"expected pass, got {result}"
        assert result["status"] == "warn"
        assert result["kind"] == "CONVENTION"
        assert "nonstandard_reference" in result["detail"]
        print("  PASS: paren form WO-004 (rev 1) -> pass/warn")


def test_comma_revision_form_passes() -> None:
    """Comma-separated form WO-004, Revision 1 passes with warn."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        deliverables = _make_report(
            root,
            (
                "# Report WO-004, Revision 1\n\n"
                "## Summary\n\nSummary text.\n\n"
                "## Key Findings\n\nFindings text.\n"
            ),
        )
        result = _check_report_headings(root, deliverables, "WO-004", 1)
        assert result["result"] == "pass", f"expected pass, got {result}"
        assert result["status"] == "warn"
        assert result["kind"] == "CONVENTION"
        assert "nonstandard_reference" in result["detail"]
        print("  PASS: comma form WO-004, Revision 1 -> pass/warn")


def test_case_insensitive_revision_label() -> None:
    """Case-insensitive field labels: 'revision', 'Rev' accepted."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        deliverables = _make_report(
            root,
            (
                "# Report\n\n"
                "work order: WO-004\n"
                "rev: 1\n\n"
                "## Summary\n\nSummary text.\n\n"
                "## Key Findings\n\nFindings text.\n"
            ),
        )
        result = _check_report_headings(root, deliverables, "WO-004", 1)
        assert result["result"] == "pass", f"expected pass, got {result}"
        assert result["status"] == "warn"
        assert "nonstandard_reference" in result["detail"]
        print("  PASS: case-insensitive revision labels -> pass/warn")


# ===========================================================================
# Item 2: Better error messages
# ===========================================================================


def test_wrong_wo_identifier_fails() -> None:
    """Wrong WO identifier (different number) fails."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        deliverables = _make_report(
            root,
            (
                "# Report WO-999-r1\n\n"
                "## Summary\n\nSummary text.\n\n"
                "## Key Findings\n\nFindings text.\n"
            ),
        )
        result = _check_report_headings(root, deliverables, "WO-004", 1)
        assert result["result"] == "fail", f"expected fail, got {result}"
        assert result["status"] == "fail"
        assert "files_missing_reference" in result["detail"]
        print("  PASS: wrong WO identifier -> fail")


def test_missing_wo_identifier_fails() -> None:
    """No WO identifier at all fails."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        deliverables = _make_report(
            root,
            (
                "# Report\n\n"
                "## Summary\n\nSummary text.\n\n"
                "## Key Findings\n\nFindings text.\n"
            ),
        )
        result = _check_report_headings(root, deliverables, "WO-004", 1)
        assert result["result"] == "fail", f"expected fail, got {result}"
        assert result["status"] == "fail"
        assert "files_missing_reference" in result["detail"]
        print("  PASS: missing WO identifier -> fail")


def test_error_message_shows_expected_forms() -> None:
    """Error message includes accepted forms and what was found."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        deliverables = _make_report(
            root,
            (
                "# Report\n\n"
                "## Summary\n\nSummary text.\n\n"
                "## Key Findings\n\nFindings text.\n"
            ),
        )
        result = _check_report_headings(root, deliverables, "WO-004", 1)
        assert result["result"] == "fail"
        detail = result["detail"]
        # Error message should be present and informative.
        assert "error_message" in detail, f"expected error_message in detail: {detail}"
        msg = detail["error_message"]
        assert "WO-004-r1" in msg, f"expected WO-004-r1 in error message: {msg}"
        assert "Accepted forms" in msg, (
            f"expected 'Accepted forms' in error message: {msg}"
        )
        assert "Work Order: WO-004" in msg
        # accepted_forms should list recognised patterns.
        assert "accepted_forms" in detail
        assert "WO-004 r1" in detail["accepted_forms"]
        print("  PASS: error message shows expected forms and found identifiers")


# ===========================================================================
# Item 3: Severity adjustment
# ===========================================================================


def test_nonstandard_form_is_warning_not_failure() -> None:
    """Non-standard but unambiguous form emits warn, not fail."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        deliverables = _make_report(
            root,
            (
                "# Report\n\n"
                "Work Order: WO-004\n"
                "Revision: 1\n\n"
                "## Summary\n\nSummary text.\n\n"
                "## Key Findings\n\nFindings text.\n"
            ),
        )
        result = _check_report_headings(root, deliverables, "WO-004", 1)
        # Must NOT be a failure — the WO identity is not in doubt.
        assert result["result"] == "pass", f"expected pass, got {result}"
        assert result["status"] == "warn"
        assert result["kind"] == "CONVENTION"
        print("  PASS: non-standard form -> warn (not fail)")


def test_no_identifier_is_failure() -> None:
    """Completely missing identifier -> fail / COMPLETENESS."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        deliverables = _make_report(
            root,
            (
                "# Report\n\n"
                "## Summary\n\nSummary text.\n\n"
                "## Key Findings\n\nFindings text.\n"
            ),
        )
        result = _check_report_headings(root, deliverables, "WO-004", 1)
        assert result["result"] == "fail"
        assert result["status"] == "fail"
        assert result["kind"] == "COMPLETENESS"
        print("  PASS: no identifier at all -> fail / COMPLETENESS")


# ===========================================================================
# Edge cases
# ===========================================================================


def test_wo_id_with_prefix_stripped() -> None:
    """wo_id passed as 'WO-004' works (prefix stripped internally)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        deliverables = _make_report(
            root,
            (
                "# Report WO-004-r2\n\n"
                "## Summary\n\nSummary text.\n\n"
                "## Key Findings\n\nFindings text.\n"
            ),
        )
        result = _check_report_headings(root, deliverables, "WO-004", 2)
        assert result["result"] == "pass"
        assert result["status"] == "ok"
        print("  PASS: WO-004 prefix handled correctly for revision 2")


def test_split_fields_wrong_revision_fails() -> None:
    """Split fields with wrong revision number fails."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        deliverables = _make_report(
            root,
            (
                "# Report\n\n"
                "Work Order: WO-004\n"
                "Revision: 99\n\n"
                "## Summary\n\nSummary text.\n\n"
                "## Key Findings\n\nFindings text.\n"
            ),
        )
        result = _check_report_headings(root, deliverables, "WO-004", 1)
        assert result["result"] == "fail", f"expected fail, got {result}"
        assert "files_missing_reference" in result["detail"]
        print("  PASS: split fields with wrong revision -> fail")


def test_split_fields_wrong_wo_number_fails() -> None:
    """Split fields with wrong WO number fails."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        deliverables = _make_report(
            root,
            (
                "# Report\n\n"
                "Work Order: WO-999\n"
                "Revision: 1\n\n"
                "## Summary\n\nSummary text.\n\n"
                "## Key Findings\n\nFindings text.\n"
            ),
        )
        result = _check_report_headings(root, deliverables, "WO-004", 1)
        assert result["result"] == "fail", f"expected fail, got {result}"
        assert "files_missing_reference" in result["detail"]
        print("  PASS: split fields with wrong WO number -> fail")


def test_canonical_form_takes_precedence_over_split() -> None:
    """When both canonical and split forms present, result is ok (not warn)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        deliverables = _make_report(
            root,
            (
                "# Report WO-004-r1\n\n"
                "Work Order: WO-004\n"
                "Revision: 1\n\n"
                "## Summary\n\nSummary text.\n\n"
                "## Key Findings\n\nFindings text.\n"
            ),
        )
        result = _check_report_headings(root, deliverables, "WO-004", 1)
        assert result["result"] == "pass"
        assert result["status"] == "ok"
        assert "nonstandard_reference" not in result["detail"]
        print("  PASS: canonical + split present -> ok (canonical wins)")


if __name__ == "__main__":
    test_concatenated_form_passes()
    test_split_form_passes()
    test_inline_r_form_passes()
    test_paren_rev_form_passes()
    test_comma_revision_form_passes()
    test_case_insensitive_revision_label()
    test_wrong_wo_identifier_fails()
    test_missing_wo_identifier_fails()
    test_error_message_shows_expected_forms()
    test_nonstandard_form_is_warning_not_failure()
    test_no_identifier_is_failure()
    test_wo_id_with_prefix_stripped()
    test_split_fields_wrong_revision_fails()
    test_split_fields_wrong_wo_number_fails()
    test_canonical_form_takes_precedence_over_split()
    print("\nAll tests passed!")
