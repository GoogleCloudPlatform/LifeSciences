---
name: pico-search-strategy
description: Translate a clinical or research question into a PICO/PECO-structured PubMed + Europe PMC search strategy with MeSH terms, field tags, and search hedges. Use whenever the user asks a comparative-effectiveness, etiology, prognosis, diagnosis, or HEOR question, OR when they explicitly ask for a "search strategy".
---

# PICO Search Strategy

You are constructing a transparent, reproducible literature-search strategy
for a pharmaceutical researcher. The goal is to produce a query that another
analyst could re-run six months from now and get the same hits.

## Workflow

### 1. Decompose the question into PICO (or PECO for etiology)

| Element | Clinical question | Etiology / safety question |
|---------|-------------------|----------------------------|
| **P**   | Population         | Population                 |
| **I**   | Intervention       | **E**xposure               |
| **C**   | Comparator         | Comparator (often unexposed) |
| **O**   | Outcome            | Outcome                    |

Restate the user's question as a single PICO sentence and confirm with them
*before* running searches if anything is ambiguous (especially the
population — adults vs. pediatric, treatment-naive vs. refractory).

### 2. Build the concept blocks

For each PICO element, build a concept block that combines:

- **Controlled vocabulary**: MeSH (PubMed) and EMTREE-equivalent terms.
  Use the `[MeSH]` field tag and explode by default.
- **Free-text synonyms**: include brand + generic drug names, gene symbols
  + protein names, and the major spelling variants (US/UK).
- **Field tags** to control precision: `[Title/Abstract]` for high-precision
  blocks, no tag for high-recall blocks.

Combine within a block with `OR`, between blocks with `AND`.

### 3. Apply methodologic filters as a separate block

Use validated search hedges rather than ad-hoc filters:

- **Systematic reviews**: `(systematic review[PT] OR meta-analysis[PT])`
- **RCTs**: append the [Cochrane Highly Sensitive Search Strategy for
  RCTs](https://training.cochrane.org/handbook/current/chapter-04-supplement-1).
- **Observational studies**: `(cohort studies[MeSH] OR case-control
  studies[MeSH] OR observational study[PT])`
- **Real-world evidence**: combine `("real world"[TIAB] OR "real-world"[TIAB]
  OR registry[TIAB])` with the population block.

### 4. Add date / language / human filters last

- Date: `("YYYY/MM/DD"[PDAT] : "YYYY/MM/DD"[PDAT])`. Default to the last 5
  years for active areas, 10 years for chronic-disease background.
- Humans: `humans[MeSH]` only when the user wants to exclude in-vitro /
  animal work.
- Language: avoid filtering by language unless explicitly requested; doing
  so introduces selection bias.

### 5. Run + report

Always report:

- The full PubMed query string verbatim (the user must be able to paste it
  into PubMed).
- The hit count.
- For Europe PMC, the equivalent query in Europe PMC field-tag syntax
  (TITLE:, ABS:, MESH:, KW:, PUB_YEAR:[YYYY TO YYYY]).
- A one-paragraph rationale for the trade-offs (why MeSH explosion was/was
  not used, why a particular synonym was included).

If hits exceed ~500, propose narrowing concept-by-concept; if hits are
under ~10, propose loosening the highest-precision block first (typically
the outcome).

## Tools to call

- `search_pubmed` for free-text + field-tag queries.
- `advanced_search` when the request specifies dates, MeSH, journal, or
  publication type as discrete filters.
- `search_europe_pmc` for the parallel Europe PMC run (broader coverage).

## What good output looks like

A pharma evidence-generation analyst should be able to take your output,
paste the query into PubMed and Europe PMC, and confirm the hit count
matches yours within a small drift (NCBI updates daily). Include the
PRISMA-style "search executed on YYYY-MM-DD" line.
