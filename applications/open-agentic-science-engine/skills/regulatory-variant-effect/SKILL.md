---
name: regulatory-variant-effect
description: >
  Score a genomic variant's predicted effect on regulatory tracks —
  gene expression (RNA-seq, CAGE, PROCAP), chromatin accessibility
  (ATAC, DNase), histone marks, transcription-factor binding, splicing,
  and 3D contact maps — predict baseline regulatory activity across
  a genomic interval, or predict cell-type-specific expression patterns
  from genomic sequence. Use when interpreting a non-coding or regulatory
  variant, assessing regulatory evidence for or against a target,
  evaluating whether a variant disrupts a cis-regulatory element, or
  establishing baseline epigenomic context at a locus. Do not use for
  protein structure confidence (use protein-structure-confidence),
  protein-coding variant consequence (use sequence-annotation tools),
  genetic association and population constraint evidence (use
  target-genetic-evidence), or measured tissue-level or cell-type
  expression (use tissue-expression-profile or single-cell-expression).
---

## 1. When to use, and when not

Use this skill when you need to evaluate a variant's predicted regulatory
impact or understand the baseline regulatory landscape at a locus. Entry
points include:

- Scoring a non-coding variant identified from GWAS, eQTL, or clinical
  sequencing to assess its regulatory effect across tissues and assays.
- Predicting baseline chromatin and expression tracks across an interval
  to establish regulatory context before variant interpretation.
- Performing in-silico mutagenesis to discover which positions in a
  regulatory element are functionally important (when available).
- Assessing whether a variant near a drug target's regulatory region
  might alter its expression — relevant to target validation, on-target
  toxicity assessment, and pharmacogenomic risk.
- Predicting cell-type-specific expression from genomic sequence when
  measured cell-type data is unavailable — using
  `predict-interval --output-type RNA_SEQ` to access 667 RNA-seq tracks
  (271 positive-strand, 271 negative-strand, 125 unstranded) covering
  diverse cell types and conditions.

**Do not use when:**

- You need protein structure or fold confidence
  -> `protein-structure-confidence`.
- You need protein-coding variant consequence (missense, nonsense,
  frameshift) -> sequence annotation tools.
- You need population-level genetic constraint, loss-of-function
  intolerance, or disease association -> `target-genetic-evidence`.
- You need to interpret a variant's effect on protein function or
  stability -> protein-level tools.
- You need measured tissue-level expression
  -> `tissue-expression-profile`. AlphaGenome predicts expression from
  genomic features; tissue-expression-profile reports measured RNA
  from HPA.
- You need measured cell-type-resolved expression data
  -> `single-cell-expression`. It discovers datasets; AlphaGenome
  predicts from sequence.

## 2. Preconditions

- **Variant input**: chromosome (e.g. `chr17`), 1-based position,
  reference base(s), and alternate base(s). The CLI centres a context
  window on the variant; you do not supply an interval.
- **Window**: defaults to 131072 bp. Snapped to the nearest of
  {16384, 131072, 524288, 1048576} because the model rejects any other
  length. The window size changes the gene set and scores — see §4.
- **Interval input** (for `predict-interval`): genomic coordinates in
  `chrN:start-end` format. Length must be exactly one of the valid
  windows.
- **Scorer**: defaults to `geneMask`. Options: geneMask,
  geneMaskActive, geneMaskSplicing, centerMask, paQtl, spliceJunction,
  contactMap. Three scorers — paQtl, spliceJunction, contactMap — are
  fieldless: the endpoint proto declares no output-type field for them,
  so each answers for one fixed modality. If you pass `--output-type`
  with a fieldless scorer, the CLI drops the flag and records a warning
  in the sidecar explaining that the result is not the modality you
  requested. geneMaskSplicing accepts only
  SPLICE_SITES and SPLICE_SITE_USAGE; any other output type is refused.
- **Output type**: defaults to CAGE. Repeatable. One or more of: ATAC,
  CAGE, DNASE, RNA_SEQ, CHIP_HISTONE, CHIP_TF, SPLICE_SITES,
  SPLICE_SITE_USAGE, SPLICE_JUNCTIONS, CONTACT_MAPS, PROCAP. Large
  multi-type requests risk gateway 502s; split if needed. Not applicable
  to the three fieldless scorers above.
- **Strand**: required by the endpoint. Defaults to STRAND_POSITIVE.
  STRAND_UNSPECIFIED is rejected. Its effect on gene-level scores has
  not been characterised; strand handling is driven by gene metadata.
- **Backend** (`score-variant` only): `vertex` (default, GCP auth) or
  `pip` (requires `ALPHAGENOME_API_KEY`; raises immediately — the pip
  path is not implemented).
- **Organism**: defaults to `HOMO_SAPIENS`. Also supports
  `MUS_MUSCULUS`.
- Run `dde doctor` before first use. It ends with a verdict line:
  `STOP` means fix or report before running anything; `PROCEED` means
  work, and the grouped warnings tell you which commands would refuse,
  which results need careful reading, and which are the tooling lead's
  to clear. Do not judge by the warning count; the verdict line grades
  them for you.

## 3. Tool invocations

### Variant scoring

| Question | Run | Writes to |
|---|---|---|
| What is this variant's regulatory effect? | `dde alphagenome score-variant --chrom <C> --pos <P> --ref <R> --alt <A> [--window <W>] [--scorer <S>] [--output-type <T>...]` | `raw/genomics/<chrom>-<pos>-<ref>-<alt>.scores.json`<br>`raw/genomics/<chrom>-<pos>-<ref>-<alt>.response.ndjson`<br>`raw/genomics/<chrom>-<pos>-<ref>-<alt>.request.json`<br>`raw/genomics/<chrom>-<pos>-<ref>-<alt>.meta.json` |
| How strong is the effect? | `dde alphagenome analyze <SCORES_PATH> [--quantile-significance <V>]` | `raw/genomics/<chrom>-<pos>-<ref>-<alt>.analysis.json` |

Run `score-variant` before `analyze`. `analyze` reads the stored
`.scores.json` and applies the `alphagenome-variant-effect` threshold
set. It can be re-run with different thresholds without re-querying.

The `.scores.json` contains `blocks: [...]`, one block per output type
or per scorer (fieldless scorers like paQtl are labelled `SCORER_paQtl`
rather than by output type, since neither request nor response names
one). Each block carries its own gene list, quantile availability, and
strand masking statistics. The `.response.ndjson` preserves the raw
endpoint response so the decoding can be re-checked without another
call.

### Baseline interval prediction

| Question | Run | Writes to |
|---|---|---|
| What is the predicted regulatory activity across this interval? | `dde alphagenome predict-interval --interval <I> --strand <S> [--output-type <T>...]` | `raw/genomics/<chrom>-<start>-<end>.tracks.json`<br>`raw/genomics/<chrom>-<start>-<end>.tracks.npz`<br>`raw/genomics/<chrom>-<start>-<end>.tracks.response.ndjson`<br>`raw/genomics/<chrom>-<start>-<end>.tracks.request.json`<br>`raw/genomics/<chrom>-<start>-<end>.tracks.meta.json` |

The tensor is stored as `.npz` (compressed NumPy). The `.tracks.json`
index carries track metadata and references the `.npz`; open the index
first.

#### Cell-type expression prediction

```
dde alphagenome predict-interval --interval <I> --strand <S> --output-type RNA_SEQ
```

**What it returns**: a [positions x tracks] tensor across 667 RNA-seq
tracks. Track metadata in the `.tracks.json` index names each track
(cell type, tissue, condition, strand). The `.npz` holds the numerical
tensor.

**Track structure**: 271 positive-strand tracks, 271 negative-strand
tracks, 125 unstranded tracks. The track names in `track_metadata`
identify cell types and conditions (e.g. specific cell lines, primary
cells, tissue samples).

**How to identify relevant tracks**: open the `.tracks.json` index and
inspect `track_metadata`. Each track entry includes a `name` field.
Filter for tracks whose names match the cell type, tissue, or condition
of interest.

**Strand handling**: a single RNA_SEQ call returns all 667 tracks across
both strands. A gene on the negative strand will have signal on
negative-strand tracks. When interpreting results for a specific gene,
attend to tracks matching the gene's strand plus the 125 unstranded
tracks. The `--strand` flag sets the interval's strand for the request,
not a filter on the returned tracks.

### In-silico mutagenesis (ISM)

| Question | Run | Writes to |
|---|---|---|
| Which positions in this element are functionally important? | `dde alphagenome ism --interval <I>` | **Not yet implemented** — requires `ALPHAGENOME_API_KEY` (pip backend only). |
| What motifs does the ISM scan reveal? | `dde alphagenome analyze-ism <ARTIFACT>` | **Blocked** — the `alphagenome-ism` threshold set has unresolved cutoffs with no cited source. |

ISM is not available for planning.

All output options: `--json` for machine-readable output, `--quiet` for
paths only, `--out` to override the default output directory.

## 4. Interpretation contract

### Per-block verdicts and the confirmed/unconfirmed split

Results are per block — one block per output type, or one per fieldless
scorer. Each block carries its own verdict. Whether a quantile significance test was applied depends on the
**output type**, not the scorer: RNA_SEQ returns a signed quantile plane,
while CAGE, ATAC, DNASE, and PROCAP do not.

This creates two categories of verdict that are **different instruments,
not stronger and weaker versions of the same call**:

**When quantile scores are available** (e.g. RNA_SEQ):

- **moderate_magnitude_confirmed** / **strong_magnitude_confirmed** —
  the magnitude band is supported by the quantile test.
- The guide's artifact rule (high quantile + low raw) is applied and
  flagged genes are excluded.

**When quantile scores are not available** (e.g. CAGE, ATAC):

- **moderate_magnitude_unconfirmed** / **strong_magnitude_unconfirmed**
  — an untested magnitude. This is not weaker evidence of the same
  kind; it is a magnitude reading from an instrument that provides no
  significance test on this path. Do not describe it as "significant."

**Both paths share:**

- **no_effect_by_magnitude** / **subtle_by_magnitude** — below the
  relevant threshold regardless of quantile availability.
- **no_scores** — no genes were scored. The cause is uncharacterised;
  do not assume a specific reason.

### Overall verdict

The overall verdict is the strongest per-block verdict, and
`verdict_from` in the analysis names which output type it came from.
The finding must state the assay, because "strong" from RNA_SEQ
(confirmed) and "strong" from CAGE (unconfirmed) are not the same
claim.

### The quantile artifact trap

When quantile scores are available, an extreme |quantile| paired with a
negligible |raw_score| is a **documented statistical artifact** — a
rank against a flat background in a low-expression gene, not a molecular
effect. The analysis flags these and the `quantile_artifact` relay
requires **excluding them from the effect list entirely**, not merely
disclosing them. Reporting a negligible raw score as a top-percentile
regulatory effect inverts the finding.

### Strand masking and track counts

A gene is scored only on tracks of its own strand plus any unstranded
ones, so much of a `score_variant` tensor is NaN by design. The fraction
varies by output type and scorer — CAGE masks about half (273 of 546),
RNA_SEQ about 40% (396 of 667, because it has 125 unstranded tracks
scored for every gene), and paQtl masks none. Never infer the fraction;
quote `n_tracks_scored` per gene from the artifact. A finding that says
"across 546 tracks" on CAGE when 273 carry values overstates the
evidence by 2x.

### Window sensitivity

The gene set and the scores are properties of the context window, not of
the variant alone. The same variant scored at different window sizes
produces different gene lists and different magnitudes. A finding that
names genes without naming the window is not reproducible. Always report
the window size alongside any gene list or score.

### Mandatory relays

The following relay codes mark obligations on the finding. Each must be
satisfied — not merely disclosed — in any Layer 1 finding.

All four relays are **qualifiers** — they fire on defects or anomalies
in the data. A clean variant-effect prediction (RNA_SEQ output type,
quantile scores available, no missing-score anomaly, no artifact flag)
produces **zero relays**. That is not a clean bill of health — it means
no defect was found. Nothing has been said about whether a large
predicted effect constitutes pathogenicity or causality. A variant
effect score is a prediction of regulatory impact on gene expression;
reading it as clinical pathogenicity or as evidence of causal mechanism
is a substitution the tool cannot guard against because no relay fires
on the impressive result.

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `alphagenome.no_quantile_scores` | Qualifier | Blocks whose output type returns no quantile (conditional — names the specific types) | Do not call any effect significant on raw score alone. The significance rule the interpretation guide defines could not be applied to the named output types. |
| `alphagenome.band_modality_mismatch` | Qualifier | Any call not using `--output-type RNA_SEQ` (standing) | State the assay the score came from alongside any magnitude word. The bands are RNA-seq-derived, so "moderate" on a CAGE or ATAC track is an extrapolation, not a calibrated call. |
| `alphagenome.unexplained_missing_scores` | Qualifier | Only when NaN pattern violates the strand rule (anomaly) | Report the scored-track count, not the track total, and say that some tracks went unscored for reasons the tool could not explain. Do not aggregate over the full track set. |
| `alphagenome.quantile_artifact` | Qualifier | Only when |quantile| exceeds the configured `quantile_significance` threshold while |raw| stays below the `artifact_raw_ceiling` | Exclude the flagged genes from the effect list entirely. An extreme quantile on a negligible raw score is a rank against a flat background; reporting it as a top-percentile effect inverts the finding. |

Check `mandatory_relays` in both the `.analysis.json` and the
`.meta.json`. Every relay code present must be satisfied in the finding.

### Mandatory advisories

Every analysis carries these advisories:

1. Raw-score magnitude bands are rules of thumb derived primarily from
   RNA-seq and are not absolute; interpretation depends on modality and
   assay type.
2. Raw scores are not percentages and must not be reported as such.
3. AlphaGenome does not model miRNA effects, RNA secondary structure,
   protein folding consequences, or developmental timing.

When at least one block carries quantile scores, a fourth advisory is
present: quantile scores are ranked against common variants, so a
variant in a low-expression region can score a high quantile without
biological significance.

### Consequence rules

- **Confirmed effect** (quantile-tested): state the assay, the window,
  and the per-gene scored-track count. This is the strongest regulatory
  evidence this tool provides.
- **Unconfirmed magnitude**: state clearly that this is a magnitude
  reading, not a significance call. Name the output type and the
  limitation.
- **Quantile artifacts**: exclude entirely. Do not list them as effects
  with a caveat.
- **Effect on splicing tracks**: a predicted splice-site or junction
  change warrants separate assessment of protein-level consequences.
  This skill does not predict protein-level impact.
- **No effect by magnitude**: report the null result; it is evidence
  within the scope of the window tested.
- **Multiple output types**: when several assays show concordant effects,
  state which are confirmed and which are unconfirmed. Do not average
  across output types.

### Cross-disciplinary consequences

- A variant with a strong confirmed effect on a drug target's expression
  is material to pharmacogenomic risk regardless of which specialist
  discovered it.
- A regulatory variant in a safety-gene's promoter is relevant to
  toxicology even when discovered during target validation.
- Regulatory evidence complements but does not replace genetic
  association evidence from population studies.

### What this section produces

Following this contract produces a Layer 1 finding in `findings/` that
cites the `.analysis.json`, satisfies all mandatory relays, states the
window size, names the assay for every verdict, and distinguishes
confirmed from unconfirmed results. Everything the tools emitted stays
under `raw/genomics/`.

Thresholds are cited by name (e.g. "the configured
`quantile_significance` threshold"), never by value.

### Predicted vs measured expression

AlphaGenome predicts expression from **genomic sequence features**
(regulatory elements, promoter structure, enhancer patterns). It does
not measure RNA.

A prediction that a gene shows signal on sensory neuron RNA-seq tracks
is evidence of **sequence-level regulatory potential** in that cell
type, not a measurement of actual expression. This distinction matters
because: (a) predictions can be wrong — the model may miss
post-transcriptional regulation, chromatin state, or environmental
factors; (b) predictions cannot capture dynamic or condition-dependent
expression changes that are not encoded in the reference genome
sequence.

**Complementary evidence**: use `tissue-expression-profile` for measured
tissue-level expression (HPA data) and `single-cell-expression` for
discovering measured single-cell datasets. AlphaGenome predictions add
value where measured cell-type data does not exist — they extend the
evidence map, not replace it.

**Wording constraint**: never say a gene "is expressed" based on
predict-interval output. Say "is predicted to show expression signal" or
"shows predicted regulatory potential for expression." The verb
"expressed" implies measurement.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the tool
emitted. If the tool did not run, there is no value. If a required tool
is unavailable, report the task as blocked. Do not estimate, and do not
proceed on an assumed result.

### Named pathologies

- **Calling an unconfirmed magnitude "significant."** An `_unconfirmed`
  verdict means no significance test was performed, not that the test
  was performed and passed. Describing it as "a significant regulatory
  effect" misrepresents the evidence.
- **Treating confirmed and unconfirmed as the same instrument.** A
  `strong_magnitude_confirmed` result from RNA_SEQ and a
  `strong_magnitude_unconfirmed` result from CAGE are not the same
  claim. The finding must name the assay and the suffix.
- **Reporting a quantile artifact as an effect.** An extreme quantile
  on a negligible raw score is a ranking artifact. The obligation is
  to exclude the gene entirely, not to disclose it with a caveat.
  Reporting it inverts the finding.
- **Reporting raw scores as percentages.** Raw scores are continuous
  values on an unbounded scale, not percentages.
- **Quoting the track total instead of the scored count.** Much of the
  tensor is NaN by design (strand masking), and the fraction varies by
  output type and scorer. The per-gene `n_tracks_scored` is the number
  to quote.
- **Omitting the window from a gene list.** The gene set is a property
  of the request. A different window returns different genes and
  different scores. Without the window, the finding is not
  reproducible.
- **Saying "moderate" on CAGE without naming the modality.** The
  magnitude bands are calibrated against RNA-seq. Applying them to
  other assays is an extrapolation. The `band_modality_mismatch`
  relay requires stating the assay.
- **Attributing a contactMap score to a gene.** contactMap scores the
  whole interval, not individual genes. Its row is named for the
  interval and marked `interval_level`. No gene in that interval owns
  the number.
- **Interpreting a null result as "no regulatory role."** AlphaGenome
  predicts effects on the tracks it models. Absence of a predicted
  effect is not evidence of no regulatory function.
- **Endpoint 502 misread as null result.** Since the scale-to-zero fix,
  a persistent 502 is more often a rejected request than an outage:
  missing organism, unset interval strand, an interval length not in
  {16384, 131072, 524288, 1048576}, too many output types in one call,
  or CHIP_HISTONE / CHIP_TF output types whose response exceeds a
  gateway size limit and has never returned successfully at any window.
  The CLI warns to stderr before attempting these types. Unlike the
  other four causes (instant rejections), the gateway size failure
  consumes the full retry budget by default; use `--deadline` to
  shorten the wait. Check the request before retrying.
- **ISM with invented thresholds.** The `alphagenome-ism` threshold set
  has unresolved cutoffs. The CLI will refuse to produce a verdict.
