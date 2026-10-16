---
name: gwas-noncoding-variant-mechanisms
description: Methodology for prioritizing non-coding GWAS variants, interpreting trait associations, linkage disequilibrium, and causal regulatory mechanisms.
---

# GWAS Non-Coding Variant Mechanisms

This skill guides the functional dissection of non-coding genome-wide association study (GWAS) hits, translating statistical locus associations into precise molecular regulatory mechanisms.

## Workflow Overview

Over 90% of disease-associated variants identified by GWAS fall within non-coding genomic regions (introns, intergenic regions, promoters, enhancers). Interpreting their biological significance requires a multi-step functional genomics framework:

1. **GWAS Catalog Trait Mapping**:
   - Query the NHGRI-EBI GWAS Catalog (v.1.0) (Sollis et al. 2023).
   - Extract reported trait(s), Experimental Factor Ontology (EFO) identifiers, p-values, odds ratios (OR) or beta effect sizes, and risk alleles.
2. **Fine-Mapping & Linkage Disequilibrium (LD)**:
   - Identify whether the query variant is the reported lead SNP or an LD proxy ($r^2 \ge 0.8$) within the association block.
3. **Cis-Regulatory Element (cCRE) Colocalization**:
   - Intersect coordinates with ENCODE candidate cis-Regulatory Elements (promoters, proximal/distal enhancers, CTCF-only boundaries).
4. **Epigenomic & Expression Disruption via AlphaGenome**:
   - Determine whether the variant disrupts transcription factor binding motifs (JASPAR / UniBind / ChIP-seq).
   - Quantify predicted directional expression changes ($\log_2\text{FC}$) in disease-relevant cell types using AlphaGenome `MAX_ABS_RNA_SEQ` and `MAX_ABS_CAGE`.
   - Inspect chromatin accessibility changes (`MAX_ABS_DNASE` / `MAX_ABS_ATAC`).
5. **Target Gene Assignment (Variant-to-Gene)**:
   - Connect enhancer variants to candidate target promoters via AlphaGenome 3D chromatin conformation (`MAX_ABS_CONTACT_MAPS`) and GTEx eQTL colocalization.
