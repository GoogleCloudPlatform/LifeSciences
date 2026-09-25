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
Post-build fix: localize CDN resources for offline use.

Downloads external CSS and JS assets referenced by <link> and <script> tags
in the built site, stores them in a ``vendor/`` directory, and rewrites HTML
references to use local paths.  Handles transitive references such as
Google Fonts CSS that pulls ``.woff2`` files from ``fonts.gstatic.com``.

Run after ``dde site build`` and before verification::

    dde site build
    python3 fix_localize_cdn.py _site/
    # verify and serve

Idempotent: skips resources that have already been downloaded.  Re-running
after a rebuild is safe and will only download newly-added CDN references.

Follows the post-build fix pattern from ``references/postbuild-template.py``.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Configuration — CDN domains to localize
# ---------------------------------------------------------------------------

CDN_DOMAINS: set[str] = {
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.jsdelivr.net",
}

# Google Fonts CSS expects a browser User-Agent to serve woff2.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_cdn_url(url: str) -> bool:
    """Return True if *url* points to one of the known CDN domains."""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    return host in CDN_DOMAINS


def _url_to_vendor_path(url: str) -> str:
    """Map a CDN URL to a deterministic relative path under ``vendor/``.

    Strategy: ``vendor/<domain>/<url-path-without-leading-slash>``.
    Query strings are hashed and appended to avoid collisions while
    keeping the directory structure readable.

    Examples
    --------
    >>> _url_to_vendor_path("https://cdn.jsdelivr.net/npm/3dmol@2.4.2/build/3Dmol-min.js")
    'vendor/cdn.jsdelivr.net/npm/3dmol@2.4.2/build/3Dmol-min.js'
    >>> _url_to_vendor_path("https://fonts.googleapis.com/css2?family=Roboto")
    'vendor/fonts.googleapis.com/css2_q_<hash>'
    """
    parsed = urlparse(url)
    domain = parsed.hostname or "unknown"
    # Sanitize domain to prevent traversal via crafted hostnames.
    domain = re.sub(r"[^a-zA-Z0-9._-]", "_", domain)
    path = parsed.path.lstrip("/")
    # Remove ".." segments to prevent directory traversal.
    segments = [s for s in path.split("/") if s and s != ".."]
    path = "/".join(segments)

    if parsed.query:
        qhash = hashlib.sha256(parsed.query.encode()).hexdigest()[:12]
        # Append a query-hash suffix so different query variants stay unique.
        path = f"{path}_q_{qhash}"

    return f"vendor/{domain}/{path}"


def _download(url: str, dest: Path) -> bool:
    """Download *url* to *dest*.  Return True on success, False on failure."""
    if dest.exists() and dest.stat().st_size > 0:
        return True  # already downloaded — idempotent skip

    dest.parent.mkdir(parents=True, exist_ok=True)

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            dest.write_bytes(resp.read())
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        print(f"  WARNING: failed to download {url}: {exc}")
        return False
    return True


def _relative_path(from_file: Path, to_file: Path) -> str:
    """Compute a relative path from *from_file* to *to_file*.

    Both paths must be relative to the same site root.
    """
    try:
        return str(to_file.relative_to(from_file.parent))
    except ValueError:
        # Different subtrees — walk via common ancestor.
        from os.path import relpath

        return relpath(to_file, from_file.parent)


# ---------------------------------------------------------------------------
# CSS transitive reference handling
# ---------------------------------------------------------------------------

_CSS_URL_RE = re.compile(r"""url\(\s*(['"]?)(https?://[^)'"]+)\1\s*\)""")


def _localize_css_urls(
    css_text: str,
    css_vendor_path: Path,
    site_dir: Path,
    manifest: dict[str, str],
) -> str:
    """Download resources referenced by ``url()`` in CSS and rewrite paths.

    Handles transitive references such as ``.woff2`` files loaded by
    Google Fonts CSS.
    """

    def _replace_url(m: re.Match) -> str:
        quote = m.group(1)
        url = m.group(2)
        if not _is_cdn_url(url):
            return m.group(0)

        rel_vendor = _url_to_vendor_path(url)
        dest = site_dir / rel_vendor
        # Verify the destination stays within site_dir.
        resolved_dest = dest.resolve()
        if not resolved_dest.is_relative_to(site_dir.resolve()):
            return m.group(0)  # skip — path escapes site_dir
        if _download(url, dest):
            manifest[url] = rel_vendor
            # Relative path from the CSS file to the downloaded resource.
            local_rel = _relative_path(css_vendor_path, dest)
            return f"url({quote}{local_rel}{quote})"
        # Download failed — leave the original URL so the page still works
        # online even if offline is broken for this resource.
        return m.group(0)

    return _CSS_URL_RE.sub(_replace_url, css_text)


# ---------------------------------------------------------------------------
# HTML scanning and rewriting
# ---------------------------------------------------------------------------

# Matches <link ... href="https://..."> and <script ... src="https://...">.
_LINK_RE = re.compile(
    r"""(<link\b[^>]*\bhref\s*=\s*["'])(https?://[^"']+)(["'][^>]*>)""",
    re.IGNORECASE,
)
_SCRIPT_RE = re.compile(
    r"""(<script\b[^>]*\bsrc\s*=\s*["'])(https?://[^"']+)(["'][^>]*>)""",
    re.IGNORECASE,
)


def _localize_html(
    html_file: Path,
    site_dir: Path,
    manifest: dict[str, str],
) -> int:
    """Localize CDN references in a single HTML file.

    Returns the number of references rewritten.
    """
    text = html_file.read_text(encoding="utf-8")
    rewritten = 0

    def _rewrite_tag(m: re.Match) -> str:
        nonlocal rewritten
        prefix = m.group(1)
        url = m.group(2)
        suffix = m.group(3)

        if not _is_cdn_url(url):
            return m.group(0)

        rel_vendor = _url_to_vendor_path(url)
        dest = site_dir / rel_vendor
        # Verify the destination stays within site_dir.
        resolved_dest = dest.resolve()
        if not resolved_dest.is_relative_to(site_dir.resolve()):
            return m.group(0)  # skip — path escapes site_dir
        if not _download(url, dest):
            return m.group(0)  # keep original on failure

        manifest[url] = rel_vendor

        # If this is a CSS file, handle transitive url() references.
        if dest.suffix == ".css" or url.endswith(".css") or "css" in url:
            try:
                css_text = dest.read_text(encoding="utf-8")
                new_css = _localize_css_urls(css_text, dest, site_dir, manifest)
                if new_css != css_text:
                    dest.write_text(new_css, encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                print(f"  WARNING: could not process CSS in {dest}: {exc}")

        local_rel = _relative_path(html_file, dest)
        rewritten += 1
        return f"{prefix}{local_rel}{suffix}"

    text = _LINK_RE.sub(_rewrite_tag, text)
    text = _SCRIPT_RE.sub(_rewrite_tag, text)

    if rewritten > 0:
        html_file.write_text(text, encoding="utf-8")

    return rewritten


# ---------------------------------------------------------------------------
# Public entry point (follows postbuild-template pattern)
# ---------------------------------------------------------------------------


def fix_localize_cdn(site_dir: Path) -> str:
    """Localize CDN resources in the built site.

    Scans all HTML files for external ``<link>`` and ``<script>`` references
    to known CDN domains, downloads them to ``vendor/``, and rewrites HTML
    tags to use local paths.

    Detection guard: skips if no CDN references are found in any HTML file.
    """
    manifest_path = site_dir / "vendor" / "manifest.json"

    # Load existing manifest for idempotency.
    manifest: dict[str, str] = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            manifest = {}

    html_files = list(site_dir.rglob("*.html"))
    if not html_files:
        return "skipped — no HTML files found"

    # Detection guard: check whether any CDN references exist.
    has_cdn = False
    for html_file in html_files:
        text = html_file.read_text(encoding="utf-8")
        for pattern in (_LINK_RE, _SCRIPT_RE):
            for m in pattern.finditer(text):
                if _is_cdn_url(m.group(2)):
                    has_cdn = True
                    break
            if has_cdn:
                break
        if has_cdn:
            break

    if not has_cdn:
        return "skipped — no CDN references found"

    total_rewritten = 0
    files_changed = 0
    for html_file in html_files:
        count = _localize_html(html_file, site_dir, manifest)
        if count > 0:
            total_rewritten += count
            files_changed += 1

    # Write manifest.
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if total_rewritten == 0:
        return "skipped — CDN references found but all downloads failed"
    return (
        f"applied — localized {total_rewritten} reference(s) "
        f"in {files_changed} file(s), manifest at vendor/manifest.json"
    )


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 fix_localize_cdn.py <site-dir>")
        sys.exit(2)

    site_path = Path(sys.argv[1])
    if not site_path.is_dir():
        print(f"Error: {site_path} is not a directory")
        sys.exit(2)

    print(f"fix_localize_cdn: processing {site_path}")
    result = fix_localize_cdn(site_path)
    print(f"  {result}")

    if result.startswith("error"):
        sys.exit(1)
