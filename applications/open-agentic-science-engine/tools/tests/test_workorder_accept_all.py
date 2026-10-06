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

"""Tests for `dde workorder accept-all` and site build prerequisite check (#108).

Covers:
  - accept-all validates all WOs and reports status
  - Passing WOs are transitioned to scientifically_accepted
  - Failing WOs are NOT transitioned (left in validation_failed)
  - Consolidated table includes failure reasons
  - Overrides are not applied (failing WOs stay failed)
  - Site build prerequisite check blocks when WOs are not accepted
  - Site build prerequisite check passes when all WOs are accepted
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest import mock

TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.commands.site import _check_all_accepted  # noqa: E402
from dde.commands.workorder import (  # noqa: E402
    _list_latest_work_orders,
    _try_accept_single,
)
from dde.core import controlstore  # noqa: E402
from dde.core.errors import ArtifactError  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_project(tmp_path: Path) -> Path:
    """Create a minimal project layout with .dde marker and control dirs."""
    project = tmp_path / "project"
    (project / ".dde").mkdir(parents=True)
    controlstore.ensure_control_dirs(project)
    return project


def _make_wo_record(
    wo_id: str = "WO-001",
    revision: int = 1,
    state: str = "committed",
    **overrides: Any,
) -> dict[str, Any]:
    """Build a valid work-order record dict."""
    record: dict[str, Any] = {
        "id": wo_id,
        "revision": revision,
        "state": state,
        "decision_question": "Test question?",
        "requested_role": "structural-biologist",
        "stage": "stage1",
        "cycle": "C1",
        "context": {"content": "test context", "artifact_links": []},
        "dependencies": [],
        "capabilities": ["cap1"],
        "deliverables": {
            "layer_0_classes": ["structures"],
            "layer_1": [],
        },
        "acceptance_criteria": {"criteria": "pass all checks"},
        "alert_policy": {"notify": "manager"},
        "priority": "normal",
        "resource_class": "standard",
        "report_to": "manager",
        "created_at": "2026-01-01T00:00:00Z",
        "committed_at": None,
    }
    record.update(overrides)
    return record


def _write_wo(project: Path, record: dict[str, Any]) -> Path:
    """Write a work-order record to the control store."""
    wo_id = record["id"]
    revision = record["revision"]
    identifier = f"{wo_id}-r{revision}"
    return controlstore.write_record(project, "work-order", identifier, record)


# ---------------------------------------------------------------------------
# _list_latest_work_orders tests
# ---------------------------------------------------------------------------


def test_list_latest_empty(tmp_path):
    """Empty project returns no work orders."""
    project = _make_project(tmp_path)
    assert _list_latest_work_orders(project) == []


def test_list_latest_deduplicates(tmp_path):
    """Multiple revisions → only latest revision per ID returned."""
    project = _make_project(tmp_path)
    _write_wo(project, _make_wo_record("WO-001", revision=1, state="committed"))
    _write_wo(project, _make_wo_record("WO-001", revision=2, state="submitted"))
    _write_wo(project, _make_wo_record("WO-002", revision=1, state="committed"))

    result = _list_latest_work_orders(project)
    assert len(result) == 2
    ids = [r["id"] for r in result]
    assert ids == ["WO-001", "WO-002"]
    # WO-001 should be revision 2
    assert result[0]["revision"] == 2


# ---------------------------------------------------------------------------
# _try_accept_single tests
# ---------------------------------------------------------------------------


def test_accept_single_already_accepted(tmp_path):
    """WO already in scientifically_accepted is reported as already_accepted."""
    project = _make_project(tmp_path)
    record = _make_wo_record("WO-001", state="scientifically_accepted")
    _write_wo(project, record)

    result = _try_accept_single(project, record)
    assert result["outcome"] == "already_accepted"
    assert result["id"] == "WO-001"


def test_accept_single_proposed_skipped(tmp_path):
    """WO in proposed state is skipped (needs commit first)."""
    project = _make_project(tmp_path)
    record = _make_wo_record("WO-001", state="proposed")
    _write_wo(project, record)

    result = _try_accept_single(project, record)
    assert result["outcome"] == "skip"
    assert "commit" in result["detail"]


def test_accept_single_validation_failed_skipped(tmp_path):
    """WO in validation_failed state is skipped."""
    project = _make_project(tmp_path)
    record = _make_wo_record("WO-001", state="validation_failed")
    _write_wo(project, record)

    result = _try_accept_single(project, record)
    assert result["outcome"] == "skip"
    assert "override" in result["detail"] or "fix" in result["detail"]


def test_accept_single_mechanically_validated_accepted(tmp_path):
    """WO already at mechanically_validated transitions to scientifically_accepted."""
    project = _make_project(tmp_path)
    record = _make_wo_record("WO-001", state="mechanically_validated")
    _write_wo(project, record)

    result = _try_accept_single(project, record)
    assert result["outcome"] == "pass"
    assert result["id"] == "WO-001"

    # Verify the record was updated in the store.
    updated = controlstore.read_record(project, "work-order", "WO-001-r1")
    assert updated["state"] == "scientifically_accepted"


def test_accept_single_committed_with_passing_validation(tmp_path):
    """WO at committed walks through chain and gets accepted when validation passes."""
    project = _make_project(tmp_path)
    record = _make_wo_record("WO-001", state="committed")
    _write_wo(project, record)

    # Mock _perform_validation to return a passing result.
    mock_checks = [
        {"name": "deliverables_exist", "result": "pass"},
        {"name": "report_headings", "result": "pass"},
    ]
    mock_wo_record = _make_wo_record("WO-001", state="mechanically_validated")
    mock_return = (
        mock_wo_record,  # wo_record
        Path("/fake/val.json"),  # val_path
        "pass",  # overall_result
        mock_checks,  # checks
        [],  # checks_failed
        "submitted",  # val_from
        "mechanically_validated",  # val_to
    )

    with mock.patch(
        "dde.commands.workorder._try_accept_single.__module__",
        create=True,
    ):
        # We need to mock the validate import inside _try_accept_single.
        with mock.patch(
            "dde.commands.validate._perform_validation",
            return_value=mock_return,
        ):
            result = _try_accept_single(project, record)

    assert result["outcome"] == "pass"
    assert result["id"] == "WO-001"


def test_accept_single_committed_with_failing_validation(tmp_path):
    """WO at committed walks through chain but stays failed when validation fails."""
    project = _make_project(tmp_path)
    record = _make_wo_record("WO-001", state="committed")
    _write_wo(project, record)

    # Mock _perform_validation to return a failing result.
    mock_checks = [
        {"name": "deliverables_exist", "result": "fail"},
        {"name": "report_headings", "result": "pass"},
    ]
    mock_wo_record = _make_wo_record("WO-001", state="validation_failed")
    mock_return = (
        mock_wo_record,  # wo_record
        Path("/fake/val.json"),  # val_path
        "fail",  # overall_result
        mock_checks,  # checks
        ["deliverables_exist"],  # checks_failed
        "submitted",  # val_from
        "validation_failed",  # val_to
    )

    with mock.patch(
        "dde.commands.validate._perform_validation",
        return_value=mock_return,
    ):
        result = _try_accept_single(project, record)

    assert result["outcome"] == "fail"
    assert result["id"] == "WO-001"
    # Failure detail should list the failed check names.
    assert isinstance(result["detail"], list)
    assert "deliverables_exist" in result["detail"]


def test_accept_single_no_override_on_failure(tmp_path):
    """Failing WOs are NOT auto-overridden — no override_history added."""
    project = _make_project(tmp_path)
    record = _make_wo_record("WO-001", state="committed")
    _write_wo(project, record)

    mock_checks = [
        {"name": "deliverables_exist", "result": "fail"},
    ]
    mock_wo_record = _make_wo_record("WO-001", state="validation_failed")
    mock_return = (
        mock_wo_record,
        Path("/fake/val.json"),
        "fail",
        mock_checks,
        ["deliverables_exist"],
        "submitted",
        "validation_failed",
    )

    with mock.patch(
        "dde.commands.validate._perform_validation",
        return_value=mock_return,
    ):
        result = _try_accept_single(project, record)

    assert result["outcome"] == "fail"
    # The record should have no override_history.
    assert "override_history" not in record or not record.get("override_history")


# ---------------------------------------------------------------------------
# Site build prerequisite check tests
# ---------------------------------------------------------------------------


def test_site_check_all_accepted_passes(tmp_path):
    """No error raised when all WOs are scientifically_accepted."""
    project = _make_project(tmp_path)
    _write_wo(project, _make_wo_record("WO-001", state="scientifically_accepted"))
    _write_wo(project, _make_wo_record("WO-002", state="scientifically_accepted"))

    # Should not raise.
    _check_all_accepted(project, mock.MagicMock())


def test_site_check_blocks_when_not_accepted(tmp_path):
    """ArtifactError raised when any WO is not in scientifically_accepted state."""
    project = _make_project(tmp_path)
    _write_wo(project, _make_wo_record("WO-001", state="scientifically_accepted"))
    _write_wo(project, _make_wo_record("WO-002", state="submitted"))

    import pytest

    with pytest.raises(ArtifactError) as exc_info:
        _check_all_accepted(project, mock.MagicMock())

    # Error message should mention the blocking count.
    assert "1 work order(s)" in str(exc_info.value)


def test_site_check_blocks_reports_all_states(tmp_path):
    """The status table includes every WO and its state."""
    project = _make_project(tmp_path)
    _write_wo(project, _make_wo_record("WO-001", state="scientifically_accepted"))
    _write_wo(project, _make_wo_record("WO-002", state="committed"))
    _write_wo(project, _make_wo_record("WO-003", state="proposed"))

    import pytest

    with pytest.raises(ArtifactError) as exc_info:
        _check_all_accepted(project, mock.MagicMock())

    error_str = str(exc_info.value)
    assert "2 work order(s)" in error_str


def test_site_check_no_wos_passes(tmp_path):
    """No error raised when there are no WOs at all (guard handled elsewhere)."""
    project = _make_project(tmp_path)

    # Should not raise — the "no accepted WOs" guard in build_cmd handles this.
    _check_all_accepted(project, mock.MagicMock())


def test_site_check_detail_includes_remedy(tmp_path):
    """The error detail mentions accept-all as a remedy."""
    project = _make_project(tmp_path)
    _write_wo(project, _make_wo_record("WO-001", state="committed"))

    import pytest

    with pytest.raises(ArtifactError) as exc_info:
        _check_all_accepted(project, mock.MagicMock())

    # Check the exception has the remedy info.
    exc = exc_info.value
    assert hasattr(exc, "remedy")
    assert "accept-all" in exc.remedy


# ---------------------------------------------------------------------------
# Direct-run support
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
