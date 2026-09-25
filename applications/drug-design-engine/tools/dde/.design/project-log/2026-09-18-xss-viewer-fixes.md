# Fix DOM XSS Vulnerabilities in HTML Viewer Templates

**Date:** 2026-09-18
**Issues:** #225, #226, #227, #228, #229, #230
**Branch:** mantis/fix-xss-viewers

## Summary

Fixed DOM-based XSS vulnerabilities in 5 HTML viewer template files under
`applications/DDE/tools/dde/site_templates/viewers/`. All shared the same
root-cause pattern: using `innerHTML` to insert data values derived from
JSON input without HTML escaping.

## Root Cause

These viewers fetch JSON data files and render them as HTML tables, cards,
or badges using string concatenation into `innerHTML`. When data values
are inserted directly (without escaping), a malicious or corrupted JSON
file could inject arbitrary HTML/JavaScript into the rendered page.

## Changes

### Files with escapeHtml added (did not previously have it)

1. **constraint-viewer.html** — Added `escapeHtml()` function. Wrapped
   `displayVal`, confidence interval strings (`m.ci`), and count strings
   (`m.counts`) with `escapeHtml()`. Hardcoded labels/descriptions left
   unescaped (safe by definition).

2. **contacts-viewer.html** — Added `escapeHtml()` function. Escaped
   `residues.length`, `chainKeys.length`, and individual chain key values
   before insertion into the binding-info `innerHTML`.

3. **pockets-viewer.html** — Added `escapeHtml()` function. All formatted
   data values (`col.format(val)`) are now escaped via a `safeFormatted`
   variable before being placed into table cells and score badges.

### Files with existing escapeHtml (already had the function)

4. **admet-viewer.html** — `metrics.n_liabilities` and `metrics.n_marginals`
   were inserted raw into the verdict badge HTML. Now wrapped with
   `escapeHtml(String(...))`.

5. **tournament-viewer.html** — Multiple numeric values in the tournament
   table were inserted raw: ranking, ELO rating, win/loss counts, win rate
   percentage, and review count. Also three values in the tournament-meta
   header (n_ideas_generated, highest_elo, date). All now wrapped with
   `escapeHtml(String(...))`.

### Not changed (false positive)

- **tournament-viewer.html line 97-100** — The scanner flagged
  `div.innerHTML` at line 100, but this is the *implementation* of the
  `escapeHtml` function itself (reads innerHTML from a div whose
  textContent was set). This is the standard browser-native escape
  technique and is correct.

## Design Decisions

- **Defense-in-depth for numeric values:** Even values expected to be
  numeric (ranking, ELO, counts) are escaped via `escapeHtml(String(...))`
  rather than just `Number()` coercion. This ensures that if the data
  source is corrupted and a "numeric" field contains a string, the worst
  case is display corruption rather than script execution.

- **Hardcoded literals not escaped:** Labels, descriptions, thresholds,
  and CSS class names defined as string literals in the JavaScript code
  itself are not wrapped — they cannot be influenced by external data.

- **No structural changes:** The fix preserves the exact visual appearance
  and functionality of all viewers. Sort handlers, click handlers,
  expand/collapse, and Plotly chart rendering are unchanged.

## Verification

- **Syntax check:** All 5 files pass `node --check` on the extracted
  `<script>` content (no JavaScript syntax errors).

### Manual Verification Steps

To confirm the fixes work correctly:

1. **Functional test:** Open each viewer with a valid JSON data file and
   verify it renders identically to the pre-fix version:
   - `constraint-viewer.html?file=<gene>.gnomad-constraint.json`
   - `contacts-viewer.html?file=<compound>.contacts.json`
   - `admet-viewer.html?file=<compound>.predict.json`
   - `tournament-viewer.html?file=<tournament>.json`
   - `pockets-viewer.html?file=<structure>.pockets.json`

2. **XSS test:** Create a test JSON file with a malicious value like
   `"<img src=x onerror=alert(1)>"` in a field that gets displayed
   (e.g., a chain key in contacts, a verdict in admet). Verify the
   HTML entities are visible as text rather than being interpreted as
   HTML elements.

3. **Sort/interaction test:** In tournament-viewer and pockets-viewer,
   verify that column sorting still works after the fix.

## Gates Run

- JavaScript syntax validation via `node --check`: PASSED (all 5 files)
- No automated test framework exists for these HTML template files
