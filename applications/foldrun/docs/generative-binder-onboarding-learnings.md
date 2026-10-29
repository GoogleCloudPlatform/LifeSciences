# Generative Binder Models Onboarding: Architecture & Technical Learnings

This document summarizes the architectural design, runtime edge cases, infrastructure fixes, and verification workflows established while onboarding three generative protein/binder design models—**BoltzGen**, **RFD4-Proteina**, and **BindCraft (`FreeBindCraft`)**—into the FoldRun platform.

---

## 1. Architectural Patterns

### 1.1 Two-Stage Vertex AI Pipeline Topology (`ConfigureSeeds -> ParallelFor[PredictOp]`)
Unlike structure prediction models (`AlphaFold2`, `OpenFold3`, `Boltz-2`) that require CPU-heavy MSA search stages before inference, generative binder design models operate directly on a target structure (`.cif`/`.pdb`) and hotspot/contig specifications:
- **`ConfigureSeeds` (CPU)**: Normalizes seed configurations (`random_seed`, `num_seeds` / `num_designs`) and emits a JSON array of seed objects (`[{"seed": 42, "design_idx": 0}, ...]`).
- **`ParallelFor[PredictOp]` (GPU)**: Fans out independent design trajectories across `NVIDIA_TESLA_A100` (40 GB) or `NVIDIA_A100_80GB` (80 GB) GPUs (with optional `NVIDIA_L4` override for `RFD4-Proteina`), mounting the shared Cloud Filestore NFS volume (`/mnt/nfs/foldrun`) for cached model weights.

### 1.2 Shared Target Resolution (`target_resolver.py`)
Generative binder pipelines accept diverse target inputs (`gs://...` URIs, local paths, inline PDB/mmCIF strings, or 4-character RCSB PDB IDs such as `4Z18`).
- `foldrun_app/core/target_resolver.py` centralizes target resolution:
  1. Fetches missing RCSB PDB structures (`https://files.rcsb.org/download/<id>.cif` / `.pdb`).
  2. Converts `.cif` targets to `.pdb` via BioPython `MMCIFParser` + `PDBIO` for tools that require PDB input (`BindCraft`).
  3. Extracts chain IDs, residue numbering (`auth_seq_id` vs. 1-indexed sequential `label_seq_id`), and sequence lengths to validate hotspot specifications before submitting Vertex AI Pipeline jobs.

### 1.3 Thread-Safe KFP Compilation (`GLOBAL_COMPILE_LOCK`)
The KFP compiler (`kfp.compiler.Compiler().compile()`) mutates global component/pipeline registries during YAML compilation. When the ADK agent invokes multiple submission tools concurrently in the same reasoning turn, concurrent `Compiler().compile()` calls corrupt each other's component definitions.
- Guarding all `Compiler().compile()` invocations with `with GLOBAL_COMPILE_LOCK:` eliminates race conditions during parallel tool execution.

---

## 2. Model-Specific Runtime & Container Learnings

### 2.1 BoltzGen (`boltzgen`)
1. **CLI Flag Contract**:
   - `boltzgen run` requires `--num_designs <int>` and `--budget <int>` (where `budget <= num_designs`).
   - Passing `--seed` to `boltzgen run` fails because `--seed` is not a valid CLI argument in the upstream `boltzgen` CLI.
2. **Relative Target Paths in `spec.yaml`**:
   - `boltzgen` resolves `file: <path>` entries in `spec.yaml` relative to the directory containing `spec.yaml`.
   - Writing an absolute path (e.g., `/tmp/boltzgen_work/input.cif`) inside `spec.yaml` causes `boltzgen` to concatenate the working directory twice (`/tmp/boltzgen_work/tmp/boltzgen_work/input.cif`). Staging the target file as `input.cif` in the same directory as `spec.yaml` and referencing `path: input.cif` resolves this.
3. **Output Directory Discovery**:
   - `boltzgen` writes final ranked structures to `final_ranked_designs/` and `intermediate_designs_inverse_folded/` rather than a flat output directory. Walking all subdirectories for `.cif` and `.pdb` outputs (excluding the staged `input.cif` / `input.pdb`) ensures reliable artifact collection.

### 2.2 RFD4-Proteina (`rfd4_proteina` / `atomworks` + `rfproteina`)
1. **Hotspot Condition Naming (`C_HOT`)**:
   - Upstream `rfproteina` (`atomworks`) validates condition tokens against `{C_ADD, C_HOT, C_TERM, H, E, L}`. Using `HOTSPOT` raises `AssertionError: Invalid conditions: {'HOTSPOT'}`; hotspot specifications must use `C_HOT`.
2. **1-Indexed Sequential Residue Spans (`label_seq_id`)**:
   - `atomworks` re-indexes parsed target chains into 1-indexed contiguous `label_seq_id` coordinates (`1..N`). Passing crystallographic `auth_seq_id` numbers (such as `A18-132` when residues 1–17 are unresolved in `4Z18`) causes out-of-bounds slicing (`IndexError: index 114 is out of bounds for axis 0 with size 114`).
   - `input_converter.py` maps user-supplied `auth_seq_id` spans and hotspot residue IDs onto 1-indexed sequential indices (`1..N`) before constructing the RFD4 YAML config.
3. **`NaN` B-Factors in Generated `.cif` Files**:
   - `rfd4-generate` outputs `NaN` in the `_atom_site.B_iso_or_equiv` column of generated `.cif` files.
   - Downstream parsers (`parse_cif_chains`, `calculate_plddt_stats`, `calculate_per_chain_plddt` in `shared_utils.py`) and the viewer (`/api/analysis` and `/api/cif` in `src/foldrun-viewer/app.py`) must sanitize `NaN`/`Infinity` values to prevent invalid RFC 8259 JSON responses (`Unexpected token 'N', ..."max":NaN...`) and broken `3Dmol.js` pLDDT coloring.

### 2.3 BindCraft (`FreeBindCraft --no-pyrosetta`)
1. **PyRosetta-Free Execution (`--no-pyrosetta`)**:
   - Using `FreeBindCraft` with `--no-pyrosetta` replaces commercial PyRosetta dependencies with open-source `OpenMM`, `FASPR`, `FreeSASA`, and `sc-rs`.
2. **Required Asset Symlinks & Config Files**:
   - `FreeBindCraft` expects AlphaFold2 weights at `<bindcraft_dir>/params/params_model_{1..5}_ptm.npz` and `default_4stage_multimer.json` in the working directory. Symlinking `/mnt/nfs/foldrun/alphafold2/params` to `/opt/bindcraft/params` and symlinking `/opt/bindcraft/settings_advanced/default_4stage_multimer.json` into the run directory satisfies both requirements without duplicating weights.
3. **`ColabDesign` SoluProtMPNN Weight Compatibility Shim**:
   - Newer `colabdesign` releases removed the `weights` keyword argument from `mk_mpnn_model.__init__`, whereas `FreeBindCraft` passes `weights="soluble"`. Wrapping `mk_mpnn_model.__init__` in `sitecustomize.py` / `predict.py` to pop unsupported `weights` kwargs restores compatibility.
4. **OpenMM PTX Compilation Fallback on A100 GPUs**:
   - While JAX/ColabDesign runs hallucination and AF2 backpropagation on the A100 GPU, `OpenMM`'s bundled CUDA kernel compiler can fail with `CUDA_ERROR_UNSUPPORTED_PTX_VERSION (222)` when the host driver predates the container's CUDA toolkit minor version. Forcing `OpenMM` relaxation onto the `CPU` platform avoids PTX compilation failures while keeping JAX trajectory optimization on the GPU.

---

## 3. Infrastructure, Cloud Batch, and Cloud Build Learnings

1. **Per-Model Weight Check Before Triggering Cloud Batch (`setup_data.py` & `submit_prediction.py`)**:
   - Previously, `submit_prediction.py` invoked `setup_data.py --all` whenever weight directories were checked, and `setup_data.py` unconditionally submitted a Cloud Batch job even when weights already existed on `/mnt/nfs/foldrun`.
   - Passing `--model <model_type>` and checking `os.path.exists(nfs_dest_path)` in `setup_data.py` prevents redundant Cloud Batch jobs on every prediction request.
2. **Cloud Batch Job ID Naming Constraints (`download.py`)**:
   - Cloud Batch job IDs must match `^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$`. Model names containing underscores (`rfd4_proteina`) must be sanitized (`model.replace("_", "-")`) before constructing the Cloud Batch `job_id`.
3. **VPC Network Self-Link Comparison (`batch.py`)**:
   - GCE instance templates return network/subnetwork URLs containing the **project ID** (`projects/my-gcp-project/...`), whereas Cloud Filestore returns URLs containing the **project number** (`projects/123456789012/...`).
   - `_template_nic_matches()` in `foldrun_app/core/batch.py` must compare normalized `/global/networks/<name>` and `/regions/<region>/subnetworks/<name>` suffixes rather than full self-link strings; otherwise, every Batch submission deletes and recreates the instance template, triggering `409 Conflict` race conditions when multiple downloads run in parallel.
4. **HuggingFace Xet Storage Over NFS (`databases.yaml`)**:
   - Downloading multi-file HuggingFace snapshots (`rfd4_proteina_cache`) directly onto Cloud Filestore NFS via `hf_xet` fails with `CasObjectError: PresignedUrlExpirationError` due to NFS write latency. Exporting `HF_HUB_DISABLE_XET=1` forces standard HTTPS chunked downloads and eliminates the error.
5. **Cloud Build Comma-Separated `--build-target` Substitutions (`deploy-all.sh`)**:
   - Passing comma-separated targets (`--build-target viewer,analysis`) in `gcloud builds submit --substitutions=_BUILD_TARGET=viewer,analysis` fails because `gcloud` uses `,` as the key-value delimiter by default. Using `gcloud`'s custom delimiter syntax (`--substitutions="^;^_REGION=...;_BUILD_TARGET=${BUILD_TARGET}"`) allows comma-separated targets.

---

## 4. FoldRun Viewer & IAM Learnings

1. **GCS Output Directory Resolution (`src/foldrun-viewer/app.py`)**:
   - `AlphaFold2`, `OpenFold3`, and `Boltz-2` store outputs under timestamped directories (`pipeline_runs/YYYYMMDD_HHMMSS/`), whereas `BoltzGen`, `RFD4-Proteina`, and `BindCraft` use slug-named directories (`pipeline_runs/<job_name_slug>/`).
   - Querying the Vertex AI `PipelineJob` `runtimeConfig.gcsOutputDirectory` in `get_analysis_summary()` and matching arbitrary `pipeline_runs/<run_dir>/` directories in `_scan_analysis_state()` and `list_jobs()` ensures the viewer locates `analysis/summary.json` and displays the **View** button for all model types.
2. **Cloud Storage Overwrite Permissions (`terraform/viewer.tf`)**:
   - Granting `roles/storage.objectCreator` to `foldrun-viewer-sa` allows creating new objects in GCS, but overwriting an existing `task_config.json` or `analysis_metadata.json` via `/api/analyze` requires `storage.objects.delete` permission (`403 Forbidden`).
   - Granting `roles/storage.objectUser` in `terraform/viewer.tf` allows `foldrun-viewer-sa` to both create and overwrite analysis configuration files.
3. **Frontend Schema Normalization (`src/foldrun-viewer/templates/combined.html`)**:
   - `combined.html` now resolves predictions from `all_predictions_summary || top_predictions || all_predictions`, renders generative binder metrics (`sc_score`, `clash_score`, `designed_sequence`, `ranking_comparison`), supports `gemini_expert_analysis` (`analysis_text`), and normalizes `(0, 1]` B-factors to `[0, 100]` for `3Dmol.js` pLDDT coloring.
