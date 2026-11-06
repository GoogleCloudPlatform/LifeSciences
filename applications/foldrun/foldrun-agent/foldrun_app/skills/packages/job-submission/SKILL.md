---
name: job-submission
description: Submits protein, RNA, DNA, ligand, and multimer structure prediction jobs using AlphaFold2, AlphaFold3, OpenFold3, or Boltz-2. Use when the user provides a FASTA sequence, JSON/YAML query, SMILES/CCD ligand, or asks to fold, predict, or batch-submit biomolecular structures.
metadata:
  adk_additional_tools:
    - submit_af2_monomer_prediction
    - submit_af2_multimer_prediction
    - submit_af2_batch_predictions
    - submit_of3_prediction
    - submit_boltz2_prediction
    - submit_af3_endpoint_prediction
    - submit_af3_batch_predictions
---

# Job Submission — Model Selection & Workflow

## Model Selection Matrix

| Model | Tool | Use When |
|-------|------|----------|
| **AlphaFold2** | `submit_af2_monomer_prediction` | Single-chain protein (monomer, >=10 aa) |
| **AlphaFold2** | `submit_af2_multimer_prediction` | Protein-only complex (multimer, 2+ chains) |
| **AlphaFold2** | `submit_af2_batch_predictions` | Multiple AF2 jobs at once |
| **AlphaFold3** | `submit_af3_endpoint_prediction` | Single all-atom complex (protein, RNA, DNA, ligands, ions) on dedicated H100 endpoint (`msa_free=False` Full 630 GB MSA default, or `msa_free=True` fast screening) |
| **AlphaFold3** | `submit_af3_batch_predictions` | 2+ AF3 targets queued across warm H100 replica(s) (always prefer over calling `submit_af3_endpoint_prediction` in a loop) |
| **OpenFold3** | `submit_of3_prediction` | Multi-molecule complexes (protein + RNA, DNA, or ligands); **preferred for RNA** (runs nhmmer RNA MSA against Rfam + RNAcentral) |
| **Boltz-2** | `submit_boltz2_prediction` | Covalent modifications, glycans, **binding affinity prediction** (`IC50` / binder probability), or when explicitly requested (no external RNA MSA) |

**Decision rules**:
- Protein-only → **AlphaFold2** (monomer/multimer) or **AlphaFold 3** (if requested or comparing).
- Contains RNA, DNA, or ligands → **OpenFold3** (preferred for RNA accuracy) or **AlphaFold 3** (if H100 endpoint is enabled/requested).
- Contains covalent modifications, glycans, or requires ligand binding affinity (`properties: [{affinity: ...}]`) → **Boltz-2**.
- Multiple jobs (2+ targets) → Use `submit_af2_batch_predictions` (AF2) or `submit_af3_batch_predictions` (AF3).

## Default Hardware, Provisioning & Input Summary

| Model | Data / MSA Phase | Predict Hardware (`gpu_type="auto"`) | Relax Phase | Default Seeds × Samples | Provisioning |
|-------|------------------|--------------------------------------|-------------|-------------------------|--------------|
| **AlphaFold2** | `c2-standard-16` CPU (`jackhmmer`, `use_small_bfd=True`) or GPU `mmseqs2` | `<500` aa: `L4` (`g2-standard-12`, 24 GB) or `A100` (`a2-highgpu-1g`, 40 GB); `500–1500` aa: `A100` 40 GB; `>1500` aa: `A100_80GB` (`a2-ultragpu-1g`, 80 GB) | `L4` / `A100` (`run_relaxation=True`) | Monomer: `5 × 1 = 5`; Multimer: `5 × 5 = 25` | `DWS FLEX_START` |
| **OpenFold3** | `c2-standard-16` CPU (`jackhmmer` + `nhmmer` RNA MSA + PDB templates) | `<=2200` tokens: `A100` 40 GB (`a2-highgpu-1g`); `>2200` tokens: `A100_80GB` 80 GB (`a2-ultragpu-1g`) | None | `1 × 5 = 5` (or `5 × 5 = 25` AF3 protocol) | `DWS FLEX_START` |
| **Boltz-2** | `c2-standard-16` CPU (`jackhmmer` protein MSA) | `<=2000` tokens: `A100` 40 GB (`a2-highgpu-1g`); `>2000` tokens: `A100_80GB` 80 GB (or `L4` for `<=1000` tokens) | None | `1 × 5 = 5` (or `5 × 5 = 25`) | `DWS FLEX_START` |
| **AlphaFold 3** | H100 local NVMe SSD (`msa_free=False` Full 630 GB MSA default, or `msa_free=True`) | `H100` 80 GB (`a3-highgpu-1g` Dedicated Endpoint) | None | Monomer/`msa_free`: `1 × 5 = 5`; Full-MSA complex: `5 × 5 = 25` | `DWS FLEX_START` |

- **Boltz-2 Binding Affinity (`submit_boltz2_prediction`)**: Use Boltz-2 YAML (`version: 1`) with `sequences:` (`protein` chain `A`, `ligand` chain `B` via `ccd: ATP` or `smiles: '...'`) and `properties: [{affinity: {binder: B}}]`. Outputs `affinity_pred_value` (`log10(IC50 uM)`) and `affinity_probability_binary` (`0–1` active binder probability).

## Pre-Submission Confirmation Required (Plan → Validate → Confirm → Submit)

**CRITICAL GUARDRAIL**: NEVER call `submit_af2_*`, `submit_of3_*`, `submit_boltz2_*`, or `submit_af3_*` without first presenting the hardware breakdown table and receiving explicit user approval (e.g., `"yes"`, `"submit"`, `"go ahead"`).

**FAST CONFIRMATION RULE**: Use the **Default Hardware, Provisioning & Input Summary** table above to render the pre-submission confirmation table immediately—do **NOT** call `load_skill_resource` on Turn 1 just to build a standard confirmation table. Only call `load_skill_resource` when the user asks for advanced schema syntax (covalent modifications, glycans, dsDNA reverse complements, H100 runtime benchmarks) or when constructing complex multi-chain JSON/YAML payloads after confirmation:
- **AlphaFold2**: `references/af2-submission.md`
- **OpenFold3**: `references/of3-submission.md`
- **Boltz-2**: `references/boltz2-submission.md`
- **AlphaFold 3**: `references/af3-submission.md`

### Standard Pre-Submission Hardware Breakdown Table
Always show the **resolved** GPU (not `'auto'`), MSA method, scheduling strategy (`DWS FLEX_START` default), and template/relaxation settings:

| Pipeline Phase | Machine Type | GPU | Count | Strategy |
|----------------|-------------|-----|-------|----------|
| Data / MSA Pipeline | c2-standard-16 | None (CPU, Jackhmmer) | - | DWS FLEX_START / STANDARD |
| Predict | a2-highgpu-1g (or g2-standard-12 / a3-highgpu-1g) | L4 / A100 40GB / A100 80GB / H100 80GB | 1 | DWS FLEX_START |
| Relax (AF2 only; None for OF3/Boltz-2/AF3) | a2-highgpu-1g (or g2-standard-12) | L4 / A100 40GB | 1 | DWS FLEX_START |

### Data Handling Notice (First Submission Only)
On the user's **first** prediction submission in a session, include this notice in the confirmation prompt (do not repeat on subsequent submissions):
> "Your protein sequence will be stored in your project's Cloud Storage bucket, processed by Agent Platform Pipelines, and analyzed by Gemini — all within your GCP project. No data is sent outside Google Cloud. Results persist until you delete them."
