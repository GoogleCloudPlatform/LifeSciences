# FoldRun Multi-Model Integration Plan

## Goal

Extend FoldRun from AF2-only to support multiple structure prediction models
(AlphaFold2, OpenFold3, Boltz-2) with clean isolation, shared infrastructure,
and independent viewers/analysis services per model.

---

## Models Overview

| | AlphaFold2 | OpenFold3 | Boltz-2 |
|--|-----------|-----------|---------|
| Repo | (vendored) | [aqlaboratory/openfold-3](https://github.com/aqlaboratory/openfold-3) | [jwohlwend/boltz](https://github.com/jwohlwend/boltz) |
| License | Apache 2.0 | Apache 2.0 | MIT |
| Native input | FASTA | JSON | YAML |
| Output format | PDB | CIF | CIF |
| Protein | Yes | Yes | Yes |
| RNA/DNA | No | Yes | Yes |
| Ligands | No | Yes (SMILES/CCD) | Yes (SMILES/CCD) |
| Binding affinity | No | No | Yes (unique) |
| MSA pipeline | Jackhmmer/HHblits (NFS DBs) | Jackhmmer/nhmmer (NFS DBs) | Jackhmmer (NFS DBs) |
| Relaxation phase | AMBER (separate GPU step) | None (diffusion-based) | None (diffusion-based) |
| Confidence metrics | pLDDT, PAE | pLDDT, ipTM, chain-pair PAE | pLDDT, ipTM, affinity score |
| GPU requirement | L4 / A100 / A100 80GB | A100 40GB+ (32GB VRAM min) | A100 40GB+ |
| Install | Docker image | `pip install openfold3` | `pip install boltz[cuda]` |
| Run command | Custom Python | `run_openfold predict --query_json=...` | `boltz predict input.yaml` |

---

## Architecture: Model-Plugin Pattern

### Directory Structure

```
foldrun_app/
  agent.py                      # Single agent, model-aware instructions
  core/                         # SHARED -- model-agnostic
    base_tool.py                # BaseTool (GCS, Vertex AI, NFS -- no AF2 logic)
    config.py                   # Shared infra config (project, NFS, GCS bucket)
    hardware.py                 # GPU configs, DWS, quota checking
    pipeline_utils.py           # KFP compilation, NFS mount helpers
    input_converter.py          # FASTA to JSON/YAML converters

  models/                       # ONE DIRECTORY PER MODEL
    af2/
      __init__.py               # MODEL_ID = "alphafold2", CAPABILITIES = ["protein"]
      config.py                 # AF2-specific: DB paths, image, model params GCS
      tools.py                  # AF2SubmitTool, etc. (extends BaseTool)
      hardware.py               # AF2 GPU tiers (predict + relax phases)
      analysis.py               # AF2-specific: PDB parsing, pLDDT, PAE
      pipeline/
        pipeline.py             # AF2 KFP pipeline definition
        components/             # AF2 KFP components (data_pipeline, predict, relax)

    of3/
      __init__.py               # MODEL_ID = "openfold3", CAPABILITIES = ["protein", "rna", "dna", "ligand"]
      config.py                 # OF3-specific: weight paths, CCD, image
      tools.py                  # OF3SubmitTool (extends BaseTool)
      hardware.py               # OF3 GPU tiers (no relax phase)
      analysis.py               # OF3-specific: CIF parsing, ipTM, chain-pair PAE
      pipeline/
        pipeline.py             # OF3 KFP pipeline (MSA -> predict, no relax)
        components/             # OF3 KFP components

    boltz/
      __init__.py               # MODEL_ID = "boltz", CAPABILITIES = ["protein", "rna", "dna", "ligand", "affinity"]
      config.py                 # Boltz-specific: weight paths, image
      tools.py                  # BoltzSubmitTool (extends BaseTool)
      hardware.py               # Boltz GPU tiers
      analysis.py               # Boltz-specific: CIF, affinity scores, pLDDT
      pipeline/
        pipeline.py             # Boltz KFP pipeline
        components/             # Boltz KFP components

  skills/                       # AGENT TOOL WRAPPERS (thin, model-routing)
    job_submission.py           # submit_prediction(model="af2"|"of3"|"boltz", ...)
    job_management.py           # Shared -- Vertex AI pipeline ops (model-agnostic)
    results_analysis.py         # Routes to models/<model>/analysis.py
    visualization.py            # Routes to model-specific viewer URL
    ...

src/                            # CLOUD RUN SERVICES (per-model)
  alphafold-components/         # AF2 container image (existing)
  openfold3-components/         # OF3 container image (new)
  boltz-components/             # Boltz container image (new)
  af2-analysis-job/             # AF2 analysis Cloud Run (existing)
  of3-analysis-job/             # OF3 analysis Cloud Run (done — PR 10)
  boltz-analysis-job/           # Boltz analysis Cloud Run (new)
  foldrun-viewer/               # Combined viewer — AF2+OF3+Boltz (renamed from af2-analysis-viewer)
```

### Why Separate Viewers/Analysis Per Model

Each model has fundamentally different outputs:

- AF2: PDB files, pLDDT/PAE only, relaxed vs unrelaxed structures
- OF3: CIF files, multi-molecule display (protein+ligand+RNA), different seed/sample structure
- Boltz: CIF files, binding affinity heatmaps, pocket views, affinity probability scores

Separate Cloud Run services let you deploy, debug, and evolve each independently.
Cost is minimal (Cloud Run scales to zero when idle).

### What's Shared (core/)

These are truly model-agnostic:

1. **BaseTool** -- GCS upload/download, Vertex AI init, label cleaning, Filestore lookup
2. **Hardware/quota** -- GPU quota checking, DWS config, supported GPU detection
   (GPU types are the same across models, just the tier thresholds differ)
3. **Pipeline utils** -- NFS mount helper, `create_custom_training_job_from_component` wrapper
4. **Job management** -- `list_jobs`, `check_job_status`, `delete_job`, `get_job_details`
   all operate on Vertex AI pipeline metadata -- completely model-agnostic
5. **Storage management** -- GCS cleanup, orphan detection -- model-agnostic
6. **Input conversion** -- FASTA to JSON (OF3), FASTA to YAML (Boltz) converters

### What's Model-Specific (models/<model>/)

Each model plugin provides:

1. **Config** -- container image, NFS paths for weights/DBs, default parameters
2. **Hardware tiers** -- which GPU for which sequence length, number of pipeline phases
3. **Pipeline + components** -- the actual KFP definition
4. **Analysis** -- output parsing, metric extraction, viewer URL construction
5. **Submit tool** -- input validation, FASTA conversion, pipeline dispatch

---

## Model-Routing Pattern

The agent-facing tools become thin routers:

```python
# skills/job_submission.py

def submit_prediction(
    sequence: str,
    model: str = "alphafold2",  # "alphafold2" | "openfold3" | "boltz"
    job_name: str = None,
    gpu_type: str = "auto",
    ...
) -> dict:
    """Submit a structure prediction using the specified model."""
    registry = get_model_registry()
    return registry[model].submit(
        sequence=sequence, job_name=job_name, gpu_type=gpu_type, ...
    )
```

Each model registers itself:

```python
# models/of3/__init__.py
MODEL_ID = "openfold3"
DISPLAY_NAME = "OpenFold3"
CAPABILITIES = ["protein", "rna", "dna", "ligand"]
```

---

## NFS Layout (implemented)

Shared databases live at the NFS root — no per-model subdirectories, no symlinks.
Each model's pipeline config points to the same paths. Database ownership is
declared in `databases.yaml` via the `models` list.

```
/mnt/nfs/foldrun/
  uniref90/              # Shared (AF2, OF3, Boltz)
  mgnify/                # Shared (AF2, OF3, Boltz)
  pdb_seqres/            # Shared (AF2, OF3, Boltz)
  uniprot/               # Shared (AF2, OF3)
  pdb_mmcif/             # Shared (AF2, OF3)
  small_bfd/             # AF2 only (reduced mode)
  bfd/                   # AF2 only (full mode)
  pdb70/                 # AF2 only
  uniref30/              # AF2 only
  alphafold2/params/     # AF2 model weights
  of3/params/            # OF3 model weights (~2GB)
  of3/ccd/               # OF3 Chemical Component Dictionary (~500MB)
  rfam/                  # OF3 only (RNA MSA)
  rnacentral/            # OF3 only (RNA MSA)
  boltz/params/          # Boltz model weights (future)
```

---

## KFP Pipeline Comparison

### AF2 Pipeline (existing, 3 phases)

```
ConfigureRun -> DataPipeline(NFS, CPU) -> ParallelFor[Predict(GPU) -> Relax(GPU)]
```

- 5 AF2 model variants x 1 seed = 5 predict tasks + 5 relax tasks
- Data pipeline: Jackhmmer/HHblits on NFS databases (CPU) or MMseqs2 (GPU)
- Predict: AF2 model inference (GPU, NFS for model params via GCS)
- Relax: AMBER relaxation (GPU, downgraded tier)

### OF3 Pipeline (implemented, 3 steps, no relax, ParallelFor over seeds)

```
ConfigureSeeds(CPU) → MSA(NFS, CPU) → ParallelFor[Predict(A100, 1 seed each)]
```

- Default: 5 seeds × 5 diffusion samples = 25 structures on 5 A100s (AF3 paper protocol)
- ConfigureSeeds: generates N seed values, outputs list for ParallelFor
- MSA: Jackhmmer (protein) + nhmmer (RNA) on NFS databases, CPU-only
- Predict: each seed runs on its own A100 via `--runner_yaml` (injects seed value)
  `run_openfold predict --query_json=... --inference_ckpt_path=/mnt/nfs/.../of3/params/of3_ft3_v1.pt
  --runner_yaml=<per-seed yaml> --num_model_seeds=1 --num_diffusion_samples=5
  --use_msa_server=False --use_templates=False`
- No relax step (diffusion-based model)
- No relax phase (diffusion-based model)
- Weights loaded from NFS at runtime (not baked into container)

### Boltz Pipeline (new, 2 phases, no relax)

```
ConfigureRunBoltz -> MSAPipeline(NFS, CPU) -> ParallelFor[PredictBoltz(NFS weights, GPU)]
```

- 1 model x N seeds x M samples
- **MSA default: Vertex AI + NFS databases** (Jackhmmer for protein)
  - Same shared databases as AF2/OF3
- Predict: `boltz predict input.yaml` with weights from NFS and precomputed MSAs
- No relax phase
- Additional output: binding affinity scores (when ligands present)

### MSA Strategy (all models)

All three models run MSA generation on Vertex AI with NFS-mounted databases.
No external MSA servers (ColabFold etc.) — all data stays within the GCP
project boundary. This is critical for pharma/biotech IP protection.

The MSA code in each model's pipeline can be duplicated — each model may
need slightly different MSA processing (e.g., OF3 needs paired MSAs in a
specific format, Boltz needs .a3m files). Keeping MSA code per-model avoids
forced abstractions and lets each model evolve independently.

---

## Container Images

| Image | Base | Contents | NFS Deps |
|-------|------|----------|----------|
| `ALPHAFOLD_COMPONENTS_IMAGE` | AF2 image (existing) | AF2 model code, `alphafold_utils` | DB paths, model params |
| `OPENFOLD3_COMPONENTS_IMAGE` | `openfoldconsortium/openfold3:stable` | OF3 runtime, `run_openfold`, `kalign2` | Weights, CCD, optional MSA DBs |
| `BOLTZ_COMPONENTS_IMAGE` | Python 3.11 + `boltz[cuda]` | Boltz runtime, `boltz predict` | Weights, CCD |

All images do NOT bake in weights -- weights come from NFS at runtime.
This keeps images smaller and allows weight updates without rebuilds.

---

## .env Config Additions

```bash
# Existing AF2
ALPHAFOLD_COMPONENTS_IMAGE=...
MODEL_PARAMS_GCS_LOCATION=gs://bucket/alphafold2

# New: OF3
OPENFOLD3_COMPONENTS_IMAGE=us-docker.pkg.dev/PROJECT/foldrun/openfold3:latest
OF3_PARAMS_PATH=of3/params
OF3_CCD_PATH=of3/ccd
OF3_VIEWER_URL=https://of3-viewer-HASH.run.app
OF3_ANALYSIS_URL=https://of3-analysis-HASH.run.app

# New: Boltz
BOLTZ_COMPONENTS_IMAGE=us-docker.pkg.dev/PROJECT/foldrun/boltz:latest
BOLTZ_PARAMS_PATH=boltz/params
BOLTZ_VIEWER_URL=https://boltz-viewer-HASH.run.app
BOLTZ_ANALYSIS_URL=https://boltz-analysis-HASH.run.app
```

---

## Implementation: PR-by-PR Breakdown

Strategy: refactor AF2 first to prove the plugin pattern, then add OF3/Boltz
into the proven structure. Each PR leaves AF2 fully working. Tests pass
before and after every merge.

---

### PR 1: Create core/config.py (extract shared infra config) — DONE

**What moves:**
- Copy `af2_lib/config.py` -> `core/config.py`
- Keep ONLY model-agnostic properties: project_id, region, zone, bucket_name,
  databases_bucket_name, filestore_id, filestore_ip, filestore_network,
  nfs_share, nfs_mount_point, dws_max_wait_hours, supported_gpus
- Remove AF2-specific properties: base_image (-> AF2 config), viewer_url
  (-> AF2 config), parallelism (-> AF2 config)
- Remove `ALPHAFOLD_COMPONENTS_IMAGE` from required validation

**What stays:**
- `af2_lib/config.py` still exists, imports and extends `core/config.py`
  adding AF2-specific properties (base_image, viewer_url, parallelism)
- All existing imports of `af2_lib.config.Config` still work

**Boundary:** `af2_lib/config.py` becomes a thin subclass:
```python
from foldrun_app.core.config import CoreConfig

class Config(CoreConfig):
    """AF2-specific config, extends shared infra config."""
    @property
    def base_image(self) -> str:
        return os.getenv('ALPHAFOLD_COMPONENTS_IMAGE')
    # ... other AF2-only props
```

**Tests:** Existing `test_config.py` passes unchanged (Config still works).
Add `test_core_config.py` for CoreConfig in isolation.

**Risk:** Low. Additive change, no import paths change.

---

### PR 2: Create core/base_tool.py (extract from AF2Tool) — DONE

**What moves:**
- Copy `af2_lib/af2_tool.py` -> `core/base_tool.py`
- BaseTool keeps: GCS upload/download, Vertex AI init, `_ensure_clients`,
  label cleaning, `_get_filestore_info`, `gcs_console_url`
- BaseTool takes `CoreConfig` (not AF2 Config)

**What stays in AF2Tool:**
- `_recommend_gpu` (AF2-specific tier thresholds)
- `_get_hardware_config` (AF2-specific: relax GPU, MMseqs2 data pipeline)
- `_setup_compile_env` (AF2-specific env vars like ALPHAFOLD_COMPONENTS_IMAGE)

**Boundary:** `AF2Tool` becomes:
```python
from foldrun_app.core.base_tool import BaseTool

class AF2Tool(BaseTool):
    """AF2-specific tool with GPU tiers and pipeline compilation."""
    # _recommend_gpu, _get_hardware_config, _setup_compile_env stay here
```

**Tests:** All 19 AF2 tools still extend AF2Tool. No behavior change.
Add `test_base_tool.py` for BaseTool GCS/NFS methods.

**Risk:** Low. AF2Tool's __init__ just calls super().__init__().

---

### PR 3: Create core/hardware.py (extract GPU quota) — DONE

**What moves:**
- GPU quota checking logic from `AF2CheckGPUQuotaTool` splits into:
  - `core/hardware.py`: `check_gpu_quota(project, region)` -- pure function,
    queries Compute Engine API, returns quota dict. Model-agnostic.
  - `AF2CheckGPUQuotaTool.run()` calls `core.hardware.check_gpu_quota()` and
    formats the response

- GPU startup auto-detection from `af2_lib/startup.py`:
  - `core/hardware.py`: `detect_supported_gpus(project, region)` -- returns
    ordered list of GPU types with quota > 0
  - `af2_lib/startup.py` calls `core.hardware.detect_supported_gpus()`

**Tests:** Existing quota tests pass. Add unit test for `detect_supported_gpus`.

**Risk:** Low. Extracting pure functions, callers just redirect.

---

### PR 4: Create core/pipeline_utils.py (extract NFS/KFP helpers) — DONE

**What moves:**
- NFS mount dict construction (currently inline in pipeline.py)
- `create_custom_training_job_from_component` wrapper with retry/DWS config
- Pipeline compilation helpers from `af2_lib/utils/pipeline_utils.py`

**Tests:** Pipeline compilation tests pass unchanged.

**Risk:** Low. Helper functions, no state.

---

### PR 5: Create models/af2/ directory, move AF2-specific code — DONE

This is the big structural move. Do it in one PR to avoid half-migrated state.

**What moves:**
- `af2_lib/af2_tool.py` -> `models/af2/base.py` (AF2Tool with GPU tiers)
- `af2_lib/tools/*.py` -> `models/af2/tools/` (all 19 AF2 tool classes)
- `af2_lib/vertex_pipeline/` -> `models/af2/pipeline/` (KFP pipeline + components)
- `af2_lib/config.py` -> `models/af2/config.py` (AF2-specific config subclass)
- `af2_lib/startup.py` -> `models/af2/startup.py` (AF2 tool config loading)
- `af2_lib/data/alphafold_tools.json` -> `models/af2/data/alphafold_tools.json`
- `af2_lib/utils/` -> `models/af2/utils/` (fasta_utils, viz_utils, etc.)

**What stays:**
- `af2_lib/` directory remains as a redirect shim (re-exports from new locations)
  so external scripts and tests don't break immediately:
  ```python
  # af2_lib/config.py (shim)
  from foldrun_app.models.af2.config import Config  # noqa: F401
  ```
- These shims get cleaned up in a follow-up PR once all imports are updated

**models/af2/__init__.py:**
```python
MODEL_ID = "alphafold2"
DISPLAY_NAME = "AlphaFold2"
CAPABILITIES = ["protein"]
INPUT_FORMAT = "fasta"
OUTPUT_FORMAT = "pdb"
```

**Tests:** ALL existing tests pass through the shims. Then update test imports
to point to `models.af2.*` directly.

**Risk:** Medium. Lots of file moves. Mitigated by shim re-exports and running
full test suite before merging.

---

### PR 6: Model registry + routing in skills/ — DONE

**What changes:**
- New `core/model_registry.py`:
  ```python
  _MODELS = {}

  def register_model(model_id, module):
      _MODELS[model_id] = module

  def get_model(model_id):
      return _MODELS[model_id]

  def list_models():
      return list(_MODELS.keys())
  ```

- `models/af2/__init__.py` calls `register_model("alphafold2", ...)`

- `skills/_tool_registry.py` refactored:
  - Instead of hardcoding 19 AF2 tool class imports, it loads tools from
    registered models: `models.af2.startup.get_tools(config)`
  - Still returns same tool instances, just loaded through the registry

- `skills/job_submission.py` gains `model` parameter:
  ```python
  def submit_monomer_prediction(
      sequence: str,
      model: str = "alphafold2",  # future: "openfold3", "boltz"
      ...
  )
  ```
  For now, only "alphafold2" is registered. Unknown model -> error.

**Tests:** Existing tests pass. New test: `test_model_registry.py`.

**Risk:** Medium. The registry is the new contract for future models.
Getting it right here means OF3/Boltz integration is just filling in templates.

---

### PR 7: Remove af2_lib/ shims (cleanup) — DONE

- Delete all shim files in `af2_lib/`
- Update remaining imports in tests, scripts, agent.py
- `af2_lib/` directory removed entirely

**Tests:** Full test suite passes with no shims.

**Risk:** Low after PR 5-6 are proven. Pure cleanup.

---

### PR 8: OF3 container image + NFS setup (COMPLETED)

See `REFACTOR_SUMMARY.md` for full details. Summary:

- `src/openfold3-components/Dockerfile` — minimal, based on `openfold3:stable`
- `databases.yaml` — declarative manifest for all models' databases
- `core/batch.py` + `core/download.py` — shared download infrastructure
- `scripts/setup_data.py` — unified CLI (`--models`, `--mode`, `--status`, `--dry-run`, `--force`)
- OF3 data (params, CCD, rfam, rnacentral) downloaded and verified
- NFS mount renamed to `/mnt/nfs/foldrun`
- `cloudbuild.yaml` updated with `_DOWNLOAD_MODELS` and `_DATABASES_BUCKET`

---

### PR 9: OF3 model plugin (models/of3/) — DONE

Full OF3 model plugin validated against the actual OpenFold3 source code
(`openfoldconsortium/openfold3:stable`). See `OpenFold3Plan.md` PR 9 section
for detailed implementation notes.

**What was built (20 new files, 6 modified):**
- `models/of3/` — full plugin: `__init__.py`, `config.py`, `base.py`, `startup.py`
- `models/of3/pipeline/` — 2-step pipeline: MSA(CPU) → Predict(GPU)
  - No ConfigureRun (OF3 CLI handles seeds internally via `--num_model_seeds`)
  - No ParallelFor (all seeds run sequentially in one predict task)
  - No relax step (diffusion-based model)
- `models/of3/tools/submit_prediction.py` — accepts FASTA or native OF3 JSON
- `models/of3/utils/input_converter.py` — FASTA→JSON with correct OF3 schema
  (`{"queries": {"name": {"chains": [...]}}}`)
- `models/of3/data/openfold3_tools.json` — tool configuration
- Config module isolation fix — `sys.modules.pop('config')` prevents AF2/OF3
  config cache collision

**Agent changes:**
- `submit_of3_prediction` tool (conditionally registered when `OPENFOLD3_COMPONENTS_IMAGE` set)
- AF2 tools renamed: `submit_af2_monomer_prediction`, `submit_af2_multimer_prediction`,
  `submit_af2_batch_predictions`
- Model selection table in agent instructions
- Detailed OF3 guidance: ligand input (SMILES/CCD), multi-copy chains, quality metrics

**Key findings from OF3 source code review:**
- CLI is `run_openfold predict` (click-based), NOT `python -m openfold.predict`
- No `--ccd_path` flag (CCD resolved internally by OF3)
- No `--seed` flag (seeds generated from hardcoded start_seed=42 via `--num_model_seeds`)
- `--num_diffusion_samples` (default: 5) controls structural diversity per seed
- Query JSON schema uses `queries` → `chains` with `molecule_type`, `chain_ids`

**Tests (57 OF3-specific, 214 total):**
- Config, hardware, pipeline source inspection
- Pipeline compiles to valid JSON
- Config module isolation (AF2→OF3 and OF3→AF2)
- Predict CLI args validated against actual OF3 source
- Input converter with correct schema, type detection, token counting

---

### PR 10: OF3 Cloud Run services — DONE

- `src/of3-analysis-job/` — Cloud Run Job: CIF parsing, per-chain pLDDT (protein vs ligand), PDE heatmaps with chain boundaries, ipTM matrix with molecule type labels, Gemini multimodal expert analysis with OF3-specific interpretation knowledge
- `src/foldrun-viewer/` — Renamed from af2-analysis-viewer. Combined viewer auto-detects AF2 vs OF3 from summary.json. OF3 mode: CIF loading, ligand ball+stick rendering, per-chain confidence table, ranking_score/pTM/ipTM metrics, copyable input query JSON for resubmission
- Agent tools: `of3_analyze_job_parallel`, `of3_get_analysis_results`, `open_of3_structure_viewer`
- Enhanced Gemini prompts for both AF2 and OF3 with metric interpretation thresholds
- Both analysis jobs upgraded to `gemini-3.1-pro-preview`
- `cloudbuild.yaml` updated for foldrun-viewer + of3-analysis-job
- Graceful error handling for deleted/nonexistent jobs (NotFound crash fix)

**Risk:** Low. Standalone services, AF2 regression verified.

---

### PR 11: GCS database restore (cross-project sharing)

- Add GCS-first restore to `core/download.py` — if database exists in a GCS bucket, `gsutil rsync` from GCS → NFS instead of downloading from internet (~15 min vs 2-4 hours)
- Support any GCS bucket (cross-project) via `--source-bucket` flag or `GCS_DATABASES_BUCKET` env var
- Enables sharing database backups between projects (colleague sets your bucket, needs `roles/storage.objectViewer`)
- Flow: check GCS → restore from GCS if exists → else download from internet → backup to GCS
- Update `setup_data.py` with `--source-bucket` flag
- Update README with cross-project sharing instructions

**Risk:** Low. Only changes download.py and setup_data.py.

---

### PR 12: Boltz container image + NFS setup

- `src/boltz-components/Dockerfile` (Python 3.11 + boltz[cuda])
- `scripts/setup_boltz_data.py` (download weights to NFS)
- .env additions for BOLTZ_COMPONENTS_IMAGE, BOLTZ_PARAMS_PATH

**Risk:** Low. Same pattern as PR 8.

---

### PR 13: Boltz model plugin (models/boltz/)

- Same structure as OF3 plugin
- `core/input_converter.py` gains `fasta_to_boltz_yaml()` converter
- Boltz-specific: affinity prediction support in tools
- Registers via model registry

**Risk:** Low. Follows proven OF3 pattern.

---

### PR 14: Boltz Cloud Run services

- `src/boltz-analysis-job/` — CIF + affinity score parsing, Boltz-specific plots
- `src/foldrun-viewer/` — add Boltz mode to combined viewer (auto-detect, affinity heatmaps)
- Agent tools: `boltz_analyze_parallel`, `boltz_get_analysis_results`, `open_boltz_viewer`
- Note: No separate viewer — extend the combined foldrun-viewer (same pattern as OF3)

**Risk:** Low. Same pattern as PR 10.

---

### PR 15: Multi-model agent experience

- Agent instructions: model selection guidance
- `submit_comparison(sequence, models=["alphafold2","openfold3","boltz"])`
- Comparison analysis tool (TM-score, pLDDT side-by-side)
- Comparison viewer URL

**Risk:** Low. Additive feature on top of working models.

---

### Future: Database freshness tracking and auto-refresh

Currently `setup_data.py --status` reports whether databases exist on NFS,
but not when they were downloaded or whether upstream has newer versions.

**Database update frequencies:**

| Database | Upstream Cycle | Staleness Impact |
|----------|---------------|------------------|
| UniRef90 | ~8 weeks (UniProt) | High — more sequences = better MSAs |
| PDB mmcif/seqres | Weekly (RCSB) | High — newer templates |
| MGnify | ~quarterly | Medium — metagenomic diversity |
| UniProt | ~8 weeks | Medium — paired MSAs |
| PDB70 | ~quarterly (HH-suite) | Medium — template index |
| Rfam | ~annually | Low |
| RNAcentral | ~quarterly | Low-medium |
| BFD / Small BFD | Frozen | None — never updated |

**Implementation:**
1. Record download timestamp in `databases_metadata.json` on NFS (per database)
2. `setup_data.py --status` shows age: `uniref90: downloaded 2025-04-15 (327 days ago)`
3. `setup_data.py --stale [--days=90]` lists databases older than threshold
4. `setup_data.py --refresh` re-downloads only stale databases (skips frozen ones)
5. Optional: agent tool `check_database_freshness` so the LLM can proactively
   suggest refreshing before important predictions

**Priority:** Low — databases from April 2025 are fine for most use cases.
Worth revisiting after 6-12 months or when predicting novel/orphan proteins.

---

## PR Dependency Graph

```
PR 1 (core/config)              ✅ DONE
  |
PR 2 (core/base_tool)           ✅ DONE
  |
PR 3 (core/hardware) ----+      ✅ DONE
  |                       |
PR 4 (core/pipeline)     |      ✅ DONE
  |                       |
PR 5 (models/af2/ move) -+      ✅ DONE
  |
PR 6 (model registry + routing) ✅ DONE
  |
PR 7 (cleanup af2_lib/ shims)   ✅ DONE
  |
  +--- PR 8 (OF3 container+data) ✅ DONE --- PR 9 (OF3 plugin) ✅ DONE --- PR 10 (OF3 Cloud Run) ✅ DONE
  |
  +--- PR 11 (GCS database restore) -- enables fast cross-project setup
  |
  +--- PR 12 (Boltz container) --- PR 13 (Boltz plugin) --- PR 14 (Boltz Cloud Run)
  |
  +--- PR 15 (multi-model agent) -- after PR 10 + PR 13
```

**Status: PRs 1-10 complete.** Next up is PR 11 (GCS database restore for cross-project sharing).

Current capabilities:
- ✅ AF2: Full lifecycle (submit → monitor → analyze → view)
- ✅ OF3: Full lifecycle (submit → monitor → analyze → view)
- ❌ GCS restore: Databases only download from internet (PR 11)
- ❌ Boltz: Not started (PRs 12-14)

PR 11 is independent (unblocks fast colleague onboarding).
PRs 12-14 are the Boltz integration track.
PR 15 ties it all together with multi-model comparison.

---

## Setup & Install Experience (IMPLEMENTED)

Database setup is driven by `databases.yaml` and `scripts/setup_data.py`:

```bash
# Check what's downloaded vs missing
python scripts/setup_data.py --status

# Preview what would be downloaded
python scripts/setup_data.py --models af2,of3 --dry-run

# Download (skips what's already in GCS)
python scripts/setup_data.py --models af2,of3 --mode reduced

# Single database ad-hoc
python scripts/setup_data.py --db of3_params --force
```

Cloud Build uses `_DOWNLOAD_MODELS` and `_DOWNLOAD_MODE` substitutions.

### What `make setup` Does Per Model

Each model registers a setup manifest in `models/<model>/setup.py`:

```python
# models/of3/setup.py
SETUP = {
    "model_id": "openfold3",
    "display_name": "OpenFold3",

    # Container image to build/pull
    "container": {
        "dockerfile": "src/openfold3-components/Dockerfile",
        "image_name": "openfold3",
        "pull_fallback": "openfoldconsortium/openfold3:stable",
    },

    # Data to download to NFS
    "nfs_data": [
        {"name": "model_weights", "target": "of3/params/", "source": "setup_openfold", "size": "~2GB"},
        {"name": "ccd", "target": "of3/ccd/", "source": "setup_openfold", "size": "~500MB"},
    ],

    # Optional: extra databases (downloaded via Cloud Batch like AF2)
    "databases": [
        {"name": "rfam", "target": "of3/databases/rfam/", "source": "s3://openfold/alignment_databases/rfam.fasta.gz"},
        {"name": "rnacentral", "target": "of3/databases/rnacentral/", "source": "s3://openfold/alignment_databases/rnacentral.fasta.gz"},
        {"name": "nucleotide_collection", "target": "of3/databases/nucleotide_collection/", "source": "s3://openfold/alignment_databases/nucleotide_collection.fasta.gz"},
    ],

    # Cloud Run services to deploy
    "services": [
        {"name": "of3-analysis-job", "source": "src/of3-analysis-job/"},
        {"name": "of3-analysis-viewer", "source": "src/of3-analysis-viewer/"},
    ],

    # .env variables this model requires
    "env_vars": [
        "OPENFOLD3_COMPONENTS_IMAGE",
        "OF3_PARAMS_PATH",
        "OF3_CCD_PATH",
        "OF3_VIEWER_URL",
        "OF3_ANALYSIS_URL",
    ],
}
```

### Setup Flow

```
make setup MODELS=af2,of3
  |
  v
1. Validate prerequisites
   - gcloud auth, project set, APIs enabled
   - Filestore exists and is accessible
   - Artifact Registry repo exists
   |
2. Per-model setup (parallel where possible)
   |
   +-- AF2:
   |   a. Build & push AF2 container image (or skip if exists)
   |   b. Download AF2 databases to NFS via Cloud Batch
   |   c. Deploy af2-analysis-job Cloud Run
   |   d. Deploy af2-analysis-viewer Cloud Run
   |   e. Write AF2 env vars to .env
   |
   +-- OF3:
       a. Build & push OF3 container image (or pull from Docker Hub)
       b. Download OF3 weights + CCD to NFS
       c. Download RNA databases to NFS via Cloud Batch (optional)
       d. Deploy of3-analysis-job Cloud Run
       e. Deploy of3-analysis-viewer Cloud Run
       f. Write OF3 env vars to .env
   |
3. Validate setup
   - Check NFS paths exist and have expected files
   - Check container images exist in Artifact Registry
   - Check Cloud Run services are healthy
   - Print summary table
```

### Makefile Targets

```makefile
# Full setup with model selection
setup:
    uv run python scripts/setup.py --models=$(MODELS)

# Individual setup steps (for debugging / partial re-runs)
setup-infra:           # Filestore, VPC, Artifact Registry, APIs
setup-containers:      # Build and push container images
    uv run python scripts/setup.py --models=$(MODELS) --step=containers
setup-data:            # Download databases and weights to NFS
    uv run python scripts/setup.py --models=$(MODELS) --step=data
setup-services:        # Deploy Cloud Run analysis + viewer services
    uv run python scripts/setup.py --models=$(MODELS) --step=services
setup-validate:        # Check everything is healthy
    uv run python scripts/setup.py --models=$(MODELS) --step=validate

# Per-model convenience targets
setup-af2:
    uv run python scripts/setup.py --models=af2
setup-of3:
    uv run python scripts/setup.py --models=of3
setup-boltz:
    uv run python scripts/setup.py --models=boltz
```

### scripts/setup.py (Replaces setup_data.py)

```python
"""Multi-model setup orchestrator.

Usage:
    python scripts/setup.py --models=af2,of3,boltz
    python scripts/setup.py --models=af2 --step=data
    python scripts/setup.py --models=all
"""

def main():
    args = parse_args()
    models = resolve_models(args.models)  # "all" -> ["af2", "of3", "boltz"]

    for model_id in models:
        manifest = load_manifest(model_id)  # imports models/<model>/setup.py

        if args.step in (None, "containers"):
            build_container(manifest)
        if args.step in (None, "data"):
            download_data(manifest)         # NFS weights + databases
        if args.step in (None, "services"):
            deploy_services(manifest)       # Cloud Run
        if args.step in (None, "validate"):
            validate(manifest)

    print_summary(models)
```

### Adding a New Model's Setup

When adding a 4th model, you just:
1. Create `models/<model>/setup.py` with the SETUP manifest
2. Add a Dockerfile in `src/<model>-components/`
3. Run `make setup MODELS=<model>`

No changes to the setup orchestrator needed.

### PR Mapping

- **PR 5** (models/af2/ move): Create `models/af2/setup.py` manifest from
  existing `setup_data.py` logic. `make setup MODELS=af2` replaces
  `python scripts/setup_data.py`.
- **PR 8** (OF3 container): Add `models/of3/setup.py` manifest.
- **PR 11** (Boltz container): Add `models/boltz/setup.py` manifest.
- **New PR between 7 and 8**: Create `scripts/setup.py` orchestrator and
  Makefile targets. Retrofit AF2 setup manifest.

---

## Testing Strategy

### Current Test Suite

```
tests/
  unit/
    test_agent.py           # Agent creation, model config, tool count
    test_config.py          # Config validation, env var loading
    test_skills.py          # Skill wrapper functions
    test_tool_registry.py   # Tool registry initialization
    test_pipeline.py        # Pipeline compilation, hardware configs, DB paths
    test_mmseqs2.py         # MMseqs2 GPU pipeline, conversion tool
  integration/
    test_conversation_flow.py   # Multi-turn agent conversations
    test_tool_routing.py        # Agent routes to correct tools
    test_response_quality.py    # Response content quality
```

### Testing Rules Per PR

Every PR must:
1. All existing tests pass BEFORE the PR (baseline)
2. All existing tests pass AFTER the PR (no regressions)
3. New code has new tests (listed below per PR)

### New Tests Per PR

**PR 1 (core/config.py):**
- `tests/unit/test_core_config.py`
  - CoreConfig loads shared env vars (project, region, bucket, filestore)
  - CoreConfig does NOT require ALPHAFOLD_COMPONENTS_IMAGE
  - AF2 Config subclass still passes existing test_config.py

**PR 2 (core/base_tool.py):**
- `tests/unit/test_base_tool.py`
  - BaseTool initializes with CoreConfig
  - GCS upload/download methods work
  - Vertex AI init called once (singleton)
  - gcs_console_url conversion
  - _get_filestore_info with and without env vars

**PR 3 (core/hardware.py):**
- `tests/unit/test_core_hardware.py`
  - check_gpu_quota returns quota dict
  - detect_supported_gpus filters zero-quota types
  - GPU ordering (L4, A100, A100_80GB)
  - Existing test_pipeline.py GPU tests still pass

**PR 4 (core/pipeline_utils.py):**
- `tests/unit/test_core_pipeline.py`
  - compile_pipeline produces valid JSON
  - NFS mount dict construction
  - Existing test_pipeline.py and test_mmseqs2.py pass unchanged

**PR 5 (models/af2/ move):**
- NO new test files -- existing tests pass through shims
- Update test imports from `foldrun_app.af2_lib.*` to `foldrun_app.models.af2.*`
  (can be done incrementally; shims keep old imports working)

**PR 6 (model registry):**
- `tests/unit/test_model_registry.py`
  - register_model / get_model / list_models
  - AF2 auto-registers on import
  - Unknown model raises error
  - Model capabilities queryable
- Update `test_agent.py`:
  - Agent has model-aware submit tools
  - Tool count reflects registered models

**PR 7 (cleanup shims):**
- Update ALL remaining test imports to `foldrun_app.models.af2.*`
- Delete any test code that referenced shim paths
- Full test suite green with no shims

**PR 9 (OF3 plugin):**
- `tests/unit/test_of3_config.py` -- OF3Config, env vars, weight paths
- `tests/unit/test_of3_pipeline.py` -- OF3 pipeline compiles, no relax step
- `tests/unit/test_of3_hardware.py` -- OF3 GPU tiers (A100 default, no relax)
- `tests/unit/test_input_converter.py` -- FASTA to OF3 JSON conversion
  - Monomer FASTA -> single protein chain JSON
  - Multimer FASTA -> multi-chain JSON
  - Edge cases: empty, invalid, very long sequences
- `tests/integration/test_of3_tool_routing.py` -- agent routes to OF3 tools

**PR 12 (Boltz plugin):**
- `tests/unit/test_boltz_config.py`
- `tests/unit/test_boltz_pipeline.py`
- `tests/unit/test_boltz_hardware.py`
- `tests/unit/test_input_converter.py` -- add FASTA to Boltz YAML tests
- `tests/integration/test_boltz_tool_routing.py`

**PR 14 (multi-model agent):**
- `tests/integration/test_multi_model.py`
  - Agent recommends correct model for protein-only vs ligand vs RNA
  - submit_comparison dispatches to multiple models
  - Model capabilities filtering works

### Test Directory Structure (target)

```
tests/
  unit/
    core/
      test_core_config.py
      test_base_tool.py
      test_core_hardware.py
      test_core_pipeline.py
      test_input_converter.py
      test_model_registry.py
    models/
      af2/
        test_af2_config.py       # renamed from test_config.py
        test_af2_pipeline.py     # renamed from test_pipeline.py
        test_af2_mmseqs2.py      # renamed from test_mmseqs2.py
        test_af2_hardware.py     # extracted from test_pipeline.py
      of3/
        test_of3_config.py
        test_of3_pipeline.py
        test_of3_hardware.py
      boltz/
        test_boltz_config.py
        test_boltz_pipeline.py
        test_boltz_hardware.py
    test_agent.py
    test_skills.py
    test_tool_registry.py
  integration/
    test_conversation_flow.py
    test_tool_routing.py
    test_response_quality.py
    test_of3_tool_routing.py
    test_boltz_tool_routing.py
    test_multi_model.py
```

### CI Gate

`make test` must pass before any PR merges. The Makefile already runs:
```
uv run pytest tests/unit && uv run pytest tests/integration
```

No changes to CI needed -- just add test files and they are picked up automatically.

---

## Documentation Updates

### Current Docs

- `applications/foldrun/README.md` -- top-level project overview, deployment
- `foldrun-agent/README.md` -- agent-specific setup and development
- `src/af2-analysis-job/README.md` -- analysis Cloud Run service
- `src/af2-analysis-viewer/README.md` -- viewer Cloud Run service

### Doc Updates Per PR

**PR 5 (models/af2/ move):**
- Update `foldrun-agent/README.md`:
  - New directory structure showing `core/` and `models/`
  - Updated import paths for contributors

**PR 6 (model registry):**
- Add section to `foldrun-agent/README.md`:
  - "Adding a New Model" guide (create models/<name>/, register, add setup manifest)
  - Model registry API reference

**PR 7 (cleanup):**
- Remove any references to `af2_lib/` from docs

**PR 8 (OF3 container):**
- `src/openfold3-components/README.md` -- build, test, push instructions

**PR 9 (OF3 plugin):**
- Update `applications/foldrun/README.md`:
  - Add OF3 to feature list and tech stack
  - Add OF3 to Quick Start (make setup MODELS=af2,of3)
  - Add OF3 input examples (protein+ligand JSON)
- Update `foldrun-agent/README.md`:
  - OF3 .env variables
  - OF3 tool descriptions

**PR 10 (OF3 Cloud Run):**
- `src/of3-analysis-job/README.md`
- `src/of3-analysis-viewer/README.md`

**PRs 11-13 (Boltz):** Same pattern as OF3.

**PR 14 (multi-model):**
- Update `applications/foldrun/README.md`:
  - Multi-model comparison feature
  - Model selection guidance table
  - Updated architecture diagram showing all three models
- Add `docs/model-comparison.md`:
  - When to use each model
  - Input/output format differences
  - Hardware requirements comparison

### Setup Guide Updates

The setup sections in README.md must reflect the new `make setup MODELS=...`
flow. Currently the README shows:

```bash
./deploy-all.sh YOUR_PROJECT_ID
```

After PR 9+, it should show:

```bash
# Deploy with AF2 only (current default)
make setup MODELS=af2

# Deploy with AF2 + OpenFold3
make setup MODELS=af2,of3

# Deploy everything
make setup MODELS=all
```

---

## Cloud Run Services Strategy

### Current AF2 Services

Two Cloud Run services handle post-prediction work:

**af2-analysis-job** (Cloud Run Job, `src/af2-analysis-job/main.py`):
- Runs as a parallel Cloud Run Job (one task per prediction)
- Downloads AF2 raw prediction pickle from GCS
- Extracts pLDDT scores, PAE matrix from pickle
- Generates pLDDT per-residue plot and PAE heatmap (matplotlib)
- Calls Gemini for expert biological analysis (multimodal: text + plots)
- Writes per-prediction JSON + consolidated summary.json to GCS

**af2-analysis-viewer** (Cloud Run Service, `src/af2-analysis-viewer/app.py`):
- Flask web app with 3D structure viewer (Mol* or 3Dmol.js)
- Loads PDB files from GCS via `/api/pdb` endpoint
- Loads summary.json from GCS via `/api/analysis` endpoint
- Combined view: 3D structure + pLDDT plot + PAE heatmap + expert analysis
- Routes: `/job/<job_id>`, `/structure`, `/analysis`, `/combined`

### What's AF2-Specific vs Reusable

| Component | AF2 | OF3 / Boltz | Reusable? |
|-----------|-----|-------------|-----------|
| GCS download/upload helpers | Yes | Same | Yes |
| Pickle loading | AF2 pickle format | Different output format | No |
| pLDDT extraction | `raw_prediction['plddt']` | CIF confidence fields | No |
| PAE extraction | `raw_prediction['predicted_aligned_error']` | CIF/JSON PAE format | No |
| pLDDT plot generation | matplotlib, same chart | Same chart, same format | Yes |
| PAE heatmap | matplotlib, same chart | Same chart | Yes |
| Gemini expert analysis | AF2-specific prompt | Model-specific prompt | Partially |
| Consolidation logic | Sort by pLDDT, re-rank | Same pattern | Yes |
| Flask viewer app | PDB loading, Mol* | CIF loading, Mol* | Partially |
| `/api/pdb` endpoint | PDB content-type | CIF content-type | Adapt |
| 3D viewer template | PDB + pLDDT coloring | CIF + multi-molecule | No |
| Affinity display | N/A | Boltz only | No |

### Strategy: Shared Library + Per-Model Services

Don't copy-paste 870 lines of main.py three times. Extract shared code into a
library, then each model's service imports it and adds model-specific logic.

```
src/
  foldrun-analysis-lib/           # Shared Python package (NEW)
    __init__.py
    gcs.py                        # download/upload helpers
    plots.py                      # pLDDT plot, PAE heatmap (matplotlib)
    gemini.py                     # Gemini expert analysis (parameterized prompt)
    consolidation.py              # Sort, re-rank, build summary.json
    viewer/
      base_app.py                 # Flask app factory with shared routes
      templates/
        base.html                 # Shared layout
        components/               # Shared Jinja2 components (plots panel, etc.)

  af2-analysis-job/               # AF2-specific (EXISTING, refactored)
    Dockerfile
    main.py                       # Imports from foldrun-analysis-lib
                                  # AF2-specific: pickle parsing, pLDDT/PAE extraction

  af2-analysis-viewer/            # AF2-specific (EXISTING, refactored)
    Dockerfile
    app.py                        # Imports base_app, adds PDB endpoints
    templates/
      combined.html               # AF2-specific: PDB viewer, relax vs unrelaxed toggle

  of3-analysis-job/               # OF3-specific (NEW)
    Dockerfile
    main.py                       # Imports from foldrun-analysis-lib
                                  # OF3-specific: CIF parsing, ipTM extraction,
                                  # chain-pair PAE, diffusion sample handling

  of3-analysis-viewer/            # OF3-specific (NEW)
    Dockerfile
    app.py                        # Imports base_app, adds CIF endpoints
    templates/
      combined.html               # OF3-specific: CIF viewer, multi-molecule display,
                                  # RNA/DNA coloring, ligand rendering

  boltz-analysis-job/             # Boltz-specific (NEW)
    Dockerfile
    main.py                       # Imports from foldrun-analysis-lib
                                  # Boltz-specific: CIF parsing, affinity score extraction,
                                  # binding probability, delta-G display

  boltz-analysis-viewer/          # Boltz-specific (NEW)
    Dockerfile
    app.py                        # Imports base_app, adds CIF + affinity endpoints
    templates/
      combined.html               # Boltz-specific: CIF viewer, affinity heatmap,
                                  # pocket view, binder/decoy classification
```

### Shared Library: foldrun-analysis-lib

**gcs.py** (extracted from current main.py):
```python
def download_from_gcs(gcs_uri, local_path): ...
def upload_to_gcs(local_path, gcs_uri): ...
def download_json_from_gcs(gcs_uri) -> dict: ...
def download_image_from_gcs(gcs_uri) -> bytes: ...
```

**plots.py** (extracted from current main.py):
```python
def plot_plddt(scores, model_name, output_path): ...
def plot_pae(pae_matrix, model_name, output_path, max_pae=31.0): ...
# Future: plot_affinity(), plot_iptm() for Boltz/OF3
```

**gemini.py** (parameterized version of current generate_gemini_expert_analysis):
```python
def generate_expert_analysis(
    summary_data: dict,
    model_type: str,          # "alphafold2" | "openfold3" | "boltz"
    prompt_template: str,     # Model-specific prompt
    plot_uris: list = None,   # Plot images to include
) -> dict: ...
```

Each model provides its own prompt template. AF2 prompt talks about model
variants and AMBER relaxation. OF3 prompt talks about diffusion seeds and
multi-molecule prediction. Boltz prompt includes affinity interpretation.

**consolidation.py** (extracted from current consolidate_results):
```python
def consolidate_analyses(
    analyses: list[dict],
    job_id: str,
    analysis_path: str,
    rank_key: str = "plddt_mean",   # Primary ranking metric
    extra_metrics: list = None,      # Model-specific metrics to include
) -> dict: ...
```

**viewer/base_app.py** (Flask app factory):
```python
def create_viewer_app(
    model_type: str,
    structure_format: str = "pdb",   # "pdb" or "cif"
    extra_routes: callable = None,   # Model-specific route registrar
) -> Flask: ...
```

Shared routes: `/health`, `/api/analysis`, `/api/image`, `/job/<job_id>`.
Model-specific routes: `/api/pdb` (AF2), `/api/cif` (OF3/Boltz),
`/api/affinity` (Boltz only).

### Per-Model Analysis Jobs: What Differs

**AF2 (existing):**
- Input: AF2 raw prediction pickle (`.pkl`)
- Metrics: pLDDT, PAE, ranking_confidence
- Plots: pLDDT per-residue, PAE heatmap
- Gemini prompt: AF2-specific (model variants, relaxation, template quality)

**OF3 (new):**
- Input: OF3 output directory (CIF files, confidence JSON)
- Metrics: pLDDT, ipTM, chain-pair PAE, ptm
- Plots: pLDDT per-residue, PAE heatmap, ipTM matrix (for complexes)
- Gemini prompt: OF3-specific (diffusion samples, multi-molecule, RNA/DNA)
- Extra: Parse CIF for chain-level metrics, ligand contacts

**Boltz (new):**
- Input: Boltz output directory (CIF files, confidence JSON, affinity JSON)
- Metrics: pLDDT, ipTM, affinity_pred_value, affinity_probability_binary
- Plots: pLDDT, PAE, affinity landscape, pocket contact map
- Gemini prompt: Boltz-specific (affinity interpretation, binder classification,
  comparison to FEP, drug discovery context)
- Extra: Parse affinity scores, binding probability thresholds

### Per-Model Viewers: What Differs

**AF2 (existing):**
- Structure format: PDB
- Viewer features: pLDDT confidence coloring, relaxed vs unrelaxed toggle
- Analysis panel: pLDDT distribution chart, PAE heatmap, model comparison table

**OF3 (new):**
- Structure format: CIF
- Viewer features: Multi-molecule display (protein + ligand + RNA/DNA),
  chain-specific coloring, pLDDT confidence coloring
- Analysis panel: pLDDT distribution, PAE heatmap, ipTM matrix,
  per-chain confidence breakdown, seed comparison

**Boltz (new):**
- Structure format: CIF
- Viewer features: Protein-ligand complex, pocket surface rendering,
  binding site highlight, pLDDT coloring
- Analysis panel: pLDDT, PAE, affinity prediction display (log10 IC50),
  binding probability score, pocket contacts table,
  binder/decoy classification badge

### PR Mapping for Cloud Run

**PR 5 (models/af2/ move):** No Cloud Run changes yet. Services stay in src/ as-is.

**PR 10 (OF3 Cloud Run — expanded):**
1. Create `src/foldrun-analysis-lib/` — extract shared code from af2-analysis-job
2. Refactor `src/af2-analysis-job/main.py` to import from shared lib
3. Refactor `src/af2-analysis-viewer/app.py` to import from shared lib
4. Verify AF2 services still work (redeploy, test with existing job)
5. Create `src/of3-analysis-job/` using shared lib + OF3-specific parsing
6. Create `src/of3-analysis-viewer/` using shared lib + CIF viewer template
7. Deploy OF3 services

**PR 13 (Boltz Cloud Run):**
1. Create `src/boltz-analysis-job/` using shared lib + affinity parsing
2. Create `src/boltz-analysis-viewer/` using shared lib + affinity display
3. Deploy Boltz services

### Testing Cloud Run Services

Each Cloud Run service gets:
- `test_main.py` (analysis job): Mock GCS, test metric extraction, test plot generation
- `test_app.py` (viewer): Flask test client, test API endpoints, test error handling
- Integration test: Deploy to staging, submit a real prediction, verify end-to-end

Add to Makefile:
```makefile
test-services:
    cd src/af2-analysis-job && pytest test_main.py
    cd src/af2-analysis-viewer && pytest test_app.py
    cd src/of3-analysis-job && pytest test_main.py
    cd src/of3-analysis-viewer && pytest test_app.py
    cd src/boltz-analysis-job && pytest test_main.py
    cd src/boltz-analysis-viewer && pytest test_app.py
```

---

## Deployment & CI/CD Updates

### Files That Need Multi-Model Awareness

The following files are currently AF2-hardcoded and must be updated:

### cloudbuild.yaml

Currently has 5 sequential steps, all AF2-specific. Needs to become
model-aware — build/deploy only the models that are configured.

**Target structure:**
```yaml
steps:
  # Shared infra (always)
  - id: 'build-shared'
    ...

  # Per-model steps (conditional on _MODELS substitution)
  # AF2
  - id: 'build-af2-viewer'
    ...
  - id: 'build-af2-analysis-job'
    ...
  - id: 'build-af2-components'
    ...

  # OF3 (only if "of3" in _MODELS)
  - id: 'build-of3-viewer'
    ...
  - id: 'build-of3-analysis-job'
    ...
  - id: 'build-of3-components'
    ...

  # Boltz (only if "boltz" in _MODELS)
  - id: 'build-boltz-viewer'
    ...
  - id: 'build-boltz-analysis-job'
    ...
  - id: 'build-boltz-components'
    ...

  # Agent Engine (always — single agent, receives all model env vars)
  - id: 'deploy-agent-engine'
    ...
    # Passes env vars for ALL installed models:
    # ALPHAFOLD_COMPONENTS_IMAGE, AF2_VIEWER_URL,
    # OPENFOLD3_COMPONENTS_IMAGE, OF3_VIEWER_URL,
    # BOLTZ_COMPONENTS_IMAGE, BOLTZ_VIEWER_URL, etc.

substitutions:
  _MODELS: af2              # "af2", "af2,of3", "af2,of3,boltz", "all"
```

**Alternative:** Split into per-model Cloud Build configs:
```
cloudbuild-af2.yaml
cloudbuild-of3.yaml
cloudbuild-boltz.yaml
cloudbuild-agent.yaml       # Agent Engine deploy (always last)
```
And orchestrate from `deploy-all.sh` which calls them in sequence.
This is simpler than conditional steps in a single file.

### deploy-all.sh

Currently AF2-only. Needs `--models` flag:
```bash
./deploy-all.sh PROJECT_ID --models af2,of3
./deploy-all.sh PROJECT_ID --models all
./deploy-all.sh PROJECT_ID --steps build --models of3    # Only build OF3
```

The `--steps` flag stays (infra, build, data, convert). The `--models` flag
filters which models are included in each step.

### check-status.sh

Currently checks: APIs, Terraform, Artifact Registry, Cloud Run services (AF2
only), NFS databases, Agent Engine. Needs to check per-model:
- Container images in Artifact Registry (per model)
- Cloud Run services (per model: viewer + analysis job)
- NFS data (per model: weights, databases)
- Show which models are installed vs not installed

### terraform/main.tf

Infrastructure is mostly shared (VPC, Filestore, Artifact Registry, APIs).
Changes needed:
- No new resources required for OF3/Boltz (they use the same Filestore, VPC,
  and Artifact Registry)
- May need additional IAM bindings if OF3/Boltz Cloud Run services need
  different permissions
- Cloud Run service accounts may need model-specific secrets

### agent_engine_app.py

No code changes needed. Single agent, single deployment. The agent already
handles multiple models through the tool registry. The only change is that
the deploy step must pass env vars for ALL installed models to Agent Engine.

### PR Mapping for Deployment Updates

These changes slot into existing PRs:

- **PR 5** (models/af2/ move): No deployment changes yet
- **PR 9** (OF3 plugin): Update `cloudbuild.yaml` or add `cloudbuild-of3.yaml`.
  Update `deploy-all.sh` with `--models` flag. Update `check-status.sh`.
- **PR 12** (Boltz plugin): Add `cloudbuild-boltz.yaml`. Same deploy-all.sh
  and check-status.sh patterns.
- **PR 14** (multi-model): Final pass on deploy scripts, ensure `--models all`
  works end-to-end.

---

## Agent Tools: Model-Specific Routing Details

### Tools That Are Already Model-Agnostic (no changes needed)

These tools operate on Vertex AI pipeline metadata, not model-specific outputs:

| Tool | Why it's model-agnostic |
|------|----------------------|
| `check_job_status` | Reads pipeline state from Vertex AI API |
| `list_jobs` | Lists pipeline jobs, filters by labels |
| `get_job_details` | Reads pipeline metadata, parameters, task configs |
| `delete_job` | Deletes pipeline job from Vertex AI |
| `check_gpu_quota` | Queries Compute Engine quota API |
| `cleanup_gcs_files` | Deletes GCS paths (no format awareness) |
| `find_orphaned_gcs_files` | Compares GCS paths with Vertex AI jobs |

These move to `core/` or stay shared — no per-model implementations needed.

### Tools That Need Per-Model Implementations

| Tool | AF2 behavior | What changes for OF3/Boltz |
|------|-------------|--------------------------|
| `submit_monomer_prediction` | FASTA -> AF2 pipeline | FASTA -> JSON/YAML -> OF3/Boltz pipeline |
| `submit_multimer_prediction` | FASTA -> AF2 pipeline | FASTA -> JSON/YAML -> OF3/Boltz pipeline |
| `submit_batch_predictions` | Multiple AF2 submissions | Routes to correct model per batch item |
| `get_prediction_results` | Finds PDB files in GCS | Finds CIF files, different dir structure |
| `analyze_prediction_quality` | Loads AF2 pickle, extracts pLDDT/PAE | Loads CIF/JSON, extracts model-specific metrics |
| `analyze_job_parallel` | Launches AF2 Cloud Run analysis job | Launches model-specific Cloud Run job |
| `get_analysis_results` | Reads AF2 summary.json format | Reads model-specific summary format |
| `analyze_job` | AF2-specific log parsing, task names | Model-specific task name patterns |
| `open_structure_viewer` | Opens AF2 viewer URL | Opens model-specific viewer URL |

Each of these gets a model-specific implementation in `models/<model>/tools.py`
and the skills/ wrappers route to the correct one based on the `model` parameter
or job labels (for post-submission tools that look up existing jobs).

### AlphaFold DB Tools — Stay AF2-Only

`query_alphafold_db_prediction`, `query_alphafold_db_summary`,
`query_alphafold_db_annotations` query the public EMBL-EBI AlphaFold Database.
This database only contains AlphaFold2 predictions — there is no equivalent
public database for OF3 or Boltz predictions.

These tools stay in `models/af2/` as AF2-specific tools. The agent instructions
should note: "Use AlphaFold DB tools to check if a structure already exists
before running any model. If found, the user can skip prediction entirely."

### How Post-Submission Tools Know Which Model

When a job is submitted, the pipeline labels include `model_type`:
```python
labels = {
    'model_type': 'alphafold2',  # or 'openfold3', 'boltz'
    'job_type': 'monomer',
    ...
}
```

Post-submission tools (`get_results`, `analyze`, `open_viewer`) read the
`model_type` label from the Vertex AI job to route to the correct
model-specific implementation. This means you can run `analyze_job(job_id)`
without specifying the model — it auto-detects.

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Phase 1-2 refactor breaks AF2 | Comprehensive tests before refactoring; AF2 is the reference |
| OF3 container image is large (~15-20GB) | Pre-pull to Artifact Registry, use persistent disk for weights on NFS |
| ColabFold rate limits | N/A — all MSA runs locally on NFS databases, no external servers |
| GPU memory for large complexes | A100 80GB tier; benchmark each model's memory profile |
| CIF vs PDB in downstream tools | Per-model viewer/analysis handles its own format |
| NFS reorganization (moving AF2 DBs into af2/ subdir) | Symlink old paths during transition; update env vars |
| cloudbuild.yaml complexity with 3 models | Split into per-model Cloud Build configs |
| Post-submission tools need model detection | Pipeline labels include model_type; auto-routing |
| Agent Engine env vars grow with each model | Manageable — ~5 env vars per model |

---

## Automated Refactor: Sonnet Implementation Guide

This section provides step-by-step instructions for an AI agent (Claude Sonnet)
to execute PRs 1-7 overnight on the `multi-model-refactor` branch.

### Ground Rules

1. **Test after every step.** Run `cd foldrun-agent && uv run pytest tests/unit && uv run pytest tests/integration` after each commit. If tests fail, fix before moving on. NEVER proceed with failing tests.
2. **Smoke test after every step.** Run `cd foldrun-agent && uv run python -c "from foldrun_app.agent import create_alphafold_agent"` to verify the full import chain works. If this fails, the agent won't boot — fix it.
3. **Do not change behavior.** This is a refactor. AF2 must work identically before and after. No new features, no API changes, no "improvements."
4. **Preserve every file's license header.** All new files get the same Apache 2.0 header as existing files.
5. **One commit per step.** Commit message format: `refactor: step N — description`
6. **Read before writing.** Always read the full file before modifying. Don't guess at contents.
7. **No circular imports.** `core/` must NOT import from `af2_lib/` or `models/`. `models/af2/` imports from `core/`. `skills/` imports from both.

### Step 1: Create core/config.py

**Read:** `foldrun_app/af2_lib/config.py`

**Create:** `foldrun_app/core/__init__.py` (empty)

**Create:** `foldrun_app/core/config.py` — extract model-agnostic config:
- Copy the `Config` class, rename to `CoreConfig`
- Keep properties: `project_id`, `region`, `zone`, `bucket_name`,
  `databases_bucket_name`, `filestore_id`, `filestore_ip`,
  `filestore_network`, `nfs_share`, `nfs_mount_point`,
  `dws_max_wait_hours`, `supported_gpus`, `set_supported_gpus`, `to_dict`
- Remove properties: `base_image`, `viewer_url`, `parallelism`
- Remove `ALPHAFOLD_COMPONENTS_IMAGE` from `_validate()` required checks
- Keep `_validate()` but only check shared vars (project, region, bucket, filestore)

**Modify:** `foldrun_app/af2_lib/config.py` — make it a thin subclass:
- `from foldrun_app.core.config import CoreConfig`
- `class Config(CoreConfig):` — adds `base_image`, `viewer_url`, `parallelism`
- Add `ALPHAFOLD_COMPONENTS_IMAGE` back to validation in this subclass
- All existing behavior preserved

**Test:** `uv run pytest tests/unit/test_config.py` + full suite + smoke test

**Commit:** `refactor: step 1 — extract core/config.py from af2_lib/config.py`

### Step 2: Create core/base_tool.py

**Read:** `foldrun_app/af2_lib/af2_tool.py`

**Create:** `foldrun_app/core/base_tool.py`:
- Copy `_ensure_clients()` function and `AF2Tool` class, rename class to `BaseTool`
- `BaseTool.__init__` takes `CoreConfig` (import from `core.config`)
- Keep in BaseTool: `_ensure_clients`, `__init__`, `run` (abstract),
  `gcs_console_url`, `_upload_to_gcs`, `_download_from_gcs`,
  `_get_filestore_info`, `_clean_label`
- Remove from BaseTool: `_recommend_gpu`, `_get_hardware_config`,
  `_setup_compile_env` (these stay in AF2Tool)

**Modify:** `foldrun_app/af2_lib/af2_tool.py`:
- `from foldrun_app.core.base_tool import BaseTool`
- `class AF2Tool(BaseTool):` — keeps `_recommend_gpu`,
  `_get_hardware_config`, `_setup_compile_env`
- Remove duplicated methods that now live in BaseTool
- AF2Tool.__init__ calls `super().__init__(tool_config, config)`

**Test:** Full suite + smoke test

**Commit:** `refactor: step 2 — extract core/base_tool.py from af2_lib/af2_tool.py`

### Step 3: Create core/hardware.py

**Read:** `foldrun_app/af2_lib/tools/check_gpu_quota.py`,
         `foldrun_app/af2_lib/startup.py`

**Create:** `foldrun_app/core/hardware.py`:
- Extract `check_gpu_quota(project_id, region)` as a pure function
  (queries Compute Engine API, returns quota dict)
- Extract `detect_supported_gpus(project_id, region)` — returns ordered list
  of GPU types with quota > 0
- These are model-agnostic — they just query GCE quotas

**Modify:** `foldrun_app/af2_lib/tools/check_gpu_quota.py`:
- `AF2CheckGPUQuotaTool.run()` calls `core.hardware.check_gpu_quota()`
  and formats the response (instead of inline API calls)

**Modify:** `foldrun_app/af2_lib/startup.py`:
- `_auto_detect_gpus()` calls `core.hardware.detect_supported_gpus()`

**Test:** Full suite + smoke test

**Commit:** `refactor: step 3 — extract core/hardware.py`

### Step 4: Create core/pipeline_utils.py

**Read:** `foldrun_app/af2_lib/utils/pipeline_utils.py`

**Create:** `foldrun_app/core/pipeline_utils.py`:
- Move `compile_pipeline()` function (model-agnostic KFP compilation)
- Keep it simple — just the compiler wrapper

**Modify:** `foldrun_app/af2_lib/utils/pipeline_utils.py`:
- Import and re-export `compile_pipeline` from `core.pipeline_utils`
- Keep `load_vertex_pipeline` and `get_pipeline_parameters` here
  (these are AF2-specific)

**Test:** Full suite + smoke test

**Commit:** `refactor: step 4 — extract core/pipeline_utils.py`

### Step 5: Move AF2 into models/af2/

This is the largest step. Create the directory structure and move files,
leaving shims at the old paths.

**Create directories:**
```
foldrun_app/models/__init__.py
foldrun_app/models/af2/__init__.py
foldrun_app/models/af2/tools/
foldrun_app/models/af2/utils/
foldrun_app/models/af2/pipeline/
foldrun_app/models/af2/pipeline/components/
foldrun_app/models/af2/pipeline/pipelines/
foldrun_app/models/af2/data/
```

**Move files** (copy to new location, replace old file with re-export shim):

| From | To |
|------|-----|
| `af2_lib/af2_tool.py` | `models/af2/base.py` |
| `af2_lib/config.py` | `models/af2/config.py` |
| `af2_lib/startup.py` | `models/af2/startup.py` |
| `af2_lib/tools/*.py` | `models/af2/tools/*.py` |
| `af2_lib/tools/__init__.py` | `models/af2/tools/__init__.py` |
| `af2_lib/utils/*.py` | `models/af2/utils/*.py` |
| `af2_lib/data/alphafold_tools.json` | `models/af2/data/alphafold_tools.json` |
| `af2_lib/vertex_pipeline/` | `models/af2/pipeline/` |

**Shim pattern** — each old file becomes a re-export:
```python
# foldrun_app/af2_lib/config.py (shim)
from foldrun_app.models.af2.config import Config  # noqa: F401
```

**Do this for every moved file.** The shim ensures all existing imports
(in tests, scripts, agent.py, skills/) continue to work unchanged.

**Update internal imports within moved files:**
- Files in `models/af2/tools/` that import `from ..af2_tool import AF2Tool`
  become `from ..base import AF2Tool`
- Files in `models/af2/tools/` that import `from ..utils.X import Y`
  become `from foldrun_app.models.af2.utils.X import Y`
- Files in `models/af2/pipeline/` update similarly

**Create `models/af2/__init__.py`:**
```python
MODEL_ID = "alphafold2"
DISPLAY_NAME = "AlphaFold2"
CAPABILITIES = ["protein"]
INPUT_FORMAT = "fasta"
OUTPUT_FORMAT = "pdb"
```

**CRITICAL:** Do NOT update imports in `agent.py`, `skills/`, tests, or
scripts yet. They all use the shims. That happens in step 7.

**Test:** Full suite + smoke test. Every test must pass through shims.

**Commit:** `refactor: step 5 — move AF2 code to models/af2/ with shims`

### Step 6: Model registry + routing

**Create:** `foldrun_app/core/model_registry.py`:
```python
"""Model plugin registry."""
import logging

logger = logging.getLogger(__name__)
_MODELS = {}


def register_model(model_id: str, model_module):
    """Register a model plugin."""
    _MODELS[model_id] = model_module
    logger.info(f"Registered model: {model_id}")


def get_model(model_id: str):
    """Get a registered model plugin."""
    if model_id not in _MODELS:
        available = ", ".join(_MODELS.keys()) or "(none)"
        raise ValueError(
            f"Unknown model '{model_id}'. Available: {available}"
        )
    return _MODELS[model_id]


def list_models() -> list:
    """List registered model IDs."""
    return list(_MODELS.keys())
```

**Modify:** `foldrun_app/models/af2/__init__.py` — add registration:
```python
from foldrun_app.core.model_registry import register_model

MODEL_ID = "alphafold2"
DISPLAY_NAME = "AlphaFold2"
CAPABILITIES = ["protein"]
INPUT_FORMAT = "fasta"
OUTPUT_FORMAT = "pdb"

register_model(MODEL_ID, __import__(__name__))
```

**Modify:** `foldrun_app/skills/_tool_registry.py`:
- Add `import foldrun_app.models.af2` at top (triggers registration)
- Keep existing tool loading logic for now (can be cleaned up later)

**Add `model_type` label** to both submit tools:
- `models/af2/tools/submit_monomer.py`: add `'model_type': 'alphafold2'` to labels dict
- `models/af2/tools/submit_multimer.py`: add `'model_type': 'alphafold2'` to labels dict

**Test:** Full suite + smoke test.

**Commit:** `refactor: step 6 — add model registry, register AF2, add model_type labels`

### Step 7: Remove shims, update all imports

**Update imports** in these files to point to `models.af2.*` or `core.*`:

- `foldrun_app/agent.py` — update `from foldrun_app.af2_lib.startup` to `from foldrun_app.models.af2.startup`
- `foldrun_app/skills/_tool_registry.py` — update all `from foldrun_app.af2_lib.*` imports
- `tests/unit/test_config.py`
- `tests/unit/test_agent.py`
- `tests/unit/test_pipeline.py`
- `tests/unit/test_mmseqs2.py`
- `tests/unit/test_tool_registry.py`
- `tests/conftest.py`
- `scripts/setup_data.py`

**Delete shim files** — remove all files in `af2_lib/` that are now just
re-exports. Keep `af2_lib/__init__.py` only if needed, otherwise delete
the entire `af2_lib/` directory.

**Test:** Full suite + smoke test. This is the final validation.

**Commit:** `refactor: step 7 — remove af2_lib/ shims, update all imports`

### Final Validation

After all 7 steps, run:
```bash
cd foldrun-agent
uv run pytest tests/unit -v
uv run pytest tests/integration -v
uv run python -c "from foldrun_app.agent import create_alphafold_agent; a = create_alphafold_agent(); print(f'Agent created with {len(a.tools)} tools')"
uv run python -c "from foldrun_app.core.model_registry import list_models; print(f'Registered models: {list_models()}')"
```

All must pass. The agent should have the same number of tools as before.
`list_models()` should return `['alphafold2']`.

---

## Key Design Decisions

1. **Plugin pattern over monolith** -- each model is self-contained, can be developed and tested independently
2. **Separate Cloud Run per model** -- independent deployment, debugging, and evolution; scales to zero
3. **NFS for everything** -- weights, databases, CCD all on shared Filestore; containers stay lean
4. **FASTA as universal input** -- agent accepts FASTA for protein jobs, converts to model-native format internally
5. **Shared job management** -- Vertex AI pipeline operations are model-agnostic; one set of tools for all models
6. **No premature abstraction** -- models register capabilities but each implements its own pipeline; no forced interface beyond BaseTool

---

## Future: GKE + KFP Backend Support

The architecture supports deploying to GKE with self-hosted KFP in addition to
Vertex AI Pipelines. This is NOT built in PRs 1-14 — add it when there is a
real GKE deployment to test against.

### Current Vertex-Specific Coupling (3 layers)

**Layer 1 — Pipeline DSL: Already portable.**
`@dsl.component` and `@dsl.pipeline` are standard KFP SDK. They work
unchanged on both Vertex AI Pipelines and KFP on GKE.

**Layer 2 — GPU/NFS Provisioning: Vertex-specific.**
`create_custom_training_job_from_component` (from `google_cloud_pipeline_components`)
wraps components with Vertex-specific GPU, NFS mount, and DWS scheduling.
On GKE, these become Kubernetes-native constructs.

**Layer 3 — Job Submission & Management: Vertex-specific.**
`vertex_ai.PipelineJob.submit()`, `PipelineServiceClient.list_pipeline_jobs()`,
etc. On GKE, these become `kfp.Client()` calls.

### Mapping: Vertex vs GKE

| Concern | Vertex AI Pipelines | KFP on GKE |
|---------|-------------------|------------|
| GPU provisioning | `create_custom_training_job_from_component` | `node_selector` + `tolerations` + `resource_limits` |
| NFS mount | `nfs_mounts=[{server, path, mountPoint}]` | PVC or `V1Volume(nfs={server, path})` |
| Spot/preemptible | DWS FLEX_START strategy | Spot node pools + Kueue |
| Job submission | `vertex_ai.PipelineJob.submit()` | `kfp.Client().create_run_from_pipeline_func()` |
| Job listing | `aiplatform_v1.PipelineServiceClient` | `kfp.Client().list_runs()` |
| Container image | Artifact Registry | Same (Artifact Registry or any registry) |
| Network | VPC for NFS access | GKE cluster already in VPC |
| Auth | IAM service account | Workload Identity |
| Console URL | Vertex AI Pipelines console | KFP dashboard URL |

### Backend Abstraction (PR 15, future)

When GKE support is needed, add `core/backends/`:

```
core/
  backends/
    __init__.py             # get_backend() -> PipelineBackend
    base.py                 # PipelineBackend ABC
    vertex.py               # VertexBackend (current behavior, extracted)
    gke.py                  # GKEBackend (new)
```

The interface is small — 4 operations:

```python
class PipelineBackend(ABC):
    def wrap_component(self, component, gpu_config, nfs_config):
        """Wrap a KFP component with GPU/NFS provisioning.

        Vertex: create_custom_training_job_from_component(...)
        GKE:    add node_selector, tolerations, PVC to component
        """

    def submit(self, pipeline_func, params, labels) -> JobHandle:
        """Submit a compiled pipeline.

        Vertex: PipelineJob(**kwargs).submit()
        GKE:    kfp.Client().create_run_from_pipeline_func()
        """

    def list_jobs(self, filters) -> list:
        """List pipeline runs.

        Vertex: PipelineServiceClient.list_pipeline_jobs()
        GKE:    kfp.Client().list_runs()
        """

    def get_job(self, job_id) -> JobInfo:
        """Get job status/details.

        Vertex: PipelineServiceClient.get_pipeline_job()
        GKE:    kfp.Client().get_run()
        """
```

**Selection via .env:**
```bash
PIPELINE_BACKEND=vertex        # or "gke"
KFP_HOST=https://kfp.example.com  # only for GKE backend
```

### Why Not Build It Now

- The plugin pattern (models/) already isolates model logic from infrastructure.
  The backend abstraction is a second axis of isolation that can be added later
  without touching any model code.
- Building a GKE backend without a real GKE cluster to test against means
  guessing at the interface. Better to wait until there is a concrete deployment.
- PRs 1-14 keep Vertex hardcoded. This is fine — extracting to the backend
  pattern later is a clean refactor (move existing Vertex code into
  `backends/vertex.py`, then add `backends/gke.py`).

### Updated PR Dependency Graph (with GKE)

```
PRs 1-7 (AF2 refactor)
  |
  +--- PRs 8-10 (OF3)
  +--- PRs 11-13 (Boltz)
  +--- PR 14 (multi-model agent)
  |
PR 15 (backend abstraction) -- when GKE deployment is needed
  |
PR 16 (GKE backend) -- implements GKEBackend against real cluster
```
