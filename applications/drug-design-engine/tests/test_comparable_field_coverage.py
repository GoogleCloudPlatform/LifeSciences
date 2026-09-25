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

"""Systemic guard: every field in an analysis record must be classified.

``_comparable()`` decides whether two analysis records agree.  Fields can
be in one of three buckets:

* **volatile** — excluded from comparison (``_VOLATILE_ANALYSIS_FIELDS``).
  These are provenance stamps: tool version, commit SHA, environment,
  attribution, capability snapshots.  A change here never triggers a
  mismatch.
* **normalised** — transformed before comparison
  (``_NORMALIZED_ANALYSIS_FIELDS``).  A change in notation (but not
  substance) does not trigger a mismatch.
* **scientific** — compared as-is.  Any change triggers a mismatch.
  These carry the verdict: source data, thresholds, metrics, assessment.

This test enumerates every field that ``write_analysis()`` can produce
and asserts each is explicitly classified.  A new field added without
classification fails CI, forcing the author to decide how the field
interacts with ``_may_write()`` before the bug ships.

Pattern follows ``test_artifact_dirs_completeness.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure the tools package is importable.
_TOOLS_ROOT = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOLS_ROOT))

from dde.core.provenance import (
    _NORMALIZED_ANALYSIS_FIELDS,
    _VOLATILE_ANALYSIS_FIELDS,
    _comparable,
    _may_write,
)

# ---------------------------------------------------------------------------
# The canonical registry of scientific fields.  A field is scientific
# when it carries substantive content and should trigger a mismatch if it
# changes.  This set MUST be updated when write_analysis() gains a new
# scientific field — the test below enforces that.
# ---------------------------------------------------------------------------

_SCIENTIFIC_ANALYSIS_FIELDS = frozenset(
    {
        "cli_version",
        "threshold_set",
        "thresholds_applied",
        "metrics",
        "assessment",
        "mandatory_relays",
        "threshold_sources",
        "threshold_provenance",
        "thresholds_unresolved",
        "source_sha256",
    }
)

# All three buckets must be disjoint and exhaustive.
_ALL_CLASSIFIED = (
    frozenset(_VOLATILE_ANALYSIS_FIELDS)
    | frozenset(_NORMALIZED_ANALYSIS_FIELDS)
    | _SCIENTIFIC_ANALYSIS_FIELDS
)


# ---------------------------------------------------------------------------
# Helper: build a maximal analysis record (all optional fields present)
# ---------------------------------------------------------------------------


def _maximal_record() -> dict[str, object]:
    """Return an analysis record with every optional field populated.

    This does not go through write_analysis() — that would need a real
    project root, toolchain integrity, and filesystem — but it mirrors
    the dict literal in write_analysis() exactly, including every
    conditional branch.
    """
    return {
        # Normalised fields:
        "record_type": "analysis",
        "source": "raw/chemistry/compound_data.json",
        # Volatile (provenance) fields:
        "cli_integrity": "abc1234",
        "env_version": "test",
        "timestamp": "2025-01-01T00:00:00Z",
        "written_by": "test-agent",
        "cli_modified": True,
        "cli_modified_note": "test",
        "capability_state": {"hypex": "available"},
        # Scientific fields:
        "cli_version": "0.0.0-test",
        "threshold_set": "default",
        "thresholds_applied": {"key": 0.5},
        "metrics": {"score": 1.0},
        "assessment": {"verdict": "pass"},
        "mandatory_relays": [],
        "threshold_sources": {"key": "source"},
        "threshold_provenance": "inline",
        "thresholds_unresolved": ["x"],
        "work_order_id": "WO-001",
        "source_sha256": "abc123",
    }


# ---------------------------------------------------------------------------
# Guard test — prevents future _comparable() / _may_write() regressions
# ---------------------------------------------------------------------------


class TestComparableFieldCoverage:
    """Every analysis-record field must be classified as volatile,
    normalised, or scientific."""

    def test_buckets_are_disjoint(self) -> None:
        """No field appears in more than one bucket."""
        vol = frozenset(_VOLATILE_ANALYSIS_FIELDS)
        norm = frozenset(_NORMALIZED_ANALYSIS_FIELDS)
        sci = _SCIENTIFIC_ANALYSIS_FIELDS

        assert not (vol & norm), f"volatile ∩ normalised: {vol & norm}"
        assert not (vol & sci), f"volatile ∩ scientific: {vol & sci}"
        assert not (norm & sci), f"normalised ∩ scientific: {norm & sci}"

    def test_all_record_fields_classified(self) -> None:
        """Every field in a maximal analysis record is in one of the
        three buckets.

        A failure here means write_analysis() gained a new field that
        is not yet classified.  Fix: add the field to one of:

        * ``_VOLATILE_ANALYSIS_FIELDS`` in provenance.py — if the field
          is a provenance stamp (tool environment, attribution).
        * ``_NORMALIZED_ANALYSIS_FIELDS`` in provenance.py — if the
          field needs notation normalisation in ``_comparable()``.
        * ``_SCIENTIFIC_ANALYSIS_FIELDS`` in this test file — if the
          field carries scientific content and changes should trigger
          exit 9.
        """
        record = _maximal_record()
        unclassified = set(record.keys()) - _ALL_CLASSIFIED

        assert not unclassified, (
            "Analysis-record field(s) not classified for _comparable():\n"
            + "\n".join(f"  - {f!r}" for f in sorted(unclassified))
            + "\n\nEvery field must be in exactly one of:\n"
            "  _VOLATILE_ANALYSIS_FIELDS   (provenance.py) — provenance stamps\n"
            "  _NORMALIZED_ANALYSIS_FIELDS  (provenance.py) — notation-normalised\n"
            "  _SCIENTIFIC_ANALYSIS_FIELDS  (this test file) — scientific content\n"
            "\nSee test_comparable_field_coverage.py docstring for details."
        )

    def test_no_stale_classifications(self) -> None:
        """No classified field is absent from the maximal record.

        Catches stale entries left behind after a field is removed.
        """
        record_fields = set(_maximal_record().keys())
        stale = _ALL_CLASSIFIED - record_fields

        assert not stale, (
            "Classified field(s) no longer appear in analysis records:\n"
            + "\n".join(f"  - {f!r}" for f in sorted(stale))
            + "\n\nRemove them from their bucket in provenance.py or this test."
        )


# ---------------------------------------------------------------------------
# Source normalisation in _comparable()
# ---------------------------------------------------------------------------


class TestComparableSourceNormalization:
    """_comparable() normalises source before comparison so that bare
    filenames (pre-#129) and project-relative paths (post-#129)
    compare equal when they refer to the same file."""

    def test_bare_filename_and_relative_path_compare_equal(
        self, tmp_path: Path
    ) -> None:
        """Old artifact: source='compound_data.json'
        New artifact: source='raw/compounds/compound_data.json'
        Same file -> _comparable() should produce equal dicts."""
        # Create a fake project with the file in an artifact dir
        # that exists in ARTIFACT_DIRS (compounds -> raw/compounds).
        (tmp_path / "raw" / "compounds").mkdir(parents=True)
        (tmp_path / "raw" / "compounds" / "compound_data.json").write_text("{}")
        (tmp_path / ".dde").mkdir()

        old_record = _maximal_record()
        old_record["source"] = "compound_data.json"
        del old_record["record_type"]  # simulate pre-#130

        new_record = _maximal_record()
        new_record["source"] = "raw/compounds/compound_data.json"
        new_record["record_type"] = "analysis"

        # Patch _normalize_source to resolve the bare filename against
        # our tmp_path instead of the real project root.
        def mock_normalize(source: str) -> str:
            from dde.core.context import ARTIFACT_DIRS

            if "/" not in source and "\\" not in source and "." in source:
                for rel_dir in sorted(set(ARTIFACT_DIRS.values())):
                    candidate = tmp_path / rel_dir / source
                    if candidate.is_file():
                        return str(Path(rel_dir) / source)
            return source

        with patch("dde.core.provenance._normalize_source", side_effect=mock_normalize):
            assert _comparable(old_record) == _comparable(new_record)

    def test_different_files_still_differ(self) -> None:
        """source='compound_data.json' vs source='raw/chemistry/OTHER.json'
        should NOT compare equal."""
        old_record = _maximal_record()
        old_record["source"] = "raw/chemistry/compound_data.json"

        new_record = _maximal_record()
        new_record["source"] = "raw/chemistry/OTHER_data.json"

        # No normalisation needed — both are already relative paths.
        assert _comparable(old_record) != _comparable(new_record)

    def test_normalize_source_is_idempotent(self, tmp_path: Path) -> None:
        """Applying _normalize_source twice yields the same result."""
        (tmp_path / "raw" / "chemistry").mkdir(parents=True)
        (tmp_path / "raw" / "chemistry" / "data.json").write_text("{}")
        (tmp_path / ".dde").mkdir()

        def mock_normalize(source: str) -> str:
            from dde.core.context import ARTIFACT_DIRS

            if "/" not in source and "\\" not in source and "." in source:
                for rel_dir in sorted(set(ARTIFACT_DIRS.values())):
                    candidate = tmp_path / rel_dir / source
                    if candidate.is_file():
                        return str(Path(rel_dir) / source)
            return source

        with patch("dde.core.provenance._normalize_source", side_effect=mock_normalize):
            once = _comparable({"source": "data.json"})
            twice = _comparable({"source": once["source"]})
            assert once["source"] == twice["source"]

    def test_already_normalized_source_unchanged(self) -> None:
        """A source that is already a relative path passes through."""
        record = _maximal_record()
        record["source"] = "raw/tox/compound.selectivity.json"

        result = _comparable(record)
        assert result["source"] == "raw/tox/compound.selectivity.json"


# ---------------------------------------------------------------------------
# record_type absence handling in _comparable()
# ---------------------------------------------------------------------------


class TestComparableRecordTypeAbsence:
    """_comparable() defaults absent record_type to 'analysis' so
    pre-#130 records compare equal to post-#130 records."""

    def test_absent_record_type_defaults_to_analysis(self) -> None:
        """Old record without record_type should compare equal to new
        record with record_type='analysis'."""
        old_record = _maximal_record()
        del old_record["record_type"]

        new_record = _maximal_record()
        new_record["record_type"] = "analysis"

        assert _comparable(old_record) == _comparable(new_record)

    def test_present_record_type_preserved(self) -> None:
        """If record_type is present, it should be used as-is."""
        record = _maximal_record()
        record["record_type"] = "analysis"

        result = _comparable(record)
        assert result["record_type"] == "analysis"

    def test_different_record_type_still_differs(self) -> None:
        """A hypothetical non-analysis record_type should not match."""
        record_a = _maximal_record()
        record_a["record_type"] = "analysis"

        record_b = _maximal_record()
        record_b["record_type"] = "meta"

        assert _comparable(record_a) != _comparable(record_b)


# ---------------------------------------------------------------------------
# Volatile / provenance field exclusion
# ---------------------------------------------------------------------------


class TestComparableVolatileExclusion:
    """Provenance stamps are excluded from comparison — changes in tool
    environment, commit SHA, or attribution never cause exit 9."""

    def test_timestamp_excluded(self) -> None:
        a = _maximal_record()
        b = _maximal_record()
        b["timestamp"] = "2099-12-31T23:59:59Z"
        assert _comparable(a) == _comparable(b)

    def test_written_by_excluded(self) -> None:
        a = _maximal_record()
        b = _maximal_record()
        b["written_by"] = "different-agent"
        assert _comparable(a) == _comparable(b)

    def test_cli_integrity_excluded(self) -> None:
        """cli_integrity (bare commit SHA) changes on every commit."""
        a = _maximal_record()
        b = _maximal_record()
        b["cli_integrity"] = "completely-different-sha"
        assert _comparable(a) == _comparable(b)

    def test_env_version_excluded(self) -> None:
        a = _maximal_record()
        b = _maximal_record()
        b["env_version"] = "python-4.0-future"
        assert _comparable(a) == _comparable(b)

    def test_capability_state_excluded(self) -> None:
        a = _maximal_record()
        b = _maximal_record()
        b["capability_state"] = {"hypex": "unavailable", "new_cap": "available"}
        assert _comparable(a) == _comparable(b)

    def test_cli_modified_excluded(self) -> None:
        """cli_modified is conditional (absent when clean, True when dirty)."""
        a = _maximal_record()
        b = _maximal_record()
        del b["cli_modified"]
        del b["cli_modified_note"]
        assert _comparable(a) == _comparable(b)

    def test_substantive_change_detected(self) -> None:
        """A change in a scientific field must trigger a mismatch."""
        a = _maximal_record()
        b = _maximal_record()
        b["threshold_set"] = "different_set"
        assert _comparable(a) != _comparable(b)


# ---------------------------------------------------------------------------
# End-to-end: unchanged analysis re-run exits 0
# ---------------------------------------------------------------------------


class TestUnchangedRerunExitsClean:
    """An idempotent re-run of an analysis must NOT trigger exit 9,
    even when provenance stamps (cli_integrity, env_version, etc.)
    differ between the stored record and the new write."""

    def test_rerun_with_different_provenance_compares_equal(self) -> None:
        """Simulate: same science, different tool environment."""
        stored = _maximal_record()
        stored["cli_integrity"] = "old-commit-sha"
        stored["env_version"] = "old-env"
        stored["timestamp"] = "2025-01-01T00:00:00Z"
        stored["written_by"] = "agent-alpha"
        stored["capability_state"] = {"hypex": "unavailable"}

        rerun = _maximal_record()
        rerun["cli_integrity"] = "new-commit-sha"
        rerun["env_version"] = "new-env"
        rerun["timestamp"] = "2025-06-15T12:00:00Z"
        rerun["written_by"] = "agent-beta"
        rerun["capability_state"] = {"hypex": "available"}

        # Science is identical — _comparable() must agree.
        assert _comparable(stored) == _comparable(rerun)

    def test_rerun_pre130_record_with_post130_tool(self) -> None:
        """Pre-#130 record (no record_type) re-run with post-#130 tool
        (record_type='analysis', different cli_integrity)."""
        stored = _maximal_record()
        del stored["record_type"]
        del stored["cli_modified"]
        del stored["cli_modified_note"]
        stored["cli_integrity"] = "old-sha"

        rerun = _maximal_record()
        rerun["record_type"] = "analysis"
        rerun["cli_integrity"] = "new-sha"

        assert _comparable(stored) == _comparable(rerun)


# ---------------------------------------------------------------------------
# work_order_id is volatile — different WO, same science, should agree
# ---------------------------------------------------------------------------


class TestWorkOrderIdVolatile:
    """work_order_id is an attribution field, not scientific content.
    Two records with identical science but different work_order_id
    must compare equal via _comparable()."""

    def test_different_work_order_id_compares_equal(self) -> None:
        """WO-A specialist and WO-B reviewer produce identical science."""
        wo_a = _maximal_record()
        wo_a["work_order_id"] = "WO-A"

        wo_b = _maximal_record()
        wo_b["work_order_id"] = "WO-B"

        assert _comparable(wo_a) == _comparable(wo_b)

    def test_work_order_id_absent_vs_present(self) -> None:
        """A record without work_order_id should compare equal to one
        with it, since the field is volatile."""
        without = _maximal_record()
        del without["work_order_id"]

        with_wo = _maximal_record()
        with_wo["work_order_id"] = "WO-123"

        assert _comparable(without) == _comparable(with_wo)


# ---------------------------------------------------------------------------
# ArtifactError from _normalize_source() caught by _may_write()
# ---------------------------------------------------------------------------


class TestArtifactErrorCaughtInMayWrite:
    """A pathological source path that causes _normalize_source() to raise
    ArtifactError must not propagate uncaught from _may_write().  Instead
    it falls into the 'cannot be shown to agree' branch (Refusal/exit-9)."""

    def test_artifact_error_does_not_propagate(self, tmp_path: Path) -> None:
        """_may_write() catches ArtifactError from _comparable() and
        raises Refusal rather than letting ArtifactError escape."""
        import json as _json

        from dde.core.errors import Refusal

        # Write a stored record with a pathological source that will
        # make _normalize_source() raise ArtifactError.
        stored = _maximal_record()
        stored["source"] = "/etc/passwd/../../../escape"

        analysis_path = tmp_path / "compound.analysis.json"
        analysis_path.write_text(_json.dumps(stored), encoding="utf-8")

        new_record = _maximal_record()

        # _may_write should NOT raise ArtifactError.  It should raise
        # Refusal (the "cannot be shown to agree" path) because the
        # comparison cannot be completed.
        with pytest.raises(Refusal):
            _may_write(analysis_path, new_record)
