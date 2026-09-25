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

"""Tests for cross-WO consumes block in deliverables_exist (#87 Phase 1).

Covers:
  - _build_consumes_map: valid entries, empty, malformed, dde.* prefix normalization
  - deliverables_exist with consumes: consumed WO artifacts present → pass
  - deliverables_exist with consumes: consumed WO artifacts absent → fail
  - deliverables_exist without consumes: behavior unchanged
  - deliverables_exist with consumes + own artifacts in same class
  - normalize_deliverables preserves consumes field
"""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.commands.validate import (  # noqa: E402
    _build_consumes_map,
    _check_deliverables_exist,
)
from dde.core.controlstore import normalize_deliverables  # noqa: E402
from dde.core.provenance import Sidecar  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_project(tmp_path: Path, artifact_class: str = "structures") -> Path:
    """Create a minimal project layout with .dde marker and artifact dir."""
    project = tmp_path / "project"
    (project / ".dde").mkdir(parents=True)
    (project / "raw" / artifact_class).mkdir(parents=True)
    return project


def _write_artifact(
    project: Path,
    artifact_class: str,
    filename: str,
    content: str = "data",
) -> Path:
    """Write an artifact file and return its path."""
    art = project / "raw" / artifact_class / filename
    art.write_text(content, encoding="utf-8")
    return art


def _write_sidecar_for(
    artifact_path: Path,
    wo_id: str,
) -> Path:
    """Write a production sidecar for *artifact_path* tagged to *wo_id*."""
    import os

    old_env = os.environ.get("DDE_WORK_ORDER_ID")
    os.environ["DDE_WORK_ORDER_ID"] = wo_id
    try:
        sc = Sidecar(tool="test-tool", subcommand="fetch")
        sc.add_output(artifact_path)
        sidecar_path = artifact_path.parent / f"{artifact_path.name}.meta.json"
        sc.write(sidecar_path)
        return sidecar_path
    finally:
        if old_env is None:
            os.environ.pop("DDE_WORK_ORDER_ID", None)
        else:
            os.environ["DDE_WORK_ORDER_ID"] = old_env


# ---------------------------------------------------------------------------
# _build_consumes_map tests
# ---------------------------------------------------------------------------


def test_build_consumes_map_valid():
    """Valid consumes entries are parsed correctly."""
    deliverables = {
        "layer_0_classes": ["structures"],
        "consumes": [
            {"artifact_class": "structures", "from_work_order": "WO-002"},
            {"artifact_class": "genomics", "from_work_order": "WO-003"},
        ],
    }
    result = _build_consumes_map(deliverables)
    assert result == {
        "structures": {"WO-002"},
        "genomics": {"WO-003"},
    }


def test_build_consumes_map_empty():
    """Empty consumes list returns empty dict."""
    deliverables = {"layer_0_classes": ["structures"], "consumes": []}
    assert _build_consumes_map(deliverables) == {}


def test_build_consumes_map_absent():
    """Missing consumes key returns empty dict."""
    deliverables = {"layer_0_classes": ["structures"]}
    assert _build_consumes_map(deliverables) == {}


def test_build_consumes_map_malformed():
    """Malformed entries (not dicts, missing keys) are skipped."""
    deliverables = {
        "consumes": [
            "not-a-dict",
            {"artifact_class": "structures"},  # missing from_work_order
            {"from_work_order": "WO-002"},  # missing artifact_class
            {"artifact_class": "", "from_work_order": ""},  # empty strings
            42,
        ],
    }
    assert _build_consumes_map(deliverables) == {}


def test_build_consumes_map_dde_prefix_normalization():
    """dde.* prefix on artifact_class is stripped by normalize_artifact_class."""
    deliverables = {
        "consumes": [
            {"artifact_class": "dde.structures", "from_work_order": "WO-002"},
        ],
    }
    result = _build_consumes_map(deliverables)
    assert "structures" in result
    assert result["structures"] == {"WO-002"}


def test_build_consumes_map_multiple_wos_same_class():
    """Multiple WOs consuming the same class are collected in a set."""
    deliverables = {
        "consumes": [
            {"artifact_class": "structures", "from_work_order": "WO-002"},
            {"artifact_class": "structures", "from_work_order": "WO-003"},
        ],
    }
    result = _build_consumes_map(deliverables)
    assert result["structures"] == {"WO-002", "WO-003"}


# ---------------------------------------------------------------------------
# deliverables_exist with consumes tests
# ---------------------------------------------------------------------------


def test_deliverables_exist_with_consumes_pass(tmp_path):
    """Consumed WO's artifacts present → deliverables_exist passes."""
    project = _make_project(tmp_path)
    art = _write_artifact(project, "structures", "model.cif")
    _write_sidecar_for(art, "WO-002")

    deliverables = {
        "layer_0_classes": ["structures"],
        "consumes": [
            {"artifact_class": "structures", "from_work_order": "WO-002"},
        ],
    }

    result = _check_deliverables_exist(project, deliverables, wo_id="WO-005")
    assert result["result"] == "pass", f"Expected pass, got: {result}"


def test_deliverables_exist_with_consumes_no_consumed_artifacts(tmp_path):
    """Consumed WO's artifacts absent → deliverables_exist fails."""
    project = _make_project(tmp_path)
    # Write an artifact with a sidecar tagged to WO-099 (not the consumed WO)
    art = _write_artifact(project, "structures", "model.cif")
    _write_sidecar_for(art, "WO-099")

    deliverables = {
        "layer_0_classes": ["structures"],
        "consumes": [
            {"artifact_class": "structures", "from_work_order": "WO-002"},
        ],
    }

    result = _check_deliverables_exist(project, deliverables, wo_id="WO-005")
    assert result["result"] == "fail", f"Expected fail, got: {result}"


def test_deliverables_exist_without_consumes_unchanged(tmp_path):
    """Without consumes, behavior is identical to before (#87 regression guard)."""
    project = _make_project(tmp_path)
    # Artifact with sidecar for WO-002 → should fail for WO-005 (no own artifacts).
    art = _write_artifact(project, "structures", "model.cif")
    _write_sidecar_for(art, "WO-002")

    deliverables = {"layer_0_classes": ["structures"]}

    result = _check_deliverables_exist(project, deliverables, wo_id="WO-005")
    assert result["result"] == "fail"

    # Own artifact → should pass.
    art2 = _write_artifact(project, "structures", "own.cif", content="own-data")
    _write_sidecar_for(art2, "WO-005")

    result2 = _check_deliverables_exist(project, deliverables, wo_id="WO-005")
    assert result2["result"] == "pass"


def test_deliverables_exist_consumes_plus_own_artifacts(tmp_path):
    """WO both consumes from WO-002 and produces its own → pass."""
    project = _make_project(tmp_path)

    # Artifact from consumed WO
    consumed = _write_artifact(
        project, "structures", "consumed.cif", content="from-002"
    )
    _write_sidecar_for(consumed, "WO-002")

    # Own artifact
    own = _write_artifact(project, "structures", "own.cif", content="from-005")
    _write_sidecar_for(own, "WO-005")

    deliverables = {
        "layer_0_classes": ["structures"],
        "consumes": [
            {"artifact_class": "structures", "from_work_order": "WO-002"},
        ],
    }

    result = _check_deliverables_exist(project, deliverables, wo_id="WO-005")
    assert result["result"] == "pass"


# ---------------------------------------------------------------------------
# normalize_deliverables preserves consumes
# ---------------------------------------------------------------------------


def test_normalize_deliverables_preserves_consumes():
    """normalize_deliverables passes through consumes field unchanged."""
    consumes = [
        {"artifact_class": "dde.structures", "from_work_order": "WO-002"},
    ]
    deliverables = {
        "layer_0_classes": ["structures"],
        "consumes": consumes,
    }
    result = normalize_deliverables(deliverables)
    assert result["consumes"] is consumes  # exact same object, not stripped


# ---------------------------------------------------------------------------
# Direct-run support
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
