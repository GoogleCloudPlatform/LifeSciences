# #275 URL Sanitization Bypass — Combined Fix

**Date:** 2026-09-19
**Issue:** #275
**Branch:** `scion/dev-url-sanitize-v3`

## Problem

The URL sanitization in `_sanitize_external_urls()` (site.py) was vulnerable to
bypass via two independent vectors:

1. **Broken heuristic for scheme detection:** The original `"://" in url` check
   missed single-slash schemes (`https:/attacker.com`), backslash variants
   (`https:\\evil.com`), and non-hierarchical schemes (`javascript:alert(1)`).

2. **C0 control character injection:** Browsers strip leading C0 control
   characters (U+0000-U+001F) and embedded tab/newline/CR from URLs per the
   WHATWG URL Standard section 4.2, but Python's `str.strip()` / `str.lstrip()`
   only strips whitespace. An attacker could prepend `\x01` or embed `\t` to
   bypass both `_is_external_url()` and the dangerous-scheme prefix check.

## Solution

Combined two prior partial fixes (v1 urlsplit, v2 WHATWG normalization) into a
single robust implementation:

### 1. WHATWG C0 Normalization (`_whatwg_normalize_url`)

Added a helper that strips:
- Leading C0 control characters (U+0000-U+001F) and space (U+0020)
- Embedded tab (`\t`), newline (`\n`), and carriage return (`\r`)

This mirrors the WHATWG URL Standard section 4.2 steps 1 and 3.

### 2. `urlsplit`-based scheme detection (`_is_external_url`)

Replaced the broken `"://" in url` heuristic with
`urllib.parse.urlsplit()`, which correctly identifies schemes for all URL
forms (single-slash, no-slash, backslash).

### 3. Updated dangerous-scheme checks

Both `_replace_img()` (data: URI check) and `_replace_a()` (dangerous
scheme check) now apply `_whatwg_normalize_url()` before prefix matching,
closing the C0 injection vector.

## Files Changed

- `tools/dde/commands/site.py` — added `_whatwg_normalize_url()`, rewrote
  `_is_external_url()` with urlsplit + WHATWG normalization, updated
  dangerous-scheme checks in `_replace_img()` and `_replace_a()`
- `tests/test_url_sanitization_bypass.py` — new test file with 53 tests covering
  urlsplit scheme detection, C0 control char bypasses, embedded tab/newline/CR
  bypasses, and end-to-end sanitization

## Verification

- 53/53 URL sanitization bypass tests pass
- 18/18 non-mistune site renderer tests pass (17 mistune-dependent tests skipped
  due to missing `mistune` in this environment — unrelated to this change)
- `ruff check` and `ruff format --check` clean on both changed files
