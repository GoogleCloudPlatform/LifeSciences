---
name: prisma-systematic-review
description: Run a PRISMA 2020-aligned systematic-review workflow — identification → screening → eligibility → included — with a transparent record of exclusions at each step and a rendered PRISMA flow diagram. Use when the user asks for a systematic review, evidence map, scoping review, or any task that requires a defensible screening audit trail.
---

# PRISMA 2020 Systematic Review

You are executing a [PRISMA 2020](https://www.bmj.com/content/372/bmj.n71)
aligned systematic review. Pharma reviewers use these for HEOR
submissions, payer dossiers, and regulatory briefing books — every
exclusion must be defensible and counted.

## Workflow

### Step 0 — Lock the protocol

Before any searches, get explicit user agreement on:

- The PICO question (use the `pico-search-strategy` skill if not already
  done).
- Inclusion criteria: study designs, population, intervention/exposure,
  comparator, outcomes, follow-up window, language, date range.
- Exclusion criteria, listed positively (e.g. "exclude single-arm Phase 1
  trials", not "exclude weak studies").

Echo these back to the user as a numbered list and ask "approve to
proceed?" before running searches. This is the version of record for the
review's audit trail.

### Step 1 — Identification

Call `DeepResearchPipeline` with a `request` that includes the locked PICO
+ filters, so PubMed + Europe PMC + ClinicalTrials.gov + preprints all run
in parallel. Record:

- Records identified per database (counts go in the PRISMA flow diagram).
- Records identified from registers (ClinicalTrials.gov hits — PRISMA 2020
  separates databases from registers).

If the user names additional sources (Embase via a partner, internal
study registry, conference abstracts), add a note that these were not
searched in this run.

### Step 2 — Deduplication

De-duplicate across PubMed + Europe PMC by PMID first, then by DOI, then
by normalized title (lowercased, punctuation-stripped, first 80 chars).
Record the number of duplicates removed.

### Step 3 — Title/abstract screening

For each unique record, decide INCLUDE / EXCLUDE / UNCERTAIN against the
inclusion criteria. For every EXCLUDE, record the single most specific
reason from the standard PRISMA exclusion taxonomy:

- Wrong population
- Wrong intervention
- Wrong comparator
- Wrong outcome
- Wrong study design
- Wrong publication type (editorial / commentary / letter)
- Conference abstract only
- Not in target language
- Outside date range
- Duplicate
- Other (must specify)

Mark UNCERTAIN cases for full-text review rather than guessing. Do NOT
silently drop records.

### Step 4 — Full-text retrieval + eligibility

For INCLUDE + UNCERTAIN records: call `get_europe_pmc_fulltext` for
open-access PMCIDs. For paywalled records, mark as "full text not
retrieved" — this is a real PRISMA category and must be counted.

Re-screen against inclusion criteria with the full text in hand. Record
exclusions with the same reason taxonomy.

### Step 5 — Render the PRISMA flow diagram

Call `visualize_concept` with `figure_type="prisma_flow"` and a description
that includes every count from steps 1-4:

```
Identification: PubMed n=X, Europe PMC n=Y, ClinicalTrials.gov n=Z, Preprints n=W
Duplicates removed: D
Records screened (title/abstract): S
Excluded at title/abstract: E (with the top-3 reasons + counts)
Full-text reports sought: F
Reports not retrieved: NR
Reports assessed for eligibility: A
Excluded at full text: X (with reasons + counts)
Studies included in synthesis: I
```

### Step 6 — Synthesis + reporting

Hand the included set to a synthesis pass that produces:

- Characteristics-of-included-studies table (study, design, n, population,
  intervention, comparator, outcome, follow-up).
- Narrative synthesis grouped by outcome.
- Risk-of-bias note (acknowledge that formal RoB scoring needs a human
  reviewer; this skill does not auto-grade studies).

Always cite each included study by PMID / NCT and link.

## Failure modes to avoid

- Silent exclusions: every dropped record must have a counted reason.
- Mixing identification of databases with registers in the PRISMA counts.
- Over-claiming: state "this is a literature scan suitable for an
  internal evidence brief" unless the user has done dual-reviewer
  screening + formal RoB scoring offline.
- Forgetting the search date — record YYYY-MM-DD on the flow diagram.
