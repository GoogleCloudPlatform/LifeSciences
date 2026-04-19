# Plan: Add Boltz-2 as a New Model to FoldRun

## Context

FoldRun is a multi-model protein structure prediction platform deployed on GCP. It currently supports **AlphaFold2** (protein-only) and **OpenFold3** (protein + RNA + DNA + ligands). Both are integrated as model plugins through a registry pattern with KFP pipelines on Vertex AI.

**Boltz-2** is an open-source diffusion-based biomolecular structure prediction model (from MIT, `jwohlwend/boltz`). Like OF3, it predicts proteins, RNA, DNA, ligands, and additionally supports covalent modifications and glycans. It outputs CIF structures with confidence metrics (pLDDT, pTM, ipTM, ranking_score) — very similar to OF3.

The goal is to add Boltz-2 as a third model, following the established OF3 plugin pattern.

### References

- Boltz repo: https://github.com/jwohlwend/boltz
- NVIDIA NIM Boltz-2 docs: https://docs.nvidia.com/nim/bionemo/boltz2/latest/configure.html

### Design Decisions (confirmed)

1. **Self-hosted pipeline** — Follow the OF3 pattern exactly: self-hosted container via Vertex AI Pipelines + KFP, not a NIM inference server. Consistent architecture, batch-oriented.
2. **Custom-built container** — Build a custom container from the `jwohlwend/boltz` repo (like the existing `alphafold-components` and `openfold3-components` images). Full control over CLI and dependencies.

---

## Architecture Overview

The OF3 integration follows a layered pattern. Boltz-2 will mirror it exactly:

```
Model Plugin Layer     foldrun_app/models/boltz2/     ← NEW (config, tools, pipeline, utils)
       ↓
Skills Layer           foldrun_app/skills/*/tools.py   ← MODIFY (add wrapper functions)
       ↓
Agent Layer            foldrun_app/agent.py             ← MODIFY (register tools, update instructions)
       ↓
Infrastructure         terraform/, src/                 ← MODIFY/ADD (Cloud Run job, env vars)
```

---

## Detailed Implementation Plan

### Phase 1: Model Plugin — `foldrun_app/models/boltz2/`

Create the entire `models/boltz2/` directory tree mirroring `models/of3/`.

#### 1.1 `models/boltz2/__init__.py`
Register the model plugin with the model registry.
```python
MODEL_ID = "boltz2"
DISPLAY_NAME = "Boltz-2"
CAPABILITIES = ["protein", "rna", "dna", "ligand"]
INPUT_FORMAT = "yaml"   # Boltz uses YAML input natively
OUTPUT_FORMAT = "cif"
register_model(MODEL_ID, __import__(__name__))
```
- **Reference:** `models/of3/__init__.py:15-23`

#### 1.2 `models/boltz2/config.py` — `Boltz2Config(CoreConfig)`
Extends `CoreConfig` with Boltz-2-specific env vars:
- `BOLTZ2_COMPONENTS_IMAGE` (required) — Custom container image
- `BOLTZ2_PARAMS_PATH` — Model weights path on NFS (default: `boltz2/params`)
- `BOLTZ2_CCD_PATH` — CCD database path on NFS
- Reuse `viewer_url` from existing config
- **Reference:** `models/of3/config.py:25-78`

#### 1.3 `models/boltz2/base.py` — `Boltz2Tool(BaseTool)`
Base class for Boltz-2 tools with:
- `_setup_compile_env()` — Set pipeline env vars (BOLTZ2_COMPONENTS_IMAGE, NFS, GPU config)
- `_recommend_gpu(num_tokens)` — GPU tiers (A100 for <=2000 tokens, A100_80GB for >2000; same as OF3)
- `_get_hardware_config()` — Hardware config dict (same A100/A100_80GB tiers, no L4)
- **Reference:** `models/of3/base.py:28-163`

#### 1.4 `models/boltz2/startup.py`
Singleton config + GPU auto-detection (filter to A100+ only, like OF3).
- **Reference:** `models/of3/startup.py`

#### 1.5 `models/boltz2/data/boltz2_tools.json`
Tool definitions for the 4 Boltz-2 tools:
- `boltz2_submit_prediction` — Submit prediction job
- `boltz2_analyze_parallel` — Trigger parallel analysis
- `boltz2_get_analysis_results` — Get analysis results
- `boltz2_open_viewer` — Open 3D viewer
- **Reference:** `models/of3/data/openfold3_tools.json`
- Adjust parameter descriptions: mention Boltz-2 YAML input format, `boltz predict` CLI

#### 1.6 `models/boltz2/utils/__init__.py`
Empty.

#### 1.7 `models/boltz2/utils/input_converter.py`
Converts FASTA to Boltz-2 YAML input format. Boltz-2 uses YAML with entity definitions:
```yaml
version: 2
sequences:
  - protein:
      id: A
      sequence: MKTI...
  - rna:
      id: B
      sequence: AGCU...
  - ligand:
      id: C
      smiles: CC(=O)O
```
Functions:
- `fasta_to_boltz2_yaml(fasta_content, job_name)` → YAML string
- `is_boltz2_yaml(content)` → bool (detect existing YAML input)
- `count_tokens(yaml_data)` → int (total sequence tokens)
- `_detect_molecule_type(sequence)` — Reuse logic from OF3's `input_converter.py`
- **Reference:** `models/of3/utils/input_converter.py`

#### 1.8 `models/boltz2/utils/pipeline_utils.py`
Pipeline compilation utilities — `load_vertex_pipeline(enable_flex_start)`.
Same pattern as OF3: add pipeline dir to sys.path, evict cached `config` module, load pipeline factory.
- **Reference:** `models/of3/utils/pipeline_utils.py`

#### 1.9 `models/boltz2/pipeline/config.py`
Pipeline environment config:
- `BOLTZ2_COMPONENTS_IMAGE` — Container image
- NFS config (shared: NFS_SERVER, NFS_PATH, NFS_MOUNT_POINT, NETWORK)
- `BOLTZ2_PARAMS_PATH`, `BOLTZ2_CCD_PATH`
- MSA database paths (same as OF3: uniref90, mgnify, pdb_seqres, rfam, rnacentral)
- GPU machine config (PREDICT_MACHINE_TYPE, PREDICT_ACCELERATOR_TYPE, etc.)
- **Reference:** `models/of3/pipeline/config.py`

#### 1.10 `models/boltz2/pipeline/components/`

**`__init__.py`** — Export: `configure_seeds_boltz2`, `msa_pipeline_boltz2`, `predict_boltz2`

**`configure_run.py`** — Seed config generator (same as OF3: `random.seed(42)`, N random ints).
- **Reference:** `models/of3/pipeline/components/configure_run.py`
- **Key decision:** Verify Boltz-2's seed generation logic. If it differs from OF3, adjust accordingly.

**`msa_pipeline.py`** — MSA search component.
- Runs jackhmmer (protein) and nhmmer (RNA) against NFS-mounted databases
- Injects MSA file paths into the query data
- **Reference:** `models/of3/pipeline/components/msa_pipeline.py`

**`predict.py`** — Prediction KFP component.
- Runs `boltz predict` CLI for a single seed
- Input: patched query YAML + MSA data
- Output: CIF structure + confidence JSON
- Finds best sample by `ranking_score` (same as OF3)
- Uses `@dsl.component(base_image=config.BOLTZ2_COMPONENTS_IMAGE)`
- **Reference:** `models/of3/pipeline/components/predict.py`
- **Verify:** Exact `boltz predict` CLI arguments from the GitHub repo README

#### 1.11 `models/boltz2/pipeline/pipelines/boltz2_inference_pipeline.py`
KFP pipeline definition:
- 3-step pipeline: ConfigureSeeds → MSA (CPU, NFS) → ParallelFor[Predict(GPU)]
- Same structure as OF3: `create_boltz2_inference_pipeline(strategy)` factory
- Uses `create_custom_training_job_from_component` for GPU tasks
- DWS FLEX_START support
- **Reference:** `models/of3/pipeline/pipelines/of3_inference_pipeline.py`

#### 1.12 `models/boltz2/tools/`

**`__init__.py`** — Export all 4 tool classes

**`submit_prediction.py`** — `Boltz2SubmitPredictionTool(Boltz2Tool)`
- Accepts FASTA or Boltz-2 YAML input
- Converts FASTA → YAML via `input_converter`
- Uploads query to GCS
- Compiles and submits KFP pipeline
- Labels with `model_type: "boltz2"`
- **Reference:** `models/of3/tools/submit_prediction.py`

**`analyze_job.py`** — `Boltz2JobAnalysisTool(Boltz2Tool)`
- Discovers output samples from GCS (CIF + confidence files)
- Triggers Cloud Run Job for parallel analysis
- **Reference:** `models/of3/tools/analyze_job.py`
- **Note:** Output directory structure may differ from OF3; verify against Boltz-2 output format

**`get_analysis_results.py`** — `Boltz2GetAnalysisResultsTool(Boltz2Tool)`
- Retrieves analysis results from GCS
- Polls for completion
- Same pattern as OF3
- **Reference:** `models/of3/tools/get_analysis_results.py`

**`open_viewer.py`** — `Boltz2OpenViewerTool(Boltz2Tool)`
- Opens viewer URL (same pattern, viewer handles CIF natively)
- **Reference:** `models/of3/tools/open_viewer.py`

---

### Phase 2: Skills Layer Updates

#### 2.1 `skills/job_submission/tools.py` — Add wrapper function
```python
def submit_boltz2_prediction(
    input: str,
    job_name: Optional[str] = None,
    num_model_seeds: int = 1,
    num_diffusion_samples: int = 5,
    gpu_type: str = "auto",
    enable_flex_start: bool = True,
) -> dict:
```
- **Reference:** `submit_of3_prediction()` at line 109

#### 2.2 `skills/job_submission/__init__.py` — Export `submit_boltz2_prediction`

#### 2.3 `skills/results_analysis/tools.py` — Add 2 wrapper functions
- `boltz2_analyze_job_parallel(job_id, overwrite)` → calls `get_tool("boltz2_analyze_parallel")`
- `boltz2_get_analysis_results(job_id, wait, timeout, poll_interval)` → calls `get_tool("boltz2_get_analysis_results")`
- **Reference:** `of3_analyze_job_parallel()` and `of3_get_analysis_results()` in same file

#### 2.4 `skills/results_analysis/__init__.py` — Export new functions

#### 2.5 `skills/visualization/tools.py` — Add wrapper function
- `open_boltz2_structure_viewer(job_id, open_browser)` → calls `get_tool("boltz2_open_viewer")`

#### 2.6 `skills/visualization/__init__.py` — Export `open_boltz2_structure_viewer`

#### 2.7 `skills/_tool_registry.py` — Add `_initialize_boltz2_tools()`
- Follow `_initialize_of3_tools()` pattern (line 140)
- Import Boltz-2 tool classes, load config, instantiate tools
- Call from `_initialize_all_tools()`

---

### Phase 3: Agent Layer Updates

#### 3.1 `agent.py` — Modifications

**Imports** (around line 48): Add Boltz-2 skill imports:
```python
from foldrun_app.skills.job_submission import submit_boltz2_prediction
from foldrun_app.skills.results_analysis import boltz2_analyze_job_parallel, boltz2_get_analysis_results
from foldrun_app.skills.visualization import open_boltz2_structure_viewer
```

**Tool registration** (around line 848): Add conditional block for Boltz-2 (similar to OF3 block):
```python
if os.getenv("BOLTZ2_COMPONENTS_IMAGE"):
    all_tools.extend([
        FunctionTool(submit_boltz2_prediction),
        FunctionTool(boltz2_analyze_job_parallel),
        FunctionTool(boltz2_get_analysis_results),
        FunctionTool(open_boltz2_structure_viewer),
    ])
```

**Startup initialization** (around line 959): Add Boltz-2 config init:
```python
try:
    from foldrun_app.models.boltz2.startup import get_config as get_boltz2_config
    get_boltz2_config()
except Exception:
    pass  # Boltz-2 not configured
```

**AGENT_INSTRUCTION** — Update the instruction string:
- Add Boltz-2 to the model selection table (use when: same as OF3 — multi-molecule complexes, but also highlight Boltz-2's additional capabilities like glycans, covalent mods)
- Add Boltz-2 input format docs (YAML)
- Add Boltz-2 pre-submission confirmation template
- Add Boltz-2 output/quality metrics section
- Update the welcome message to mention 3 models
- Add decision rule: "If user wants glycan/covalent modification support → Boltz-2"

---

### Phase 4: Infrastructure

#### 4.1 `src/boltz2-analysis-job/` — Cloud Run analysis job
Create by copying `src/of3-analysis-job/` and adapting:
- `main.py` — Same analysis logic (reads CIF + confidence JSON, generates plots, Gemini analysis). Boltz-2 outputs the same format (CIF + confidence JSON) so this is largely identical. Update `model_type` labels to `"boltz2"`.
- `Dockerfile` — Same as OF3 analysis job
- `requirements.txt` — Same dependencies
- `cloudbuild.yaml` — Same build config
- `deploy.sh` — Update job name to `boltz2-analysis-job`

#### 4.2 `terraform/analysis.tf` — Add Cloud Run job
Add `google_cloud_run_v2_job.boltz2_analysis_job` resource (copy of `of3_analysis_job`, name = `"boltz2-analysis-job"`).

#### 4.3 `.env.example` — Add Boltz-2 env vars
```
# BOLTZ2_COMPONENTS_IMAGE=us-central1-docker.pkg.dev/my-project-id/foldrun-repo/boltz2-components:latest
# BOLTZ2_ANALYSIS_JOB_NAME=boltz2-analysis-job
```

#### 4.4 `src/boltz2-components/Dockerfile` (required)
Container with Boltz-2 installed from `jwohlwend/boltz` repo. Mirrors the pattern of `src/openfold3-components/Dockerfile`. Includes:
- Boltz-2 package and dependencies
- `boltz predict` CLI entrypoint
- MSA tools (jackhmmer, nhmmer) for the MSA pipeline component
- Cloud Build config (`cloudbuild.yaml`) for automated builds

---

### Phase 5: Cost Estimation

#### 5.1 `skills/cost_estimation/pricing.py`
- Add `"boltz2"` as a valid `job_type` in `estimate_single_job()`
- Same GPU tiers as OF3 (A100, A100_80GB)
- Duration scaling similar to OF3 (diffusion-based, per-sample timing)

#### 5.2 `skills/cost_estimation/tools.py`
- Update `estimate_job_cost()` docstring to mention `"boltz2"` as valid job_type

---

## Things to Verify During Implementation

These items should be verified against the Boltz-2 repo and NVIDIA NIM docs:

1. **Boltz-2 CLI interface** — Exact `boltz predict` arguments, YAML schema, output directory structure
2. **Seed generation** — Whether Boltz-2 uses the same `random.seed(42)` pattern as OF3
3. **MSA handling** — Whether Boltz-2 handles MSA internally or requires external MSA like OF3
4. **Confidence file format** — Exact field names in Boltz-2's confidence JSON (likely `ranking_score`, `ptm`, `iptm`, `plddt`, `pde` — same as OF3/AF3)
5. **GPU memory requirements** — Minimum VRAM and token-to-GPU mapping

---

## File Summary

### New files (~27 files):
```
foldrun_app/models/boltz2/
  __init__.py
  config.py
  base.py
  startup.py
  data/boltz2_tools.json
  utils/__init__.py
  utils/input_converter.py
  utils/pipeline_utils.py
  pipeline/__init__.py
  pipeline/config.py
  pipeline/components/__init__.py
  pipeline/components/configure_run.py
  pipeline/components/msa_pipeline.py
  pipeline/components/predict.py
  pipeline/pipelines/__init__.py
  pipeline/pipelines/boltz2_inference_pipeline.py
  tools/__init__.py
  tools/submit_prediction.py
  tools/analyze_job.py
  tools/get_analysis_results.py
  tools/open_viewer.py

src/boltz2-analysis-job/
  main.py
  Dockerfile
  requirements.txt
  cloudbuild.yaml
  deploy.sh

src/boltz2-components/
  Dockerfile
  cloudbuild.yaml
```

### Modified files (~11 files):
```
foldrun_app/skills/job_submission/tools.py
foldrun_app/skills/job_submission/__init__.py
foldrun_app/skills/results_analysis/tools.py
foldrun_app/skills/results_analysis/__init__.py
foldrun_app/skills/visualization/tools.py
foldrun_app/skills/visualization/__init__.py
foldrun_app/skills/_tool_registry.py
foldrun_app/agent.py
foldrun_app/skills/cost_estimation/pricing.py
foldrun_app/skills/cost_estimation/tools.py
terraform/analysis.tf
foldrun-agent/.env.example
```

---

## Verification Plan

1. **Unit test**: Import `foldrun_app.models.boltz2` and verify it registers with the model registry (`list_models()` includes `"boltz2"`)
2. **Input converter test**: Verify FASTA → Boltz-2 YAML conversion produces valid output
3. **Tool registry test**: With `BOLTZ2_COMPONENTS_IMAGE` set, verify all 4 tools are loaded in `_tool_registry`
4. **Pipeline compilation test**: Compile the KFP pipeline to JSON and verify it contains the expected components
5. **Agent test**: Verify `create_alphafold_agent()` includes Boltz-2 tools when env var is set
6. **End-to-end test**: Submit a Boltz-2 prediction with a short test protein, monitor completion, run analysis, open viewer
