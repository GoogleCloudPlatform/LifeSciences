---
name: competitive-landscape-scan
description: Build a competitor pipeline view for a target / mechanism / indication — who is in the clinic, what phase, what differentiation. Use for BD / portfolio / commercial-strategy questions like "who else is developing X" or "what's the pipeline for indication Y".
---

# Competitive Landscape Scan

You are producing a competitive intelligence brief for a pharma BD,
portfolio strategy, or commercial team. They need a structured view of
who is doing what, where in the development cycle, and what makes each
program distinct. Speed-to-answer matters — keep the synthesis dense.

## Workflow

### 1. Define the scan boundary

Get the user to specify (or infer + confirm):

- **Axis**: target (e.g. "anti-PD-1"), mechanism class (e.g. "GLP-1/GIP
  dual agonists"), or indication (e.g. "non-small-cell lung cancer
  second-line").
- **Phase scope**: usually Phase 1 onwards; ask whether to include
  preclinical (slower) or to restrict to Phase 2+.
- **Sponsor scope**: industry only, or include academic / cooperative
  groups.
- **Geography**: default to all; restrict if asked (e.g. China-only
  registry).

### 2. Pull the trial pipeline

Call `search_clinical_trials` with the right combination of
`condition` / `intervention` / `phase`. Run it twice if needed — once by
indication, once by intervention — and de-duplicate by NCT ID.

Group results by:

- **Sponsor** — lead sponsor + collaborators.
- **Asset** — the intervention name. Many trials test the same asset
  across indications; collapse them.
- **Phase** — the highest phase any trial of the asset has reached.
- **Status** — recruiting, active, completed, terminated. Terminated
  programs are often the most informative — investigate the reason in
  the trial's full record via `get_clinical_trial`.

### 3. Pull the publication footprint

For the top 5-10 assets, run `search_europe_pmc` with
`<asset_name> AND <indication>` to find:

- Clinical readouts (cite the specific trial PMID / NCT).
- Mechanism papers that justify the differentiation claim.
- Recent congress abstracts (Europe PMC indexes ASCO / ASH / AHA
  abstracts).

### 4. Surface the differentiation story

For each top asset, write 1-2 lines on what makes it distinct:

- Modality (small molecule vs. mAb vs. ADC vs. bispecific vs. cell
  therapy vs. RNAi).
- Selectivity / specificity (e.g. KRAS G12C selective vs. pan-KRAS).
- Dosing / convenience (oral vs. IV, monthly vs. weekly).
- Combination strategy (mono vs. doublet vs. triplet).
- Patient-selection biomarker (e.g. PD-L1 ≥1%, HER2-low).

These are the dimensions BD/portfolio teams ask about; without them the
scan is just a trial list.

### 5. Output

```
# Competitive landscape — <axis>
Scan date: YYYY-MM-DD | Sources: ClinicalTrials.gov + Europe PMC + PubMed

## Pipeline at a glance
Total assets: N | Phase 3+: N | Phase 2: N | Phase 1: N | Recently terminated: N

## Leading programs
| Asset | Sponsor | Modality | Highest phase | Indications | Differentiation | Key refs |
|-------|---------|----------|---------------|-------------|-----------------|----------|

## Notable readouts in last 12 months
- ... (PMID / NCT / sponsor / one-line outcome)

## Recent terminations / setbacks
- ... (asset / sponsor / reason if disclosed)

## Whitespace / under-served angles
- ... (3-5 bullets on indications, lines of therapy, or biomarker
  segments where the pipeline is thin)
```

### 6. Optional figure

Call `visualize_concept` with `figure_type="infographic"` to render a
small-multiples panel of the top assets — useful for one-pager
distribution. Each card shows asset / sponsor / phase / modality /
indications.

## Guardrails

- Differentiate *approved* vs. *investigational* claims — never imply a
  Phase 2 asset has an approved indication.
- ClinicalTrials.gov has US bias; flag if a likely-relevant Asia-Pacific
  program is missing (search by sponsor name when the user mentions one).
- Use the trial's `phase` field, not the sponsor's marketing language —
  press releases inflate phase.
