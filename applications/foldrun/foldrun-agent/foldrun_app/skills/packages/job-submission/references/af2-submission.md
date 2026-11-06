# AlphaFold2 (AF2) Submission Reference

## FASTA Sequence Format & Validation
- **Minimum length**: 10 residues per chain (`ACDEFGHIKLMNPQRSTVWY`).
- **Monomer (`submit_af2_monomer_prediction`)**: Single FASTA record (`>protein_name\nSEQUENCE`).
- **Multimer (`submit_af2_multimer_prediction`)**: 2+ FASTA records (`>chain_A\nSEQ_A\n>chain_B\nSEQ_B`).
- **Batch (`submit_af2_batch_predictions`)**: List of monomer/multimer job configs submitted in parallel.
- The system automatically validates sequences before submission and strips internal whitespace.

## Hardware & Pipeline Parameters
- **`gpu_type='auto'` (default)**: Selects GPU by sequence length (A100 provisions faster than L4 under DWS `FLEX_START`):
  - Monomer <=1,500 residues → `A100` 40GB (`a2-highgpu-1g`, relax: `A100` 40GB)
  - Monomer >1,500 residues → `A100_80GB` (`a2-ultragpu-1g`, relax: `A100` 40GB)
  - Multimer <1,000 total residues → `A100` 40GB (relax: `A100` 40GB)
  - Multimer >=1,000 total residues → `A100_80GB` (relax: `A100` 40GB)
- **Explicit override**: `gpu_type` accepts `'L4'`, `'A100'`, or `'A100_80GB'`. Override AMBER relax GPU via `relax_gpu_type`.
- **MSA Method (`msa_method`)**:
  - `'auto'` (default) / `'jackhmmer'`: CPU-based search (`c2-standard-16`) using Jackhmmer (UniRef90, MGnify) and HHblits (UniRef30, BFD).
  - `'mmseqs2'`: GPU-accelerated search (monomers with `use_small_bfd=True`; requires pre-built MMseqs2 indexes on Filestore via `./deploy-all.sh <PROJECT_ID> <REGION> --steps convert`).
- **Database (`use_small_bfd`)**: `True` (default, faster ~15–30 min) or `False` (Full BFD, ~30–60+ min, compatible with `jackhmmer` only).
- **Templates (`max_template_date`)**: Default `'2030-01-01'` (all PDB templates enabled). Set an earlier date to restrict templates.
- **Scheduling (`enable_flex_start`)**: `True` (default, DWS `FLEX_START` spot/preemptible queueing) or `False` (`ON_DEMAND`).

## AF2 Pre-Submission Confirmation Table
Present the resolved hardware and parameters before calling `submit_af2_*`:

```markdown
## Submission Plan for <name> (<N> residues, <monomer|multimer>)

| Pipeline Phase | Machine Type | GPU | Count | Strategy |
|----------------|-------------|-----|-------|----------|
| Data Pipeline | c2-standard-16 | None (CPU, Jackhmmer) | - | DWS FLEX_START |
| Predict | a2-highgpu-1g | A100 40GB | 1 | DWS FLEX_START |
| Relax | a2-highgpu-1g | A100 40GB | 1 | DWS FLEX_START |

- GPU: A100 40GB (auto-selected)
- MSA Method: Jackhmmer (default, CPU) — set `msa_method='mmseqs2'` for GPU search
- Database: Small BFD (`use_small_bfd=True`)
- Templates: enabled (`max_template_date=2030-01-01`)
- Relaxation: Enabled (`run_relaxation=True`)

Shall I submit this job?
```

## AF2 Reference (internal knowledge — use when helping users, don't dump unprompted)
- AF2 runs **5 distinct neural network architectures** per prediction (not random seeds):
  - **Monomer**: `model_1` through `model_5` (5 predictions total).
  - **Monomer with templates**: `model_1_ptm` through `model_5_ptm` (includes pTM head).
  - **Multimer**: `model_1_multimer_v3` through `model_5_multimer_v3` (`num_predictions_per_model=5` seeds × 5 models = 25 predictions total).
- Best model is ranked by `ranking_confidence` (average `pLDDT` for monomers; `0.8 * ipTM + 0.2 * pTM` for multimers).
