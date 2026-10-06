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
Post-build fix: inject a drop-in theme system into every page.

Adds a theme chooser dropdown to the site navigation, defines five themes
using CSS custom properties, persists the user's choice in localStorage,
and applies the saved theme before first paint to prevent FOUC.

Usage:
    python3 fix_add_themes.py <site-dir>

Idempotent — skips pages that already contain the theme system.
Follows the post-build fix pattern from references/postbuild-template.py.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Detection marker injected into themed pages.
THEME_MARKER = "<!-- dde-theme-system -->"

# Google Fonts — one <link> tag loading all theme families.
GOOGLE_FONTS_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?'
    "family=Inter:wght@400;500;600;700&"
    "family=Merriweather:wght@400;700&"
    "family=Fira+Code:wght@400;500&"
    'display=swap" rel="stylesheet">'
)

# Inline script placed in <head> — applies saved theme before first paint
# so the page never flashes in the wrong colours (FOUC prevention).
FOUC_SCRIPT = (
    "<script>"
    "(function(){var t=localStorage.getItem('dde-theme');"
    "if(t)document.documentElement.setAttribute('data-theme',t)})()"
    "</script>"
)

# ---------------------------------------------------------------------------
# Theme CSS — five themes, ~30 custom properties each
# ---------------------------------------------------------------------------

THEME_CSS = """\
<style>
/* dde-theme-system */

/* ---- Clean (default): light, sans-serif ---- */
:root, [data-theme="clean"] {
  --font-family: 'Inter', system-ui, -apple-system, sans-serif;
  --font-family-code: 'Fira Code', ui-monospace, monospace;
  --font-size-base: 16px;
  --bg-primary: #ffffff;
  --bg-secondary: #f8f9fa;
  --text-primary: #1a1a2e;
  --text-secondary: #555770;
  --heading-color: #1a1a2e;
  --border-color: #e0e0e6;
  --accent-color: #4361ee;
  --sidebar-bg: #f3f4f6;
  --sidebar-text: #374151;
  --sidebar-border: #e5e7eb;
  --sidebar-hover-bg: #e5e7eb;
  --nav-bg: #ffffff;
  --nav-text: #1a1a2e;
  --nav-border: #e0e0e6;
  --nav-hover-bg: #f3f4f6;
  --link-color: #4361ee;
  --link-hover-color: #3148c4;
  --link-visited-color: #6b5bae;
  --table-bg: #ffffff;
  --table-border: #e0e0e6;
  --table-header-bg: #f3f4f6;
  --table-stripe-bg: #f8f9fa;
  --table-hover-bg: #eef0f5;
  --code-bg: #f4f4f8;
  --code-text: #d6336c;
  --code-border: #e0e0e6;
  --hover-bg: #f0f0f5;
}

/* ---- Dark mode ---- */
[data-theme="dark"] {
  --font-family: 'Inter', system-ui, -apple-system, sans-serif;
  --font-family-code: 'Fira Code', ui-monospace, monospace;
  --font-size-base: 16px;
  --bg-primary: #1a1a2e;
  --bg-secondary: #16213e;
  --text-primary: #e0e0e6;
  --text-secondary: #a0a0b8;
  --heading-color: #e8e8f0;
  --border-color: #2d2d44;
  --accent-color: #6c83f7;
  --sidebar-bg: #16213e;
  --sidebar-text: #c8c8d8;
  --sidebar-border: #2d2d44;
  --sidebar-hover-bg: #2d2d44;
  --nav-bg: #1a1a2e;
  --nav-text: #e0e0e6;
  --nav-border: #2d2d44;
  --nav-hover-bg: #2d2d44;
  --link-color: #6c83f7;
  --link-hover-color: #8da0ff;
  --link-visited-color: #9b8ec4;
  --table-bg: #1a1a2e;
  --table-border: #2d2d44;
  --table-header-bg: #16213e;
  --table-stripe-bg: #1e1e36;
  --table-hover-bg: #2a2a42;
  --code-bg: #16213e;
  --code-text: #ff6b9d;
  --code-border: #2d2d44;
  --hover-bg: #2a2a42;
}

/* ---- Serif / editorial (reading-heavy content) ---- */
[data-theme="serif"] {
  --font-family: 'Merriweather', Georgia, 'Times New Roman', serif;
  --font-family-code: 'Fira Code', ui-monospace, monospace;
  --font-size-base: 17px;
  --bg-primary: #faf8f5;
  --bg-secondary: #f0ece4;
  --text-primary: #2c2416;
  --text-secondary: #5c5040;
  --heading-color: #2c2416;
  --border-color: #d6ceb8;
  --accent-color: #8b5e3c;
  --sidebar-bg: #f0ece4;
  --sidebar-text: #4a4030;
  --sidebar-border: #d6ceb8;
  --sidebar-hover-bg: #e4ddd0;
  --nav-bg: #faf8f5;
  --nav-text: #2c2416;
  --nav-border: #d6ceb8;
  --nav-hover-bg: #f0ece4;
  --link-color: #8b5e3c;
  --link-hover-color: #6b4428;
  --link-visited-color: #7a6b5a;
  --table-bg: #faf8f5;
  --table-border: #d6ceb8;
  --table-header-bg: #f0ece4;
  --table-stripe-bg: #f5f1ea;
  --table-hover-bg: #ebe5da;
  --code-bg: #f0ece4;
  --code-text: #8b5e3c;
  --code-border: #d6ceb8;
  --hover-bg: #ebe5da;
}

/* ---- Ocean: blue accent variant ---- */
[data-theme="ocean"] {
  --font-family: 'Inter', system-ui, -apple-system, sans-serif;
  --font-family-code: 'Fira Code', ui-monospace, monospace;
  --font-size-base: 16px;
  --bg-primary: #f0f7ff;
  --bg-secondary: #e1effe;
  --text-primary: #0c2d48;
  --text-secondary: #3a6b8c;
  --heading-color: #0c2d48;
  --border-color: #b8d4e8;
  --accent-color: #0077b6;
  --sidebar-bg: #e1effe;
  --sidebar-text: #1a4a6e;
  --sidebar-border: #b8d4e8;
  --sidebar-hover-bg: #c8ddf4;
  --nav-bg: #f0f7ff;
  --nav-text: #0c2d48;
  --nav-border: #b8d4e8;
  --nav-hover-bg: #e1effe;
  --link-color: #0077b6;
  --link-hover-color: #005a8c;
  --link-visited-color: #4a7fa0;
  --table-bg: #f0f7ff;
  --table-border: #b8d4e8;
  --table-header-bg: #e1effe;
  --table-stripe-bg: #e8f2ff;
  --table-hover-bg: #d0e5f8;
  --code-bg: #e1effe;
  --code-text: #0077b6;
  --code-border: #b8d4e8;
  --hover-bg: #d0e5f8;
}

/* ---- Forest: green accent variant ---- */
[data-theme="forest"] {
  --font-family: 'Inter', system-ui, -apple-system, sans-serif;
  --font-family-code: 'Fira Code', ui-monospace, monospace;
  --font-size-base: 16px;
  --bg-primary: #f4f9f4;
  --bg-secondary: #e8f3e8;
  --text-primary: #1a3a1a;
  --text-secondary: #4a6b4a;
  --heading-color: #1a3a1a;
  --border-color: #b8d4b8;
  --accent-color: #2d7d46;
  --sidebar-bg: #e8f3e8;
  --sidebar-text: #2a5a2a;
  --sidebar-border: #b8d4b8;
  --sidebar-hover-bg: #d4e8d4;
  --nav-bg: #f4f9f4;
  --nav-text: #1a3a1a;
  --nav-border: #b8d4b8;
  --nav-hover-bg: #e8f3e8;
  --link-color: #2d7d46;
  --link-hover-color: #1d5f32;
  --link-visited-color: #5a8a5a;
  --table-bg: #f4f9f4;
  --table-border: #b8d4b8;
  --table-header-bg: #e8f3e8;
  --table-stripe-bg: #eef6ee;
  --table-hover-bg: #d8ecd8;
  --code-bg: #e8f3e8;
  --code-text: #2d7d46;
  --code-border: #b8d4b8;
  --hover-bg: #d8ecd8;
}

/* ---- Apply custom properties to page elements ---- */
body {
  font-family: var(--font-family) !important;
  font-size: var(--font-size-base) !important;
  background-color: var(--bg-primary) !important;
  color: var(--text-primary) !important;
}
h1, h2, h3, h4, h5, h6 {
  color: var(--heading-color) !important;
}
a { color: var(--link-color) !important; }
a:hover { color: var(--link-hover-color) !important; }
a:visited { color: var(--link-visited-color) !important; }
code, pre code {
  font-family: var(--font-family-code) !important;
  background-color: var(--code-bg) !important;
  color: var(--code-text) !important;
  border-color: var(--code-border) !important;
}
pre {
  background-color: var(--code-bg) !important;
  border: 1px solid var(--code-border) !important;
}
table {
  background-color: var(--table-bg) !important;
  border-color: var(--table-border) !important;
}
table th {
  background-color: var(--table-header-bg) !important;
  border-color: var(--table-border) !important;
}
table td {
  border-color: var(--table-border) !important;
}
table tr:nth-child(even) {
  background-color: var(--table-stripe-bg) !important;
}
table tr:hover {
  background-color: var(--table-hover-bg) !important;
}
nav, .nav, header {
  background-color: var(--nav-bg) !important;
  color: var(--nav-text) !important;
  border-color: var(--nav-border) !important;
}
nav a, .nav a, header a {
  color: var(--nav-text) !important;
}
nav a:hover, .nav a:hover {
  background-color: var(--nav-hover-bg) !important;
}
.sidebar, aside {
  background-color: var(--sidebar-bg) !important;
  color: var(--sidebar-text) !important;
  border-color: var(--sidebar-border) !important;
}
.sidebar a, aside a {
  color: var(--sidebar-text) !important;
}
.sidebar a:hover, aside a:hover {
  background-color: var(--sidebar-hover-bg) !important;
}

/* Theme chooser widget */
.dde-theme-chooser {
  display: inline-block;
  margin-left: 1rem;
}
.dde-theme-chooser select {
  font-family: var(--font-family);
  font-size: 0.85rem;
  padding: 0.25rem 0.5rem;
  border: 1px solid var(--border-color);
  border-radius: 4px;
  background-color: var(--bg-secondary);
  color: var(--text-primary);
  cursor: pointer;
}
.dde-theme-chooser select:hover {
  border-color: var(--accent-color);
}
</style>"""

# Theme chooser dropdown HTML.
THEME_CHOOSER = (
    '<span class="dde-theme-chooser">'
    '<select id="dde-theme-select" aria-label="Choose theme">'
    '<option value="clean">Clean</option>'
    '<option value="dark">Dark</option>'
    '<option value="serif">Serif</option>'
    '<option value="ocean">Ocean</option>'
    '<option value="forest">Forest</option>'
    "</select></span>"
)

# Theme switching script — under 20 lines of JS.
THEME_SCRIPT = """\
<script>
(function() {
  var sel = document.getElementById('dde-theme-select');
  if (!sel) return;
  var saved = localStorage.getItem('dde-theme') || 'clean';
  document.documentElement.setAttribute('data-theme', saved);
  sel.value = saved;
  sel.addEventListener('change', function() {
    var t = sel.value;
    document.documentElement.setAttribute('data-theme', t);
    localStorage.setItem('dde-theme', t);
  });
})();
</script>"""


# ---------------------------------------------------------------------------
# Fix function
# ---------------------------------------------------------------------------


def fix_add_themes(site_dir: Path) -> str:
    """Inject theme system into all HTML pages.

    Detection guard: skips files that already contain the theme marker comment.
    Returns a status string following the post-build fix convention.
    """
    changed = 0
    skipped = 0

    for html_file in sorted(site_dir.rglob("*.html")):
        # Skip symlinks to prevent symlink exploitation (issue #246).
        if html_file.is_symlink():
            continue
        text = html_file.read_text(encoding="utf-8")

        # --- detection guard ---
        if THEME_MARKER in text:
            skipped += 1
            continue

        # Skip viewer files — they are standalone utilities, not content pages.
        if "-viewer.html" in html_file.name:
            continue

        # Must have <head> and </body> to inject into.
        if "<head" not in text.lower() or "</body>" not in text.lower():
            continue

        new_text = text

        # 1. Inject marker + Google Fonts + FOUC script + theme CSS in <head>.
        head_payload = (
            f"\n{THEME_MARKER}\n{GOOGLE_FONTS_LINK}\n{FOUC_SCRIPT}\n{THEME_CSS}\n"
        )
        new_text = re.sub(
            r"(<head[^>]*>)",
            r"\1" + head_payload,
            new_text,
            count=1,
            flags=re.IGNORECASE,
        )

        # 2. Inject theme chooser into the navigation area.
        chooser_placed = False
        if re.search(r"</nav>", new_text, re.IGNORECASE):
            new_text = re.sub(
                r"(</nav>)",
                THEME_CHOOSER + r"\1",
                new_text,
                count=1,
                flags=re.IGNORECASE,
            )
            chooser_placed = True
        elif re.search(r"</header>", new_text, re.IGNORECASE):
            new_text = re.sub(
                r"(</header>)",
                THEME_CHOOSER + r"\1",
                new_text,
                count=1,
                flags=re.IGNORECASE,
            )
            chooser_placed = True

        if not chooser_placed:
            # No nav or header element — place chooser right after <body>.
            new_text = re.sub(
                r"(<body[^>]*>)",
                r"\1\n" + THEME_CHOOSER,
                new_text,
                count=1,
                flags=re.IGNORECASE,
            )

        # 3. Inject theme switching script before </body>.
        new_text = re.sub(
            r"(</body>)",
            THEME_SCRIPT + r"\n\1",
            new_text,
            count=1,
            flags=re.IGNORECASE,
        )

        if new_text != text:
            html_file.write_text(new_text, encoding="utf-8")
            changed += 1

    if changed == 0:
        if skipped > 0:
            return f"skipped — already applied to {skipped} file(s)"
        return "skipped — no eligible HTML files found"
    return f"applied — injected theme system into {changed} file(s)"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 fix_add_themes.py <site-dir>")
        sys.exit(2)
    site_path = Path(sys.argv[1])
    if not site_path.is_dir():
        print(f"error: {site_path} is not a directory")
        sys.exit(1)
    status = fix_add_themes(site_path)
    print(f"fix_add_themes: {status}")
    if status.startswith("error"):
        sys.exit(1)
