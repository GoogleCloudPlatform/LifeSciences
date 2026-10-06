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
Post-build orchestrator template.

Copy this file into your project root or site-tools/ directory and adapt
the FIXES list and fix functions to your program's needs.  Run after every
`dde site build` and before verification:

    dde site build
    python3 postbuild.py _site/
    # verify and serve

Every fix function must be **idempotent**: safe to re-run without
accumulating changes.  Each function returns a short status string
("applied", "skipped — already applied", or "error: ...").
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Ordered list of (name, callable) pairs.  Execution follows this order.
# Categories (run in sequence):
#   1. Structural HTML fixes
#   2. CSS / styling fixes
#   3. Link resolution
#   4. Cosmetic passes
#   5. Scan / report


def fix_tables(site_dir: Path) -> str:
    """Structural HTML — convert pipe-delimited markdown tables to <table>.

    The build tool sometimes emits raw pipe-delimited tables instead of
    proper HTML <table> elements.  This fix scans every .html file for
    lines that look like markdown table rows and wraps them in <table>
    markup.

    Detection guard: skip files that already contain a <table> element
    where the raw-text table was expected.
    """
    changed = 0
    for html_file in site_dir.rglob("*.html"):
        # Skip symlinks to prevent symlink exploitation (issue #247).
        if html_file.is_symlink():
            continue
        text = html_file.read_text(encoding="utf-8")

        # --- detection guard ---
        # Look for pipe-delimited lines that are NOT inside a <table>.
        # A simple heuristic: lines starting with "| " outside <pre>/<code>.
        pipe_lines = re.findall(r"^(?:<p>)?\|.+\|(?:</p>)?$", text, re.MULTILINE)
        if not pipe_lines:
            continue  # nothing to fix in this file

        # --- apply fix ---
        # TODO: Replace this stub with actual table-conversion logic.
        # Parse consecutive pipe-delimited lines, split cells on "|",
        # emit <table><thead>/<tbody> with <tr>/<th>/<td> elements.
        changed += 1

    if changed == 0:
        return "skipped — no unconverted tables found"
    return f"stub — {changed} file(s) need table conversion (not yet implemented)"


def fix_duplicate_h1(site_dir: Path) -> str:
    """Structural HTML — remove duplicate H1 elements.

    The page template injects an <h1> from metadata.  When the markdown
    source also starts with ``# Title``, the rendered page contains two
    <h1> tags.  This fix removes the second <h1> when its text matches
    the first.

    Detection guard: skip files with zero or one <h1>.
    """
    changed = 0
    for html_file in site_dir.rglob("*.html"):
        # Skip symlinks to prevent symlink exploitation (issue #247).
        if html_file.is_symlink():
            continue
        text = html_file.read_text(encoding="utf-8")
        h1s = re.findall(r"<h1[^>]*>(.*?)</h1>", text, re.DOTALL)

        # --- detection guard ---
        if len(h1s) <= 1:
            continue

        # --- apply fix ---
        # Remove the second <h1> if its stripped text matches the first.
        first = h1s[0].strip()
        second = h1s[1].strip()
        if first.lower() != second.lower():
            continue

        # Remove the second occurrence only.
        # TODO: Implement precise removal preserving surrounding markup.
        changed += 1

    if changed == 0:
        return "skipped — no duplicate H1s found"
    return f"stub — {changed} file(s) have duplicate H1 (not yet implemented)"


def fix_md_links(site_dir: Path) -> str:
    """Link resolution — rewrite .md hrefs to .html.

    Internal links generated from markdown cross-references retain the
    ``.md`` extension.  This fix rewrites ``href="*.md"`` to
    ``href="*.html"`` for internal links only (ignores external URLs).

    Detection guard: skip if no href contains ``.md``.
    """
    changed = 0
    md_href = re.compile(r'href="([^"]*\.md)((?:[#?][^"]*)?)"')

    for html_file in site_dir.rglob("*.html"):
        # Skip symlinks to prevent symlink exploitation (issue #247).
        if html_file.is_symlink():
            continue
        text = html_file.read_text(encoding="utf-8")

        # --- detection guard ---
        matches = md_href.findall(text)
        internal = [
            base
            for base, _suffix in matches
            if not base.startswith(("http://", "https://"))
        ]
        if not internal:
            continue

        # --- apply fix ---
        def rewrite(m: re.Match) -> str:
            base = m.group(1)
            suffix = m.group(2)  # fragment or query string
            if base.startswith(("http://", "https://")):
                return m.group(0)
            return f'href="{base.removesuffix(".md")}.html{suffix}"'

        new_text = md_href.sub(rewrite, text)
        if new_text != text:
            html_file.write_text(new_text, encoding="utf-8")
            changed += 1

    if changed == 0:
        return "skipped — no .md links found"
    return f"applied — rewrote links in {changed} file(s)"


# ---------------------------------------------------------------------------
# Fix registry — add or remove entries to match your program's needs.
# ---------------------------------------------------------------------------

FIXES: list[tuple[str, Callable[[Path], str]]] = [
    # 1. Structural HTML
    ("fix_tables", fix_tables),
    ("fix_duplicate_h1", fix_duplicate_h1),
    # 2. CSS / styling  — add your fix functions here
    # ("fix_scroll_context", fix_scroll_context),
    # 3. Link resolution
    ("fix_md_links", fix_md_links),
    # 4. Cosmetic passes — add your fix functions here
    # ("fix_whitespace", fix_whitespace),
    # 5. Scan / report   — add a final scan function here
    # ("scan_remaining_issues", scan_remaining_issues),
]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run(site_dir: Path) -> None:
    """Execute all registered fixes in order, print summary."""
    print(f"postbuild: processing {site_dir}")
    results: list[tuple[str, str]] = []

    for name, fn in FIXES:
        try:
            status = fn(site_dir)
        except Exception as exc:
            status = f"error: {exc}"
        results.append((name, status))
        print(f"  {name}: {status}")

    # --- summary ---
    applied = sum(1 for _, s in results if s.startswith("applied"))
    skipped = sum(1 for _, s in results if s.startswith("skipped"))
    errors = sum(1 for _, s in results if s.startswith("error"))
    print(f"postbuild: done — {applied} applied, {skipped} skipped, {errors} errors")

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 postbuild.py <site-dir>")
        sys.exit(2)
    run(Path(sys.argv[1]))
