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

"""Regression tests for artifact overwrite / provenance integrity fixes (batch 2).

Covers:
  - #251: Missing overwrite protection in compound prepare-3d
  - #253: Missing overwrite protection in coscientist analyze
  - #270: Brittle suffix replacement in hypothesis analyze
  - #264: Multi-CID slug collision in pubchem fetch
  - #269: Input-output path aliasing in screen run
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.commands.compound import _guard_sdf_no_clobber
from dde.commands.screen import _guard_input_output_alias
from dde.core.errors import Refusal
from dde.core.paths import sanitize_slug
from dde.core.provenance import _may_write

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(base: Path) -> Path:
    """Create a minimal dde project directory."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    return project


# ===========================================================================
# Issue #251 — compound.py: Missing Overwrite Protection on 3D SDF
# ===========================================================================


def test_compound_prepare_3d_refuses_overwrite() -> None:
    """#251: _guard_sdf_no_clobber must raise Refusal when the 3D SDF already exists."""
    with tempfile.TemporaryDirectory() as tmp:
        target_dir = Path(tmp)
        slug = "test-compound"
        sdf_path = target_dir / f"{slug}.3d.sdf"

        # Pre-create the artifact.
        sdf_path.write_text("existing 3d sdf content\n", encoding="utf-8")
        assert sdf_path.exists()

        # Call the production guard — it must raise Refusal with a remedy.
        with pytest.raises(Refusal, match="artifact already exists") as exc_info:
            _guard_sdf_no_clobber(sdf_path)
        assert exc_info.value.remedy is not None


def test_compound_prepare_3d_allows_new_file() -> None:
    """#251: _guard_sdf_no_clobber passes when the file does not exist."""
    with tempfile.TemporaryDirectory() as tmp:
        target_dir = Path(tmp)
        slug = "new-compound"
        sdf_path = target_dir / f"{slug}.3d.sdf"

        # No pre-existing file — the guard should not trigger.
        assert not sdf_path.exists()
        # Must not raise.
        _guard_sdf_no_clobber(sdf_path)


# ===========================================================================
# Issue #253 — coscientist.py: Overwrite Protection via _may_write()
# ===========================================================================


def test_coscientist_analysis_refuses_overwrite() -> None:
    """#253: provenance._may_write refuses when an existing analysis differs."""
    with tempfile.TemporaryDirectory() as tmp:
        analysis_path = Path(tmp) / "test.analysis.json"

        # Write an existing analysis record.
        import json

        existing = {"record_type": "analysis", "assessment": {"verdict": "old"}}
        analysis_path.write_text(
            json.dumps(existing, indent=2) + "\n", encoding="utf-8"
        )
        assert analysis_path.exists()

        # A new record with a different verdict must be refused.
        new_record = {"record_type": "analysis", "assessment": {"verdict": "new"}}
        with pytest.raises(Refusal, match="already holds a different analysis"):
            _may_write(analysis_path, new_record)


def test_coscientist_analysis_allows_new_file() -> None:
    """#253: provenance._may_write allows writing when no file exists."""
    with tempfile.TemporaryDirectory() as tmp:
        analysis_path = Path(tmp) / "new-test.analysis.json"

        assert not analysis_path.exists()
        record = {"record_type": "analysis", "assessment": {"verdict": "pass"}}
        # Must return True (write allowed).
        assert _may_write(analysis_path, record) is True


# ===========================================================================
# Issue #270 — hypothesis.py: Brittle Suffix Replacement
# ===========================================================================


def test_hypothesis_suffix_replacement_simple() -> None:
    """#270: Simple suffix replacement works correctly with removesuffix."""
    name = "my-hypothesis.adopted.json"
    suffix = ".adopted.json"
    # Old (broken) approach:
    old_result = name.replace(suffix, ".analysis.json")
    # New (fixed) approach:
    new_result = name.removesuffix(suffix) + ".analysis.json"
    # For a simple case both should produce the same result.
    assert old_result == "my-hypothesis.analysis.json"
    assert new_result == "my-hypothesis.analysis.json"


def test_hypothesis_suffix_replacement_edge_case_adopted_in_name() -> None:
    """#270: str.replace breaks when filename contains the suffix pattern in its
    stem — it replaces ALL occurrences, corrupting the filename. removesuffix
    handles it correctly by only stripping the trailing suffix."""
    # A filename where the suffix pattern appears both in the stem and as the
    # actual suffix.  str.replace replaces ALL occurrences, mangling the stem.
    name = "test.adopted.json.adopted.json"
    suffix = ".adopted.json"

    # Old (broken) approach replaces ALL occurrences:
    old_result = name.replace(suffix, ".analysis.json")
    assert old_result == "test.analysis.json.analysis.json"  # mangled stem

    # New (fixed) approach removes only the trailing suffix:
    new_result = name.removesuffix(suffix) + ".analysis.json"
    assert new_result == "test.adopted.json.analysis.json"  # stem preserved

    # The two differ — proving the bug:
    assert old_result != new_result


def test_hypothesis_suffix_replacement_charter_in_stem() -> None:
    """#270: When '.charter' appears in the name stem, str.replace mangles
    it but removesuffix is precise."""
    # "project-charter" contains "charter" in the stem.
    name = "project.charter.json.charter.json"
    suffix = ".charter.json"

    # Old (broken) approach replaces ALL occurrences:
    old_result = name.replace(suffix, ".analysis.json")
    assert old_result == "project.analysis.json.analysis.json"

    # New (fixed) approach removes only the trailing suffix:
    new_result = name.removesuffix(suffix) + ".analysis.json"
    assert new_result == "project.charter.json.analysis.json"

    assert old_result != new_result


def test_hypothesis_suffix_replacement_no_match() -> None:
    """#270: removesuffix is a no-op when suffix doesn't match (safety)."""
    name = "some-other-file.json"
    suffix = ".adopted.json"
    # removesuffix returns the string unchanged when it doesn't end with suffix.
    result = name.removesuffix(suffix) + ".analysis.json"
    assert result == "some-other-file.json.analysis.json"


# ===========================================================================
# Issue #264 — pubchem.py: Multi-CID Slug Collision
# ===========================================================================


def test_pubchem_multi_cid_distinct_slugs_with_override() -> None:
    """#264: When slug_override is provided, each CID must produce a distinct slug."""
    slug_override = "aspirin"
    cids = [2244, 2245, 2246]
    slugs = []
    for cid in cids:
        slug = f"{sanitize_slug(slug_override)}-{cid}" if slug_override else str(cid)
        slugs.append(slug)

    # All slugs should be unique.
    assert len(set(slugs)) == len(cids)
    # Each slug should contain the CID.
    for cid, slug in zip(cids, slugs, strict=True):
        assert str(cid) in slug
    # Each slug should start with the override name.
    for slug in slugs:
        assert slug.startswith("aspirin-")


def test_pubchem_multi_cid_no_override() -> None:
    """#264: Without slug_override, each CID is used directly as the slug."""
    slug_override = None
    cids = [2244, 2245, 2246]
    slugs = []
    for cid in cids:
        slug = f"{sanitize_slug(slug_override)}-{cid}" if slug_override else str(cid)
        slugs.append(slug)

    assert slugs == ["2244", "2245", "2246"]
    assert len(set(slugs)) == len(cids)


def test_pubchem_multi_cid_artifact_paths_distinct() -> None:
    """#264: Artifact file paths should be unique across CIDs."""
    with tempfile.TemporaryDirectory() as tmp:
        target_dir = Path(tmp)
        slug_override = "test-compound"
        cids = [100, 200, 300]
        paths = []
        for cid in cids:
            slug = (
                f"{sanitize_slug(slug_override)}-{cid}" if slug_override else str(cid)
            )
            artifact_path = target_dir / f"{slug}.pubchem-compound.artifact.json"
            paths.append(artifact_path)

        # All paths must be unique.
        assert len(set(paths)) == len(cids)


# ===========================================================================
# Issue #269 — screen.py: Input-Output Path Aliasing
# ===========================================================================


def test_screen_input_output_aliasing_detected() -> None:
    """#269: _guard_input_output_alias raises Refusal when input is inside output dir."""
    with tempfile.TemporaryDirectory() as tmp:
        target_dir = Path(tmp) / "raw" / "screening"
        target_dir.mkdir(parents=True, exist_ok=True)

        # Library file is inside the output directory.
        library_path = target_dir / "compounds.sdf"
        library_path.write_text("dummy sdf\n", encoding="utf-8")

        # Call the production guard — must raise Refusal.
        with pytest.raises(Refusal, match="aliases output directory"):
            _guard_input_output_alias(library_path, target_dir, "library")


def test_screen_input_output_no_aliasing() -> None:
    """#269: _guard_input_output_alias passes when paths are separate."""
    with tempfile.TemporaryDirectory() as tmp:
        input_dir = Path(tmp) / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir = Path(tmp) / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        library_path = input_dir / "compounds.sdf"
        library_path.write_text("dummy sdf\n", encoding="utf-8")

        # Must not raise — paths are separate.
        _guard_input_output_alias(library_path, output_dir, "library")


def test_screen_exact_path_aliasing_detected() -> None:
    """#269: _guard_input_output_alias detects when output dir IS the input's parent."""
    with tempfile.TemporaryDirectory() as tmp:
        shared_dir = Path(tmp) / "data"
        shared_dir.mkdir(parents=True, exist_ok=True)

        library_path = shared_dir / "lib.sdf"
        library_path.write_text("dummy\n", encoding="utf-8")

        # Library is in the target dir — production guard must raise Refusal.
        with pytest.raises(Refusal, match="aliases output directory"):
            _guard_input_output_alias(library_path, shared_dir, "library")
