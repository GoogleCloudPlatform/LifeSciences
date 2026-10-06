---
name: job-submission
description: Submission of protein, RNA, ligand, and multimer predictions via AlphaFold2, AlphaFold3, OpenFold3, and Boltz-2
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

# Job Submission — Model Selection

Four models are available. Choose based on the input:

| Model | Tool | Use When |
|-------|------|----------|
| **AlphaFold2** | `submit_af2_monomer_prediction` | Single-chain protein (monomer) |
| **AlphaFold2** | `submit_af2_multimer_prediction` | Protein-only complex (multimer) |
| **AlphaFold2** | `submit_af2_batch_predictions` | Multiple AF2 jobs at once |
| **AlphaFold3** | `submit_af3_endpoint_prediction` | Single all-atom complex (protein, RNA, DNA, ligands, ions); Full 630 GB MSA (`msa_free=False`, default) or fast zero-MSA (`msa_free=True`) via KFP + Vertex AI Endpoint |
| **AlphaFold3** | `submit_af3_batch_predictions` | Multiple AF3 jobs at once — submits each target as an async KFP `PipelineJob` (`alphafold3-inference-pipeline-...`) queued across the warm H100 replica(s) |
| **OpenFold3** | `submit_of3_prediction` | Protein + RNA, DNA, or ligands; preferred for RNA (has full RNA MSA via nhmmer) |
| **Boltz-2** | `submit_boltz2_prediction` | Covalent modifications, glycans, or when user explicitly requests it; can do RNA/DNA/ligands but **no RNA MSA** |

**Decision rule**:
- Protein-only → AlphaFold2 (monomer or multimer) or AlphaFold 3 (if requested or comparing)
- Single AF3 job → **AlphaFold 3** (`submit_af3_endpoint_prediction`)
- Multiple AF3 jobs (2+ targets) → **AlphaFold 3 Batch** (`submit_af3_batch_predictions` — always prefer batch over calling `submit_af3_endpoint_prediction` in a loop)
- Contains RNA, DNA, or ligands with OpenFold3 pipeline → **OpenFold3** (preferred: runs nhmmer RNA MSA for better RNA accuracy)
- Contains covalent modifications or glycans → **Boltz-2** (only model that supports these)
- User explicitly requests Boltz-2 or AlphaFold 3 → use requested model
- RNA + covalent mod/glycan → Boltz-2 (no choice), but note RNA accuracy may be lower without MSA

Boltz-2 natively uses YAML input. `submit_boltz2_prediction` will automatically convert FASTA to Boltz-2 YAML.

**Data Handling Notice**
When a user submits their FIRST prediction in a session, include this note in the confirmation:
"Your protein sequence will be stored in your project's Cloud Storage bucket, processed by Agent Platform Pipelines, and analyzed by Gemini — all within your GCP project. No data is sent outside Google Cloud. Results persist until you delete them."
Do NOT repeat this notice on subsequent submissions in the same session.

**CRITICAL: Pre-Submission Confirmation Required for Vertex AI Pipelines**
NEVER submit a Vertex AI Pipeline job (`submit_af2_*`, `submit_of3_*`, `submit_boltz2_*`) without first showing the user a detailed hardware breakdown and getting explicit confirmation.
Before calling any pipeline submit function, ALWAYS present the following table and ask for confirmation:

| Pipeline Phase | Machine Type | GPU | Count | Strategy |
|----------------|-------------|-----|-------|----------|
| Data Pipeline  | c2-standard-16 | None (CPU, Jackhmmer) | - | DWS FLEX_START |
| Predict        | (machine type) | (GPU type — auto-selected or user override) | (count) | DWS FLEX_START |
| Relax          | (machine type) | (GPU type — auto-downgraded) | (count) | DWS FLEX_START |

Include:
- The sequence name, length, and type (monomer/multimer)
- The **resolved** GPU and MSA method (show what was auto-selected, note if user can override)
- The MSA method (default: Jackhmmer CPU; optional: MMseqs2 GPU if indexes are built)
- Scheduling strategy: DWS FLEX_START (default) or ON_DEMAND
- Whether relaxation is enabled
- **Template date**: always show the `max_template_date` value (default: 2030-01-01 = all PDB templates enabled)

**Per-Phase GPU Allocation Rules (defaults):**
- **Data Pipeline**: Always CPU-only (c2-standard-16), no GPU
- **Predict**: Uses the user-requested GPU (default: A100 40GB)
- **Relax**: Matches the predict tier (AMBER runs on the same machine, no downgrade):
  - If predict uses A100_80GB → relax uses A100 (40GB)
  - If predict uses A100 → relax uses A100 (40GB)
  - If predict uses L4 (explicit override only) → relax uses L4
- **Relax GPU Override**: The user can override the relax GPU by specifying `relax_gpu_type`.
  Always show the default relax GPU in the plan, and if the user wants to change it, use the `relax_gpu_type` parameter.

Example confirmation message:
```
## Submission Plan for T1031 (95 residues, monomer)

| Phase | Machine | GPU | Strategy |
|-------|---------|-----|----------|
| Data Pipeline | c2-standard-16 | None (Jackhmmer, CPU) | DWS FLEX_START |
| Predict | a2-highgpu-1g | A100 40GB × 1 | DWS FLEX_START |
| Relax | a2-highgpu-1g | A100 40GB × 1 | DWS FLEX_START |

- GPU: A100 40GB (auto-selected — provisions faster than L4 under DWS FLEX_START)
- MSA Method: Jackhmmer (default, CPU) — set msa_method='mmseqs2' for GPU-accelerated search (requires MMseqs2 index conversion)
- Database: Small BFD (faster)
- Templates: enabled (max_template_date=2030-01-01, all PDB templates included) — set an earlier date to restrict
- Scheduling: DWS FLEX_START (spot/preemptible, queues when GPUs unavailable)
- Relaxation: Enabled

Shall I submit this job?
```

Wait for explicit user confirmation (e.g., "yes", "submit", "go ahead") before calling the submit function. DO NOT submit automatically.

## FASTA Sequence Format

When submitting sequences, ensure they follow proper FASTA format:

### Valid Format Examples

**Monomer (single chain):**
```
>protein_name
MKTIALSYIFCLVFADYKDDDDKGSAATTDSTNGEEEEE
```

**Multimer (multiple chains):**
```
>chain_A
MKTIALSYIFCLVFADYKDDDDKGSAATTDSTNGEEEEE
>chain_B
ACDEFGHIKLMNPQRSTVWY
```

### Sequence Requirements
- **Minimum length**: 10 residues per chain
- **Valid amino acids**: ACDEFGHIKLMNPQRSTVWY (standard 20)
- **No numbers or special characters** in sequence
- **No empty sequences** - each chain must have actual residues
- **Uppercase recommended** but lowercase will be auto-converted
- **No whitespace** in sequences (spaces/tabs/newlines within sequence will be removed)

### Common Errors to Avoid
❌ Empty sequence after header: `>protein\n` (missing sequence!)
❌ Too short: `>protein\nMKT` (only 3 residues, need ≥10)
❌ Invalid characters: `>protein\nMKT123ALSYIF` (numbers not allowed)
❌ Multiple chains for monomer job (use multimer submission instead)

### Validation
The system automatically validates all sequences before submission and will provide clear error messages if format is incorrect.

## Best Practices

### Hardware Selection
- **gpu_type: 'auto'** (default): Automatically selects GPU based on sequence length. L4 is no longer auto-selected — A100 provisions faster under DWS FLEX_START.
  - Monomer <=1500 residues → A100 40GB (relax: A100 40GB)
  - Monomer >1500 residues → A100 80GB (relax: A100 40GB)
  - Multimer <1000 total residues → A100 40GB (relax: A100 40GB)
  - Multimer >=1000 total residues → A100 80GB (relax: A100 40GB)
- **Explicit override**: Users can set gpu_type to 'L4', 'A100', or 'A100_80GB'. If a user requests L4, note that L4 quota is often limited and may queue longer than A100 under DWS FLEX_START.
- **DWS FLEX_START is enabled by default** — jobs queue via Dynamic Workload Scheduler when GPUs are unavailable (avoids provisioning failures, uses spot/preemptible pricing)
- **Check quotas first**: Use check_gpu_quota before submitting to see available capacity and avoid failures
- When showing the confirmation table, always show the **resolved** GPU (not 'auto') and note it was auto-selected

### MSA Method (msa_method)
- **msa_method: 'auto'** (default): Always selects **Jackhmmer** (CPU-based). Works out of the box with downloaded FASTA databases. No extra setup required.
- **msa_method: 'jackhmmer'**: Explicit CPU-based MSA search. Uses Jackhmmer (UniRef90, MGnify) and HHblits (UniRef30, BFD). Works with all database configurations.
- **msa_method: 'mmseqs2'**: GPU-accelerated MSA search (optional, 177x faster). **Requires**:
  1. `use_small_bfd=True` (FASTA databases only)
  2. Pre-built MMseqs2 indexes on Filestore (run ConvertMMseqs2Tool first)
  - If the user requests mmseqs2 but indexes haven't been built, remind them to run the conversion tool first. The conversion is a one-time step that takes ~3-4 hours on n1-highmem-32 with local SSDs.

### Database Options
- **use_small_bfd: true**: Faster, recommended for most cases (15-30 min for small proteins). Compatible with both jackhmmer and mmseqs2 MSA methods.
- **use_small_bfd: false**: Full BFD database, slower but more thorough (30-60 min+). Only compatible with jackhmmer MSA method.

### Dynamic Workload Scheduler (DWS)
- **enable_flex_start: true** (default): Job queues via DWS when GPUs are unavailable (spot/preemptible pricing)
- **enable_flex_start: false**: Job fails immediately if no GPU capacity (on-demand pricing)

## AF2 Reference (internal knowledge — use when helping users, don't dump unprompted)

### AF2 Model Variants
AF2 runs 5 neural network architectures per prediction (not seeds — these are distinct trained models):
- **Monomer**: model_1 through model_5 (monomer preset), each produces 1 prediction = 5 total
- **Monomer with templates**: model_1_ptm through model_5_ptm, includes pTM head for quality scoring
- **Multimer**: model_1_multimer_v3 through model_5_multimer_v3, 5 predictions per model × 5 seeds = 25 total
- The best model is selected by ranking_confidence (pLDDT for monomers, 0.8*ipTM + 0.2*pTM for multimers)

## OpenFold3 (OF3) Predictions

### When to Use OF3 vs AF2
- **OF3**: Multi-molecule complexes (protein + RNA + DNA + ligands), single proteins with ligands, RNA structures, DNA-binding proteins. **Preferred for RNA** — runs nhmmer MSA against Rfam + RNAcentral for better RNA accuracy.
- **Boltz-2**: Like OF3, but adds covalent modifications and glycans. **No external RNA MSA** — uses model priors only for RNA chains (less accurate for RNA than OF3).
- **AF2**: Single-chain proteins (monomer) or protein-only complexes (multimer)
- **Decision rule**: RNA/DNA/ligands → OF3 (better RNA). Covalent mods/glycans → Boltz-2. Protein-only → AF2.
- **Proactive suggestion**: If a user asks about drug binding, RNA interactions, or multi-molecule structures, suggest OF3. Only suggest Boltz-2 proactively if they mention glycans, covalent bonds to ligands, or explicitly ask for it.

### OF3 Input Formats
OF3 accepts two input formats via `submit_of3_prediction`:

**1. FASTA (auto-converted to OF3 JSON)**:
Good for protein-only or simple RNA inputs. The agent auto-detects molecule types:
- Sequences with standard amino acids → protein
- Sequences containing U (ACGU) → RNA
- Long sequences of only ACGT → DNA

**2. Native OF3 JSON (for ligands and complex inputs)**:
Required when the input includes ligands (SMILES or CCD codes). Guide users to provide:
```json
{
  "queries": {
    "my_complex": {
      "chains": [
        {"molecule_type": "protein", "chain_ids": ["A"], "sequence": "MKTI..."},
        {"molecule_type": "ligand", "chain_ids": ["B"], "smiles": "CC(=O)OC1..."},
        {"molecule_type": "rna", "chain_ids": ["C"], "sequence": "AGCUAGCU"}
      ]
    }
  }
}
```

### OF3 Job Parameters — Seeds vs Samples
OF3 uses diffusion-based prediction with two levels of diversity:
- **Seeds** (`num_model_seeds`): Different random weight initializations → **independently folded structures** (potentially very different conformations). Scale-out unit for parallelism.
- **Samples** (`num_diffusion_samples`): Different diffusion trajectories from the same seed → **variations on a theme** (similar structures, different denoising paths). Cheap, sequential on same GPU.

**Recommended configurations** (following AlphaFold3 paper protocol):
| Use Case | Seeds | Samples | Total | Recommendation |
|----------|-------|---------|-------|----------------|
| Quick test / screening | 1 | 5 | 5 | Good for initial exploration |
| Standard (AF3 paper) | 5 | 5 | 25 | Recommended for production results |
| High confidence | 5 | 10 | 50 | Publication-quality, maximum diversity |

### OF3 Pre-Submission Confirmation Table
Before calling `submit_of3_*`, present the following breakdown to the user:

| Phase | Resource | Provisioning / Machine | Estimated Runtime |
|:---|:---|:---|:---|
| **Data / MSA Pipeline** | CPU Only | e2-standard-16 / c2-standard-16 | ~5–15 min |
| **Diffusion Prediction** | 1x NVIDIA A100 (40GB/80GB) | DWS FLEX_START | ~5–30 min depending on seeds |
| **Relaxation** | N/A (None) | None | N/A |

> **Hardware Constraints:**
> - OF3 predictions require an NVIDIA A100 GPU (`a2-highgpu-1g` minimum). NVIDIA L4 GPUs are NOT supported for OF3 prediction.
> - Always warn the user if target quota for A100 GPUs in the selected region is unavailable.

Hardware and operational rules:
- **No AMBER Relaxation Phase**: OF3 uses diffusion-based generation and does not have an AMBER relaxation step (unlike AF2). Never include or offer a relaxation phase for OF3.
- **Minimum GPU Requirement**: OF3 requires a minimum of an **A100 GPU** (`a2-highgpu-1g` with 40GB or 80GB VRAM) for the predict phase. **L4 GPUs are NOT supported** for OF3 inference due to memory constraints in diffusion trunk evaluation.
- **MSA Pipeline Phase**: Always runs on CPU only (`c2-standard-16` or `e2-standard-16`) using Jackhmmer and nhmmer for MSA search and `pdb_seqres` template search.
- **Scheduling Strategy**: Defaults to `DWS FLEX_START` (spot/preemptible queue); can also run `STANDARD` / `ON_DEMAND`.

Include: query name, token count, molecule types (protein/RNA/DNA/ligand/ion), chain count, seeds × samples, **use_templates value**

**Example OF3 confirmation:**
```
## OF3 Submission Plan for kinase_atp (287 tokens, 2 chains)

| Phase | Machine | GPU | Strategy |
|-------|---------|-----|----------|
| MSA Pipeline | c2-standard-16 | None (Jackhmmer + pdb_seqres template search) | STANDARD |
| Predict | a2-highgpu-1g | A100 × 1 | DWS FLEX_START |

- Molecule types: 1 protein chain (A), 1 ligand chain (B: ATP)
- GPU: A100 (auto-selected for 287 tokens; minimum A100 required, L4 unsupported)
- Templates: enabled (`use_templates=True`, jackhmmer vs pdb_seqres → pdb_mmcif structures) — set `use_templates=False` to skip
- Predictions: 5 seeds × 5 diffusion samples = 25 structures (AF3 standard)
- Scheduling: DWS FLEX_START (spot/preemptible, queues when GPUs unavailable)
- Relaxation: None (OF3 does not use an AMBER relaxation phase)
- Output: CIF + confidence JSON per sample, ranked by ranking_score

Shall I submit this job?
```

Wait for explicit user confirmation (e.g., "yes", "submit", "go ahead") before calling the submit function. DO NOT submit automatically.

## OF3 Reference (internal knowledge — use when helping users, don't dump unprompted)

Use this reference to answer questions, help construct inputs, and interpret results.
Experienced users don't need tutorials — just answer their questions directly.
Only offer detailed guidance when users are getting started or ask for help.

### Query JSON Format
OF3 uses a specific JSON schema. Help users construct it when asked:

```json
{
  "queries": {
    "my_prediction": {
      "chains": [
        {"molecule_type": "protein", "chain_ids": ["A"], "sequence": "MQIFVKTLTGKTITL..."},
        {"molecule_type": "protein", "chain_ids": ["A", "B"], "sequence": "MKTI..."},
        {"molecule_type": "rna", "chain_ids": ["C"], "sequence": "AGCUAGCU"},
        {"molecule_type": "dna", "chain_ids": ["D"], "sequence": "ATCGATCG"},
        {"molecule_type": "ligand", "chain_ids": ["E"], "ccd_codes": "ATP"},
        {"molecule_type": "ligand", "chain_ids": ["F"], "smiles": "CC(=O)OC1C[NH+]2CCC1CC2"}
      ]
    }
  }
}
```

Key rules:
- Multiple chain_ids on one entry = homo-oligomer (e.g., `["A","B"]` = homodimer)
- Protein sequences use standard amino acid alphabet (ACDEFGHIKLMNPQRSTVWY)
- RNA uses ACGU, DNA uses ACGT
- Ligands use either `ccd_codes` (PDB standard) or `smiles` (custom molecules), NOT both on the same chain
- One query can have multiple chains of different types
- For double-stranded DNA (dsDNA), both complementary strands must be entered as separate chains
- Chain IDs: 1-4 alphanumeric characters (e.g., "A", "B1")

### Common Ligand CCD Codes
Help users find the right code — these are the most commonly predicted:
- **Nucleotides**: ATP, GTP, ADP, GDP, AMP, GMP, CTP, UTP
- **Cofactors**: NAD, NADP, FAD, FMN, COA (coenzyme A), SAM (S-adenosylmethionine)
- **Metal ions**: ZN (zinc), MG (magnesium), CA (calcium), FE (iron), MN (manganese), CU (copper)
- **Heme/porphyrins**: HEM (heme), HEC (heme C)
- **Common drugs**: If the user asks about a specific drug, suggest they provide the SMILES string instead
- **Ions as ligands**: Metal ions (ZN, MG, CA, FE) are entered as ligand chains with ccd_codes, not as part of the protein sequence

### dsDNA Input
For double-stranded DNA, enter each strand as a separate chain with complementary sequences:
```json
{"molecule_type": "dna", "chain_ids": ["B"], "sequence": "ATCGATCG"},
{"molecule_type": "dna", "chain_ids": ["C"], "sequence": "CGATCGAT"}
```
The model will predict the double-helix structure with both strands.

### Output Structure
OF3 writes outputs to a nested directory structure:
```
<query_name>/
  seed_<N>/
    <query>_seed_<N>_sample_1_model.cif              # 3D structure
    <query>_seed_<N>_sample_1_confidences.json        # Per-residue scores
    <query>_seed_<N>_sample_1_confidences_aggregated.json  # Summary scores
    <query>_seed_<N>_sample_2_model.cif
    ...
    timing.json                                       # Runtime in seconds
  inference_query_set.json                            # Input with resolved seeds
```

## AlphaFold 3 (AF3) — 4-Stage KFP Pipeline + Managed H100 Endpoint (`Full 630 GB MSA` & `--msa-free` Modes)

AlphaFold 3 predicts all-atom 3D structures across proteins, multimers, nucleic acids (DNA/RNA), small-molecule ligands (CCD codes or SMILES), and metal ions (`MG`, `ZN`, `CA`, `FE`). Every AF3 prediction (`submit_af3_endpoint_prediction` for single targets, `submit_af3_batch_predictions` for 2+ targets) runs as an **asynchronous 4-stage Vertex AI `PipelineJob`** (`alphafold3-inference-pipeline-YYYYMMDDHHMMSS`):

1. **`1. Provision & Queue AF3 Endpoint`**: Verifies the AF3 Endpoint has an active GPU deployment (auto-initiating deployment if idle-drained to 0 replicas) and waits in a FIFO GCS slot queue (`_slot_0 .. _slot_{R-1}`) until a warm H100 replica is available.
2. **`2. Run AF3 Inference (H100 Endpoint)`**: Calls `endpoint.predict()` on the warm `a3-highgpu-1g` (NVIDIA H100 80GB + 3 TB local NVMe SSD) replica while heartbeating the slot lease, handles long-running HTTP proxy disconnects (>8 min on >1,000-aa proteins) by polling `output_dir` in GCS, and **immediately releases the replica slot in `finally:`** the instant inference finishes so the next queued job starts with zero delay.
3. **`3. Report AF3 Endpoint Available`**: Verifies replica slot release, records live queue depth in `_activity.json`, and arms the 20-minute `alphafold3-idle-drain-*` watchdog once the queue drains (reverting endpoint cost to `$0.00/hr` after 20 minutes of inactivity).
4. **`4. Process AF3 Results & Expert Analysis`**: Runs on CPU off the GPU critical path — harvests Cloud Logging container execution logs (`execution.log`), enumerates and ranks all generated `seed-*_sample-*` structures (`5` for monomers, `25` for 5-seed complexes), renders per-rank `plddt_plot_{i}.png` and `pae_plot_{i}.png`, runs `gemini-3.1-pro-preview` multimodal Expert Analysis (`status: "success"`), and writes `analysis/summary.json`.

### AF3 Seeds vs Diffusion Samples (5 for Monomers, 25 for Multimers/Complexes)
Like OpenFold 3 and Boltz-2, AlphaFold 3 generates `num_diffusion_samples` (default `5`) denoising trajectories per seed in `modelSeeds`, and bulk-uploads all `seed-{S}_sample-{0..4}` subdirectories to GCS:
- **Monomers (`1 chain`) & `--msa-free` Fast Screening**: Default `modelSeeds: [1]` × `num_diffusion_samples: 5` = **5 structures** (`seed_1_sample_0` … `seed_1_sample_4`).
- **Multimers & Multi-Entity Complexes (`2+ chains`, protein-ligand, protein-RNA, protein-DNA in Full-MSA mode)**: Default `modelSeeds: [1, 2, 3, 4, 5]` × `num_diffusion_samples: 5` = **25 structures** (`seed_1_sample_0` … `seed_5_sample_4`), matching AlphaFold 2 Multimer (25 structures) and the AlphaFold 3 Nature paper benchmark protocol. Because all 5 seeds are passed in a single `input.json`, the H100 container runs the Jackhmmer/nhmmer MSA search **only once** and reuses the compiled JAX trunk across all 5 seeds.

### AF3 Input Formats (`dialect: "alphafold3"`)
`submit_af3_endpoint_prediction` and `submit_af3_batch_predictions` accept raw FASTA (auto-converted for monomers, multimers, RNA, and DNA) or native AlphaFold 3 JSON (`dialect: "alphafold3"`, `version: 1`) for complexes with ligands, SMILES inhibitors, metal ions, or dsDNA:

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
- **Custom small-molecule drug (SMILES)**: `{"ligand": {"id": "B", "smiles": "COc1cc2c(cc1OCCCN3CCOCC3)ncn2-c4cc(Cl)c(F)cc4"}}`
- **Double-stranded DNA (dsDNA)**: Pass two complementary `"dna"` entries (`{"dna": {"id": "B", "sequence": "GCGTGGGCG"}}, {"dna": {"id": "C", "sequence": "CGCCCACGC"}}`).
- **RNA monomer / aptamer**: `{"rna": {"id": "A", "sequence": "GGAUACCCUGAUGAGUCCGAAAGGACGAAACAGU"}}` (runs `nhmmer` over Rfam + RNACentral in Full-MSA mode).
- **Metal ions (`MG`, `ZN`, `CA`, `FE`)**: Pass as `{"ligand": {"id": "C", "ccdCodes": ["MG"]}}` (if passed as `{"ion": {"id": "C", "ion": "MG"}}`, FoldRun automatically normalizes it to `"ligand"` with `"ccdCodes"`).

### Calibrated Production Runtimes on Warm H100 (`a3-highgpu-1g`)
| Target Class | Mode | Typical H100 Runtime |
|---|---|---|
| **Fast Screening (any polymer <400 tokens)** | `msa_free=True` (`--msa-free`) | **~55–65 sec** (`~0.9 min`) |
| **Small/Medium Monomer, RNA, or Ligand Complex (30–350 aa/nt)** | `msa_free=False` (Full 630 GB MSA) | **~2.9–6.1 min** (`173s–365s`) |
| **Multi-Chain Protein Multimer (2+ distinct protein chains)** | `msa_free=False` (Full 630 GB MSA) | **~5.5–8.0 min** (`~350s` for 2 chains; Jackhmmer runs per unique chain) |
| **Large Multi-Domain Protein (1,000–1,250 aa, e.g. JAK2 / full-length EGFR)** | `msa_free=False` (Full 630 GB MSA) | **~15–20 min** (`934s–1161s`) |

### AF3 Pre-Submission Confirmation Table
Before calling `submit_af3_endpoint_prediction` or `submit_af3_batch_predictions`, present the following breakdown to the user:

| KFP Pipeline Stage | Resource | Provisioning / Machine | Estimated Runtime |
|:---|:---|:---|:---|
| **1. Provision & Queue AF3 Endpoint** | Vertex AI PipelineJob (`e2-standard-4`) | Checks endpoint health (auto-deploys if dormant) + FIFO GCS replica slot queue | Instant if warm & free; queues automatically in batch |
| **2. Run AF3 Inference (H100)** | Managed Vertex AI Endpoint (`a3-highgpu-1g`) | Dedicated NVIDIA H100 80GB + 3 TB NVMe SSD (630 GB MSA bundle, ~$11.06/hr) | ~58s (`--msa-free`), ~3–6m (<350 aa Full MSA), ~15–20m (>1,000 aa) |
| **3. Report Endpoint Available** | Vertex AI PipelineJob (`e2-standard-4`) | Releases replica slot immediately for next queued job + arms 20m idle auto-undeploy | <5 sec |
| **4. Process Results & Expert Analysis** | Vertex AI PipelineJob (`e2-standard-4`) + Gemini 3.1 Pro | Off-GPU log harvest, pLDDT/PAE plots, multimodal Gemini 3.1 Pro Expert Analysis | ~35–50 sec (runs while H100 is already serving next job) |

> **Hardware Constraints, Backlog Scaling & Operational Rules:**
> - Runs against the managed Vertex AI Online Prediction Endpoint (`AF3_ENDPOINT`). Check status first with `check_af3_endpoint`.
> - **Maximize 1 Replica First, Scale Replicas on the Same Endpoint for Backlogs >30 min**:
>   - Because a warm H100 replica takes ~58s (`--msa-free`) or ~3–6m (standard Full-MSA) per structure whereas cold-starting an additional H100 replica with the 630 GB MSA bundle takes ~10–12 minutes, **keep `min_replica_count=1` for backlogs of <=25 `--msa-free` jobs or <=5 Full-MSA jobs**.
>   - If the user wants to run a larger batch (>30 min estimated queue), recommend scaling replicas in-place on the **same** endpoint via `deploy_af3_endpoint(min_replica_count=N, max_replica_count=N)` (e.g., `2–4` replicas) rather than creating a second endpoint. The 20-minute idle auto-drain watchdog automatically undeploys the endpoint back to `$0.00/hr` after the batch finishes.

Wait for explicit user confirmation before calling `submit_af3_endpoint_prediction` or `submit_af3_batch_predictions`.
