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

"""Tests for layer_0_classes consumes integration (#132).

Verifies that:
- Consumed classes satisfy layer_0_classes
- Non-consumed missing classes still fail
- Consumed artifacts keep their original work_order_id
- layer_0_classes_optional passes when absent
- layer_0_classes_optional passes when present
- Cross-WO citations are recorded
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


def _setup_consumed_project(
    root: Path,
    *,
    upstream_wo: str = "WO-001",
    artifact_class_dir: str = "structures",
    artifact_name: str = "model.pdb",
    artifact_content: bytes = b"ATOM mock structure",
) -> str:
    """Create a project where *upstream_wo* produced artifacts.

    Returns the sha256 of the artifact.
    """
    art_dir = root / "raw" / artifact_class_dir
    art_dir.mkdir(parents=True, exist_ok=True)
    sha = _write(art_dir / artifact_name, artifact_content)
    # Sidecar attributed to the upstream WO.
    sidecar = {
        "work_order_id": upstream_wo,
        "outputs": [{"sha256": sha, "path": artifact_name}],
    }
    _write(
        art_dir / f"{artifact_name.rsplit('.', 1)[0]}.meta.json", json.dumps(sidecar)
    )
    return sha


# ===========================================================================
# Test: Consumed class satisfies layer_0_classes
# ===========================================================================


def test_consumed_class_satisfies_layer0_classes() -> None:
    """A class in consumes from upstream WO satisfies layer_0_classes."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_consumed_project(root, upstream_wo="WO-001")

        deliverables = normalize_deliverables(
            {
                "layer_0_classes": ["dde.structures"],
                "consumes": [
                    {"artifact_class": "dde.structures", "from_work_order": "WO-001"},
                ],
                "layer_1": [],
            }
        )

        result = _check_deliverables_exist(root, deliverables, wo_id="WO-002")
        assert result["result"] == "pass", f"expected pass, got {result}"
        assert result["status"] == "ok"
        # Verify consumed_satisfied is recorded.
        consumed = result["detail"].get("consumed_satisfied", [])
        assert len(consumed) == 1
        assert consumed[0]["class"] == "dde.structures"
        assert "WO-001" in consumed[0]["satisfied_by"]
        print("  PASS: consumed class satisfies layer_0_classes")


# ===========================================================================
# Test: Non-consumed missing class still fails
# ===========================================================================


def test_non_consumed_missing_class_fails() -> None:
    """A missing class that is NOT in consumes still fails."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Create structures from WO-001 (consumed).
        _setup_consumed_project(root, upstream_wo="WO-001")

        deliverables = normalize_deliverables(
            {
                "layer_0_classes": ["dde.structures", "dde.compounds"],
                "consumes": [
                    {"artifact_class": "dde.structures", "from_work_order": "WO-001"},
                ],
                "layer_1": [],
            }
        )

        result = _check_deliverables_exist(root, deliverables, wo_id="WO-002")
        assert result["result"] == "fail", f"expected fail, got {result}"
        # structures should pass (consumed), compounds should fail (no artifacts).
        missing = result["detail"].get("missing", [])
        assert any("compounds" in m for m in missing), (
            f"compounds not in missing: {missing}"
        )
        # consumed_satisfied should still be recorded even when overall is fail.
        consumed = result["detail"].get("consumed_satisfied", [])
        assert len(consumed) == 1
        assert consumed[0]["class"] == "dde.structures"
        print("  PASS: non-consumed missing class still fails")


# ===========================================================================
# Test: Consumed artifacts keep their original work_order_id
# ===========================================================================


def test_consumed_artifacts_keep_original_wo_id() -> None:
    """Consumed artifacts retain their original work_order_id (not re-stamped)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_consumed_project(root, upstream_wo="WO-001")

        deliverables = normalize_deliverables(
            {
                "layer_0_classes": ["dde.structures"],
                "consumes": [
                    {"artifact_class": "dde.structures", "from_work_order": "WO-001"},
                ],
                "layer_1": [],
            }
        )

        result = _check_deliverables_exist(root, deliverables, wo_id="WO-002")
        assert result["result"] == "pass", f"expected pass, got {result}"

        # Verify the sidecar still has the original WO ID.
        sidecar_path = root / "raw" / "structures" / "model.meta.json"
        sidecar_data = json.loads(sidecar_path.read_text())
        assert sidecar_data["work_order_id"] == "WO-001", (
            "Sidecar work_order_id was changed — consumed artifacts must keep "
            "their original work_order_id"
        )
        print("  PASS: consumed artifacts keep original work_order_id")


# ===========================================================================
# Test: layer_0_classes_optional passes when absent
# ===========================================================================


def test_optional_class_absent_passes() -> None:
    """layer_0_classes_optional entry with no artifacts passes with info."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Create structures (required).
        art_dir = root / "raw" / "structures"
        art_dir.mkdir(parents=True)
        sha = _write(art_dir / "model.pdb", "ATOM mock")
        _write(
            art_dir / "model.meta.json",
            json.dumps(
                {
                    "outputs": [{"sha256": sha, "path": "model.pdb"}],
                }
            ),
        )

        deliverables = normalize_deliverables(
            {
                "layer_0_classes": ["dde.structures"],
                "layer_0_classes_optional": ["dde.genomics"],
                "layer_1": [],
            }
        )

        result = _check_deliverables_exist(root, deliverables)
        assert result["result"] == "pass", f"expected pass, got {result}"
        assert result["status"] == "ok"
        # Verify optional class is recorded as absent.
        opt = result["detail"].get("layer_0_classes_optional", [])
        assert len(opt) == 1
        assert opt[0]["class"] == "dde.genomics"
        assert opt[0]["status"] == "absent"
        print("  PASS: optional class absent -> pass with info")


# ===========================================================================
# Test: layer_0_classes_optional passes when present
# ===========================================================================


def test_optional_class_present_passes() -> None:
    """layer_0_classes_optional entry with artifacts passes."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Create structures (required).
        art_dir = root / "raw" / "structures"
        art_dir.mkdir(parents=True)
        sha = _write(art_dir / "model.pdb", "ATOM mock")
        _write(
            art_dir / "model.meta.json",
            json.dumps(
                {
                    "outputs": [{"sha256": sha, "path": "model.pdb"}],
                }
            ),
        )
        # Create genomics (optional, present).
        gen_dir = root / "raw" / "genomics"
        gen_dir.mkdir(parents=True)
        gen_sha = _write(gen_dir / "genes.json", '{"gene": "BRCA1"}')
        _write(
            gen_dir / "genes.meta.json",
            json.dumps(
                {
                    "outputs": [{"sha256": gen_sha, "path": "genes.json"}],
                }
            ),
        )

        deliverables = normalize_deliverables(
            {
                "layer_0_classes": ["dde.structures"],
                "layer_0_classes_optional": ["dde.genomics"],
                "layer_1": [],
            }
        )

        result = _check_deliverables_exist(root, deliverables)
        assert result["result"] == "pass", f"expected pass, got {result}"
        # Verify optional class is recorded as present.
        opt = result["detail"].get("layer_0_classes_optional", [])
        assert len(opt) == 1
        assert opt[0]["class"] == "dde.genomics"
        assert opt[0]["status"] == "present"
        print("  PASS: optional class present -> pass")


# ===========================================================================
# Test: Cross-WO citation is recorded
# ===========================================================================


def test_cross_wo_citation_recorded() -> None:
    """Cross-WO citations are recorded in the validation output."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_consumed_project(root, upstream_wo="WO-001")

        deliverables = normalize_deliverables(
            {
                "layer_0_classes": ["dde.structures"],
                "consumes": [
                    {"artifact_class": "dde.structures", "from_work_order": "WO-001"},
                ],
                "layer_1": [],
            }
        )

        result = _check_deliverables_exist(root, deliverables, wo_id="WO-002")
        assert result["result"] == "pass", f"expected pass, got {result}"
        # Verify cross-WO citations are recorded.
        citations = result["detail"].get("cross_wo_citations", [])
        assert len(citations) >= 1, (
            f"expected cross_wo_citations, got {result['detail']}"
        )
        citation = citations[0]
        assert citation["class"] == "dde.structures"
        assert "WO-001" in citation["from_work_orders"]
        print("  PASS: cross-WO citation recorded")


# ===========================================================================
# Runner
# ===========================================================================


if __name__ == "__main__":
    print("layer_0_classes consumes integration (#132)")
    test_consumed_class_satisfies_layer0_classes()
    test_non_consumed_missing_class_fails()
    test_consumed_artifacts_keep_original_wo_id()
    test_optional_class_absent_passes()
    test_optional_class_present_passes()
    test_cross_wo_citation_recorded()
    print("\nAll #132 tests passed!")
