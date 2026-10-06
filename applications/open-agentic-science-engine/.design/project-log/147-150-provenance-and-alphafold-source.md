# Fix: Provenance sidecar scan-and-match (#147) and alphafold source paths (#150)

**Date:** 2026-08-20
**Branch:** `scion/tools-lead-em-147-dev-phase-a`
**Files changed:**
- `tools/venter/commands/validate.py`
- `tools/venter/commands/alphafold.py`

## Bug #147: `_check_provenance_valid` false positives

**Root cause:** `_check_provenance_valid` constructed an expected sidecar
filename as `{artifact_name}.meta.json` (e.g., `GENE.gtex.json.meta.json`).
Real sidecars are shared across multiple output files and use a stem-based
name (e.g., `GENE.meta.json` covering `GENE.gtex.json`). The filename
assumption never matched, producing false "missing sidecar" failures across
all artifact classes.

**Fix:** Replaced the per-artifact filename lookup with a scan-and-match
approach via a new `_build_sidecar_index` helper:
1. For each artifact-class directory, discover all `*.meta.json` files.
2. Read each sidecar's `outputs[]` array; build a `sha256 → sidecar_path` mapping.
3. For each artifact, compute its sha256 and check the index.
4. The index is built once per directory, reading each sidecar exactly once.

The existing exclusion filter in `_find_layer0_artifacts` is correct and
unchanged — it already excludes `*.meta.json` and `*.analysis.json` from
the artifact list.

## Bug #150: alphafold `source` field is unprefixed

**Root cause:** `alphafold analyze` and `alphafold analyze-prediction` wrote
the `source` field in analysis records using bare filenames (e.g.,
`AF-O60443-F1.meta.json`). The validator's `_check_analysis_citations`
resolves `source` relative to the project root, so a bare filename looked
for `<project_root>/AF-O60443-F1.meta.json` instead of
`<project_root>/raw/structures/AF-O60443-F1.meta.json`.

**Fix:** Changed both commands to use `project.relative(path)` (defined in
`core/context.py:79`), producing project-relative paths like
`raw/structures/AF-P04637-F1.meta.json`. This matches the convention used
by genetics, expression, and gtex commands.

## Verification

8 test scenarios pass:
- **#147 positive:** GTEx (1 artifact + shared sidecar), expression (2 artifacts + shared sidecar), structures/AFDB (3 artifacts + shared sidecar)
- **#147 negative:** missing sidecar (no sidecar in directory), sha256 mismatch (sidecar exists but wrong hash)
- **#150 positive:** project-relative source resolves, bare filename fails, AF3 project-relative resolves

All 4 check scripts pass:
- `check_invocations.py` — 0 problems
- `check_artifact_paths.py` — 0 problems
- `check_threshold_names.py` — 0 new problems (2 pre-existing skill citation issues)
- `check_relay_codes.py` — all registered codes emitted, all emitted codes registered

## Noted but not fixed

The `docking analyze` and `pocket analyze` commands write absolute paths in
the `source` field, which creates a portability issue. This is not currently
causing validate failures (the validator checks that the source file exists,
and absolute paths resolve correctly on the machine that wrote them). Not
in scope for this phase.
