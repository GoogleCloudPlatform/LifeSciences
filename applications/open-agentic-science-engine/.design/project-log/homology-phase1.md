# Homology Phase 1: Structural homology search (#184)

**Date:** 2026-08-20
**Agent:** dev-homology-p1
**Branch:** scion/dev-homology-p1

## Summary

Implemented Phase 1 of the `homology` command group — a BLAST-based structural
homology search against the RCSB PDB, following the `alphafold.py` pattern.

## Changes

### New file: `tools/venter/commands/homology.py`

- `venter homology search <uniprot_id> --range START-END` subcommand
- Fetches canonical sequence from UniProt REST API (QPS: 3.0)
- Validates UniProt accession format before any network request
- Validates `--range` (START ≤ END, both within canonical length)
- Submits domain subsequence to RCSB Search API v2 sequence service (BLAST)
- Fetches structure metadata via RCSB Data API GraphQL (batched single query)
- Determines `is_direct_structure` by checking polymer entity alignments
- Writes `HOMOLOGY-{ACC}-{START}-{END}.search.json` manifest
- Writes `HOMOLOGY-{ACC}-{START}-{END}.meta.json` provenance sidecar
- Handles empty results (204 or empty result_set) gracefully — writes manifest
  with `hit_count: 0`, does not raise
- Supports `--evalue`, `--identity`, `--max-hits`, `--out`, `--json`, `--quiet`

### Modified: `tools/venter/core/thresholds.py`

- Registered `homology@1.0` threshold set with cited provenance:
  - `identity_high`: 0.5 (Chothia & Lesk 1986)
  - `identity_moderate`: 0.3 (Rost 1999 twilight zone)
  - `resolution_high`: 2.5 Å (CCP4 convention)
  - `resolution_low`: 3.5 Å (CCP4 convention)
  - `coverage_minimum`: 0.5 (operational)

### Modified: `tools/venter/core/provenance.py`

- Registered relay code `homology.structure_is_not_target` in `RELAY_CODES`

### Modified: `tools/venter/cli.py`

- Added import and `cli.add_command(homology)` before `enforce_phase_two(cli)`

## Verification gates

- **Syntax check:** All four changed files pass `ast.parse()` — OK
- **Full build/test:** Could not run — no Python environment with dependencies
  (click, requests) available in this container. This is an environment limit,
  not a failed check.
- **Manual code review:** Verified imports, function signatures, and call sites
  match existing patterns in `alphafold.py` and other command modules.

## Not built (explicitly out of scope)

- No SWISS-MODEL or 3D-Beacons integration
- No new artifact class (reuses `structures`)
- No composite confidence score
- No phase 2 (analyze) command
