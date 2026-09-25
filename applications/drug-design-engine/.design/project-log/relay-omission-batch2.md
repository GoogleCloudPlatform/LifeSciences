# Relay Omission Batch 2 — Silent Safety Relay Fixes

**Date**: 2026-09-18
**Branch**: `fix/relay-omission-batch2`
**Issues**: #191, #198, #205, #216

## Summary

Fixed four silent relay omission bugs where mandatory safety relays were
dropped without error, violating tool-design-guidance §8 ("fail loudly,
never degrade silently").

## Fixes

### #191 — docking.py: prepare sidecar lookup by wrong stem

**Root cause**: `analyze_cmd` derived `stem` from the docking result
filename as `{ligand}_{receptor_id}`, then looked for the prepare sidecar
at `{stem}.prepare.meta.json`. But `prepare_cmd` writes the sidecar as
`{receptor_id}.prepare.meta.json` (keyed on the structure stem). The
mismatch caused silent lookup failure — all upstream relays (pocket quality
alerts, conformation dependence, peptide occlusion) were dropped.

**Fix**: Extract `receptor_id` from `result_doc.get("receptor_id")` and
use it for the prepare sidecar lookup. Log a warning when the prepare
sidecar is missing.

### #198 — litref.py: missing response files treated as verified negative

**Root cause**: `analyze_cmd` iterated over response files with
`if not path.is_file(): continue`. When no response files existed, `totals`
stayed empty, `total_hits = 0`, and the command concluded `outcome =
"not_found"` with exit 0 — silently converting missing files into a
verified scientific finding that publications don't exist.

**Fix**: After the response file loop, check `if not totals` and raise
`ArtifactError` with a clear message directing the user to run
`dde litref resolve` first.

### #205 — selectivity.py: string "false" suppresses panel_incomplete relay

**Root cause**: `_validate_panel` did not validate the type of
`panel_complete`. When input contained `"panel_complete": "false"` (string,
not boolean), Python treated the non-empty string as truthy:
`not panel_complete` evaluated to `False`, silently suppressing the
`selectivity.panel_incomplete` relay.

**Fix**: (1) Added type validation in `_validate_panel` — if
`panel_complete` is present but not a boolean, report a validation problem.
(2) Changed the relay guard from `if not panel_complete:` to
`if panel_complete is not True:` for explicit boolean truth checking.

### #216 — tox.py: clinical PK sidecar relays dropped

**Root cause**: In `margins_cmd`, step 6 only read and forwarded
`mandatory_relays` from the animal PK sidecar (`pk_path`). Clinical PK
sidecar relays (e.g., `pk.single_species_scaling`) were completely ignored.
Additionally, the sidecar filename was computed via brittle string
replacement (`.pk-nca.json` → `.pk-nca.meta.json`) which could fail
silently on non-standard suffixes.

**Fix**: (1) Extracted sidecar forwarding into `_forward_pk_relays()`
helper. (2) Call it for both `pk_path` (animal) and `clinical_pk_path`
(clinical). (3) Replaced brittle `.replace()` with `Path.with_suffix()`
for robust sidecar resolution. (4) Log warnings when sidecars are missing
or unreadable.

## Tests

Added `test_relay_omission_batch2.py` with 5 regression tests:

1. `test_docking_analyze_finds_prepare_sidecar_by_receptor_id` — verifies
   prepare sidecar relays are forwarded via receptor_id lookup
2. `test_litref_analyze_raises_on_missing_response_files` — verifies
   ArtifactError instead of silent NOT_FOUND
3. `test_selectivity_compare_rejects_string_panel_complete` — verifies
   validation rejects non-boolean panel_complete
4. `test_selectivity_analyze_fires_relay_when_panel_complete_not_true` —
   verifies relay fires when panel_complete is False
5. `test_tox_margins_forwards_both_pk_sidecar_relays` — verifies both
   animal and clinical PK relays appear in output

## Verification

- All 5 new tests pass
- All 9 existing `test_pk_relay_fix.py` tests pass (no regressions)
- `ruff check` clean on all modified files
- `ruff format --check` clean on all modified files
