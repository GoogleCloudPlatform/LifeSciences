---
name: target-genetic-evidence
description: >
  Retrieve human genetic evidence for a gene target — population
  constraint (gnomAD), disease associations (Open Targets, GWAS
  Catalog), clinical variant pathogenicity (ClinVar), and whole-blood
  expression (GTEx). Use when assessing whether a gene has causal or
  associative links to disease, whether loss of function is tolerated,
  what clinical variant classifications exist, or what the gene's
  whole-blood expression level is. Do not use for predicted regulatory
  variant effects (use regulatory-variant-effect), multi-tissue
  expression profiles (use tissue-expression-profile), protein structure
  confidence (use protein-structure-confidence), or model organism
  phenotypes (use model-organism-phenotype).
---

## 1. When to use, and when not

Use this skill when you need human genetic evidence for a gene. Entry
points include:

- Assessing population-level LoF constraint (gnomAD) as part of target
  validation or safety review.
- Checking disease associations from genome-wide studies (Open Targets,
  GWAS Catalog) to establish genetic links between a gene and disease.
- Reviewing ClinVar variant classifications for clinical variant
  pathogenicity relevant to the target indication.
- Measuring whole-blood gene expression (GTEx) as one tissue data point
  within genetic evidence gathering.
- Synthesising across these sources to evaluate the overall strength of
  human genetic evidence for a target.

**Do not use when:**

- You need predicted regulatory effects of a variant
  -> `regulatory-variant-effect`.
- You need multi-tissue expression profiles (50 tissues, specificity)
  -> `tissue-expression-profile`.
- You need protein structure or fold confidence
  -> `protein-structure-confidence`.
- You need model organism phenotype data (mouse, HPO)
  -> `model-organism-phenotype`.

## 2. Preconditions

- **Gene input**: a gene symbol (e.g. `TP53`, `PKMYT1`). All tools
  resolve one symbol to one gene or refuse.
- **No authentication** needed — all sources are public APIs.
- **Rate limiting**: gnomAD throttles aggressively (HTTP 200 with errors
  array). NCBI E-utilities allow 3 req/s without API key. Open Targets
  and GTEx pace politely. Do not run parallel fetches across containers.
- **gnomAD constraint availability**: gnomAD computes constraint only on
  MANE Select transcripts passing outlier filters. No constraint record
  is a property of the gene, not a fetch failure.
- **GTEx gene resolution**: GTEx is exact-match only. Ambiguous symbols
  are refused. GTEx outputs use Gencode IDs, not gene symbols.
- Run `dde doctor` before first use.

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| What is this gene's gnomAD constraint? | `dde genetics fetch <SYMBOL>` | `raw/genomics/<SYMBOL>.gnomad-constraint.json`<br>`raw/genomics/<SYMBOL>.gnomad-constraint.meta.json` |
| Is loss of function tolerated? | `dde genetics analyze <SYMBOL>` | `raw/genomics/<SYMBOL>.gnomad-constraint.analysis.json` |
| What disease associations exist (Open Targets)? | `dde gwas search <GENE> --source opentargets` | `raw/genomics/<slug>.gwas-opentargets.json`<br>`raw/genomics/<slug>.gwas-opentargets.artifact.json`<br>`raw/genomics/<slug>.gwas-opentargets.meta.json` |
| Are the associations significant? | `dde gwas analyze <GENE> --source opentargets` | `raw/genomics/<slug>.gwas-opentargets.analysis.json` |
| What GWAS Catalog associations exist? | `dde gwas search <GENE> --source gwas-catalog` | `raw/genomics/<slug>.gwas-gwas-catalog.json`<br>`raw/genomics/<slug>.gwas-gwas-catalog.artifact.json`<br>`raw/genomics/<slug>.gwas-gwas-catalog.meta.json` |
| Are the GWAS hits genome-wide significant? | `dde gwas analyze <GENE> --source gwas-catalog` | `raw/genomics/<slug>.gwas-gwas-catalog.analysis.json` |
| What ClinVar classifications exist? | `dde gwas search <GENE> --source clinvar` | `raw/genomics/<slug>.gwas-clinvar.json`<br>`raw/genomics/<slug>.gwas-clinvar.artifact.json`<br>`raw/genomics/<slug>.gwas-clinvar.meta.json` |
| Are there pathogenic variants? | `dde gwas analyze <GENE> --source clinvar` | `raw/genomics/<slug>.gwas-clinvar.analysis.json` |
| What is this gene's whole-blood expression? | `dde gtex fetch <GENE>` | `raw/gtex/<GENCODE_ID>.gtex.json`<br>`raw/gtex/<GENCODE_ID>.meta.json` |
| What does the expression level mean? | `dde gtex analyze <GENE>` | `raw/gtex/<GENCODE_ID>.analysis.json` |

Run `fetch`/`search` before `analyze`. `analyze` reads from disk and
can be re-run without re-querying. All tools support `--json`,
`--quiet`, and `--out`.

## 4. Interpretation contract

### gnomAD constraint verdicts

`genetics analyze` applies the `gnomad-constraint` threshold set and
emits:

- **lof_intolerant** — pLI or LOEUF (or both) indicates constraint.
- **lof_tolerant** — both metrics indicate tolerance.
- **indeterminate** — the gene is too small for reliable constraint
  estimation.

The verdict derives from two independent metrics (`verdict_by_pli` and
`verdict_by_loeuf`) that **can disagree**. When they disagree,
`constraint_unreliable` fires, and the finding must quote both metrics
with the 90% CI. See `references/gnomad-constraint-details.md` for the
split-verdict problem and the v2/v4 LOEUF cutoff hazard.

#### Unresolved threshold

`loeuf_unreliable_min_expected_lof` is **UNRESOLVED** — gnomAD
publishes no minimum expected-LoF count below which LOEUF should be
distrusted. The analysis reports `exp_lof` and the full 90% CI and
declines to threshold on them. Do not invent a floor.

### GWAS / disease association verdicts

`gwas analyze` classifies associations from Open Targets or GWAS
Catalog:

- **associations_found** — one or more associations above the
  significance threshold. Open Targets uses an overall association
  score (higher is stronger); GWAS Catalog uses p-values (lower is
  more significant). The threshold applied is recorded in
  `thresholds_applied` in the `.analysis.json`.
- **no_associations** — no associations above the threshold.

The `--threshold` flag overrides the default significance cutoff. The
analysis records top diseases and the number of significant hits.

### ClinVar verdicts

`gwas analyze --source clinvar` classifies by ACMG clinical
significance tier, not by numeric threshold:

- **pathogenic_variants_found** — one or more variants classified as
  Pathogenic or Likely pathogenic.
- **no_pathogenic_variants** — no pathogenic/LP variants found (may
  still have VUS, benign, or other classifications).

ClinVar classifications carry a **review status** that determines
evidence quality. The analysis reports a breakdown by review tier
(strong, moderate, weak). A "Pathogenic" call with "no assertion
criteria provided" is materially weaker than one "reviewed by expert
panel." When pathogenic variants have weak review status,
`clinvar.weak_review_status` fires.

The analysis also reports `classification_counts` across all ACMG tiers
and `top_conditions` from pathogenic variants. When the result set is
truncated (heavily-studied genes may exceed the retrieval limit), the
sidecar records this.

### GTEx whole-blood expression

`gtex analyze` reports the median TPM from GTEx whole-blood RNA-seq
(755 samples, bulk RNA-seq from femoral/subclavian veins):

- **measured** — a median TPM value exists. Classification into
  expression tiers (not_expressed, low, moderate, high) is only applied
  when program-level threshold overrides are set in
  `.dde/thresholds.yaml`. By default, all `gtex-expression`
  thresholds are **UNRESOLVED** — GTEx publishes no expression-level
  cutoffs, and HPA nTPM cutoffs do not transfer (different
  normalization). Do not invent expression-level cutoffs.
- **no_data** — GTEx holds no whole-blood expression data for this gene.
  This is a real answer (the gene is not measured in whole blood), not
  an error.

### Mandatory relays

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `gnomad.constraint_unreliable` | Qualifier | pLI is in the intermediate band, OR gnomAD flags the transcript, OR pLI and LOEUF disagree (conditional) | Report the constraint metric with its 90% CI and say the gene could not be confidently categorised. Do not pick the metric that agrees with the hypothesis. |
| `gnomad.constraint_is_not_safety` | Stop | The overall verdict is lof_intolerant (conditional) | Do not carry constraint into a safety or tolerability claim. LoF intolerance describes complete loss from conception; it says nothing about partial, reversible, adult pharmacological inhibition. |
| `gwas.association_not_causation` | Qualifier | Significant GWAS associations exist (conditional) | State that GWAS associations are statistical correlations between genetic variants and disease phenotypes, not evidence of causation or therapeutic mechanism. |
| `clinvar.classification_is_curated` | Qualifier | Pathogenic/LP variants exist (conditional) | State that these are curated clinical assertions that the variant causes the named condition, not statistical correlations — but variant-level pathogenicity does not imply the gene is a therapeutic target. |
| `clinvar.weak_review_status` | Qualifier | Pathogenic/LP variants have weak review status (conditional) | Do not cite the pathogenic classification without stating its review status. A classification without review status is incomplete. |
| `gtex.whole_blood_is_not_peripheral_blood` | Qualifier | Every GTEx query (unconditional) | Report this as "GTEx whole-blood RNA-seq expression", not as "peripheral blood expression". GTEx whole blood is drawn from femoral/subclavian veins and measured by bulk RNA-seq — a partial proxy, not an equivalent measurement. |

The same relay list appears under two different key names depending on
where you read it:

- `mandatory_relays` — the key in persisted artifact files
  (`.analysis.json`, `.meta.json`). This is the authoritative
  representation and what `dde validate` checks.
- `relays` — the key in CLI stdout JSON output (`--json` flag). The
  two names refer to the same data.

Always check `mandatory_relays` in the persisted `.analysis.json` and
`.meta.json` files. If you search for `relays` in an `.analysis.json`
file, you will find nothing — the key is `mandatory_relays`. Always
use `mandatory_relays` when reading persisted artifact files. Every
relay code present in `mandatory_relays` must be satisfied in the
finding.

### Synthesis rules

When combining evidence across the grouped sources:

- gnomAD constraint and GWAS associations answer different questions.
  Constraint asks whether LoF is tolerated; GWAS asks whether variants
  near the gene correlate with disease. Both can be true (a constrained
  gene with strong disease associations) or independent.
- ClinVar classifications are curated assertions at the variant level.
  They complement GWAS (which is statistical and locus-level) and
  gnomAD (which is gene-level constraint).
- GTEx expression provides tissue context but does not confirm or deny
  genetic evidence. A gene may have strong GWAS associations with no
  detectable whole-blood expression, or high expression with no disease
  associations.
- Strength of genetic evidence depends on the convergence of multiple
  independent lines, not on any single source.

### Consequence rules

- **LoF intolerant**: the gene is constrained. The
  `constraint_is_not_safety` relay fires — do not carry this into a
  safety claim. See `references/gnomad-constraint-details.md`.
- **LoF tolerant / indeterminate**: report as stated; do not treat
  indeterminate as tolerant.
- **No constraint record**: report as missing data, not as tolerance.
  Do not substitute a related gene's constraint.
- **GWAS associations found**: report the top diseases and the number
  of significant associations. The `association_not_causation` relay
  fires — these are correlations, not causal evidence.
- **No GWAS associations**: report as "no associations above the
  significance threshold", not as "gene has no disease link."
- **Pathogenic ClinVar variants found**: report the count, the review
  status breakdown, and the top conditions. Weak-review classifications
  must be qualified.
- **No pathogenic ClinVar variants**: report as "no pathogenic or
  likely pathogenic variants in ClinVar." The gene may still have VUS
  or benign variants.
- **GTEx measured**: report the median TPM and the tissue. Carry the
  `whole_blood_is_not_peripheral_blood` qualifier.
- **GTEx no data**: report as "no whole-blood data in GTEx", not as
  "not expressed."

### Cross-disciplinary consequences

- A LoF-intolerant target carries population-level evidence relevant to
  safety review even when discovered by a target validation specialist.
  Constraint evidence complements but does not replace preclinical
  toxicology.
- GWAS associations inform indication selection but do not validate a
  mechanism. A gene associated with a disease by GWAS may not be
  druggable or may act through a pathway not addressable by the proposed
  modality.
- ClinVar pathogenic variants establish human clinical relevance for a
  gene-disease link. Strong-review classifications carry more weight
  than weak-review ones.
- GTEx whole-blood expression provides one tissue data point. For
  multi-tissue profiling, use `tissue-expression-profile`.
- The absence of evidence from any single source is not evidence of
  absence. A gene with no GWAS hits may still be a valid target based
  on other evidence.

### What this section produces

Following this contract produces a Layer 1 finding in `findings/` that
cites the relevant `.analysis.json` files, satisfies all mandatory
relays, and synthesises across sources without conflating correlation
with causation or constraint with safety. Everything the tools emitted
stays under `raw/genomics/` and `raw/gtex/`.

Thresholds are cited by name (e.g. "the configured
`lof_intolerant_pli` threshold"), never by value. The values in force
are in the artifact: every `.analysis.json` carries `threshold_set`
(name@version), `thresholds_applied`, `threshold_sources` (default |
program | flag), and `threshold_provenance`. A specialist quoting a
number should quote it from the analysis they are citing.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the tool
emitted. If the tool did not run, there is no value. If a required tool
is unavailable, report the task as blocked. Do not estimate, and do not
proceed on an assumed result.

### Named pathologies

- **Picking the agreeable metric when pLI and LOEUF disagree.** Quote
  both with the 90% CI when `constraint_unreliable` fires. See
  `references/gnomad-constraint-details.md`.
- **Carrying LoF intolerance into a safety claim.** The
  `constraint_is_not_safety` relay blocks this inference — pLI
  describes complete loss from conception, not reversible
  pharmacological inhibition.
- **Treating indeterminate constraint as tolerant.** Indeterminate
  means too small to categorise, not evidence of tolerance.
- **Importing the v2 LOEUF cutoff from the literature.** gnomAD v4
  o/e values are higher than v2. The tool's default is the v4 figure.
  Read `thresholds_applied` from the `.analysis.json`.
- **Substituting a paralog's constraint.** A gene with no gnomAD
  record gets no verdict. Do not substitute.
- **Reporting GWAS association as causation.** The
  `association_not_causation` relay fires specifically because this is
  the most common misreading. GWAS identifies correlated loci, not
  causal genes.
- **Citing a ClinVar classification without its review status.** A
  "Pathogenic" call with "no assertion criteria provided" is materially
  weaker than one "reviewed by expert panel." The
  `weak_review_status` relay requires stating the review tier.
- **Conflating variant pathogenicity with target validity.** A variant
  classified as Pathogenic in ClinVar means the variant causes the
  condition. It does not mean the gene is a viable drug target.
- **Reporting GTEx whole-blood as peripheral blood.** The
  `whole_blood_is_not_peripheral_blood` relay fires on every GTEx
  query.
- **Inventing GTEx expression-level cutoffs.** All `gtex-expression`
  thresholds are UNRESOLVED by default. GTEx publishes no cutoffs and
  HPA nTPM cutoffs do not transfer.
- **Treating GTEx no_data as "not expressed."** No data means GTEx did
  not measure the gene in whole blood. It is not an expression finding.
- **Reporting a gnomAD throttle as a missing gene.** gnomAD returns
  HTTP 200 with an errors array for both "gene not found" and "service
  overloaded." The CLI distinguishes them and retries.
