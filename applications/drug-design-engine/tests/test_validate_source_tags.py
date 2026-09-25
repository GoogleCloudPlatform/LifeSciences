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

"""Tests for _check_source_tags_resolve (#100)."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.commands.validate import _check_source_tags_resolve

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str | bytes) -> None:
    """Write a file, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)


def _make_deliverables(layer_1_paths: list[str]) -> dict:
    return {"layer_1": layer_1_paths}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_source_tags() -> None:
    """File with no source tags → pass/ok."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        finding = root / "findings" / "report.md"
        _write(finding, "# Report\n\nNo source tags here.\n")
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "pass", f"expected pass, got {result['result']}"
        assert result["status"] == "ok"
        assert result["kind"] == "DATA_INTEGRITY"
        assert result["sub_findings"] == []


def test_scalar_tag_value_match() -> None:
    """Tag with matching value within tolerance → ok/DATA_INTEGRITY."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Create artifact
        artifact = root / "raw" / "data.json"
        _write(artifact, json.dumps({"pocket_volume": 419.7}))
        # Create finding
        finding = root / "findings" / "report.md"
        _write(
            finding,
            "The pocket volume is 420 {source: raw/data.json $.pocket_volume} cubic angstroms.\n",
        )
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "pass", f"expected pass, got {result}"
        assert result["status"] == "ok"
        subs = result["sub_findings"]
        assert len(subs) == 1
        assert subs[0]["status"] == "ok"
        assert subs[0]["kind"] == "DATA_INTEGRITY"


def test_scalar_tag_value_mismatch() -> None:
    """Value outside tolerance → fail/DATA_INTEGRITY."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "raw" / "data.json"
        _write(artifact, json.dumps({"score": 100.0}))
        finding = root / "findings" / "report.md"
        _write(finding, "The score is 200 {source: raw/data.json $.score} points.\n")
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "fail", f"expected fail, got {result}"
        assert result["status"] == "fail"
        assert result["kind"] == "DATA_INTEGRITY"
        subs = result["sub_findings"]
        assert subs[0]["status"] == "fail"


def test_scalar_tag_missing_locator() -> None:
    """Numerical tag without locator → warn/FORMAT."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "raw" / "data.json"
        _write(artifact, json.dumps({"value": 42}))
        finding = root / "findings" / "report.md"
        _write(finding, "The value is 42 {source: raw/data.json}\n")
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "pass", f"expected pass, got {result}"
        assert result["status"] == "warn"
        assert result["kind"] == "FORMAT"
        subs = result["sub_findings"]
        assert subs[0]["status"] == "warn"
        assert subs[0]["kind"] == "FORMAT"
        assert "missing locator" in subs[0]["detail"]


def test_scalar_tag_non_numerical_no_locator() -> None:
    """Non-numerical ref without locator → ok (file-level provenance sufficient)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "raw" / "data.json"
        _write(artifact, json.dumps({"gene": "TP53"}))
        finding = root / "findings" / "report.md"
        # No number before the tag — just a text reference.
        _write(
            finding,
            "The data was obtained from {source: raw/data.json} the artifact.\n",
        )
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "pass"
        assert result["status"] == "ok"
        subs = result["sub_findings"]
        assert subs[0]["status"] == "ok"


def test_scalar_tag_unresolvable_path() -> None:
    """Path does not exist → fail/DATA_INTEGRITY."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        finding = root / "findings" / "report.md"
        _write(finding, "The value is 42 {source: raw/nonexistent.json $.val}\n")
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "fail"
        assert result["status"] == "fail"
        subs = result["sub_findings"]
        assert subs[0]["status"] == "fail"
        assert subs[0]["kind"] == "DATA_INTEGRITY"
        assert "does not resolve" in subs[0]["detail"]


def test_scalar_tag_unresolvable_locator() -> None:
    """JSONPath matches nothing → fail/DATA_INTEGRITY."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "raw" / "data.json"
        _write(artifact, json.dumps({"alpha": 1}))
        finding = root / "findings" / "report.md"
        _write(finding, "The value is 42 {source: raw/data.json $.nonexistent_field}\n")
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "fail"
        assert result["status"] == "fail"
        subs = result["sub_findings"]
        assert subs[0]["status"] == "fail"
        assert "locator does not resolve" in subs[0]["detail"]


def test_scalar_tag_non_scalar_resolution() -> None:
    """Locator resolves to object → warn/FORMAT."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "raw" / "data.json"
        _write(artifact, json.dumps({"nested": {"a": 1, "b": 2}}))
        finding = root / "findings" / "report.md"
        _write(
            finding,
            "The metrics are 99 {source: raw/data.json $.nested} interesting.\n",
        )
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "pass"
        assert result["status"] == "warn"
        subs = result["sub_findings"]
        assert subs[0]["status"] == "warn"
        assert subs[0]["kind"] == "FORMAT"
        assert "non-scalar" in subs[0]["detail"]


def test_source_table_tag_resolution() -> None:
    """Table tag with valid path + locator → ok."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "raw" / "data.json"
        _write(artifact, json.dumps({"properties": {"MW": 342.4, "LogP": 2.1}}))
        finding = root / "findings" / "report.md"
        _write(
            finding,
            "| Prop | Value |\n|------|-------|\n| MW | 342.4 |\n\n{source-table: raw/data.json $.properties}\n",
        )
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "pass"
        assert result["status"] == "ok"
        subs = result["sub_findings"]
        assert len(subs) == 1
        assert subs[0]["status"] == "ok"


def test_source_table_tag_non_scalar_ok() -> None:
    """Table tag resolving to array is ok (expected for tables)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "raw" / "data.json"
        _write(artifact, json.dumps({"rows": [{"a": 1}, {"a": 2}]}))
        finding = root / "findings" / "report.md"
        _write(finding, "| A |\n|---|\n| 1 |\n\n{source-table: raw/data.json $.rows}\n")
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "pass"
        subs = result["sub_findings"]
        assert subs[0]["status"] == "ok"
        # Crucially, non-scalar resolution is NOT a warning for table tags.
        assert subs[0]["kind"] == "DATA_INTEGRITY"


def test_identifier_not_extracted() -> None:
    """Identifier-like tokens (Q99650, CID12345) are not extracted as claimed values."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "raw" / "data.json"
        _write(artifact, json.dumps({"id": "Q99650"}))
        finding = root / "findings" / "report.md"
        # "Q99650" should NOT be extracted as the number 99650.
        _write(
            finding, "UniProt entry Q99650 {source: raw/data.json $.id} was found.\n"
        )
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        # Since no numerical claim is extracted, this should be ok
        # (locator resolves to a string, no numerical comparison needed).
        assert result["result"] == "pass"
        subs = result["sub_findings"]
        assert subs[0]["status"] == "ok"


def test_code_fenced_tags_skipped() -> None:
    """Tags inside code fences are ignored."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        finding = root / "findings" / "report.md"
        content = (
            "# Report\n\n"
            "Here is an example:\n\n"
            "```\n"
            "The value is 42 {source: raw/nonexistent.json $.val}\n"
            "```\n\n"
            "That was just an example.\n"
        )
        _write(finding, content)
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        # The tag inside the code fence should be skipped → no tags found.
        assert result["result"] == "pass"
        assert result["status"] == "ok"
        assert result["sub_findings"] == []


def test_inline_code_tags_skipped() -> None:
    """Tags inside inline code (single backticks) are ignored."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        finding = root / "findings" / "report.md"
        content = (
            "Use the tag like `{source: raw/file.json $.field}` in your findings.\n"
        )
        _write(finding, content)
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "pass"
        assert result["sub_findings"] == []


def test_tolerance_matching_close() -> None:
    """0.94 vs 0.9387 (within 1%), 420 vs 419.7 (within 1%) → ok."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "raw" / "data.json"
        _write(artifact, json.dumps({"score": 0.9387, "volume": 419.7}))
        finding = root / "findings" / "report.md"
        _write(
            finding,
            "Score is 0.94 {source: raw/data.json $.score} and "
            "volume is 420 {source: raw/data.json $.volume} units.\n",
        )
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "pass", f"expected pass, got {result}"
        assert result["status"] == "ok"
        for sf in result["sub_findings"]:
            assert sf["status"] == "ok", f"sub-finding failed: {sf}"


def test_path_escapes_root() -> None:
    """Path with ../../etc/passwd → fail/DATA_INTEGRITY."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        finding = root / "findings" / "report.md"
        _write(finding, "secret 42 {source: ../../../etc/passwd $.val}\n")
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "fail"
        subs = result["sub_findings"]
        assert subs[0]["status"] == "fail"
        assert subs[0]["kind"] == "DATA_INTEGRITY"
        assert "escapes project root" in subs[0]["detail"]


def test_non_dict_json() -> None:
    """Top-level array JSON does not crash; JSONPath $[0].score works."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "raw" / "pae.json"
        _write(artifact, json.dumps([{"score": 0.85}, {"score": 0.90}]))
        finding = root / "findings" / "report.md"
        _write(finding, "PAE score 0.85 {source: raw/pae.json $.[0].score}\n")
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "pass"
        subs = result["sub_findings"]
        assert subs[0]["status"] == "ok"


def test_no_layer1_deliverables() -> None:
    """No layer_1 → skip."""
    result = _check_source_tags_resolve(
        Path("/nonexistent"), {"layer_0_classes": ["foo"]}
    )
    assert result["result"] == "skip"
    assert result["status"] == "skip"


def test_exact_value_match() -> None:
    """Exact integer/float match works: 330 == 330.0."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "raw" / "data.json"
        _write(artifact, json.dumps({"count": 330.0}))
        finding = root / "findings" / "report.md"
        _write(finding, "Found 330 {source: raw/data.json $.count} hits.\n")
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "pass"
        assert result["sub_findings"][0]["status"] == "ok"


def test_percentage_value() -> None:
    """Percentage value (78.3%) extracts 78.3."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "raw" / "data.json"
        _write(artifact, json.dumps({"tpsa": 78.3}))
        finding = root / "findings" / "report.md"
        _write(finding, "TPSA is 78.3% {source: raw/data.json $.tpsa} of total.\n")
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "pass"
        assert result["sub_findings"][0]["status"] == "ok"


def test_scientific_notation() -> None:
    """Scientific notation (3.5e-4) extracts correctly."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "raw" / "data.json"
        _write(artifact, json.dumps({"ki": 3.5e-4}))
        finding = root / "findings" / "report.md"
        _write(finding, "Ki is 3.5e-4 {source: raw/data.json $.ki} M.\n")
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "pass"
        assert result["sub_findings"][0]["status"] == "ok"


def test_multiple_tags_one_line() -> None:
    """Multiple tags on one line: each parsed independently."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "raw" / "data.json"
        _write(artifact, json.dumps({"total": 687, "retrieved": 30}))
        finding = root / "findings" / "report.md"
        _write(
            finding,
            "Found 687 {source: raw/data.json $.total} total results, "
            "30 {source: raw/data.json $.retrieved} retrieved.\n",
        )
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "pass"
        subs = result["sub_findings"]
        assert len(subs) == 2
        for sf in subs:
            assert sf["status"] == "ok", f"sub-finding failed: {sf}"


def test_source_table_unresolvable_path() -> None:
    """Source-table tag with nonexistent path → fail."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        finding = root / "findings" / "report.md"
        _write(finding, "| A |\n|---|\n\n{source-table: raw/nonexistent.json $.data}\n")
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "fail"
        subs = result["sub_findings"]
        assert subs[0]["status"] == "fail"
        assert subs[0]["kind"] == "DATA_INTEGRITY"


def test_mixed_ok_and_warn() -> None:
    """Mix of ok and warn sub-findings → overall warn/FORMAT."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "raw" / "data.json"
        _write(artifact, json.dumps({"score": 0.95, "count": 42}))
        finding = root / "findings" / "report.md"
        _write(
            finding,
            "Score is 0.95 {source: raw/data.json $.score} good. "
            "Count is 42 {source: raw/data.json}\n",  # missing locator on numerical
        )
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "pass"
        assert result["status"] == "warn"
        assert result["kind"] == "FORMAT"


def test_mixed_ok_and_fail() -> None:
    """Mix of ok and fail sub-findings → overall fail/DATA_INTEGRITY."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "raw" / "data.json"
        _write(artifact, json.dumps({"score": 0.95, "count": 42}))
        finding = root / "findings" / "report.md"
        _write(
            finding,
            "Score is 0.95 {source: raw/data.json $.score} good. "
            "Count is 999 {source: raw/data.json $.count} wrong.\n",
        )
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "fail"
        assert result["status"] == "fail"
        assert result["kind"] == "DATA_INTEGRITY"


# ---------------------------------------------------------------------------
# Line-locator tests (R-1 from Phase 2 review)
# ---------------------------------------------------------------------------


def test_line_locator_match() -> None:
    """Line-number locator pointing to a line with a matching value → ok."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data_file = root / "raw" / "data.csv"
        _write(data_file, "header\n42.5\nfooter\n")
        finding = root / "findings" / "report.md"
        _write(finding, "Value is 42.5 {source: raw/data.csv :2}\n")
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["status"] == "ok", f"expected ok, got {result}"
        sf = result["sub_findings"][0]
        assert sf["status"] == "ok"
        assert sf["kind"] == "DATA_INTEGRITY"
        assert "match" in sf["detail"]


def test_line_locator_out_of_range() -> None:
    """Line-number locator exceeding file length → fail/DATA_INTEGRITY."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data_file = root / "raw" / "data.csv"
        _write(data_file, "line1\nline2\nline3\n")
        finding = root / "findings" / "report.md"
        _write(finding, "Value is 99 {source: raw/data.csv :999}\n")
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        assert result["result"] == "fail", f"expected fail, got {result}"
        sf = result["sub_findings"][0]
        assert sf["status"] == "fail"
        assert sf["kind"] == "DATA_INTEGRITY"
        assert "does not exist" in sf["detail"]


def test_line_locator_no_number() -> None:
    """Line-number locator pointing to a line with no number → warn/FORMAT."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data_file = root / "raw" / "data.csv"
        _write(data_file, "header\njust text here\nfooter\n")
        finding = root / "findings" / "report.md"
        _write(finding, "Value is 5 {source: raw/data.csv :2}\n")
        deliverables = _make_deliverables(["findings/report.md"])
        result = _check_source_tags_resolve(root, deliverables)
        sf = result["sub_findings"][0]
        assert sf["status"] == "warn"
        assert sf["kind"] == "FORMAT"
        assert "no number found" in sf["detail"]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        ("test_no_source_tags", test_no_source_tags),
        ("test_scalar_tag_value_match", test_scalar_tag_value_match),
        ("test_scalar_tag_value_mismatch", test_scalar_tag_value_mismatch),
        ("test_scalar_tag_missing_locator", test_scalar_tag_missing_locator),
        (
            "test_scalar_tag_non_numerical_no_locator",
            test_scalar_tag_non_numerical_no_locator,
        ),
        ("test_scalar_tag_unresolvable_path", test_scalar_tag_unresolvable_path),
        ("test_scalar_tag_unresolvable_locator", test_scalar_tag_unresolvable_locator),
        (
            "test_scalar_tag_non_scalar_resolution",
            test_scalar_tag_non_scalar_resolution,
        ),
        ("test_source_table_tag_resolution", test_source_table_tag_resolution),
        ("test_source_table_tag_non_scalar_ok", test_source_table_tag_non_scalar_ok),
        ("test_identifier_not_extracted", test_identifier_not_extracted),
        ("test_code_fenced_tags_skipped", test_code_fenced_tags_skipped),
        ("test_inline_code_tags_skipped", test_inline_code_tags_skipped),
        ("test_tolerance_matching_close", test_tolerance_matching_close),
        ("test_path_escapes_root", test_path_escapes_root),
        ("test_non_dict_json", test_non_dict_json),
        ("test_no_layer1_deliverables", test_no_layer1_deliverables),
        ("test_exact_value_match", test_exact_value_match),
        ("test_percentage_value", test_percentage_value),
        ("test_scientific_notation", test_scientific_notation),
        ("test_multiple_tags_one_line", test_multiple_tags_one_line),
        ("test_source_table_unresolvable_path", test_source_table_unresolvable_path),
        ("test_mixed_ok_and_warn", test_mixed_ok_and_warn),
        ("test_mixed_ok_and_fail", test_mixed_ok_and_fail),
        ("test_line_locator_match", test_line_locator_match),
        ("test_line_locator_out_of_range", test_line_locator_out_of_range),
        ("test_line_locator_no_number", test_line_locator_no_number),
    ]
    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  PASS: {name}")
        except Exception as e:
            failed += 1
            print(f"  FAIL: {name} — {e}")
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
