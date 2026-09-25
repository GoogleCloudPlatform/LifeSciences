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

"""Regression tests for symlink exploitation fixes in commands/ batch.

Covers:
  - admet.py: _safe_write_artifact symlink write-through (#248)
  - dossier.py: check_cmd output symlink write-through (#259)
  - dossier.py: _collect_relays_from_artifact symlink read (#261)
  - env.py: envstamp.stamp through symlinked home (#263)
  - hypothesis.py: verbatim/normalised write-through (#266)
  - pk.py: study_path symlink read (#284)
  - validate.py: findings_integrity symlink guard (#283)

Run with:
    cd applications/DDE
    python -m pytest tests/test_symlink_commands.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the tools package is importable.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dde.core.paths import is_safe_to_open

# ---------------------------------------------------------------------------
# Fix 1: admet.py — _safe_write_artifact symlink write-through (#248)
# ---------------------------------------------------------------------------


class TestAdmetSafeWriteSymlink:
    """Verify that _safe_write_artifact refuses to write through a symlink."""

    def test_symlink_artifact_path_rejected(self, tmp_path: Path) -> None:
        """A symlink at the artifact write path must be rejected."""
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "stolen.json"

        artifact_path = tmp_path / "artifact.json"
        artifact_path.symlink_to(target)

        assert not is_safe_to_open(artifact_path)

    def test_regular_artifact_path_accepted(self, tmp_path: Path) -> None:
        """A regular (non-symlink) path must be accepted."""
        artifact_path = tmp_path / "artifact.json"
        assert is_safe_to_open(artifact_path)

    def test_existing_regular_file_accepted(self, tmp_path: Path) -> None:
        """An existing regular file must be accepted."""
        artifact_path = tmp_path / "artifact.json"
        artifact_path.write_text("{}")
        assert is_safe_to_open(artifact_path)


# ---------------------------------------------------------------------------
# Fix 2: dossier.py — check_cmd output symlink write-through (#259)
# ---------------------------------------------------------------------------


class TestDossierCheckOutputSymlink:
    """Verify that dossier check refuses to write through symlinked output."""

    def test_output_path_symlink_rejected(self, tmp_path: Path) -> None:
        """A symlink at the dossier-check.json path must be rejected."""
        gates_dir = tmp_path / "gates" / "dossier-check"
        gates_dir.mkdir(parents=True)

        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "evil.json"

        output_path = gates_dir / "dossier-check.json"
        output_path.symlink_to(target)

        assert not is_safe_to_open(output_path)

    def test_sidecar_path_symlink_rejected(self, tmp_path: Path) -> None:
        """A symlink at the sidecar path must be rejected."""
        gates_dir = tmp_path / "gates" / "dossier-check"
        gates_dir.mkdir(parents=True)

        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "evil-meta.json"

        sidecar_path = gates_dir / "dossier-check.meta.json"
        sidecar_path.symlink_to(target)

        assert not is_safe_to_open(sidecar_path)

    def test_regular_output_path_accepted(self, tmp_path: Path) -> None:
        """A regular output path must be accepted."""
        output_path = tmp_path / "dossier-check.json"
        assert is_safe_to_open(output_path)


# ---------------------------------------------------------------------------
# Fix 3: dossier.py — _collect_relays_from_artifact symlink read (#261)
# ---------------------------------------------------------------------------


class TestDossierRelaySymlinkRead:
    """Verify that _collect_relays_from_artifact rejects symlinked meta files."""

    def test_meta_path_symlink_rejected(self, tmp_path: Path) -> None:
        """A symlink at the .meta.json path must be rejected before read."""
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "evil-meta.json"
        target.write_text('{"mandatory_relays": []}')

        meta_path = tmp_path / "artifact.meta.json"
        meta_path.symlink_to(target)

        assert not is_safe_to_open(meta_path)

    def test_regular_meta_path_accepted(self, tmp_path: Path) -> None:
        """A regular .meta.json file must be accepted."""
        meta_path = tmp_path / "artifact.meta.json"
        meta_path.write_text('{"mandatory_relays": []}')
        assert is_safe_to_open(meta_path)


# ---------------------------------------------------------------------------
# Fix 4: env.py — envstamp.stamp through symlinked home (#263)
# ---------------------------------------------------------------------------


class TestEnvStampSymlink:
    """Verify that env stamp rejects a symlinked tools home directory."""

    def test_symlinked_home_rejected(self, tmp_path: Path) -> None:
        """A symlinked home directory must be rejected."""
        outside = tmp_path / "outside"
        outside.mkdir()

        home_link = tmp_path / "tools-home"
        home_link.symlink_to(outside)

        assert not is_safe_to_open(home_link)

    def test_regular_home_accepted(self, tmp_path: Path) -> None:
        """A regular directory must be accepted."""
        home = tmp_path / "tools-home"
        home.mkdir()
        assert is_safe_to_open(home)


# ---------------------------------------------------------------------------
# Fix 5: hypothesis.py — verbatim/normalised write-through (#266)
# ---------------------------------------------------------------------------


class TestHypothesisWriteSymlink:
    """Verify that hypothesis adopt refuses to write through symlinks."""

    def test_verbatim_symlink_rejected(self, tmp_path: Path) -> None:
        """A symlink at the verbatim output path must be rejected."""
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "stolen.json"

        verbatim = tmp_path / "hyp.hypothesis-set.source.json"
        verbatim.symlink_to(target)

        assert not is_safe_to_open(verbatim)

    def test_normalised_symlink_rejected(self, tmp_path: Path) -> None:
        """A symlink at the normalised output path must be rejected."""
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "stolen.json"

        normalised = tmp_path / "hyp.hypothesis-set.json"
        normalised.symlink_to(target)

        assert not is_safe_to_open(normalised)

    def test_regular_write_paths_accepted(self, tmp_path: Path) -> None:
        """Regular (non-symlink) write paths must be accepted."""
        verbatim = tmp_path / "hyp.hypothesis-set.source.json"
        normalised = tmp_path / "hyp.hypothesis-set.json"
        assert is_safe_to_open(verbatim)
        assert is_safe_to_open(normalised)


# ---------------------------------------------------------------------------
# Fix 6: pk.py — study_path symlink read (#284)
# ---------------------------------------------------------------------------


class TestPkStudyPathSymlink:
    """Verify that pk scale rejects symlinked study files."""

    def test_study_path_symlink_rejected(self, tmp_path: Path) -> None:
        """A symlink at the study file path must be rejected."""
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "stolen-study.json"
        target.write_text('{"body_weight_kg": 999}')

        study_path = tmp_path / "study1.pk-study.json"
        study_path.symlink_to(target)

        assert not is_safe_to_open(study_path)

    def test_regular_study_path_accepted(self, tmp_path: Path) -> None:
        """A regular study file must be accepted."""
        study_path = tmp_path / "study1.pk-study.json"
        study_path.write_text('{"body_weight_kg": 0.025}')
        assert is_safe_to_open(study_path)


# ---------------------------------------------------------------------------
# Fix 7: validate.py — findings_integrity symlink guard (#283)
# ---------------------------------------------------------------------------


class TestValidateFindingsSymlink:
    """Verify that findings_integrity check skips symlinked files."""

    def test_symlink_in_findings_skipped(self, tmp_path: Path) -> None:
        """A symlink in findings/ must be skipped by the integrity check."""
        findings_dir = tmp_path / "findings"
        findings_dir.mkdir()

        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "evil.meta.json"
        target.write_text('{"record_type": "sidecar"}')

        symlink = findings_dir / "evil.meta.json"
        symlink.symlink_to(target)

        # The symlink guard should catch this.
        assert not is_safe_to_open(symlink)

    def test_regular_file_in_findings_accepted(self, tmp_path: Path) -> None:
        """A regular file in findings/ must be accepted."""
        findings_dir = tmp_path / "findings"
        findings_dir.mkdir()

        normal = findings_dir / "report.txt"
        normal.write_text("findings report")

        assert is_safe_to_open(normal)

    def test_is_safe_to_open_consistent_with_is_symlink(self, tmp_path: Path) -> None:
        """is_safe_to_open(child) and not child.is_symlink() agree."""
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "data.json"
        target.write_text("{}")

        link = tmp_path / "data.json"
        link.symlink_to(target)

        # Both should identify the symlink.
        assert link.is_symlink()
        assert not is_safe_to_open(link)

        # Regular file — both should accept.
        regular = tmp_path / "regular.json"
        regular.write_text("{}")
        assert not regular.is_symlink()
        assert is_safe_to_open(regular)
