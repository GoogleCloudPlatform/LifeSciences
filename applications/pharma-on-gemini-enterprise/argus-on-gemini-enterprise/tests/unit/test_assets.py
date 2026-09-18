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

"""Unit tests for secure asset storage in app.tools.assets."""

import os
import stat

import pytest

from app.tools import assets


def test_asset_dir_is_unique_and_restricted():
    """Verify _ASSET_DIR is created via mkdtemp with restricted 0o700 permissions."""
    assert os.path.isdir(assets._ASSET_DIR)
    basename = os.path.basename(assets._ASSET_DIR)
    assert basename.startswith("argus_assets_")
    assert basename != "argus_assets"
    mode = stat.S_IMODE(os.stat(assets._ASSET_DIR).st_mode)
    assert mode == 0o700


def test_save_asset_prevents_symlink_following(tmp_path):
    """Verify save_asset uses O_NOFOLLOW and refuses to overwrite symlink targets."""
    victim_file = tmp_path / "sensitive_target.txt"
    victim_file.write_text("original secret content", encoding="utf-8")

    asset_id = "symlink_test_asset"
    symlink_path = os.path.join(assets._ASSET_DIR, f"{asset_id}.png")
    if os.path.lexists(symlink_path):
        os.unlink(symlink_path)
    os.symlink(victim_file, symlink_path)

    try:
        with pytest.raises(OSError):
            assets.save_asset(asset_id, b"malicious png payload")
        # Victim file must remain completely untouched
        assert victim_file.read_text(encoding="utf-8") == "original secret content"
    finally:
        if os.path.lexists(symlink_path):
            os.unlink(symlink_path)


def test_save_asset_file_permissions_and_roundtrip():
    """Verify saved assets have 0o600 permissions and resolve properly."""
    token = assets.save_asset("chart_runway_test", b"\x89PNG\r\n\x1a\n")
    assert token == "asset://chart_runway_test"

    resolved = assets.asset_path("chart_runway_test")
    assert resolved is not None
    assert os.path.isfile(resolved)
    mode = stat.S_IMODE(os.stat(resolved).st_mode)
    assert mode == 0o600

    md = "Here is ![Runway](asset://chart_runway_test) and ![Unknown](asset://missing)"
    rendered = assets.resolve_tokens_to_paths(md)
    assert resolved in rendered
    assert "asset://missing" in rendered


def test_asset_path_sanitization_and_containment(tmp_path):
    """Verify asset_path sanitizes traversal attempts and blocks symlink escapes."""
    outside_file = tmp_path / "outside.png"
    outside_file.write_bytes(b"outside")

    # Traversal attempt is sanitized to safe characters inside _ASSET_DIR
    assert assets.asset_path("../../etc/passwd") is None

    # Symlink inside _ASSET_DIR pointing outside must be rejected by realpath containment
    escape_id = "symlink_escape_asset"
    symlink_path = os.path.join(assets._ASSET_DIR, f"{escape_id}.png")
    if os.path.lexists(symlink_path):
        os.unlink(symlink_path)
    os.symlink(outside_file, symlink_path)

    try:
        assert assets.asset_path(escape_id) is None
    finally:
        if os.path.lexists(symlink_path):
            os.unlink(symlink_path)
