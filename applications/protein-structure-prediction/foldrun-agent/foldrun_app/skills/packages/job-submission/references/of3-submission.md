# OpenFold3 (OF3) Submission Reference

## Input Formats (`submit_of3_prediction`)
1. **FASTA (auto-converted to OF3 JSON)**: Protein (`ACDEFGHIKLMNPQRSTVWY`), RNA (`ACGU`), or DNA (`ACGT`, or header hint `>seq|dna`). Minimum 5 residues per polymer chain.
2. **Native OF3 JSON (required for ligands/ions)**:
```json
{
  "queries": {
    "my_complex": {
      "chains": [
        {"molecule_type": "protein", "chain_ids": ["A"], "sequence": "MQIFVKTLTGKTITL..."},
        {"molecule_type": "protein", "chain_ids": ["B", "C"], "sequence": "MKTI..."},
        {"molecule_type": "rna", "chain_ids": ["D"], "sequence": "AGCUAGCU"},
        {"molecule_type": "dna", "chain_ids": ["E"], "sequence": "ATCGATCG"},
        {"molecule_type": "ligand", "chain_ids": ["F"], "ccd_codes": "ATP"},
        {"molecule_type": "ligand", "chain_ids": ["G"], "smiles": "CC(=O)OC1C[NH+]2CCC1CC2"}
      ]
    }
  }
}
```
- **Homo-oligomers**: Pass multiple `chain_ids` on one entry (e.g. `["B", "C"]`).
- **dsDNA**: Pass both complementary strands as separate `"dna"` chain entries.
- **Ligands**: Specify either `ccd_codes` or `smiles` (never both on the same chain).

## Seeds vs Diffusion Samples
- **Seeds (`num_model_seeds`)**: Distinct random initializations producing independent folds.
- **Samples (`num_diffusion_samples`)**: Denoising trajectories per seed on the same GPU.

| Use Case | Seeds | Samples | Total | Recommendation |
|----------|-------|---------|-------|----------------|
| Quick screening | 1 | 5 | 5 | Initial exploration |
| Standard (AF3 paper) | 5 | 5 | 25 | Production default (`5 × 5`) |
| High confidence | 5 | 10 | 50 | Maximum conformational diversity |

## OF3 Pre-Submission Confirmation
- **Hardware rules**: Minimum **NVIDIA A100** (`a2-highgpu-1g` 40GB, `a2-ultragpu-1g` 80GB, or `g4-standard-48` 96GB `RTX_PRO_6000`; **L4 is unsupported**). No AMBER relaxation phase (`Relaxation: None`). MSA Pipeline runs on CPU (`n4-standard-8`, Jackhmmer + nhmmer RNA MSA + `pdb_seqres` templates).

```markdown
## OF3 Submission Plan for <query_name> (<N> tokens, <C> chains)

| Phase | Machine | GPU | Strategy |
|-------|---------|-----|----------|
| MSA Pipeline | n4-standard-8 | None (Jackhmmer + nhmmer + pdb_seqres) | STANDARD |
| Predict | a2-highgpu-1g | A100 × 1 | DWS FLEX_START |

- Molecule types: <chain breakdown>
- GPU: A100 (minimum A100 required; L4 unsupported)
- Templates: enabled (`use_templates=True`)
- Predictions: 5 seeds × 5 diffusion samples = 25 structures (AF3 paper protocol)
- Relaxation: None (diffusion model)

Shall I submit this job?
```

## OF3 Reference (internal knowledge — use when helping users, don't dump unprompted)
- **Common Ligand CCD Codes**:
  - **Nucleotides**: `ATP`, `GTP`, `ADP`, `GDP`, `AMP`, `GMP`, `CTP`, `UTP`
  - **Cofactors**: `NAD`, `NADP`, `FAD`, `FMN`, `COA`, `SAM`
  - **Metal ions** (entered as `"ligand"` chains with `ccd_codes`): `ZN`, `MG`, `CA`, `FE`, `MN`, `CU`
  - **Heme**: `HEM`, `HEC`
  - **Custom drugs**: Prefer `smiles` string when CCD code is unknown.
- **Output layout**: `<query_name>/seed_<N>/<query>_seed_<N>_sample_<M>_{model.cif,confidences.json,confidences_aggregated.json}`.
