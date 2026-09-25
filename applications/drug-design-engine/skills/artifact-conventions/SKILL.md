# Artifact Conventions

Standard report format, headings, and linking conventions for all dde project artifacts.

## Project Filesystem

Every project follows this layout:

```
project-<name>/
├── raw/                              # Layer 0: Tool I/O
│   ├── structures/                   # alphafold
│   ├── genomics/                     # alphagenome, genetics
│   ├── hypotheses/                   # coscientist, hypex, adopted
│   ├── literature/                   # litref
│   ├── expression/                   # expression
│   ├── docking/                      # (declared, no tool writes yet)
│   ├── compounds/                    # (declared, no tool writes yet)
│   ├── assays/                       # (declared, no tool writes yet)
│   └── reanalysis/<date>-<agent>/    # Independent re-analysis output
├── findings/                         # Layer 1: Specialist Reports
│   ├── structural-biology/
│   ├── computational-biology/
│   ├── medicinal-chemistry/
│   ├── computational-chemistry/
│   ├── admet-dmpk/
│   ├── experimental-biology/
│   ├── regulatory/
│   └── reviews/<finding>-review.md   # Prose review reports
├── program-state/                    # Layer 2: Orchestrator Working State
│   ├── active-series.md
│   ├── liability-tracker.md
│   ├── decision-log.md
│   └── open-questions.md
├── gates/                            # Layer 3: Stage Gate Documents
│   ├── stage1-target-nomination/
│   ├── stage2-hit-declaration/
│   ├── stage3-candidate-dossier/
│   └── stage4-ind-package/
└── executive/                        # Layer 4: Executive Summary
    └── program-summary.md
```

## Layer 1 — Specialist Finding Format

Every specialist report uses these headings:

```markdown
# [Finding Title]
**Role**: [role-name] | **Date**: [date] | **DMTA Round**: [if applicable]
**Task ref**: [link to orchestrator dispatch or parent finding]

## Summary
2-3 sentence bottom line up front.

## Key Findings
Narrative with inline references to raw data and source tags for numerical values:
'Pocket volume is 420 Å³ {source: raw/structures/target-apo.analysis.json $.pocket_volume}
([raw/structures/target-apo-af3.pdb]).'

## Implications
What this means for the program direction.

## Open Questions
Unresolved items that may require follow-up.

## Caveats & Confidence
Model confidence metrics, experimental limitations, assumptions made.
```

## Linking Conventions

Every factual claim links to its supporting artifact. Three link directions:

- **Vertical (deeper):** `[raw/structures/file.pdb]` — relative paths down to Layer 0 raw data.
- **Lateral (peer findings):** `[findings/medicinal-chemistry/series-a-sar.md]` — cross-references to related specialist work at the same layer.
- **Upward (program state):** `[program-state/active-series.md#series-a]` — anchoring into the orchestrator's decision surface.

**Path resolution differs between source tags and markdown links:**
- Source tags (`{source: raw/...}`) resolve **project-root-relative**.
- Markdown links (`[text](raw/...)`) resolve **file-relative** (standard Markdown behavior).

This means a finding at `findings/computational-biology/assessment.md` that links to `raw/genomics/` must write `[raw/genomics/](../../raw/genomics/)`, but its source tag writes `{source: raw/genomics/tp53.analysis.json $.field}` without the `../../` prefix.

### Source tags for numerical claims

Every numerical value cited from a tool output must carry an inline source tag
that traces the value to its exact location in the source artifact. Path links
(`[path]`) tie a claim to an artifact generally; source tags tie a **specific
value** to the **exact field** that produced it.

**Format:** `{source: <path> <locator>}`

Locators are **required** on all numerical source tags. A tag without a locator (`{source: raw/structures/target-apo.analysis.json}`) is valid only for non-numerical artifact references where the link establishes provenance without claiming a specific value. The validator emits a format warning for numerical tags missing a locator.

The locator identifies where in the artifact the value lives:

- **JSONPath (RFC 9535 subset)** for `.json`, `.analysis.json`, and `.meta.json` files. Supported syntax: `$`, dot and bracket child selectors, single-index array access `[N]`, and slice notation `[start:end]`. Recursive descent (`..`) and filter expressions (`?()`) are not supported — source tags must resolve to exactly one scalar value.
  `{source: raw/structures/target-apo.analysis.json $.pocket_volume}`
- **Line number** for non-JSON text or when field-level precision is unnecessary:
  `{source: raw/structures/target-apo.meta.json:14}`

The locator must resolve to a **scalar value** (string, number, boolean, or null). Tags whose locators resolve to an object or array produce a format warning — the claimed value cannot be tolerance-compared against a non-scalar.

Path and JSONPath locator are space-separated (not colon-separated) to avoid
ambiguity with colons in file paths. Line-number locators use the `:N` suffix
convention.

**Examples in context:**

```markdown
Pocket volume is 420 Å³ {source: raw/structures/target-apo.analysis.json $.pocket_volume}
([raw/structures/target-apo-af3.pdb]).

Conservation score is 0.94 {source: raw/genomics/tp53.analysis.json $.conservation_score},
indicating strong evolutionary constraint.

The top hypothesis scored 8.2 {source: raw/hypotheses/hbv-entry.tournament.json $.rounds[0].winner.score}
across the tournament evaluation.

The search returned 687 results {source: raw/genomics/tp53.meta.json $.metrics.total_results}
across all patent families.
```

`.meta.json` files are valid source-tag targets. When a value is only available in `.meta.json`, it can be tagged directly. Tooling should also surface frequently-cited meta values in the `.analysis.json` `metrics` object for convenience.

A source tag binds to the **immediately preceding numerical value**. When a clause contains multiple numbers from source artifacts, each number carries its own tag. This makes mechanical extraction unambiguous: the validator reads the number immediately before the `{source:` token.

**What must be tagged:**

- Any number cited from a tool output: scores, volumes, distances, counts,
  p-values, confidence metrics, thresholds
- Threshold values when they come from `.analysis.json` or `thresholds.yaml`

**What is not tagged:**

- General quantities from literature (covered by citation-resolution)
- Counts or descriptions that are not derived from a specific tool output
  (e.g., "three binding pockets were identified" — the artifact link suffices)

**Bulk table citations:**
When a table's values derive from a single artifact, use `{source-table: <path> <locator>}` on the line following the table. This asserts provenance for the table as a whole. The locator may resolve to an object or array. Per-cell `{source: ...}` tags are optional and additive — use them for values that warrant individual mechanical verification.

Source tags and path links coexist. A claim can carry both — the path link
points to the artifact, the source tag points to the specific value within it.
The source tag is the one the finding-validator checks mechanically.

### Citing analysis records

`.analysis.json` records carry a `written_by` field (from
`$SCION_AGENT_SLUG`). Since two records can now legitimately describe
the same artifact — the specialist's original and a reviewer's
re-analysis — anything citing an analysis should cite it by `written_by`
and path, not by path alone. A finding that says "the analysis shows..."
without naming whose analysis is ambiguous when a reanalysis exists.

## T-Shaped Liability Handoff

When a specialist identifies a cross-disciplinary liability (e.g., a medicinal chemist identifies a CYP liability, or a structural biologist spots a flexibility issue affecting selectivity), they must:

1. Document it in their own finding under **Implications**.
2. Append a structured entry to `program-state/liability-tracker.md` with:
   - The liability description
   - The originating finding (link)
   - The recommended receiving role(s)
   - Severity assessment (critical / monitor / informational)

This ensures liabilities are not lost between specialist handoffs.

## The Layer 0 / Layer 1 Boundary

**No tool ever writes under `findings/`.** Layer 0 (`raw/`) holds tool
output — deterministic, reproducible, no judgment. Layer 1 (`findings/`)
holds specialist prose that adds judgment, interpretation, and
cross-disciplinary implications. If a finding is a reformatted
`.analysis.json` it has added no judgment and defeats the layer
separation. The boundary is defined by the kind of content, not by who
produced it.

## Raw Data Conventions (Layer 0)

- Tool outputs go to `raw/<category>/` — never inline large data in findings.
- Name files descriptively: `target-apo-af3.pdb`, not `output.pdb`.
- Include the tool version and parameters in a sidecar `<filename>.meta.json` when available.
- MCP tool responses should be summaries with pointers to full data on disk, not the full data itself (zero-bloat principle).

### Independent re-analysis (`raw/reanalysis/`)

When an agent re-runs a phase-2 `analyze` command over evidence it did
not produce — a reviewer auditing a specialist's finding is the live
case — the output goes under `raw/reanalysis/<date>-<agent>/`, **not**
beside the original artifact.

The default `analyze` writes its `.analysis.json` beside the artifact
it read. A bare re-run therefore overwrites the record it was sent to
check, and the auditor compares its own output against itself. The
`--out` flag redirects the output. Analyzers come in two shapes:

    OUT=raw/reanalysis/$(date +%F)-$SCION_AGENT_SLUG

    # Identifier-based (genetics, expression, litref, alphafold analyze):
    dde genetics analyze TP53 --out "$OUT"

    # Path-based (coscientist, alphagenome, alphafold analyze-prediction):
    dde coscientist analyze raw/hypotheses/<stem>.tournament.json --out "$OUT"

Run `dde <tool> analyze --help` to confirm which shape a command
uses. `$SCION_AGENT_SLUG` is set in the agent environment. Use it in
preference to a literal `<agent>` placeholder.

This path is under `raw/` because the content is tool output —
deterministic, no judgment. It is named `reanalysis` rather than
`review` or `audit` because the name describes the bytes, not the role
of whoever produced them. Partitioned by `<date>-<agent>` because the
date alone is an attribute two independent auditors share.

## Review Reports (`findings/reviews/`)

Reviewer prose goes under `findings/reviews/<finding-name>-review.md`.
This directory holds **prose only** — no tool writes here. A review
report follows the same Layer 1 format as any specialist finding, but
its vertical links point at both the original finding under review and
the re-analysis artifacts under `raw/reanalysis/`.

## Report Quality Standards

- Reports are professional-grade scientific deliverables, not chat messages.
- Use quantitative evidence: numbers, confidence intervals, p-values, scores.
- Distinguish between measured values and model predictions.
- State units explicitly.
- Acknowledge limitations and alternative interpretations.

## Addressing Mandatory Relays

When a finding addresses a mandatory relay, use the label format `**Relay: \`<code>\`**` followed by the substantive response. Every mandatory relay from the work order's artifacts must be addressed with this label.

```markdown
**Relay: `compound.alerts_not_toxicology`**
The structural alerts identified (PAINS pattern A) are pharmacological mechanism-related
rather than toxicological. This is supported by the target's known SAR...
```
