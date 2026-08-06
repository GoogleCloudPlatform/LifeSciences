---
name: diligence-playbook
description: "The five life-sciences M&A diligence pillars (scientific, clinical/regulatory, commercial, financial, deal/risk), the cash-runway calculation, deal-thesis archetypes, evidence standards, and the red-flag checklist. Load for any acquisition assessment."
---

# Life Sciences M&A Diligence Playbook

Domain methodology for Argus. Load the relevant section based on the user's
request and the mode (quick answer / whitepaper / target screening).

## The five diligence pillars

Every acquisition assessment covers these. Weight them by deal thesis
(platform buy vs. single-asset buy vs. commercial-stage tuck-in).

1. **Scientific & Modality** — mechanism of action, target validation, novelty
   vs. crowded class, platform breadth, IP/freedom-to-operate, differentiation
   vs. standard of care and known competitors.
2. **Clinical & Regulatory** — pipeline stage, trial design quality, endpoints,
   readouts/catalysts, prior FDA/EMA interactions, breakthrough/fast-track/
   orphan designations, CMC/manufacturing readiness, safety signals.
3. **Commercial & Market** — addressable patient population, epidemiology,
   pricing/reimbursement, competitive landscape, peak-sales potential, launch
   readiness, existing revenue.
4. **Financial** — cash & equivalents, quarterly burn, cash runway (months),
   R&D vs G&A split, debt, dilution history, valuation vs. comparable deals,
   ownership/insider stakes.
5. **Deal & Risk** — patent cliff/exclusivity timeline, litigation, key-person
   dependence, partnership/royalty encumbrances, integration complexity,
   antitrust, single-asset concentration risk.

## Cash runway (core financial calculation)

- Quarterly net cash burn ≈ |NetCashProvidedByUsedInOperatingActivities| for
  the quarter (prefer cash-flow statement over net loss).
- Total liquidity = CashAndCashEquivalents + ShortTermInvestments +
  MarketableSecuritiesCurrent.
- Runway (months) ≈ total_liquidity / (quarterly_burn / 3).
- Flag runway < 18 months as a financing-risk / negotiating-leverage signal.
- Always use the most recent reported quarterly period available from EDGAR tools
  as of today's date; do not default to historical cutoff years.
- Always cite the filing (form + period end) each figure came from.

## Deal-thesis archetypes

- **Platform acquisition**: weight Scientific highest; value the technology's
  reusability across indications, not one asset.
- **Single-asset / late-stage**: weight Clinical/Regulatory + Commercial;
  binary readout risk dominates.
- **Commercial tuck-in**: weight Financial + Commercial; revenue quality,
  margins, and channel fit.
- **Distressed / buy-the-dip**: weight Financial (runway) + Deal/Risk; the
  edge is timing a financing wall.

## Evidence standards

- Prefer primary sources: SEC filings (via EDGAR tools), regulatory/scientific
  databases (via science skills: openFDA, ClinicalTrials.gov, ChEMBL, Open Targets,
  PubMed), and trial registries.
- Real-time ground truth: Treat live filing dates, clinical trial updates, and news
  from recent/current calendar years as authentic records (never future placeholders
  or system anomalies).
- Use Google Search for recent news, deal comps, and catalysts without hardcoding
  historical cutoff years — but treat it as a lead to confirm against a primary
  source, not as the citation itself.
- Every material claim in a whitepaper needs a source. Distinguish fact from
  inference explicitly.
- State confidence and gaps. "Unknown / not disclosed" is a valid, valuable
  finding in diligence.

## Red-flag checklist (surface these prominently)

- Cash runway < 12–18 months without a clear financing path.
- Single asset carrying >70% of the pipeline value.
- Primary endpoint missed, or trial design that can't support approval.
- Patent expiry / loss of exclusivity within the investment horizon.
- Undisclosed safety signals, clinical holds, or CRLs (complete response letters).
- Heavy royalty/milestone obligations to third parties on the lead asset.
- Going-concern language in the latest 10-K/10-Q.
