# dde CLI — Tool Index

Every `dde` subcommand falls into one of two categories:

1. **Vendor process / control-plane tools** — orchestration machinery around
   work orders, validation, runs, and environment management.  These are tools
   you use to *manage the workflow*, not to do science.
2. **Science tools** — tools that fetch, analyze, or assess actual scientific,
   chemical, or biological data.

This split helps a new reader — human or agent — quickly decide: "Is this a
tool I run as part of doing science, or as part of managing the work-order
lifecycle?"

---

## Vendor process / control-plane tools

| Command | Description |
|---------|-------------|
| `workorder` (`wo`) | Work-order management for the control plane. |
| `validate` | Mechanical validation of submitted work-order deliverables. |
| `run` | Run record management for the control plane. |
| `program` | Cross-phase program management for the control plane. |
| `env` | Environment identity: the value stamped into every sidecar. |
| `doctor` | Assert tools, credentials and environment version. Exits non-zero if broken. |
| `site` | Deterministic site build from accepted work-order deliverables. |
| `relays` | List mandatory-relay codes and their emission sites. |
| `init` | Create a program directory with a .dde/ marker and raw/ tree. |

`wo` is an alias for `workorder`, not a separate tool.

## Science tools

| Command | Description |
|---------|-------------|
| `admet` | ADMET endpoint prediction (RDKit). |
| `alphafold` | AlphaFold DB structures and AlphaFold 3 predictions. |
| `alphagenome` | AlphaGenome regulatory variant scoring against the Vertex endpoint. |
| `analog` | Analog design and validation workflows. |
| `assay` | T3 assay data: ingest and analyze. |
| `compound` | Compound property profiling (RDKit). |
| `compreg` | Compound-registry identity resolution. |
| `conservation` | Evolutionary conservation scoring (rate4site / ConSurf-style). |
| `coscientist` | Co-Scientist tournament exports: ingest, analyze, read. |
| `dice` | DICE immune cell subtype expression (bulk RNA-seq, TPM). |
| `docking` | Molecular docking (AutoDock Vina). |
| `dossier` | IND evidence-package readiness checker. |
| `expression` | Measured human RNA expression (Human Protein Atlas, CC BY 4.0). |
| `faers` | FDA adverse event and drug label lookup. |
| `genetics` | Human population genetics evidence (gnomAD, free and unauthenticated). |
| `gtex` | GTEx whole-blood median gene expression (RNA-seq, TPM). |
| `gwas` | GWAS and disease association lookup. |
| `homology` | Structural homology search via RCSB PDB BLAST. |
| `hypex` | Normalize and analyze a completed DDE Hypex exploration run. |
| `litref` | Resolve a cited paper or trial to a real record — or fail. |
| `mmp` | Matched molecular pair analysis (RDKit BRICS). |
| `mpo` | Multiparameter optimization scoring. |
| `pathway` | Pathway and gene ontology lookup. |
| `phenotype` | Model organism phenotype lookup (MGI, HPO). |
| `pk` | In vivo PK analysis and human dose projection. |
| `pocket` | Binding-site detection and druggability (fpocket). |
| `ppi` | Protein-protein interaction lookup (STRING, free and unauthenticated). |
| `pubmed` | PubMed literature search. |
| `preprint` | Search arXiv and bioRxiv preprints. |
| `screen` | Virtual screening of compound libraries. |
| `selectivity` | Selectivity panel: compare and analyze. |
| `tox` | Preclinical toxicology data processing and assessment. |

> **Note on `dossier`:** classified as a science tool despite touching
> regulatory process — it assesses the completeness of *scientific*
> deliverables (compound/tox/pk artifacts) for a regulatory purpose, not the
> dde tool orchestration process itself.

---

## Further reading

- [BOOTSTRAP.md](BOOTSTRAP.md) — setup and installation
- [Hypex integration](../docs/hypex-integration.md) — vendoring, provisioning, and orchestration boundary
- [docs/workorder-yaml-reference.md](../docs/workorder-yaml-reference.md) — work-order YAML schema
- [docs/tool-design-guidance.md](../docs/tool-design-guidance.md) — conventions for building new tools
- [docs/operating-environment.md](../docs/operating-environment.md) — runtime environment details
