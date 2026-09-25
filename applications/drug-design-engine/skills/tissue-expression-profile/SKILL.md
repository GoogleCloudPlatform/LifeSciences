---
name: tissue-expression-profile
description: >
  Retrieve measured human RNA expression across tissues and classify
  tissue specificity for a gene. Use when checking whether a drug target
  is expressed in the tissue of interest, whether off-target expression
  creates a safety signal, or when establishing tissue-level expression
  context. Do not use for predicted regulatory variant effects (use
  regulatory-variant-effect), protein structure confidence (use
  protein-structure-confidence), genetic constraint evidence (use
  target-genetic-evidence), or cell-type-resolved expression — this tool
  returns tissue-level averages only.
---

## 1. When to use, and when not

Use this skill when you need measured tissue-level RNA expression data
for a human gene. Entry points include:

- Checking whether a drug target is expressed in the tissue of
  interest — or whether it is not detected.
- Establishing a gene's tissue specificity profile as part of target
  validation or selectivity arguments.
- Looking for off-target expression in safety-relevant tissues.
- Confirming that a gene is not detected in a tissue — with the caveat
  that this is one dataset's cutoff, not proof of absence.

**Do not use when:**

- You need predicted regulatory effects of a variant
  -> `regulatory-variant-effect`.
- You need protein structure or fold confidence
  -> `protein-structure-confidence`.
- You need genetic constraint or loss-of-function intolerance
  -> `target-genetic-evidence`.
- You need cell-type-resolved expression — this tool returns
  tissue-level averages only. When HPA holds single-cell data, the
  relay says so but the tool cannot fetch it.

## 2. Preconditions

- **Gene input**: a gene symbol (e.g. `TP53`) or an Ensembl gene ID
  (e.g. `ENSG00000141510`). Symbols that match more than one HPA gene
  are refused rather than resolved — the tool will not choose between
  WEE1 and WEE2.
- **Tissue filter** (optional): one or more `--tissue` arguments for
  `analyze`. Omit for the full 50-tissue profile. Unknown tissue names
  fail rather than resolving to nothing — HPA silently drops
  unrecognised column codes, and the tool guards against that.
- **Tissue aliases**: plausible names that HPA does not accept are
  mapped automatically (e.g. `skin` -> `skin 1`, `stomach` ->
  `stomach 1`, `heart` -> `heart muscle`, `pituitary` ->
  `pituitary gland`). Run `dde expression tissues` for the full
  list of 50 consensus tissues and their aliases.
- **No authentication** needed — HPA is a public API (CC BY 4.0).
- Run `dde doctor` before first use. It ends with a verdict line:
  `STOP` means fix or report before running anything; `PROCEED` means
  work, and the grouped warnings tell you which commands would refuse,
  which results need careful reading, and which are the tooling lead's
  to clear. Do not judge by the warning count; the verdict line grades
  them for you.

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| What is this gene's measured expression across tissues? | `dde expression fetch <GENE>` | `raw/expression/<ENSG>.hpa.json`<br>`raw/expression/<ENSG>.tissue.json`<br>`raw/expression/<ENSG>.meta.json` |
| Is this gene expressed in tissue X, and what is its specificity? | `dde expression analyze <GENE> [--tissue T ...] [--expressed-ntpm V] [--enriched-fold V]` | `raw/expression/<ENSG>.analysis.json` |
| Which tissues does this tool measure? | `dde expression tissues` | *(stdout only, no network)* |

Run `fetch` before `analyze`. `analyze` reads from disk and applies the
`expression` threshold set. It can be re-run with different thresholds
without re-querying HPA.

The `.hpa.json` is the per-gene annotation record; the `.tissue.json`
carries the full 50-tissue consensus nTPM profile. Both are stored as
the exact bytes received. The sidecar records both the payload SHA-256
(always) and the HPA release label (scraped from `/about/download` when
obtainable). Cite the release version when it is present in the sidecar;
cite the SHA-256 when `release_version_unknown` fires.

All output options: `--json` for machine-readable output, `--quiet` for
paths only, `--out` to override the default output directory.

## 4. Interpretation contract

### Per-tissue verdicts

`analyze` applies the `expression` threshold set and emits one verdict
per tissue queried:

- **expressed** — nTPM at or above the configured `expressed_ntpm`
  threshold.
- **not_detected** — nTPM below the threshold. This is a measurement
  below a cutoff in one consensus bulk RNA dataset, not proof that the
  gene is absent from the tissue.

### Specificity classification

`analyze` also computes one overall specificity class:

- **tissue enriched** — expression in one tissue is at least
  `enriched_fold` times higher than in any other tissue.
- **tissue enhanced** — expression in one tissue is at least
  `enriched_fold` times higher than the mean of all others.
- **low tissue specificity** — detected but not concentrated in any
  single tissue.
- **not detected** — below the expression cutoff in all 50 tissues.

The recomputed class is checked against HPA's own curated label shipped
in the same record. A disagreement is reported in the analysis warnings
— HPA's classifier uses the full expression matrix while this one uses
the 50 consensus tissues, so minor discrepancies are expected and are
not errors.

### Unresolved threshold

`low_confidence_ntpm` is **UNRESOLVED** — HPA publishes no near-threshold
confidence band. A gene sitting exactly at the cutoff is reported as
`expressed` with no margin, and the analysis says so in its warnings.
Do not invent a margin.

### Mandatory relays

The following relay codes mark obligations on the finding. Each must be
satisfied — not merely disclosed — in any Layer 1 finding.

All four relays are **qualifiers** — the obligation is to scope the
result, not to stop reporting it. Three fire on conditions in the data
(resolution limits, provenance gaps): `tissue_resolution_only`,
`single_cell_unavailable`, and `release_version_unknown`. The fourth,
`absent_is_not_evidence`, fires on the verdict itself — a not_detected
result is a legitimate answer, and the relay guards its most available
wrong reading. An "expressed" verdict on a gene with single-cell data
and a scraped release version may produce only `tissue_resolution_only`
(standing for most genes). Nothing has been said about whether nTPM
constitutes protein abundance or whether an "expressed" verdict
constitutes target validation. nTPM is an RNA measurement; protein
level is a different quantity, and expression in a tissue does not by
itself validate a drug target.

Exactly one of the first two fires on every analysis. The third fires
whenever any tissue returns `not_detected`. The fourth fires when the
HPA release version could not be scraped.

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `expression.tissue_resolution_only` | Qualifier | HPA holds single-cell RNA data for this gene (conditional; exactly one of this pair fires) | Scope the claim to whole-tissue averages and say so. Do not infer anything about a cell population from a tissue mean — HPA holds cell-resolved data for this gene that was not used, so the ecological fallacy is live rather than hypothetical. |
| `expression.single_cell_unavailable` | Qualifier | HPA holds no single-cell data for this gene (conditional; exactly one of this pair fires) | State that no cell-resolved data exists, so the tissue-average verdict is the best obtainable and cannot be refined by fetching more. |
| `expression.absent_is_not_evidence` | Qualifier | Any tissue has a not_detected verdict (conditional) | Write the negative as "not detected above the cutoff in HPA bulk consensus", naming the dataset. Do not write that the gene is absent from the tissue — one dataset cannot support that claim. |
| `expression.release_version_unknown` | Qualifier | HPA release label could not be scraped (conditional) | Cite the artifact by its payload SHA-256, not by an HPA release number. Any version stated in the finding would be invented. |

Check `mandatory_relays` in both the `.analysis.json` and the
`.meta.json`. Every relay code present must be satisfied in the finding.

### The ecological fallacy

When `tissue_resolution_only` fires, the tissue nTPM is a weighted
average over all cell types in that tissue. A gene at 30 nTPM in
testis may be expressed in a few cell types and absent from the rest.
Inferring expression in a specific cell population from a tissue average
is the ecological fallacy — and because HPA holds single-cell data for
most genes, this gap is concrete rather than theoretical. The relay
requires **scoping the claim to tissue level and refusing the
cell-population inference**, not merely acknowledging the limitation.
This is the failure the tournament reviewer raised.

### Consequence rules

- **Expressed**: state the tissue, the nTPM value, and the specificity
  class. If the gene is tissue-enriched, name the enriched tissue.
- **Not detected**: state clearly that this is below the detection
  cutoff in one consensus dataset, not proof of absence. Satisfy the
  `absent_is_not_evidence` relay.
- **Near-threshold values**: because `low_confidence_ntpm` is
  UNRESOLVED, values just above the cutoff carry no confidence margin.
  State that no near-threshold band is applied.
- **Off-target expression**: when checking safety-relevant tissues,
  report all tissues where the gene is detected, not only the highest.
- **Curated vs recomputed disagreement**: when the analysis warns of
  a specificity disagreement, report both values and say the difference
  arises from the 50-tissue subset.

### Cross-disciplinary consequences

- A target expressed at high levels in a safety-relevant tissue (heart,
  liver, kidney) is material to on-target toxicity assessment regardless
  of which specialist fetched the data.
- Tissue specificity informs target selectivity arguments: a
  tissue-enriched target in the disease tissue is more selective than
  one with low specificity.
- Expression evidence complements but does not replace protein-level
  evidence — a gene may be transcribed without producing functional
  protein.

### What this section produces

Following this contract produces a Layer 1 finding in `findings/` that
cites the `.analysis.json`, satisfies all mandatory relays, names the
dataset, and scopes negatives to the detection cutoff. Everything the
tools emitted stays under `raw/expression/`.

Thresholds are cited by name (e.g. "the configured `expressed_ntpm`
threshold"), never by value. The values in force are in the artifact:
every `.analysis.json` carries `threshold_set` (name@version),
`thresholds_applied`, `threshold_sources` (default | program | flag),
and `threshold_provenance`. A specialist quoting a number should quote
it from the analysis they are citing.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the tool
emitted. If the tool did not run, there is no value. If a required tool
is unavailable, report the task as blocked. Do not estimate, and do not
proceed on an assumed result.

### Named pathologies

- **Saying "not expressed in tissue X" from a not_detected verdict.**
  not_detected means below the cutoff in one consensus bulk RNA
  dataset. The gene may be expressed in specific cell types within
  the tissue, detected by other assays, or present at levels just
  below the threshold. The `absent_is_not_evidence` relay requires
  naming the dataset and the cutoff.
- **Inferring cell-type expression from a tissue average.** A gene
  at 30 nTPM in testis may be concentrated in spermatocytes and
  absent from Sertoli cells. The `tissue_resolution_only` relay
  requires scoping to tissue level and refusing the cell-population
  inference. This is the ecological fallacy the tournament reviewer
  raised.
- **Reporting a tissue name HPA does not accept.** HPA silently
  drops unrecognised column codes — `skin`, `stomach`, `endometrium`
  all return no column rather than an error. The CLI maps aliases
  and validates columns, but a finding that names the alias instead
  of HPA's label (e.g. "skin" instead of "skin 1") may confuse
  reproducibility.
- **Omitting the dataset from a negative claim.** "Gene X is not
  expressed in liver" is a biological claim. "Gene X was not detected
  above the configured `expressed_ntpm` threshold in liver in HPA
  bulk consensus RNA" is a measurement report. Only the second is
  supported.
- **Citing an HPA release number the tool did not obtain.** If
  `release_version_unknown` fires, the finding must cite the payload
  SHA-256, not a version number looked up elsewhere. The number from
  a different query may correspond to a different payload.
- **Treating near-threshold values as confident calls.** With
  `low_confidence_ntpm` UNRESOLVED, a value at the cutoff has no
  margin either side of it. Do not describe this as "clearly
  expressed."
- **Averaging nTPM across tissues.** The 50-tissue mean has no
  biological interpretation. A gene at 50 nTPM in testis and 0 nTPM
  everywhere else is not "1 nTPM on average."
