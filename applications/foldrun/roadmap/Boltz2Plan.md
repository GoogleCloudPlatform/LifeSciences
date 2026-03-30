# Boltz-2 Integration Plan

## Overview

Boltz-2 is an open-source biomolecular structure prediction model by the
Wohlwend Lab. MIT license. Predicts protein, RNA, DNA, and ligand structures,
plus **binding affinity** — unique among the three models.

- Repo: https://github.com/jwohlwend/boltz
- Install: `pip install boltz[cuda]`
- Run: `boltz predict input.yaml`
- GPU: A100 40GB+ recommended
- Output: CIF files + confidence JSON + affinity JSON
- Unique: binding affinity prediction (log10 IC50, binder probability)

## Prerequisites

- AF2 refactor (PRs 1-7) complete
- Recommended: OF3 (PRs 8-10) complete first (proves the plugin pattern)

## Implementation (PRs 11-13)

---

### PR 11: Container Image + NFS Data

#### Container: `src/boltz-components/`

```dockerfile
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

RUN apt-get update && apt-get install -y python3 python3-pip
RUN pip install boltz[cuda] google-cloud-storage

COPY scripts/ /opt/boltz-scripts/
```

Key scripts:
- `run_predict.py` — wraps `boltz predict` with GCS I/O
  - Downloads input YAML from GCS
  - Sets `--cache` to NFS weights path
  - Sets precomputed MSA paths
  - Uploads CIF + confidence + affinity outputs to GCS
- `run_msa.py` — runs Jackhmmer against NFS databases for protein chains
  - Outputs .a3m files for Boltz to consume

#### NFS Data

```
/mnt/nfs/foldrun/
  boltz/
    params/            # Boltz model weights (auto-downloaded on first run,
                       # or pre-downloaded via `boltz predict --cache /path`)
```

Protein MSA databases (uniref90, mgnify) already on NFS — reused.
No RNA-specific databases needed (Boltz uses ColabFold for RNA MSAs
or accepts precomputed).

#### .env additions

```bash
BOLTZ_COMPONENTS_IMAGE=us-central1-docker.pkg.dev/PROJECT/foldrun-repo/boltz-components:latest
BOLTZ_PARAMS_PATH=boltz/params
BOLTZ_VIEWER_URL=https://boltz-viewer-HASH.run.app
BOLTZ_ANALYSIS_URL=https://boltz-analysis-HASH.run.app
```

---

### PR 12: Model Plugin (`models/boltz/`)

#### Directory structure

```
models/boltz/
  __init__.py          # MODEL_ID, CAPABILITIES, register
  config.py            # BoltzConfig(CoreConfig) — image, params path
  base.py              # BoltzTool(BaseTool) — GPU tiers
  hardware.py          # Boltz GPU selection (A100 default, no relax)
  tools.py             # BoltzSubmitTool, extends BaseTool
  startup.py           # Boltz tool config loading
  pipeline/
    config.py          # Boltz pipeline env vars
    pipeline.py        # Boltz KFP pipeline definition
    components/
      boltz_configure_run.py   # Download YAML from GCS, resolve seeds
      boltz_msa_pipeline.py    # Jackhmmer against NFS databases
      boltz_predict.py         # boltz predict with NFS weights
  utils/
    input_converter.py  # fasta_to_boltz_yaml() converter
```

#### models/boltz/__init__.py

```python
from foldrun_app.core.model_registry import register_model

MODEL_ID = "boltz"
DISPLAY_NAME = "Boltz-2"
CAPABILITIES = ["protein", "rna", "dna", "ligand", "affinity"]
INPUT_FORMAT = "yaml"
OUTPUT_FORMAT = "cif"

register_model(MODEL_ID, __import__(__name__))
```

#### Input Format: Boltz YAML

```yaml
version: 1
sequences:
  - protein:
      id: A
      sequence: MQIFVKTLTGKTITL...
      msa: /path/to/precomputed.a3m
  - ligand:
      id: B
      ccd: ATP
  - ligand:
      id: C
      smiles: 'N[C@@H](Cc1ccc(O)cc1)C(=O)O'
properties:
  - affinity:
      binder: C
```

For protein-only jobs, the agent accepts FASTA and converts:

```python
# core/input_converter.py
def fasta_to_boltz_yaml(fasta_content: str, msa_paths: dict = None) -> str:
    """Convert FASTA to Boltz YAML."""
    sequences = parse_fasta_content(fasta_content)
    yaml_data = {"version": 1, "sequences": []}
    for i, seq in enumerate(sequences):
        chain_id = chr(65 + i)
        entry = {
            "protein": {
                "id": chain_id,
                "sequence": seq["sequence"],
            }
        }
        if msa_paths and chain_id in msa_paths:
            entry["protein"]["msa"] = msa_paths[chain_id]
        yaml_data["sequences"].append(entry)
    return yaml.dump(yaml_data)
```

For complex jobs (protein + ligand with affinity), the agent builds YAML
interactively or accepts structured input from the user.

#### Output Format

Boltz produces per-input output directory:
```
out_dir/
  predictions/
    input_name/
      input_name_model_0.cif                    # Best structure (CIF)
      confidence_input_name_model_0.json        # Confidence scores
      affinity_input_name.json                  # Affinity predictions (if requested)
      pae_input_name_model_0.npz                # PAE matrix
      pde_input_name_model_0.npz                # PDE matrix
      plddt_input_name_model_0.npz              # Per-token pLDDT
      input_name_model_1.cif                    # 2nd best structure
      ...
  processed/                                    # Preprocessed input data
```

Key confidence metrics from `confidence_*.json`:
- `confidence_score` — overall prediction quality
- `ptm` — predicted TM-score
- `iptm` — interface predicted TM-score
- `ligand_iptm` — ligand-specific interface score
- `protein_iptm` — protein-specific interface score
- `complex_plddt` — overall complex pLDDT
- `complex_iplddt` — interface pLDDT
- `chains_ptm` — per-chain TM-scores
- `pair_chains_iptm` — per-chain-pair interface scores

Key affinity metrics from `affinity_*.json`:
- `affinity_pred_value` — predicted binding affinity as log10(IC50) in uM
  - Use for: lead optimization, comparing binder strength
- `affinity_probability_binary` — probability that ligand is a binder (0-1)
  - Use for: hit discovery, binder/decoy classification
- Multiple heads: `affinity_pred_value1/2`, `affinity_probability_binary1/2`

#### KFP Pipeline: `boltz_pipeline.py`

```
ConfigureRunBoltz -> MSAPipeline(NFS, CPU) -> ParallelFor[PredictBoltz(NFS weights, GPU)]
```

**ConfigureRunBoltz component:**
- Downloads YAML (or FASTA, converted to YAML) from GCS
- Determines seed configs
- Outputs: input_yaml artifact, seed_configs list

**MSAPipeline component (CPU, NFS-mounted):**
- Runs Jackhmmer against uniref90, mgnify (protein chains)
- Outputs .a3m files
- Injects MSA paths into YAML (`msa:` field per protein chain)
- Machine: c2-standard-16

**MSA optional: ColabFold** (set `msa_source='colabfold'`):
- Skip MSAPipeline component
- Set `--use_msa_server` flag in predict step

**PredictBoltz component (GPU, NFS-mounted):**
- Runs `boltz predict input.yaml` with:
  - `--cache /mnt/nfs/foldrun/boltz/params/`
  - `--out_dir <GCS staging path>`
  - `--diffusion_samples 5` (configurable)
  - `--output_format mmcif`
  - `--use_potentials` (optional, improves physical quality)
  - `--write_full_pae` (for analysis)
- Machine: a2-highgpu-1g (A100 40GB) default
- NFS mount: for weights
- No relax phase

**Affinity prediction:** When the input YAML includes a `properties.affinity`
section, Boltz automatically computes binding affinity. No extra pipeline
step needed — it's part of the predict run.

#### GPU Tiers

```python
# models/boltz/hardware.py
def recommend_gpu(total_tokens: int) -> str:
    """Boltz GPU selection. No relax phase."""
    if total_tokens > 2000:
        return 'A100_80GB'
    return 'A100'
```

Same as OF3 — A100 minimum, no L4, no relax.

#### Agent Tool: submit_boltz_prediction

```python
def submit_boltz_prediction(
    sequence: str,              # FASTA string (protein-only) or Boltz YAML string
    job_name: str = None,
    gpu_type: str = "auto",
    diffusion_samples: int = 5,
    predict_affinity: bool = False,  # Boltz-unique
    ligand_smiles: str = None,       # For quick protein+ligand jobs
    msa_source: str = "nfs",         # "nfs" (default) or "colabfold"
    enable_flex_start: bool = True,
    use_potentials: bool = True,
) -> dict:
```

When `predict_affinity=True` and `ligand_smiles` is provided, the tool
auto-builds the YAML with the affinity section — the user doesn't need
to write YAML manually.

#### Tests

- `tests/unit/models/boltz/test_boltz_config.py` — BoltzConfig, env vars
- `tests/unit/models/boltz/test_boltz_pipeline.py` — pipeline compiles
- `tests/unit/models/boltz/test_boltz_hardware.py` — GPU tiers
- `tests/unit/core/test_input_converter.py` — add FASTA to Boltz YAML tests
  - Monomer FASTA -> single protein YAML
  - With ligand SMILES -> protein + ligand YAML
  - With affinity flag -> YAML includes properties section
- `tests/integration/test_boltz_tool_routing.py` — agent routes to Boltz

---

### PR 13: Cloud Run Services

#### boltz-analysis-job (`src/boltz-analysis-job/`)

Imports shared library from `src/foldrun-analysis-lib/`.

Boltz-specific parsing:
- Input: CIF files + `confidence_*.json` + `affinity_*.json`
- Metrics: confidence_score, ptm, iptm, ligand_iptm, complex_plddt,
  affinity_pred_value, affinity_probability_binary
- Plots: pLDDT per-residue, PAE heatmap, affinity bar chart
- Gemini prompt: Boltz-specific
  - Affinity interpretation: "The predicted binding affinity (log10 IC50 = -7.2)
    suggests nanomolar binding. The binder probability of 0.92 indicates high
    confidence this is a true binder."
  - Drug discovery context: comparison to FEP accuracy
  - Pocket analysis: which residues contact the ligand
- Consolidation: rank by confidence_score, include affinity in summary

#### boltz-analysis-viewer (`src/boltz-analysis-viewer/`)

Flask app, imports shared base from `src/foldrun-analysis-lib/`.

Boltz-specific viewer features:
- CIF loading via `/api/cif` endpoint
- Protein-ligand complex rendering
- Pocket surface rendering (binding site highlight)
- **Affinity display panel** (unique to Boltz):
  - Binding affinity value (log10 IC50)
  - Binder probability score with confidence badge
  - Binder/decoy classification
- pLDDT confidence coloring
- Sample comparison (select different diffusion samples)
- Ligand contacts table (which residues are within 4A)

---

## Key Differences from OF3

| Aspect | OpenFold3 | Boltz-2 |
|--------|-----------|---------|
| Input format | JSON | YAML |
| Unique feature | RNA MSA databases | Binding affinity |
| MSA requirement | Jackhmmer + nhmmer (RNA) | Jackhmmer only (protein) |
| Output confidence | summary_confidences.json | confidence_*.json per sample |
| Affinity output | No | affinity_*.json |
| Potentials | No | `--use_potentials` (improves poses) |
| Weight download | `setup_openfold` | Auto-downloads on first run |
| CLI | `run_openfold predict` | `boltz predict` |

## Automated Implementation

```bash
stdbuf -oL claude --model 'claude-sonnet-4-6[1m]' \
  --dangerously-skip-permissions \
  --print \
  -p "Read applications/foldrun/roadmap/Boltz2Plan.md. Execute PRs 11-13.
After each major step, run tests. Commit after each passing step.
Do not proceed if tests fail." \
  2>&1 | stdbuf -oL tee applications/foldrun/boltz-refactor-log.txt
```
