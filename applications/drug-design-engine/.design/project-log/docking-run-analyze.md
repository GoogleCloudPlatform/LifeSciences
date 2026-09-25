# docking: add run and analyze subcommands

**Date:** 2026-08-19
**Agent:** tools-lead-em-33-dev-run-analyze
**Issue:** #33

## What was done

Added `docking run` and `docking analyze` subcommands to
`tools/venter/commands/docking.py`, completing the three-phase docking
pipeline (prepare → run → analyze).

### `docking run` (Phase 1)

- Executes AutoDock Vina subprocess against a prepared receptor PDBQT
  and one or more ligand files (PDBQT or SDF format).
- SDF ligands are converted to PDBQT via lazy-imported RDKit + Meeko.
- Reads grid box parameters (center, size) from the JSON produced by
  `docking prepare`.
- Parses Vina's `REMARK VINA RESULT:` lines to extract per-pose
  affinity scores, rmsd_lb, rmsd_ub.
- Writes per-ligand:
  - `{stem}.docking_result.json` — scores and pose list
  - `{stem}.poses.pdbqt` — Vina output poses
  - `{stem}.docking.meta.json` — provenance sidecar (DISTINCT from
    prepare's `{stem}.prepare.meta.json`)
- Error handling: non-zero Vina exit, unparseable SDF, no poses
  produced — all raise `ArtifactError` with clear remedy.

### `docking analyze` (Phase 2)

- Reads stored docking result JSON and applies `docking-scores`
  threshold set.
- Classifies every pose by score band (strong / moderate / weak /
  unclassified), reporting ALL ranked poses per the "never rank and
  pick" philosophy.
- Handles UNRESOLVED `weak_binding_energy` gracefully: catches
  `ThresholdError`, classifies as "unclassified" rather than raising,
  and emits an advisory.
- Determines overall verdict: `strong-binders-found`,
  `moderate-binders-found`, or `no-significant-binding`.
- Emits `docking.score_is_not_affinity` relay conditionally — only
  when favorable (strong or moderate) scores are present.
- Collects upstream relays from the phase-1 sidecar.
- Writes `.docking.analysis.json` via `provenance.write_analysis()`.
- Genuinely offline: no Meeko, Vina, or RDKit imports in the analyze
  code path. The phase-2 latch enforces the network ban.

### Other changes

- Updated module docstring: removed "(future)" from run and analyze
  descriptions.
- Added helper functions: `_vina_version()`, `_convert_ligand_to_pdbqt()`,
  `_parse_vina_poses()`.
- Added imports: `subprocess`, `tempfile`, `beside_or_out`, `from_option`,
  `load_thresholds`, `ThresholdError`.

## Verification

- `python3 -m py_compile tools/venter/commands/docking.py` passes.
- Cannot run full integration tests (Vina binary, Meeko, RDKit not
  installed in this environment). The code follows the exact same
  patterns as `pocket.py` (run + analyze) and `compound.py` (analyze
  with thresholds + relays).
