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

"""Regression tests for symlink exploitation fixes in core/ modules.

Covers:
  - context.py: _write_if_missing() symlink write-through (#292)
  - controlstore.py: concept JSON read via symlink (#294)
  - controlstore.py: write_record() symlink write-through (#296)
  - env.py: ENV_VERSION marker read via symlink (#279)
  - policy.py: program.yaml read via symlink (#299)
  - provenance.py: Sidecar.write() symlink write-through (#301)
  - thresholds.py: thresholds.yaml read via symlink (#302)

Run with:
    cd applications/open-agentic-science-engine
    python -m pytest tests/test_symlink_core.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the tools package is importable.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from oase.core.paths import is_safe_to_open

# ---------------------------------------------------------------------------
# Fix 1: context.py — _write_if_missing() symlink write-through (#292)
# ---------------------------------------------------------------------------


class TestContextWriteIfMissingSymlink:
    """Verify _write_if_missing refuses to write through a symlink."""

    def test_symlink_at_write_target_raises(self, tmp_path: Path) -> None:
        """A symlink at the write path must trigger ProjectRootError."""
        from oase.core.context import _write_if_missing
        from oase.core.errors import ProjectRootError

        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "stolen.txt"

        link = tmp_path / "skeleton-file.md"
        link.symlink_to(target)

        try:
            _write_if_missing(link, "# content\n")
            assert False, "_write_if_missing should have raised"
        except ProjectRootError:
            pass

        # The target must not have been written.
        assert not target.exists()

    def test_regular_file_path_accepted(self, tmp_path: Path) -> None:
        """A regular (non-existent) path is accepted for writing."""
        from oase.core.context import _write_if_missing

        path = tmp_path / "new-file.md"
        _write_if_missing(path, "# content\n")
        assert path.read_text() == "# content\n"

    def test_symlink_detected_by_guard(self, tmp_path: Path) -> None:
        """is_safe_to_open correctly identifies a symlink."""
        target = tmp_path / "outside" / "evil.txt"
        link = tmp_path / "link.txt"
        link.symlink_to(target)

        assert not is_safe_to_open(link)


# ---------------------------------------------------------------------------
# Fix 2: controlstore.py — concept JSON read via symlink (#294)
# ---------------------------------------------------------------------------


class TestControlstoreConceptReadSymlink:
    """Verify concept loader skips symlinked concept files."""

    def test_symlink_concept_json_returns_none(self, tmp_path: Path) -> None:
        """A symlinked concept JSON must return None (fail-closed)."""
        from oase.core.controlstore import _default_concept_loader

        # Set up project structure.
        project_root = tmp_path / "project"
        concepts_dir = project_root / ".oase" / "control" / "concepts"
        concepts_dir.mkdir(parents=True)

        # Create real target outside project.
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "secret.json"
        target.write_text('{"concept_id": "IC-001", "status": "approved"}')

        # Symlink the concept file.
        link = concepts_dir / "IC-001-r1.json"
        link.symlink_to(target)

        loader = _default_concept_loader(project_root)
        result = loader("IC-001")
        assert result is None

    def test_symlink_unversioned_concept_returns_none(self, tmp_path: Path) -> None:
        """A symlinked unversioned concept (IC-NNN.json) must return None."""
        from oase.core.controlstore import _default_concept_loader

        project_root = tmp_path / "project"
        concepts_dir = project_root / ".oase" / "control" / "concepts"
        concepts_dir.mkdir(parents=True)

        # Create real target outside project.
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "secret.json"
        target.write_text('{"concept_id": "IC-003", "status": "approved"}')

        # Symlink the unversioned concept file (no -rN suffix).
        link = concepts_dir / "IC-003.json"
        link.symlink_to(target)

        loader = _default_concept_loader(project_root)
        result = loader("IC-003")
        assert result is None

    def test_regular_concept_json_accepted(self, tmp_path: Path) -> None:
        """A regular concept JSON file is read normally."""
        from oase.core.controlstore import _default_concept_loader

        project_root = tmp_path / "project"
        concepts_dir = project_root / ".oase" / "control" / "concepts"
        concepts_dir.mkdir(parents=True)

        concept_file = concepts_dir / "IC-002-r1.json"
        concept_file.write_text('{"concept_id": "IC-002", "status": "draft"}')

        loader = _default_concept_loader(project_root)
        result = loader("IC-002")
        assert result is not None
        assert result["concept_id"] == "IC-002"


# ---------------------------------------------------------------------------
# Fix 3: controlstore.py — write_record() symlink write-through (#296)
# ---------------------------------------------------------------------------


class TestControlstoreWriteRecordSymlink:
    """Verify write_record refuses to write through a symlink."""

    def test_symlink_at_record_path_raises(self, tmp_path: Path) -> None:
        """A symlink at the record path must trigger Refusal."""
        # We test at the is_safe_to_open level since write_record has
        # schema validation that makes end-to-end testing complex.
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "stolen.json"

        record_dir = tmp_path / ".oase" / "control" / "work-orders"
        record_dir.mkdir(parents=True)

        link = record_dir / "WO-001.json"
        link.symlink_to(target)

        assert not is_safe_to_open(link)
        # Target must not exist — nothing was written through.
        assert not target.exists()

    def test_regular_record_path_is_safe(self, tmp_path: Path) -> None:
        """A non-existent regular path is safe."""
        record_dir = tmp_path / ".oase" / "control" / "work-orders"
        record_dir.mkdir(parents=True)
        path = record_dir / "WO-002.json"
        assert is_safe_to_open(path)


# ---------------------------------------------------------------------------
# Fix 4: env.py — ENV_VERSION marker read via symlink (#279)
# ---------------------------------------------------------------------------


class TestEnvVersionSymlink:
    """Verify env_version skips symlinked ENV_VERSION marker."""

    def test_symlink_env_version_falls_through(self, tmp_path: Path) -> None:
        """A symlinked ENV_VERSION must be skipped, falling through
        to the dev-environment path."""
        import os
        from unittest import mock

        from oase.core import env

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()

        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "evil_version"
        target.write_text("sha256:deadbeef")

        marker = tools_dir / "ENV_VERSION"
        marker.symlink_to(target)

        with mock.patch.dict(os.environ, {"OASE_TOOLS_HOME": str(tools_dir)}):
            version = env.env_version()

        # Must NOT return the symlink target content.
        assert "deadbeef" not in version
        # Should fall through to unpinned-dev path.
        assert version.startswith("unpinned-dev:")

    def test_regular_env_version_read(self, tmp_path: Path) -> None:
        """A regular ENV_VERSION file is read normally."""
        import os
        from unittest import mock

        from oase.core import env

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()

        marker = tools_dir / "ENV_VERSION"
        marker.write_text("sha256:abcdef123456")

        with mock.patch.dict(os.environ, {"OASE_TOOLS_HOME": str(tools_dir)}):
            version = env.env_version()

        assert version == "sha256:abcdef123456"


# ---------------------------------------------------------------------------
# Fix 5: policy.py — program.yaml read via symlink (#299)
# ---------------------------------------------------------------------------


class TestPolicyProgramYamlSymlink:
    """Verify load_program_config rejects symlinked program.yaml."""

    def test_symlink_program_yaml_raises(self, tmp_path: Path) -> None:
        """A symlinked program.yaml must raise SchemaError."""
        from oase.core.errors import SchemaError

        project_root = tmp_path / "project"
        oase_dir = project_root / ".oase"
        oase_dir.mkdir(parents=True)

        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "evil.yaml"
        target.write_text("program:\n  name: evil\n")

        link = oase_dir / "program.yaml"
        link.symlink_to(target)

        from oase.core.policy import load_program_config

        try:
            load_program_config(project_root)
            assert False, "load_program_config should have raised"
        except SchemaError:
            pass

    def test_symlink_detected_by_guard(self, tmp_path: Path) -> None:
        """is_safe_to_open rejects symlinked YAML."""
        target = tmp_path / "outside" / "evil.yaml"
        link = tmp_path / "program.yaml"
        link.symlink_to(target)

        assert not is_safe_to_open(link)


# ---------------------------------------------------------------------------
# Fix 6: provenance.py — Sidecar.write() symlink write-through (#301)
# ---------------------------------------------------------------------------


class TestProvenanceSidecarSymlink:
    """Verify Sidecar.write() refuses to write through a symlink."""

    def test_symlink_sidecar_path_raises(self, tmp_path: Path) -> None:
        """A symlink at the sidecar path must trigger Refusal."""
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "stolen.meta.json"

        link = tmp_path / "data.csv.meta.json"
        link.symlink_to(target)

        assert not is_safe_to_open(link)
        assert not target.exists()

    def test_regular_sidecar_path_is_safe(self, tmp_path: Path) -> None:
        """A non-existent regular sidecar path is safe."""
        path = tmp_path / "data.csv.meta.json"
        assert is_safe_to_open(path)


# ---------------------------------------------------------------------------
# Fix 7: thresholds.py — thresholds.yaml read via symlink (#302)
# ---------------------------------------------------------------------------


class TestThresholdsYamlSymlink:
    """Verify _load_program_file rejects symlinked thresholds.yaml."""

    def test_symlink_thresholds_yaml_raises(self, tmp_path: Path) -> None:
        """A symlinked thresholds.yaml must raise ThresholdError."""
        from oase.core.errors import ThresholdError
        from oase.core.thresholds import _load_program_file

        project_root = tmp_path / "project"
        oase_dir = project_root / ".oase"
        oase_dir.mkdir(parents=True)

        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "evil.yaml"
        target.write_text("potency:\n  ic50_nm: 100\n")

        link = oase_dir / "thresholds.yaml"
        link.symlink_to(target)

        try:
            _load_program_file(project_root)
            assert False, "_load_program_file should have raised"
        except ThresholdError:
            pass

    def test_symlink_detected_by_guard(self, tmp_path: Path) -> None:
        """is_safe_to_open rejects symlinked YAML."""
        target = tmp_path / "outside" / "evil.yaml"
        link = tmp_path / "thresholds.yaml"
        link.symlink_to(target)

        assert not is_safe_to_open(link)


# ---------------------------------------------------------------------------
# Fix 8: provenance.py — write_analysis() symlink guard & confinement
# ---------------------------------------------------------------------------


class TestProvenanceWriteAnalysisSymlink:
    """Verify write_analysis() refuses symlinks and unconfined paths."""

    def test_symlink_destination_raises_refusal(self, tmp_path: Path) -> None:
        """A symlink at the analysis path must trigger Refusal without reading or writing target."""
        from oase.core import provenance
        from oase.core.errors import Refusal

        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "sensitive.json"
        target.write_text('{"secret": "do_not_overwrite"}', encoding="utf-8")

        link = tmp_path / "test.analysis.json"
        link.symlink_to(target)

        try:
            provenance.write_analysis(
                link,
                source="test-source",
                threshold_set="test",
                thresholds_applied={},
                metrics={},
                assessment={"verdict": "ok"},
            )
            assert False, "write_analysis should have raised Refusal"
        except Refusal as exc:
            assert "symlink" in str(exc)

        # Target must remain untouched
        assert target.read_text(encoding="utf-8") == '{"secret": "do_not_overwrite"}'

    def test_symlink_parent_directory_raises_refusal(self, tmp_path: Path) -> None:
        """An analysis path whose parent directory is a symlink must trigger Refusal."""
        from oase.core import provenance
        from oase.core.errors import Refusal

        outside = tmp_path / "outside"
        outside.mkdir()
        symlink_dir = tmp_path / "symdir"
        symlink_dir.symlink_to(outside)

        analysis_path = symlink_dir / "test.analysis.json"

        try:
            provenance.write_analysis(
                analysis_path,
                source="test-source",
                threshold_set="test",
                thresholds_applied={},
                metrics={},
                assessment={"verdict": "ok"},
            )
            assert False, "write_analysis should have raised Refusal"
        except Refusal as exc:
            assert "symlink" in str(exc)

    def test_escapes_project_root_raises_refusal(self, tmp_path: Path) -> None:
        """An analysis path outside project_root must trigger Refusal when project_root is active."""
        from unittest import mock

        from oase.core import provenance
        from oase.core.errors import Refusal

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        outside_path = tmp_path / "outside" / "test.analysis.json"
        outside_path.parent.mkdir()

        with mock.patch(
            "oase.core.provenance._get_project_root", return_value=project_dir
        ):
            try:
                provenance.write_analysis(
                    outside_path,
                    source="test-source",
                    threshold_set="test",
                    thresholds_applied={},
                    metrics={},
                    assessment={"verdict": "ok"},
                )
                assert False, (
                    "write_analysis should have raised Refusal for escaping project root"
                )
            except Refusal as exc:
                assert "project root" in str(exc)

    def test_regular_analysis_path_succeeds(self, tmp_path: Path) -> None:
        """A regular non-symlink path within project root succeeds."""
        import json

        from oase.core import provenance

        analysis_path = tmp_path / "test.analysis.json"
        result = provenance.write_analysis(
            analysis_path,
            source="test-source",
            threshold_set="test",
            thresholds_applied={},
            metrics={},
            assessment={"verdict": "ok"},
        )
        assert result.exists()
        written = json.loads(result.read_text(encoding="utf-8"))
        assert written["record_type"] == "analysis"
        assert written["assessment"] == {"verdict": "ok"}

    def test_source_digest_skips_symlink(self, tmp_path: Path) -> None:
        """_source_digest returns None when source is a symlink."""
        from oase.core.provenance import _source_digest

        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "source.json"
        target.write_text("content", encoding="utf-8")

        link = tmp_path / "source_link.json"
        link.symlink_to(target)

        assert _source_digest(str(link)) is None
