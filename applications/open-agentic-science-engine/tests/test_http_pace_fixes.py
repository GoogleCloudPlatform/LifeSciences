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

"""Regression tests for HTTP pacing security fixes.

Covers:
  - Issue #290: Cleartext credential exposure in pace filenames
  - Issue #293: TOCTOU race condition in _pace_disk symlink check
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

# Ensure the tools package is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from dde.core.http import _pace, _pace_disk

# ---------------------------------------------------------------------------
# Issue #290: Credential exposure in pace filenames
# ---------------------------------------------------------------------------


class TestCredentialExposureInPaceFilename:
    """Verify that URLs with embedded credentials produce pace filenames
    that contain only the hostname — no username, password, or '@'."""

    def test_url_with_credentials_uses_only_hostname(self, tmp_path):
        """A URL like http://user:secret@api.example.com/path must produce
        a pace filename based solely on 'api.example.com'."""
        with (
            mock.patch("dde.core.http._PACE_DIR", tmp_path),
            mock.patch("dde.core.http._PACE_TIER", "shared"),
        ):
            _pace("http://user:secret@api.example.com/path", qps=1.0)

        files = list(tmp_path.iterdir())
        assert len(files) == 1
        filename = files[0].name
        assert "user" not in filename
        assert "secret" not in filename
        assert "@" not in filename
        assert filename == "api.example.com"

    def test_url_with_credentials_and_port_uses_hostname(self, tmp_path):
        """Credentials AND a port must still resolve to just the hostname."""
        with (
            mock.patch("dde.core.http._PACE_DIR", tmp_path),
            mock.patch("dde.core.http._PACE_TIER", "shared"),
        ):
            _pace("https://admin:p4ss@db.internal:8443/query", qps=1.0)

        files = list(tmp_path.iterdir())
        assert len(files) == 1
        filename = files[0].name
        assert "admin" not in filename
        assert "p4ss" not in filename
        assert "@" not in filename
        # hostname only — no port, no credentials
        assert filename == "db.internal"

    def test_url_without_credentials_unchanged(self, tmp_path):
        """A plain URL without credentials should still work correctly."""
        with (
            mock.patch("dde.core.http._PACE_DIR", tmp_path),
            mock.patch("dde.core.http._PACE_TIER", "shared"),
        ):
            _pace("https://api.example.com/data", qps=1.0)

        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "api.example.com"


# ---------------------------------------------------------------------------
# Issue #293: TOCTOU race in _pace_disk
# ---------------------------------------------------------------------------


class TestTOCTOURaceInPaceDisk:
    """Verify that _pace_disk uses O_NOFOLLOW to atomically reject symlinks,
    preventing the TOCTOU race between is_safe_to_open and open()."""

    def test_pace_disk_uses_o_nofollow(self, tmp_path):
        """os.open must be called with O_NOFOLLOW in its flags."""
        with (
            mock.patch("dde.core.http._PACE_DIR", tmp_path),
            mock.patch("dde.core.http.os.open", wraps=os.open) as mock_os_open,
        ):
            _pace_disk("safe.example.com", 1.0)

        mock_os_open.assert_called_once()
        call_args = mock_os_open.call_args
        flags = call_args[0][1]  # second positional arg is flags
        assert flags & os.O_NOFOLLOW, f"O_NOFOLLOW not set in flags: {flags:#x}"
        assert flags & os.O_CREAT, f"O_CREAT not set in flags: {flags:#x}"
        assert flags & os.O_RDWR, f"O_RDWR not set in flags: {flags:#x}"

    def test_pace_disk_refuses_symlink(self, tmp_path):
        """When the pace file is a symlink, _pace_disk must not follow it.

        The is_safe_to_open pre-check returns False for symlinks, falling
        back to memory pacing. Even if that check were bypassed, os.open
        with O_NOFOLLOW would raise OSError on symlinks.
        """
        target = tmp_path / "real_file"
        target.write_text("0.0")
        symlink = tmp_path / "evil-link"
        symlink.symlink_to(target)

        with mock.patch("dde.core.http._PACE_DIR", tmp_path):
            # The is_safe_to_open check catches the symlink first and
            # falls back to memory pacing — no write to the target.
            original_content = target.read_text()
            _pace_disk("evil-link", 1.0)
            # The target file should be untouched (memory fallback was used).
            assert target.read_text() == original_content

    def test_os_open_with_o_nofollow_rejects_symlink(self, tmp_path):
        """Directly verify that os.open with O_NOFOLLOW raises on symlinks."""
        target = tmp_path / "target_file"
        target.write_text("data")
        symlink = tmp_path / "link"
        symlink.symlink_to(target)

        with pytest.raises(OSError):
            os.open(
                str(symlink),
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                0o644,
            )
