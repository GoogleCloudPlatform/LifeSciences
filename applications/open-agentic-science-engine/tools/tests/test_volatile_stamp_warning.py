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

"""Tests for volatile stamp warnings in _may_write.

When an otherwise-agreeing record differs only on provenance stamps,
_may_write should emit an informational warning naming the changed
stamps.  Exit code stays 0 and the record is NOT rewritten.

Covers:
  - Identical science + different env_version emits warning
  - Identical science + different capability_state emits warning
  - Identical science + same volatile stamps emits no warning
  - Exit code is still 0 (not rewritten, _may_write returns False)
  - timestamp/written_by differences do NOT trigger warning
  - Capability upgrade detection
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.core.provenance import (  # noqa: E402
    _INTERESTING_VOLATILE_FIELDS,
    _capability_upgrades,
    _emit_volatile_stamp_warning,
    _may_write,
    _volatile_stamp_changes,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_record(**overrides: Any) -> dict[str, Any]:
    """Build a minimal analysis record with sensible defaults."""
    record: dict[str, Any] = {
        "record_type": "analysis",
        "source": "raw/structures/model.cif",
        "cli_version": "0.9.1",
        "cli_integrity": "abc1234",
        "env_version": "dde-tools@0.9.1+ab12cd3",
        "timestamp": "2026-09-01T00:00:00Z",
        "threshold_set": "default",
        "thresholds_applied": {"pLDDT": 70},
        "metrics": {"pLDDT_mean": 85.2},
        "assessment": {"verdict": "high_confidence"},
        "written_by": "agent-alpha",
    }
    record.update(overrides)
    return record


def _write_record(path: Path, record: dict[str, Any]) -> None:
    """Write a record to disk as JSON."""
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# _volatile_stamp_changes unit tests
# ---------------------------------------------------------------------------


def test_volatile_stamp_changes_detects_env_version():
    """Different env_version is detected."""
    old = _base_record(env_version="dde-tools@0.9.1+ab12cd3")
    new = _base_record(env_version="dde-tools@0.9.2+ef45gh6")
    changes = _volatile_stamp_changes(old, new)
    assert "env_version" in changes
    assert changes["env_version"] == (
        "dde-tools@0.9.1+ab12cd3",
        "dde-tools@0.9.2+ef45gh6",
    )


def test_volatile_stamp_changes_detects_capability_state():
    """Different capability_state is detected."""
    old = _base_record(capability_state={"hypex": "unavailable"})
    new = _base_record(capability_state={"hypex": "available"})
    changes = _volatile_stamp_changes(old, new)
    assert "capability_state" in changes


def test_volatile_stamp_changes_detects_cli_integrity():
    """Different cli_integrity is detected."""
    old = _base_record(cli_integrity="abc1234")
    new = _base_record(cli_integrity="def5678")
    changes = _volatile_stamp_changes(old, new)
    assert "cli_integrity" in changes
    assert changes["cli_integrity"] == ("abc1234", "def5678")


def test_volatile_stamp_changes_detects_cli_modified():
    """Different cli_modified is detected (absent vs present)."""
    old = _base_record()  # no cli_modified
    new = _base_record(cli_modified=True)
    changes = _volatile_stamp_changes(old, new)
    assert "cli_modified" in changes
    assert changes["cli_modified"] == (None, True)


def test_volatile_stamp_changes_no_diff_when_same():
    """Identical volatile stamps produce no changes."""
    old = _base_record()
    new = _base_record()
    changes = _volatile_stamp_changes(old, new)
    assert changes == {}


def test_volatile_stamp_changes_ignores_timestamp():
    """timestamp is NOT in _INTERESTING_VOLATILE_FIELDS."""
    assert "timestamp" not in _INTERESTING_VOLATILE_FIELDS
    old = _base_record(timestamp="2026-09-01T00:00:00Z")
    new = _base_record(timestamp="2026-09-10T12:00:00Z")
    changes = _volatile_stamp_changes(old, new)
    assert "timestamp" not in changes


def test_volatile_stamp_changes_ignores_written_by():
    """written_by is NOT in _INTERESTING_VOLATILE_FIELDS."""
    assert "written_by" not in _INTERESTING_VOLATILE_FIELDS
    old = _base_record(written_by="agent-alpha")
    new = _base_record(written_by="agent-beta")
    changes = _volatile_stamp_changes(old, new)
    assert "written_by" not in changes


# ---------------------------------------------------------------------------
# _capability_upgrades unit tests
# ---------------------------------------------------------------------------


def test_capability_upgrade_unavailable_to_available():
    """unavailable -> available is flagged as an upgrade."""
    old_state = {"hypex": "unavailable", "alphafold3": "available"}
    new_state = {"hypex": "available", "alphafold3": "available"}
    upgrades = _capability_upgrades(old_state, new_state)
    assert len(upgrades) == 1
    assert upgrades[0] == ("hypex", "unavailable", "available")


def test_capability_upgrade_absent_to_available():
    """absent -> available is flagged as an upgrade."""
    old_state = {"alphafold3": "available"}
    new_state = {"alphafold3": "available", "hypex": "available"}
    upgrades = _capability_upgrades(old_state, new_state)
    assert len(upgrades) == 1
    assert upgrades[0] == ("hypex", "absent", "available")


def test_capability_no_upgrade_when_same():
    """No changes, no upgrades."""
    state = {"hypex": "available"}
    upgrades = _capability_upgrades(state, state)
    assert upgrades == []


def test_capability_no_upgrade_available_to_unavailable():
    """available -> unavailable is not an upgrade (it's a downgrade)."""
    old_state = {"hypex": "available"}
    new_state = {"hypex": "unavailable"}
    upgrades = _capability_upgrades(old_state, new_state)
    assert upgrades == []


def test_capability_no_upgrade_when_none():
    """None states produce no upgrades."""
    assert _capability_upgrades(None, None) == []
    assert _capability_upgrades(None, {"hypex": "available"}) == []
    assert _capability_upgrades({"hypex": "available"}, None) == []


# ---------------------------------------------------------------------------
# _emit_volatile_stamp_warning output tests
# ---------------------------------------------------------------------------


def test_emit_warning_format(tmp_path, capsys):
    """Warning output contains the expected structure."""
    path = tmp_path / "test.analysis.json"
    changes = {
        "env_version": (
            "dde-tools@0.9.1+ab12cd3",
            "dde-tools@0.9.2+ef45gh6",
        ),
    }
    _emit_volatile_stamp_warning(path, changes)
    captured = capsys.readouterr()
    stderr = captured.err
    assert "NOTE:" in stderr
    assert "science unchanged" in stderr
    assert "Not rewritten" in stderr
    assert "Provenance stamps differ" in stderr
    assert "env_version" in stderr
    assert "dde-tools@0.9.1+ab12cd3" in stderr
    assert "dde-tools@0.9.2+ef45gh6" in stderr
    assert "different toolchain conditions" in stderr


def test_emit_warning_capability_state_per_key(tmp_path, capsys):
    """capability_state diffs are shown per-key."""
    path = tmp_path / "test.analysis.json"
    changes = {
        "capability_state": (
            {"hypex": "unavailable", "alphafold3": "available"},
            {"hypex": "available", "alphafold3": "available"},
        ),
    }
    _emit_volatile_stamp_warning(path, changes)
    captured = capsys.readouterr()
    stderr = captured.err
    assert "capability_state.hypex" in stderr
    assert '"unavailable"' in stderr
    assert '"available"' in stderr
    # alphafold3 didn't change, should not appear
    assert "alphafold3" not in stderr


def test_emit_warning_capability_upgrade_note(tmp_path, capsys):
    """Capability upgrade emits the specific upgrade signal."""
    path = tmp_path / "test.analysis.json"
    changes = {
        "capability_state": (
            {"hypex": "unavailable"},
            {"hypex": "available"},
        ),
    }
    _emit_volatile_stamp_warning(path, changes)
    captured = capsys.readouterr()
    stderr = captured.err
    assert "Capability upgrade available:" in stderr
    assert "hypex" in stderr
    assert "--overwrite" in stderr


# ---------------------------------------------------------------------------
# Integration: _may_write with volatile stamp differences
# ---------------------------------------------------------------------------


def test_may_write_emits_warning_on_env_version_diff(tmp_path, capsys):
    """_may_write emits volatile stamp warning when env_version differs."""
    path = tmp_path / "test.analysis.json"
    existing = _base_record(env_version="dde-tools@0.9.1+ab12cd3")
    new = _base_record(
        env_version="dde-tools@0.9.2+ef45gh6",
        timestamp="2026-09-10T00:00:00Z",
        written_by="agent-beta",
    )
    _write_record(path, existing)

    # Mock _normalize_source to be a no-op (avoids project root lookup).
    with patch("dde.core.provenance._normalize_source", side_effect=lambda s: s):
        result = _may_write(path, new)

    assert result is False  # record not rewritten
    captured = capsys.readouterr()
    stderr = captured.err
    assert "env_version" in stderr
    assert "dde-tools@0.9.1+ab12cd3" in stderr
    assert "dde-tools@0.9.2+ef45gh6" in stderr


def test_may_write_emits_warning_on_capability_state_diff(tmp_path, capsys):
    """_may_write emits volatile stamp warning when capability_state differs."""
    path = tmp_path / "test.analysis.json"
    existing = _base_record(capability_state={"hypex": "unavailable"})
    new = _base_record(
        capability_state={"hypex": "available"},
        timestamp="2026-09-10T00:00:00Z",
        written_by="agent-beta",
    )
    _write_record(path, existing)

    with patch("dde.core.provenance._normalize_source", side_effect=lambda s: s):
        result = _may_write(path, new)

    assert result is False
    captured = capsys.readouterr()
    stderr = captured.err
    assert "capability_state.hypex" in stderr
    assert "Capability upgrade available:" in stderr


def test_may_write_no_warning_when_stamps_same(tmp_path, capsys):
    """_may_write does NOT emit volatile stamp warning when stamps are identical."""
    path = tmp_path / "test.analysis.json"
    existing = _base_record()
    new = _base_record(
        timestamp="2026-09-10T00:00:00Z",
        written_by="agent-beta",
    )
    _write_record(path, existing)

    with patch("dde.core.provenance._normalize_source", side_effect=lambda s: s):
        result = _may_write(path, new)

    assert result is False
    captured = capsys.readouterr()
    stderr = captured.err
    # The standard "identical record" message should be there
    assert "identical record" in stderr
    # But no volatile stamp warning
    assert "Provenance stamps differ" not in stderr


def test_may_write_returns_false_exit_zero(tmp_path):
    """_may_write returns False (no rewrite) — caller exits 0."""
    path = tmp_path / "test.analysis.json"
    existing = _base_record(env_version="old")
    new = _base_record(env_version="new", timestamp="now", written_by="x")
    _write_record(path, existing)

    with patch("dde.core.provenance._normalize_source", side_effect=lambda s: s):
        result = _may_write(path, new)

    # _may_write returns False: the record is NOT rewritten, exit 0.
    assert result is False
    # Verify file is unchanged.
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["env_version"] == "old"


def test_may_write_suppress_warnings_no_output(tmp_path, capsys):
    """suppress_warnings=True suppresses all warnings including volatile stamp."""
    path = tmp_path / "test.analysis.json"
    existing = _base_record(env_version="old")
    new = _base_record(env_version="new", timestamp="now")
    _write_record(path, existing)

    with patch("dde.core.provenance._normalize_source", side_effect=lambda s: s):
        result = _may_write(path, new, suppress_warnings=True)

    assert result is False
    captured = capsys.readouterr()
    assert captured.err == ""


def test_may_write_timestamp_written_by_only_no_stamp_warning(tmp_path, capsys):
    """Only timestamp/written_by differ — no volatile stamp warning."""
    path = tmp_path / "test.analysis.json"
    existing = _base_record(
        timestamp="2026-09-01T00:00:00Z",
        written_by="agent-alpha",
    )
    new = _base_record(
        timestamp="2026-09-10T12:00:00Z",
        written_by="agent-beta",
    )
    _write_record(path, existing)

    with patch("dde.core.provenance._normalize_source", side_effect=lambda s: s):
        result = _may_write(path, new)

    assert result is False
    captured = capsys.readouterr()
    stderr = captured.err
    # Standard identical-record message should appear
    assert "identical record" in stderr
    # No volatile stamp warning
    assert "Provenance stamps differ" not in stderr


def test_may_write_multiple_stamp_changes(tmp_path, capsys):
    """Multiple volatile stamp changes are all reported."""
    path = tmp_path / "test.analysis.json"
    existing = _base_record(
        env_version="old-env",
        cli_integrity="old-commit",
    )
    new = _base_record(
        env_version="new-env",
        cli_integrity="new-commit",
        timestamp="2026-09-10T00:00:00Z",
        written_by="agent-beta",
    )
    _write_record(path, existing)

    with patch("dde.core.provenance._normalize_source", side_effect=lambda s: s):
        result = _may_write(path, new)

    assert result is False
    captured = capsys.readouterr()
    stderr = captured.err
    assert "env_version" in stderr
    assert "cli_integrity" in stderr
    assert "old-env" in stderr
    assert "new-env" in stderr
    assert "old-commit" in stderr
    assert "new-commit" in stderr


# ---------------------------------------------------------------------------
# Direct-run support
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
