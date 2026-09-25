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

"""Tests for ``dde artifact register`` command (#87 Phase 2).

Covers:
  - register writes sidecar with correct fields
  - register refuses overwrite of existing .meta.json
  - register sets work_order_id from $DDE_WORK_ORDER_ID
  - register requires --source
  - register computes correct sha256 in outputs
  - register sets type: "registration" in sidecar
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from click.testing import CliRunner  # noqa: E402

from dde.cli import cli  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """Create a minimal project directory and set DDE_PROJECT."""
    root = tmp_path / "program"
    (root / ".dde").mkdir(parents=True)
    (root / "raw" / "genomics").mkdir(parents=True)
    monkeypatch.setenv("DDE_PROJECT", str(root))
    # Clear work order env var by default; individual tests set it.
    monkeypatch.delenv("DDE_WORK_ORDER_ID", raising=False)
    return root


def _write_file(project: Path, rel: str, content: str = "test-content") -> Path:
    """Write a file under the project and return its path."""
    p = project / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_register_writes_sidecar(project):
    """register writes a .meta.json with correct type, tool, outputs, sha256."""
    f = _write_file(project, "raw/genomics/msa.fasta", "ACGT")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "artifact",
            "register",
            str(f.relative_to(project)),
            "--source",
            "EBI ClustalO",
        ],
    )
    assert result.exit_code == 0, result.output

    sidecar_path = f.parent / f"{f.name}.meta.json"
    assert sidecar_path.exists()
    data = json.loads(sidecar_path.read_text("utf-8"))

    assert data["tool"] == "external-registration"
    assert data["subcommand"] == "register"
    assert data["type"] == "registration"
    assert data["source"] == "EBI ClustalO"
    assert len(data["outputs"]) == 1
    assert data["outputs"][0]["path"] == "msa.fasta"
    assert data["outputs"][0]["sha256"] == _sha256("ACGT")
    assert data["outputs"][0]["bytes"] == 4


def test_register_refuses_overwrite(project):
    """Existing .meta.json causes a Refusal (non-zero exit)."""
    f = _write_file(project, "raw/genomics/msa.fasta", "ACGT")
    # Pre-create the sidecar.
    sidecar_path = f.parent / f"{f.name}.meta.json"
    sidecar_path.write_text("{}", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "artifact",
            "register",
            str(f.relative_to(project)),
            "--source",
            "EBI ClustalO",
        ],
    )
    assert result.exit_code != 0
    assert (
        "already exists" in result.output.lower()
        or "already exists" in (result.output + str(result.exception)).lower()
    )


def test_register_sets_work_order_id(project, monkeypatch):
    """work_order_id in the sidecar comes from $DDE_WORK_ORDER_ID."""
    monkeypatch.setenv("DDE_WORK_ORDER_ID", "WO-005")
    f = _write_file(project, "raw/genomics/msa.fasta", "ACGT")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "artifact",
            "register",
            str(f.relative_to(project)),
            "--source",
            "test",
        ],
    )
    assert result.exit_code == 0, result.output

    sidecar_path = f.parent / f"{f.name}.meta.json"
    data = json.loads(sidecar_path.read_text("utf-8"))
    assert data["work_order_id"] == "WO-005"


def test_register_requires_source(project):
    """Missing --source fails with a clear error."""
    f = _write_file(project, "raw/genomics/msa.fasta", "ACGT")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "artifact",
            "register",
            str(f.relative_to(project)),
        ],
    )
    assert result.exit_code != 0
    assert (
        "source" in result.output.lower()
        or "source" in str(result.exception or "").lower()
    )


def test_register_sha256_correct(project):
    """sha256 in outputs matches the file content exactly."""
    content = "specific-test-content-for-hash"
    f = _write_file(project, "raw/genomics/data.json", content)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "artifact",
            "register",
            str(f.relative_to(project)),
            "--source",
            "manual",
        ],
    )
    assert result.exit_code == 0, result.output

    sidecar_path = f.parent / f"{f.name}.meta.json"
    data = json.loads(sidecar_path.read_text("utf-8"))
    expected_sha = _sha256(content)
    assert data["outputs"][0]["sha256"] == expected_sha


def test_register_type_field(project):
    """Sidecar has type: "registration"."""
    f = _write_file(project, "raw/genomics/msa.fasta", "ACGT")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "artifact",
            "register",
            str(f.relative_to(project)),
            "--source",
            "test",
        ],
    )
    assert result.exit_code == 0, result.output

    sidecar_path = f.parent / f"{f.name}.meta.json"
    data = json.loads(sidecar_path.read_text("utf-8"))
    assert data.get("type") == "registration"


def test_register_registered_by_env(project, monkeypatch):
    """registered_by comes from $SCION_AGENT_SLUG."""
    monkeypatch.setenv("SCION_AGENT_SLUG", "test-agent-42")
    f = _write_file(project, "raw/genomics/msa.fasta", "ACGT")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "artifact",
            "register",
            str(f.relative_to(project)),
            "--source",
            "test",
        ],
    )
    assert result.exit_code == 0, result.output

    sidecar_path = f.parent / f"{f.name}.meta.json"
    data = json.loads(sidecar_path.read_text("utf-8"))
    assert data["registered_by"] == "test-agent-42"


def test_register_registered_by_default(project, monkeypatch):
    """registered_by defaults to 'unattributed' when SCION_AGENT_SLUG is unset."""
    monkeypatch.delenv("SCION_AGENT_SLUG", raising=False)
    f = _write_file(project, "raw/genomics/msa.fasta", "ACGT")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "artifact",
            "register",
            str(f.relative_to(project)),
            "--source",
            "test",
        ],
    )
    assert result.exit_code == 0, result.output

    sidecar_path = f.parent / f"{f.name}.meta.json"
    data = json.loads(sidecar_path.read_text("utf-8"))
    assert data["registered_by"] == "unattributed"


# ---------------------------------------------------------------------------
# Direct-run support
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
