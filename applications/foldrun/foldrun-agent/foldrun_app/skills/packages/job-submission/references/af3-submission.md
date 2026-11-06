# AlphaFold 3 (AF3) Submission Reference

## Execution Modes & Tools
- **Single Target (`submit_af3_endpoint_prediction`)** or **Batch (`submit_af3_batch_predictions` for 2+ targets)**:
  - Runs as a 4-stage KFP `PipelineJob` (`alphafold3-inference-pipeline-...`) against the Terraform-managed dedicated H100 endpoint (`a3-highgpu-1g`, NVIDIA H100 80GB + 3 TB local NVMe SSD with the 630 GB MSA bundle):
    1. **Provision & Queue AF3 Endpoint**: Checks endpoint model deployment and waits in the FIFO replica slot queue.
    2. **Run AF3 Inference (H100)**: Runs inference (`msa_free=False` Full 630 GB MSA by default, or `msa_free=True` fast `--msa-free` screening in ~58s) and writes all `seed-*_sample-*` structures to GCS.
    3. **Report Endpoint Available**: Releases the replica slot immediately; endpoint scales to zero (`$0.00/hr`) after 20 minutes idle.
    4. **Process Results & Expert Analysis**: Dispatches the parallel `foldrun-analysis-job` Cloud Run Job (`taskCount = 5` or `25`) + Gemini Expert Analysis and completes KFP immediately.

## Seeds, Diffusion Samples & Ensemble Advisory Rule
- **Monomers (1 chain) or `--msa-free`**: Default `modelSeeds: [1]` × `num_diffusion_samples: 5` = **5 structures**.
- **Multimers & Multi-Entity Complexes (2+ chains in Full-MSA mode)**: Default `modelSeeds: [1, 2, 3, 4, 5]` × `num_diffusion_samples: 5` = **25 structures** (Jackhmmer/nhmmer MSA runs once and reuses the compiled trunk across all 5 seeds).
- **Rule — Diffusion Ensemble Verification**: If a monomer specifies <5 samples or a multimer/complex specifies <25 samples, DO NOT overwrite silently. Highlight in the pre-submission plan:
  > "⚠️ **Ensemble Advisory**: Your input specifies `<N>` structure(s). Standard AF3 best practice is 5 structures for monomers and 25 (5 seeds × 5 samples) for multimers/complexes. Would you like to (A) keep `<N>` for a rapid screen, or (B) upgrade to `<5 or 25>` for the full ensemble?"

## AF3 Input Formats (`dialect: "alphafold3"`, `version: 1`)
Accepts raw FASTA (auto-converted for proteins, RNA, DNA) or native AF3 JSON for ligands, SMILES, ions, or dsDNA:
```json
{
  "name": "af3_kinase_atp_mg",
  "modelSeeds": [1, 2, 3, 4, 5],
  "sequences": [
    {"protein": {"id": "A", "sequence": "IGRGNFGEVFSGRLRADNTLVAVKSCRETLPPDIKAKFLQEAKILKQYSHPNIVRLIGVCTQKQPIYIVMELVQGGDFLTFLRTEGARLRVKTLLQMVGDAAAGMEYLESKCCIHRDLAA"}},
    {"ligand": {"id": "B", "ccdCodes": ["ATP"]}},
    {"ligand": {"id": "C", "ccdCodes": ["MG"]}}
  ],
  "dialect": "alphafold3",
  "version": 1
}
```
- **Custom SMILES drug**: `{"ligand": {"id": "B", "smiles": "COc1cc2..."}}`
- **dsDNA**: Two complementary `"dna"` entries (`{"dna": {"id": "B", "sequence": "GCGTGGGCG"}}, {"dna": {"id": "C", "sequence": "CGCCCACGC"}}`).
- **Metal ions (`MG`, `ZN`, `CA`, `FE`)**: Pass as `{"ligand": {"id": "C", "ccdCodes": ["MG"]}}`.

## Calibrated H100 Runtimes & Pre-Submission Confirmation
| Target Class | Mode | Typical Warm H100 Runtime |
|---|---|---|
| Fast Screening (<400 tokens) | `msa_free=True` (`--msa-free`) | ~55–65 sec (~0.9 min) |
| Small/Medium Monomer, RNA, or Ligand Complex (30–350 tokens) | `msa_free=False` (Full 630 GB MSA) | ~2.9–6.1 min |
| Multi-Chain Protein Multimer (2+ chains) | `msa_free=False` (Full 630 GB MSA) | ~5.5–8.0 min |
| Large Multi-Domain Protein (1,000–1,250 aa) | `msa_free=False` (Full 630 GB MSA) | ~15–20 min |

- **Endpoint & Batch Scaling Rules**:
  - Check `check_af3_endpoint` before large submissions. If no model is deployed, instruct the user to run `./deploy-all.sh <PROJECT_ID> <REGION> --steps infra --enable-af3`.
  - For batch submissions of `N` Full-MSA jobs where `N × 4.5 min / active_replicas > 30 min` on 1 replica, note: *"Estimated queue drain time is ~`<M>` min on 1 replica. To parallelize, increase `af3_max_replica_count` in Terraform and re-run the infrastructure step."*
