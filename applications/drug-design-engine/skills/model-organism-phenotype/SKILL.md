---
name: model-organism-phenotype
description: >
  Retrieve phenotype annotations for a gene from model organism and
  human phenotype databases — mouse knockout/knockin phenotypes from
  MGI (via the Alliance of Genome Resources) and human phenotype term
  associations from HPO. Use when assessing what happens when a gene
  is disrupted in mouse, what human phenotype terms are associated with
  a gene, or when building the phenotypic component of a target
  validation or safety case. Do not use for human genetic constraint
  evidence (use target-genetic-evidence), tissue expression (use
  tissue-expression-profile or target-genetic-evidence for GTEx), or
  predicted regulatory variant effects (use regulatory-variant-effect).
---

## 1. When to use, and when not

Use this skill when you need model organism phenotype data or human
phenotype ontology associations for a gene. Entry points include:

- Checking what mouse knockout or knockin phenotypes are reported for a
  gene — the observable consequences of gene disruption in a model
  organism.
- Looking up HPO (Human Phenotype Ontology) terms associated with a
  gene through disease-gene mappings.
- Building the phenotypic evidence layer of a target validation case:
  does disrupting this gene produce phenotypes relevant to the
  indication?
- Identifying safety-relevant phenotypes from mouse knockouts as part
  of a preclinical safety review — with the standing caveat that mouse
  phenotypes are informative, not predictive.
- Comparing phenotype profiles across candidate targets.

**Do not use when:**

- You need human genetic constraint (pLI, LOEUF) or disease
  associations (GWAS, ClinVar)
  -> `target-genetic-evidence`.
- You need tissue expression data
  -> `tissue-expression-profile` (multi-tissue) or
  `target-genetic-evidence` (GTEx whole-blood).
- You need predicted regulatory variant effects
  -> `regulatory-variant-effect`.
- You need protein structure confidence
  -> `protein-structure-confidence`.

## 2. Preconditions

- **Gene input**: a gene symbol (e.g. `TP53`, `BRCA1`). MGI resolves
  symbols to mouse orthologs; HPO resolves to NCBI Gene IDs.
- **No authentication** needed — MGI, the Alliance of Genome Resources,
  and HPO are all public APIs.
- **MGI resolution path**: the tool resolves gene symbols through MGI's
  marker search, scrapes the MGI accession ID from the marker detail
  page, then fetches phenotype annotations from the Alliance of Genome
  Resources REST API. This multi-step resolution means that genes
  without MGI marker records or without Alliance phenotype annotations
  are refused with a remedy message.
- **HPO resolution path**: the tool resolves gene symbols through HPO's
  gene search API to an NCBI Gene ID, then fetches phenotype and
  disease annotations.
- Run `dde doctor` before first use.

## 3. Tool invocations

| Question | Run | Writes to |
|---|---|---|
| What mouse phenotypes are reported for this gene? | `dde phenotype search <GENE> --source mgi` | `raw/genomics/<slug>.phenotype-mgi.json`<br>`raw/genomics/<slug>.phenotype-mgi.artifact.json`<br>`raw/genomics/<slug>.phenotype-mgi.meta.json` |
| What human phenotype terms are associated? | `dde phenotype search <GENE> --source hpo` | `raw/genomics/<slug>.phenotype-hpo.json`<br>`raw/genomics/<slug>.phenotype-hpo.artifact.json`<br>`raw/genomics/<slug>.phenotype-hpo.meta.json` |
| What do the phenotype annotations show? | `dde phenotype analyze <GENE> --source <mgi\|hpo>` | `raw/genomics/<slug>.phenotype-<source>.analysis.json` |

Run `search` before `analyze`. `analyze` reads from disk and
summarises without network access.

The `.phenotype-<source>.json` is the verbatim combined API response.
The `.artifact.json` is the structured `dde.phenotype.v1` artifact
with parsed phenotype records. For MGI, each record carries the MP
ontology term, allele type, and PMID references. For HPO, each record
carries the HPO term ID, and the artifact summary includes
gene-level disease associations.

All output options: `--json` for machine-readable output, `--quiet` for
paths only, `--out` to override the default output directory.

## 4. Interpretation contract

### Verdicts

`phenotype analyze` produces:

- **phenotypes_found** — one or more phenotype annotations exist for
  the gene in the queried source. The analysis reports the count of
  phenotypes, unique terms, and the top phenotype terms.
- **no_phenotypes** — no phenotype annotations found.

### MGI phenotype annotations

MGI/Alliance phenotype data describes observable consequences of gene
disruption in mouse — knockout, knockin, and other allele types. Each
annotation carries:

- **phenotype_term**: the Mammalian Phenotype (MP) ontology term (e.g.
  "embryonic lethality", "abnormal heart morphology").
- **mp_id**: the MP ontology identifier.
- **allele_type**: the allele symbol (describes the type of genetic
  modification).
- **references**: PMID references for the annotation.

Multiple phenotype terms for one gene are normal — a knockout may
produce many observable phenotypes. The number and severity of
phenotypes is informative but does not by itself predict human outcomes.

### HPO phenotype annotations

HPO maps genes to human phenotype terms through disease-gene
associations. These are ontology-derived associations, not direct
experimental observations. Each annotation carries:

- **phenotype_term**: the HPO term name (e.g. "Intellectual
  disability", "Short stature").
- **hpo_id**: the HPO identifier.

The artifact summary includes **disease_associations** — the diseases
through which the gene-phenotype link was established. This context
is essential: an HPO term associated with a gene through a rare
Mendelian disease may not be relevant to a common-disease drug target.

### Mandatory relays

| Relay code | Kind | Fires when | Obligation |
|---|---|---|---|
| `phenotype.model_organism_not_human` | Qualifier | MGI phenotypes found (conditional — fires for MGI source only) | State that mouse phenotype data may not translate directly to humans. Species differences in gene function, expression patterns, and compensatory mechanisms mean that a knockout phenotype in mouse is informative but not predictive of human clinical outcomes. |

Check `mandatory_relays` in the `.analysis.json`. Every relay code
present must be satisfied in the finding.

### Consequence rules

- **MGI phenotypes found**: report the count and the most prominent
  phenotype terms. Carry the `model_organism_not_human` qualifier.
  State the organism (Mus musculus) and the allele types observed.
  Embryonic lethality in mouse knockouts is particularly informative
  for safety assessment — but the relay applies: lethal in mouse does
  not mean toxic in human pharmacological inhibition.
- **HPO phenotypes found**: report the phenotype terms and the disease
  associations through which they were established. HPO terms linked
  through a rare Mendelian disease are evidence of gene-disease
  biology, but the clinical relevance to a common-disease program
  depends on the pathway, not on the ontology association alone.
- **No phenotypes**: report as "no phenotype annotations in [source]
  for this gene." This may mean the gene has not been knocked out in
  mouse (for MGI) or has no mapped HPO terms. It is missing data, not
  evidence of no phenotype.
- **Both sources**: when both MGI and HPO data are available, report
  them separately. Mouse phenotypes are experimental observations;
  HPO associations are ontology-derived. Concordance between them
  (e.g. both sources reporting cardiac phenotypes) strengthens the
  evidence but does not prove the human phenotype will match.

### Cross-disciplinary consequences

- Mouse knockout lethality or severe organ phenotypes are material to
  safety assessment regardless of which specialist fetched the data.
  The `model_organism_not_human` relay ensures these are reported as
  model organism observations, not human safety predictions.
- Phenotype data complements genetic constraint evidence (gnomAD) and
  disease association evidence (GWAS, ClinVar). A constrained gene
  with lethal mouse knockouts and strong GWAS associations presents a
  different target profile than one with only GWAS associations.
- HPO disease associations can identify indication-relevant biology
  that GWAS alone may miss — particularly for rare diseases where GWAS
  power is insufficient.
- The absence of mouse phenotype data for a gene does not mean the
  gene can be safely modulated — it means the experiment has not been
  done or has not been annotated in MGI.

### What this section produces

Following this contract produces a Layer 1 finding in `findings/` that
cites the `.analysis.json`, satisfies the `model_organism_not_human`
relay when it fires, names the organism, and does not carry mouse
phenotypes into human safety predictions. Everything the tools emitted
stays under `raw/genomics/`.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the tool
emitted. If the tool did not run, there is no value. If a required tool
is unavailable, report the task as blocked. Do not estimate, and do not
proceed on an assumed result.

### Named pathologies

- **Carrying mouse knockout phenotypes into human safety claims.** A
  lethal knockout in mouse does not mean the drug will be toxic. The
  `model_organism_not_human` relay exists because this is the most
  consequential misreading. Species differences in gene function,
  compensatory mechanisms, and the difference between genetic knockout
  and pharmacological inhibition all limit the inference.
- **Treating HPO associations as direct experimental evidence.** HPO
  maps genes to phenotype terms through disease associations in the
  ontology. These are curated associations, not experimental
  observations. A gene associated with "Intellectual disability" via
  a rare syndrome does not mean modulating the gene causes cognitive
  impairment.
- **Ignoring the disease context of HPO associations.** An HPO term
  linked through a rare autosomal recessive condition has different
  relevance to a common-disease drug program than one linked through
  an autosomal dominant condition affecting the same pathway.
- **Treating no phenotype data as "gene is safe to modulate."** No MGI
  phenotypes means the knockout has not been reported, not that it
  produces no phenotype. Many genes have not been knocked out in mouse
  or have not had their phenotypes deposited in MGI.
- **Conflating phenotype count with severity.** A gene with 50
  phenotype annotations may have many mild or overlapping phenotypes.
  A gene with 3 annotations including embryonic lethality is a more
  significant safety signal. Phenotype terms must be read, not counted.
- **Blending MGI and HPO results into a single phenotype profile.**
  MGI provides mouse experimental data; HPO provides human ontology
  associations. They use different ontologies (MP vs HP), describe
  different organisms, and represent different types of evidence.
  Report them separately and note concordance or discordance.
- **Reporting a gene resolution failure as "no phenotypes."** MGI
  resolution goes through marker search and accession ID scraping. A
  failure at any resolution step is a tool error (refused with
  remedy), not a phenotype finding. Do not report "no phenotypes"
  when the tool could not resolve the gene.
