# Shared Path Safety Helper Module

**Date:** 2026-09-18
**Author:** dev-paths-helper agent
**Branch:** `scion/dev-paths-helper`

## What

Created `tools/dde/core/paths.py` — a shared security helper module providing
three functions:

- **`confine_path(base_dir, path)`** — resolves a path and verifies it stays
  within a base directory, returning `None` on escape (traversal, null bytes,
  symlink loops).  Extracted from the private `_confine_path` in
  `commands/validate.py:130-145`.

- **`sanitize_slug(text, max_length=80)`** — strips a user-controlled string
  down to `[a-zA-Z0-9._-]`, collapses hyphens, enforces a length limit, and
  raises `ValueError` on empty results.  Dots are preserved for backward
  compatibility with GENCODE-style identifiers (e.g. `ENSG00000141510.16`).

- **`is_safe_to_open(path, allow_symlinks=False)`** — guards against opening
  symlinks unless explicitly opted in.

## Why

This is Phase 1 of fixing 16 path-traversal and symlink-exploitation
vulnerabilities identified across the DDE toolchain.  By centralising path
safety logic in one well-tested module, subsequent phases can replace ad-hoc
checks with calls to these shared functions, reducing duplication and the
risk of inconsistent or incomplete guards.

## Tests

Created `tests/test_paths.py` with 19 unit tests covering:

- `confine_path`: normal confinement, `../` escape, absolute-path escape,
  base-dir-itself edge case, embedded null bytes, non-existent child paths.
- `sanitize_slug`: alphanumeric passthrough, path-separator sanitization,
  GENCODE dot preservation, empty-slug errors, length truncation,
  leading/trailing stripping, consecutive-char collapsing.
- `is_safe_to_open`: regular files, symlink rejection, opt-in allow, and
  non-existent paths.

## Verification

- `pytest tests/test_paths.py -v` — 19/19 passed
- `ruff check` — all checks passed
- `ruff format --check` — already formatted
