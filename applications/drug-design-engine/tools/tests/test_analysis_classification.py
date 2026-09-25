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

"""Tests for content-based analysis record classification (#130).

Covers:
  - _is_analysis: standard suffix recognition (.analysis.json, .sc-analysis.json)
  - _is_analysis: content-based recognition via record_type field
  - _is_analysis: non-analysis JSON not classified as analysis
  - _check_unrecognized_json: distinct finding for unrecognized JSON
  - write_analysis: records carry the record_type field
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.commands.validate import (  # noqa: E402
    _check_provenance_valid,
    _check_unrecognized_json,
    _is_analysis,
)
from dde.core.context import ARTIFACT_DIRS  # noqa: E402
from dde.core.provenance import sha256_file  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_project(tmp_path: Path, artifact_class: str = "structures") -> Path:
    """Create a minimal project layout with .dde marker and artifact dir."""
    project = tmp_path / "project"
    (project / ".dde").mkdir(parents=True)
    rel_dir = ARTIFACT_DIRS.get(artifact_class, f"raw/{artifact_class}")
    (project / rel_dir).mkdir(parents=True)
    return project


def _write_json(path: Path, data: dict[str, Any]) -> Path:
    """Write a JSON file and return its path."""
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _write_sidecar_for(
    artifact_path: Path,
    wo_id: str = "WO-001",
) -> Path:
    """Write a minimal sidecar covering *artifact_path*."""
    sha = sha256_file(artifact_path)
    sidecar_path = artifact_path.with_suffix(".meta.json")
    sidecar = {
        "work_order_id": wo_id,
        "outputs": [{"sha256": sha, "path": artifact_path.name}],
    }
    return _write_json(sidecar_path, sidecar)


# ---------------------------------------------------------------------------
# Tests: _is_analysis — filename-based recognition
# ---------------------------------------------------------------------------


class TestIsAnalysisSuffix:
    """Standard suffix recognition — fast path, no file I/O."""

    def test_standard_analysis_suffix(self) -> None:
        assert _is_analysis("protein.analysis.json") is True

    def test_sc_analysis_suffix(self) -> None:
        assert _is_analysis("protein.sc-analysis.json") is True

    def test_non_analysis_json(self) -> None:
        assert _is_analysis("protein.json") is False

    def test_non_json(self) -> None:
        assert _is_analysis("protein.pdb") is False

    def test_sidecar_not_analysis(self) -> None:
        assert _is_analysis("protein.meta.json") is False

    def test_mmp_analysis_suffix_alone(self) -> None:
        """*.mmp-analysis.json does NOT match the suffix allowlist."""
        assert _is_analysis("protein.mmp-analysis.json") is False


# ---------------------------------------------------------------------------
# Tests: _is_analysis — content-based recognition
# ---------------------------------------------------------------------------


class TestIsAnalysisContent:
    """Content-based fallback: reads the file and checks record_type."""

    def test_mmp_analysis_with_record_type(self, tmp_path: Path) -> None:
        """A .mmp-analysis.json with record_type='analysis' is recognised."""
        path = tmp_path / "protein.mmp-analysis.json"
        _write_json(
            path,
            {
                "record_type": "analysis",
                "source": "protein.pdb",
                "threshold_set": "default",
                "thresholds_applied": {},
                "metrics": {},
                "assessment": {},
            },
        )
        assert _is_analysis(path.name, path) is True

    def test_custom_suffix_with_record_type(self, tmp_path: Path) -> None:
        """Any *.json with record_type='analysis' is recognised."""
        path = tmp_path / "data.custom-analysis.json"
        _write_json(path, {"record_type": "analysis", "source": "x"})
        assert _is_analysis(path.name, path) is True

    def test_json_without_record_type(self, tmp_path: Path) -> None:
        """A .json file without record_type is NOT an analysis."""
        path = tmp_path / "data.json"
        _write_json(path, {"source": "x", "metrics": {}})
        assert _is_analysis(path.name, path) is False

    def test_json_with_wrong_record_type(self, tmp_path: Path) -> None:
        """A .json file with record_type='sidecar' is NOT an analysis."""
        path = tmp_path / "data.something.json"
        _write_json(path, {"record_type": "sidecar"})
        assert _is_analysis(path.name, path) is False

    def test_invalid_json_not_analysis(self, tmp_path: Path) -> None:
        """A .json file with invalid JSON is NOT an analysis."""
        path = tmp_path / "broken.mmp-analysis.json"
        path.write_text("{invalid json", encoding="utf-8")
        assert _is_analysis(path.name, path) is False

    def test_standard_suffix_skips_content_check(self, tmp_path: Path) -> None:
        """Standard suffix is the fast path — no file read needed."""
        path = tmp_path / "protein.analysis.json"
        # Don't write the file — the fast path should not try to read it.
        assert _is_analysis(path.name, path) is True

    def test_no_path_no_content_check(self) -> None:
        """Without a path, content check cannot run."""
        # mmp-analysis.json doesn't match suffix, and no path → False
        assert _is_analysis("protein.mmp-analysis.json") is False


# ---------------------------------------------------------------------------
# Tests: _check_unrecognized_json — distinct finding for unknown JSON
# ---------------------------------------------------------------------------


class TestUnrecognizedJson:
    """Unrecognized JSON files emit finding kind 'unrecognized_record_type'."""

    def test_unrecognized_json_emits_distinct_finding(self, tmp_path: Path) -> None:
        """A .json file that is not analysis and has no sidecar coverage."""
        project = _make_project(tmp_path, "structures")
        art_dir = project / ARTIFACT_DIRS["structures"]

        # Write an unrecognized JSON file.
        mystery = art_dir / "mystery.json"
        _write_json(mystery, {"some": "data"})

        deliverables = {"layer_0_classes": ["structures"]}
        result = _check_unrecognized_json(project, deliverables)

        assert result["name"] == "unrecognized_json"
        assert result["result"] == "fail"
        assert result["kind"] == "unrecognized_record_type"
        assert len(result["detail"]["unrecognized"]) == 1
        entry = result["detail"]["unrecognized"][0]
        assert "mystery.json" in entry["file"]
        assert "not a recognized analysis or raw artifact type" in entry["message"]

    def test_analysis_json_not_flagged(self, tmp_path: Path) -> None:
        """A properly-typed analysis JSON should NOT be flagged."""
        project = _make_project(tmp_path, "structures")
        art_dir = project / ARTIFACT_DIRS["structures"]

        # Write a standard analysis record.
        analysis = art_dir / "protein.analysis.json"
        _write_json(
            analysis,
            {
                "record_type": "analysis",
                "source": "protein.pdb",
                "threshold_set": "default",
                "thresholds_applied": {},
                "metrics": {},
                "assessment": {},
            },
        )

        deliverables = {"layer_0_classes": ["structures"]}
        result = _check_unrecognized_json(project, deliverables)
        assert result["result"] == "pass"

    def test_content_typed_analysis_not_flagged(self, tmp_path: Path) -> None:
        """A .mmp-analysis.json with record_type=analysis is NOT flagged."""
        project = _make_project(tmp_path, "structures")
        art_dir = project / ARTIFACT_DIRS["structures"]

        analysis = art_dir / "protein.mmp-analysis.json"
        _write_json(
            analysis,
            {
                "record_type": "analysis",
                "source": "protein.pdb",
                "threshold_set": "default",
                "thresholds_applied": {},
                "metrics": {},
                "assessment": {},
            },
        )

        deliverables = {"layer_0_classes": ["structures"]}
        result = _check_unrecognized_json(project, deliverables)
        assert result["result"] == "pass"

    def test_json_with_sidecar_not_flagged(self, tmp_path: Path) -> None:
        """A .json file covered by a sidecar is a known raw artifact."""
        project = _make_project(tmp_path, "structures")
        art_dir = project / ARTIFACT_DIRS["structures"]

        # Write a raw artifact JSON with sidecar coverage.
        raw = art_dir / "data.json"
        _write_json(raw, {"raw": "data"})
        _write_sidecar_for(raw)

        deliverables = {"layer_0_classes": ["structures"]}
        result = _check_unrecognized_json(project, deliverables)
        assert result["result"] == "pass"

    def test_sidecar_not_flagged(self, tmp_path: Path) -> None:
        """Sidecar files (*.meta.json) should NOT be flagged."""
        project = _make_project(tmp_path, "structures")
        art_dir = project / ARTIFACT_DIRS["structures"]

        # Write a sidecar without any associated artifact.
        sidecar = art_dir / "protein.meta.json"
        _write_json(sidecar, {"outputs": []})

        deliverables = {"layer_0_classes": ["structures"]}
        result = _check_unrecognized_json(project, deliverables)
        assert result["result"] == "pass"

    def test_no_layer_0_classes_skips(self) -> None:
        """When no layer_0_classes declared, check is skipped."""
        result = _check_unrecognized_json(Path("/nonexistent"), {})
        assert result["status"] == "skip"

    def test_provenance_valid_skips_unrecognized_json(self, tmp_path: Path) -> None:
        """provenance_valid should NOT report unrecognized JSON files.

        JSON files without sidecar coverage are handled by
        _check_unrecognized_json instead (#130, #84).
        """
        project = _make_project(tmp_path, "structures")
        art_dir = project / ARTIFACT_DIRS["structures"]

        # Write an unrecognized JSON file (no sidecar, not analysis).
        mystery = art_dir / "mystery.json"
        _write_json(mystery, {"some": "data"})

        deliverables = {"layer_0_classes": ["structures"]}
        result = _check_provenance_valid(project, deliverables)

        # The provenance_valid check should not report this file.
        if result["result"] == "fail":
            issues = result["detail"].get("issues", [])
            for issue in issues:
                assert "mystery.json" not in issue.get("artifact", ""), (
                    "provenance_valid should not report unrecognized JSON files"
                )


# ---------------------------------------------------------------------------
# Tests: write_analysis — record_type field
# ---------------------------------------------------------------------------


class TestWriteAnalysisRecordType:
    """write_analysis must include record_type='analysis' in every record."""

    def test_record_type_field_present(self, tmp_path: Path) -> None:
        """write_analysis produces a record with record_type='analysis'."""
        out = tmp_path / "test.analysis.json"
        source_file = tmp_path / "source.pdb"
        source_file.write_text("ATOM ...", encoding="utf-8")

        with patch("dde.core.provenance.check_integrity") as mock_tc:
            mock_tc.return_value = type(
                "TC",
                (),
                {
                    "integrity": "ok",
                    "modified": False,
                },
            )()
            from dde.core.provenance import write_analysis

            write_analysis(
                path=out,
                source=str(source_file),
                threshold_set="default",
                thresholds_applied={"cutoff": 0.5},
                metrics={"score": 0.8},
                assessment={"verdict": "pass"},
            )

        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["record_type"] == "analysis"

    def test_record_type_is_first_key(self, tmp_path: Path) -> None:
        """record_type should appear early in the record for readability."""
        out = tmp_path / "test.analysis.json"
        source_file = tmp_path / "source.pdb"
        source_file.write_text("ATOM ...", encoding="utf-8")

        with patch("dde.core.provenance.check_integrity") as mock_tc:
            mock_tc.return_value = type(
                "TC",
                (),
                {
                    "integrity": "ok",
                    "modified": False,
                },
            )()
            from dde.core.provenance import write_analysis

            write_analysis(
                path=out,
                source=str(source_file),
                threshold_set="default",
                thresholds_applied={},
                metrics={},
                assessment={},
            )

        data = json.loads(out.read_text(encoding="utf-8"))
        keys = list(data.keys())
        assert keys[0] == "record_type"
