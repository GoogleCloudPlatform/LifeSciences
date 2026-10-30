# RFD4-Proteina on FoldRun via `foldrun-shim` (`RFD4_Proteina_FoldRun_Shim.ipynb`)

This directory contains [`RFD4_Proteina_FoldRun_Shim.ipynb`](./RFD4_Proteina_FoldRun_Shim.ipynb), a modified Jupyter / Colab notebook adapted from the official [**`nvidia/RFD4-Proteina`**](https://huggingface.co/nvidia/RFD4-Proteina) ([`rfd4-proteina.org`](https://rfd4-proteina.org) / [**`NVIDIA-Digital-Bio/proteina`**](https://github.com/NVIDIA-Digital-Bio/proteina) + `atomworks` / `rfproteina`) examples. It executes RFD4-Proteina joint sequence-structure flow-matching protein and binder design across all three generative design modes (`binder_design`, `motif_scaffolding`, and `unconditional_monomer`) on FoldRun's auto-scaling Google Cloud Vertex AI GPU Pipelines (`A100`, `A100_80GB`, or `L4`) via `foldrun-shim` and `foldrun-gateway`.


---

## Original Notebook Reference, Credits & Citations

- **Original Public Examples & Documentation**:
  - Official Model Card & Inference Examples: [`nvidia/RFD4-Proteina` on HuggingFace](https://huggingface.co/nvidia/RFD4-Proteina) and [`rfd4-proteina.org`](https://rfd4-proteina.org)
  - Official Proteina Codebase & Notebooks: [`NVIDIA-Digital-Bio/proteina`](https://github.com/NVIDIA-Digital-Bio/proteina) (`atomworks` / `rfproteina`)
- **Authors & Credits**: Tomas Geffner, Kieran Didi, Zuobai Zhang, Danny Reidenbach, Zhonglin Cao, Jason Yim, Mario Geiger, Christian Dallago, Emine Kucukbenli, Karsten Kreis, Arash Vahdat, and collaborators (**NVIDIA Digital Biology Research & Institute for Protein Design (IPD), University of Washington**).
- **License**: NVIDIA Open Model License (`nvidia/RFD4-Proteina`) / Apache License 2.0 (`NVIDIA-Digital-Bio/proteina`).
- **Primary Citations**:
  1. Geffner, T., Didi, K., Zhang, Z. *et al.* "Proteina: Scaling Flow-Based Protein Structure Generative Models." *International Conference on Learning Representations (ICLR)* (2025). [arXiv:2503.00710](https://arxiv.org/abs/2503.00710)
  2. NVIDIA Digital Biology & IPD. "RFD4-Proteina: Joint Sequence-Structure Flow-Matching for De Novo Protein and Binder Design." (2025). [huggingface.co/nvidia/RFD4-Proteina](https://huggingface.co/nvidia/RFD4-Proteina)

---

## 1. Prerequisites, Authentication & Installing `foldrun-shim` from GCS

1. **Authenticate to Google Cloud**:
   ```bash
   gcloud auth login
   ```
2. **Automatic Configuration Loading (`.env`)**:
   `deploy-all.sh` automatically generates `sdk/foldrun-shim/notebooks/.env` and uploads `gs://<PROJECT_ID>-foldrun-data/sdk/.env` with the deployed URLs and bucket names. The notebook automatically detects and loads this configuration in Cell 2.
3. **Download & Install the Shim**:
   ```bash
   gcloud storage cp gs://<PROJECT_ID>-foldrun-data/sdk/foldrun_shim-0.1.0-py3-none-any.whl /tmp/
   pip install /tmp/foldrun_shim-0.1.0-py3-none-any.whl
   ```
4. **Configure the Gateway URL**:
   ```bash
   export FOLDRUN_GATEWAY_URL="https://foldrun-gateway-<hash>-uc.a.run.app"
   ```
   Requires only `roles/run.invoker` on the `foldrun-gateway` Cloud Run service.

---

## Part A — Original Upstream Example (Unmodified / Verbatim Workflow)

### Original HuggingFace `nvidia/RFD4-Proteina` Quickstart Examples

The notebook runs the exact quickstart command from the official [`nvidia/RFD4-Proteina` HuggingFace Model Card](https://huggingface.co/nvidia/RFD4-Proteina):

```bash
# Exact upstream HuggingFace nvidia/RFD4-Proteina command verbatim:
rfd4-generate inputs=data/benchmarks/monomer/monomer-short.cif \
  hf_ckpt_path=nvidia/RFD4-Proteina \
  hf_ckpt_revision=a06e2509c7033b010fc43614e9fd840303588502 \
  num_replicates=1 out_dir=./runs/first '~metrics' compile_lengths=null
```

#### Packaged Python Snippet & Container Benchmark Resolution
In standard upstream environments, running this command requires a local clone of `NVIDIA-Digital-Bio/proteina` or importing the package:
```bash
inputs=$(python -c "import rfproteina, pathlib; print(pathlib.Path(rfproteina.__file__).parent / 'data/benchmarks/monomer/monomer-short.cif')")
```

With **`foldrun-shim`**, **`rfproteina` does not even need to be installed locally** on the notebook machine!
1. `foldrun-shim`'s `bundle_local_inputs` intercepts `data/benchmarks/...` paths, extracts the relative benchmark path, and synthesizes a benchmark descriptor without raising `FileNotFoundError`.
2. `_strip_local_rfd4_proteina_args` strips local checkpoint flags (`hf_ckpt_path`, `hf_ckpt_revision`, `ckpt_path`, `cache`, `data_dir`).
3. Inside the FoldRun container, `predict_rfd4_proteina` automatically resolves the benchmark file directly at `/opt/rfd4-proteina/data/benchmarks/...`.

#### Additional Upstream Benchmarks
- **PPI Binder Benchmark (PD-L1)**:
  ```bash
  rfd4-generate inputs=data/benchmarks/ppi/pdl1_short.cif \
    hf_ckpt_path=nvidia/RFD4-Proteina \
    hf_ckpt_revision=a06e2509c7033b010fc43614e9fd840303588502 \
    num_replicates=1 out_dir=./runs/pdl1 '~metrics' compile_lengths=null
  ```
- **Motif Scaffolding Benchmark**:
  ```bash
  rfd4-generate inputs=data/benchmarks/motif_scaffolding/02_1YCR.cif \
    hf_ckpt_path=nvidia/RFD4-Proteina \
    hf_ckpt_revision=a06e2509c7033b010fc43614e9fd840303588502 \
    num_replicates=1 out_dir=./runs/motif '~metrics' compile_lengths=null
  ```

---

## Part B — FoldRun Additions (`foldrun-shim` Python API, `/mnt/nfs/foldrun` Shared Databases & Weights, Local Asset Bundling, and Multi-GPU Seed Parallelism)

### 1. Pre-Staged NFS Model + Autoencoder Cache (`/mnt/nfs/foldrun/rfd4_proteina/cache`)
- Pre-staged once onto a `REGIONAL` Cloud Filestore NFS share at `/mnt/nfs/foldrun/rfd4_proteina/cache/hub` using `HF_HUB_DISABLE_XET=1` to prevent HuggingFace Xet CAS network timeout errors.
- GPU workers mount `/mnt/nfs/foldrun` and export `HF_HOME`, `HF_HUB_CACHE`, `RFD4_CACHE`, and `DATA_PATH`, eliminating cold-start download delays.

### 2. Automatic `auth_seq_id -> label_seq_id` Mapping & `C_HOT` Translation
- `gemmi` inspects `_atom_site.auth_asym_id`, `_atom_site.auth_seq_id`, and `_atom_site.label_seq_id` to translate crystallographic numbers (e.g., PD-L1 `4Z18` Chain A residues `54, 56, 66`) to 1-indexed `label_seq_id` coordinates (`37, 39, 49`) under `C_HOT`, preventing upstream `atomworks` `IndexError` and `AssertionError`.

### 3. Local Asset Bundling (`assets.tar.gz`)
- When custom local `.cif` or `.pdb` structures are passed (e.g. `input: ./local_pdl1_target.cif`), `foldrun-shim` packages them into `assets.tar.gz` and the gateway automatically rewrites target references.

### 4. Multi-Seed Parallel Vertex AI GPU Execution
- Fans out `--num_model_seeds=5` in parallel across dedicated NVIDIA A100 (`a2-highgpu-1g` / `a2-ultragpu-1g`) or cost-optimized L4 GPUs with DWS `FLEX_START`.

### 5. Fast Mode (`~metrics`) vs. Full Self-Consistency Metrics (`--metrics` / `compute_metrics=True`)
- **Explicit Fast Mode (Default — `compute_metrics=False` / `--fast-no-metrics` / `~metrics`)**: By default, `rfd4-generate` and `rfd4_proteina.predict(..., compute_metrics=False)` run in fast mode (`compute_metrics=false`), skipping the expensive self-consistency refolding pass and printing an explicit notice (`[FoldRun] RFD4-Proteina running in fast mode (~metrics; self-consistency metrics disabled — pass compute_metrics=True or --metrics to compute full metrics)`). Uncomputed self-consistency metrics (such as `sc_score`) are reported honestly as `None` / `N/A` rather than synthetic placeholders.
- **Full Self-Consistency Metrics (`--metrics` / `compute_metrics=True`)**: Pass `--metrics` (or `compute_metrics=true`) on the CLI or `compute_metrics=True` in `rfd4_proteina.predict(...)` to run full self-consistency refolding and populate `sc_score` across all generated designs.

### 6. Programmatic Python Helper API (`rfd4_proteina.predict`)
- Clean programmatic execution with rich Jupyter HTML status cards (`job._repr_html_()`), direct link to the 3D FoldRun Viewer (`job.viewer_url`), and disconnect recovery via `.foldrun_job.json`.
