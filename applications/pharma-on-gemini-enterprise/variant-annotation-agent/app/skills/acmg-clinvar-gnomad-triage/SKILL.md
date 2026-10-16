---
name: acmg-clinvar-gnomad-triage
description: ACMG/AMP clinical variant interpretation and triage workflow integrating ClinVar assertions and gnomAD v4 population frequency thresholds.
---

# ACMG/AMP ClinVar and gnomAD Clinical Triage

This skill provides a standardized, evidence-based workflow for classifying germline genetic variants under the ACMG/AMP (Richards et al. 2015, PMID: 25741868) guidelines, cross-referencing ClinVar consensus assertions and gnomAD v4 population genetics.

## Core ACMG/AMP Criteria Evaluated

### 1. Population Allele Frequency (gnomAD v4)
- **BA1 (Stand-alone Benign)**:
  - Filtering Allele Frequency (FAF) or global Allele Frequency (AF) > 0.05 (5.0%) across gnomAD exomes or genomes.
- **BS1 (Strong Benign)**:
  - Allele frequency is greater than expected for the disease disorder (typically AF > 0.01 for rare Mendelian conditions).
- **PM2_Supporting (Moderate/Supporting Pathogenic)**:
  - Absent or extremely rare in gnomAD v4 (AF < 0.0001 or 0.01% in all continental populations) with adequate sequencing coverage (depth ≥ 20x).

### 2. Clinical Assertions (NCBI ClinVar)
- **VCV Accessions**: Track variation-level consensus records.
- **Review Status Stars**:
  - `4 stars`: Practice guideline.
  - `3 stars`: Reviewed by expert panel (ClinGen VCEP).
  - `2 stars`: Multiple submitters, criteria provided, no conflicts.
  - `1 star`: Single submitter or conflicting interpretations.
  - `0 stars`: No assertion criteria provided.
- **Conflict Resolution**:
  - Differentiate between older legacy submissions and current ClinGen VCEP curated classifications.

### 3. In Silico & Functional Evidence
- **PVS1 (Very Strong Pathogenic)**: Null variant (nonsense, frameshift, canonical ±1,2 splice site, initiation codon) in a gene where loss-of-function is an established disease mechanism (gnomAD pLI ≥ 0.9 or LOEUF < 0.35).
- **PS1 / PM5**: Same amino acid change as previously established pathogenic variant (PS1) or novel missense change at an established pathogenic residue (PM5).
- **PP3 / BP4**: Multiple lines of computational evidence supporting deleterious impact (PP3) or benign neutrality (BP4), including AlphaGenome AVI, AlphaMissense, and vertebrate conservation (phyloP / phastCons).

## Triage Decision Tree

1. **Check Stand-alone Benign (BA1)**:
   - If gnomAD AF > 0.05 and no conflicting dominant severe early-onset phenotype $\rightarrow$ Classify as **Benign**.
2. **Evaluate ClinVar Expert Panel Assertions**:
   - If 3-star or 4-star ClinVar assertion exists $\rightarrow$ Adopt consensus assertion and document ClinGen criteria.
3. **Combine Pathogenic / Benign Criteria Rules**:
   - **Pathogenic**:
     - 1 Very Strong (PVS1) + ≥1 Strong (PS1-PS4) OR
     - 1 Very Strong (PVS1) + ≥2 Moderate (PM1-PM6) OR
     - 1 Very Strong (PVS1) + 1 Moderate + 1 Supporting OR
     - 1 Very Strong (PVS1) + ≥2 Supporting (PP1-PP5) OR
     - ≥2 Strong OR
     - 1 Strong + ≥3 Moderate
   - **Likely Pathogenic**:
     - 1 Very Strong + 1 Moderate OR
     - 1 Strong + 1-2 Moderate OR
     - 1 Strong + ≥2 Supporting OR
     - ≥3 Moderate OR
     - 2 Moderate + ≥2 Supporting OR
     - 1 Moderate + ≥4 Supporting
   - **Uncertain Significance (VUS)**:
     - Conflicting criteria or insufficient evidence.
