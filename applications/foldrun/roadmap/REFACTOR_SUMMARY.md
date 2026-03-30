# FoldRun Multi-Model Refactor Summary

## Why

FoldRun currently only supports AlphaFold2. We need to add OpenFold3 and
Boltz-2 (and potentially more models). The AF2 code was tightly coupled —
adding a second model would mean copy-pasting thousands of lines and
maintaining parallel codebases.

## What we did (PRs 1-7, completed)

Reorganized the agent codebase into a **plugin architecture** where each
model is self-contained. No features changed — AF2 works identically.

**Before:**
```
foldrun_app/
  af2_lib/           <-- Everything was here, tightly coupled
    config.py
    af2_tool.py      <-- Base class mixed AF2 logic with shared infra
    tools/           <-- 19 tools, all AF2-specific
    vertex_pipeline/ <-- KFP pipeline + components
```

**After:**
```
foldrun_app/
  core/              <-- Shared infrastructure (model-agnostic)
    config.py        <-- GCP project, NFS, GCS -- no model logic
    base_tool.py     <-- GCS upload/download, Vertex AI init
    hardware.py      <-- GPU quota checking
    pipeline_utils.py<-- KFP compilation
    model_registry.py<-- Plugin registration system

  models/
    af2/             <-- AF2-specific (self-contained plugin)
      config.py      <-- AF2 image, viewer URL, parallelism
      base.py        <-- AF2Tool with GPU tiers, relax phase logic
      tools/         <-- All 19 AF2 tools
      pipeline/      <-- KFP pipeline + components
      utils/         <-- FASTA parsing, visualization
```

### Step-by-step breakdown

| Step | Commit | What moved |
|------|--------|------------|
| 1 | `9c9de81` | Extracted `core/config.py` — shared infra config (project, NFS, GCS). AF2 config becomes a thin subclass that adds `ALPHAFOLD_COMPONENTS_IMAGE`, `viewer_url`, `parallelism`. |
| 2 | `8256780` | Extracted `core/base_tool.py` — GCS upload/download, Vertex AI init, Filestore lookup, label cleaning. AF2Tool now extends BaseTool, keeping only GPU tiers and relax logic. |
| 3 | `9785c5a` | Extracted `core/hardware.py` — GPU quota checking and auto-detection as pure functions. Any model can query GPU availability. |
| 4 | `d00e0e4` | Extracted `core/pipeline_utils.py` — KFP pipeline compilation helper. Model-agnostic. |
| 5 | `9737840` | Moved all AF2 code into `models/af2/` (tools, pipeline, components, utils, config). Left re-export shims at old paths so nothing broke. |
| 6 | `8d1a5c4` | Added `core/model_registry.py` — plugin registration system. AF2 auto-registers on import. Added `model_type: alphafold2` label to pipeline jobs. |
| 7 | `3bae27c` | Removed all shims. Updated every import in agent, skills, tests, and scripts to use new paths. `af2_lib/` directory deleted entirely. |

### What didn't change

- All 19 agent tools work the same
- Pipeline definitions, container images, NFS/GCS paths — unchanged
- Cloud Run analysis job and viewer — unchanged
- 114 unit tests pass
- Agent deployed and live on Agent Engine

### One new thing

Pipeline jobs now include a `model_type: alphafold2` label. This is prep
for when multiple models exist — post-submission tools will auto-detect
which model ran the job.

## How new models plug in

To add OpenFold3, create `models/of3/` following the same structure — config,
tools, pipeline, components. It registers with the model registry and the
agent automatically picks it up. No changes to shared code needed.

```python
# models/of3/__init__.py
from foldrun_app.core.model_registry import register_model

MODEL_ID = "openfold3"
DISPLAY_NAME = "OpenFold3"
CAPABILITIES = ["protein", "rna", "dna", "ligand"]

register_model(MODEL_ID, __import__(__name__))
```

## PR 8: Data infrastructure + OF3 container (completed)

Extracted shared download infrastructure, added OF3 container and data.

### What changed

1. **`databases.yaml`** — declarative manifest for all databases across all models.
   Each entry declares which models need it (`models: [af2, of3, boltz]`), source
   URL or custom script, NFS path, and extraction method. Shared databases are
   downloaded once and used by all models that need them.

2. **`core/batch.py`** — extracted Cloud Batch submission from AF2DownloadDatabaseTool
   into standalone functions. Any model's tools can submit NFS-mounted Batch jobs.

3. **`core/download.py`** — generic downloader that reads databases.yaml, builds
   scripts, checks GCS for existing data, and submits Batch jobs for the gaps.

4. **`scripts/setup_data.py`** — unified CLI replacing the old AF2-only script:
   - `--models af2,of3` — select which models to install
   - `--mode reduced|full|core` — download mode per model
   - `--status` — check GCS for what's present vs missing (includes MMseqs2 status)
   - `--dry-run` — preview what would be downloaded
   - `--force` — re-download even if already present
   - `--db uniref90` — download a single database ad-hoc

5. **Tool rename** — `AF2DownloadDatabaseTool` → `DownloadDatabaseTool`,
   `AF2ConvertMMseqs2Tool` → `ConvertMMseqs2Tool` (still in `models/af2/tools/`,
   still extends AF2Tool for agent registration).

6. **`src/openfold3-components/Dockerfile`** — OF3 container image built and
   pushed to Artifact Registry. Minimal — just the base `openfold3:stable` image.

7. **NFS mount renamed** — default `NFS_MOUNT_POINT` changed from
   `/mnt/nfs/alphafold` to `/mnt/nfs/foldrun`. Data on Filestore doesn't move
   (share is still `/datasets`), just the VM mount point name.

8. **`cloudbuild.yaml`** updated — OF3 container build step, unified data
   download step with `_DOWNLOAD_MODELS` and `_DOWNLOAD_MODE` substitutions.

### Adding a new model's data

To add Boltz databases, just add entries to `databases.yaml`:

```yaml
boltz_params:
  models: [boltz]
  display_name: Boltz Weights
  nfs_path: boltz/params
  source: https://...
```

Then `python scripts/setup_data.py --models boltz --dry-run` shows the gap,
and `--models boltz` fills it. No Python code changes needed.

## Remaining steps

Full plan is in `MULTI_MODEL_PLAN.md` and `OpenFold3Plan.md`.

### PR 9: OF3 model plugin
- Create `models/of3/` — config, tools, hardware tiers, pipeline, components
- OF3 KFP pipeline: simpler than AF2 (no relax phase, diffusion-based)
- FASTA-to-JSON input converter (OF3 uses JSON, not FASTA)
- Registers via model registry, agent routes `model="openfold3"` to it

### PR 10: OF3 Cloud Run services
- `of3-analysis-job` — CIF parsing (not PDB), OF3 confidence metrics (ipTM)
- `of3-analysis-viewer` — multi-molecule viewer (protein + ligand + RNA/DNA)
- Extract shared analysis library from AF2 services to avoid duplication

### PR 11-13: Boltz (same pattern as OF3)
- Container, model plugin, Cloud Run services
- Unique feature: binding affinity prediction (log10 IC50, binder probability)
- FASTA-to-YAML input converter (Boltz uses YAML)

### PR 14: Multi-model agent experience
- Agent recommends model based on input (protein-only vs ligand vs RNA)
- `submit_comparison()` — same sequence through multiple models
- Comparison viewer (TM-score, pLDDT side-by-side)

### Future: GKE backend
- Architecture supports KFP on GKE in addition to Vertex AI Pipelines
- Backend abstraction in `core/backends/` (Vertex vs GKE)
- Not built until there's a real GKE deployment to test against
