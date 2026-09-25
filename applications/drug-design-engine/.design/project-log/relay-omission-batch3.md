# Relay Omission Fix — Batch 3

**Date**: 2026-09-18
**Issue**: #197
**Branch**: `fix/relay-omission-batch3`

## Problem

In `hypex.py`, the `_build_hypex_record` function set `manifest_present = True`
**before** attempting to parse the citation manifest file via `_read_json_file`.
When the file was corrupt or contained invalid JSON, `_read_json_file` raised
`SchemaError`, which was caught and silently ignored — but `manifest_present`
remained `True`.

This prevented the `hypex.citation_manifest_absent` mandatory relay from firing
during analysis, violating the "fail loudly, never degrade silently" principle
(tool-design-guidance.md §8).

## Fix

Moved `manifest_present = True` to after the successful call to
`_read_json_file`, inside the `try` block. Now `manifest_present` is only set
to `True` when the manifest is actually valid and parsed. If `SchemaError` is
raised, `manifest_present` stays `False` and the relay fires correctly.

## Files Changed

- `applications/DDE/tools/dde/commands/hypex.py` — moved `manifest_present = True` after `_read_json_file` succeeds
- `applications/DDE/tests/test_relay_omission_batch3.py` — regression test (3 tests)

## Tests

Three regression tests added:

1. **test_manifest_present_false_when_citation_manifest_corrupt** — verifies `manifest_present` is `False` in the ingested record when the citation manifest is corrupt
2. **test_relay_fires_when_citation_manifest_corrupt** — full pipeline test: ingest + analyze, verifies `hypex.citation_manifest_absent` relay fires
3. **test_manifest_present_true_when_citation_manifest_valid** — sanity check: valid manifests still set `manifest_present` to `True`

## Verification

- `ruff check` — passed
- `ruff format --check` — passed
- `pytest` on test file — 3/3 passed
