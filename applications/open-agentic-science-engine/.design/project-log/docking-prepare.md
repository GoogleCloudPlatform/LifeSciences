# docking: add prepare subcommand with Meeko-based PDBQT conversion (#33)

**Date**: 2026-08-19
**Agent**: tools-lead-em-33-dev-prepare
**Phase**: docking prepare (phase 1 of 3)

## What was done

Created the `venter docking` command group with the `prepare` subcommand,
plus all supporting infrastructure changes. This is the first three-phase
tool in the codebase (prepare → run → analyze).

### Files changed

1. **`tools/venter/commands/docking.py`** (new) — Module scaffold with:
   - Module docstring describing the three-phase layout
   - `@click.group() def docking()` with help string
   - `_require_meeko()` — lazy Meeko import with DependencyError
   - `_require_vina()` — PATH check for the `vina` binary
   - `docking prepare` subcommand: reads a PDB structure + pocket record,
     derives grid box from fpocket pocket atom coordinates (10 A padding,
     Eberhardt et al. 2021), converts receptor to PDBQT via Meeko, writes
     three outputs under `raw/docking/`

2. **`tools/requirements-science.txt`** — Added `meeko>=0.5` in the
   molecular chemistry section alongside rdkit.

3. **`tools/venter/core/provenance.py`** — Registered
   `docking.score_is_not_affinity` relay code (for the future `analyze`
   subcommand to emit).

4. **`tools/venter/core/thresholds.py`** — Declared `docking-scores@1.0`
   threshold set with cited sources (Eberhardt et al. 2021, Quiroga &
   Villarreal 2016). `weak_binding_energy` left as UNRESOLVED — no
   defensible citation exists.

5. **`tools/venter/commands/doctor.py`** — Added `meeko` to the optional
   packages check.

6. **`tools/venter/cli.py`** — Imported and registered `docking` command
   before `enforce_phase_two(cli)`.

### Design decisions

- **Sidecar filename**: `{stem}.prepare.meta.json` — distinct from what
  `run` will use (`{stem}.docking.meta.json`), avoiding the compound.py
  round-1 sidecar collision bug.
- **Meeko only**: No OpenBabel path. Meeko is the decided receptor prep
  tool.
- **Grid box padding**: 10 A per side, cited as common Vina practice
  (Eberhardt et al. 2021).
- **ARTIFACT_CLASS = "docking"**: outputs go under `raw/docking/`.

### Verification

- `python3 -m py_compile tools/venter/commands/docking.py` — passes
- `python3 -m py_compile tools/venter/cli.py` — passes
- `python3 -m py_compile tools/venter/core/provenance.py` — passes
- `python3 -m py_compile tools/venter/core/thresholds.py` — passes
- `python3 -m py_compile tools/venter/commands/doctor.py` — passes
- No test suite exists in this repo; verification limited to syntax checks
  and manual code review against the existing patterns.

### What's next

A subsequent developer will add the `run` and `analyze` subcommands to
`docking.py`. The module scaffold, dependency checks, threshold set, and
relay code are ready for them.
