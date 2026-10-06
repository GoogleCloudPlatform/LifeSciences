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

"""Tests for Phase 3 validation improvements.

Phase 3a: Report Headings (#102) — heading-text variance detection.
Phase 3b: layer_0_classes (#103) — required/authorized split.
Phase 3c: Relay Addressing (#104) — label-format checking.
Phase 3d: Path Resolution (#109) — root-resolvable link detection.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.commands.validate import (
    _check_deliverables_exist,
    _check_paths_resolve,
    _check_relay_coverage,
    _check_report_headings,
    _collect_relay_codes,
)
from dde.core.controlstore import normalize_deliverables

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str | bytes) -> str:
    """Write a file and return its sha256."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


# ===========================================================================
# Phase 3a: Report Headings (#102)
# ===========================================================================


def test_report_headings_key_findings_present() -> None:
    """Layer 1 file with ## Key Findings and WO reference → pass."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        finding = root / "findings" / "report.md"
        content = (
            "# Report WO-001-r1\n\n"
            "## Summary\n\nSummary text.\n\n"
            "## Key Findings\n\nFindings text.\n\n"
            "## Implications\n\nImplications text.\n"
        )
        _write(finding, content)
        deliverables = {"layer_1": ["findings/report.md"]}
        result = _check_report_headings(root, deliverables, "WO-001", 1)
        assert result["result"] == "pass", f"expected pass, got {result}"
        assert result["status"] == "ok"
        assert result["kind"] == "COMPLETENESS"
        assert "heading_variants" not in result["detail"]
        assert "missing_headings" not in result["detail"]
        print("  PASS: report_headings — Key Findings present → pass")


def test_report_headings_variant_heading() -> None:
    """Layer 1 file with variant heading between Summary and Implications → warn/CONVENTION."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        finding = root / "findings" / "report.md"
        content = (
            "# Report WO-001-r1\n\n"
            "## Summary\n\nSummary text.\n\n"
            "## Per-Target Assessments\n\nAssessment text.\n\n"
            "## Implications\n\nImplications text.\n"
        )
        _write(finding, content)
        deliverables = {"layer_1": ["findings/report.md"]}
        result = _check_report_headings(root, deliverables, "WO-001", 1)
        assert result["result"] == "pass", f"expected pass, got {result}"
        assert result["status"] == "warn"
        assert result["kind"] == "CONVENTION"
        assert len(result["detail"]["heading_variants"]) == 1
        variant = result["detail"]["heading_variants"][0]
        assert variant["found_heading"] == "## Per-Target Assessments"
        print("  PASS: report_headings — variant heading → warn/CONVENTION")


def test_report_headings_no_equivalent() -> None:
    """Layer 1 file with no ## Key Findings and no plausible equivalent → fail/COMPLETENESS."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        finding = root / "findings" / "report.md"
        content = (
            "# Report WO-001-r1\n\n"
            "## Summary\n\nSummary text.\n\n"
            "## Implications\n\nImplications text.\n"
        )
        _write(finding, content)
        deliverables = {"layer_1": ["findings/report.md"]}
        result = _check_report_headings(root, deliverables, "WO-001", 1)
        assert result["result"] == "fail", f"expected fail, got {result}"
        assert result["status"] == "fail"
        assert result["kind"] == "COMPLETENESS"
        assert "findings/report.md" in result["detail"]["missing_headings"]
        print("  PASS: report_headings — no equivalent → fail/COMPLETENESS")


def test_report_headings_missing_ref_and_variant() -> None:
    """File missing WO reference but has variant heading → fail (worst of both)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        finding = root / "findings" / "report.md"
        content = (
            "# Report\n\n"  # no WO reference
            "## Summary\n\nSummary text.\n\n"
            "## Analysis Results\n\nResults text.\n\n"
            "## Implications\n\nImplications text.\n"
        )
        _write(finding, content)
        deliverables = {"layer_1": ["findings/report.md"]}
        result = _check_report_headings(root, deliverables, "WO-001", 1)
        assert result["result"] == "fail", f"expected fail, got {result}"
        assert result["status"] == "fail"
        assert result["kind"] == "COMPLETENESS"
        assert "files_missing_reference" in result["detail"]
        assert "heading_variants" in result["detail"]
        print("  PASS: report_headings — missing ref + variant → fail")


# ===========================================================================
# Phase 3b: layer_0_classes Semantics (#103)
# ===========================================================================


def test_required_classes_backward_compat() -> None:
    """layer_0_classes maps to required_classes in normalize_deliverables."""
    deliverables = {
        "layer_0_classes": ["dde.alphafold", "dde.fpocket"],
        "layer_1": ["findings/report.md"],
    }
    normalized = normalize_deliverables(deliverables)
    # required_classes should be populated
    assert "required_classes" in normalized
    assert normalized["required_classes"] == ["dde.alphafold", "dde.fpocket"]
    # layer_0_classes backward compat
    assert normalized["layer_0_classes"] == ["dde.alphafold", "dde.fpocket"]
    print("  PASS: normalize_deliverables — layer_0_classes → required_classes")


def test_required_classes_from_layer_0() -> None:
    """layer_0 (shortest alias) maps to required_classes."""
    deliverables = {
        "layer_0": ["dde.alphafold"],
    }
    normalized = normalize_deliverables(deliverables)
    assert normalized["required_classes"] == ["dde.alphafold"]
    assert normalized["layer_0_classes"] == ["dde.alphafold"]
    assert "layer_0" not in normalized
    print("  PASS: normalize_deliverables — layer_0 → required_classes")


def test_required_classes_takes_priority() -> None:
    """required_classes takes priority over layer_0_classes."""
    deliverables = {
        "required_classes": ["dde.fpocket"],
        "layer_0_classes": ["dde.alphafold"],  # should be dropped
    }
    normalized = normalize_deliverables(deliverables)
    assert normalized["required_classes"] == ["dde.fpocket"]
    assert normalized["layer_0_classes"] == ["dde.fpocket"]
    print("  PASS: normalize_deliverables — required_classes priority")


def test_authorized_classes_missing_ok() -> None:
    """Missing authorized class does not cause a failure in _check_deliverables_exist."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Create structures dir with an artifact (dde.structures is recognized)
        art_dir = root / "raw" / "structures"
        art_dir.mkdir(parents=True)
        _write(art_dir / "model.pdb", "ATOM mock")
        _write(
            art_dir / "model.meta.json",
            json.dumps(
                {
                    "outputs": [
                        {
                            "sha256": hashlib.sha256(b"ATOM mock").hexdigest(),
                            "path": "model.pdb",
                        }
                    ],
                }
            ),
        )
        # dde.genomics dir does NOT exist (authorized but missing → ok)
        deliverables = normalize_deliverables(
            {
                "required_classes": ["dde.structures"],
                "authorized_classes": ["dde.genomics"],
                "layer_1": [],
            }
        )
        result = _check_deliverables_exist(root, deliverables)
        assert result["result"] == "pass", f"expected pass, got {result}"
        assert result["status"] == "ok"
        print("  PASS: authorized_classes missing → pass (no fail)")


def test_not_applicable_skip() -> None:
    """required_classes entry with not_applicable → skip with reason."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # structures present
        art_dir = root / "raw" / "structures"
        art_dir.mkdir(parents=True)
        _write(art_dir / "model.pdb", "ATOM mock")
        _write(
            art_dir / "model.meta.json",
            json.dumps(
                {
                    "outputs": [
                        {
                            "sha256": hashlib.sha256(b"ATOM mock").hexdigest(),
                            "path": "model.pdb",
                        }
                    ],
                }
            ),
        )
        deliverables = normalize_deliverables(
            {
                "required_classes": [
                    "dde.structures",
                    {
                        "class": "dde.genomics",
                        "not_applicable": "no target-CID pathway",
                    },
                ],
                "layer_1": [],
            }
        )
        result = _check_deliverables_exist(root, deliverables)
        assert result["result"] == "pass", f"expected pass, got {result}"
        # Verify skipped entry is reported
        na_detail = result["detail"].get("not_applicable", [])
        assert len(na_detail) == 1
        assert na_detail[0]["class"] == "dde.genomics"
        assert "no target-CID pathway" in na_detail[0]["reason"]
        print("  PASS: not_applicable → skip with reason")


def test_not_applicable_preserved_in_normalization() -> None:
    """not_applicable dicts are preserved in required_classes, not flattened."""
    deliverables = {
        "required_classes": [
            "dde.alphafold",
            {"class": "dde.pubchem-annotation", "not_applicable": "reason"},
        ],
    }
    normalized = normalize_deliverables(deliverables)
    req = normalized["required_classes"]
    assert req[0] == "dde.alphafold"
    assert isinstance(req[1], dict)
    assert req[1]["not_applicable"] == "reason"
    # layer_0_classes should have both as strings
    assert "dde.pubchem-annotation" in normalized["layer_0_classes"]
    print("  PASS: not_applicable preserved in required_classes")


# ===========================================================================
# Phase 3c: Relay Addressing (#104)
# ===========================================================================


def _setup_relay_project(tmp: str, finding_content: str, relay_code: str) -> Path:
    """Create a minimal project with a relay code and a finding."""
    root = Path(tmp)
    # Create artifact class dir with a sidecar containing mandatory_relays
    art_dir = root / "raw" / "compounds"
    art_dir.mkdir(parents=True)
    _write(art_dir / "mol.sdf", "fake sdf data")
    meta = {
        "outputs": [
            {"sha256": hashlib.sha256(b"fake sdf data").hexdigest(), "path": "mol.sdf"}
        ],
        "mandatory_relays": [{"code": relay_code, "message": "check this"}],
    }
    _write(art_dir / "mol.meta.json", json.dumps(meta))
    # Create the finding
    finding = root / "findings" / "report.md"
    _write(finding, finding_content)
    return root


def test_relay_label_format_ok() -> None:
    """Relay code in standard label format → pass/ok."""
    with tempfile.TemporaryDirectory() as tmp:
        code = "compound.alerts_not_toxicology"
        content = (
            "# Report\n\n"
            f"**Relay: `{code}`**\n"
            "The structural alerts are pharmacological.\n"
        )
        root = _setup_relay_project(tmp, content, code)
        deliverables = {
            "layer_0_classes": ["dde.compounds"],
            "layer_1": ["findings/report.md"],
        }
        result = _check_relay_coverage(root, deliverables)
        assert result["result"] == "pass", f"expected pass, got {result}"
        assert result["status"] == "ok"
        assert "sub_findings" not in result
        print("  PASS: relay label format ok → pass/ok")


def test_relay_found_no_label() -> None:
    """Relay code found in text but not in label format → warn/FORMAT."""
    with tempfile.TemporaryDirectory() as tmp:
        code = "compound.alerts_not_toxicology"
        content = f"# Report\n\nWe addressed the relay {code} in this section.\n"
        root = _setup_relay_project(tmp, content, code)
        deliverables = {
            "layer_0_classes": ["dde.compounds"],
            "layer_1": ["findings/report.md"],
        }
        result = _check_relay_coverage(root, deliverables)
        assert result["result"] == "pass", f"expected pass, got {result}"
        assert result["status"] == "warn"
        assert result["kind"] == "FORMAT"
        assert len(result["sub_findings"]) == 1
        sf = result["sub_findings"][0]
        assert sf["code"] == code
        assert sf["status"] == "warn"
        assert sf["kind"] == "FORMAT"
        print("  PASS: relay found without label → warn/FORMAT")


def test_relay_not_found() -> None:
    """Relay code not found at all → fail/COMPLETENESS (existing behavior)."""
    with tempfile.TemporaryDirectory() as tmp:
        code = "compound.alerts_not_toxicology"
        content = "# Report\n\nNo mention of the relay code.\n"
        root = _setup_relay_project(tmp, content, code)
        deliverables = {
            "layer_0_classes": ["dde.compounds"],
            "layer_1": ["findings/report.md"],
        }
        result = _check_relay_coverage(root, deliverables)
        assert result["result"] == "fail", f"expected fail, got {result}"
        assert result["status"] == "fail"
        assert result["kind"] == "COMPLETENESS"
        assert code in result["detail"]["unaddressed"]
        print("  PASS: relay not found → fail/COMPLETENESS")


def test_collect_relay_codes_helper() -> None:
    """_collect_relay_codes extracts codes from sidecar files."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        art_dir = root / "raw" / "compounds"
        art_dir.mkdir(parents=True)
        _write(art_dir / "mol.sdf", "data")
        meta = {
            "outputs": [
                {"sha256": hashlib.sha256(b"data").hexdigest(), "path": "mol.sdf"}
            ],
            "mandatory_relays": [
                {"code": "relay.one", "message": "msg1"},
                {"code": "relay.two", "message": "msg2"},
            ],
        }
        _write(art_dir / "mol.meta.json", json.dumps(meta))
        deliverables = {"layer_0_classes": ["dde.compounds"]}
        codes = _collect_relay_codes(root, deliverables)
        assert codes == {"relay.one", "relay.two"}
        print("  PASS: _collect_relay_codes extracts codes")


# ===========================================================================
# Phase 3d: Path Resolution (#109)
# ===========================================================================


def test_root_resolvable_link() -> None:
    """Broken link that resolves from project root → warn/CONVENTION."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Create the target file at the root level
        target = root / "raw" / "data" / "file.json"
        _write(target, '{"data": true}')
        # Create finding in a subdirectory — link uses root-relative path
        finding = root / "findings" / "sub" / "report.md"
        content = "See [data file](raw/data/file.json) for details.\n"
        _write(finding, content)
        deliverables = {"layer_1": ["findings/sub/report.md"]}
        result = _check_paths_resolve(root, deliverables)
        # The link doesn't resolve from the file's directory, but DOES
        # resolve from the project root → warn/CONVENTION.
        assert result["result"] == "pass", f"expected pass, got {result}"
        assert result["status"] == "warn"
        assert result["kind"] == "CONVENTION"
        broken = result["detail"]["broken_links"]
        assert len(broken) == 1
        assert broken[0]["would_resolve_from_root"] is True
        assert "suggested_fix" in broken[0]
        print("  PASS: root-resolvable link → warn/CONVENTION")


def test_truly_broken_link() -> None:
    """Broken link that resolves nowhere → fail/DATA_INTEGRITY."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        finding = root / "findings" / "report.md"
        content = "See [data](nonexistent/file.json) for details.\n"
        _write(finding, content)
        deliverables = {"layer_1": ["findings/report.md"]}
        result = _check_paths_resolve(root, deliverables)
        assert result["result"] == "fail", f"expected fail, got {result}"
        assert result["status"] == "fail"
        assert result["kind"] == "DATA_INTEGRITY"
        broken = result["detail"]["broken_links"]
        assert len(broken) == 1
        assert "would_resolve_from_root" not in broken[0]
        print("  PASS: truly broken link → fail/DATA_INTEGRITY")


def test_mixed_broken_links() -> None:
    """Mix of root-resolvable and truly broken → fail/DATA_INTEGRITY."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Create root-resolvable target
        _write(root / "raw" / "data.json", '{"x": 1}')
        finding = root / "findings" / "sub" / "report.md"
        content = (
            "See [data](raw/data.json) for details.\n"
            "Also see [other](totally/missing.md).\n"
        )
        _write(finding, content)
        deliverables = {"layer_1": ["findings/sub/report.md"]}
        result = _check_paths_resolve(root, deliverables)
        # One root-resolvable + one truly broken → fail
        assert result["result"] == "fail", f"expected fail, got {result}"
        assert result["status"] == "fail"
        assert result["kind"] == "DATA_INTEGRITY"
        print("  PASS: mixed broken links → fail/DATA_INTEGRITY")


def test_all_links_resolve_ok() -> None:
    """All links resolve correctly → pass/ok (unchanged behavior)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / "findings" / "other.md", "# Other")
        finding = root / "findings" / "report.md"
        content = "See [other report](other.md) for details.\n"
        _write(finding, content)
        deliverables = {"layer_1": ["findings/report.md"]}
        result = _check_paths_resolve(root, deliverables)
        assert result["result"] == "pass"
        assert result["status"] == "ok"
        assert result["kind"] == "DATA_INTEGRITY"
        print("  PASS: all links resolve → pass/ok")


# ===========================================================================
# Runner
# ===========================================================================


if __name__ == "__main__":
    print("Phase 3a: Report Headings (#102)")
    test_report_headings_key_findings_present()
    test_report_headings_variant_heading()
    test_report_headings_no_equivalent()
    test_report_headings_missing_ref_and_variant()

    print("\nPhase 3b: layer_0_classes Semantics (#103)")
    test_required_classes_backward_compat()
    test_required_classes_from_layer_0()
    test_required_classes_takes_priority()
    test_authorized_classes_missing_ok()
    test_not_applicable_skip()
    test_not_applicable_preserved_in_normalization()

    print("\nPhase 3c: Relay Addressing (#104)")
    test_relay_label_format_ok()
    test_relay_found_no_label()
    test_relay_not_found()
    test_collect_relay_codes_helper()

    print("\nPhase 3d: Path Resolution (#109)")
    test_root_resolvable_link()
    test_truly_broken_link()
    test_mixed_broken_links()
    test_all_links_resolve_ok()

    print("\nAll Phase 3 tests passed!")
