# DDE: Agentic Pre-Clinical Pharmaceutical R&D System

> **Companion guidance.** This document describes *what* dde is and how it is
> organized. Three companion documents are normative for *how* its orchestration,
> skills, and tools are built:
>
> - [`orchestration-design-guidance.md`](orchestration-design-guidance.md) — the
>   authority model, work-order contract, control plane, agent lifecycle, validation,
>   scientific acceptance, and publication pipeline.
> - [`skill-design-guidance.md`](skill-design-guidance.md) — what belongs in a skill,
>   how tools are grouped into capabilities, and how skills route to tool invocations.
>   One capability serves several roles, so skills stay specialist-neutral.
> - [`tool-design-guidance.md`](tool-design-guidance.md) — the tools environment, the
>   `dde` CLI, and the artifact contract (paths, provenance sidecars, thresholds,
>   output discipline). **Normative for anything artifact-related**; §5 below defers
>   to it on specifics.

## 1. What DDE Is

DDE is a packaged repository that bootstraps an agentic system for end-to-end pre-clinical pharmaceutical research and development. Built on scion (an agent orchestration platform), it provides the agent templates, skills, tools, and artifact conventions needed to stand up a coordinated multi-agent research team for any drug discovery program.

Each research project gets its own agent team and project folder. The process begins by creating a Science Program Lead and giving it a scientific objective — a disease indication, a target hypothesis, a modality. The lead creates a persistent Research Operations Controller, then issues scientific work orders as the program evolves. The controller supervises ephemeral specialist agents and maintains the operational control plane; the science lead accepts evidence, coordinates the four stages of pre-clinical R&D, and maintains the structured project state that serves as its decision surface.

---

## 2. Design Principles

### 2.1 Don't Re-teach the LLM What It Already Knows

Modern LLMs already possess deep knowledge of pharmaceutical R&D — professional quality standards, scientific reasoning frameworks, and domain expertise across structural biology, medicinal chemistry, ADMET science, and more. Role definitions in dde are deliberately lean: a short persona trigger activates the right parametric knowledge, available tools tell the agent what it can invoke, and output contracts tell it what format to report in. The stage-specific question comes from the Science Program Lead's work order, not from hundreds of lines of re-taught domain knowledge.

A structural biologist agent definition doesn't explain what pLDDT is or how to check for crystal packing artifacts — the LLM already knows. It says: "You are a structural biologist. You have access to these tools. Report findings in this format." The expertise and professional standards come from the model's training; the tools and formats come from dde.

### 2.2 Invariant Structure, Dynamic Content

The four-stage pre-clinical pipeline (Intervention Point → Starting Matter → Multiparameter Optimization → Human Readiness) is invariant — it represents the universal drug R&D value chain regardless of modality (small molecule, biologic, RNA therapeutic, gene therapy, PROTAC). But within each stage, everything is dynamic:

- Which specialist roles participate depends on the modality and science (an antibody program doesn't need a computational chemist for docking; a well-characterized kinase can skip structural prediction)
- Which workflows execute depends on the scientific context (GWAS-driven programs use variant fine-mapping; phenotypic programs start with target deconvolution; repurposing programs may skip Stages 1-2 entirely)
- The Science Program Lead selects and sequences work based on the evolving project state, not a fixed script

### 2.3 Separation of Reporting from Reasoning

Stage gate documents (Intervention Validation Package, Starting Matter Declaration, Candidate Nomination Dossier, Human Readiness Package) are reporting artifacts for external stakeholders — the R&D steering committee, the portfolio review board. They are retrospective summaries, not the mechanism for internal decision-making.

The Science Program Lead's real-time reasoning happens against the living project state — a continuously-updated collection of accepted specialist findings, active compound series, known liabilities, and open questions. This separation prevents the system from conflating checkpoint documentation with operational routing logic.

### 2.4 Ephemeral Specialists with Rich Task Context

Specialist agents don't need to be persistent across the entire project lifecycle. The Science Program Lead maintains project state and commits the relevant context slice in each work order. The Research Operations Controller dispatches that immutable work order to the specialist. A structural biologist spun up in Stage 3 doesn't need the full Stage 1 conversation history — it needs: "Here is the target structure. The 145-160 loop is flexible (observed in Stage 1 modeling). The catalytic water at W301 is displaceable. Interpret this co-crystal in that context."

The quality of the work order and its immutable context snapshot determines whether ephemeral specialists can match persistent ones. The science lead owns the question and context; the operations controller owns reliable execution. See [`orchestration-design-guidance.md`](orchestration-design-guidance.md) §3.

### 2.5 Computation in Tools, Judgment in Skills

Anything that can be computed belongs in the `dde` CLI. Anything that constitutes a threshold belongs in its configuration. What remains in a skill is the part a model cannot get from tool output alone: whether to run the tool, and what the result licenses the specialist to claim.

This division exists for a specific reason. Principle 2.1 — lean roles that don't re-teach domain knowledge — combined with an output contract that demands quantitative evidence, produces a fabrication risk whenever a specialist is asked for a number it has no tool to compute. A model asked for a pocket volume with no pocket-detection tool will produce a plausible one. The mitigations are structural, not exhortative:

- Every capability a role or skill claims must map to a real tool invocation.
- Tools fail loudly and never synthesize; a missing tool yields a blocked task, not an estimate.
- Every tool output is a durable, checksummed artifact, so a claim can be traced to the computation that produced it — or shown not to have one.

A third boundary follows from these two. Because one capability serves several specialists, skills must stay specialist-neutral. The role-specific part — which question is being asked, and what happens next — belongs in the template. See §4.

See [`skill-design-guidance.md`](skill-design-guidance.md) and [`tool-design-guidance.md`](tool-design-guidance.md).

### 2.6 One Scientific Authority, Separate Operational Control

Scientific orchestration and operational orchestration are separate concerns. The Science Program Lead is the sole authority for scientific direction, evidence acceptance, Layer 2 state, and stage gates. A persistent Research Operations Controller has bounded autonomy to validate and execute approved work orders, supervise agents, manage resources and retries, validate artifact contracts, and publish accepted artifacts.

These are not peer orchestrators. The controller cannot change a scientific question, accept an interpretation, or advance a gate. Their durable handoff is a versioned work order, not conversation history. Operational records live in a machine-readable control plane outside the five scientific artifact layers. The full normative design is in [`orchestration-design-guidance.md`](orchestration-design-guidance.md).

---

## 3. The Four Stages of Pre-Clinical R&D

### Stage 0: Hypothesis Entry

Before the four invariant stages begin, a program acquires its initial hypothesis set
through one of four strategies: sponsor-supplied adoption, charter-authored hypotheses,
a Co-Scientist tournament export, or a hypex tournament run within the program. This
is Stage 0 — how a program acquires something to take into Stage 1. See the
`hypothesis-entry` skill for strategy selection and availability.

### The four invariant stages

The four stages represent a universal abstraction of drug R&D, independent of modality. Every program — small molecule, biologic, RNA therapeutic, gene therapy, PROTAC, drug repurposing — passes through these stages, though the specific work within each stage varies dramatically by modality and scientific context.

### Stage 1: Identify & Validate the Intervention Point

**Objective:** Establish a high-confidence causal link between a molecular intervention point (protein target, pathway node, transcript, cell surface antigen) and the disease, and confirm that the intervention point is tractable for the chosen modality.

**Typical roles:** Computational Biologist, Structural Biologist, Experimental Biologist. Modality-specific specialists may join early (e.g., antibody engineer for surface antigen assessment).

**Gate question:** Is there sufficient causal evidence linking this intervention point to the disease, and is it tractable for our modality?

**Decision gate criteria** (adapted per modality and program):
- Causal evidence: genome-wide significance (p < 5×10⁻⁸), replicated Mendelian variant, validated functional perturbation, or clinical proof-of-concept from related mechanism
- Tractability: confirmed druggable interface for the chosen modality (binding pocket, accessible epitope, targetable transcript, etc.)
- Safety: genetic constraint data (gnomAD LOEUF > 0.35) or clinical precedent suggesting acceptable on-target safety
- Functional validation: phenotypic rescue, loss/gain-of-function, or clinical correlation in disease-relevant model

**How this stage looks across modalities:**

| Program Type | Intervention Point | Key Validation Work |
|---|---|---|
| **Small molecule (GWAS-driven)** | Protein target from genetic association | Non-coding variant fine-mapping (AlphaGenome), eQTL colocalization, binding pocket druggability (AlphaFold + fpocket) |
| **Small molecule (phenotypic)** | Unknown at start — target deconvolution | Phenotypic screen first (Stage 2 may precede Stage 1), then chemoproteomic target ID, CETSA, photoaffinity labeling |
| **Antibody / biologic** | Cell surface receptor or soluble ligand | Epitope mapping, receptor expression profiling across tissues, internalization kinetics, species cross-reactivity |
| **RNA therapeutic (ASO/siRNA)** | mRNA transcript | Transcript isoform mapping, tissue-specific expression, sequence conservation, off-target hybridization prediction |
| **PROTAC / molecular glue** | Protein target + E3 ligase pair | Target degradability assessment, E3 ligase tissue expression, ubiquitin-proteasome pathway validation |
| **Gene therapy** | Gene / regulatory element | Delivery vector tropism, transgene expression cassette design, immunogenicity of vector |
| **Rare Mendelian disease** | Known causal gene | Skip fine-mapping, go straight to structural tractability and functional validation in patient-derived cells |
| **Drug repurposing** | Known target with existing compound | Partial or full skip — existing clinical data may satisfy validation; focus shifts to new indication evidence |
| **Complex polygenic (Alzheimer's, NASH)** | Pathway node from multi-omic integration | Co-expression network analysis, Mendelian randomization, multi-tissue eQTL, convergent evidence scoring |

### Stage 2: Find Starting Matter

**Objective:** Identify initial active entities (compounds, sequences, constructs) that engage the validated intervention point, confirm activity, eliminate artifacts, and select 2-3 tractable series or candidates for optimization.

**Typical roles vary by modality:** Computational Chemist + Medicinal Chemist (small molecule), Antibody Engineer + Immunologist (biologics), Sequence Designer (RNA), Structural Biologist (cross-modality for binding confirmation).

**Gate question:** Do we have confirmed, tractable starting matter with a viable path to optimization?

**Decision gate criteria** (adapted per modality):
- Confirmed target engagement with quantitative activity metric appropriate to the modality
- Viable path to optimization (synthetic feasibility, sequence design space, maturation potential)
- Clean artifact profile (no assay interference, aggregation, or false positives)
- Structural or mechanistic understanding of how the entity engages the target

**How this stage looks across modalities:**

| Program Type | Starting Matter | Key Identification Work |
|---|---|---|
| **Small molecule (structure-based)** | Hit compounds from virtual screen | Docking campaign → focused HTS confirmation → co-crystal binding pose → PAINS/aggregator filtering |
| **Small molecule (phenotypic)** | Active compounds from cell-based screen | Full HTS primary screen → dose-response confirmation → counter-screens → target deconvolution |
| **Small molecule (fragment-based)** | Fragment hits | Biophysical fragment screening (SPR, DSF, NMR) → fragment soaking/co-crystals → fragment merging/growing |
| **Antibody / biologic** | Antibody leads from discovery campaign | Phage display or hybridoma screening → functional assay → epitope binning → early developability assessment |
| **RNA therapeutic** | Active sequences | Sequence walk across target transcript → in vitro potency screening → off-target hybridization filtering |
| **PROTAC** | Bifunctional degrader leads | Binary binding assessment (target warhead + E3 ligase binder) → ternary complex formation → degradation confirmation (DC₅₀) |
| **Drug repurposing** | Existing approved/clinical compound | May skip entirely — starting matter already exists. Focus on confirming activity in new indication context |

### Stage 3: Multiparameter Optimization

**Objective:** Intensive iterative optimization to evolve starting matter into entities that simultaneously satisfy efficacy, safety, and developability requirements. This is typically the longest and most resource-intensive stage.

**Typical roles:** Modality-appropriate design specialists, ADMET/DMPK Scientist, Structural Biologist (for binding mode guidance), Experimental Biologist (for cellular/functional readouts).

**Gate question:** Do we have an optimized entity that meets all critical quality attributes for advancement to safety studies?

**Decision gate criteria** (adapted per modality):
- Potency: modality-appropriate metric at target threshold (e.g., cellular IC₅₀ < 50 nM, DC₅₀ < 10 nM, KD < 1 nM)
- Selectivity: adequate margin over relevant off-targets (homologs, kinome panel, tissue cross-reactivity)
- Drug-like properties: metabolic stability, permeability/bioavailability (small molecule); stability, aggregation, viscosity (biologic); metabolic stability, tissue distribution (RNA)
- Safety margin: hERG/ion channel (small molecule); immunogenicity, effector function (biologic); complement activation, hepatotoxicity (RNA)

**Reasoning cadence:** This stage has a natural batch rhythm tied to iterative design cycles (DMTA for small molecules, affinity maturation rounds for antibodies, sequence optimization cycles for RNA). The Science Program Lead reviews accumulated accepted results as a cohort to spot emergent patterns invisible in individual results. Structured critical safety alerts trigger immediate re-evaluation.

**How this stage looks across modalities:**

| Program Type | Optimization Focus | Iterative Cycle |
|---|---|---|
| **Small molecule** | SAR-driven MPO: potency, selectivity, metabolic stability, permeability, hERG, solubility | DMTA rounds (10-20 compounds/round): design analogs → synthesize → assay → analyze SAR → repeat |
| **Antibody** | Affinity maturation, developability, effector function tuning | CDR mutagenesis rounds: design variants → express → bind/function → stability/aggregation → repeat |
| **RNA therapeutic** | Potency, metabolic stability, off-target reduction, delivery optimization | Sequence/chemistry modification rounds: modify backbone/sugar → potency assay → off-target profiling → repeat |
| **PROTAC** | Ternary complex optimization, linker length/rigidity, selectivity, PK | Linker/warhead DMTA: vary linker → degradation assay → selectivity panel → metabolic stability → repeat |
| **Gene therapy** | Expression level, durability, immunogenicity, manufacturing | Vector engineering cycles: capsid/promoter variants → transduction efficiency → expression duration → repeat |

### Stage 4: Demonstrate Human Readiness

**Objective:** Generate the safety, efficacy, pharmacology, and regulatory evidence package required to support first-in-human clinical studies. Modality-specific regulatory frameworks apply.

**Typical roles:** Preclinical Toxicologist, ADMET/DMPK Scientist (in vivo), Regulatory Scientist, Experimental Biologist (in vivo efficacy).

**Gate question:** Do we have a complete, GLP-compliant evidence package supporting an acceptable risk-benefit for human dosing?

**Decision gate criteria** (adapted per modality):
- In vivo efficacy at pharmacologically achievable exposures
- Therapeutic index ≥ 10-fold (safety exposure / efficacious exposure)
- Viable human dose projection with acceptable administration route
- GLP-compliant safety/toxicology package with no unmitigated signals
- Regulatory-compliant dossier (IND, CTA, or equivalent) for the target jurisdiction

**How this stage looks across modalities:**

| Program Type | Key Safety/Regulatory Work |
|---|---|
| **Small molecule** | 28-day GLP tox (two species), safety pharmacology (hERG, Irwin, respiratory), PBPK human dose projection, CYP/DDI, genotoxicity (Ames, micronucleus), IND Module 4 |
| **Antibody / biologic** | Tissue cross-reactivity, immunogenicity (ADA), repeat-dose tox in relevant species (often NHP), PK/PD modeling, BLA-track CMC |
| **RNA therapeutic** | Complement activation, hepatotoxicity, off-target transcript profiling in vivo, biodistribution, delivery vehicle safety |
| **PROTAC** | Standard small-molecule tox PLUS hook effect assessment, E3 ligase tissue safety, degradation selectivity in vivo |
| **Gene therapy** | Biodistribution, vector shedding, long-term expression durability, insertional mutagenesis risk, immunogenicity of vector and transgene |
| **Drug repurposing** | Abbreviated — leverage existing safety data, focus on new indication PK/PD and any additional safety studies required by regulators |

---

## 4. Roles: What's Invariant, What's Dynamic

### Role Names and Template Names

A role has one name. A template has one directory name. The two differ in one place,
and this table is the only mapping:

| Role name, used in all documents | Template directory |
|---|---|
| Science Program Lead | `science-program-lead` |
| Research Operations Controller | `research-operations-controller` |
| Head of Discovery | `head-of-discovery` |
| Scientific Reviewer | `scientific-reviewer` |
| Specialist roles | The role name, in lower case, with hyphens |

Do not introduce a third name for the same role. "Orchestrator" alone is ambiguous,
because dde has two orchestrating roles with different authority. Use the role name
in prose and the directory name in configuration.

### Cross-Stage Role Participation

Roles are not confined to a single stage. In real pharmaceutical R&D, key disciplines span multiple stages with evolving responsibilities:

| Role | Intervention Point | Starting Matter | Optimization | Human Readiness |
|---|---|---|---|---|
| Structural Biologist | Target structure prediction, pocket/epitope assessment | Binding mode confirmation, co-complex analysis | Induced-fit SAR guidance, selectivity engineering | — |
| Experimental Biologist | Functional validation (CRISPR, cell models) | Screening execution, dose-response, biophysical confirmation | Cellular potency, selectivity panels | In vivo efficacy models |
| Computational Chemist | — | Virtual screening, artifact filtering | FEP/RBFE, MMP analysis, ML property prediction | — |
| ADMET/DMPK Scientist | — | (Early triage) | In vitro ADME, CYP, permeability | In vivo PK, DDI, human dose projection |
| Medicinal Chemist | — | Tractability assessment, scaffold selection | SAR optimization, bioisosteres, MPO | — |
| Regulatory Scientist | — | — | — | Dossier assembly, GLP compliance |

Each stage-specific question is defined by the Science Program Lead in a versioned work order with the relevant project context, then dispatched by the Research Operations Controller. The role definition itself is stage-agnostic — it defines *who the specialist is*, not *what specific stage work it does*.

### Roles and Capabilities Are Many-to-Many

A capability is not owned by a role. The AlphaFold cluster serves the structural biologist in Stage 1, the computational biologist reading a variant position, the computational chemist deciding whether a pocket is resolved enough to dock into, and the medicinal chemist rationalising an SAR trend. Genetic evidence tooling serves the computational biologist in Stage 1 and the toxicologist in Stage 4 — same four APIs, opposite ends of the pipeline.

Three dimensions vary independently, and each has a different home:

| Dimension | Home |
|---|---|
| Tool | The capability skill |
| Role | The template — the `skills:` grant, the questions owned, the handoffs |
| Stage | The Science Program Lead's work order (principle 2.4) |
| Program and modality | `.dde/thresholds.yaml`, and which skills the template grants |

Stage is deliberately not templated. Ten roles across four stages would be forty templates. Principle 2.4 already solves this: the specialist is ephemeral, and the stage-specific question arrives in the work order.

Some capabilities do bind to one role — IND dossier assembly, independent scientific review, stakeholder narrative editing, GLP study design. These sit at the ends of the pipeline, where the work is procedural. Deterministic site generation is CLI functionality, not an agent capability. The many-to-many science capabilities sit in Stages 1 to 3, where several disciplines interrogate the same data with different questions.

Grant capabilities generously. An unused skill costs one description, roughly 60 tokens. A missing one costs a blocked task, or a specialist that reasons its way to a number it had no means to compute. The asymmetry runs opposite to the context-budget intuition.

### Lean Role Definitions

Each agent template contains:

1. **Persona trigger** (1-2 sentences): Activates the LLM's parametric domain knowledge. "You are a structural biologist specializing in protein-ligand interactions and druggability assessment."

2. **Capability grant**: The `skills:` list in `scion-agent.yaml`. This is the only declaration of what the agent can do, and it is genuinely novel information the model does not have from training. The role definition must not restate the list in prose. An unsynchronised second copy will drift, and a specialist that believes it has a tool it was never granted is the precondition for reporting a value that no tool computed.

3. **Output contract**: The semi-structured report format expected by the Science Program Lead and downstream consumers. Standard headings, linking conventions, confidence reporting requirements.

4. **Organization-specific policy overrides** (if any): Proprietary thresholds, company-specific decision criteria, or enterprise policy constraints that the LLM cannot know from training.

What is explicitly **not** in the role definition: step-by-step procedural workflows. Those are either already in the LLM's training (a structural biologist knows how to interpret pLDDT scores) or provided dynamically by the Science Program Lead's work order.

---

## 5. Artifact Architecture

### 5.1 The Five Layers

Project artifacts are organized into five layers of increasing abstraction, stored in the filesystem and navigable through a project website.

**Layer 0 — Raw Tool I/O:** Everything the tools emit. Model output formats (AlphaFold `.cif`, PAE JSON, docking poses), assay plate reader CSVs, RDKit descriptor tables — plus two derived-but-uninterpreted classes: the `.meta.json` provenance sidecar written alongside every artifact, and the `.analysis.json` structured verdict produced by a CLI `analyze` phase. All of it is machine-readable and deterministic; none of it involves professional judgment. Nothing a tool writes goes anywhere but `raw/`.

**Layer 1 — Specialist Findings:** The prose conclusion of a decision step. What a role concluded after weighing Layer 0 output against the program's context — the specialist's interpretation, not a reformatting of the numbers. A finding that merely restates an `.analysis.json` adds no judgment and defeats the layer separation.

The boundary between Layer 0 and Layer 1 is the boundary between *computed* and *judged*, and it is what makes review mechanical: a reviewer re-runs `analyze` against the stored artifact, diffs the result, and then asks whether the finding's prose is supported by it. See [`tool-design-guidance.md`](tool-design-guidance.md) §3 for the artifact contract.

**Layer 2 — Science Program Working State:** The Science Program Lead's decision surface. Synthesizes across scientifically accepted findings into a running project narrative: active compound series, known liabilities, decision rationale, open questions. This is the "living project notebook" the science lead reasons over to decide what to do next.

**Layer 3 — Stage Gate Documents:** Formal deliverables for external stakeholders. Intervention Validation Package, Starting Matter Declaration, Candidate Nomination Dossier, Human Readiness Package. Compiled from Layer 1-2 artifacts, narrativized for the review committee. These are reporting snapshots, not reasoning inputs.

**Layer 4 — Executive Summary:** One-page program status for portfolio-level stakeholders. Current stage, lead series status, key risks, timeline.

### 5.2 Project Filesystem Layout

The program directory is identified to the CLI by `.dde/` walk-up discovery
(preferred) or by setting `DDE_PROJECT` explicitly. In a standalone program
workspace, the program directory is `/workspace`. When `/workspace` is the
dde repo itself, point `DDE_PROJECT` at the program subdirectory.

```
project-<name>/                       # program root (resolved via .dde/ walk-up or $DDE_PROJECT)
├── .dde/                          # Configuration and the operational control plane
│   ├── thresholds.yaml               # Program gate criteria; overrides CLI defaults
│   ├── program.yaml                  # Identity, stage, policy versions, approval boundaries
│   └── control/                      # Machine-written records; not a sixth artifact layer
├── raw/                              # Layer 0: everything the tools emit
│   ├── docking/
│   ├── structures/                   # e.g. AF-P00520-F1.cif
│   │                                 #      AF-P00520-F1.meta.json    (provenance sidecar)
│   │                                 #      AF-P00520-F1.analysis.json (structured verdict)
│   ├── assay-data/
│   ├── descriptors/
│   └── literature/
├── findings/                         # Layer 1: Specialist Reports
│   ├── structural-biology/
│   ├── computational-biology/
│   ├── medicinal-chemistry/
│   ├── computational-chemistry/
│   ├── admet-dmpk/
│   ├── experimental-biology/
│   └── regulatory/
├── program-state/                    # Layer 2: Science Program Working State
│   ├── active-series.md
│   ├── liability-tracker.md
│   ├── decision-log.md
│   └── open-questions.md
├── gates/                            # Layer 3: Stage Gate Documents
│   ├── stage1-intervention-validation/
│   ├── stage2-starting-matter-declaration/
│   ├── stage3-candidate-nomination/
│   └── stage4-human-readiness-package/
└── executive/                        # Layer 4: Executive Summary
    └── program-summary.md
```

The contents of `.dde/control/` are specified in
[`orchestration-design-guidance.md`](orchestration-design-guidance.md) §4. That
document owns the control-plane layout; this one does not restate it.

### 5.3 Semi-Structured Report Conventions

Reports at Layers 1-2 follow consistent conventions without rigid schemas. Standard headings provide navigability; content within sections is free-form narrative.

**Layer 1 — Specialist Finding Template:**

```markdown
# [Finding Title]
**Role**: [role-name] | **Date**: [date] | **DMTA Round**: [if applicable]
**Work order**: [link to immutable work-order revision]

## Summary
2-3 sentence bottom line up front.

## Key Findings
Narrative with inline references to deeper artifacts:
'Pocket volume is 420Å³ ([raw/structures/target-apo-af3.pdb])
with key H-bond network involving Asp184, Glu276, and a bridging
water ([raw/structures/target-waters.json]).'

## Implications
What this means for the program direction. References to lateral
findings when relevant: 'Induced-fit binding suggests rigid docking
scores underestimate affinity — see
[findings/computational-chemistry/docking-protocol-v2.md].'

## Open Questions
Unresolved items that may require follow-up from this or another role.

## Caveats & Confidence
Model confidence metrics, experimental limitations, assumptions made.
```

**Layer 2 — Science Program Working State Example:**

```markdown
# Active Series Tracker

## Series A: Pyrazole Core
**Status**: Active — Stage 3 Multiparameter Optimization, DMTA Round 4
**Current best**: Compound A-047 (IC50 12nM, HLM CLint 18 µL/min/mg)
**Key liability**: hERG margin at 15x — needs improvement
  ([findings/admet-dmpk/series-a-herg-panel-r4.md])
**SAR summary**: R2 fluorination improves metabolic stability without
  potency loss. R3 size correlates with hERG liability.
  ([findings/medicinal-chemistry/series-a-round4-sar.md])
**Decision pending**: If Round 5 doesn't achieve >30x hERG margin,
  deprioritize series in favor of Series B.

## Series B: Aminopyridine Core
**Status**: Active — Stage 3, DMTA Round 2
**Current best**: Compound B-019 (IC50 85nM, HLM CLint 9 µL/min/mg)
**Strength**: Clean safety profile, excellent metabolic stability
**Weakness**: Potency 7x behind Series A
**Next action**: Scaffold modification at C5 to improve target engagement
  ([findings/structural-biology/series-b-cocrystal-r2.md])
```

### 5.4 Linking Conventions

Every factual claim in a report links to its supporting artifact:

- **Vertical (deeper):** `[raw/structures/file.pdb]` — relative paths down to Layer 0 raw data
- **Lateral (peer findings):** `[findings/medicinal-chemistry/series-a-sar.md]` — cross-references to related specialist work at the same layer
- **Upward (program state):** `[program-state/active-series.md#series-a]` — anchoring into the Science Program Lead's decision surface

These links serve double duty: they provide audit trails for scientific rigor, and they give the deterministic presentation builder its drill-down navigation structure.

The five artifact layers do not include work orders, run state, retry history, or publication state. Those records belong to the operational control plane under `.dde/control/`. They are auditable but are not scientific citation sources. See [`orchestration-design-guidance.md`](orchestration-design-guidance.md) §4.

### 5.5 Project Website and Dashboards

The website and dashboards are deterministic projections of accepted artifacts. The
`dde` CLI builds them, and the Research Operations Controller supervises the build.
The layer hierarchy becomes the navigation structure: executive summary → program
state → specialist findings → raw data. Draft, rejected, and mechanically invalid
findings stay out of the default stakeholder view.

Agents read the filesystem documents directly. The presentation layer serves human
observers and stakeholders, not agent reasoning. An optional Project Curator agent may
improve the executive narrative or design a new stakeholder view. It does not own
synchronization, link integrity, or scientific currency.

The build steps, the validation rules, and the publication record are specified in
[`orchestration-design-guidance.md`](orchestration-design-guidance.md) §7.

---

## 6. Program Orchestration

The user creates one Science Program Lead. It receives the scientific objective and is the sole authority for scientific direction and decisions. The lead creates one persistent Research Operations Controller to execute approved work orders and supervise the operational lifecycle. Detailed authority, state transitions, and handoff contracts are normative in [`orchestration-design-guidance.md`](orchestration-design-guidance.md).

### 6.1 Division of Labour

| Concern | Science Program Lead | Research Operations Controller |
|---|---|---|
| Question | Selects the decision question and the role | Executes it without changing it |
| Context | Chooses the evidence slice and commits the snapshot | Dispatches the snapshot unaltered |
| Execution | — | Agents, dependencies, leases, retries, run records |
| Validation | Judges whether the science is sound | Checks paths, schemas, provenance, links |
| Layer 2 | Sole author | No write authority |
| Gates | Decides advance, loop, pivot, pause, or terminate | Records and reports; decides nothing |
| Publication | Authorizes which artifacts are accepted | Builds and deploys the projection |

Mechanical validation is not scientific acceptance. The controller may return a
malformed deliverable for correction. Only the science lead can accept its
interpretation and bring it into Layer 2.

**Bootstrap is the one exception to the Execution row.** The science lead
creates the Research Operations Controller, and only the controller, and only
at bootstrap (§7.3 step 3). After that the direction is fixed: the lead commits
work orders, and the controller creates specialists and reviewers. Read without
this exception, the Execution row forbids the step that starts the program.
State the exception whenever the prohibition is restated, or it will be read as
absolute.

The full duty lists, the forbidden actions, and the state machine are normative in
[`orchestration-design-guidance.md`](orchestration-design-guidance.md) §2 and §5.

### 6.2 Dynamic Routing Examples

The science lead's routing is context-dependent, not scripted:

**Reordering roles based on evidence:** "The computational biologist found strong GWAS signal but no known protein structure. Prioritize the structural biologist before the experimental biologist — without a tractable interface there's no point designing CRISPR experiments for an untractable intervention point."

**Pulling work forward:** "ADMET flagged a CYP2D6 liability in early starting matter triage. Pull the medicinal chemist's analog expansion forward before committing to a full HTS campaign on this scaffold."

**Skipping unnecessary work:** "This is a well-characterized kinase with 200+ published co-crystal structures. Skip Stage 1 structural work entirely, proceed to virtual screening for starting matter."

**Looping back:** "Optimization SAR hit a potency plateau. The original docking pose relied on a hydrogen bond to Asp184 that the co-crystal now shows doesn't form. Re-engage the structural biologist to reinterpret the binding mode before the next synthesis cycle."

**Modality pivot:** "Small molecule optimization revealed the target has a flat, featureless binding surface — no tractable pocket for conventional inhibition. Pivot to a PROTAC degrader strategy, engaging new specialists for E3 ligase selection and ternary complex modeling."

**Program termination:** "Three independent scaffold series all show hERG liability below 30x margin. The liability tracker shows this correlates with the target's structural similarity to hERG channel — this is on-target, not fixable by chemistry. Recommend termination."

### 6.3 Reasoning Cadence

The Science Program Lead balances two modes while the Research Operations Controller tracks when a declared cohort becomes reviewable and routes structured alerts:

- **Batch review:** After a DMTA round or declared set of work orders completes, the science lead reads the full accepted project state holistically. This is where emergent patterns are spotted — trends across compound series, correlations between structural features and liabilities. Batch reasoning is strictly superior to event-driven reasoning for pattern recognition.

- **Interrupt-driven escalation:** Pre-defined conditions in structured analysis output or program policy trigger immediate re-evaluation. Ames positive, hERG breach, unexpected binding mode — these bypass the normal batch cadence. The controller may pause dependent work when policy requires it, but the science lead decides the scientific response.

The ratio adapts to the program stage: relaxed cadence in early exploration (Stages 1-2: intervention point and starting matter), tighter review in late-stage convergence (Stages 3-4: optimization and human readiness) where the cost of wasted effort is highest.

---

## 7. DDE Repository Structure

```
dde/
├── templates/                         # Scion agent templates
│   ├── science-program-lead/          # Science Program Lead; user entry point
│   │   ├── scion-agent.yaml
│   │   ├── agents.md
│   │   └── system-prompt.md
│   ├── research-operations-controller/ # Persistent operational controller
│   │   ├── scion-agent.yaml
│   │   ├── agents.md
│   │   └── system-prompt.md
│   ├── scientific-reviewer/           # Ephemeral independent evidence audit
│   │   ├── scion-agent.yaml
│   │   ├── agents.md
│   │   └── system-prompt.md
│   ├── structural-biologist/
│   │   ├── scion-agent.yaml
│   │   ├── agents.md
│   │   └── system-prompt.md
│   ├── computational-biologist/
│   │   ├── scion-agent.yaml
│   │   ├── agents.md
│   │   └── system-prompt.md
│   ├── medicinal-chemist/
│   │   ├── scion-agent.yaml
│   │   ├── agents.md
│   │   └── system-prompt.md
│   ├── computational-chemist/
│   │   ├── scion-agent.yaml
│   │   ├── agents.md
│   │   └── system-prompt.md
│   ├── admet-dmpk-scientist/
│   │   ├── scion-agent.yaml
│   │   ├── agents.md
│   │   └── system-prompt.md
│   ├── experimental-biologist/
│   │   ├── scion-agent.yaml
│   │   ├── agents.md
│   │   └── system-prompt.md
│   ├── preclinical-toxicologist/
│   │   ├── scion-agent.yaml
│   │   ├── agents.md
│   │   └── system-prompt.md
│   ├── regulatory-scientist/
│   │   ├── scion-agent.yaml
│   │   ├── agents.md
│   │   └── system-prompt.md
│   └── project-curator/               # Optional editorial presentation work
│       ├── scion-agent.yaml
│       ├── agents.md
│       └── system-prompt.md
├── skills/                            # One directory per skill, each with a SKILL.md
│   ├── artifact-conventions/          # Report format, headings, linking conventions
│   ├── <orchestration skills>/        # Inventory: orchestration-design-guidance §8
│   └── <science capability skills>/   # Grouping test: skill-design-guidance §2
│                                      #   e.g. protein-structure-confidence,
│                                      #        target-genetic-evidence
├── tools/                             # The dde CLI and its environment
│   ├── install.sh                     # Provisions the shared environment
│   ├── requirements.txt
│   ├── pyproject.toml                 # PEP 621 packaging; console_scripts entry point
│   └── dde/
│       ├── cli.py                     # Click entry point; one group per tool
│       ├── common.py                  # Shared group and state plumbing
│       ├── core/                      # context, env, errors, http, output,
│       │                              #   provenance, thresholds
│       └── commands/                  # One module per tool; phases as subcommands
├── artifact-templates/                # Stamped into each new project
│   ├── layer0-raw/
│   │   └── README.md
│   ├── layer1-findings/
│   │   └── finding-template.md
│   ├── layer2-program-state/
│   │   ├── active-series.md
│   │   ├── liability-tracker.md
│   │   ├── decision-log.md
│   │   └── open-questions.md
│   ├── layer3-gates/
│   │   ├── intervention-validation.md
│   │   ├── starting-matter-declaration.md
│   │   ├── candidate-nomination.md
│   │   └── human-readiness-package.md
│   └── layer4-executive/
│       └── program-summary.md
├── docs/
│   ├── dde-plan.md                 # This document
│   ├── orchestration-design-guidance.md
│   ├── skill-design-guidance.md
│   ├── tool-design-guidance.md
│   ├── contributing.md
│   └── code-of-conduct.md
└── README.md
```

Two documents own the skill inventory, and this one does not restate either. The
orchestration skills are listed in `orchestration-design-guidance.md` §8. The science
capability skills come from the grouping test in `skill-design-guidance.md` §2, which
is expected to yield roughly 8–12 skills rather than a 1:1 conversion of the ~38
upstream skills.

The CLI is a Python package installed as an editable `console_scripts` entry point
via `pip install --no-deps -e .` (see `pyproject.toml`). `install.sh` handles this
automatically, producing a `dde` command in the venv bin that works correctly
in subshells and shell loops. One module per tool, and one subcommand per phase.
Shared concerns — project-root resolution, HTTP and retry,
provenance, thresholds, and output limits — live in `core/` so that no command can
implement them differently. See [`tool-design-guidance.md`](tool-design-guidance.md).

The tools environment itself does **not** live in the repo. `install.sh` provisions a
shared virtualenv and binary directory on a scion shared volume
(`/scion-volumes/tools/`), which every specialist mounts read-only. A local
venv under `tools/` is the development mode only; a running program uses the shared
volume. This keeps heavy and compiled dependencies — RDKit, AutoDock Vina, fpocket,
and deployment-built Hypex executables —
out of both the repo and the container images, and makes adding a tool additive rather
than release-managed. See [`tool-design-guidance.md`](tool-design-guidance.md) §2 for
the rules that make a shared mutable environment safe.

### 7.1 Template Anatomy

Each agent template follows scion conventions. Example for the structural biologist:

**`templates/structural-biologist/scion-agent.yaml`:**
```yaml
schema_version: "1"
description: "Structural biologist for protein structure analysis, druggability assessment, and binding mode interpretation"
agent_instructions: agents.md
system_prompt: system-prompt.md

# DDE_PROJECT is resolved by .dde/ walk-up discovery by default.
# Set it explicitly only when the agent's workspace is not the program
# directory — e.g. when /workspace is the dde repo:
#   env:
#     DDE_PROJECT: /workspace/program-hr-mbc

skills:
  - uri: "https://github.com/GoogleCloudPlatform/LifeSciences/tree/main/applications/drug-design-engine/skills/protein-structure-confidence"
  - uri: "https://github.com/GoogleCloudPlatform/LifeSciences/tree/main/applications/drug-design-engine/skills/artifact-conventions"
```

Note that `skills:` entries are non-optional by default — an unresolvable URI fails
provisioning outright. Skill names must match the upstream directory names exactly.

**`templates/structural-biologist/system-prompt.md`:**
```markdown
# Structural Biologist

You are a structural biologist specializing in protein-ligand interactions,
druggability assessment, and structure-guided drug design. You work across
all stages of pre-clinical R&D — from initial target structure prediction
through co-crystal SAR interpretation to selectivity engineering.
```

**`templates/structural-biologist/agents.md`:**
```markdown
## Your Role

You receive structural biology tasks from the science program orchestrator.
Each task includes the relevant project context — target information, known
liabilities, prior structural findings, and the specific question to answer.

## Available Tools

All computation runs through the `dde` CLI. Your skills tell you which
subcommands to use for a given question and where their output lands; run
`dde doctor` at startup to confirm the environment is intact.

Do not compute structural metrics yourself. If a tool you need is unavailable,
report the task as blocked — do not estimate.

## Output Contract

Write your findings as a markdown report following the artifact-conventions
skill. Every report must include:

- Summary (2-3 sentence bottom line)
- Key Findings with inline links to raw structural data in `raw/structures/`
- Implications for the program direction
- Open Questions for follow-up
- Caveats & Confidence (model resolution, pLDDT ranges, limitations)

Save reports to `findings/structural-biology/` in the project folder.
```

The role definition names no specific tools. The set of available invocations comes
from the skills the template declares, which is what keeps role definitions lean while
still preventing a specialist from claiming a capability it has no way to exercise.

### 7.2 Skill Referencing

Agent templates reference skills through three mechanisms:

1. **DDE skills** (shared across roles): `https://github.com/GoogleCloudPlatform/LifeSciences/tree/main/applications/drug-design-engine/skills/artifact-conventions` — the report format and linking conventions that all specialists follow. Skill names must match the directory names in `skills/` exactly.

2. **Local template skills** (role-specific): Skills in the template's own `skills/` directory when a workflow is not reusable or published. Shared orchestration capabilities belong in `skills/` rather than copied between the Science Program Lead and the Research Operations Controller.

3. **External skills** (from the broader scion ecosystem): Any published skill that a role might benefit from.

Use one URI scheme. `gh://` resolution is the current scheme for all dde skills.

### 7.3 Project Bootstrapping

Starting a new research project:

1. The user creates a Science Program Lead agent: `scion start program-lead --type science-program-lead`
2. The user sends the orchestrator the scientific objective: "Investigate PCSK9 as a target for familial hypercholesterolemia. Small molecule modality. Evaluate druggability and advance through starting matter identification."
3. The Science Program Lead creates a Research Operations Controller
4. The controller initializes the artifact directories and machine-readable control plane, then validates the environment
5. The science lead creates the program charter, reasons about the first decision questions, and commits versioned work orders with immutable context snapshots
6. The controller validates the work orders, creates the appropriate specialist agents, supervises their runs, and performs mechanical artifact intake
7. The science lead reviews validated findings, obtains independent review where required, accepts evidence into Layer 2, and dynamically issues subsequent work
8. The controller keeps the website and dashboards synchronized with accepted artifact revisions

---

## 8. Relationship to the Legacy `pharma_skills` Repository

The existing `pharma_skills` repository contains ~490 files with ~168 agent skills and 57 tool skills. It represents comprehensive domain coverage across all four pre-clinical stages. DDE draws on this material but restructures it according to the principles above:

| Legacy Approach | DDE Approach |
|---|---|
| ~200 lines per role skill re-teaching domain procedures | ~20 lines: persona trigger + tools + output contract |
| Procedural workflows baked into role definitions | Stage-specific questions come from Science Program Lead work orders; professional method comes from LLM knowledge |
| Stage gates serve as both reporting and routing mechanism | Gates are reporting artifacts; the Science Program Lead reasons against accepted living project state |
| Roles instantiated separately per stage | Roles are stage-agnostic; stage context comes from the task |
| Monolithic SKILL.md per role with embedded workflow selection | Lean role definitions; scientific work selection belongs to the Science Program Lead |
| A CLI that emits raw payload and interpreted finding in one call | A CLI with separated phases: `fetch`/`run` writes Layer 0, `analyze` reads it back |
| Thresholds hardcoded in tool code, or asserted in prose | Named constants in CLI config, overridable per program, stamped into every output |

### 8.1 Two things to carry forward with care

**The unified CLI is the right execution surface, but not in its legacy shape.** An earlier version of this plan proposed MCP servers (`pharma-data-mcp`, `pharma-compute-mcp`) as the tooling direction. That has been superseded: a single `dde` CLI gives one place for credential resolution, rate limiting, retry, provenance stamping, and artifact naming, without the per-agent server lifecycle MCP requires. MCP remains the right answer for genuinely *stateful, shared* services — most concretely a lease broker for single-flight endpoints like AlphaFold 3, where file locks don't help because specialists run in separate containers. For general HTTP rate limits (NCBI, PubChem, etc.), cross-container pacing is handled via flock on a shared filesystem volume (#59, #68), making a full lease broker unnecessary for this case.

**The legacy CLI's interface shape is a cautionary example, not a template.** `pharma_cli.py` collapses query and interpretation into a single invocation that emits both the raw payload and the finding. That shape is a large part of why fabrication in it is undetectable: there is no point at which raw data exists independently of the claim made about it. Several of its `compute` subcommands never open their inputs at all and emit a hardcoded pass verdict. Anything adapted from that repository must be read as a specification of intent, not as working code, and must be verified to actually perform the computation it reports. The same applies to any routine producing synthetic values in place of a real model call.

**The same defect class appeared in dde's own first CLI.** The single-file `dde_cli.py` bound to minified JSON keys that no real Co-Scientist export uses. Its `overview` subcommand printed a plausible, well-formatted tournament report with the entire top-ideas table missing, and exited 0. Nothing downstream could detect the loss. The legacy code was removed after all subcommands were ported to the two-phase model with shape-based field resolution; the lesson is preserved here.

This first-party case carries more weight than the inherited one. The fault is not confined to code dde did not write, and no amount of reading found it — it appeared when the tool ran against a real export. Two rules follow. Treat "the tool ran and printed something plausible" as unverified until an artifact exists that a reviewer can re-read. Resolve data by shape rather than by a name that carries no meaning, and raise on absence rather than rendering an empty result.

---

## 9. Open Design Questions

1. **Modality-specific specialist roles:** The four abstract stages accommodate all modalities, but certain programs require specialists not yet templated (antibody engineer, sequence designer, gene therapy vector engineer). Should dde ship with modality-specific specialist templates, or should these be added as extension packs?

2. **Multi-program portfolio:** When an organization runs multiple drug programs simultaneously, is there a portfolio-level orchestrator above the individual program orchestrators? This would handle resource allocation, competitive intelligence, and strategic portfolio decisions.

3. **Wet-lab integration boundaries:** DDE's specialist agents reason about experiments and interpret results, but the physical experimental work happens outside the system. How does data from real lab instruments (plate readers, SPR, crystallography) flow into the Layer 0 raw artifacts? This interfaces with LIMS/lab automation tooling.

4. **Enterprise policy extensibility:** The current material includes Sobi-specific policies (FcRn thresholds, haematology focus). How should dde support pluggable enterprise policy modules for different organizations? Program-level thresholds already have a mechanism (`.dde/thresholds.yaml`); an organization-level layer beneath it is the likely shape.

5. **Lease broker implementation:** The Research Operations Controller owns resource scheduling policy, and `dde` invocations must acquire leases for single-flight resources such as AlphaFold 3. This is now load-bearing rather than theoretical: `dde alphafold predict` serializes callers with an `fcntl` lock, which holds only within one container. Specialists run in separate containers, so cross-container collisions are unsolved and the endpoint returns 429 under concurrency. Whether the shared broker is a shared-volume service or an MCP service remains unsettled. Direct specialist coordination and ad hoc file locks are not an acceptable end state. *Note:* General HTTP rate-limit coordination (NCBI, PubChem, etc.) is now handled by flock on a shared filesystem volume (#59, #68); the lease broker question is scoped to truly single-flight endpoints (AF3) where only one concurrent request is permitted.

6. **Upstream skill provenance:** Converting science-skills into the dde pattern changes their execution model, which makes it a fork rather than a wrapper. Upstream is effectively dormant, so the maintenance cost is low, but each converted skill should record its upstream commit and license (Apache 2.0 for code, CC-BY 4.0 for materials). The mechanism for that is not yet defined.

---

## 10. Near-Term Sequence

DDE is built in the order **tools → skills → templates**, against a three-tool
pilot. The order follows the tier ownership in
[`tool-design-guidance.md`](tool-design-guidance.md) §1. A skill cannot name an
invocation that does not exist, and a template cannot grant a skill nobody has written.

| Step | Work | Complete when |
|---|---|---|
| **1. Tooling** | The `dde` package and its `core/` modules, `doctor`, `init`, the control-plane commands, and the three pilot tools | Each pilot tool writes Layer 0 artifacts with sidecars, and `analyze` re-runs from disk |
| **2. Skills** | Convert the pilot tools into capability skills; write the orchestration skills | A specialist can route to an invocation without guessing a path |
| **3. Templates** | Add the controller and reviewer templates; replace the upstream skill grants with dde grants | No template grants an upstream URI, and no template names a tool it cannot invoke |

The templates in the repo today grant `google-deepmind/science-skills` URIs directly.
That is the state before conversion. Step 3 replaces those grants.

**The pilot subset is co-scientist, AlphaFold, and AlphaGenome.** The three stress
different parts of the contract. AlphaFold is the canonical cheap-fetch and
deterministic-analyze case. AlphaGenome hands an interpretation across disciplines.
Co-scientist is the expensive long-running case (~2 h) that proves phase separation
and persistent operational supervision earn their keep.

The pilot produces real Layer 0 artifacts and real Layer 1 findings against a real
target. It must also exercise immutable work orders, separate run identities, a forced
retry, mechanical rejection of a malformed artifact, structured alert routing,
independent scientific review, acceptance into Layer 2, threshold re-analysis, and an
idempotent presentation rebuild. See
[`orchestration-design-guidance.md`](orchestration-design-guidance.md) §10 for the
acceptance tests.

All three guidance documents are v0.1. Revise them from what the pilot exposes before
the remaining conversions begin.
