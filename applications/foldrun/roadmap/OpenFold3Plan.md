# OpenFold3 Integration Plan

## Overview

OpenFold3 (OF3) is an open-source reproduction of AlphaFold3 by the AlQuraishi
Lab / OpenFold Consortium. Apache 2.0 license. Predicts protein, RNA, DNA, and
small molecule structures using a diffusion-based architecture.

- Repo: https://github.com/aqlaboratory/openfold-3
- Install: `pip install openfold3` + `mamba install kalign2`
- Run: `run_openfold predict --query_json=query.json`
- GPU: A100 40GB+ (32GB VRAM minimum)
- Output: CIF files + confidence JSON

## Prerequisites

The AF2 refactor (PRs 1-7) must be complete. The codebase should have:
- `core/` — BaseTool, CoreConfig, model_registry, hardware, pipeline_utils
- `models/af2/` — AF2 as a self-contained plugin
- `af2_lib/` — fully removed

## Implementation (PRs 8-10)

---

### PR 8: Container Image + NFS Data (COMPLETED)

#### What was built

1. **OF3 container** — `src/openfold3-components/Dockerfile`, built and pushed
   to Artifact Registry. Minimal image based on `openfold3:stable`, no GCS
   libraries (consistent with AF2 container).

2. **`databases.yaml`** — declarative manifest replacing hardcoded Python dicts.
   All databases across all models defined in one YAML file. Each entry declares
   `models: [af2, of3, boltz]` so shared protein databases (uniref90, mgnify,
   pdb_seqres, uniprot, pdb_mmcif) are downloaded once and used by all models.

3. **`core/batch.py`** — shared Cloud Batch submission extracted from AF2 tools.
   Any model can submit NFS-mounted Batch jobs.

4. **`core/download.py`** — generic downloader reads YAML, builds scripts,
   checks GCS for existing data, submits jobs only for what's missing.

5. **`scripts/setup_data.py`** — unified CLI with `--models`, `--mode`,
   `--status`, `--dry-run`, `--force`, `--db` flags.

6. **NFS mount renamed** — `/mnt/nfs/alphafold` → `/mnt/nfs/foldrun`.

7. **`cloudbuild.yaml`** — OF3 container build step added, data download step
   updated with `_DOWNLOAD_MODELS`, `_DOWNLOAD_MODE`, `_DATABASES_BUCKET`
   substitutions.

8. **OF3 data downloaded and verified** — params, CCD, rfam, rnacentral all
   downloaded to NFS via Cloud Batch from OpenFold S3 bucket. Shared protein
   databases (uniref90, mgnify, etc.) correctly skipped.

#### NFS layout

```
/mnt/nfs/foldrun/
  uniref90/              # Shared (AF2, OF3, Boltz)
  mgnify/                # Shared (AF2, OF3, Boltz)
  pdb_seqres/            # Shared (AF2, OF3, Boltz)
  uniprot/               # Shared (AF2, OF3)
  pdb_mmcif/             # Shared (AF2, OF3)
  alphafold2/params/     # AF2 only
  of3/params/            # OF3 only (~2GB weights)
  of3/ccd/               # OF3 only (~500MB CCD)
  rfam/                  # OF3 only (RNA MSA)
  rnacentral/            # OF3 only (RNA MSA)
```

#### Usage

```bash
# Check what's present vs missing
python scripts/setup_data.py --status

# Preview OF3 downloads (skips shared DBs already present)
python scripts/setup_data.py --models of3 --dry-run

# Download OF3 data
python scripts/setup_data.py --models of3
```

---

### PR 9: Model Plugin (`models/of3/`) — COMPLETED

#### What was built

Full OF3 model plugin following the AF2 pattern. Verified against the actual
OpenFold3 source code (`openfoldconsortium/openfold3:stable`).

#### Directory structure (actual)

```
models/of3/
  __init__.py          # MODEL_ID="openfold3", CAPABILITIES, register
  config.py            # OF3Config(CoreConfig) — image, params path
  base.py              # OF3Tool(BaseTool) — A100-only GPU tiers, no relax
  startup.py           # Singleton config, GPU auto-detection (A100+ only)
  pipeline/
    __init__.py
    config.py          # OF3 pipeline env vars (image, NFS, DB paths, RNA DBs)
    components/
      __init__.py
      msa_pipeline.py  # Jackhmmer (protein) + nhmmer (RNA) on CPU
      predict.py       # run_openfold predict with NFS weights
    pipelines/
      __init__.py
      of3_inference_pipeline.py  # 2-step: MSA(CPU) → Predict(GPU)
  tools/
    __init__.py
    submit_prediction.py  # OF3SubmitPredictionTool
  utils/
    __init__.py
    input_converter.py    # fasta_to_of3_json(), is_of3_json(), count_tokens()
    pipeline_utils.py     # load_vertex_pipeline for OF3
  data/
    openfold3_tools.json  # Tool config JSON
```

#### Key design decisions (validated against OF3 source)

**CLI**: `run_openfold predict` (click-based entrypoint, NOT `python -m`).
Verified flags from `openfold3/run_openfold.py`:
- `--query_json` (required) — path to query JSON
- `--inference_ckpt_path` (optional) — NFS weights path
- `--output_dir` (optional) — output directory
- `--use_msa_server` (bool, default True) — we set False (precomputed MSAs)
- `--use_templates` (bool, default True) — we set False
- `--num_model_seeds` (int) — seeds handled internally by OF3
- `--num_diffusion_samples` (int, default 5) — samples per seed
- NO `--ccd_path` flag (CCD resolved internally by OF3)
- NO `--seed` flag (seeds generated from hardcoded start_seed=42)

**Pipeline**: 2-step (not 3-step). No ConfigureRun component needed because
OF3's CLI handles seeds internally via `--num_model_seeds`. No ParallelFor
because all seeds run sequentially in one predict task.

```
MSAPipeline(CPU, NFS) → Predict(GPU, NFS weights)
```

**No ParallelFor**: OF3 generates seeds internally from start_seed=42. The
CLI doesn't expose a `--seed` flag, so we can't easily split seeds across
VMs. See "Future: Parallel Seed Execution" section below for the workaround.

**Config module isolation**: Both AF2 and OF3 pipeline components use bare
`import config as config`. To prevent Python's module cache from causing
cross-model config collisions, each `pipeline_utils.py` evicts `config`
from `sys.modules` before importing its pipeline.

#### Input Format: OF3 Query JSON

OF3 takes a JSON file with queries containing chains (verified against
`examples/example_inference_inputs/` in the OF3 repo):

```json
{
  "queries": {
    "my_prediction": {
      "chains": [
        {
          "molecule_type": "protein",
          "chain_ids": ["A"],
          "sequence": "MQIFVKTLTGKTITL..."
        },
        {
          "molecule_type": "ligand",
          "chain_ids": ["B"],
          "smiles": "CC(=O)OC1C[NH+]2CCC1CC2"
        },
        {
          "molecule_type": "rna",
          "chain_ids": ["C"],
          "sequence": "AGCUAGCU"
        }
      ]
    }
  }
}
```

The agent accepts both FASTA (auto-converted) and native OF3 JSON input.
`fasta_to_of3_json()` auto-detects molecule types (protein/rna/dna) and
assigns chain IDs (A, B, C...). For ligands, users must pass native JSON
with SMILES or CCD codes.

#### Output Format

OF3 produces per-query output (verified from source):
```
output_dir/
  my_prediction/
    seed_<N>/
      <query>_seed_<N>_sample_<M>_model.cif
      summary_confidences.json
    inference_query_set.json
```

#### Agent Tool: submit_of3_prediction

```python
def submit_of3_prediction(
    input: str,              # FASTA or OF3 JSON (auto-detected)
    job_name: str = None,
    num_model_seeds: int = 1,
    num_diffusion_samples: int = 5,
    gpu_type: str = "auto",  # A100 (<=2000 tokens) or A100_80GB (>2000)
    enable_flex_start: bool = True,
) -> dict:
```

**Conditional registration**: `FunctionTool(submit_of3_prediction)` is only
added to the agent's tool list when `OPENFOLD3_COMPONENTS_IMAGE` env var is
set. Without it, the agent runs in AF2-only mode.

**AF2 tool rename**: `submit_monomer_prediction` → `submit_af2_monomer_prediction`,
`submit_multimer_prediction` → `submit_af2_multimer_prediction`,
`submit_batch_predictions` → `submit_af2_batch_predictions`.

#### Tests (57 OF3-specific tests, 214 total)

- `test_of3_config.py` — OF3Config, env vars, missing image, defaults
- `test_of3_hardware.py` — A100-only GPU tiers, no L4, no relax, multi-GPU
- `test_of3_pipeline.py` — 2-step pipeline, no relax, no ParallelFor, retry policies
- `test_input_converter.py` — FASTA→JSON with correct schema, type detection, tokens
- `test_of3_compilation.py`:
  - Config module isolation (AF2→OF3 and OF3→AF2 order)
  - Pipeline compiles to valid JSON
  - Predict CLI: `run_openfold predict`, correct flags, no `--ccd_path`/`--seed`
  - No relax in compiled output
- `tests/unit/models/of3/test_of3_hardware.py` — GPU tiers (A100 default, no L4)
- `tests/unit/core/test_input_converter.py` — FASTA to OF3 JSON
  - Monomer FASTA -> single chain JSON
  - Multimer FASTA -> multi-chain JSON
  - Edge cases: empty, invalid sequences
- `tests/integration/test_of3_tool_routing.py` — agent routes to OF3 tools

---

### PR 10: Cloud Run Services

#### of3-analysis-job (`src/of3-analysis-job/`)

Imports shared library from `src/foldrun-analysis-lib/`.

OF3-specific parsing:
- Input: CIF files + `summary_confidences.json` (not AF2 pickle)
- Metrics: ranking_score, ptm, iptm, chain_pair_iptm, has_clash, pLDDT
- Plots: pLDDT per-residue, PAE heatmap (same matplotlib as AF2)
- Gemini prompt: OF3-specific (diffusion samples, multi-molecule, RNA/DNA,
  ligand binding interpretation)
- Consolidation: rank by ranking_score, build summary.json

#### of3-analysis-viewer (`src/of3-analysis-viewer/`)

Flask app, imports shared base from `src/foldrun-analysis-lib/`.

OF3-specific viewer features:
- CIF loading (not PDB) via `/api/cif` endpoint
- Multi-molecule display: protein + ligand + RNA/DNA in one view
- Chain-specific confidence coloring
- Ligand rendering with binding pocket highlight
- Seed/sample comparison (select different diffusion samples)
- ipTM matrix display for complexes

#### Shared analysis library extraction (part of this PR)

Before building OF3 services, extract shared code from `src/af2-analysis-job/`
into `src/foldrun-analysis-lib/`:
- GCS helpers (download/upload)
- Plot generation (pLDDT, PAE — shared matplotlib code)
- Gemini analysis (parameterized prompt)
- Consolidation logic (sort, rank, build summary)
- Flask app factory (shared routes, templates)

Then refactor `src/af2-analysis-job/` to import from the shared lib.
Verify AF2 services still work before building OF3 services.

---

### Parallel Seed Execution — IMPLEMENTED (PR 9)

Each seed runs on its own A100 via KFP ParallelFor. Default: 5 seeds × 5
diffusion samples = 25 structures (matching AF3 paper benchmark protocol).

**Pipeline**:
```
ConfigureSeeds(CPU) → MSA(CPU, NFS) → ParallelFor[Predict(A100, 1 seed each)]
```

**How it works**: The OF3 CLI doesn't have a `--seed` flag. ConfigureSeeds
generates N random seed values (matching OF3's internal `random.seed(42)`
logic). Each predict task writes a 2-line `runner_config.yaml` with
`experiment_settings: seeds: [<value>]` and passes it via `--runner_yaml`.
Each task runs `--num_model_seeds=1` with its unique seed.

**Cost**: 5 × A100 × ~8.5 min ≈ same as 1 × A100 × ~42 min. Same total
GPU-hours, 5× faster wall clock.

**Scaling guidance** (from AF3 paper + OF3 docs):

| Use Case | Seeds | Samples | Total | GPUs |
|----------|-------|---------|-------|------|
| Quick test | 1 | 5 | 5 | 1 A100 |
| Standard (AF3 paper) | 5 | 5 | 25 | 5 A100s |
| High confidence | 5 | 10 | 50 | 5 A100s |

---

## Automated Implementation

This plan can be executed by Sonnet using the same pattern as PRs 1-7.
Create a `refactor-run-of3.sh` similar to `refactor-run.sh`:

```bash
stdbuf -oL claude --model 'claude-sonnet-4-6[1m]' \
  --dangerously-skip-permissions \
  --print \
  -p "Read applications/foldrun/roadmap/OpenFold3Plan.md. Execute PRs 8-10.
After each major step, run tests. Commit after each passing step.
Do not proceed if tests fail." \
  2>&1 | stdbuf -oL tee applications/foldrun/of3-refactor-log.txt
```
