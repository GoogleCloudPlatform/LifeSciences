---
name: target-evidence-dossier
description: Build a target-validation dossier for a gene / protein / pathway — biology, disease association, druggability, existing programs, key publications, trial pipeline, safety signals. Use for early-stage discovery target review, portfolio decisions, or when the user asks "what do we know about target X".
---

# Target Evidence Dossier

You are building the evidence package a pharma R&D team uses to decide
whether to advance, deprioritize, or further-validate a target. Audience
is a biology or computational-bio team lead — they want compactness and
citations, not narrative fluff.

## Workflow

### 1. Identify the target canonically

Call `lookup_entity_id` with `concept="gene"` to get the canonical
PubTator3 ID (e.g. `@GENE_BRCA1`). Note any synonyms / aliases / paralogs
the user should be aware of (PubTator3 returns these; surface them
prominently because alias drift causes evidence to be missed).

### 2. Biology section

Use `search_pubmed` with `publication_types=["review"]` on the gene name
to surface the canonical reviews. Distill:

- Protein family + domain architecture.
- Cellular localization and expression pattern (which tissues highly
  express it; which cell types).
- Known biological function and pathway membership.
- Knockout / loss-of-function phenotype (mouse and, where available,
  human LoF).

### 3. Disease association

Three angles, in order:

1. **Genetic association** — call `find_related_entities` with the gene
   ID, `relation_type="associate"`, `target_type="disease"`. Cross-check
   against `search_pubmed` for `"<gene> AND GWAS"` and
   `"<gene> AND mutation AND <disease>"`.
2. **Functional / mechanistic association** — `relation_type="cause"` and
   `relation_type="positive_correlate"` / `"negative_correlate"`.
3. **Expression-based association** — note if the literature flags
   over- / under-expression in disease tissue.

Tag each association with strength of evidence (genetic > mechanistic >
correlation).

### 4. Druggability + existing programs

- Existing drugs / probes: `find_related_entities` with the gene ID,
  `relation_type="inhibit"` and `relation_type="stimulate"`,
  `target_type="chemical"`.
- Trials targeting it: `search_clinical_trials` with `intervention=`
  the gene name and / or `condition=` the leading associated indication.
  Group results by sponsor and phase.
- Modality landscape: small molecule vs. biologic vs. PROTAC vs. genetic
  medicine. The trial table usually answers this implicitly.

### 5. Translatability + safety signals

- Animal-model evidence: include reviews that cite KO/CKO mouse phenotypes.
- Human genetic evidence: surface known LoF tolerance — if humans with
  natural LoF are healthy, that's a positive translatability signal; if
  LoF is associated with severe disease, flag the on-target safety risk.
- Literature on pathway-level toxicity (e.g. inhibiting target X disrupts
  pathway Y which controls Z).

### 6. Output

Final structure:

```
# Target dossier — <GENE_SYMBOL>

## Snapshot
- Family / domain / localization
- Strongest disease association (1 sentence + PMID)
- Druggability verdict (Tractable / Challenging / Undruggable + 1 sentence)
- Pipeline status (count of trials by phase, lead sponsors)

## Biology
... cited bullets ...

## Disease association
| Disease | Evidence type | Strength | Key refs |

## Existing programs
| Asset / probe | Modality | Sponsor | Phase | NCT |

## Translatability + safety
... cited bullets ...

## Open questions / next experiments
... 3-5 bullets framed as testable hypotheses ...

## References
PMIDs grouped by section.
```

Optionally render a one-panel target-context diagram via
`visualize_concept` (`figure_type="diagram"`) — protein in its pathway,
disease tissue overlay, existing drugs as inhibitor arrows. Useful for
slide use.

## Guardrails

- Distinguish "X is associated with disease Y" from "X causes disease Y" —
  use the strength-of-evidence tag.
- Do not invent KO phenotypes or LoF data — if the literature does not
  cover it, write "no published mouse KO data found" rather than
  speculating.
- Aliases matter: if PubTator3 returns multiple canonical IDs for the
  query, run the dossier on each and note the alias mapping.
