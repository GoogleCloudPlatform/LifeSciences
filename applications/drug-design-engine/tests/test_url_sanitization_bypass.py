#!/usr/bin/env python3
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

"""Tests for URL sanitization bypass vectors (#275).

Covers both urlsplit-based scheme detection (v1) and WHATWG C0 control
character normalization (v2), ensuring no bypass is possible via:
- Single-slash schemes (``https:/attacker.com``)
- Backslash substitution (``https:\\\\evil.com``)
- Non-hierarchical schemes (``javascript:alert(1)``)
- Protocol-relative URLs (``//evil.com``)
- Leading C0 control characters (U+0000-U+001F)
- Embedded tab, newline, carriage return

Run with:
    PYTHONPATH=tools python3 tests/test_url_sanitization_bypass.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap — add tools/ to sys.path so dde is importable
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dde.commands.site import (
    _is_external_url,
    _sanitize_external_urls,
    _whatwg_normalize_url,
)

# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------

_RESULTS: list[tuple[str, bool, str]] = []


def _run(name: str, fn):
    try:
        fn()
        _RESULTS.append((name, True, ""))
    except Exception:
        _RESULTS.append((name, False, traceback.format_exc()))


# ---------------------------------------------------------------------------
# _whatwg_normalize_url unit tests
# ---------------------------------------------------------------------------


def test_whatwg_strips_leading_c0():
    assert _whatwg_normalize_url("\x01\x02hello") == "hello"


def test_whatwg_strips_embedded_tab():
    assert _whatwg_normalize_url("java\tscript") == "javascript"


def test_whatwg_strips_combined_c0_and_embedded():
    assert _whatwg_normalize_url("\x00\x01ja\nva\rscri\tpt") == "javascript"


def test_whatwg_unchanged_normal():
    assert _whatwg_normalize_url("normal_url") == "normal_url"


def test_whatwg_empty_string():
    assert _whatwg_normalize_url("") == ""


def test_whatwg_strips_leading_space():
    assert _whatwg_normalize_url("   hello") == "hello"


def test_whatwg_strips_leading_null():
    assert _whatwg_normalize_url("\x00hello") == "hello"


# ---------------------------------------------------------------------------
# _is_external_url — standard scheme detection (urlsplit-based)
# ---------------------------------------------------------------------------


def test_external_http():
    assert _is_external_url("http://example.com") is True


def test_external_https():
    assert _is_external_url("https://example.com") is True


def test_external_protocol_relative():
    assert _is_external_url("//cdn.example.com/img.png") is True


def test_internal_relative():
    assert _is_external_url("images/logo.png") is False


def test_internal_absolute():
    assert _is_external_url("/pages/about.html") is False


def test_internal_anchor():
    assert _is_external_url("#section-1") is False


def test_internal_empty():
    assert _is_external_url("") is False


# ---------------------------------------------------------------------------
# _is_external_url — single-slash bypass (v1 urlsplit fix)
# ---------------------------------------------------------------------------


def test_single_slash_https():
    """https:/attacker.com — urlsplit correctly identifies the scheme."""
    assert _is_external_url("https:/attacker.com") is True


def test_single_slash_http():
    assert _is_external_url("http:/attacker.com") is True


def test_single_slash_javascript():
    assert _is_external_url("javascript:alert(1)") is True


def test_single_slash_data():
    assert _is_external_url("data:text/html,<h1>hi</h1>") is True


def test_single_slash_vbscript():
    assert _is_external_url("vbscript:msgbox") is True


# ---------------------------------------------------------------------------
# _is_external_url — backslash bypass
# ---------------------------------------------------------------------------


def test_backslash_double():
    assert _is_external_url("https:\\\\evil.com") is True


def test_backslash_single():
    assert _is_external_url("https:\\evil.com") is True


def test_backslash_protocol_relative():
    assert _is_external_url("\\\\evil.com") is True


# ---------------------------------------------------------------------------
# _is_external_url — whitespace / casing
# ---------------------------------------------------------------------------


def test_leading_spaces():
    assert _is_external_url("   https://evil.com") is True


def test_trailing_spaces():
    assert _is_external_url("https://evil.com   ") is True


# ---------------------------------------------------------------------------
# _is_external_url — C0 control character bypasses (WHATWG normalization)
# ---------------------------------------------------------------------------


def test_c0_leading_x01_javascript():
    assert _is_external_url("\x01javascript:alert(1)") is True


def test_c0_leading_x00_x01_https():
    assert _is_external_url("\x00\x01https://evil.com") is True


def test_embedded_tab_javascript():
    assert _is_external_url("java\tscript:alert(1)") is True


def test_embedded_newline_javascript():
    assert _is_external_url("java\nscript:alert(1)") is True


def test_embedded_cr_javascript():
    assert _is_external_url("java\rscript:alert(1)") is True


def test_c0_combined_leading_and_embedded():
    assert _is_external_url("\x01java\tscript:alert(1)") is True


# ---------------------------------------------------------------------------
# _sanitize_external_urls — end-to-end: standard bypass vectors
# ---------------------------------------------------------------------------


def test_e2e_external_img_blocked():
    html = '<img src="https://evil.com/logo.png">'
    out, warnings = _sanitize_external_urls(html)
    assert "blocked-image" in out
    assert len(warnings) == 1


def test_e2e_internal_img_allowed():
    html = '<img src="images/logo.png">'
    out, warnings = _sanitize_external_urls(html)
    assert out == html
    assert len(warnings) == 0


def test_e2e_data_uri_img_blocked():
    html = '<img src="data:image/png;base64,abc">'
    out, _warnings = _sanitize_external_urls(html)
    assert "blocked-image" in out


def test_e2e_javascript_link_neutralized():
    html = '<a href="javascript:alert(1)">click</a>'
    out, _ = _sanitize_external_urls(html)
    assert 'href="#"' in out
    assert "javascript:" not in out.split('href="')[1].split('"')[0]


def test_e2e_external_link_safety_attrs():
    html = '<a href="https://example.com">link</a>'
    out, _ = _sanitize_external_urls(html)
    assert "nofollow" in out
    assert "noopener" in out
    assert 'target="_blank"' in out


def test_e2e_internal_link_unchanged():
    html = '<a href="pages/about.html">About</a>'
    out, warnings = _sanitize_external_urls(html)
    assert out == html
    assert len(warnings) == 0


def test_e2e_single_slash_img_blocked():
    html = '<img src="https:/evil.com/logo.png">'
    out, _warnings = _sanitize_external_urls(html)
    assert "blocked-image" in out


def test_e2e_single_slash_link_safety():
    html = '<a href="https:/evil.com">click</a>'
    out, _ = _sanitize_external_urls(html)
    assert "nofollow" in out


def test_e2e_backslash_img_blocked():
    html = '<img src="https:\\\\evil.com\\logo.png">'
    out, _warnings = _sanitize_external_urls(html)
    assert "blocked-image" in out


def test_e2e_vbscript_neutralized():
    html = '<a href="vbscript:msgbox">click</a>'
    out, _ = _sanitize_external_urls(html)
    assert 'href="#"' in out


def test_e2e_data_link_neutralized():
    html = '<a href="data:text/html,hello">click</a>'
    out, _ = _sanitize_external_urls(html)
    assert 'href="#"' in out


# ---------------------------------------------------------------------------
# _sanitize_external_urls — end-to-end: C0 / embedded char bypass vectors
# ---------------------------------------------------------------------------


def test_e2e_c0_javascript_link_neutralized():
    """<a href="\x01javascript:alert(1)"> must be neutralized to href="#"."""
    html = '<a href="\x01javascript:alert(1)">click</a>'
    out, _ = _sanitize_external_urls(html)
    assert 'href="#"' in out


def test_e2e_embedded_tab_javascript_link_neutralized():
    """<a href="java\\tscript:alert(1)"> must be neutralized to href="#"."""
    html = '<a href="java\tscript:alert(1)">click</a>'
    out, _ = _sanitize_external_urls(html)
    assert 'href="#"' in out


def test_e2e_vt_javascript_link_neutralized():
    """<a href="\x0bjavascript:alert(1)"> must be neutralized to href="#"."""
    html = '<a href="\x0bjavascript:alert(1)">click</a>'
    out, _ = _sanitize_external_urls(html)
    assert 'href="#"' in out


def test_e2e_c0_data_img_blocked():
    """<img src="\x01data:image/png;base64,abc"> must be blocked."""
    html = '<img src="\x01data:image/png;base64,abc">'
    out, _warnings = _sanitize_external_urls(html)
    assert "blocked-image" in out


def test_e2e_embedded_tab_data_img_blocked():
    """<img src="da\\tta:image/png;base64,abc"> must be blocked."""
    html = '<img src="da\tta:image/png;base64,abc">'
    out, _warnings = _sanitize_external_urls(html)
    assert "blocked-image" in out


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_href_unchanged():
    html = '<a href="">text</a>'
    out, warnings = _sanitize_external_urls(html)
    assert out == html
    assert len(warnings) == 0


def test_anchor_href_unchanged():
    html = '<a href="#top">text</a>'
    out, warnings = _sanitize_external_urls(html)
    assert out == html
    assert len(warnings) == 0


def test_no_href_a_tag_unchanged():
    html = '<a name="section">text</a>'
    out, warnings = _sanitize_external_urls(html)
    assert out == html
    assert len(warnings) == 0


def test_no_src_img_unchanged():
    html = '<img alt="placeholder">'
    out, warnings = _sanitize_external_urls(html)
    assert out == html
    assert len(warnings) == 0


def test_protocol_relative_img_blocked():
    html = '<img src="//evil.com/img.png">'
    out, _warnings = _sanitize_external_urls(html)
    assert "blocked-image" in out


def test_ftp_scheme_detected():
    assert _is_external_url("ftp://files.example.com/data.zip") is True


def test_mailto_scheme_detected():
    assert _is_external_url("mailto:user@example.com") is True


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

_ALL_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for fn in _ALL_TESTS:
        _run(fn.__name__, fn)

    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    failed = sum(1 for _, ok, _ in _RESULTS if not ok)
    total = len(_RESULTS)

    print(f"\n{'=' * 60}")
    print(f"URL sanitization bypass tests: {passed}/{total} passed")
    if failed:
        print(f"\nFAILED ({failed}):")
        for name, ok, tb in _RESULTS:
            if not ok:
                print(f"\n  ✗ {name}")
                for line in tb.strip().split("\n"):
                    print(f"    {line}")
        print()
        sys.exit(1)
    else:
        print("All tests passed ✓")
        sys.exit(0)
