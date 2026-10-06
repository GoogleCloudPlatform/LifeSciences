# Review: [finding title]

**Reviewer**: scientific-reviewer | **Date**: [date]
**Finding under review**: [findings/<path>]
**Decision question**: [from brief]
**Verdict**: accept | revise | reject

## Summary

2-3 sentences. The verdict and the reason for it.

## Records Compared

| | Path | written_by | source_sha256 (first 12) |
|---|---|---|---|
| Under audit | | | |
| My re-analysis | | | |

The `written_by` of your re-analysis **must be your own agent slug and
must differ from the `written_by` of the record under audit.** That is the
independence guarantee, and it is checkable by anyone reading this report.

If you omitted `--out` and agreed with the specialist, the tool declined
to write — exit 0 with a `left as it is` warning. Your confirmation is
real but unrecorded. The second row is empty and cannot be filled from the
specialist's record. Re-run with `--out "$OUT"` to produce a citable
record, then fill both rows from the files on disk.

## Re-analysis Diff

| Claim | Cited | Regenerated | Artifact | Match |
|---|---|---|---|---|

Threshold set used: [name@version]. Matches program policy: yes / no / unverified.

## Mechanical Validation

- [ ] Declared deliverables exist in the expected artifact layer
- [ ] Required report headings and work-order reference present
- [ ] Every cited path resolves within the program root
- [ ] Layer 0 outputs have valid provenance sidecars and checksums
- [ ] Each `.analysis.json` cites its source artifact and threshold set
- [ ] Every `mandatory_relays` code addressed in the finding
- [ ] No tool-written byte appears under `findings/`
- [ ] Cited PMIDs, DOIs, NCT numbers resolve to real records

**Evidence intact**: yes, [n] files guarded | ALTERED — review void | VOID — snapshot empty
**Re-analysis written to**: raw/reanalysis/[date]-[slug]/
**Phase-2 coverage**: [n] of [n] commands latched offline, redirectable and non-clobbering
**Prior verdict encountered**: none | exit 9 at [path], written_by [slug]

## Relay Audit

| Code | Obligation | Status | Evidence |
|---|---|---|---|

Status: discharged / mentioned-only / ignored.

## Scientific Audit

Unsupported inference, overclaimed confidence, conflicting evidence.

## Unverified

Every check that could not be performed, and why. Empty section is valid;
omitted section is not.

## Required Changes

For `revise` or `reject`: numbered, specific, each naming the claim and
what would discharge it.

## Pre-Mortem Objections (if applicable)

If this review includes a pre-mortem assessment, list the failure
hypotheses raised during review here. Each objection must reference
the pre-mortem template (`findings/reviews/<finding>-premortem.md`)
where the full hypothesis is documented.

| Objection ID | Description | Speculative? | Resolution |
|---|---|---|---|
| | | yes/no | pending / accepted / rebutted / accepted_risk / unresolved |

Speculative objections (no discriminating check) are recorded but do
not gate progress.
