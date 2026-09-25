# Validation Conventions Cluster (#100-105, #109)

**Date**: 2026-09-09
**Issues**: #100, #101, #102, #103, #104, #105, #109
**Branch**: `scion/val-em`

## Summary

Implemented seven interrelated issues forming the "Validation Convention Cluster"
for the DDE mechanical validator. The work was executed in three sequential phases,
each independently reviewed by code-reviewer, test-engineer, and security-auditor
agents before integration.

## What Was Implemented

### Phase 1: Severity Model + Convention Text (#101, #105)

1. **Two-axis severity model** — Every mechanical check now returns both a
   `status` field (ok / warn / fail / skip) and a `kind` field (FORMAT /
   DATA_INTEGRITY / COMPLETENESS / CONVENTION / None). The legacy `result`
   field is preserved for backward compatibility.

2. **`_overall_verdict` helper** — Aggregates per-check statuses into a single
   validation verdict: all-fail → fail, any-warn → pass_with_warnings,
   all-ok → pass, all-skip → fail (vacuous).

3. **`pass_with_warnings` state transition** — The `workorder accept` command
   now treats `pass_with_warnings` identically to `pass`, transitioning the
   work order to `mechanically_validated`.

4. **Convention documentation** — Updated `artifact-conventions/SKILL.md` with
   path resolution rules, RFC 9535 JSONPath subset, scalar resolution rule,
   binding semantics, `.meta.json` examples, bulk table citations, and relay
   label convention.

### Phase 2: Source-Tags-Resolve Check (#100)

5. **`_check_source_tags_resolve`** — 9th mechanical check (~430 lines).
   Parses `{source: <path> <locator>}` and `{source-table: <path> <locator>}`
   tags from Layer 1 reports, resolves paths against the project root, evaluates
   JSONPath and line-number locators against the referenced files, and compares
   claimed values with ±1% relative tolerance.

   - Uses `jsonpath-ng` for JSONPath evaluation (new dependency)
   - Code-fence stripping preserves character positions
   - 50 MB file-size guard prevents memory exhaustion
   - `_confine_path` hardened against null bytes and symlink loops
   - Graceful degradation: returns `skip` if `jsonpath-ng` is not installed

### Phase 3: Four Improvements (#102, #103, #104, #109)

6. **Report headings advisory severity (#102)** — `_check_report_headings` now
   detects heading-text variance (e.g., `## Per-Target Assessments` instead of
   `## Key Findings`) between Summary and Implications sections. Present
   variants produce warn/CONVENTION; absent sections produce fail/COMPLETENESS.

7. **Required/authorized classes split (#103)** — `normalize_deliverables`
   handles the new `required_classes` / `authorized_classes` schema with full
   backward compatibility. `layer_0_classes` is always populated from
   `required_classes` for downstream checks. `not_applicable` dicts are
   preserved in `required_classes` but class names are still flattened to
   `layer_0_classes`. Three legacy key forms (`required_classes`,
   `layer_0_classes`, `layer_0`) all normalize correctly.

8. **Relay label format checking (#104)** — `_check_relay_coverage` now
   distinguishes between relay codes found in the standard `**Relay: \`code\`**`
   label format (ok) and codes found only in plain text (warn/FORMAT). Missing
   codes remain fail/COMPLETENESS. Extracted `_collect_relay_codes` helper for
   testability.

9. **Root-resolvable path hints (#109)** — `_check_paths_resolve` detects
   broken markdown links that would resolve from the project root and provides
   a `suggested_fix` relative path. Root-resolvable links produce
   warn/CONVENTION; truly broken links remain fail/DATA_INTEGRITY.

## ENV_VERSION Partition

Adding the `jsonpath-ng` dependency to `requirements.txt` changes the
environment manifest hash (`ENV_VERSION`). This hash is computed from
`env-manifest.txt`, which records every installed Python distribution. The
`ENV_VERSION` value is stamped into every analysis record at creation time.

**Impact**: Pre-existing analyses produced before this merge carry the old
`ENV_VERSION` hash. After this merge, any re-analysis attempt will detect an
`env_version` mismatch and produce a re-analysis CONFLICT (exit code 9).

**This is expected behavior, not a regression.** The `env_version` comparison
exists to detect real environment drift — excluding it from comparison would
hide genuine differences in the analysis environment. Operators encountering
`env_version`-only conflicts after this merge should recognize them as the
expected partition boundary caused by the `jsonpath-ng` addition.

No mitigation is needed beyond awareness. The conflict is a one-time boundary
at the point of deployment; all analyses created after the merge will carry the
new hash and compare consistently.

## Verification

### Phase 1
- Code review: APPROVE
- Tests: 31/31 passed (27 existing + 4 new `_overall_verdict` tests)
- Security: 0 Critical/High

### Phase 2
- Code review: APPROVE (4 findings fixed: import guard, path hardening,
  file-size guard, line-locator tests)
- Tests: 54/54 passed (27 existing + 27 new source-tag tests)
- Security: 0 Critical/High

### Phase 3
- Code review: APPROVE (1 comment fix: misleading authorized_classes comment)
- Tests: 72/72 passed (54 existing + 18 new phase-3 tests)
- Security: 0 Critical/High, 1 Medium (not_applicable vacuous pass — noted
  for follow-up)

## Files Changed

- `tools/dde/commands/validate.py` — all 9 checks, severity model, overall verdict
- `tools/dde/core/controlstore.py` — `normalize_deliverables` rewrite for classes split
- `tools/dde/commands/workorder.py` — `pass_with_warnings` handling in accept
- `tools/pyproject.toml` — `jsonpath-ng>=1.6` dependency
- `tools/requirements.txt` — `jsonpath-ng>=1.6` dependency
- `skills/artifact-conventions/SKILL.md` — convention documentation
- `tests/test_validate_provenance_and_alphafold_source.py` — updated assertions
- `tests/test_validate_source_tags.py` (new) — 27 source-tag tests
- `tests/test_validate_phase3.py` (new) — 18 phase-3 tests
- `.design/project-log/2026-09-09-validation-conventions.md` (this file)
