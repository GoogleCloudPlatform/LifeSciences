# Agent Skills Directory

This directory contains the **4 custom clinical and regulatory genomics workflow skills** authored specifically for the Variant Annotation Agent:

1. `acmg-clinvar-gnomad-triage` — ACMG/AMP germline variant classification integrating ClinVar and gnomAD v4.
2. `alphagenome-avi-interpretation` — AlphaGenome Variant Impact (AVI) Phred tiering and 18-modality interpretation.
3. `gwas-noncoding-variant-mechanisms` — Non-coding GWAS locus prioritization and cis-regulatory mechanism mapping.
4. `pharmacogenomics-star-allele-review` — PharmGKB clinical evidence levels and CPIC star-allele dosing guidance.

## Google Cloud Agent Registry Science Skills (`ScopedGCPSkillRegistry`)

Upstream [Google DeepMind Science Skills](https://github.com/google-deepmind/science-skills) are registered in Google Cloud Agent Registry using the shared script (`../shared/scripts/sync_skills_to_registry.py`) and dynamically queried at runtime via ADK's `GCPSkillRegistry` (`ScopedGCPSkillRegistry` in `app/app_utils/skills_loader.py`).
