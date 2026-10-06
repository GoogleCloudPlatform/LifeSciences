# Docking: fix receptor PDBQT conversion (#33)

**Date:** 2026-08-19
**Agent:** tools-lead-em-33-dev-fix-receptor

## Problem

`_convert_receptor_to_pdbqt()` in `docking.py` imported `meeko.PDBMoleculeSetup`,
which does not exist in meeko 0.7.1 (the current release satisfying `meeko>=0.5`).
The `prepare` subcommand crashed on first invocation with `ImportError`.

## Solution

Rewrote receptor conversion to use `mk_prepare_receptor.py` as a subprocess,
which is meeko's documented modern entry point for receptor PDBQT conversion.
This matches the codebase's existing subprocess pattern (fpocket in pocket.py,
Vina in docking.py).

## Changes

### `tools/venter/commands/docking.py`

1. **`_require_meeko()`** — now returns only `MoleculePreparation` (not a tuple
   with `PDBMoleculeSetup`). The detail/remedy text updated to reflect ligand-only
   usage.

2. **`_require_mk_prepare_receptor()`** — new function following the `_require_vina()`
   pattern: checks `shutil.which("mk_prepare_receptor.py")` and raises
   `DependencyError` if absent.

3. **`_convert_receptor_to_pdbqt(pdb_path, output_path)`** — signature changed from
   `(Path) -> str` to `(Path, Path) -> None`. Now runs `mk_prepare_receptor.py
   --read_pdb <input> -p <output>` via `subprocess.run()`. Error handling follows
   the fpocket subprocess pattern (check returncode, check output file existence).

4. **`prepare_cmd()`** — updated call site: defines `receptor_path` before
   conversion, passes it as `output_path`, removed `pdbqt_string` variable and
   `.write_text()` call.

5. **`_convert_ligand_to_pdbqt()`** — updated `_require_meeko()` call from tuple
   unpacking to single-value assignment.

### `tools/venter/commands/doctor.py`

6. **meeko description** — updated from `"receptor/ligand PDBQT preparation for
   docking"` to `"ligand PDBQT preparation and receptor preparation
   (mk_prepare_receptor.py) for docking"`.

## Verification

- `python3 -m py_compile tools/venter/commands/docking.py` — passes
- `python3 -m py_compile tools/venter/commands/doctor.py` — passes
- `grep PDBMoleculeSetup docking.py` — no matches
- `which mk_prepare_receptor.py` — found in provisioned environment
- `python3 -c "from meeko import MoleculePreparation"` — succeeds
