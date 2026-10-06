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

"""Tests for source_commit() in envstamp.py (#45).

Covers:
  - Happy path: commit reachable on a non-main remote ref
  - Not reachable: commit not an ancestor of any remote ref
  - No remote refs: for-each-ref returns empty (on_origin stays None)
  - No git: _git returns None for everything (all fields None)
  - Subprocess error in ref loop: OSError/SubprocessError handled gracefully
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.core.envstamp import PROVISIONING_INPUTS, source_commit  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_git_side_effect(responses: dict[tuple[str, ...], str | None]):
    """Build a _git() mock side-effect from a dict mapping arg tuples to results.

    Keys are the *args passed to _git (not including cwd).  A value of
    None makes _git return None (simulating failure); a string value is
    the successful stdout.
    """

    def side_effect(*args, cwd):
        return responses.get(args)

    return side_effect


# ---------------------------------------------------------------------------
# Tests for source_commit()
# ---------------------------------------------------------------------------


def test_happy_path_reachable_on_non_main_ref():
    """Commit reachable on origin/DDE — on_origin=True, remote_ref='origin/DDE'."""
    git_responses = {
        ("rev-parse", "HEAD"): "abc123",
        ("status", "--porcelain", "--", "."): "",
        ("status", "--porcelain", "--", *PROVISIONING_INPUTS): "",
        (
            "for-each-ref",
            "--format=%(refname)",
            "refs/remotes/origin/",
        ): "refs/remotes/origin/main\nrefs/remotes/origin/DDE\n",
    }

    # merge-base --is-ancestor: fail for origin/main (rc=1), succeed for origin/DDE (rc=0)
    def run_side_effect(cmd, **kwargs):
        mock_result = MagicMock()
        if cmd[1:] == ["merge-base", "--is-ancestor", "abc123", "origin/main"]:
            mock_result.returncode = 1
        elif cmd[1:] == ["merge-base", "--is-ancestor", "abc123", "origin/DDE"]:
            mock_result.returncode = 0
        else:
            mock_result.returncode = 1
        return mock_result

    with (
        patch(
            "dde.core.envstamp._git", side_effect=_make_git_side_effect(git_responses)
        ),
        patch("dde.core.envstamp.subprocess.run", side_effect=run_side_effect),
    ):
        result = source_commit(tree=Path("/fake/tree"))

    assert result["commit"] == "abc123"
    assert result["on_origin"] is True
    assert result["remote_ref"] == "origin/DDE"
    assert result["dirty"] is False


def test_not_reachable_on_any_ref():
    """Commit not an ancestor of any remote ref — on_origin=False."""
    git_responses = {
        ("rev-parse", "HEAD"): "deadbeef",
        ("status", "--porcelain", "--", "."): "",
        ("status", "--porcelain", "--", *PROVISIONING_INPUTS): "",
        (
            "for-each-ref",
            "--format=%(refname)",
            "refs/remotes/origin/",
        ): "refs/remotes/origin/main\nrefs/remotes/origin/DDE\n",
    }

    def run_side_effect(cmd, **kwargs):
        mock_result = MagicMock()
        mock_result.returncode = 1  # not ancestor for any ref
        return mock_result

    with (
        patch(
            "dde.core.envstamp._git", side_effect=_make_git_side_effect(git_responses)
        ),
        patch("dde.core.envstamp.subprocess.run", side_effect=run_side_effect),
    ):
        result = source_commit(tree=Path("/fake/tree"))

    assert result["commit"] == "deadbeef"
    assert result["on_origin"] is False
    assert result["remote_ref"] is None


def test_no_remote_refs_keeps_on_origin_none():
    """for-each-ref returns empty string — on_origin stays None (O1 fix)."""
    git_responses = {
        ("rev-parse", "HEAD"): "abc123",
        ("status", "--porcelain", "--", "."): "",
        ("status", "--porcelain", "--", *PROVISIONING_INPUTS): "",
        ("for-each-ref", "--format=%(refname)", "refs/remotes/origin/"): "",
    }

    with patch(
        "dde.core.envstamp._git", side_effect=_make_git_side_effect(git_responses)
    ):
        result = source_commit(tree=Path("/fake/tree"))

    assert result["commit"] == "abc123"
    assert result["on_origin"] is None
    assert result["remote_ref"] is None


def test_no_git_all_none():
    """_git returns None for everything — all fields None."""

    def git_returns_none(*args, cwd):
        return None

    with patch("dde.core.envstamp._git", side_effect=git_returns_none):
        result = source_commit(tree=Path("/fake/tree"))

    assert result["commit"] is None
    assert result["dirty"] is None
    assert result["dirty_inputs"] is None
    assert result["on_origin"] is None
    assert result["remote_ref"] is None


def test_subprocess_error_in_ref_loop_skips_ref():
    """OSError/SubprocessError in merge-base loop skips the failing ref."""
    git_responses = {
        ("rev-parse", "HEAD"): "abc123",
        ("status", "--porcelain", "--", "."): "",
        ("status", "--porcelain", "--", *PROVISIONING_INPUTS): "",
        (
            "for-each-ref",
            "--format=%(refname)",
            "refs/remotes/origin/",
        ): "refs/remotes/origin/broken\nrefs/remotes/origin/good\n",
    }

    call_count = 0

    def run_side_effect(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        if any("broken" in arg for arg in cmd):
            raise OSError("git not found")
        mock_result = MagicMock()
        mock_result.returncode = 0  # "good" ref succeeds
        return mock_result

    with (
        patch(
            "dde.core.envstamp._git", side_effect=_make_git_side_effect(git_responses)
        ),
        patch("dde.core.envstamp.subprocess.run", side_effect=run_side_effect),
    ):
        result = source_commit(tree=Path("/fake/tree"))

    assert result["on_origin"] is True
    assert result["remote_ref"] == "origin/good"
    assert call_count == 2  # both refs attempted


def test_timeout_in_ref_loop_skips_ref():
    """subprocess.TimeoutExpired in merge-base loop skips the failing ref."""
    git_responses = {
        ("rev-parse", "HEAD"): "abc123",
        ("status", "--porcelain", "--", "."): "",
        ("status", "--porcelain", "--", *PROVISIONING_INPUTS): "",
        (
            "for-each-ref",
            "--format=%(refname)",
            "refs/remotes/origin/",
        ): "refs/remotes/origin/slow\nrefs/remotes/origin/fast\n",
    }

    def run_side_effect(cmd, **kwargs):
        if any("slow" in arg for arg in cmd):
            raise subprocess.TimeoutExpired(cmd, 10)
        mock_result = MagicMock()
        mock_result.returncode = 0
        return mock_result

    with (
        patch(
            "dde.core.envstamp._git", side_effect=_make_git_side_effect(git_responses)
        ),
        patch("dde.core.envstamp.subprocess.run", side_effect=run_side_effect),
    ):
        result = source_commit(tree=Path("/fake/tree"))

    assert result["on_origin"] is True
    assert result["remote_ref"] == "origin/fast"


def test_for_each_ref_failure_keeps_on_origin_none():
    """When for-each-ref itself fails (_git returns None), on_origin stays None."""
    git_responses = {
        ("rev-parse", "HEAD"): "abc123",
        ("status", "--porcelain", "--", "."): "",
        ("status", "--porcelain", "--", *PROVISIONING_INPUTS): "",
        ("for-each-ref", "--format=%(refname)", "refs/remotes/origin/"): None,
    }

    with patch(
        "dde.core.envstamp._git", side_effect=_make_git_side_effect(git_responses)
    ):
        result = source_commit(tree=Path("/fake/tree"))

    assert result["commit"] == "abc123"
    assert result["on_origin"] is None
    assert result["remote_ref"] is None


# ---------------------------------------------------------------------------
# Direct-run support
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
