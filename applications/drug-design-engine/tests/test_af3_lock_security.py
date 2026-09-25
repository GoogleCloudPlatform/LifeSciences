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

"""Regression tests for #182: AF3 lock file security hardening.

Covers:
  1. Default lock path is NOT in /tmp/ — uses ~/.cache/dde/ instead.
  2. Lock acquisition times out rather than blocking indefinitely.
  3. Symlink at the lock path causes the open to fail safely.
"""

from __future__ import annotations

import fcntl
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.commands.alphafold import (
    _acquire_lock_bounded,
    _resolve_lock_path,
)
from dde.core.errors import EndpointUnavailable

# ---------------------------------------------------------------------------
# 1. Lock file location
# ---------------------------------------------------------------------------


def test_default_lock_path_not_in_tmp():
    """The default lock path must NOT live under /tmp/."""
    with mock.patch.dict(os.environ, {}, clear=False):
        # Remove DDE_AF3_LOCK if set so the default path is used.
        env = os.environ.copy()
        env.pop("DDE_AF3_LOCK", None)
        with mock.patch.dict(os.environ, env, clear=True):
            path = _resolve_lock_path()
    assert not str(path).startswith("/tmp"), (
        f"Default lock path should not be in /tmp/, got {path}"
    )


def test_default_lock_path_under_user_cache():
    """The default lock path lives under ~/.cache/dde/."""
    with mock.patch.dict(os.environ, {}, clear=False):
        env = os.environ.copy()
        env.pop("DDE_AF3_LOCK", None)
        with mock.patch.dict(os.environ, env, clear=True):
            path = _resolve_lock_path()
    expected_parent = Path.home() / ".cache" / "dde"
    assert path.parent == expected_parent, (
        f"Expected parent {expected_parent}, got {path.parent}"
    )


def test_env_override_respected():
    """DDE_AF3_LOCK overrides the default lock path."""
    custom = "/some/custom/lock.file"
    with mock.patch.dict(os.environ, {"DDE_AF3_LOCK": custom}):
        path = _resolve_lock_path()
    assert str(path) == custom


# ---------------------------------------------------------------------------
# 2. Bounded lock acquisition
# ---------------------------------------------------------------------------


def test_lock_acquisition_times_out():
    """_acquire_lock_bounded raises EndpointUnavailable when the deadline
    expires while waiting for the lock."""
    with tempfile.TemporaryDirectory() as tmpdir:
        lock_file = os.path.join(tmpdir, "test.lock")

        # Open and hold an exclusive lock on the file in this process.
        holder_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(holder_fd, fcntl.LOCK_EX)

        try:
            # Open a second fd that will try to acquire the same lock.
            waiter_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                # Set deadline to 0.3s in the future — should time out.
                deadline = time.monotonic() + 0.3
                try:
                    _acquire_lock_bounded(waiter_fd, deadline)
                    assert False, (
                        "_acquire_lock_bounded should have raised EndpointUnavailable"
                    )
                except EndpointUnavailable:
                    pass  # expected
            finally:
                os.close(waiter_fd)
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            os.close(holder_fd)


def test_lock_acquisition_succeeds_when_free():
    """_acquire_lock_bounded acquires the lock immediately when it is free."""
    with tempfile.TemporaryDirectory() as tmpdir:
        lock_file = os.path.join(tmpdir, "test.lock")
        fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            deadline = time.monotonic() + 5.0
            _acquire_lock_bounded(fd, deadline)
            # If we get here, the lock was acquired — unlock it.
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# ---------------------------------------------------------------------------
# 3. Symlink protection
# ---------------------------------------------------------------------------


def test_symlink_at_lock_path_fails():
    """Opening the lock file with O_NOFOLLOW must fail when the path is a
    symlink, preventing symlink attacks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        real_file = os.path.join(tmpdir, "real.lock")
        symlink = os.path.join(tmpdir, "symlink.lock")

        # Create target and a symlink pointing to it.
        Path(real_file).touch()
        os.symlink(real_file, symlink)

        try:
            fd = os.open(symlink, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            assert False, (
                "os.open with O_NOFOLLOW should have raised OSError "
                "when the path is a symlink"
            )
        except OSError:
            pass  # expected — symlink rejected
