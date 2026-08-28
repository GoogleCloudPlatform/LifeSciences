---
name: target-screening
description: "Methodology and ranked-shortlist output format for recommending acquisition targets: candidate generation, screening funnel, pillar scoring. Load for 'recommend targets' requests."
---

# Acquisition Target Screening Playbook

Methodology for the "recommend companies that might be good targets" mode.

## Inputs to elicit or infer

- **Acquirer profile / thesis**: therapeutic areas of interest, modality
  preferences (small molecule, biologics, ADC, cell/gene therapy, RNA),
  stage appetite (platform vs. de-risked late-stage vs. commercial),
  approximate deal-size budget, and strategic gaps to fill.
- If the user gives only a vague ask, state the assumptions you screen under.

## Screening funnel

1. **Generate candidate universe** — use Google Search for recent pipeline
   news and analyst M&A speculation, and EDGAR full-text search to find
   companies whose filings discuss the target technology/indication.
2. **First-pass filter** — modality/indication fit, stage fit, and rough size
   fit. Discard obvious mismatches; keep 8–12 names.
3. **Verify corporate independence & M&A status (CRITICAL - BATCH PARALLEL)** —
   for all 8–12 candidate survivors from Step 2, execute verification searches
   **in parallel within a single turn** (do not query one-by-one serially):
   - Query format: `"<Company>" (acquired OR acquisition OR buyout OR merger OR "deal closed" OR delisted)` or `"<Company>" acquired acquisition merger buyout`
   - For public companies, verify active trading and actively filing (check for Form 15/25 delisting notices or 8-K/6-K merger completions).
   - **Immediately discard** all acquired/merged/delisted entities.
   - Advance only verified independent standalone survivors to scoring.
4. **Score survivors** — for each, a lightweight version of the five diligence
   pillars. Emphasize: strategic fit, catalyst timing, and (for public names)
   cash runway as a negotiating-leverage / urgency signal.
5. **Rank** — produce a ranked shortlist with a fit score and a one-line
   thesis per name.

## Output format (screening mode)

A ranked table:

| Rank | Company | Ticker | Lead asset / platform | Stage | Fit rationale | Key risk | Est. cash runway | Source |
|---|---|---|---|---|---|---|---|---|

Follow with 2–4 sentences per top candidate expanding the thesis, and a note
on what deeper diligence (a full whitepaper) would resolve next.

## Guardrails

- **Target Acquirability Rule**: Every recommended target MUST be an independent,
  standalone entity. Never recommend companies that have already been acquired,
  merged into another entity, or are operating subsidiaries of larger biopharma.
- **No Assumed/Fabricated Filings**: Never invent or extrapolate quarterly filing
  periods (e.g. unretrieved current-year 10-Q/10-K filings) for an acquired or delisted company. If a company
  has ceased filing or has no recent filings in the current calendar year, investigate
  whether it was acquired or taken private.
- Only recommend names you can support with at least one concrete source.
- Distinguish public (screenable via EDGAR) from private (news)
  candidates; flag data limitations for private ones.
- Never present speculation as a confirmed deal rumor; attribute rumors.
- Offer to produce a full whitepaper on any shortlisted name.
