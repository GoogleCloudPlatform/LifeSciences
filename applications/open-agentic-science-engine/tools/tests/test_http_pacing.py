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

"""Regression tests for core/http.py pacing functions (#59, #68).

Covers cross-invocation disk pacing, backward clock-jump capping,
corrupted pace file recovery, OSError fallback to in-memory pacing,
in-memory pacing behaviour, and pacing tier resolution.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Patch optional dependencies before importing the module under test so
# that the import succeeds even when ``requests`` or ``click`` are not
# installed in the test environment.
with patch.dict("sys.modules", {"requests": MagicMock(), "click": MagicMock()}):
    from dde.core import http
    from dde.core.errors import Refusal
    from dde.core.http import (
        _pace,
        _pace_disk,
        _pace_memory,
        _resolve_pace_dir,
    )


class TestPaceDisk(unittest.TestCase):
    """Tests for ``_pace_disk`` — the flock-based cross-invocation pacer."""

    def setUp(self):
        self._orig_pace_dir = http._PACE_DIR
        self._tmpdir = tempfile.mkdtemp(prefix="dde_pace_test_")
        http._PACE_DIR = Path(self._tmpdir)

    def tearDown(self):
        http._PACE_DIR = self._orig_pace_dir
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # 1. Basic interval enforcement
    # ------------------------------------------------------------------
    def test_pace_disk_enforces_interval(self):
        """When a pace file records a recent timestamp, ``_pace_disk``
        should sleep for the remainder of the interval."""
        host = "api.example.com"
        interval = 1.0  # 1 QPS

        # First call: no pace file yet → previous=0.0, now=1000.0
        # wait = 1.0 - (1000.0 - 0.0) < 0 → no sleep
        # Second call: pace file contains ~1000.0, now=1000.3
        # wait = 1.0 - (1000.3 - 1000.0) = 0.7 → sleep(0.7)
        time_values = iter([1000.0, 1000.0, 1000.3, 1000.3])

        with (
            patch("time.time", side_effect=lambda: next(time_values)),
            patch("time.sleep") as mock_sleep,
        ):
            _pace_disk(host, interval)
            mock_sleep.assert_not_called()

            _pace_disk(host, interval)
            mock_sleep.assert_called_once()
            slept = mock_sleep.call_args[0][0]
            self.assertAlmostEqual(slept, 0.7, places=5)

    # ------------------------------------------------------------------
    # 2. Backward clock jump is capped
    # ------------------------------------------------------------------
    def test_pace_disk_caps_backward_clock_jump(self):
        """If the wall clock jumps backward, the sleep must be capped at
        ``interval``, not ``interval + |jump|``."""
        host = "api.example.com"
        interval = 1.0

        # First call writes timestamp 1000.0.
        # Second call reads 1000.0 but now=500.0 (clock jumped back 500s).
        # Uncapped wait = 1.0 - (500.0 - 1000.0) = 501.0
        # Capped wait  = min(501.0, 1.0) = 1.0
        time_values = iter([1000.0, 1000.0, 500.0, 500.0])

        with (
            patch("time.time", side_effect=lambda: next(time_values)),
            patch("time.sleep") as mock_sleep,
        ):
            _pace_disk(host, interval)

            _pace_disk(host, interval)
            mock_sleep.assert_called_once()
            slept = mock_sleep.call_args[0][0]
            self.assertAlmostEqual(slept, interval, places=5)

    # ------------------------------------------------------------------
    # 3. Corrupted pace file falls back to previous=0.0
    # ------------------------------------------------------------------
    def test_pace_disk_corrupted_file(self):
        """A pace file with non-numeric content should be treated as 0.0
        rather than raising ``ValueError``."""
        host = "api.example.com"
        interval = 1.0

        # Pre-create a corrupted pace file.
        pace_file = http._PACE_DIR / host
        pace_file.parent.mkdir(parents=True, exist_ok=True)
        pace_file.write_text("NOT_A_NUMBER\n")

        # now=2000.0, previous falls back to 0.0
        # wait = 1.0 - (2000.0 - 0.0) < 0 → no sleep
        with patch("time.time", return_value=2000.0), patch("time.sleep") as mock_sleep:
            _pace_disk(host, interval)  # Should not raise
            mock_sleep.assert_not_called()


class TestPaceFallback(unittest.TestCase):
    """Tests for ``_pace`` dispatch and ``_pace_memory`` fallback."""

    def setUp(self):
        self._orig_pace_dir = http._PACE_DIR
        self._orig_last_call = http._last_call.copy()
        http._last_call.clear()

    def tearDown(self):
        http._PACE_DIR = self._orig_pace_dir
        http._last_call.clear()
        http._last_call.update(self._orig_last_call)

    # ------------------------------------------------------------------
    # 4. OSError in _pace_disk triggers _pace_memory fallback
    # ------------------------------------------------------------------
    def test_pace_fallback_on_oserror(self):
        """When ``_pace_disk`` raises ``OSError``, ``_pace`` should fall
        back to ``_pace_memory``."""
        # Point _PACE_DIR at a non-existent, non-creatable path.
        http._PACE_DIR = Path("/nonexistent/root/dde_pace_test")

        url = "https://api.example.com/resource"

        with patch("time.sleep"), patch("time.monotonic", return_value=9999.0):
            _pace(url, qps=1.0)

        # _pace_memory should have recorded the host.
        self.assertIn("api.example.com", http._last_call)

    # ------------------------------------------------------------------
    # 5. In-memory pacing preserves original behaviour
    # ------------------------------------------------------------------
    def test_pace_memory_preserves_original_behavior(self):
        """``_pace_memory`` should use ``time.monotonic`` and enforce
        the interval between calls to the same host."""
        host = "api.example.com"
        interval = 1.0

        mono_values = iter([100.0, 100.0, 100.4, 100.4])

        with (
            patch("time.monotonic", side_effect=lambda: next(mono_values)),
            patch("time.sleep") as mock_sleep,
        ):
            # First call — no previous entry, no sleep.
            _pace_memory(host, interval)
            mock_sleep.assert_not_called()

            # Second call — 0.4s elapsed, should sleep 0.6s.
            _pace_memory(host, interval)
            mock_sleep.assert_called_once()
            slept = mock_sleep.call_args[0][0]
            self.assertAlmostEqual(slept, 0.6, places=5)

    # ------------------------------------------------------------------
    # 6. qps=0 disables pacing entirely
    # ------------------------------------------------------------------
    def test_pace_zero_qps_skips(self):
        """``_pace`` with qps<=0 should return immediately."""
        with (
            patch.object(http, "_pace_disk") as mock_disk,
            patch.object(http, "_pace_memory") as mock_mem,
        ):
            _pace("https://example.com", qps=0)
            mock_disk.assert_not_called()
            mock_mem.assert_not_called()


class TestResolvePaceDir(unittest.TestCase):
    """Tests for ``_resolve_pace_dir`` — the three-tier fallback (#68)."""

    # ------------------------------------------------------------------
    # 7. DDE_PACE_DIR env var → shared tier
    # ------------------------------------------------------------------
    def test_resolve_pace_dir_env_var(self):
        """When DDE_PACE_DIR is set to a valid path, resolve to shared tier."""
        tmpdir = tempfile.mkdtemp(prefix="dde_pace_env_")
        try:
            with patch.dict(os.environ, {"DDE_PACE_DIR": tmpdir}):
                path, tier = _resolve_pace_dir()
            self.assertEqual(tier, "shared")
            self.assertEqual(path, Path(tmpdir))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # 8. No env var, no shared volume → local tier
    # ------------------------------------------------------------------
    def test_resolve_pace_dir_fallback_to_local(self):
        """With no env var and no shared volume, resolve to local tier."""
        env_clean = os.environ.copy()
        env_clean.pop("DDE_PACE_DIR", None)
        env_clean.pop("DDE_PACE_REQUIRE_SHARED", None)

        original_mkdir = Path.mkdir

        def _selective_mkdir(self_path, *args, **kwargs):
            # Fail only for the shared volume path
            if str(self_path).startswith("/scion-volumes"):
                raise OSError("no mount")
            return original_mkdir(self_path, *args, **kwargs)

        with patch.dict(os.environ, env_clean, clear=True):
            with patch.object(Path, "mkdir", _selective_mkdir):
                path, tier = _resolve_pace_dir()
        self.assertEqual(tier, "local")
        self.assertEqual(path, Path.home() / ".cache" / "dde" / "pace")

    # ------------------------------------------------------------------
    # 9. _PACE_TIER is importable and valid
    # ------------------------------------------------------------------
    def test_pace_tier_exposed(self):
        """``_PACE_TIER`` is importable and is a valid tier string."""
        from dde.core.http import _PACE_TIER

        self.assertIn(_PACE_TIER, {"shared", "local", "memory"})

    # ------------------------------------------------------------------
    # 10. DDE_PACE_REQUIRE_SHARED — _resolve_pace_dir does NOT raise
    # ------------------------------------------------------------------
    def test_resolve_pace_dir_require_shared_does_not_raise(self):
        """When DDE_PACE_REQUIRE_SHARED=1 and no shared path is available,
        _resolve_pace_dir falls through (local/memory) without raising.
        The strict-mode check is deferred to _pace()."""
        env_clean = os.environ.copy()
        env_clean.pop("DDE_PACE_DIR", None)
        env_clean["DDE_PACE_REQUIRE_SHARED"] = "1"

        def _always_fail(self_path, *args, **kwargs):
            raise OSError("no mount")

        with patch.dict(os.environ, env_clean, clear=True):
            with patch.object(Path, "mkdir", _always_fail):
                # Should NOT raise — falls through to memory tier.
                _path, tier = _resolve_pace_dir()
        self.assertEqual(tier, "memory")

    # ------------------------------------------------------------------
    # 11. Memory tier skips disk pacing
    # ------------------------------------------------------------------
    def test_pace_memory_tier_skips_disk(self):
        """When _PACE_TIER is 'memory', _pace goes straight to _pace_memory."""
        orig_tier = http._PACE_TIER
        try:
            http._PACE_TIER = "memory"
            with (
                patch.object(http, "_pace_disk") as mock_disk,
                patch.object(http, "_pace_memory") as mock_mem,
            ):
                _pace("https://example.com/foo", qps=1.0)
                mock_disk.assert_not_called()
                mock_mem.assert_called_once()
        finally:
            http._PACE_TIER = orig_tier

    # ------------------------------------------------------------------
    # 12. Deferred strict-mode check: import succeeds, _pace raises
    # ------------------------------------------------------------------
    def test_deferred_strict_mode_raises_in_pace(self):
        """With DDE_PACE_REQUIRE_SHARED=1 and a non-shared tier,
        importing http.py must succeed but calling _pace() must raise
        Refusal.  This ensures ``dde doctor`` can still run."""
        orig_tier = http._PACE_TIER
        orig_required = http._PACE_SHARED_REQUIRED
        try:
            http._PACE_TIER = "local"
            http._PACE_SHARED_REQUIRED = True
            with self.assertRaises(Refusal):
                _pace("https://example.com/resource", qps=1.0)
        finally:
            http._PACE_TIER = orig_tier
            http._PACE_SHARED_REQUIRED = orig_required


if __name__ == "__main__":
    unittest.main()
