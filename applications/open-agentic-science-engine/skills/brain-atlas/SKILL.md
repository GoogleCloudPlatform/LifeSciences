---
name: brain-atlas
description: >
  Query brain region expression and donor/disease data from the Allen
  Brain Map -- gene expression across brain structures (human and mouse)
  and the Aging, Dementia, and TBI donor cohort with Braak staging,
  DSM-IV diagnoses, and APOE4 status. Use when looking up brain region
  expression for a gene, when searching for donors with specific
  neurodegenerative diagnoses, or when building the neuroanatomical
  context for a target. Do not use for peripheral tissue expression (use
  tissue-expression-profile), for disease-state transcriptomics outside
  the brain (use disease-transcriptomics), or for single-cell dataset
  discovery (use single-cell-expression).
---

## 1. When to use, and when not

Use this skill when you need brain-specific expression or
neurodegenerative disease donor data. Entry points include:

- Looking up which Allen Brain Atlas products contain expression data
  for a gene (human microarray, mouse ISH, developing brain, etc.).
- Checking whether a gene has been profiled in the Allen human or mouse
  brain atlas.
- Querying the Aging/Dementia/TBI donor cohort for donors with
  Alzheimer's disease, other dementias, or specific Braak stages.
- Building neuroanatomical context for a target — understanding which
  brain regions express it.

**Do not use when:**

- You need peripheral tissue expression (skin, liver, DRG, etc.)
  -> `tissue-expression-profile`.
- You need disease-state transcriptomics outside the brain (skin
  lesions, tumours, etc.) -> `disease-transcriptomics`.
- You need single-cell dataset discovery -> `single-cell-expression`.
- You need genetic constraint or disease association evidence
  -> `target-genetic-evidence`.

## 2. Preconditions

- **Gene search**: a gene symbol (e.g. `GFRA3`, `APP`). The tool
  resolves symbols through the Allen Gene query endpoint.
- **Donor search**: optional filters — `--disease` (DSM-IV diagnosis
  substring), `--dementia` (flag for demented donors), `--min-braak`
  (minimum Braak stage).
- **No authentication** needed — the Allen Brain Map API is public.
- **Brain regions only**: the Allen Brain Atlas covers brain structures.
  Peripheral tissues are not represented. The
  `allen.brain_region_expression_only` relay carries this caveat.
- Run `dde doctor` before first use.

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| Is this gene in the Allen Brain Atlas? | `dde allen search <GENE> [--organism human\|mouse]` | `raw/transcriptomics/<slug>.allen.json`<br>`raw/transcriptomics/<slug>.allen.artifact.json`<br>`raw/transcriptomics/<slug>.allen.meta.json` |
| What do the Allen gene results show? | `dde allen analyze <GENE>` | `raw/transcriptomics/<slug>.allen.analysis.json` |
| What donors have dementia/disease data? | `dde allen donors [--disease D] [--dementia] [--min-braak N]` | `raw/transcriptomics/<slug>.allen-donors.json`<br>`raw/transcriptomics/<slug>.allen-donors.artifact.json`<br>`raw/transcriptomics/<slug>.allen-donors.meta.json` |
| What does the donor cohort look like? | `dde allen analyze-donors [--disease D] [--dementia]` | `raw/transcriptomics/<slug>.allen-donors.analysis.json` |

Run `search`/`donors` before `analyze`/`analyze-donors`. Analyze
commands read from disk with no network access. All tools support
`--json`, `--quiet`, and `--out`.

## 4. Interpretation contract

### Verdicts

- **Gene search**: `gene_found` or `gene_not_found`. When found,
  reports which Allen products/atlases contain the gene, number of
  experiments, and organisms.
- **Donor search**: `donors_found` or `no_donors`. Reports
  dementia/control breakdown, DSM-IV diagnosis distribution, Braak
  stage distribution, APOE4 status, and age/sex demographics.

### What each subcommand provides

**Gene search** returns which Allen Brain Atlas products contain
expression data for the queried gene. Products include the Human Brain
Microarray atlas (6 donors, ~500 brain samples), Mouse Brain ISH atlas,
Developing Human Brain, and disease-specific studies (Autism,
Schizophrenia, Neurotransmitter). The search confirms the gene is
profiled in a product; it does not return expression values per brain
region.

**Donor search** returns metadata for the Aging, Dementia, and TBI
donor cohort (107 donors, 50 with dementia). Each donor record
includes: DSM-IV clinical diagnosis (Alzheimer's, Multiple Etiologies,
Vascular, etc.), Braak stage, CERAD score, NIA-Reagan score, APOE4
status, age, sex, and TBI history. This is donor-level metadata, not
expression data.

### Mandatory relays

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `allen.brain_region_expression_only` | Qualifier | Every gene search (unconditional) | State that Allen Brain Atlas expression data covers brain regions only. Expression in peripheral tissues (skin, DRG, etc.) is not represented. Do not infer absence of expression in non-brain tissues from this dataset. A gene absent from the Allen Brain Atlas may still be expressed in peripheral tissues relevant to the program's indication. |

Check `mandatory_relays` in the `.analysis.json`. Every relay code
present must be satisfied in the finding.

### Consequence rules

- **Gene found in Allen**: report which products/atlases contain the
  gene and the number of experiments. Carry the
  `brain_region_expression_only` relay. This confirms the gene is
  profiled in the Allen brain atlas, not that it is expressed at
  meaningful levels in any specific brain region.
- **Gene not found**: report as "gene not found in Allen Brain Atlas."
  This means the gene has not been profiled in the Allen ISH or
  microarray datasets. It does not mean the gene is not expressed in
  the brain — the Allen Atlas covers a finite set of genes.
- **Donors found**: report the dementia/control breakdown, diagnosis
  distribution, and Braak stage statistics. These describe the donor
  cohort available for brain transcriptomic analysis, not expression
  results from those donors.
- **No donors matching criteria**: report the filter criteria used and
  that no donors matched. Suggest broadening filters.

### Cross-disciplinary consequences

- Brain region expression from the Allen Atlas is relevant when the
  program's mechanism involves central nervous system pathways.
  For peripheral indications (e.g. atopic dermatitis), the Allen Atlas
  may provide context for CNS off-target effects but is not the primary
  expression evidence.
- The Aging/Dementia/TBI donor cohort is a resource for studying
  gene expression changes in neurodegeneration. It is not directly
  relevant to non-neurological indications unless the program
  hypothesis involves a neuro-immune axis.
- The `brain_region_expression_only` relay is critical when the
  program targets peripheral tissues. Failure to carry this qualifier
  could lead to wrongly concluding a gene is not expressed in the
  relevant tissue based on its absence from a brain atlas.

### What this section produces

Following this contract produces a Layer 1 finding in `findings/` that
cites the relevant `.analysis.json` files, satisfies the
`brain_region_expression_only` relay, and clearly states that evidence
comes from a brain-specific atlas. Everything the tools emitted stays
under `raw/transcriptomics/`.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the tool
emitted. If the tool did not run, there is no value. If a required tool
is unavailable, report the task as blocked.

### Named pathologies

- **Inferring peripheral tissue expression from a brain atlas.** The
  Allen Brain Atlas covers brain regions only. A gene's absence from
  the atlas does not mean it is absent from skin, DRG, or other
  peripheral tissues. The relay exists because this inference is
  natural and wrong.
- **Treating Allen donor metadata as expression evidence.** The donor
  search returns cohort metadata (diagnosis, Braak stage, APOE4
  status). It does not return expression values. "50 donors with
  dementia are available" is supported; "gene X is upregulated in
  Alzheimer's brain" is not supported by a donor metadata search.
- **Confusing gene profiling with gene expression.** Finding that a
  gene is in the Allen Human Brain Microarray product means it was
  included on the microarray platform. It does not report its
  expression level in any brain region.
- **Generalising Braak stage distribution to a population.** The
  Aging/Dementia/TBI cohort is 107 donors with specific ascertainment
  criteria. Its Braak stage distribution does not represent the
  general population or any clinical trial cohort.
