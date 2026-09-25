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

"""Tests for the Critical-liability checkpoint in _perform_commit() (#26).

Covers:
  - _find_critical_liabilities returns empty list when no tracker exists
  - _find_critical_liabilities returns empty list when tracker has no Critical entries
  - _find_critical_liabilities finds active Critical liabilities
  - _find_critical_liabilities ignores mitigated Critical liabilities
  - _find_critical_liabilities ignores accepted Critical liabilities
  - _find_critical_liabilities handles mixed severities correctly
  - _perform_commit succeeds with no liabilities (no tracker)
  - _perform_commit succeeds with Critical liabilities + justification
  - _perform_commit refuses with Critical liabilities + no justification
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from dde.commands.workorder import _find_critical_liabilities, _perform_commit
from dde.core import controlstore
from dde.core.errors import Refusal

# ---------------------------------------------------------------------------
# _find_critical_liabilities tests
# ---------------------------------------------------------------------------


class TestFindCriticalLiabilities(unittest.TestCase):
    """Unit tests for _find_critical_liabilities."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name) / "project"
        self.root.mkdir()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_no_tracker_returns_empty(self):
        """No liability-tracker.md anywhere => empty list."""
        result = _find_critical_liabilities(self.root)
        self.assertEqual(result, [])

    def test_empty_tracker_returns_empty(self):
        """Tracker exists but has no entries => empty list."""
        ps = self.root / "program-state"
        ps.mkdir(parents=True)
        (ps / "liability-tracker.md").write_text(
            "# Liability Tracker\n\nNo liabilities registered.\n",
            encoding="utf-8",
        )
        result = _find_critical_liabilities(self.root)
        self.assertEqual(result, [])

    def test_finds_active_critical(self):
        """Active Critical liability is returned."""
        ps = self.root / "program-state"
        ps.mkdir(parents=True)
        (ps / "liability-tracker.md").write_text(
            "# Liability Tracker\n\n"
            "## L-2\n\n"
            "**Severity**: critical\n"
            "**Status**: open\n\n"
            "A critical problem.\n",
            encoding="utf-8",
        )
        result = _find_critical_liabilities(self.root)
        self.assertEqual(result, ["L-2"])

    def test_mitigated_critical_ignored(self):
        """Mitigated Critical liability is not returned."""
        ps = self.root / "program-state"
        ps.mkdir(parents=True)
        (ps / "liability-tracker.md").write_text(
            "# Liability Tracker\n\n"
            "## L-3\n\n"
            "**Severity**: critical\n"
            "**Status**: mitigated\n\n"
            "Was critical, now mitigated.\n",
            encoding="utf-8",
        )
        result = _find_critical_liabilities(self.root)
        self.assertEqual(result, [])

    def test_accepted_critical_ignored(self):
        """Accepted Critical liability is not returned."""
        ps = self.root / "program-state"
        ps.mkdir(parents=True)
        (ps / "liability-tracker.md").write_text(
            "# Liability Tracker\n\n"
            "## L-4\n\n"
            "**Severity**: critical\n"
            "**Status**: accepted\n\n"
            "Accepted risk.\n",
            encoding="utf-8",
        )
        result = _find_critical_liabilities(self.root)
        self.assertEqual(result, [])

    def test_mixed_severities(self):
        """Only active Critical entries are returned, not monitor/informational."""
        ps = self.root / "program-state"
        ps.mkdir(parents=True)
        (ps / "liability-tracker.md").write_text(
            "# Liability Tracker\n\n"
            "## L-1\n\n"
            "**Severity**: monitor\n"
            "**Status**: open\n\n"
            "Just monitoring.\n\n"
            "## L-2\n\n"
            "**Severity**: critical\n"
            "**Status**: under investigation\n\n"
            "Critical and active.\n\n"
            "## L-3\n\n"
            "**Severity**: informational\n"
            "**Status**: open\n\n"
            "FYI only.\n\n"
            "## L-4\n\n"
            "**Severity**: critical\n"
            "**Status**: mitigated\n\n"
            "Was critical, now mitigated.\n\n"
            "## L-5\n\n"
            "**Severity**: Critical\n"
            "**Status**: open\n\n"
            "Another critical one (title-case severity).\n",
            encoding="utf-8",
        )
        result = _find_critical_liabilities(self.root)
        self.assertEqual(result, ["L-2", "L-5"])

    def test_severity_case_insensitive(self):
        """Severity matching is case-insensitive."""
        ps = self.root / "program-state"
        ps.mkdir(parents=True)
        (ps / "liability-tracker.md").write_text(
            "# Liability Tracker\n\n"
            "## L-7\n\n"
            "**Severity**: CRITICAL\n"
            "**Status**: open\n\n"
            "Uppercase critical.\n",
            encoding="utf-8",
        )
        result = _find_critical_liabilities(self.root)
        self.assertEqual(result, ["L-7"])


# ---------------------------------------------------------------------------
# _perform_commit checkpoint integration tests
# ---------------------------------------------------------------------------


def _make_proposed_wo(project_root: Path, wo_id: str = "WO-001", **extra: Any) -> None:
    """Create a minimal proposed work order record suitable for committing."""
    controlstore.ensure_control_dirs(project_root)
    record: dict[str, Any] = {
        "id": wo_id,
        "revision": 1,
        "state": "proposed",
        "decision_question": "Does X hold?",
        "requested_role": "analyst",
        "stage": "discovery",
        "cycle": "C1",
        "context": {"content": "background", "artifact_links": []},
        "dependencies": [],
        "capabilities": [],
        "deliverables": {"layer_0_classes": ["test"]},
        "acceptance_criteria": {"criteria": "pass"},
        "alert_policy": {"on_fail": "notify"},
        "priority": "normal",
        "resource_class": "standard",
        "report_to": "lead",
        "created_at": "2026-01-01T00:00:00Z",
        "committed_at": None,
    }
    record.update(extra)
    controlstore.write_record(project_root, "work-order", f"{wo_id}-r1", record)


class TestCommitLiabilityCheckpoint(unittest.TestCase):
    """Integration tests for the liability checkpoint in _perform_commit."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name) / "project"
        self.root.mkdir()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_commit_succeeds_without_tracker(self):
        """No liability tracker => commit proceeds normally."""
        _make_proposed_wo(self.root)
        record, _snapshot_path, _record_path = _perform_commit(self.root, "WO-001")
        self.assertEqual(record["state"], "committed")

    def test_commit_succeeds_with_justification(self):
        """Active Critical liabilities + liability_justification => commit proceeds."""
        ps = self.root / "program-state"
        ps.mkdir(parents=True)
        (ps / "liability-tracker.md").write_text(
            "# Liability Tracker\n\n"
            "## L-2\n\n"
            "**Severity**: critical\n"
            "**Status**: open\n\n"
            "Critical problem.\n",
            encoding="utf-8",
        )
        _make_proposed_wo(
            self.root,
            liability_justification={
                "L-2": "This WO is the investigation of L-2",
            },
        )
        record, _snapshot_path, _record_path = _perform_commit(self.root, "WO-001")
        self.assertEqual(record["state"], "committed")
        # Justification is preserved in the record.
        self.assertIn("liability_justification", record)

    def test_commit_refused_without_justification(self):
        """Active Critical liabilities + no justification => Refusal."""
        ps = self.root / "program-state"
        ps.mkdir(parents=True)
        (ps / "liability-tracker.md").write_text(
            "# Liability Tracker\n\n"
            "## L-2\n\n"
            "**Severity**: critical\n"
            "**Status**: open\n\n"
            "Critical problem.\n",
            encoding="utf-8",
        )
        _make_proposed_wo(self.root)
        with self.assertRaises(Refusal) as ctx:
            _perform_commit(self.root, "WO-001")
        exc = ctx.exception
        self.assertIn("liability_justification", exc.message)
        self.assertIn("L-2", exc.detail)

    def test_commit_refused_with_partial_justification(self):
        """Active Critical liabilities + partial justification => Refusal naming uncovered."""
        ps = self.root / "program-state"
        ps.mkdir(parents=True)
        (ps / "liability-tracker.md").write_text(
            "# Liability Tracker\n\n"
            "## L-2 IFIH1 contradictions\n\n"
            "**Severity**: critical\n"
            "**Status**: open\n\n"
            "First critical problem.\n\n"
            "## L-5 Sample degradation\n\n"
            "**Severity**: critical\n"
            "**Status**: open\n\n"
            "Second critical problem.\n",
            encoding="utf-8",
        )
        # Justification covers only L-2, not L-5.
        _make_proposed_wo(
            self.root,
            liability_justification={
                "L-2 IFIH1 contradictions": "This WO investigates L-2",
            },
        )
        with self.assertRaises(Refusal) as ctx:
            _perform_commit(self.root, "WO-001")
        exc = ctx.exception
        self.assertIn("L-5 Sample degradation", exc.message)
        self.assertNotIn("L-2 IFIH1 contradictions", exc.message)

    def test_commit_succeeds_when_all_critical_mitigated(self):
        """All Critical liabilities mitigated => commit proceeds without justification."""
        ps = self.root / "program-state"
        ps.mkdir(parents=True)
        (ps / "liability-tracker.md").write_text(
            "# Liability Tracker\n\n"
            "## L-2\n\n"
            "**Severity**: critical\n"
            "**Status**: mitigated\n\n"
            "Was critical.\n\n"
            "## L-3\n\n"
            "**Severity**: critical\n"
            "**Status**: accepted\n\n"
            "Risk accepted.\n",
            encoding="utf-8",
        )
        _make_proposed_wo(self.root)
        record, _snapshot_path, _record_path = _perform_commit(self.root, "WO-001")
        self.assertEqual(record["state"], "committed")


if __name__ == "__main__":
    unittest.main()
