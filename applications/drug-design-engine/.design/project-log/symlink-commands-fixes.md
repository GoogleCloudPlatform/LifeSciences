# Symlink Exploitation Fixes — commands/ Batch

**Date:** 2026-09-19
**Issues:** #248, #259, #261, #263, #266, #283, #284

## Summary

Added `is_safe_to_open()` guards to 7 call sites in `tools/dde/commands/`
where file reads or writes operated on paths without checking for symlink
exploitation. All guards use the existing `dde.core.paths.is_safe_to_open`
helper, following the same pattern already established in `validate.py`,
`artifact.py`, `hypex.py`, and `site.py`.

## Files Changed

### Source fixes

| File | Issue | Function / Line | Change |
|------|-------|-----------------|--------|
| `admet.py` | #248 | `_safe_write_artifact` | Guard before `path.write_text()` — raises `Refusal` |
| `dossier.py` | #259 | `check_cmd` | Guard before `output_path.write_text()` and `sidecar.write()` — raises `Refusal` |
| `dossier.py` | #261 | `_collect_relays_from_artifact` | Guard before `_read_artifact(meta_path)` — raises `Refusal` |
| `env.py` | #263 | `stamp` | Guard before `envstamp.stamp(home)` — raises `ArtifactError` |
| `hypothesis.py` | #266 | `_archive_verbatim` (adopt helper) | Guard before `verbatim.write_bytes()` and `normalised_path.write_text()` — raises `ArtifactError` |
| `pk.py` | #284 | `_resolve_body_weight` | Guard before `study_path.read_text()` — raises `Refusal` |
| `validate.py` | #283 | `_check_findings_integrity` | Replaced inline `child.is_symlink()` with `is_safe_to_open(child)` for consistency |

### Import additions

- `admet.py`: added `from ..core.paths import is_safe_to_open`
- `dossier.py`: added `from ..core.errors import Refusal` and `from ..core.paths import is_safe_to_open`
- `env.py`: added `from ..core.paths import is_safe_to_open`
- `hypothesis.py`: added `from ..core.paths import is_safe_to_open`
- `pk.py`: added `from ..core.paths import is_safe_to_open`
- `validate.py`: already had the import — no change needed

### Tests

- **New file:** `tests/test_symlink_commands.py` — 18 regression tests (7 test classes, one per fix)
- All tests pass: `python -m pytest tests/test_symlink_commands.py -v`

### Error handling conventions

Each fix follows the existing error-handling pattern in its file:
- `admet.py`, `dossier.py`, `pk.py` — `Refusal` (already used in those files)
- `env.py` — `ArtifactError` (the error class used in that file)
- `hypothesis.py` — `ArtifactError` (used for file-related errors in that file)
- `validate.py` — `continue` (skip pattern, matching lines 1081, 1206, 1478)

## Verification

- `ruff check` — all 7 files pass
- `ruff format --check` — all 7 files pass
- `pytest tests/test_symlink_commands.py -v` — 18/18 pass
