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
   fit. Discard obvious mismatches; keep 8–15 names.
3. **Score survivors** — for each, a lightweight version of the five diligence
   pillars. Emphasize: strategic fit, catalyst timing, and (for public names)
   cash runway as a negotiating-leverage / urgency signal.
4. **Rank** — produce a ranked shortlist with a fit score and a one-line
   thesis per name.

## Output format (screening mode)

A ranked table:

| Rank | Company | Ticker | Lead asset / platform | Stage | Fit rationale | Key risk | Est. cash runway |
|---|---|---|---|---|---|---|---|

Follow with 2–4 sentences per top candidate expanding the thesis, and a note
on what deeper diligence (a full whitepaper) would resolve next.

## Guardrails

- Only recommend names you can support with at least one concrete source.
- Distinguish public (screenable via EDGAR) from private (news)
  candidates; flag data limitations for private ones.
- Never present speculation as a confirmed deal rumor; attribute rumors.
- Offer to produce a full whitepaper on any shortlisted name.
