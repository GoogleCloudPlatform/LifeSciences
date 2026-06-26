---
name: mechanism-of-action-explainer
description: Produce a literature-grounded mechanism-of-action explanation for a drug or drug class — receptor / pathway / downstream effects / clinical relevance — with a Nano Banana Pro diagram and citations. Use for medical-affairs MSL prep, internal training, or when the user asks "how does drug X work" or "what's the MoA of class Y".
---

# Mechanism-of-Action Explainer

You are preparing a Medical Science Liaison (MSL) -grade mechanism-of-action
brief on a drug, target, or modality. The audience is a clinician or
internal scientist who needs scientifically accurate explanation plus a
diagram they can drop into a slide.

## Workflow

### 1. Identify the MoA scaffold

Call `lookup_entity_id` with `concept="chemical"` for the drug. If the
user named a class (e.g. "GLP-1 receptor agonists"), call it for the
canonical class member or treat the class as the subject directly.

Identify, from the literature:

- **Primary target(s)** — the receptor, enzyme, channel, or protein the
  drug binds. Use `find_related_entities` with `relation_type="inhibit"` /
  `"stimulate"` / `"interact"` and `target_type="gene"`.
- **Binding mode** — agonist / antagonist / inverse agonist / allosteric /
  covalent / PROTAC / antibody-drug-conjugate / oligonucleotide.
- **Downstream signaling** — the immediate post-receptor cascade.
- **Tissue / cellular localization** — where the target is expressed
  matters as much as the molecular effect.

### 2. Map signaling to clinical effect

For each clinical effect the drug is approved or studied for, trace the
chain from molecular interaction → cellular consequence → tissue-level
change → clinical phenotype. Use `search_pubmed` with query like
`"<drug> AND mechanism AND <indication>"` filtered to reviews
(`publication_types=["review"]`) for the canonical chain.

Note dose-response and time-course where relevant — pharma audiences care
whether the effect is sustained, transient, or accumulative.

### 3. Surface the differentiating biology

For an MSL brief, the most valuable content is what makes this drug or
class *different* from competitors:

- Selectivity profile vs. related targets (e.g. SGLT2 vs. SGLT1).
- Tissue restriction (e.g. peripheral-only vs. CNS-penetrant).
- Off-target activities that drive class-effect AEs.
- Pharmacokinetic differentiators that interact with the MoA (half-life,
  tissue distribution, active metabolites).

Cite the canonical reviews and key primary papers with PMIDs.

### 4. Render the MoA diagram

Call `visualize_concept` with `figure_type="diagram"`. Build the
description with:

- The drug/molecule shape on one side, the target on the other.
- Arrows showing the primary interaction with a binding-mode label.
- The downstream signaling cascade as a vertical or horizontal flow.
- The cellular/tissue context (membrane, organelle, organ) as the
  background frame.
- Every label spelled exactly as you want it rendered (Nano Banana Pro
  has industry-leading text rendering but you must dictate spelling
  verbatim — gene symbols are case-sensitive).

Pass `aspect_ratio="16:9"` for slide use, `"4:3"` for poster use.

### 5. Output

Final response structure:

- One-paragraph plain-language MoA summary (the line you'd open an MSL
  conversation with).
- "How it works" — mechanism walk-through with citations.
- "What makes it different" — competitor / class context.
- The rendered figure marker (pass through the `<start_of_user_uploaded_file: ...>`
  string verbatim so the image renders inline).
- "Key references" — 5-8 PMIDs for the most cite-worthy mechanism papers.

## Guardrails

- Distinguish *approved indications* from *investigational uses*. Cite the
  FDA / EMA label section for approved claims and the trial NCT for
  investigational claims.
- Off-label or pre-clinical mechanisms must be clearly tagged as such.
- If the literature contradicts itself (e.g. multiple proposed mechanisms
  for an effect), present both with PMIDs rather than choosing.
