# FoldRun Native CLI & Jupyter Notebook Shim (`foldrun-shim`)

`foldrun-shim` is a thin-permission Python client and CLI wrapper that intercepts native CLI commands and notebook calls across all **7 FoldRun biomolecular modeling and generative design workflows**—both in terminal shells and inside Jupyter Notebook / Colab cells—and executes them on FoldRun's auto-scaling Google Cloud Vertex AI H100 / A100 / L4 GPU Pipelines and Model Garden endpoints via `foldrun-gateway`:

- **Structure Prediction & Co-Folding (4 Models)**:
  - **AlphaFold 2**: `run_alphafold.py --fasta_paths=...` / `from foldrun_shim import alphafold2`
  - **AlphaFold 3**: `alphafold3 --json_path=...` & `run_alphafold.py --json_path=...` / `from foldrun_shim import alphafold3`
  - **Boltz-2**: `boltz predict ...` / `from foldrun_shim import boltz`
  - **OpenFold 3**: `run_openfold predict ...` / `from foldrun_shim import openfold3`
- **Generative Protein & Binder Design (3 Models)**:
  - **BoltzGen**: `boltzgen run ...` & `boltzgen check ...` / `from foldrun_shim import boltzgen`
  - **RFD4-Proteina**: `rfd4-generate ...` / `from foldrun_shim import rfd4_proteina`
  - **BindCraft (`FreeBindCraft`)**: `bindcraft ...` & `bindcraft.py ...` / `from foldrun_shim import bindcraft`

## Features

- **Zero Local GPU or Database Requirements**: Requires only `roles/run.invoker` on the `foldrun-gateway` Cloud Run service. All genetic database searches, reference caches (`/mnt/nfs/foldrun`), and H100 / A100 / L4 GPU execution run in Google Cloud.
- **Native Asset Bundling**: Automatically inspects local input files (AlphaFold 3 `.json` external `unpairedMsaPath`/`pairedMsaPath`/`templates[*].mmcifPath`, Boltz-2 `.yaml`, OpenFold 3 `.json`, BoltzGen `.yaml`, RFD4-Proteina `.yaml`/`.json`, BindCraft `--settings`/`--filters`/`--advanced` `.json`) for referenced local custom MSAs (`.a3m`, `.sto`), templates and target structures (`.cif`, `.pdb`), and `--runner_yaml` configurations, bundles them into a compressed `assets.tar.gz` archive, and uploads them to `foldrun-gateway`.
- **Server-Side RCSB PDB Resolution & Hotspot Translation**: Recognizes 4-character RCSB PDB IDs (e.g. `4Z18`) in generative binder inputs, fetches the canonical structure server-side (`target_resolver.py`), and automatically maps crystallographic `auth_seq_id` hotspot residue numbers onto 1-indexed `label_seq_id` via `gemmi`.
- **Interrupt-Resilient Notebook Execution**: Persists `<out_dir>/.foldrun_job.json` upon submission (`202 Accepted`) so re-running an interrupted Jupyter Notebook cell automatically re-attaches to the running Vertex AI `PipelineJob` instead of launching a duplicate GPU job.
- **Native Output Materialization**: Downloads and extracts completed prediction or design directories into `--out_dir` / `--output_dir` / `--output` matching each model's native local directory layout.

## Where to Find & How to Install `foldrun-shim`

During FoldRun deployment (`./deploy-all.sh` or `./deploy-all.sh --steps build --build-target sdk`), Cloud Build (`cloudbuild.yaml` steps `determine-sdk-version`, `build-sdk-wheel`, and `upload-sdk-gcs`) builds the pure-Python wheel with a date + incremental version (e.g. `foldrun_shim-0.1.0.YYYYMMDD.N-py3-none-any.whl`) and uploads the wheel, `latest_build`, and all 7 modified notebooks to the project's FoldRun GCS bucket under `gs://<PROJECT_ID>-foldrun-data/sdk/`:

- **Latest Build Pointer**: `gs://<PROJECT_ID>-foldrun-data/sdk/latest_build` (contains the exact filename of the newest SDK wheel)
- **Pre-Built Wheel on GCS**: `gs://<PROJECT_ID>-foldrun-data/sdk/foldrun_shim-0.1.0-py3-none-any.whl` (and dated incremental wheels)
- **Modified Public Notebooks on GCS**: `gs://<PROJECT_ID>-foldrun-data/sdk/notebooks/<model>/*.ipynb`

### Installation Steps (Authenticate First, Then Download from GCS)

1. **Authenticate to Google Cloud First**:
   - **In Google Colab**:
     ```python
     from google.colab import auth
     auth.authenticate_user()
     ```
   - **In Colab Enterprise / Vertex AI Workbench**: Authenticated automatically via the attached runtime service account.
   - **In a Terminal / Local Jupyter Kernel**:
     ```bash
     gcloud auth login
     ```

2. **Automatic Configuration Loading (`.env`)**:
   - FoldRun's deployment script (`./deploy-all.sh`) automatically generates local `.env` files in `sdk/foldrun-shim/notebooks/.env` (and per-model subfolders) and uploads `gs://<PROJECT_ID>-foldrun-data/sdk/.env`.
   - All 7 notebooks automatically detect and load this configuration on startup (searching `./.env`, `../.env`, `/content/.env`, or downloading from `gs://<PROJECT_ID>-foldrun-data/sdk/.env`), populating `GCP_PROJECT_ID`, `GCP_REGION`, `FOLDRUN_GCS_BUCKET`, `FOLDRUN_GATEWAY_URL`, and `FOLDRUN_VIEWER_URL` with zero manual configuration required.

3. **Download the Wheel (and Optional Notebooks) from GCS & Install**:
   Requires `roles/storage.objectViewer` on `gs://<PROJECT_ID>-foldrun-data` (recommended to scope to the `sdk/` prefix via an IAM condition):
   ```bash
   gcloud storage cp gs://<PROJECT_ID>-foldrun-data/sdk/latest_build /tmp/
   WHL=$(cat /tmp/latest_build)
   gcloud storage cp gs://<PROJECT_ID>-foldrun-data/sdk/${WHL} /tmp/
   gcloud storage cp -r gs://<PROJECT_ID>-foldrun-data/sdk/notebooks ./notebooks

   pip install /tmp/${WHL}
   export FOLDRUN_GATEWAY_URL="https://foldrun-gateway-<hash>-uc.a.run.app"
   ```

### What Is Needed to Use `foldrun-shim`
1. **Authenticate to Google Cloud & Install `foldrun-shim` from GCS**: Lightweight Python package (`httpx`, `google-auth`, `pyyaml`, `tqdm`, `requests`) downloaded from `gs://<PROJECT_ID>-foldrun-data/sdk/foldrun_shim-0.1.0-py3-none-any.whl` that runs on any CPU-only laptop, free Google Colab runtime, Colab Enterprise, or Vertex AI Workbench.
2. **Set `FOLDRUN_GATEWAY_URL`**: Point to your deployed `foldrun-gateway` Cloud Run URL.
3. **IAM `roles/run.invoker` Only (4-Tier OIDC Auth Cascade in `foldrun_shim/auth.py`)**:
   - Users **never** need direct IAM permissions (`roles/aiplatform.user` or `roles/storage.objectAdmin`) on internal GCS buckets or Vertex AI Pipelines.
   - `fetch_gateway_headers()` automatically resolves and caches `Authorization: Bearer <OIDC_ID_TOKEN>` (with 5-minute JWT `exp` expiry renewal and automatic HTTP `401` cache invalidation) in the following order:
     1. Explicit `FOLDRUN_ID_TOKEN` environment variable (falling through automatically if expired).
     2. Service account impersonation (`FOLDRUN_IMPERSONATE_SERVICE_ACCOUNT`) via `gcloud auth print-identity-token --impersonate-service-account=... --audiences=$FOLDRUN_GATEWAY_URL`.
     3. Google Cloud Metadata Server (`google.oauth2.id_token.fetch_id_token`), automatic on **Vertex AI Workbench** and **Colab Enterprise**.
     4. Local developer workstation fallback via `gcloud auth print-identity-token` (`--audiences=$FOLDRUN_GATEWAY_URL` for service accounts, bare token for human Google accounts).

## Usage

### 1. Native CLI & Jupyter `!` Shell Magic

```bash
# AlphaFold 2
run_alphafold.py --fasta_paths=monomer.fasta --output_dir=./results --model_preset=monomer --max_template_date=2025-04-01

# AlphaFold 3 (via alphafold3 or run_alphafold.py --json_path=...)
alphafold3 --json_path=troponin_c.json --output_dir=./af3_results --num_diffusion_samples=1 --run_data_pipeline=false
run_alphafold.py --json_path=troponin_c.json --output_dir=./af3_results --num_diffusion_samples=1 --run_data_pipeline=false

# Boltz-2
boltz predict affinity.yaml --use_potentials --out_dir ./results

# OpenFold 3
run_openfold predict --query_json=query.json --runner_yaml=runner.yaml --output_dir=./results

# BoltzGen
boltzgen run pdl1_binder.yaml --output ./boltzgen_out --protocol protein-anything --num_designs 4 --budget 2 --num_model_seeds 5

# RFD4-Proteina (fast mode compute_metrics=false by default; pass --metrics for full ProteinMPNN + Chai-1 self-consistency)
rfd4-generate inputs=pdl1_binder_design.yaml out_dir=./rfd4_out num_replicates=4 seed=42 --num_model_seeds=5
rfd4-generate inputs=pdl1_binder_design.yaml out_dir=./rfd4_scored_out num_replicates=4 --metrics --num_model_seeds=5

# BindCraft (FreeBindCraft --no-pyrosetta; local --filters & --advanced JSONs are auto-bundled; --out_dir defaults to settings design_path)
bindcraft --settings=pdl1_settings.json --filters=default_filters.json --advanced=default_4stage_multimer.json --no-pyrosetta --out_dir=./bindcraft_out
```

### 2. Python Notebook Helper API

```python
from foldrun_shim import alphafold2, alphafold3, boltz, openfold3, boltzgen, rfd4_proteina, bindcraft

# Structure prediction / co-folding
job_af3 = alphafold3.predict("troponin_c.json", output_dir="./af3_results", num_diffusion_samples=1, run_data_pipeline=False)
job_boltz = boltz.predict("affinity.yaml", out_dir="./results", use_potentials=True, diffusion_samples=5)

# Generative binder design
job_bg = boltzgen.predict("pdl1_binder.yaml", out_dir="./boltzgen_out", protocol="protein-anything", num_designs=4, budget=2, num_model_seeds=5)
job_rfd4 = rfd4_proteina.predict("pdl1_binder_design.yaml", out_dir="./rfd4_out", design_mode="binder_design", num_designs_per_seed=4, num_model_seeds=5, compute_metrics=False)
job_bc = bindcraft.predict("pdl1_settings.json", out_dir="./bindcraft_out", num_designs_per_seed=2, num_model_seeds=5)
```

---

## Modified Public Jupyter / Colab Notebooks (`notebooks/`)

The [`notebooks/`](./notebooks/) directory provides **seven end-to-end modified public Colab/Jupyter notebooks**—each paired with a detailed `README.md`—demonstrating both the drop-in CLI (`!`) and Python helper (`foldrun_shim.<model>.predict`) interfaces, `/mnt/nfs/foldrun` database & cache integration, local asset bundling, and downstream `py3Dmol` / `matplotlib` analysis:

| Model | Modified Notebook & Guide | Original Reference, Credits & Citations | How the FoldRun Database & Caches (`/mnt/nfs/foldrun`) Are Demonstrated | Native Materialized Output Paths |
| :--- | :--- | :--- | :--- | :--- |
| **AlphaFold 2** | • [`notebooks/alphafold2/AlphaFold2_FoldRun_Shim.ipynb`](./notebooks/alphafold2/AlphaFold2_FoldRun_Shim.ipynb)<br>• [`notebooks/alphafold2/README.md`](./notebooks/alphafold2/README.md) | • **Original Notebook**: [`google-deepmind/alphafold` `notebooks/AlphaFold.ipynb`](https://github.com/google-deepmind/alphafold/blob/v2.3.2/notebooks/AlphaFold.ipynb) ([Colab](https://colab.research.google.com/github/deepmind/alphafold/blob/main/notebooks/AlphaFold.ipynb))<br>• **Credits**: Jumper *et al.*, **Google DeepMind**<br>• **Citations**: *Nature* 596, 583–589 (2021) ([doi:10.1038/s41586-021-03819-2](https://doi.org/10.1038/s41586-021-03819-2)); Evans *et al.*, *bioRxiv* (2021) ([doi:10.1101/2021.10.04.463034](https://doi.org/10.1101/2021.10.04.463034))<br>• **License**: Apache 2.0 (code) / CC-BY 4.0 (weights) | • Compares `--db_preset=reduced_dbs` (`small_bfd` + `uniref90` + `mgnify` + `pdb70`/`pdb_seqres` + `pdb_mmcif` + `uniprot`) vs. `--db_preset=full_dbs` (1.8 TB `bfd` + 206 GB `uniref30`, impossible on standard Colab).<br>• Demonstrates `--msa_method` (`auto`, CPU `jackhmmer`, or L4 GPU `mmseqs2`) and SHA-256 feature caching in `/mnt/nfs/foldrun/af2_features_cache/`.<br>• Inspects materialized `msas/`, `features.pkl` template counts, and per-residue non-gap MSA coverage. | `<output_dir>/<fasta_stem>/`:<br>• `ranked_0.pdb` .. `ranked_4.pdb`<br>• `ranking_debug.json`<br>• `relaxed_model_*.pdb` & `unrelaxed_model_*.pdb`<br>• `features.pkl` & `msas/*` |
| **AlphaFold 3** | • [`notebooks/alphafold3/AlphaFold3_FoldRun_Shim.ipynb`](./notebooks/alphafold3/AlphaFold3_FoldRun_Shim.ipynb)<br>• [`notebooks/alphafold3/README.md`](./notebooks/alphafold3/README.md) | • **Original Spec & Repo**: [`google-deepmind/alphafold3` `docs/input.md`](https://github.com/google-deepmind/alphafold3/blob/main/docs/input.md)<br>• **Credits**: Abramson *et al.*, **Google DeepMind & Isomorphic Labs**<br>• **Citation**: *Nature* 630, 493–500 (2024) ([doi:10.1038/s41586-024-07487-w](https://doi.org/10.1038/s41586-024-07487-w))<br>• **License**: CC-BY-NC-SA 4.0 (code) / AlphaFold 3 Model Parameters Terms of Use | • Dispatches multimodal `dialect: "alphafold3"` JSON (proteins, RNA, DNA, CCD/SMILES ligands, `Mg2+`/`Ca2+` ions, PTMs) to the dedicated scale-to-zero **Vertex AI Model Garden H100 80 GB (`a3-highgpu-1g`)** endpoint.<br>• Supports MSA-free mode (`unpairedMsa: ""`, `pairedMsa: ""`, `templates: []`), server-side `/mnt/nfs/foldrun` alignment (`--run_data_pipeline=true`), and automatic local asset bundling for `unpairedMsaPath`, `pairedMsaPath`, and `templates[*].mmcifPath`. | `<output_dir>/<stem>/`:<br>• `<stem>_model.cif` (or `seed-*_sample-*/model.cif`)<br>• `*_summary_confidences.json`<br>• `*_confidences.json` |
| **Boltz-2** | • [`notebooks/boltz2/Boltz2_FoldRun_Shim.ipynb`](./notebooks/boltz2/Boltz2_FoldRun_Shim.ipynb)<br>• [`notebooks/boltz2/README.md`](./notebooks/boltz2/README.md) | • **Original Examples & Colab**: [`jwohlwend/boltz/examples`](https://github.com/jwohlwend/boltz/tree/main/examples) (`affinity.yaml`, `pocket.yaml`, `prot_custom_msa.yaml`, `prot_no_msa.yaml`) & [`sokrypton/ColabFold` `Boltz1.ipynb`](https://colab.research.google.com/github/sokrypton/ColabFold/blob/main/Boltz1.ipynb)<br>• **Credits**: Passaro, Corso, Wohlwend *et al.*, **MIT Jameel Clinic / CSAIL & Recursion**; Ovchinnikov & Steinegger (**ColabFold**)<br>• **Citations**: Passaro *et al.*, *bioRxiv* (2025) ([doi:10.1101/2025.06.14.659707](https://doi.org/10.1101/2025.06.14.659707)); Wohlwend *et al.*, *bioRxiv* (2024) ([doi:10.1101/2024.11.19.624167](https://doi.org/10.1101/2024.11.19.624167))<br>• **License**: MIT | Demonstrates all **3 Boltz-2 alignment & database modes**:<br>• **Mode A (Server-Side NFS Search)**: Runs `jackhmmer` on `c2-standard-16` against `/mnt/nfs/foldrun/uniref90` + `mgnify` with SHA-256 caching in `/mnt/nfs/foldrun/boltz2_msas_cache/` and pre-staged `boltz2_conf.ckpt`, `boltz2_aff.ckpt`, and `mols/` CCD in `/mnt/nfs/foldrun/boltz2/cache`.<br>• **Mode B (Local Asset Bundling)**: Bundles local `./custom_alignment.a3m` and `./template.cif` into `assets.tar.gz`, stages to `/gcs/.../inputs/assets/`, and skips `jackhmmer`.<br>• **Mode C (MSA-Free)**: Uses `msa: empty` for *de novo* designed proteins. | `<out_dir>/boltz_results_<stem>/predictions/<stem>/`:<br>• `<stem>_model_0.cif`<br>• `confidence_<stem>_model_0.json`<br>• `affinity_<stem>.json`<br>• `pae_<stem>_model_0.npz` & `pde_<stem>_model_0.npz` |
| **OpenFold 3** | • [`notebooks/openfold3/OpenFold3_FoldRun_Shim.ipynb`](./notebooks/openfold3/OpenFold3_FoldRun_Shim.ipynb)<br>• [`notebooks/openfold3/README.md`](./notebooks/openfold3/README.md) | • **Original Notebook & Examples**: [`aqlaboratory/openfold` `notebooks/OpenFold.ipynb`](https://github.com/aqlaboratory/openfold/blob/main/notebooks/OpenFold.ipynb) ([Colab](https://colab.research.google.com/github/aqlaboratory/openfold/blob/main/notebooks/OpenFold.ipynb)) & [`aqlaboratory/openfold-3/examples`](https://github.com/aqlaboratory/openfold-3/tree/main/examples/example_inference_inputs)<br>• **Credits**: Ahdritz, AlQuraishi *et al.*, **AlQuraishi Lab (Columbia University) & OpenFold Consortium**<br>• **Citations**: Ahdritz *et al.*, *Nature Methods* 21, 1514–1524 (2024) ([doi:10.1038/s41592-024-02272-z](https://doi.org/10.1038/s41592-024-02272-z))<br>• **License**: Apache 2.0 | • Demonstrates multi-modal `query.json` (Protein + RNA + DNA + ATP/SMILES Ligands) across all **9 databases** on `/mnt/nfs/foldrun`: protein `jackhmmer` (`uniref90`, `mgnify`, `pdb_seqres`, `uniprot`), RNA `nhmmer` (`rfam`, `rnacentral`), templates (`pdb_mmcif/mmcif_files`), ligand CCD (`of3/ccd/components.bcif`), and weights (`of3/params/of3-p2-155k.pt`), cached by SHA-256 in `/mnt/nfs/foldrun/of3_msas_cache/`.<br>• Demonstrates bundling local `.a3m`/`.sto` alignments (`main_msa_file_paths`, `template_alignment_file_path`) and safe `runner.yaml` scientific overrides (`model_update`, `low_mem`, `plddt`, `pae`, `experiment_settings`). | `<output_dir>/<query_name>/seed_<seed>/`:<br>• `<query_name>_seed_<seed>_sample_1_model.cif`<br>• `..._confidences_aggregated.json`<br>• `..._confidences.json` |
| **BoltzGen** | • [`notebooks/boltzgen/BoltzGen_FoldRun_Shim.ipynb`](./notebooks/boltzgen/BoltzGen_FoldRun_Shim.ipynb)<br>• [`notebooks/boltzgen/README.md`](./notebooks/boltzgen/README.md) | • **Original Spec & Examples**: [`HannesStark/boltzgen` `yaml_spec.md`](https://github.com/HannesStark/boltzgen/blob/main/yaml_spec.md) & [`example/`](https://github.com/HannesStark/boltzgen/tree/main/example) (`boltzgen/boltzgen-1`)<br>• **Credits**: Stark, Corso, Wohlwend, Passaro, Barzilay, Jaakkola *et al.*, **MIT Jameel Clinic / CSAIL & Boltz Community**<br>• **Citation**: Stark *et al.*, *BoltzGen* (2025) ([github.com/HannesStark/boltzgen](https://github.com/HannesStark/boltzgen))<br>• **License**: MIT | • Uses the pre-staged unified NFS cache at `/mnt/nfs/foldrun/boltzgen/cache` (`boltzgen/inference-data` + 5 checkpoints: `boltzgen1_diverse.ckpt`, `boltzgen1_adherence.ckpt`, `boltzgen1_ifold.ckpt`, `boltz2_conf_final.ckpt`, `boltz2_aff.ckpt`).<br>• Demonstrates **3 Target Structure Modes**: (1) 4-char RCSB PDB ID (`path: 4Z18`) with server-side `target_resolver.py` fetch and automatic `gemmi` `auth_seq_id -> label_seq_id` hotspot mapping (`54,56,66,115 -> 37,39,49,98`), (2) local `.cif` bundling (`path: ./custom_target.cif`), and (3) small-molecule pocket design (`ccd: SAH`). | `<out_dir>/<stem>/`:<br>• `final_ranked_designs/ranked_{0..K}.cif` & `metrics.csv`<br>• `ranked_designs/ranked_{0..K}.cif` & `metrics.csv`<br>• `predictions/design_seed<seed>_<i>.cif` & `confidence_design_seed<seed>_<i>.json` |
| **RFD4-Proteina** | • [`notebooks/rfd4_proteina/RFD4_Proteina_FoldRun_Shim.ipynb`](./notebooks/rfd4_proteina/RFD4_Proteina_FoldRun_Shim.ipynb)<br>• [`notebooks/rfd4_proteina/README.md`](./notebooks/rfd4_proteina/README.md) | • **Original Examples & Repo**: [`nvidia/RFD4-Proteina`](https://huggingface.co/nvidia/RFD4-Proteina) ([`rfd4-proteina.org`](https://rfd4-proteina.org)) & [`NVIDIA-Digital-Bio/proteina`](https://github.com/NVIDIA-Digital-Bio/proteina) (`atomworks` / `rfproteina`)<br>• **Credits**: Geffner, Didi, Zhang, Reidenbach, Kreis, Vahdat *et al.*, **NVIDIA Digital Biology Research & IPD (UW)**<br>• **Citation**: Geffner *et al.*, *ICLR* (2025) ([arXiv:2503.00710](https://arxiv.org/abs/2503.00710))<br>• **License**: NVIDIA Open Model License / Apache 2.0 | • Mounts pre-staged `nvidia/RFD4-Proteina` model and latent autoencoder weights (~1.7 GB, downloaded with `HF_HUB_DISABLE_XET=1`) from `/mnt/nfs/foldrun/rfd4_proteina/cache/hub`.<br>• Demonstrates explicit fast mode (`compute_metrics=False`, skipping ProteinMPNN + Chai-1 refolding for ~3x faster generation) vs. full self-consistency (`--metrics` / `compute_metrics=True`), across all **3 generative design modes**: `binder_design` (`rfd4-generate` YAML & `4Z18` JSON), `motif_scaffolding`, and `unconditional_monomer` (`gpu_type="L4"`). | `<out_dir>/<stem>/`:<br>• `ranked_designs/ranked_{0..K}.cif` & `metrics.csv`<br>• `predictions/design_seed<seed>_<i>.cif` & `confidence_design_seed<seed>_<i>.json` |
| **BindCraft (`FreeBindCraft`)** | • [`notebooks/bindcraft/BindCraft_FoldRun_Shim.ipynb`](./notebooks/bindcraft/BindCraft_FoldRun_Shim.ipynb)<br>• [`notebooks/bindcraft/README.md`](./notebooks/bindcraft/README.md) | • **Original Notebook**: [`martinpacesa/BindCraft` `notebooks/BindCraft.ipynb`](https://github.com/martinpacesa/BindCraft/blob/main/notebooks/BindCraft.ipynb) ([Colab](https://colab.research.google.com/github/martinpacesa/BindCraft/blob/main/notebooks/BindCraft.ipynb)) & [`settings_target/PDL1.json`](https://github.com/martinpacesa/BindCraft/blob/main/settings_target/PDL1.json)<br>• **Credits**: Pacesa, Nickel, Schellhaas, Ovchinnikov, Correia *et al.*, **LPDI (EPFL) & ColabDesign (MIT/Harvard)**<br>• **Citation**: Pacesa *et al.*, *Nature* (2025) / *bioRxiv* (2024) ([doi:10.1101/2024.09.30.615802](https://doi.org/10.1101/2024.09.30.615802))<br>• **License**: MIT | • Shares `/mnt/nfs/foldrun/alphafold2/params` (`models: [af2, bindcraft]` in `databases.yaml`, ~5.3 GB AF2 pTM + multimer v3 weights with zero duplication) alongside `/mnt/nfs/foldrun/bindcraft/mpnn_weights` (SolubleMPNN & ProteinMPNN weights).<br>• Demonstrates local `.pdb`/`.cif` bundling (`"starting_pdb": "./target_pdl1.pdb"`), custom `--filters` / `--advanced` JSON bundling, `"design_path"` default output directory resolution, and server-side RCSB PDB resolution (`"starting_pdb": "4Z18"`) with `OpenMM` relaxation, `FASPR`, `FreeSASA` ($\Delta\text{SASA}$), and `sc-rs`. | `<out_dir>/<stem>/`:<br>• `Accepted/ranked_{0..K}.cif` & `final_design_stats.csv`<br>• `ranked_designs/ranked_{0..K}.cif` & `metrics.csv`<br>• `predictions/design_seed<seed>_<i>.cif` & `confidence_design_seed<seed>_<i>.json` |

### Where FoldRun Provides an Advantage Over Standard Local / Colab Execution

1. **Zero Local GPU, Commercial License, or Multi-Terabyte Database Setup**:
   - Eliminates 20–30 minute Colab Conda/CUDA/OpenMM/cuEquivariance/atomworks compilations, commercial PyRosetta license requirements (`FreeBindCraft --no-pyrosetta`), multi-GB model checkpoint downloads, and 2.5 TB database disk requirements. Any CPU-only laptop or free Colab runtime can drive H100 / A100 / L4 predictions and binder design campaigns.
2. **Private In-VPC SSD Filestore NFS Databases (`/mnt/nfs/foldrun`) & SHA-256 Caching**:
   - Replaces rate-limited public MSA servers (`api.colabfold.com`, which expose proprietary sequences and lack RNA `rfam`/`rnacentral` or full 1.8 TB BFD support) and slow HuggingFace cold-start downloads with private, high-IOPS `BASIC_SSD` Cloud Filestore NFS mounts, shared model weights (`alphafold_params` shared between `af2` and `bindcraft`), and content-addressed MSA/feature caches (`af2_features_cache/`, `boltz2_msas_cache/`, `of3_msas_cache/`).
3. **Parallel Multi-GPU / Multi-Seed Fan-Out on Vertex AI NVIDIA H100s, A100s & L4s**:
   - Dispatches AlphaFold 3 to dedicated scale-to-zero **NVIDIA H100 80 GB (`a3-highgpu-1g`)** Model Garden endpoints and fans out AlphaFold 2 model presets (`model_1..5` / multimer seeds) and Boltz-2, OpenFold 3, BoltzGen, RFD4-Proteina, and BindCraft `--num_model_seeds` (`1..50`) in parallel across dedicated **NVIDIA A100 40 GB (`a2-highgpu-1g`)**, **NVIDIA A100 80 GB (`a2-ultragpu-1g`)**, and **NVIDIA L4 24 GB (`g2-standard-12`)** GPUs via Vertex AI `dsl.ParallelFor` with Dynamic Workload Scheduler (`FLEX_START`), avoiding sequential single-GPU bottlenecks and 16 GB T4 VRAM OOMs.
4. **Automatic Crystallographic Hotspot Mapping (`gemmi`) & Container Safeguards**:
   - Automatically maps crystallographic `auth_seq_id` hotspot numbers onto canonical 1-indexed `label_seq_id` (`C_HOT` in RFD4-Proteina and `binding_types` in BoltzGen), sanitizes `NaN` B-factors in generated `.cif` files, and injects `sitecustomize.py` / environment safeguards (`PROTEINA_DISABLE_CUSTOM_KERNELS=1`, `CUEQUIVARIANCE_OPS_FALLBACK=1`, PyTorch 2.6+ `weights_only=False`, OpenMM CPU relaxation fallback) to prevent runtime driver and library crashes.
5. **Disconnect & Kernel-Restart Resilience (`.foldrun_job.json`) + Native Output Reconstruction & 3D Viewer**:
   - Persists `<out_dir>/.foldrun_job.json` immediately on `202 Accepted` so re-running a cell after a laptop sleep or Colab disconnect automatically re-attaches to the active Vertex AI `PipelineJob` (without cancelling running jobs or triggering redundant GPU preflight checks), reconstructs each model's native local output directory via 15-minute V4 Signed URLs (with automatic `?stream=true` fallback), and provides a direct link to the hosted **3D FoldRun Viewer** (`job.viewer_url`).
