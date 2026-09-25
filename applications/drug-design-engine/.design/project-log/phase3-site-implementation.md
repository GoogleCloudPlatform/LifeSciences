# Phase 3: Site Build Implementation (Issue #22)

**Date:** 2026-08-19
**Agent:** tools-lead-em-22p3-dev-site-1
**Branch:** scion/tools-lead-em-22p3

## Summary

Implemented `venter site build` and `venter site validate` commands — Phase 3 of the control plane CLI (issue #22). These commands build a deterministic static HTML site from scientifically accepted work-order deliverables.

## Deliverables

### New Files
- `tools/venter/commands/site.py` — `site build` and `site validate` subcommands
- `tools/venter/site_templates/base.html` — base HTML template with navigation
- `tools/venter/site_templates/index.html` — index page with layer navigation
- `tools/venter/site_templates/finding.html` — template for Layer 1 finding pages
- `tools/venter/site_templates/artifact.html` — template for Layer 0 artifact class pages

### Modified Files
- `tools/venter/core/controlstore.py` — added `read_publish_state()` and `write_publish_state()`
- `tools/venter/cli.py` — registered `site` command group
- `tools/venter/core/env.py` — bumped `CLI_VERSION` to `0.3.0`
- `tools/check_invocations.py` — removed `site` from `PLANNED_BUT_UNIMPLEMENTED`
- `tools/requirements.txt` — added `jinja2>=3.1` dependency

## Design Decisions

1. **Determinism:** No wall-clock timestamps in rendered output. The `last_build_at` in publish-state uses the latest artifact timestamp, not `datetime.now()`. All iterations are sorted deterministically. Verified with back-to-back builds producing identical output.

2. **Atomic write:** Site output goes to a `tempfile.mkdtemp()` in the same parent as the target, then `shutil.move()` into place on success. On any failure, the temp dir is cleaned up and no partial `_site/` is left behind.

3. **Path confinement:** All paths resolved during artifact graph traversal are confined to the project root using the same `_confine_path` pattern from `validate.py`. Symlinks targeting outside the project are silently skipped.

4. **Filtering:** Only `scientifically_accepted` work-order revisions appear. Draft, rejected, and mechanically invalid artifacts are excluded.

5. **Layer navigation:** The index page follows the hierarchy from venter-plan.md §5.5: Executive Summary → Program State → Specialist Findings → Raw Data.

6. **Jinja2 autoescaping:** Enabled for HTML templates to prevent XSS in rendered content.

## Verification

- All four checker scripts pass (the one pre-existing `raw/pockets/` issue in `check_artifact_paths.py` is unrelated)
- Integration tests: build, validate, --json, --quiet modes all work
- Failure cases: broken links abort with exit 3, path escapes detected, atomic failure verified (no partial output)
- Determinism: back-to-back builds produce identical output
- CLI version bump to 0.3.0 reflects across all outputs

## Gates Not Run

- No existing test suite for the venter CLI was found to run against. Verification was done via integration testing with CliRunner.
