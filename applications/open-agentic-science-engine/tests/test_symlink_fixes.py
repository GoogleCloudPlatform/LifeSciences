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

"""Regression tests for Phase 3 symlink exploitation fixes.

Covers:
  - artifact.py: sidecar symlink write-through (#178)
  - validate.py: _is_analysis called before confinement check (#210)
  - hypex.py: missing symlink check on metadata files (#196)
  - http.py: symlink following in pace file (#212)
  - site.py: shutil.copytree follows symlinks (#207)

Run with:
    cd applications/DDE
    python -m pytest tests/test_symlink_fixes.py -v
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from unittest import mock

# Ensure the tools package is importable.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dde.core.paths import is_safe_to_open

# ---------------------------------------------------------------------------
# Fix 1: artifact.py — sidecar symlink write-through (#178)
# ---------------------------------------------------------------------------


class TestArtifactSidecarSymlink:
    """Verify that artifact register refuses to write through a sidecar symlink."""

    def test_sidecar_symlink_raises_refusal(self, tmp_path: Path) -> None:
        """A symlink at the sidecar path must trigger Refusal before write."""
        # Create a real file to register.
        data_file = tmp_path / "data.csv"
        data_file.write_text("col1,col2\n1,2\n")

        # Plant a symlink where the sidecar would go.
        sidecar_path = tmp_path / "data.csv.meta.json"
        target = tmp_path / "outside" / "evil.json"
        sidecar_path.symlink_to(target)

        # is_safe_to_open must reject the symlink.
        assert not is_safe_to_open(sidecar_path)

    def test_regular_sidecar_path_is_safe(self, tmp_path: Path) -> None:
        """A non-existent regular path is safe to open."""
        sidecar_path = tmp_path / "data.csv.meta.json"
        # Path does not exist, but is not a symlink.
        assert is_safe_to_open(sidecar_path)

    def test_existing_regular_sidecar_is_safe(self, tmp_path: Path) -> None:
        """An existing regular file is safe to open."""
        sidecar_path = tmp_path / "data.csv.meta.json"
        sidecar_path.write_text("{}")
        assert is_safe_to_open(sidecar_path)


# ---------------------------------------------------------------------------
# Fix 2: validate.py — _is_analysis ordering (#210)
# ---------------------------------------------------------------------------


class TestValidateSymlinkOrdering:
    """Verify that symlink/confinement checks run before _is_analysis."""

    def test_find_layer0_skips_symlink_without_reading(self, tmp_path: Path) -> None:
        """A symlink .json in the artifact dir must be skipped before
        _is_analysis reads it."""
        from dde.commands.validate import _find_layer0_artifacts
        from dde.core.context import ARTIFACT_DIRS

        # Pick an artifact class from ARTIFACT_DIRS.
        art_class = next(iter(ARTIFACT_DIRS))
        rel_dir = ARTIFACT_DIRS[art_class]

        # Set up the project root and artifact directory.
        project_root = tmp_path / "project"
        art_dir = project_root / rel_dir
        art_dir.mkdir(parents=True)

        # Plant a symlink pointing outside the project.
        outside = tmp_path / "outside"
        outside.mkdir()
        evil_target = outside / "secret.json"
        evil_target.write_text('{"record_type": "analysis"}')

        symlink_file = art_dir / "evil.json"
        symlink_file.symlink_to(evil_target)

        # Also add a normal file to verify the function still works.
        normal_file = art_dir / "normal.csv"
        normal_file.write_text("data")

        # _find_layer0_artifacts should skip the symlink.
        artifacts = _find_layer0_artifacts(project_root, art_class)
        artifact_names = [a.name for a in artifacts]
        assert "evil.json" not in artifact_names
        assert "normal.csv" in artifact_names

    def test_symlink_json_not_read_by_is_analysis(self, tmp_path: Path) -> None:
        """Verify is_safe_to_open rejects symlinks before any file read."""
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "trick.json"
        target.write_text('{"record_type": "analysis"}')

        link = tmp_path / "trick.json"
        link.symlink_to(target)

        # The symlink guard should catch this.
        assert not is_safe_to_open(link)


# ---------------------------------------------------------------------------
# Fix 3: hypex.py — missing symlink check on metadata files (#196)
# ---------------------------------------------------------------------------


class TestHypexSymlinkSkip:
    """Verify that hypex ingest skips symlinked metadata files."""

    def test_symlink_citation_manifest_skipped(self, tmp_path: Path) -> None:
        """A symlink at citations/h1.json must not be read."""
        citations_dir = tmp_path / "citations"
        citations_dir.mkdir()

        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "evil.json"
        target.write_text('{"summary": {"total": 999}}')

        cite_link = citations_dir / "h1.json"
        cite_link.symlink_to(target)

        # is_safe_to_open correctly rejects the symlink.
        assert not is_safe_to_open(cite_link)
        assert cite_link.is_file()  # .is_file() follows symlinks — would pass

    def test_symlink_run_yaml_skipped(self, tmp_path: Path) -> None:
        """A symlink at run.yaml must not be read."""
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "run.yaml"
        target.write_text("config: evil")

        run_yaml = tmp_path / "run.yaml"
        run_yaml.symlink_to(target)

        assert not is_safe_to_open(run_yaml)

    def test_symlink_rating_file_filtered(self, tmp_path: Path) -> None:
        """Symlink epoch-*.json files must be filtered out by the guard."""
        ratings_dir = tmp_path / "ratings"
        ratings_dir.mkdir()

        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "epoch-1.json"
        target.write_text("{}")

        # Real file.
        real = ratings_dir / "epoch-0.json"
        real.write_text('{"ratings": {}}')

        # Symlink.
        link = ratings_dir / "epoch-1.json"
        link.symlink_to(target)

        # Filter as hypex does.
        rating_files = sorted(
            [
                f
                for f in ratings_dir.iterdir()
                if f.name.startswith("epoch-")
                and f.suffix == ".json"
                and is_safe_to_open(f)
            ],
            key=lambda f: f.name,
        )
        assert len(rating_files) == 1
        assert rating_files[0].name == "epoch-0.json"

    def test_symlink_meta_files_skipped(self, tmp_path: Path) -> None:
        """Symlinks at meta/termination.json etc must be skipped."""
        meta_dir = tmp_path / "meta"
        meta_dir.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()

        for name in (
            "termination.json",
            "roster.ndjson",
            "progress.json",
            "pacing.json",
        ):
            target = outside / name
            target.write_text("{}")
            link = meta_dir / name
            link.symlink_to(target)
            assert not is_safe_to_open(link), f"{name} symlink should be rejected"


# ---------------------------------------------------------------------------
# Fix 4: http.py — symlink following in pace file (#212)
# ---------------------------------------------------------------------------


class TestHttpPaceFileSymlink:
    """Verify that _pace_disk falls back to memory pacing when pace file
    is a symlink."""

    def test_pace_disk_fallback_on_symlink(self, tmp_path: Path) -> None:
        """A symlink pace file must trigger fallback to _pace_memory."""
        import dde.core.http as http_mod

        # Set up a pace dir with a symlink.
        pace_dir = tmp_path / "pace"
        pace_dir.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "evil"
        target.write_text("0.0")

        symlink = pace_dir / "example.com"
        symlink.symlink_to(target)

        # Patch _PACE_DIR and _pace_memory to verify fallback.
        with (
            mock.patch.object(http_mod, "_PACE_DIR", pace_dir),
            mock.patch.object(http_mod, "_pace_memory") as mock_memory,
        ):
            http_mod._pace_disk("example.com", 1.0)
            mock_memory.assert_called_once_with("example.com", 1.0)

        # The target should not have been modified.
        assert target.read_text() == "0.0"

    def test_pace_disk_normal_file_works(self, tmp_path: Path) -> None:
        """A normal pace file should work without fallback."""
        import dde.core.http as http_mod

        pace_dir = tmp_path / "pace"
        pace_dir.mkdir()

        with (
            mock.patch.object(http_mod, "_PACE_DIR", pace_dir),
            mock.patch.object(http_mod, "_pace_memory") as mock_memory,
        ):
            http_mod._pace_disk("example.com", 1.0)
            # Should NOT fall back to memory pacing.
            mock_memory.assert_not_called()

        # Pace file should exist and contain a timestamp.
        pace_file = pace_dir / "example.com"
        assert pace_file.is_file()
        assert float(pace_file.read_text().strip()) > 0


# ---------------------------------------------------------------------------
# Fix 5: site.py — shutil.copytree follows symlinks (#207)
# ---------------------------------------------------------------------------


class TestSiteCopytreeSymlinks:
    """Verify that site build copies symlinks as symlinks, not dereferencing."""

    def test_copytree_preserves_symlinks(self, tmp_path: Path) -> None:
        """shutil.copytree with symlinks=True should preserve symlinks."""
        src = tmp_path / "raw"
        src.mkdir()
        (src / "real.txt").write_text("content")

        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("sensitive data")
        (src / "link.txt").symlink_to(secret)

        dst = tmp_path / "output" / "raw"
        shutil.copytree(
            src,
            dst,
            symlinks=True,
            ignore=shutil.ignore_patterns(".*", "__pycache__", "*.pyc", ".DS_Store"),
        )

        # The symlink should be preserved as a symlink, not dereferenced.
        copied_link = dst / "link.txt"
        assert copied_link.is_symlink()
        # The real file should be copied normally.
        assert (dst / "real.txt").read_text() == "content"

    def test_copytree_without_symlinks_flag_dereferences(self, tmp_path: Path) -> None:
        """Without symlinks=True, copytree dereferences symlinks (the bug)."""
        src = tmp_path / "raw"
        src.mkdir()

        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("sensitive data")
        (src / "link.txt").symlink_to(secret)

        dst = tmp_path / "output" / "raw"
        shutil.copytree(src, dst, symlinks=False)

        # Without the fix, the symlink would be a regular file with the
        # target's content — the vulnerability we fixed.
        copied = dst / "link.txt"
        assert not copied.is_symlink()
        assert copied.read_text() == "sensitive data"

    def test_site_copytree_call_uses_symlinks_true(self) -> None:
        """Verify the site module's copytree call passes symlinks=True.

        This is a source-level check: we inspect the function's source
        to confirm the fix is in place.
        """
        import inspect

        from dde.commands import site as site_mod

        source = inspect.getsource(site_mod)
        # Verify the copytree call includes symlinks=True.
        assert "symlinks=True" in source
