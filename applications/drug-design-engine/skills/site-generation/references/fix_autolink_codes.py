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

"""
Post-build fix: auto-link structured identifier codes.

Scans built HTML files for structured identifiers (e.g. WO-001, DEC-005,
LIA-002, OQ-007) and wraps bare text references in <a> tags pointing to
the page where that code is defined.

Copy this file into your project's site-tools/ directory (or alongside
postbuild.py) and register it in the FIXES list of your orchestrator:

    from fix_autolink_codes import fix_autolink_codes

    FIXES = [
        ...
        ("fix_autolink_codes", fix_autolink_codes),
    ]

Or run standalone:

    python3 fix_autolink_codes.py _site/

**Idempotent:** uses a detection guard — codes already wrapped in <a> tags
are skipped.  Safe to re-run after every build.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration — customize these for your program's identifier schemes
# ---------------------------------------------------------------------------

# Regex patterns for structured identifiers.  Each entry maps a human-readable
# category name to a compiled regex that matches one code.  The regex MUST
# contain exactly one capture group that yields the full code string
# (e.g. "WO-001").
#
# Add or remove entries to match your program:
#   "EXP":  re.compile(r"\b(EXP-\d{3})\b"),
#   "HYP":  re.compile(r"\b(HYP-\d{3})\b"),
#   "GATE": re.compile(r"\b(GATE-\d{3})\b"),

CODE_PATTERNS: dict[str, re.Pattern[str]] = {
    "WO": re.compile(r"\b(WO-\d{3})\b"),
    "DEC": re.compile(r"\b(DEC-\d{3})\b"),
    "LIA": re.compile(r"\b(LIA-\d{3})\b"),
    "OQ": re.compile(r"\b(OQ-\d{3})\b"),
}

# Combined pattern matching any configured code (built automatically).
_ALL_CODES_RE: re.Pattern[str] | None = None


def _all_codes_pattern() -> re.Pattern[str]:
    """Lazily build a combined regex from CODE_PATTERNS."""
    global _ALL_CODES_RE
    if _ALL_CODES_RE is None:
        alternatives = "|".join(p.pattern.strip(r"\b") for p in CODE_PATTERNS.values())
        _ALL_CODES_RE = re.compile(rf"\b({alternatives})\b")
    return _ALL_CODES_RE


# ---------------------------------------------------------------------------
# Page-mapping: discover code -> URL from built site filenames
# ---------------------------------------------------------------------------


def discover_code_urls(site_dir: Path) -> dict[str, str]:
    """Scan built HTML filenames to build a code -> relative-URL mapping.

    Filename conventions recognised (case-insensitive stem match):
        workorder_WO-001.html      -> WO-001
        workorder_WO-001_r2.html   -> WO-001  (revision suffix ignored)
        decision_DEC-005.html      -> DEC-005
        liability_LIA-002.html     -> LIA-002
        question_OQ-007.html       -> OQ-007

    The mapping values are root-relative URLs (e.g. "/workorder_WO-001.html")
    suitable for use in href attributes.  For sites served from a subdirectory,
    callers can prepend a base path.

    Override or extend this function if your build uses a different naming
    convention.
    """
    code_urls: dict[str, str] = {}
    all_re = _all_codes_pattern()

    for html_file in sorted(site_dir.rglob("*.html")):
        stem = html_file.stem  # e.g. "workorder_WO-001_r2"
        match = all_re.search(stem)
        if match:
            code = match.group(1).upper()
            # Build a root-relative URL from the site directory.
            rel_path = html_file.relative_to(site_dir)
            url = "/" + str(rel_path).replace("\\", "/")
            # First match wins — earlier revisions sort first, but the
            # latest revision (highest _rN suffix) sorts last so we
            # keep overwriting to pick up the latest.
            code_urls[code] = url

    return code_urls


# ---------------------------------------------------------------------------
# HTML replacement logic
# ---------------------------------------------------------------------------

# Regex that matches content inside tags where we must NOT replace:
#   - <a ...>...</a>     (already linked)
#   - <code>...</code>   (code blocks)
#   - <pre>...</pre>     (preformatted blocks)
#   - HTML tag attributes (anything between < and >)
_PROTECTED_RE = re.compile(
    r"<a\b[^>]*>.*?</a>"  # anchor tags (non-greedy)
    r"|<code\b[^>]*>.*?</code>"  # code elements
    r"|<pre\b[^>]*>.*?</pre>"  # pre elements
    r"|<[^>]+>",  # any HTML tag (protects attributes)
    re.DOTALL | re.IGNORECASE,
)


def _replace_codes_in_html(
    html: str,
    code_urls: dict[str, str],
    self_codes: set[str],
) -> str:
    """Replace bare code references with links, respecting protected regions.

    Parameters
    ----------
    html:
        The full HTML content of a page.
    code_urls:
        Mapping of code -> URL for all linkable codes.
    self_codes:
        Codes that appear on *this* page (to avoid self-links).

    Returns the modified HTML, or the original string if nothing changed.
    """
    all_re = _all_codes_pattern()

    def _replace_in_text(text: str) -> str:
        """Replace codes in a plain-text segment (outside protected regions)."""

        def _sub(m: re.Match[str]) -> str:
            code = m.group(1).upper()
            if code in self_codes:
                return m.group(0)  # no self-link
            url = code_urls.get(code)
            if url is None:
                return m.group(0)  # no target page known
            return f'<a href="{url}">{m.group(0)}</a>'

        return all_re.sub(_sub, text)

    # Split the HTML into protected and unprotected segments.
    result_parts: list[str] = []
    last_end = 0

    for m in _PROTECTED_RE.finditer(html):
        # Text before this protected region — eligible for replacement.
        if m.start() > last_end:
            result_parts.append(_replace_in_text(html[last_end : m.start()]))
        # The protected region itself — keep verbatim.
        result_parts.append(m.group(0))
        last_end = m.end()

    # Trailing text after the last protected region.
    if last_end < len(html):
        result_parts.append(_replace_in_text(html[last_end:]))

    return "".join(result_parts)


def _detect_self_codes(html_file: Path, code_urls: dict[str, str]) -> set[str]:
    """Determine which codes are 'self' for a given page.

    A code is a self-reference if this file is the target page for that code.
    """
    self_codes: set[str] = set()
    all_re = _all_codes_pattern()

    # Check if this filename contains a code.
    stem = html_file.stem
    match = all_re.search(stem)
    if match:
        self_codes.add(match.group(1).upper())

    return self_codes


# ---------------------------------------------------------------------------
# Main fix function — follows the postbuild-template.py contract
# ---------------------------------------------------------------------------


def fix_autolink_codes(site_dir: Path) -> str:
    """Link resolution — auto-link structured identifier codes.

    Scans built HTML filenames to discover which codes have dedicated pages,
    then replaces bare code references in text content with clickable links.

    Detection guard: skips codes already inside <a>, <code>, or <pre> tags
    and does not modify HTML attributes.  Idempotent — re-running produces
    no additional changes.
    """
    # Step 1: discover code -> URL mapping from filenames.
    code_urls = discover_code_urls(site_dir)
    if not code_urls:
        return "skipped — no linkable code pages found in site"

    # Step 2: process each HTML file.
    changed = 0
    all_re = _all_codes_pattern()

    for html_file in sorted(site_dir.rglob("*.html")):
        text = html_file.read_text(encoding="utf-8")

        # --- detection guard ---
        # Quick check: does this file contain any bare codes at all?
        if not all_re.search(text):
            continue

        self_codes = _detect_self_codes(html_file, code_urls)

        new_text = _replace_codes_in_html(text, code_urls, self_codes)
        if new_text != text:
            html_file.write_text(new_text, encoding="utf-8")
            changed += 1

    if changed == 0:
        return f"skipped — {len(code_urls)} code(s) registered but no bare references to link"
    return f"applied — linked codes in {changed} file(s) ({len(code_urls)} code(s) registered)"


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 fix_autolink_codes.py <site-dir>")
        sys.exit(2)

    site_path = Path(sys.argv[1])
    if not site_path.is_dir():
        print(f"error: {site_path} is not a directory")
        sys.exit(1)

    status = fix_autolink_codes(site_path)
    print(f"fix_autolink_codes: {status}")

    if status.startswith("error"):
        sys.exit(1)
