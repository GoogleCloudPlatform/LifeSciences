---
name: single-cell-expression
description: >
  Discover single-cell RNA-seq datasets and studies across public
  repositories -- CZ CELLxGENE Discover, Broad Institute Single Cell
  Portal, and DISCO (immunesinglecell.org). Use when identifying
  datasets that contain expression data for a tissue, cell type, or
  disease of interest, when checking whether single-cell data exists
  for a specific organ or cell population, or when planning
  experimental validation that requires cell-type-resolved expression
  context. Do not use for measured tissue-level expression (use
  tissue-expression-profile), for predicted regulatory variant effects
  (use regulatory-variant-effect), or for measured population-level
  genetic evidence (use target-genetic-evidence).
---

## 1. When to use, and when not

Use this skill when you need to discover what single-cell datasets exist
for a tissue, cell type, disease, or organism. Entry points include:

- Checking whether single-cell RNA-seq datasets exist for a specific
  tissue or cell population (e.g. dorsal root ganglia, pancreatic beta
  cells, tumour-infiltrating lymphocytes).
- Identifying datasets that could provide cell-type-resolved expression
  evidence for a gene of interest -- the datasets themselves, not the
  expression values.
- Cross-referencing two repositories to assess dataset coverage for a
  tissue or disease before planning downstream analysis.
- Cataloguing available single-cell atlases for an organ system as part
  of experimental validation planning.

**Do not use when:**

- You need measured tissue-level expression values (bulk RNA-seq, TPM)
  -> `tissue-expression-profile`.
- You need predicted regulatory variant effects on expression
  -> `regulatory-variant-effect`.
- You need population-level genetic evidence (GWAS, gnomAD, ClinVar)
  -> `target-genetic-evidence`.
- You need actual gene expression values from a single-cell dataset --
  this skill discovers datasets; it does not retrieve or analyse
  expression matrices.

## 2. Preconditions

- **Query input**: a free-text query string. Both tools accept tissue,
  cell type, disease, or organism terms. CELLxGENE additionally supports
  structured filters (`--tissue`, `--cell-type`, `--organism`,
  `--disease`). SCP supports `--max-results`.
- **No authentication** needed -- both portals are public.
- **Rate limiting**: the CLI paces requests. Do not run parallel
  searches across containers.
- **Metadata only**: both tools return dataset/study metadata. Neither
  returns gene expression values. A search that finds DRG datasets does
  not tell you whether a specific gene is expressed in DRG neurons.
- Run `dde doctor` before first use.

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| What CELLxGENE datasets match? | `dde cellxgene search <QUERY> [--tissue T] [--cell-type C] [--organism O] [--disease D]` | `raw/single-cell/<slug>.cellxgene.json`<br>`raw/single-cell/<slug>.cellxgene.artifact.json`<br>`raw/single-cell/<slug>.cellxgene.meta.json` |
| What do the CELLxGENE results show? | `dde cellxgene analyze <QUERY>` | `raw/single-cell/<slug>.cellxgene.analysis.json` |
| What SCP studies match? | `dde scp search <QUERY> [--max-results N]` | `raw/single-cell/<slug>.scp.json`<br>`raw/single-cell/<slug>.scp.meta.json` |
| What do the SCP results show? | `dde scp analyze <QUERY>` | `raw/single-cell/<slug>.scp.analysis.json` |
| What DISCO immune datasets match? | `dde disco search <QUERY> [--tissue T] [--disease D] [--species S]` | `raw/single-cell/<slug>.disco.json`<br>`raw/single-cell/<slug>.disco.artifact.json`<br>`raw/single-cell/<slug>.disco.meta.json` |
| What do the DISCO results show? | `dde disco analyze <QUERY> [--tissue T] [--disease D]` | `raw/single-cell/<slug>.disco.analysis.json` |

Run `search` before `analyze`. `analyze` reads from disk and can be
re-run without re-querying. All tools support `--json`, `--quiet`, and
`--out`.

CELLxGENE Discover indexes 33M+ cells across 436+ datasets. Broad
Single Cell Portal indexes 85.9M cells across 1042+ studies. DISCO
indexes 144.9M cells across 22,755 samples with 466 cell types,
specialising in immune cell populations across 40 atlases. Coverage
overlaps -- a dataset on one portal may also appear on another.

## 4. Interpretation contract

### Verdicts

Each `analyze` command classifies the search result:

- **datasets_found** (CELLxGENE) / **studies_found** (SCP) -- the query
  matched one or more entries. The analysis reports counts, tissue
  breakdown, organism breakdown, and summary statistics (cell counts,
  gene counts at the dataset/study level).
- **no_datasets** / **no_studies** -- the query matched nothing.

### What the analysis reports

Both analyses provide dataset/study-level metadata summaries:

- **Dataset/study count and tissue distribution**: how many datasets
  or studies matched, broken down by tissue or organ.
- **Cell and gene counts**: aggregate and per-dataset/study totals.
  These are study-level statistics describing the size of each dataset,
  not gene-specific expression measurements.
- **Organism and disease annotations**: which organisms and disease
  conditions are represented in the matching datasets.

These are catalogue statistics. They describe what data exists, not
what any gene does in that data.

### Mandatory relays

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `cellxgene.search_is_metadata_only` | Stop | Every CELLxGENE search (unconditional) | Do not infer gene expression from dataset metadata. The search returns dataset descriptors (tissue, cell type, cell count, gene count). It does not return expression values for any gene. A dataset annotated with "dorsal root ganglion" confirms that DRG data exists in CELLxGENE; it does not confirm that any specific gene is expressed there. |
| `scp.search_is_study_metadata` | Stop | Every SCP search (unconditional) | Do not infer gene expression from study metadata. The search returns study-level descriptors (title, organism, cell count, disease). It does not return expression values. A study titled "Human DRG single-cell atlas" confirms such a study exists; it does not confirm expression of any specific gene in DRG. |
| `disco.search_is_sample_metadata` | Stop | Every DISCO search (unconditional) | Do not infer gene expression from DISCO sample metadata. The search returns sample-level descriptors (tissue, disease, cell count). Expression values and cell type markers are not included. Confirming gene expression or cell type enrichment requires downloading the expression data (H5 files) from DISCO. |

Check `mandatory_relays` in each `.analysis.json` and `.meta.json`.
Every relay code present must be satisfied in the finding.

### Synthesis rules

When combining results across the two repositories:

- CELLxGENE and SCP index overlapping but non-identical collections.
  A dataset absent from one portal may be present on the other.
  Cross-referencing both gives better coverage of available data.
- Cell type annotations are not standardised across datasets or portals.
  The same neurons may be labelled "sensory neurons" in one dataset and
  "nociceptors" in another. Do not treat annotation labels as a
  controlled vocabulary unless the dataset explicitly uses a cell
  ontology.
- Dataset size (cell count, gene count) describes experimental scale,
  not data quality or relevance. A 500-cell targeted study of the right
  cell type may be more informative than a 100,000-cell whole-organ
  atlas.

### Consequence rules

- **Datasets/studies found**: report the count, the tissue and organism
  breakdown, and representative dataset identifiers. Carry both metadata
  relays. The result identifies where single-cell data can be obtained,
  not what it contains at the gene level.
- **No datasets/studies found**: report as "no matching datasets/studies
  found on [portal] for this query." This may reflect query specificity
  or genuine absence of public data. Suggest query broadening if the
  terms were narrow.
- **Cross-portal overlap**: when the same dataset appears on both
  portals, note the overlap. Do not double-count it as independent
  evidence of data availability.
- **Large result sets**: when many datasets match, the summary
  statistics describe the full set but individual datasets may vary
  widely in relevance, quality, and cell type coverage. Do not
  generalise from aggregate statistics to any single dataset.

### Cross-disciplinary consequences

- Dataset discovery establishes that single-cell data exists for a
  tissue or cell population. It does not establish gene expression,
  cell-type enrichment, or marker status. Those claims require
  downloading and analysing the expression data (H5AD or equivalent).
- The availability of a DRG or tumour dataset is relevant context for
  planning experimental validation or computational follow-up, but it
  is not itself evidence for or against a target.
- Cell type annotations from dataset metadata may inform which cell
  populations have been profiled in a tissue, useful for assessing
  whether cell-type-resolved data could be obtained for a target of
  interest.

### What this section produces

Following this contract produces a Layer 1 finding in `findings/` that
cites the relevant `.analysis.json` files, satisfies both metadata
relays, and reports dataset availability without claiming gene-level
expression evidence. Everything the tools emitted stays under
`raw/single-cell/`.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the tool
emitted. If the tool did not run, there is no value. If a required tool
is unavailable, report the task as blocked. Do not estimate, and do not
proceed on an assumed result.

### Named pathologies

- **Treating dataset discovery as expression evidence.** Finding a
  dataset that covers dorsal root ganglia does not mean any specific
  gene is expressed in DRG neurons. The `search_is_metadata_only` and
  `search_is_study_metadata` relays exist because this is the most
  common misreading. "CELLxGENE contains 3 DRG datasets" is supported;
  "GFRA3 is expressed in DRG neurons" is not supported by a metadata
  search.
- **Inferring cell-type enrichment from dataset metadata.** A dataset
  annotated with "nociceptors" confirms that nociceptors were profiled.
  It does not confirm that a gene is enriched in nociceptors versus
  other cell types. Enrichment requires analysing the expression matrix.
- **Confusing study-level statistics with gene-specific values.**
  `cell_count` and `gene_count` in the search results describe dataset
  size. They are not expression measurements for any gene. A dataset
  with 20,000 genes measured does not tell you the expression level of
  any one of them.
- **Reporting dataset annotations as a controlled vocabulary.** Cell
  type labels vary across datasets. "Sensory neurons", "nociceptors",
  and "DRG neurons" may refer to the same or overlapping populations.
  Do not treat free-text annotations as standardised terms unless the
  dataset explicitly uses a cell ontology (e.g. CL).
- **Double-counting cross-portal datasets.** The same underlying study
  may appear on both CELLxGENE and SCP. Reporting "3 datasets on
  CELLxGENE and 2 on SCP, totalling 5" overstates availability if two
  are the same study.
- **Treating zero results as evidence of absence.** Zero results may
  reflect query specificity, non-standard tissue terminology, or
  indexing lag. It does not prove no single-cell data exists for the
  tissue.
