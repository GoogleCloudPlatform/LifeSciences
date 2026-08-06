---
name: whitepaper-template
description: "Section-by-section structure for an investment-grade acquisition whitepaper (executive summary through recommendation and sources). Load before writing a detailed report or generating the PDF."
---

# Investment Whitepaper Template

Structure for a detailed acquisition-assessment whitepaper. Produce clean
markdown (headings, tables, blockquotes) — it is rendered to PDF verbatim by
the `generate_whitepaper_pdf` tool, so formatting matters.

Use markdown tables for all quantitative comparisons. Use `>` blockquotes for
the most important risk callouts. Do not invent figures.

CITATIONS: every material claim, figure, and table value carries an inline
numbered footnote marker in Markdown footnote syntax, e.g. "14.7% weight
loss[^venture]". Reuse the same short key for the same source; define every key
under a final `## References` heading (e.g. `[^venture]: Phase 2 VENTURE 13-week
results, Obesity, Jan 2026.`). The renderer turns these into a numbered, linked
reference list. Never fabricate a source, URL, accession number, or doc ID.

FORMATTING: write plain markdown only. Do NOT use LaTeX or MathJax math
notation (`$...$`, `$$...$$`, `\frac`, `\text`, `\ge`, `\gamma`, etc.) — the PDF
renderer shows it raw. Use Unicode symbols and inline arithmetic instead, e.g.
"≥70%", "Fcγ receptor", and "Runway = $56.4M / ($22.6M / 3) = 7.5 months".

---

# Acquisition Assessment: <Target Company>

## 1. Executive Summary
- 4–7 bullets: what the target is, the deal thesis, headline recommendation
  (Pursue / Pursue with conditions / Pass / Monitor), and the single biggest
  value driver and the single biggest risk.
- One-line **Recommendation** with a conviction level (High/Medium/Low).

## 2. Company & Deal Thesis
- What they do, lead assets, corporate stage, why they'd be acquired now.
- Deal-thesis archetype (platform / single-asset / tuck-in / distressed).

## 3. Scientific & Modality Assessment
- Mechanism, novelty, platform breadth, IP position, differentiation.
- Competitive class table (asset | company | mechanism | stage).

## 4. Clinical & Regulatory Assessment
- Pipeline table (asset | indication | phase | next catalyst | key risk).
- Trial design quality, regulatory designations, agency interactions, CMC.

## 5. Commercial & Market Assessment
- Addressable population, epidemiology, competitive landscape, pricing/
  reimbursement outlook, peak-sales framing (state assumptions).

## 6. Financial Assessment
- Financial-snapshot table (metric | latest | prior | source).
- Cash runway calculation shown explicitly with the formula and inputs.
- Burn trajectory, dilution history, debt, valuation context.

## 7. Deal Structure & Risk Analysis
- Risk register table (risk | likelihood | impact | mitigation).
- Exclusivity/patent timeline, encumbrances, integration considerations.

## 8. Valuation & Comparable Transactions
- Recent comparable M&A deals (target | acquirer | value | premium | date).
- Rough valuation framing; label all assumptions clearly.

## 9. Recommendation & Conditions
- Restate recommendation, conditions precedent, diligence follow-ups, and the
  key questions to resolve before term sheet.

## 10. Confidence & Information Gaps
- Explicit confidence statement (High/Medium/Low by pillar) and the biggest
  information gaps.

## References
- Define every footnote key used above, one per line
  (`[^key]: full citation with the most specific locator you have`). The renderer
  renders these as a numbered, linked reference list — do not also hand-write a
  separate bulleted source list.

---

*Footer note is added automatically by the renderer. Keep total length
proportionate to available evidence — do not pad sections with speculation.*
