# #166 — Work-order scoping for validate.py

## What changed

Added `work_order_id` tagging to provenance records and scoped
`_check_provenance_valid` and `_check_relay_coverage` to a work order's
own artifacts, eliminating false positives in shared workspaces where
multiple work orders write into the same artifact directory.

### `core/provenance.py`

- Added `_work_order()` helper that reads `VENTER_WORK_ORDER_ID` from
  the environment (follows the `_writer()` pattern).
- `Sidecar.to_dict()` now includes `"work_order_id"` in every sidecar
  record.
- `write_analysis()` now includes `"work_order_id"` in every analysis
  record.

### `commands/validate.py`

- `_build_sidecar_index()` accepts `wo_id` and returns a third value
  `other_wo_hashes` — the set of sha256 values belonging to other work
  orders. Sidecars from other WOs are excluded from the primary index
  but their hashes are tracked so those artifacts can be silently
  skipped rather than flagged as missing provenance.
- `_check_provenance_valid()` skips artifacts whose sha256 appears in
  `other_wo_hashes`, so only the current WO's artifacts are validated.
- `_check_relay_coverage()` skips relay codes from records tagged with a
  different work order. Also fixed the file-extension filter to use
  `_is_sidecar()` / `_is_analysis()` helpers for consistency with Phase
  A (catches `.sc-meta.json` and `.sc-analysis.json` variants).
- `check_cmd()` passes the resolved `wo_id` to both scoped checks.

## Backward compatibility

The WO filter logic is:
`if wo_id is not None and record_wo is not None and record_wo != wo_id: skip`

This preserves backward compatibility in three cases:

1. **Old sidecars (no `work_order_id` field):** `record_wo` is `None` →
   NOT skipped → included in validation (same as current behavior).
2. **Tools run outside WO context:** `VENTER_WORK_ORDER_ID` unset →
   `_work_order()` returns `None` → field is `None` → no scoping occurs.
3. **No schema version bump needed:** The field is additive and optional.

## Environment variable convention

`VENTER_WORK_ORDER_ID` — set by the phase-1/phase-2 wrapper when running
under a work order context. When absent, provenance records are untagged
and validation runs unscoped (full-directory scan, same as before).
