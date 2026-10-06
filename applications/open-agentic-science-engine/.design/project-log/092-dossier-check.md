# 092 — dossier check subcommand for IND Module 4 readiness

**Date:** 2026-08-19
**Agent:** tools-lead-em-92-dev-1

## What was implemented

New `venter dossier check` command — a read-only IND Module 4
evidence-package readiness checker that scans the project tree and
reports which required nonclinical study types have supporting evidence.

### Deliverables

1. **`tools/venter/commands/dossier.py`** — new module containing:
   - Click group `dossier` (using VenterGroup)
   - `check` subcommand with `--json` and `--quiet` via `output_options`
   - IND Module 4 checklist constant covering 9 required study types:
     pharmacology (primary, secondary, safety), pharmacokinetics (ADME,
     DDI), and toxicology (repeat-dose, safety pharm, genotoxicity,
     reproductive)
   - Symlink confinement via `_confine_path()` (mirrors site.py pattern)
   - Evidence matching with three-way classification: covered (finding +
     artifact), finding_only (finding without artifact), missing
   - Output to `gates/dossier-check/` with provenance sidecar
   - `scope_caveat` field always present in both report JSON and sidecar

2. **`tools/venter/cli.py`** — registered `dossier` import and
   `cli.add_command(dossier)` before `enforce_phase_two(cli)`

### Design decisions

- **scope_caveat is NOT a relay code.** The design doc originally
  proposed `dossier.checklist_not_submission` as a relay, but the caveat
  is true on every invocation regardless of result, which makes it a
  standing property of the tool, not a conditional finding. It goes
  directly in the JSON output as a string field and is also recorded in
  the sidecar via `sidecar.note()`.

- **Keyword-based evidence matching.** Findings are matched to checklist
  sections by directory structure under `findings/` and keyword presence
  in filenames, headings, and content. Artifacts in `raw/` are matched
  by domain (assays for pharmacology, pk for PK sections, tox for
  toxicology sections). This is intentionally heuristic — a more precise
  approach would require structured metadata in findings.

- **Jurisdiction configurability deferred.** A comment notes that the
  checklist could be loaded from external config per jurisdiction (EMA,
  PMDA), but this is not built — the hardcoded FDA IND checklist is the
  only implementation.

### Verification

- Live test: Created a project with `venter init`, populated findings
  and artifacts for some but not all checklist sections, ran
  `venter dossier check --json` and confirmed correct three-way
  classification (3 covered, 2 finding_only, 4 missing).
- `check_invocations.py`: 0 problems (158 invocations checked)
- `check_artifact_paths.py`: 0 problems
- `check_threshold_names.py`: 1 pre-existing problem (unrelated)
- `check_relay_codes.py`: clean pass — confirms no dossier relay code
  registered, 0 unregistered codes found
- Confirmed read-only: no files created in `findings/` or `raw/`
