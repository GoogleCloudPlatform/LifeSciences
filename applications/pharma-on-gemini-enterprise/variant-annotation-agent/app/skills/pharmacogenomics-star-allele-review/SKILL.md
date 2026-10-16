---
name: pharmacogenomics-star-allele-review
description: Pharmacogenomic star-allele curation, CPIC dosing guidelines, and PharmGKB clinical evidence level evaluation for therapeutic response.
---

# Pharmacogenomics (PGx) Star-Allele Review

This skill defines the clinical interpretation protocol for pharmacogenomic variants affecting drug metabolism, transport, efficacy, and adverse reactions.

## Key Resources & Data Sources

- **PharmGKB (Whirl-Carrillo et al. 2012, 2021)**:
  - Curated database of genetic variation influencing drug response.
- **Clinical Pharmacogenetics Implementation Consortium (CPIC)**:
  - Peer-reviewed, actionable clinical practice guidelines for gene-drug pairs.
- **Dutch Pharmacogenetics Working Group (DPWG)** & **FDA Table of Pharmacogenomic Biomarkers**.

## Evidence Levels in PharmGKB

| Level | Clinical Significance | Guideline / Actionability |
| :--- | :--- | :--- |
| **Level 1A** | Variant-drug combination with an annotated CPIC or DPWG guideline, or FDA-required prescribing recommendation. | Mandatory or strongly recommended dose adjustment / alternative agent. |
| **Level 1B** | High-level clinical evidence replicated across multiple independent cohorts, but lacking formal CPIC consensus. | Strong clinical consideration. |
| **Level 2A / 2B** | Moderate evidence; often associated with known VIP (Very Important Pharmacogene) variants or specific drug labels. | Moderate clinical consideration. |
| **Level 3** | Single-study association or un-replicated pharmacogenomic signal. | Research use only; not actionable. |
| **Level 4** | Case report, in vitro biochemical assay, or negative non-association finding. | Non-actionable. |

## Star-Allele Nomenclature & Diplotype Translation

1. **Star-Allele Assignment**:
   - Identify whether the variant defines or belongs to a core star-allele haplotype (e.g., *CYP2C19\*2*, *CYP2C19\*17*, *CYP2D6\*4*, *DPYD\*2A*, *TPMT\*3A*, *SLCO1B1\*5*, *VKORC1 -1639G>A*).
2. **Activity Score Calculation**:
   - Assign functional activity value to each allele:
     - Normal function = 1.0 (e.g. *1)
     - Decreased function = 0.5 (e.g. *9, *10, *41)
     - No function = 0.0 (e.g. *2, *3, *4, *5)
     - Increased function = ≥ 2.0 (e.g. *17 or gene duplication)
3. **Phenotype Categorization**:
   - **Ultrarapid Metabolizer (UM)**
   - **Rapid Metabolizer (RM)**
   - **Normal Metabolizer (NM / Extensive)**
   - **Intermediate Metabolizer (IM)**
   - **Poor Metabolizer (PM)**
4. **Clinical Action & Prescribing Implications**:
   - State therapeutic recommendation (e.g. 50% dose reduction, switch from clopidogrel to prasugrel/ticagrelor for *CYP2C19* IM/PM, avoid fluoropyrimidines for *DPYD* intermediate/poor metabolizers).
