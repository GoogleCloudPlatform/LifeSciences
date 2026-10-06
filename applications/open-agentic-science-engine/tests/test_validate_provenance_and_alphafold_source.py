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

"""Verification tests for #147 (provenance sidecar false positives) and #150
(alphafold source field unprefixed).

These tests exercise the fixed functions directly with mock filesystem
layouts, confirming both positive and negative cases.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.commands.validate import (
    _check_analysis_citations,
    _check_deliverables_exist,
    _check_provenance_valid,
    _check_relay_coverage,
    _overall_verdict,
)
from dde.core.provenance import Sidecar, write_analysis


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, content: str | bytes) -> str:
    """Write a file and return its sha256."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return _sha256(content)


# ---------------------------------------------------------------------------
# Bug #147: _check_provenance_valid — scan-and-match approach
# ---------------------------------------------------------------------------


def test_gtex_layout() -> None:
    """GTEx fetch-style layout: artifact has .gtex infix, sidecar does not.

    Layout:
      raw/gtex/GENE.gtex.json    (artifact)
      raw/gtex/GENE.meta.json    (sidecar, outputs[] covers GENE.gtex.json)
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        gtex_dir = root / "raw" / "gtex"
        gtex_dir.mkdir(parents=True)

        # Write artifact
        artifact_content = b'{"tissue": "liver", "tpm": 42.5}'
        art_sha = _write(gtex_dir / "GENE.gtex.json", artifact_content)

        # Write sidecar — note: NOT "GENE.gtex.json.meta.json", but "GENE.meta.json"
        sidecar = {
            "tool": "gtex",
            "subcommand": "fetch",
            "outputs": [
                {
                    "path": "GENE.gtex.json",
                    "sha256": art_sha,
                    "bytes": len(artifact_content),
                },
            ],
        }
        _write(gtex_dir / "GENE.meta.json", json.dumps(sidecar))

        deliverables = {"layer_0_classes": ["gtex"]}
        result = _check_provenance_valid(root, deliverables)

        assert result["result"] == "pass", f"Expected pass, got: {result}"
        assert result["status"] == "ok"
        assert result["kind"] == "DATA_INTEGRITY"
        assert result["detail"]["artifacts_checked"] == 1
        print("  PASS: gtex layout — shared sidecar matched by sha256")


def test_expression_layout() -> None:
    """Expression layout: two artifacts, one shared sidecar.

    Layout:
      raw/expression/ENSG.single-cell.json
      raw/expression/ENSG.hpa.json
      raw/expression/ENSG.meta.json   (sidecar covering both)

    This reproduces the core #147 bug: two artifact files share a single
    sidecar whose name does NOT match either artifact's filename. The old
    code would look for ``ENSG.hpa.json.meta.json`` and fail.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        expr_dir = root / "raw" / "expression"
        expr_dir.mkdir(parents=True)

        sc_content = b'{"cell_type": "T-cell", "nTPM": 15.3}'
        sc_sha = _write(expr_dir / "ENSG.single-cell.json", sc_content)

        hpa_content = b'{"tissue": "brain", "tpm": 8.1}'
        hpa_sha = _write(expr_dir / "ENSG.hpa.json", hpa_content)

        sidecar = {
            "tool": "expression",
            "subcommand": "fetch",
            "outputs": [
                {
                    "path": "ENSG.single-cell.json",
                    "sha256": sc_sha,
                    "bytes": len(sc_content),
                },
                {"path": "ENSG.hpa.json", "sha256": hpa_sha, "bytes": len(hpa_content)},
            ],
        }
        _write(expr_dir / "ENSG.meta.json", json.dumps(sidecar))

        deliverables = {"layer_0_classes": ["expression"]}
        result = _check_provenance_valid(root, deliverables)

        assert result["result"] == "pass", f"Expected pass, got: {result}"
        assert result["status"] == "ok"
        assert result["kind"] == "DATA_INTEGRITY"
        assert result["detail"]["artifacts_checked"] == 2
        print("  PASS: expression layout — two artifacts, one shared sidecar")


def test_structures_afdb_layout() -> None:
    """AlphaFold DB layout: three artifacts, one sidecar.

    Layout:
      raw/structures/AF-xxx.afdb.json
      raw/structures/AF-xxx.cif
      raw/structures/AF-xxx.pae.json
      raw/structures/AF-xxx.meta.json    (sidecar covering all three)
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        struct_dir = root / "raw" / "structures"
        struct_dir.mkdir(parents=True)

        afdb_content = b'{"uniprotAccession": "P04637"}'
        afdb_sha = _write(struct_dir / "AF-P04637-F1.afdb.json", afdb_content)

        cif_content = b"data_AF-P04637-F1\n_entry.id   AF-P04637-F1\n"
        cif_sha = _write(struct_dir / "AF-P04637-F1.cif", cif_content)

        pae_content = b'[{"predicted_aligned_error": [[0.5]]}]'
        pae_sha = _write(struct_dir / "AF-P04637-F1.pae.json", pae_content)

        sidecar = {
            "tool": "alphafold-db",
            "subcommand": "fetch",
            "outputs": [
                {
                    "path": "AF-P04637-F1.afdb.json",
                    "sha256": afdb_sha,
                    "bytes": len(afdb_content),
                },
                {
                    "path": "AF-P04637-F1.cif",
                    "sha256": cif_sha,
                    "bytes": len(cif_content),
                },
                {
                    "path": "AF-P04637-F1.pae.json",
                    "sha256": pae_sha,
                    "bytes": len(pae_content),
                },
            ],
        }
        _write(struct_dir / "AF-P04637-F1.meta.json", json.dumps(sidecar))

        deliverables = {"layer_0_classes": ["structures"]}
        result = _check_provenance_valid(root, deliverables)

        assert result["result"] == "pass", f"Expected pass, got: {result}"
        assert result["status"] == "ok"
        assert result["kind"] == "DATA_INTEGRITY"
        assert result["detail"]["artifacts_checked"] == 3
        print("  PASS: structures/AFDB layout — three artifacts, one shared sidecar")


def test_sc_meta_sidecar_layout() -> None:
    """Expression single-cell layout with .sc-meta.json sidecar.

    Layout:
      raw/expression/ENSG.single-cell.json   (artifact)
      raw/expression/ENSG.hpa.json           (artifact)
      raw/expression/ENSG.sc-meta.json       (sidecar, outputs[] covers both)

    Verifies that .sc-meta.json sidecars are recognised by the sidecar
    index and excluded from the artifact list, so provenance validation
    passes without false positives.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        expr_dir = root / "raw" / "expression"
        expr_dir.mkdir(parents=True)

        sc_content = b'{"cell_type": "T-cell", "nTPM": 15.3}'
        sc_sha = _write(expr_dir / "ENSG.single-cell.json", sc_content)

        hpa_content = b'{"tissue": "brain", "tpm": 8.1}'
        hpa_sha = _write(expr_dir / "ENSG.hpa.json", hpa_content)

        # Sidecar uses .sc-meta.json suffix (expression single-cell variant)
        sidecar = {
            "tool": "expression",
            "subcommand": "fetch-single-cell",
            "outputs": [
                {
                    "path": "ENSG.single-cell.json",
                    "sha256": sc_sha,
                    "bytes": len(sc_content),
                },
                {"path": "ENSG.hpa.json", "sha256": hpa_sha, "bytes": len(hpa_content)},
            ],
        }
        _write(expr_dir / "ENSG.sc-meta.json", json.dumps(sidecar))

        deliverables = {"layer_0_classes": ["expression"]}
        result = _check_provenance_valid(root, deliverables)

        assert result["result"] == "pass", f"Expected pass, got: {result}"
        assert result["status"] == "ok"
        assert result["kind"] == "DATA_INTEGRITY"
        assert result["detail"]["artifacts_checked"] == 2
        print(
            "  PASS: sc-meta.json sidecar layout — two artifacts, one .sc-meta.json sidecar"
        )


def test_missing_sidecar() -> None:
    """Negative case: artifact with no sidecar covering it at all."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        gtex_dir = root / "raw" / "gtex"
        gtex_dir.mkdir(parents=True)

        # Write artifact with no sidecar
        _write(gtex_dir / "ORPHAN.gtex.json", b'{"orphan": true}')

        deliverables = {"layer_0_classes": ["gtex"]}
        result = _check_provenance_valid(root, deliverables)

        assert result["result"] == "fail", f"Expected fail, got: {result}"
        assert result["status"] == "fail"
        assert result["kind"] == "DATA_INTEGRITY"
        issues = result["detail"]["issues"]
        assert len(issues) == 1
        assert "no provenance sidecar covers this artifact" in issues[0]["issue"]
        print("  PASS: missing sidecar correctly detected")


def test_sha256_mismatch() -> None:
    """Negative case: sidecar exists but sha256 doesn't match (data corruption)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        gtex_dir = root / "raw" / "gtex"
        gtex_dir.mkdir(parents=True)

        # Write artifact
        _write(gtex_dir / "GENE.gtex.json", b'{"tissue": "liver", "tpm": 42.5}')

        # Write sidecar with a WRONG sha256
        sidecar = {
            "tool": "gtex",
            "subcommand": "fetch",
            "outputs": [
                {
                    "path": "GENE.gtex.json",
                    "sha256": "0000deadbeef" * 5 + "00",
                    "bytes": 100,
                },
            ],
        }
        _write(gtex_dir / "GENE.meta.json", json.dumps(sidecar))

        deliverables = {"layer_0_classes": ["gtex"]}
        result = _check_provenance_valid(root, deliverables)

        assert result["result"] == "fail", f"Expected fail, got: {result}"
        assert result["status"] == "fail"
        assert result["kind"] == "DATA_INTEGRITY"
        issues = result["detail"]["issues"]
        assert len(issues) == 1
        assert "no provenance sidecar covers this artifact" in issues[0]["issue"]
        print("  PASS: sha256 mismatch correctly detected")


# ---------------------------------------------------------------------------
# Bug #150: _check_analysis_citations — source field resolution
# ---------------------------------------------------------------------------


def test_analysis_source_project_relative() -> None:
    """Positive case: analysis source field uses project-relative path and resolves."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        struct_dir = root / "raw" / "structures"
        struct_dir.mkdir(parents=True)

        # Write the source sidecar (what the analysis points to)
        sidecar = {"tool": "alphafold-db", "subcommand": "fetch", "outputs": []}
        _write(struct_dir / "AF-P04637-F1.meta.json", json.dumps(sidecar))

        # Write analysis with project-relative source (the fix)
        analysis = {
            "source": "raw/structures/AF-P04637-F1.meta.json",
            "threshold_set": "alphafold@default",
            "metrics": {},
            "assessment": {"verdict": "confident"},
        }
        _write(
            struct_dir / "AF-P04637-F1.alphafold.analysis.json", json.dumps(analysis)
        )

        deliverables = {"layer_0_classes": ["structures"]}
        result = _check_analysis_citations(root, deliverables)

        assert "status" in result
        assert "kind" in result
        # With the fixed source path, the source file should resolve
        source_issues = [
            i
            for i in result.get("detail", {}).get("issues", [])
            if "source reference does not resolve" in i.get("issue", "")
        ]
        assert not source_issues, (
            f"Unexpected source resolution issues: {source_issues}"
        )
        print("  PASS: project-relative source path resolves correctly")


def test_analysis_source_bare_name_fails() -> None:
    """Negative case: bare filename (the old bug) does NOT resolve from root."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        struct_dir = root / "raw" / "structures"
        struct_dir.mkdir(parents=True)

        # Write the source sidecar
        sidecar = {"tool": "alphafold-db", "subcommand": "fetch", "outputs": []}
        _write(struct_dir / "AF-P04637-F1.meta.json", json.dumps(sidecar))

        # Write analysis with BARE source name (the bug — before fix)
        analysis = {
            "source": "AF-P04637-F1.meta.json",  # bare name, not project-relative
            "threshold_set": "alphafold@default",
            "metrics": {},
            "assessment": {"verdict": "confident"},
        }
        _write(
            struct_dir / "AF-P04637-F1.alphafold.analysis.json", json.dumps(analysis)
        )

        deliverables = {"layer_0_classes": ["structures"]}
        result = _check_analysis_citations(root, deliverables)

        assert "status" in result
        assert "kind" in result
        # A bare filename resolves relative to project root, so
        # root / "AF-P04637-F1.meta.json" won't exist → should fail
        source_issues = [
            i
            for i in result.get("detail", {}).get("issues", [])
            if "source reference does not resolve" in i.get("issue", "")
        ]
        assert source_issues, "Bare filename should fail to resolve from project root"
        print("  PASS: bare filename correctly fails to resolve (confirms the bug)")


def test_analyze_prediction_source_project_relative() -> None:
    """Positive case: AF3 analysis source uses project-relative path."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        struct_dir = root / "raw" / "structures"
        struct_dir.mkdir(parents=True)

        # Write the AF3 summary (what the analysis points to)
        summary = {"ptm": 0.85, "iptm": None, "ranking_score": 0.9}
        _write(struct_dir / "AF3-test.summary.json", json.dumps(summary))

        # Write analysis with project-relative source (the fix)
        analysis = {
            "source": "raw/structures/AF3-test.summary.json",
            "threshold_set": "af3@default",
            "metrics": {},
            "assessment": {"verdict": "confident"},
        }
        _write(struct_dir / "AF3-test.alphafold.analysis.json", json.dumps(analysis))

        deliverables = {"layer_0_classes": ["structures"]}
        result = _check_analysis_citations(root, deliverables)

        assert "status" in result
        assert "kind" in result
        source_issues = [
            i
            for i in result.get("detail", {}).get("issues", [])
            if "source reference does not resolve" in i.get("issue", "")
        ]
        assert not source_issues, (
            f"Unexpected source resolution issues: {source_issues}"
        )
        print("  PASS: AF3 project-relative source path resolves correctly")


# ---------------------------------------------------------------------------
# Bug #166: work-order scoping for validation checks
# ---------------------------------------------------------------------------


def test_provenance_two_wos_scoped() -> None:
    """Two WOs in same directory — scoped validation only checks WO-A's artifacts."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        gtex_dir = root / "raw" / "gtex"
        gtex_dir.mkdir(parents=True)

        # WO-A artifact and sidecar
        art_a_content = b'{"tissue": "liver", "tpm": 42.5}'
        art_a_sha = _write(gtex_dir / "GENE-A.gtex.json", art_a_content)
        sidecar_a = {
            "tool": "gtex",
            "subcommand": "fetch",
            "work_order_id": "WO-A",
            "outputs": [
                {
                    "path": "GENE-A.gtex.json",
                    "sha256": art_a_sha,
                    "bytes": len(art_a_content),
                },
            ],
        }
        _write(gtex_dir / "GENE-A.meta.json", json.dumps(sidecar_a))

        # WO-B artifact and sidecar
        art_b_content = b'{"tissue": "brain", "tpm": 8.1}'
        art_b_sha = _write(gtex_dir / "GENE-B.gtex.json", art_b_content)
        sidecar_b = {
            "tool": "gtex",
            "subcommand": "fetch",
            "work_order_id": "WO-B",
            "outputs": [
                {
                    "path": "GENE-B.gtex.json",
                    "sha256": art_b_sha,
                    "bytes": len(art_b_content),
                },
            ],
        }
        _write(gtex_dir / "GENE-B.meta.json", json.dumps(sidecar_b))

        deliverables = {"layer_0_classes": ["gtex"]}
        result = _check_provenance_valid(root, deliverables, wo_id="WO-A")

        assert result["result"] == "pass", f"Expected pass, got: {result}"
        assert result["status"] == "ok"
        assert result["kind"] == "DATA_INTEGRITY"
        # Only WO-A's artifact should be checked; WO-B's skipped
        assert result["detail"]["artifacts_checked"] == 1
        print("  PASS: provenance — two WOs scoped, only WO-A checked")


def test_provenance_backward_compat_untagged() -> None:
    """Backward compatibility: untagged sidecars are included when scoping by WO."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        gtex_dir = root / "raw" / "gtex"
        gtex_dir.mkdir(parents=True)

        # WO-A tagged artifact and sidecar
        art_a_content = b'{"tissue": "liver", "tpm": 42.5}'
        art_a_sha = _write(gtex_dir / "GENE-A.gtex.json", art_a_content)
        sidecar_a = {
            "tool": "gtex",
            "subcommand": "fetch",
            "work_order_id": "WO-A",
            "outputs": [
                {
                    "path": "GENE-A.gtex.json",
                    "sha256": art_a_sha,
                    "bytes": len(art_a_content),
                },
            ],
        }
        _write(gtex_dir / "GENE-A.meta.json", json.dumps(sidecar_a))

        # Untagged artifact and sidecar (no work_order_id)
        art_old_content = b'{"tissue": "kidney", "tpm": 3.2}'
        art_old_sha = _write(gtex_dir / "GENE-OLD.gtex.json", art_old_content)
        sidecar_old = {
            "tool": "gtex",
            "subcommand": "fetch",
            "outputs": [
                {
                    "path": "GENE-OLD.gtex.json",
                    "sha256": art_old_sha,
                    "bytes": len(art_old_content),
                },
            ],
        }
        _write(gtex_dir / "GENE-OLD.meta.json", json.dumps(sidecar_old))

        deliverables = {"layer_0_classes": ["gtex"]}
        result = _check_provenance_valid(root, deliverables, wo_id="WO-A")

        assert result["result"] == "pass", f"Expected pass, got: {result}"
        assert result["status"] == "ok"
        assert result["kind"] == "DATA_INTEGRITY"
        # Both WO-A's and untagged artifacts should be checked
        assert result["detail"]["artifacts_checked"] == 2
        print("  PASS: provenance — backward compat, untagged sidecar included")


def test_relay_coverage_scoped() -> None:
    """Relay coverage scoped by WO — only WO-A's relays are checked."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        struct_dir = root / "raw" / "structures"
        struct_dir.mkdir(parents=True)
        findings_dir = root / "findings"
        findings_dir.mkdir(parents=True)

        # WO-A sidecar with afdb.partial_coverage relay
        sidecar_a = {
            "tool": "alphafold-db",
            "subcommand": "fetch",
            "work_order_id": "WO-A",
            "outputs": [],
            "mandatory_relays": [
                {"code": "afdb.partial_coverage", "message": "coverage is partial"},
            ],
        }
        _write(struct_dir / "AF-A.meta.json", json.dumps(sidecar_a))

        # WO-B sidecar with fpocket.single_conformation relay
        sidecar_b = {
            "tool": "fpocket",
            "subcommand": "analyze",
            "work_order_id": "WO-B",
            "outputs": [],
            "mandatory_relays": [
                {
                    "code": "fpocket.single_conformation",
                    "message": "single conformation",
                },
            ],
        }
        _write(struct_dir / "FP-B.meta.json", json.dumps(sidecar_b))

        # Layer 1 finding mentions WO-A's relay code only
        finding_content = (
            "# Finding\n\n"
            "This analysis addresses afdb.partial_coverage by scoping claims.\n"
        )
        _write(findings_dir / "finding.md", finding_content)

        deliverables = {
            "layer_0_classes": ["structures"],
            "layer_1": ["findings/finding.md"],
        }
        result = _check_relay_coverage(root, deliverables, wo_id="WO-A")

        assert result["result"] == "pass", f"Expected pass, got: {result}"
        # Phase 3c (#104): relay is found in text but not in label format
        # → status escalates from ok to warn/FORMAT.
        assert result["status"] == "warn"
        assert result["kind"] == "FORMAT"
        # Only WO-A's relay should be checked; WO-B's relay out of scope
        assert result["detail"]["codes_checked"] == 1
        print("  PASS: relay coverage — scoped to WO-A, WO-B's relay excluded")


def test_analysis_citations_scoped_two_wos() -> None:
    """Analysis citations scoped by WO — WO-B's analysis is excluded from WO-A check (#253)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        struct_dir = root / "raw" / "structures"
        struct_dir.mkdir(parents=True)

        # WO-A: valid analysis
        analysis_a = {
            "source": "raw/structures/AF-A.meta.json",
            "threshold_set": "alphafold@default",
            "metrics": {},
            "assessment": {"verdict": "confident"},
            "work_order_id": "WO-A",
        }
        _write(struct_dir / "AF-A.alphafold.analysis.json", json.dumps(analysis_a))
        # Create the source so it resolves
        _write(
            struct_dir / "AF-A.meta.json",
            json.dumps({"tool": "alphafold-db", "outputs": []}),
        )

        # WO-B: malformed analysis (missing source and threshold_set)
        analysis_b = {
            "metrics": {},
            "assessment": {"verdict": "fail"},
            "work_order_id": "WO-B",
        }
        _write(struct_dir / "AF-B.alphafold.analysis.json", json.dumps(analysis_b))

        deliverables = {"layer_0_classes": ["structures"]}

        # Scoped to WO-A: should PASS — WO-B's malformed file is excluded
        result_a = _check_analysis_citations(root, deliverables, wo_id="WO-A")
        assert result_a["result"] == "pass", f"Expected pass for WO-A, got: {result_a}"
        assert result_a["status"] == "ok"
        assert result_a["kind"] == "DATA_INTEGRITY"
        assert result_a["detail"]["analyses_checked"] == 1

        # Scoped to WO-B: should FAIL — WO-B's own malformed file is checked
        result_b = _check_analysis_citations(root, deliverables, wo_id="WO-B")
        assert result_b["result"] == "fail", f"Expected fail for WO-B, got: {result_b}"
        assert result_b["status"] == "fail"
        assert result_b["kind"] == "DATA_INTEGRITY"
        assert result_b["detail"]["analyses_checked"] == 1
        print("  PASS: analysis citations — two WOs scoped, cross-WO excluded")


def test_analysis_citations_backward_compat_untagged() -> None:
    """Backward compat: untagged analysis files are always checked regardless of wo_id (#253)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        struct_dir = root / "raw" / "structures"
        struct_dir.mkdir(parents=True)

        # Untagged analysis (no work_order_id — pre-#166 record)
        analysis_old = {
            "source": "raw/structures/OLD.meta.json",
            "threshold_set": "alphafold@default",
            "metrics": {},
            "assessment": {"verdict": "confident"},
        }
        _write(struct_dir / "OLD.alphafold.analysis.json", json.dumps(analysis_old))
        _write(
            struct_dir / "OLD.meta.json",
            json.dumps({"tool": "alphafold-db", "outputs": []}),
        )

        deliverables = {"layer_0_classes": ["structures"]}

        # Untagged file should be checked even when scoping to WO-A
        result = _check_analysis_citations(root, deliverables, wo_id="WO-A")
        assert result["result"] == "pass", f"Expected pass, got: {result}"
        assert result["status"] == "ok"
        assert result["kind"] == "DATA_INTEGRITY"
        assert result["detail"]["analyses_checked"] == 1
        print("  PASS: analysis citations — untagged file checked (backward compat)")


def test_analysis_citations_null_wo_checked() -> None:
    """Analysis with explicit work_order_id: null is treated as untagged (#253)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        struct_dir = root / "raw" / "structures"
        struct_dir.mkdir(parents=True)

        analysis = {
            "source": "raw/structures/X.meta.json",
            "threshold_set": "alphafold@default",
            "metrics": {},
            "assessment": {"verdict": "confident"},
            "work_order_id": None,
        }
        _write(struct_dir / "X.alphafold.analysis.json", json.dumps(analysis))
        _write(
            struct_dir / "X.meta.json",
            json.dumps({"tool": "alphafold-db", "outputs": []}),
        )

        deliverables = {"layer_0_classes": ["structures"]}

        # Explicit null work_order_id → treated as untagged → always checked
        result = _check_analysis_citations(root, deliverables, wo_id="WO-A")
        assert result["result"] == "pass", f"Expected pass, got: {result}"
        assert result["status"] == "ok"
        assert result["kind"] == "DATA_INTEGRITY"
        assert result["detail"]["analyses_checked"] == 1
        print("  PASS: analysis citations — null work_order_id treated as untagged")


def test_analysis_citations_no_wo_id_checks_all() -> None:
    """Without wo_id, all analysis files are checked (single-WO project case)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        struct_dir = root / "raw" / "structures"
        struct_dir.mkdir(parents=True)

        # Two analyses from different WOs
        for label in ("WO-A", "WO-B"):
            analysis = {
                "source": f"raw/structures/{label}.meta.json",
                "threshold_set": "alphafold@default",
                "metrics": {},
                "assessment": {"verdict": "confident"},
                "work_order_id": label,
            }
            _write(
                struct_dir / f"{label}.alphafold.analysis.json", json.dumps(analysis)
            )
            _write(
                struct_dir / f"{label}.meta.json",
                json.dumps({"tool": "alphafold-db", "outputs": []}),
            )

        deliverables = {"layer_0_classes": ["structures"]}

        # No wo_id → all analysis files checked
        result = _check_analysis_citations(root, deliverables)
        assert result["result"] == "pass", f"Expected pass, got: {result}"
        assert result["status"] == "ok"
        assert result["kind"] == "DATA_INTEGRITY"
        assert result["detail"]["analyses_checked"] == 2
        print("  PASS: analysis citations — no wo_id checks all files")


def test_sidecar_to_dict_work_order_id() -> None:
    """Sidecar.to_dict() includes work_order_id from env var."""
    import os

    # With env var set
    os.environ["DDE_WORK_ORDER_ID"] = "WO-TEST"
    try:
        sc = Sidecar(tool="test-tool", subcommand="fetch")
        d = sc.to_dict()
        assert d["work_order_id"] == "WO-TEST", (
            f"Expected WO-TEST, got: {d.get('work_order_id')}"
        )
    finally:
        del os.environ["DDE_WORK_ORDER_ID"]

    # Without env var
    sc2 = Sidecar(tool="test-tool", subcommand="fetch")
    d2 = sc2.to_dict()
    assert d2["work_order_id"] is None, f"Expected None, got: {d2.get('work_order_id')}"
    print("  PASS: Sidecar.to_dict() includes work_order_id")


def test_write_analysis_work_order_id() -> None:
    """write_analysis() includes work_order_id from env var."""
    import os

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        analysis_path = root / "test.analysis.json"

        os.environ["DDE_WORK_ORDER_ID"] = "WO-TEST"
        try:
            write_analysis(
                analysis_path,
                source="raw/structures/test.meta.json",
                threshold_set="test@default",
                thresholds_applied={"cutoff": 0.5},
                metrics={"score": 0.9},
                assessment={"verdict": "pass"},
            )
        finally:
            del os.environ["DDE_WORK_ORDER_ID"]

        data = json.loads(analysis_path.read_text(encoding="utf-8"))
        assert data["work_order_id"] == "WO-TEST", (
            f"Expected WO-TEST, got: {data.get('work_order_id')}"
        )
        print("  PASS: write_analysis() includes work_order_id")


# ---------------------------------------------------------------------------
# Bug #283: _check_deliverables_exist — vacuous pass with WO attribution
# ---------------------------------------------------------------------------


def test_deliverables_exist_vacuous_pass_blocked() -> None:
    """Exact repro from #283: WO-A declares genomics but produces zero artifacts.

    A different WO's correctly-sidecared artifact sits in the same shared
    directory.  Before the fix, deliverables_exist would PASS vacuously.
    After the fix it must FAIL.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        genomics_dir = root / "raw" / "genomics"
        genomics_dir.mkdir(parents=True)

        # WO-999 artifact and sidecar — belongs to a different WO
        art_content = b'{"gene": "TP53", "variant": "R175H"}'
        art_sha = _sha256(art_content)
        _write(genomics_dir / "TP53.variant.json", art_content)
        sidecar = {
            "tool": "gwas",
            "subcommand": "fetch",
            "work_order_id": "WO-999",
            "outputs": [
                {
                    "path": "TP53.variant.json",
                    "sha256": art_sha,
                    "bytes": len(art_content),
                },
            ],
        }
        _write(genomics_dir / "TP53.meta.json", json.dumps(sidecar))

        # WO-A declares genomics but has ZERO artifacts of its own
        deliverables = {"layer_0_classes": ["genomics"]}
        result = _check_deliverables_exist(root, deliverables, wo_id="WO-A")

        assert result["result"] == "fail", (
            f"Expected fail (vacuous pass blocked), got: {result}"
        )
        assert result["status"] == "fail"
        assert result["kind"] == "COMPLETENESS"
        assert any(
            "no artifacts attributed" in m for m in result["detail"]["missing"]
        ), (
            f"Expected 'no artifacts attributed' in missing, got: {result['detail']['missing']}"
        )
        print("  PASS: #283 vacuous-pass repro correctly blocked")


def test_deliverables_exist_own_artifact_passes() -> None:
    """Legitimate case: WO-A has its own artifact alongside WO-B's artifact.

    Both WOs write to the same shared directory.  WO-A's deliverables_exist
    should PASS because it genuinely produced an artifact.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        genomics_dir = root / "raw" / "genomics"
        genomics_dir.mkdir(parents=True)

        # WO-A artifact and sidecar
        art_a_content = b'{"gene": "BRCA1", "variant": "5382insC"}'
        art_a_sha = _sha256(art_a_content)
        _write(genomics_dir / "BRCA1.variant.json", art_a_content)
        sidecar_a = {
            "tool": "gwas",
            "subcommand": "fetch",
            "work_order_id": "WO-A",
            "outputs": [
                {
                    "path": "BRCA1.variant.json",
                    "sha256": art_a_sha,
                    "bytes": len(art_a_content),
                },
            ],
        }
        _write(genomics_dir / "BRCA1.meta.json", json.dumps(sidecar_a))

        # WO-B artifact and sidecar (different WO, same directory)
        art_b_content = b'{"gene": "TP53", "variant": "R175H"}'
        art_b_sha = _sha256(art_b_content)
        _write(genomics_dir / "TP53.variant.json", art_b_content)
        sidecar_b = {
            "tool": "gwas",
            "subcommand": "fetch",
            "work_order_id": "WO-B",
            "outputs": [
                {
                    "path": "TP53.variant.json",
                    "sha256": art_b_sha,
                    "bytes": len(art_b_content),
                },
            ],
        }
        _write(genomics_dir / "TP53.meta.json", json.dumps(sidecar_b))

        deliverables = {"layer_0_classes": ["genomics"]}
        result = _check_deliverables_exist(root, deliverables, wo_id="WO-A")

        assert result["result"] == "pass", (
            f"Expected pass (own artifact present), got: {result}"
        )
        assert result["status"] == "ok"
        assert result["kind"] == "COMPLETENESS"
        print("  PASS: WO with own artifact in shared directory passes correctly")


def test_deliverables_exist_backward_compat_untagged() -> None:
    """Backward compatibility: untagged artifact counts toward satisfying the check.

    An artifact with no work_order_id on its sidecar (pre-#166 record)
    should still satisfy deliverables_exist, exactly like the other checks.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        genomics_dir = root / "raw" / "genomics"
        genomics_dir.mkdir(parents=True)

        # Untagged artifact and sidecar (no work_order_id)
        art_content = b'{"gene": "EGFR", "variant": "T790M"}'
        art_sha = _sha256(art_content)
        _write(genomics_dir / "EGFR.variant.json", art_content)
        sidecar = {
            "tool": "gwas",
            "subcommand": "fetch",
            "outputs": [
                {
                    "path": "EGFR.variant.json",
                    "sha256": art_sha,
                    "bytes": len(art_content),
                },
            ],
        }
        _write(genomics_dir / "EGFR.meta.json", json.dumps(sidecar))

        deliverables = {"layer_0_classes": ["genomics"]}
        result = _check_deliverables_exist(root, deliverables, wo_id="WO-A")

        assert result["result"] == "pass", (
            f"Expected pass (untagged artifact), got: {result}"
        )
        assert result["status"] == "ok"
        assert result["kind"] == "COMPLETENESS"
        print(
            "  PASS: untagged artifact counts toward deliverables_exist (backward compat)"
        )


def test_deliverables_exist_single_wo_unaffected() -> None:
    """Single-WO project: only one WO ever writes to the directory.

    This must behave exactly as before — purely additive for the normal,
    non-shared case.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        genomics_dir = root / "raw" / "genomics"
        genomics_dir.mkdir(parents=True)

        # Single WO artifact and sidecar
        art_content = b'{"gene": "KRAS", "variant": "G12D"}'
        art_sha = _sha256(art_content)
        _write(genomics_dir / "KRAS.variant.json", art_content)
        sidecar = {
            "tool": "gwas",
            "subcommand": "fetch",
            "work_order_id": "WO-ONLY",
            "outputs": [
                {
                    "path": "KRAS.variant.json",
                    "sha256": art_sha,
                    "bytes": len(art_content),
                },
            ],
        }
        _write(genomics_dir / "KRAS.meta.json", json.dumps(sidecar))

        deliverables = {"layer_0_classes": ["genomics"]}
        result = _check_deliverables_exist(root, deliverables, wo_id="WO-ONLY")

        assert result["result"] == "pass", f"Expected pass (single WO), got: {result}"
        assert result["status"] == "ok"
        assert result["kind"] == "COMPLETENESS"
        print("  PASS: single-WO project unaffected")


def test_deliverables_exist_no_wo_id_checks_all() -> None:
    """Without wo_id, all artifacts count (pre-scoping behavior preserved)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        genomics_dir = root / "raw" / "genomics"
        genomics_dir.mkdir(parents=True)

        # Artifact tagged to some WO — no scoping requested
        art_content = b'{"gene": "ALK", "variant": "EML4-ALK"}'
        art_sha = _sha256(art_content)
        _write(genomics_dir / "ALK.variant.json", art_content)
        sidecar = {
            "tool": "gwas",
            "subcommand": "fetch",
            "work_order_id": "WO-X",
            "outputs": [
                {
                    "path": "ALK.variant.json",
                    "sha256": art_sha,
                    "bytes": len(art_content),
                },
            ],
        }
        _write(genomics_dir / "ALK.meta.json", json.dumps(sidecar))

        deliverables = {"layer_0_classes": ["genomics"]}
        # No wo_id — should pass regardless of which WO the artifact belongs to
        result = _check_deliverables_exist(root, deliverables)

        assert result["result"] == "pass", f"Expected pass (no wo_id), got: {result}"
        assert result["status"] == "ok"
        assert result["kind"] == "COMPLETENESS"
        print("  PASS: no wo_id checks all artifacts (pre-scoping behavior)")


# ---------------------------------------------------------------------------
# _overall_verdict tests (#105 severity model)
# ---------------------------------------------------------------------------


def test_overall_verdict_all_pass():
    checks = [
        {"name": "a", "result": "pass", "status": "ok", "kind": "DATA_INTEGRITY"},
        {"name": "b", "result": "pass", "status": "ok", "kind": "COMPLETENESS"},
    ]
    assert _overall_verdict(checks) == "pass"


def test_overall_verdict_any_fail():
    checks = [
        {"name": "a", "result": "pass", "status": "ok", "kind": "DATA_INTEGRITY"},
        {"name": "b", "result": "fail", "status": "fail", "kind": "COMPLETENESS"},
    ]
    assert _overall_verdict(checks) == "fail"


def test_overall_verdict_warns_only():
    checks = [
        {"name": "a", "result": "pass", "status": "ok", "kind": "DATA_INTEGRITY"},
        {"name": "b", "result": "pass", "status": "warn", "kind": "CONVENTION"},
    ]
    assert _overall_verdict(checks) == "pass_with_warnings"


def test_overall_verdict_all_skip():
    checks = [
        {"name": "a", "result": "skip", "status": "skip", "kind": None},
    ]
    assert _overall_verdict(checks) == "fail"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        # #147 positive cases
        ("test_gtex_layout", test_gtex_layout),
        ("test_expression_layout", test_expression_layout),
        ("test_structures_afdb_layout", test_structures_afdb_layout),
        ("test_sc_meta_sidecar_layout", test_sc_meta_sidecar_layout),
        # #147 negative cases
        ("test_missing_sidecar", test_missing_sidecar),
        ("test_sha256_mismatch", test_sha256_mismatch),
        # #150 positive cases
        (
            "test_analysis_source_project_relative",
            test_analysis_source_project_relative,
        ),
        ("test_analysis_source_bare_name_fails", test_analysis_source_bare_name_fails),
        (
            "test_analyze_prediction_source_project_relative",
            test_analyze_prediction_source_project_relative,
        ),
        # #166 work-order scoping
        ("test_provenance_two_wos_scoped", test_provenance_two_wos_scoped),
        (
            "test_provenance_backward_compat_untagged",
            test_provenance_backward_compat_untagged,
        ),
        ("test_relay_coverage_scoped", test_relay_coverage_scoped),
        ("test_sidecar_to_dict_work_order_id", test_sidecar_to_dict_work_order_id),
        ("test_write_analysis_work_order_id", test_write_analysis_work_order_id),
        # #253 analysis_citations WO scoping
        (
            "test_analysis_citations_scoped_two_wos",
            test_analysis_citations_scoped_two_wos,
        ),
        (
            "test_analysis_citations_backward_compat_untagged",
            test_analysis_citations_backward_compat_untagged,
        ),
        (
            "test_analysis_citations_null_wo_checked",
            test_analysis_citations_null_wo_checked,
        ),
        (
            "test_analysis_citations_no_wo_id_checks_all",
            test_analysis_citations_no_wo_id_checks_all,
        ),
        # #283 deliverables_exist WO scoping
        (
            "test_deliverables_exist_vacuous_pass_blocked",
            test_deliverables_exist_vacuous_pass_blocked,
        ),
        (
            "test_deliverables_exist_own_artifact_passes",
            test_deliverables_exist_own_artifact_passes,
        ),
        (
            "test_deliverables_exist_backward_compat_untagged",
            test_deliverables_exist_backward_compat_untagged,
        ),
        (
            "test_deliverables_exist_single_wo_unaffected",
            test_deliverables_exist_single_wo_unaffected,
        ),
        (
            "test_deliverables_exist_no_wo_id_checks_all",
            test_deliverables_exist_no_wo_id_checks_all,
        ),
        # #105 severity model — _overall_verdict
        ("test_overall_verdict_all_pass", test_overall_verdict_all_pass),
        ("test_overall_verdict_any_fail", test_overall_verdict_any_fail),
        ("test_overall_verdict_warns_only", test_overall_verdict_warns_only),
        ("test_overall_verdict_all_skip", test_overall_verdict_all_skip),
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
