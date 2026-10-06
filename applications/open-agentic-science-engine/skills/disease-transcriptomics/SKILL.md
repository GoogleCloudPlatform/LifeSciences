---
name: disease-transcriptomics
description: >
  Discover disease-state transcriptomics datasets and curated differential
  expression signatures across public repositories -- NCBI GEO (Gene
  Expression Omnibus) for dataset discovery, DisigNAtlas (Disease Signature
  Atlas) for pre-computed disease vs. control signatures, and SpatialDB for
  spatially resolved expression experiments. Use when searching for datasets
  comparing disease vs. control tissue, when checking whether differential
  expression data exists for a gene in a disease context, when identifying
  studies with lesional vs. non-lesional comparisons, or when looking for
  spatial expression patterns. Do not use for measured tissue-level
  expression (use tissue-expression-profile), for single-cell dataset
  discovery without disease focus (use single-cell-expression), or for
  genetic association evidence (use target-genetic-evidence).
---

## 1. When to use, and when not

Use this skill when you need to discover disease-state transcriptomics
data or curated disease gene expression signatures. Entry points include:

- Searching for GEO datasets that compare disease vs. control tissue
  (e.g. atopic dermatitis lesional vs. non-lesional skin).
- Checking whether a gene has been reported as differentially expressed
  in a disease context (DisigNAtlas curated signatures).
- Identifying published transcriptomics studies for a disease/tissue
  combination.
- Finding spatially resolved expression data for a gene across tissues
  (SpatialDB).
- Building the transcriptomic evidence layer of a target validation
  case: is the target gene dysregulated in disease tissue?

**Do not use when:**

- You need measured tissue-level expression values (bulk RNA-seq, TPM)
  in healthy tissue -> `tissue-expression-profile`.
- You need single-cell dataset discovery without a disease focus
  -> `single-cell-expression`.
- You need human genetic constraint or disease association evidence
  -> `target-genetic-evidence`.
- You need predicted regulatory variant effects
  -> `regulatory-variant-effect`.
- You need brain-region-specific expression or dementia donor data
  -> `brain-atlas`.

## 2. Preconditions

- **GEO search**: free-text query terms. Supports `--organism` (default
  "Homo sapiens"), `--entry-type` (gse/gds/gpl/gsm, default gse),
  `--data-type`, `--year-from`, `--year-to`, `--max-results`.
- **DisigNAtlas search**: gene symbol (gene mode) or disease term
  (disease mode). Supports `--mode gene|disease`, `--organism`.
- **SpatialDB search**: gene symbol. Supports `--species` (Human/Mouse).
- **No authentication** needed -- all three are public.
- **Rate limiting**: GEO uses NCBI E-utilities (3 QPS without API key).
  DisigNAtlas and SpatialDB are academic sites with lower throughput
  tolerance. The CLI paces requests.
- Run `dde doctor` before first use.

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| What GEO datasets match a disease/tissue query? | `dde geo search <QUERY> [--organism O] [--entry-type T] [--data-type D] [--year-from Y] [--year-to Y] [--max-results N]` | `raw/transcriptomics/<slug>.geo.json`<br>`raw/transcriptomics/<slug>.geo.artifact.json`<br>`raw/transcriptomics/<slug>.geo.meta.json` |
| What do the GEO results show? | `dde geo analyze <QUERY>` | `raw/transcriptomics/<slug>.geo.analysis.json` |
| Is a gene differentially expressed in any disease? | `dde disignatlas search <GENE> --mode gene` | `raw/transcriptomics/<slug>.disignatlas.json`<br>`raw/transcriptomics/<slug>.disignatlas.artifact.json`<br>`raw/transcriptomics/<slug>.disignatlas.meta.json` |
| What studies exist for a disease? | `dde disignatlas search <DISEASE> --mode disease` | same naming pattern |
| What do the DisigNAtlas results show? | `dde disignatlas analyze <QUERY> [--mode gene\|disease]` | `raw/transcriptomics/<slug>.disignatlas.analysis.json` |
| Does a gene have spatial expression data? | `dde spatialdb search <GENE> [--species S]` | `raw/transcriptomics/<slug>.spatialdb.json`<br>`raw/transcriptomics/<slug>.spatialdb.artifact.json`<br>`raw/transcriptomics/<slug>.spatialdb.meta.json` |
| What do the SpatialDB results show? | `dde spatialdb analyze <GENE> [--species S]` | `raw/transcriptomics/<slug>.spatialdb.analysis.json` |

Run `search` before `analyze`. `analyze` reads from disk and can be
re-run without re-querying. All tools support `--json`, `--quiet`, and
`--out`.

## 4. Interpretation contract

### Verdicts

Each `analyze` command classifies the search result:

- **GEO**: `datasets_found` or `no_datasets`. Reports assay type
  distribution, sample count statistics, platform breakdown, and year
  distribution.
- **DisigNAtlas (gene mode)**: `signatures_found` or `no_signatures`.
  Reports disease distribution where the gene is DE, regulation summary
  (up/down counts), log2FC statistics, and top signatures by effect size.
- **DisigNAtlas (disease mode)**: `signatures_found` or `no_signatures`.
  Reports study count, tissue distribution, organism breakdown.
- **SpatialDB**: `records_found` or `no_records`. Reports tissue
  distribution, technique breakdown, and unique PMIDs.

### What each tool provides

**GEO** returns dataset-level metadata: accession (GSE ID), title,
summary, organism, assay type, sample count, platform, publication date,
and sample titles. Sample titles often encode experimental conditions
(e.g. "lesional skin rep 1", "non-lesional skin rep 2") but this is
unstructured free text. GEO does not return expression values or
differential expression results.

**DisigNAtlas** returns curated differential expression signatures:
log2FC, adjusted p-value, regulation direction (up/down), disease,
tissue/cell type, data source (GEO, ArrayExpress, TCGA), and library
strategy. These are pre-computed from published datasets using
standardised pipelines. Quality and normalisation vary by study.

**SpatialDB** returns records of spatially resolved expression
experiments: gene, tissue, technique (Spatial Transcriptomics,
Slide-seq, MERFISH, etc.), species, and PMID. It does not return
expression values or spatial coordinates.

### Mandatory relays

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `geo.search_is_metadata_only` | Stop | Every GEO search (unconditional) | Do not infer differential expression from GEO dataset metadata. The search returns dataset descriptors (title, summary, sample list). It does not contain expression values. A dataset titled "Atopic dermatitis lesional vs non-lesional skin" confirms such a study exists; it does not confirm that any gene is differentially expressed. Determining DE requires downloading and analysing the expression data. |
| `disignatlas.curated_signatures` | Qualifier | Every DisigNAtlas search (unconditional) | State that DisigNAtlas signatures are pre-computed from public data using standardised pipelines. Individual study quality, sample sizes, and normalisation methods vary. Treat as a discovery resource for identifying disease-gene associations, not as primary evidence. The log2FC and padj values are study-specific and should not be compared across studies without accounting for differences in experimental design. |
| `spatialdb.spatial_not_bulk` | Qualifier | Every SpatialDB search (unconditional) | State that SpatialDB records describe spatially resolved expression experiments, not bulk or single-cell RNA-seq. Spatial transcriptomics covers a limited set of tissues and published studies. Absence from SpatialDB does not mean a gene lacks spatial expression data. |

Check `mandatory_relays` in each `.analysis.json` and `.meta.json`.
Every relay code present must be satisfied in the finding.

### Synthesis rules

When combining results across the three tools:

- GEO discovers datasets; DisigNAtlas provides pre-computed DEG results
  from those (and other) datasets. A gene found in DisigNAtlas with
  significant DE in a disease does not guarantee the underlying study is
  high quality — check the data source, sample size, and study design.
- GEO and DisigNAtlas overlap: DisigNAtlas curates signatures from GEO
  (and ArrayExpress, TCGA). A study in GEO may or may not have been
  processed into DisigNAtlas. Absence from DisigNAtlas does not mean
  the gene is not DE in GEO datasets — it means no curated signature
  exists.
- SpatialDB is orthogonal: it covers spatial experiments, not bulk or
  single-cell. A gene with spatial expression data provides tissue
  localisation context, not disease-state comparison.
- When GEO returns datasets with disease-relevant titles and
  DisigNAtlas shows significant DE for the same gene in the same
  disease, the convergence strengthens the evidence. But both trace
  to the same underlying public data, so this is not independent
  replication.

### Consequence rules

- **GEO datasets found**: report the count, assay type and organism
  breakdown, and representative GSE accessions with sample counts.
  Carry the `geo.search_is_metadata_only` relay. The result identifies
  where disease-state transcriptomics data can be obtained, not what
  the data shows.
- **DisigNAtlas signatures found (gene mode)**: report the number of
  disease contexts where the gene is DE, the regulation direction
  (up/down by disease), and the top signatures by |log2FC|. Carry the
  `disignatlas.curated_signatures` relay. Note the data sources and
  library strategies.
- **DisigNAtlas signatures found (disease mode)**: report the study
  count, tissue distribution, and top studies. Useful for assessing
  what disease-state data has been curated for a condition.
- **SpatialDB records found**: report tissue and technique distribution.
  Carry the `spatialdb.spatial_not_bulk` relay.
- **No results**: report as "no matching [datasets/signatures/records]
  found on [source] for this query." This may reflect query specificity,
  non-standard terminology, or genuine absence of public data.

### Cross-disciplinary consequences

- Disease-state DE evidence (from DisigNAtlas) is directly relevant to
  target validation: a gene upregulated in disease tissue is a
  candidate therapeutic target. But DE is a population-level statistical
  signal, not a per-patient guarantee.
- GEO dataset discovery is the first step toward confirming or refuting
  a disease-expression hypothesis. Identifying the right datasets
  enables follow-up with GEO2R or direct data analysis.
- Spatial expression data contextualises where a gene is expressed
  within tissue architecture — relevant for understanding target
  accessibility and mechanism of action.
- DisigNAtlas log2FC values are study-specific. Comparing a log2FC of
  2.5 in one study to 1.8 in another is not meaningful without
  accounting for differences in platform, normalisation, and sample
  composition.

### What this section produces

Following this contract produces a Layer 1 finding in `findings/` that
cites the relevant `.analysis.json` files, satisfies all fired relays,
and distinguishes between dataset discovery (GEO), curated DE signatures
(DisigNAtlas), and spatial expression records (SpatialDB). Everything
the tools emitted stays under `raw/transcriptomics/`.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the tool
emitted. If the tool did not run, there is no value. If a required tool
is unavailable, report the task as blocked. Do not estimate, and do not
proceed on an assumed result.

### Named pathologies

- **Treating GEO dataset metadata as expression evidence.** Finding a
  GEO dataset titled "AD lesional vs non-lesional skin RNA-seq" does
  not confirm that any gene is differentially expressed. The
  `geo.search_is_metadata_only` relay exists for this reason. "GEO
  contains 5 AD skin transcriptomics datasets" is supported; "ARTN is
  upregulated in AD lesional skin" is not supported by a GEO metadata
  search.
- **Treating DisigNAtlas log2FC as primary experimental evidence.** The
  signatures are pre-computed from heterogeneous public data. A
  log2FC = 3.2 with padj = 0.001 in DisigNAtlas means the curation
  pipeline found that result in one study. It does not substitute for
  reviewing the original study's methods, sample size, and
  normalisation.
- **Comparing log2FC values across DisigNAtlas studies.** Different
  studies use different platforms (microarray vs RNA-seq), normalisation
  methods, and sample compositions. A log2FC of 2.0 in a microarray
  study and 1.5 in an RNA-seq study are not directly comparable.
- **Inferring absence of disease association from zero results.** Zero
  GEO hits may reflect query specificity (try broader terms). Zero
  DisigNAtlas hits may mean the gene has not been curated, not that it
  is not DE. Zero SpatialDB hits may mean no spatial study has profiled
  the relevant tissue.
- **Double-counting GEO and DisigNAtlas results.** DisigNAtlas curates
  signatures from GEO datasets. Finding a gene in both does not
  constitute independent replication — both derive from the same
  underlying data.
- **Treating SpatialDB records as expression values.** SpatialDB
  records confirm that a gene appeared in a spatial transcriptomics
  experiment. They do not report expression levels or spatial
  coordinates. The actual expression data must be obtained from the
  original study.
