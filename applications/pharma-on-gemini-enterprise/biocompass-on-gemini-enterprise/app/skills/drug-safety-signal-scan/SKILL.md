---
name: drug-safety-signal-scan
description: Surface emerging safety signals for a drug or class from PubMed (case reports / letters / RCT AE tables), plus relevant trial AE disclosures. Use for pharmacovigilance triage, medical-affairs safety updates, or competitive risk assessment. NOT a substitute for formal PV systems.
---

# Drug Safety Signal Scan

You are doing rapid pharmacovigilance triage for a pharma medical-affairs
or PV scientist. The audience needs a fast read on whether the literature
is signaling a new or evolving safety concern — they will follow up with
their formal PV systems, this is the upstream early-warning step.

## Workflow

### 1. Disambiguate the drug

Call `lookup_entity_id` with `concept="chemical"` to nail the canonical
ID. Note synonyms — case reports often use brand or chemical name only.

If the request is for a class (e.g. "PI3K inhibitors", "JAK inhibitors"),
treat the class as the subject and run the workflow for each leading
class member, then pool the signals at the end.

### 2. Pull the literature signal sources

Run these in parallel via `DeepResearchPipeline` with a request like:

> "Surface adverse events, safety signals, case reports, and post-marketing
> findings for <drug> from the last 24 months. Include all trial reports,
> case reports, letters, editorials, and review articles. Pay attention
> to organ-class events not in the original label."

Or run individually:

- `advanced_search` with `publication_types=["case reports", "letter"]`
  + the drug name + date filter for last 24 months.
- `search_pubmed` for `"<drug> AND adverse"` and `"<drug> AND
  toxicity"`.
- `search_clinical_trials` with `intervention=<drug>` + `status=COMPLETED`
  to find recently completed trials whose AE tables may reveal new
  signals.

### 3. Use entity analysis to extract organ-class events

For each retrieved article (especially case reports), call
`annotate_articles` to extract the disease entities mentioned. Group the
mentioned diseases by organ class (cardiac, hepatic, renal, neurologic,
hematologic, immune-mediated, dermatologic, GI, endocrine, ocular).

A signal is interesting when:

- A new organ class appears that is not on the current label.
- The frequency of an existing labeled AE appears to be increasing in
  recent reports.
- A pattern of severe outcomes (hospitalization, death) clusters in
  recent reports.
- Multiple independent groups report the same AE within a short window.

### 4. Cross-check against the existing label

Ask the user whether they have current label text handy; if not, search
the literature for the most recent FDA label update or boxed-warning
addition (`search_pubmed` for `"<drug> AND label change"` or
`"<drug> AND boxed warning"`).

Tag each candidate signal as:

- **Labeled** — already in the current Warnings/Precautions/AEs section.
- **Updated** — labeled but the recent literature increases concern
  (severity, frequency, or specific subpopulations).
- **Novel** — not on the label as far as the literature suggests.

### 5. Output

```
# Safety signal scan — <drug>
Scan date: YYYY-MM-DD | Window: last 24 months | Sources: PubMed + ClinicalTrials.gov

## Top-of-mind signals
| Signal | Status (labeled / updated / novel) | # reports | Severity | Key refs |
|--------|------------------------------------|-----------|----------|----------|

## Organ-class summary
- Cardiac: ... (PMIDs)
- Hepatic: ...
- ... (only include classes with hits)

## Trial-disclosed AEs of note
- NCT##### (sponsor, phase, completion date) — AE finding (1 line)

## What this is and isn't
This is a literature-first signal scan — it does not replace formal PV
systems (FAERS, EudraVigilance, sponsor PSURs). Use as the upstream
trigger for a formal PV review.
```

### 6. Optional figure

Call `visualize_concept` with `figure_type="infographic"` for an
organ-class signal heatmap — colored cells per organ class, intensity by
report count. Useful for SAB or governance-meeting slides.

## Guardrails

- A *case report* is hypothesis-generating, not causal evidence. Flag the
  level-of-evidence on every signal.
- Do not aggregate AE rates across heterogeneous studies as if they were
  comparable.
- Class-effect attribution is hard — distinguish "reported with this
  drug" from "established class effect".
- This skill cannot access FAERS / EudraVigilance directly; if the user
  needs those, route them to their PV team or the openFDA API (not yet
  wired into this agent).
