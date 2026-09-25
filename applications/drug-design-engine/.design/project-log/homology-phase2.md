# Homology Phase 2 — `homology analyze` subcommand

**Date:** 2026-08-20
**Branch:** `scion/tools-lead-em-184`
**Issue:** #184

## What was done

Added the `analyze` subcommand to `tools/venter/commands/homology.py`.
This is the Phase 2 (offline analysis) counterpart to the existing
`homology search` command.

### CLI surface

```
venter homology analyze <uniprot_id_or_path> [--identity-high] [--identity-moderate] [--from DIR] [--out DIR] [--json] [--quiet]
```

### Execution flow

1. Resolve search manifest by accession stem or explicit path
2. Load the `homology@1.0` threshold set with optional flag overrides
3. Classify each hit on three axes:
   - **Identity band:** high (≥0.5), moderate (≥0.3), remote (<0.3)
   - **Resolution quality:** high (≤2.5Å), moderate (≤3.5Å), low (>3.5Å), not-applicable (None)
   - **Coverage:** adequate (≥0.5), insufficient (<0.5)
4. Compute overall verdict: strong-candidates, moderate-candidates, remote-only, no-coverage, no-hits
5. Emit `homology.structure_is_not_target` relay when any hit has `is_direct_structure: false`
6. Write `.homology.analysis.json` via `provenance.write_analysis()`

### Design decisions

- **Verdict priority:** `moderate-candidates` takes precedence over `no-coverage` when
  hits have high/moderate identity but inadequate coverage. This matches the brief's
  intent — high-identity hits are always worth flagging even without full coverage.
- **source field:** Points to `.meta.json` sidecar (not `.search.json`) per design doc schema.
- **No composite score:** Identity and resolution stay as independent axes per spec.

## Files changed

- `tools/venter/commands/homology.py` — added `analyze` subcommand and classification helpers
- `tests/test_homology.py` — added 17 Phase 2 tests covering all classification axes, verdicts, and relay logic

## Verification

- Syntax verification: both files pass `ast.parse()`
- Test runner could not execute due to missing `click` module in the container (no pip available)
- Code reviewed against the alphafold analyze pattern for structural consistency
