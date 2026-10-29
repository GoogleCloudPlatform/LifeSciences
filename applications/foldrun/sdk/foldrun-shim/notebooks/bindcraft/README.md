# BindCraft (`FreeBindCraft`) on FoldRun via `foldrun-shim` (`BindCraft_FoldRun_Shim.ipynb`)

This directory contains [`BindCraft_FoldRun_Shim.ipynb`](./BindCraft_FoldRun_Shim.ipynb), a modified Jupyter / Colab notebook adapted from the official [**`martinpacesa/BindCraft`**](https://github.com/martinpacesa/BindCraft) Colab notebook ([`notebooks/BindCraft.ipynb`](https://github.com/martinpacesa/BindCraft/blob/main/notebooks/BindCraft.ipynb), [`settings_target/PDL1.json`](https://github.com/martinpacesa/BindCraft/blob/main/settings_target/PDL1.json), [`settings_advanced/default_4stage_multimer.json`](https://github.com/martinpacesa/BindCraft/blob/main/settings_advanced/default_4stage_multimer.json), and [`settings_filters/default_filters.json`](https://github.com/martinpacesa/BindCraft/blob/main/settings_filters/default_filters.json)) and **FreeBindCraft (`--no-pyrosetta`)**. It executes *de novo* protein binder hallucination, SolubleMPNN sequence redesign, and biophysical interface scoring on FoldRun's auto-scaling Google Cloud Vertex AI A100 Pipelines via `foldrun-shim` and `foldrun-gateway`.


---

## Original Notebook Reference, Credits & Citations

- **Original Public Notebook**:
  - Official BindCraft Colab Notebook: [`martinpacesa/BindCraft` `notebooks/BindCraft.ipynb`](https://github.com/martinpacesa/BindCraft/blob/main/notebooks/BindCraft.ipynb) ([Open Original in Colab](https://colab.research.google.com/github/martinpacesa/BindCraft/blob/main/notebooks/BindCraft.ipynb))
  - Upstream Target & Filter Settings: [`settings_target/PDL1.json`](https://github.com/martinpacesa/BindCraft/blob/main/settings_target/PDL1.json), [`settings_advanced/default_4stage_multimer.json`](https://github.com/martinpacesa/BindCraft/blob/main/settings_advanced/default_4stage_multimer.json), [`settings_filters/default_filters.json`](https://github.com/martinpacesa/BindCraft/blob/main/settings_filters/default_filters.json)
- **Upstream Repositories**: [`martinpacesa/BindCraft`](https://github.com/martinpacesa/BindCraft) & [`sokrypton/ColabDesign`](https://github.com/sokrypton/ColabDesign) (plus PyRosetta-free `FreeBindCraft` open-source scoring via `OpenMM`, `FASPR`, `FreeSASA`, and `sc-rs`)
- **Authors & Credits**: Martin Pacesa, Lennart Nickel, Christian Schellhaas, Joseph Schmidt, Ekaterina Pyatova, Lucas Kissling, Patrick Barendse, Jagrity Choudhury, Srajan Kapoor, Ana Alcaraz-Serna, Yehlin Cho, Kourosh H. Ghamary, Laura Vinué, Brahm J. Yachnin, Andrew M. Wollacott, Stephen Buckley, Sandrine Georgeon, Casper A. Goverde, Georgios N. Hatzopoulos, Pierre Gönczy, Y. D. Muller, G. Schwank, D. C. Swarts, Sergey Ovchinnikov, and Bruno E. Correia (**Laboratory of Protein Design and Immunoengineering (LPDI), EPFL & MIT / Harvard ColabDesign**).
- **License**: MIT License.
- **Primary Citation**:
  1. Pacesa, M., Nickel, L., Schellhaas, C. *et al.* "BindCraft: one-shot design of functional protein binders." *Nature* (2025) / *bioRxiv* (2024). [doi:10.1101/2024.09.30.615802](https://doi.org/10.1101/2024.09.30.615802)

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

### Original `martinpacesa/BindCraft` Upstream `PDL1.json` Example & CLI
In the upstream repository, binder design campaigns run via the command line with local PDB files and JSON settings:
```bash
!bindcraft --settings './settings_target/PDL1.json' --filters './settings_filters/default_filters.json' --advanced './settings_advanced/default_4stage_multimer.json'
```
Standard upstream BindCraft requires local PyRosetta commercial licenses, local AlphaFold 2 weights (5.3 GB), and local SolubleMPNN weights.

---

## Part B — FoldRun Additions (`foldrun-shim` Python API, `/mnt/nfs/foldrun` Shared Databases & Weights, Local Asset Bundling, and Multi-GPU Seed Parallelism)

### 1. Server-Side RCSB PDB Resolution (`starting_pdb: "4Z18"`)
Allows passing 4-character RCSB PDB IDs directly in `starting_pdb`, resolved server-side against `/mnt/nfs/foldrun/pdb_mmcif` without requiring local structure downloads.

### 2. Pre-Mounted AF2 & MPNN Weights on Filestore NFS
Mounts `/mnt/nfs/foldrun/alphafold2/params` and `/mnt/nfs/foldrun/bindcraft/mpnn_weights` across all GPU workers.

### 3. Multi-GPU Parallel Execution (`--num_concurrent_tasks=4` / `--num_model_seeds=5`)
Distributes independent binder design trajectories in parallel across dedicated NVIDIA A100 GPUs with DWS `FLEX_START`.

### 4. FreeBindCraft Open-Source Scoring (`--no-pyrosetta`)
Replaces commercial PyRosetta with open-source `OpenMM` relaxation, `FASPR` side-chain packing, `FreeSASA` buried surface area ($\Delta	ext{SASA}$), and `sc-rs` shape complementarity.

### 5. Programmatic Python Helper API (`bindcraft.predict`)
- Clean Python execution with rich `GatewayJobResult` HTML status card and direct link to 3D FoldRun Viewer (`job.viewer_url`).
