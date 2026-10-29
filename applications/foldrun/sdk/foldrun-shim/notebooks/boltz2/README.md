# Boltz-2 on FoldRun via `foldrun-shim` (`Boltz2_FoldRun_Shim.ipynb`)

This directory contains [`Boltz2_FoldRun_Shim.ipynb`](./Boltz2_FoldRun_Shim.ipynb), a modified Jupyter / Colab notebook adapted from the official [**`jwohlwend/boltz`**](https://github.com/jwohlwend/boltz) examples (`affinity.yaml`, `prot.yaml`, `pocket.yaml`, `prot_custom_msa.yaml`, `prot_no_msa.yaml`) and public Boltz-2 Colab notebooks. It executes Boltz-2 biomolecular co-folding and quantitative binding-affinity predictions on FoldRun's auto-scaling Google Cloud Vertex AI A100 Pipelines via `foldrun-shim` and `foldrun-gateway`.


---

## Original Notebook Reference, Credits & Citations

- **Original Public Examples & Notebooks**:
  - Official [`jwohlwend/boltz` Examples](https://github.com/jwohlwend/boltz/tree/main/examples) ([`affinity.yaml`](https://github.com/jwohlwend/boltz/blob/main/examples/affinity.yaml), [`prot.yaml`](https://github.com/jwohlwend/boltz/blob/main/examples/prot.yaml), [`pocket.yaml`](https://github.com/jwohlwend/boltz/blob/main/examples/pocket.yaml), [`prot_custom_msa.yaml`](https://github.com/jwohlwend/boltz/blob/main/examples/prot_custom_msa.yaml), [`prot_no_msa.yaml`](https://github.com/jwohlwend/boltz/blob/main/examples/prot_no_msa.yaml)) & [`docs/prediction.md`](https://github.com/jwohlwend/boltz/blob/main/docs/prediction.md)
  - Public Colab Notebook: [`sokrypton/ColabFold` `Boltz1.ipynb`](https://colab.research.google.com/github/sokrypton/ColabFold/blob/main/Boltz1.ipynb)
- **Upstream Repository**: [`jwohlwend/boltz`](https://github.com/jwohlwend/boltz)
- **Authors & Credits**: Saro Passaro, Gabriele Corso, Jeremy Wohlwend, Mateo Reveiz, Stephan Thaler, Vignesh Ram Somnath, Noah Getz, Tally Portnoi, Julien Roy, Hannes Stark, David Kwabi-Addo, Dominique Beaini, Tommi Jaakkola, and Regina Barzilay (**MIT Jameel Clinic / CSAIL & Recursion**); ColabFold notebook contributions by Sergey Ovchinnikov and Martin Steinegger.
- **License**: MIT License.
- **Primary Citations**:
  1. Passaro, S., Corso, G., Wohlwend, J. *et al.* "Boltz-2: Towards Accurate and Efficient Binding Affinity Prediction." *bioRxiv* (2025). [doi:10.1101/2025.06.14.659707](https://doi.org/10.1101/2025.06.14.659707)
  2. Wohlwend, J., Corso, G., Passaro, S. *et al.* "Boltz-1: Democratizing Biomolecular Interaction Modeling." *bioRxiv* (2024). [doi:10.1101/2024.11.19.624167](https://doi.org/10.1101/2024.11.19.624167)

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

### Original `jwohlwend/boltz` Upstream Examples
The notebook authors and runs the canonical upstream examples:
- `examples/prot.yaml`: Single protein chain.
- `examples/ligand.yaml`: Protein + ligand complex with `ccd: SAH`.
- `examples/affinity.yaml`: Binding affinity prediction for `binder: B`.

### Verbatim CLI Command Execution
```bash
!boltz predict prot.yaml --use_msa_server --out_dir ./boltz_out
```

---

## Part B — FoldRun Additions (`foldrun-shim` Python API, `/mnt/nfs/foldrun` Shared Databases & Weights, Local Asset Bundling, and Multi-GPU Seed Parallelism)

### 1. Interception of `--use_msa_server` for In-VPC Private Search
Public Boltz-2 relies on `--use_msa_server` querying `api.colabfold.com`, exposing proprietary sequences to public web APIs. `foldrun-shim` intercepts `--use_msa_server`, stripping the external network flag and routing sequence alignment directly to FoldRun's internal Cloud Filestore NFS share at `/mnt/nfs/foldrun` (`uniref90` and `mgnify` via `jackhmmer`) with SHA-256 MSA caching (`/mnt/nfs/foldrun/boltz2_msas_cache/`).

### 2. Bundling Local Pre-Computed MSAs (`.a3m`) & Structural Templates (`.cif`)
Custom local alignments (`prot_custom_msa.yaml`) and template structures are automatically packaged into `assets.tar.gz` by `bundler.py` and staged to GCS FUSE paths.

### 3. Multi-Seed Parallel Vertex AI GPU Fan-Out
Runs up to 10 seeds (`--num_model_seeds=5`) across dedicated NVIDIA A100 GPUs in parallel (`dsl.ParallelFor`) with DWS `FLEX_START`.

### 4. Programmatic Python Helper API (`boltz.predict`)
- Clean Python execution: `job = boltz.predict("affinity.yaml", out_dir="./boltz_out", use_potentials=True, diffusion_samples=5, num_model_seeds=2, write_full_pae=True)`
- Displays rich `GatewayJobResult` HTML status card with direct link to 3D FoldRun Viewer (`job.viewer_url`).
