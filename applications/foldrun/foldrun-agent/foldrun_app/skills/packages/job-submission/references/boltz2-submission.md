# Boltz-2 Submission Reference

## When to Select Boltz-2 (`submit_boltz2_prediction`)
- **Required for**: Covalent modifications, glycans, or small-molecule **binding affinity prediction** (`IC50` / binary binder probability).
- **Trade-off vs OpenFold3**: Boltz-2 supports RNA/DNA/ligands, but does **not** run external nhmmer RNA MSA (prefer OpenFold3 for unmodified RNA complexes).

## Input Formats (`submit_boltz2_prediction`)
1. **FASTA (auto-converted to Boltz-2 YAML)**: Protein, RNA, or DNA sequences (minimum 5 residues per chain).
2. **Native Boltz-2 YAML (for ligands, covalent modifications, glycans, or binding affinity)**:
```yaml
version: 1
sequences:
  - protein:
      id: A
      sequence: MKTIALSYIFCLVFADYKDDDDKGSAATTDSTNGEEEEE
  - ligand:
      id: B
      smiles: "COc1cc2c(cc1OCCCN3CCOCC3)ncn2-c4cc(Cl)c(F)cc4"
  - ligand:
      id: C
      ccd: MG
properties:
  - affinity:
      binder: B
```
- **Ligands**: Use `ccd: <CODE>` (e.g. `ATP`, `MG`, `ZN`) or `smiles: "<SMILES>"` (not both on the same chain).
- **Binding Affinity (`properties`)**: Add `properties: [affinity: <LIGAND_CHAIN_ID>]` (supported for a single small-molecule ligand with <=128 heavy atoms; produces `affinity_<name>.json` with `affinity_pred_value` (`log10(IC50 uM)`) and `affinity_probability_binary`).

## Hardware & Pipeline Parameters
- **GPU**: Requires **NVIDIA A100** (`a2-highgpu-1g` 40GB or `a2-ultragpu-1g` 80GB; L4 is unsupported). Supports multi-GPU scaling (`num_gpus=1, 2, 4, 8`) on A100/A100_80GB.
- **Seeds & Samples**: Default `num_model_seeds=1`, `num_diffusion_samples=5` (use `num_model_seeds=5`, `num_diffusion_samples=5` for 25-sample production ensembles).
- **No Relaxation Phase**: Diffusion output (`*.cif`) requires no AMBER relaxation step.

## Boltz-2 Pre-Submission Confirmation Table
Present the hardware breakdown and wait for user approval before calling `submit_boltz2_prediction`:

```markdown
## Boltz-2 Submission Plan for <job_name> (<N> tokens, <C> chains)

| Phase | Machine | GPU | Strategy |
|-------|---------|-----|----------|
| MSA Pipeline | c2-standard-16 | None (CPU Jackhmmer) | STANDARD |
| Predict | a2-highgpu-1g | A100 × 1 | DWS FLEX_START |

- Entities: <chain & ligand summary + whether affinity prediction is enabled>
- GPU: A100 (auto-selected; L4 unsupported)
- Predictions: <seeds> seed(s) × <samples> diffusion samples = <total> structures

Shall I submit this job?
```
