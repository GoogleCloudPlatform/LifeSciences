---
name: protein-structure-confidence
description: >
  Retrieve protein structures and quantify how far their geometry can be
  trusted — global and per-residue model confidence, disorder extent,
  domain boundaries, and coverage of the canonical sequence. Use when any
  structural claim depends on model reliability: assessing target
  druggability, deciding whether a binding site is well enough resolved to
  dock into, checking whether a variant falls in an ordered region, or
  evaluating whether a predicted complex interface is trustworthy. Do not
  use to find structural homologs (use structural-homology-search), to
  score or interpret a docking pose (use binding-mode-analysis), or to
  compute compound properties (use compound-property-profile).
---

## 1. When to use, and when not

Use this skill whenever you need to establish the reliability of a protein
structure before making a decision that depends on it. Entry points
include:

- Assessing whether a target's fold is confident enough to support
  structural drug design (any stage).
- Determining ordered domain boundaries before docking, pocket detection,
  or structural search.
- Evaluating a predicted protein-protein or protein-ligand complex
  interface (AF3).
- Checking whether a variant of interest falls in an ordered or
  disordered region.
- Re-analyzing a stored structure against updated program thresholds
  without re-querying.

**Do not use when:**

- You already have a structure and need to find similar folds
  -> `structural-homology-search`.
- You need to dock a compound or interpret a binding pose
  -> `binding-mode-analysis`.
- You need to render, measure, or visualize a structure -> `pymol`.
- You only have a gene name — resolve it to a UniProt accession first.

## 2. Preconditions

- **Input for AFDB**: a UniProt accession (e.g. P00520, Q8NF91). Gene
  names and PDB IDs are not accepted; resolve them upstream.
- **Input for AF3**: a JSON input file conforming to the AlphaFold 3
  request schema (dialect `alphafold3`, version 1-3, with `sequences`,
  `modelSeeds`, and `name`). Relative paths resolve against the project
  root.
- **Credentials**: AF3 requires GCP authentication
  (`google-cloud-aiplatform` package and an active service account or
  ADC). AFDB is public and needs no credential.
- **Concurrency**: the AF3 Vertex endpoint is **single-flight** (max one
  replica). The CLI serialises callers within one container.
  Cross-container scheduling is managed by the Research Operations
  Controller, which ensures only one AF3 prediction runs at a time
  across all specialists. If you receive a work order requiring AF3,
  the endpoint is available — proceed without delay.
- **Cost**: AFDB fetch is a free public API call. AF3 prediction consumes
  H100 time on the Vertex dedicated endpoint; a cold start adds up to
  several minutes.
- Run `dde doctor` before first use. It ends with a verdict line:
  `STOP` means fix or report before running anything; `PROCEED` means
  work, and the grouped warnings tell you which commands would refuse,
  which results need careful reading, and which are the tooling lead's
  to clear. Do not judge by the warning count; the verdict line grades
  them for you.

## 3. Tool invocations

### AlphaFold Database (stored predictions)

| Question | Run | Writes to |
|---|---|---|
| What predicted structure exists for this protein? | `dde alphafold fetch <UNIPROT>` | `raw/structures/AF-<id>-F1.cif`<br>`raw/structures/AF-<id>-F1.pae.json`<br>`raw/structures/AF-<id>-F1.afdb.json`<br>`raw/structures/AF-<id>-F1.meta.json` |
| How confident is the fold, and where are the domains? | `dde alphafold analyze <UNIPROT>` | `raw/structures/AF-<id>-F1.alphafold.analysis.json` |

Run `fetch` before `analyze`. `analyze` reads from disk and can be re-run
with different thresholds without re-querying AFDB.

### AlphaFold 3 (on-demand complex prediction)

| Question | Run | Writes to |
|---|---|---|
| What does this complex look like? | `dde alphafold predict --input <JSON>` | `raw/structures/AF3-<name>.cif`<br>`raw/structures/AF3-<name>.summary.json`<br>`raw/structures/AF3-<name>.plddt.json`<br>`raw/structures/AF3-<name>.pae.json`<br>`raw/structures/AF3-<name>.response.json`<br>`raw/structures/AF3-<name>.request.json`<br>`raw/structures/AF3-<name>.meta.json` |
| How confident is the complex interface? | `dde alphafold analyze-prediction <SUMMARY>` | `raw/structures/AF3-<name>.alphafold.analysis.json` |

`predict` blocks until the endpoint returns or the deadline expires
(default 30 minutes). `analyze-prediction` reads the stored
`.summary.json` and applies the `af3` threshold set.

All output options: `--json` for machine-readable output, `--quiet` for
paths only, `--out` to override the default output directory.

## 4. Interpretation contract

### AFDB confidence verdicts

`analyze` applies the `alphafold` threshold set and emits one of:

- **confident** — high-confidence fold; structural work is supported.
- **confident-with-disorder** — mostly confident, but notable disordered
  regions exist. Report the ordered domain boundaries alongside any
  structural conclusion.
- **moderate** — some regions may be flexible or poorly predicted.
  Downstream structural work should be restricted to ordered domains.
- **mixed** — a mixture of structured domains and significant
  intrinsically disordered regions. Restrict structural work to the
  listed ordered domains.
- **highly-disordered** — mostly poorly predicted; likely highly
  intrinsically disordered. Whole-protein structural work (docking,
  structural search) is not supported by this prediction. Restrict
  further analysis to ordered domain boundaries, if any.
- **low-confidence** — low prediction confidence overall.

### PAE domain verdicts

When a PAE matrix is available, `analyze` also emits:

- **single-domain** — a single well-folded, rigid composite domain.
- **multi-domain** — independently positioned global domains separated
  by flexible joints. Report the domain boundaries.
- **no-rigid-domain** — no distinct rigidly-folded domain detected;
  likely disordered.

### AF3 interface verdicts

`analyze-prediction` applies the `af3` threshold set and emits one of:

- **confident-interface** — interface geometry is trustworthy.
- **borderline-interface** — treat the complex geometry as a hypothesis,
  not a result.
- **low-confidence-interface** — this complex should not be used for
  downstream structural work.
- **monomer** — single-chain prediction; ipTM is not defined and must
  not be reported.

### Coverage and mandatory relays

The following relay codes mark obligations on the finding. A relay is
an instruction, not a caveat: satisfying it requires scoping or
withholding the claim, not merely disclosing the condition. A finding
that omits a mandatory relay is wrong, not merely incomplete.

All four relays are **qualifiers** — they fire on defects in the data
(partial coverage, isoform substitution, size limit, unverified
coverage). A full-coverage, high-pLDDT model with no isoform issues
produces **zero relays**. That is not a clean bill of health — it
means no defect was found. Nothing has been said about whether the
confidence scores support the use the reader has in mind (e.g. docking,
conformation selection). pLDDT is a per-residue fold confidence, not
a measure of conformational accuracy or fitness for a particular
downstream application.

| Relay code | Kind | Obligation |
|---|---|---|
| `afdb.partial_coverage` | Qualifier | Scope every confidence claim to the modelled residue range, or withhold it. Do not describe the protein — describe the fragment. |
| `afdb.isoform_substituted` | Qualifier | Name the isoform actually analysed wherever the finding names the protein. Do not attribute these numbers to the canonical entry. |
| `afdb.above_modelling_limit` | Qualifier | State that no full-length prediction exists at any accession. Do not present the available fragments as the protein's structure. |
| `afdb.coverage_unverified` | Qualifier | Report coverage as unknown. Do not infer completeness from the absence of a truncation warning. |

Check `mandatory_relays` in the `.alphafold.analysis.json` and in the `.meta.json`.
Every relay code present must appear in the finding.

### Consequence rules

- **Highly disordered**: do not proceed with whole-protein docking,
  structural search, or pocket detection. Restrict downstream structural
  work to the ordered residue boundaries listed in the analysis.
- **Mixed order/disorder**: restrict downstream structural work to the
  listed ordered domains.
- **Partial coverage**: do not state a whole-protein conclusion from a
  fragment's confidence metrics. The analysis scopes its own verdict
  statement, but the finding must do the same.
- **AF3 borderline or low-confidence interface**: do not use the complex
  geometry for downstream binding-site analysis or selectivity arguments.
- **AF3 steric clash**: the `has_clash` flag is advisory; report it and
  note that the geometry in the clash region is unreliable.

### Synthesis across AFDB and AF3

When both an AFDB monomer prediction and an AF3 complex prediction exist
for the same protein:

- The AFDB monomer's per-residue pLDDT is the baseline fold confidence.
- The AF3 complex's ipTM/pTM scores measure interface confidence on top
  of that baseline.
- If the AFDB monomer shows disorder in a region that AF3 places at an
  interface, the interface prediction in that region is unreliable
  regardless of the ipTM score.

### What this section produces

Following this contract produces a Layer 1 finding in `findings/` that
cites the `.alphafold.analysis.json` and (where applicable) the `.meta.json`
warnings. Everything the tools emitted stays under `raw/structures/`.

Thresholds are cited by name (e.g. "the configured `plddt_confident`
threshold"), never by value. Values live in CLI config and are stamped
into `.alphafold.analysis.json`.

## 5. Failure modes and anti-fabrication guard

**Do not compute these values yourself.** The value is whatever the tool
emitted. If the tool did not run, there is no value. If a required tool
is unavailable, report the task as blocked. Do not estimate, and do not
proceed on an assumed result.

### Named pathologies

- **Isoform substitution without relay.** AFDB may return a short
  isoform for a long protein with no indication that the canonical
  sequence was not modelled. The CLI cross-checks canonical length and
  records a mandatory relay. Do not suppress or paraphrase it.
- **Fragment confidence stated as whole-protein confidence.** If
  coverage is less than 100%, the verdict describes the modelled region
  only. The analysis scopes its statement, but a finding that drops
  the scope qualifier misrepresents the evidence.
- **Disordered protein reported as "no pocket found."** A highly
  disordered protein does not have a pocket problem; it has a fold
  problem. Report the disorder verdict, not the absence of a
  downstream result that should not have been attempted.
- **AF3 ipTM on a monomer.** ipTM is undefined for single-chain
  predictions. The tool returns null and the analysis records it;
  reporting "low interface confidence" for a monomer is a category
  error.
- **AF3 cold-start delay misread as failure.** The endpoint scales from
  zero and may return 429 for several minutes. The CLI retries within
  the deadline. A timeout after exhausting the deadline is a real
  failure; a 429 during warm-up is not. Signal
  `sciontool status blocked "AF3 prediction in progress"` before
  invoking `dde alphafold predict` so the stall detector does not
  false-positive on the wait.
- **Docking scores in a disordered region.** If the pLDDT verdict or
  advisory restricts downstream work to ordered domains, docking into
  disordered residues produces scores that are not meaningful. The
  restriction is structural, not a suggestion.
