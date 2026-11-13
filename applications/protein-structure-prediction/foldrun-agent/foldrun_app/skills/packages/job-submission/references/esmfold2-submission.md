# ESMFold2 & ESMFold2-Fast Submission Reference

## When to Select ESMFold2 / ESMFold2-Fast (`submit_esmfold2_prediction`)
- **ESMFold2-Fast (`model_variant="fast"`, default)**: Uses `biohub/ESMFold2-Fast` (ESM C backbone + 24-block trunk + all-atom diffusion head). Always runs in **zero-MSA single-sequence mode** (`use_msa=False`), bypassing CPU database searches for rapid structure folding.
- **Full ESMFold2 (`model_variant="full"`)**: Uses `biohub/ESMFold2` (ESM C backbone + 48-block trunk + all-atom diffusion head). Defaults to `use_msa=True` (runs CPU Jackhmmer alignment against `uniref90` + `mgnify` with NFS sequence-hash MSA cache) and also supports `use_msa=False`.
- **Supported Molecule Types**: Supports proteins (with optional CCD modifications), RNA, DNA, and small-molecule CCD or SMILES ligands. Note: ESMFold2 does not predict IC50 binding affinity (use Boltz-2 for affinity).

## Input Formats (`submit_esmfold2_prediction`)
1. **FASTA or raw sequence (auto-converted to ESMFold2 JSON)**: Single-chain monomer, multi-chain complex, or ligand headers (`>L|ccd=ATP`, `>L|smiles`).
2. **ESMFold2 Query JSON**:
```json
{
  "name": "ubiquitin_atp",
  "sequences": [
    {"protein": {"id": "A", "sequence": "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"}},
    {"ligand": {"id": "B", "ccd": ["ATP"]}}
  ]
}
```

## Hardware & Pipeline Parameters
- **GPU Auto-Selection (`gpu_type="auto"`)**:
  - `<=2500` tokens (`fast`) / `<=2000` tokens (`full`): **NVIDIA A100 40GB** (`a2-highgpu-1g`)
  - `>2500` tokens (`fast`) / `>2000` tokens (`full`): **NVIDIA A100 80GB** (`a2-ultragpu-1g`, with `RTX_PRO_6000` 96GB `g4-standard-48` available via spillover or explicit `gpu_type="RTX_PRO_6000"`, and `L4` `g2-standard-12` available via explicit `gpu_type="L4"`)
- **Seeds & Diffusion Samples**: Default `num_model_seeds=1`, `num_diffusion_samples=1`, `num_loops=4`, `num_sampling_steps=200`.
- **Outputs**: Produces mmCIF coordinates (`seed_<seed>_sample_<idx>_model.cif`), confidence summaries (`_confidences.json` with `ptm`, `iptm`, `ranking_score`, `plddt_mean`, `chain_ptm`, `chain_pair_iptm`), and PAE matrices (`_pae.npz`).

## ESMFold2 Pre-Submission Confirmation Table
Present the hardware breakdown and wait for user approval before calling `submit_esmfold2_prediction`:

```markdown
## ESMFold2-Fast Submission Plan for <job_name> (<N> tokens, <C> chains)

| Phase | Machine | GPU | Strategy |
|-------|---------|-----|----------|
| MSA Pipeline | Skipped (ESMFold2-Fast zero-MSA mode) or n4-standard-8 (full ESMFold2) | - | - |
| Predict | a2-highgpu-1g (or a2-ultragpu-1g / g4-standard-48 / g2-standard-12) | A100 × 1 (or A100 80GB × 1 / RTX PRO 6000 × 1 / L4 × 1) | DWS FLEX_START |

- Model Variant: ESMFold2-Fast (biohub/ESMFold2-Fast, 24-block trunk, use_msa=False)
- Predictions: <seeds> seed(s) × <samples> diffusion sample(s) = <total> structure(s)

Shall I submit this job?
```
