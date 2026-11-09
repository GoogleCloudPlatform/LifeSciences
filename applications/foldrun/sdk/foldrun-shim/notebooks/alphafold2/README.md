# AlphaFold 2 on FoldRun via `foldrun-shim` (`AlphaFold2_FoldRun_Shim.ipynb`)

This directory contains [`AlphaFold2_FoldRun_Shim.ipynb`](./AlphaFold2_FoldRun_Shim.ipynb), an adaptation of Google DeepMind's official [`notebooks/AlphaFold.ipynb`](https://github.com/google-deepmind/alphafold/blob/v2.3.2/notebooks/AlphaFold.ipynb) (`google-deepmind/alphafold` v2.3.2) modified to execute through **`foldrun-shim`** and the **FoldRun Direct Cloud Run HTTP Gateway (`foldrun-gateway`)**.


---

## Original Notebook Reference, Credits & Citations

- **Original Public Notebook**: [Google DeepMind `notebooks/AlphaFold.ipynb` (v2.3.2)](https://github.com/google-deepmind/alphafold/blob/v2.3.2/notebooks/AlphaFold.ipynb) ([Open Original in Colab](https://colab.research.google.com/github/deepmind/alphafold/blob/main/notebooks/AlphaFold.ipynb))
- **Upstream Repository**: [`google-deepmind/alphafold`](https://github.com/google-deepmind/alphafold)
- **Authors & Credits**: John Jumper, Richard Evans, Alexander Pritzel, Tim Green, Michael Figurnov, Olaf Ronneberger, Kathryn Tunyasuvunakool, Russ Bates, Augustin Žídek, Anna Potapenko, Alex Bridgland, Clemens Meyer, Simon A. A. Kohl, Andrew J. Ballard, Andrew Cowie, Bernardino Romera-Paredes, Stanislav Nikolov, Rishub Jain, Jonas Adler, Trevor Back, Stig Petersen, David Reiman, Ellen Clancy, Michal Zielinski, Martin Steinegger, Michalina Pacholska, Tamas Berghammer, Sebastian Bodenstein, David Silver, Oriol Vinyals, Andrew W. Senior, Koray Kavukcuoglu, Pushmeet Kohli, and Demis Hassabis (**Google DeepMind**).
- **License**: Apache License 2.0 (source code) / CC-BY 4.0 (AlphaFold 2 model parameters).
- **Primary Citations**:
  1. Jumper, J. *et al.* "Highly accurate protein structure prediction with AlphaFold." *Nature* 596, 583–589 (2021). [doi:10.1038/s41586-021-03819-2](https://doi.org/10.1038/s41586-021-03819-2)
  2. Evans, R. *et al.* "Protein complex prediction with AlphaFold-Multimer." *bioRxiv* (2021). [doi:10.1101/2021.10.04.463034](https://doi.org/10.1101/2021.10.04.463034)

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

## Part A — Original DeepMind `AlphaFold.ipynb` Form & Visualization Workflow

### 1. Canonical Sequence Form
The notebook retains DeepMind's exact interactive Colab form with the canonical default sequence:
```python
sequence_1 = 'MAAHKGAEHHHKAAEHHEQAAKHHHAAAEHHEKGEHEQAAHHADTAYAHHKHAEEHAAQAAKHDAEHHAPKPH' #@param {type:"string"}
```
Supports both Monomer (`sequence_1`) and Multimer (`sequence_1..8`) prediction with full input length and standard amino acid validation.

### 2. Downstream 3D Structure & Confidence Visualization
- Interactive 3D visualization using `py3Dmol`, colored by pLDDT confidence bands or by chain for multimers.
- Multi-model pLDDT plots across all 5 models and 2D Predicted Aligned Error (PAE) matrix heatmaps.
- Hosted 3D FoldRun Viewer link (`job.viewer_url`).

---

## Part B — FoldRun Additions (`!run_alphafold.py` CLI, Full 2.6 TB `/mnt/nfs/foldrun` Genetic Databases vs. DeepMind's Reduced Colab Databases, and Multi-Seed Vertex AI GPU Execution)

### 1. Full 2.6 TB Genetic Databases on Filestore NFS (`/mnt/nfs/foldrun`)
In DeepMind's original Colab, database searches were restricted to `small_bfd` over HTTP chunk streaming because full databases could not fit on Colab disks, and templates were disabled (`num_templates=0`).
FoldRun mounts full 2.6 TB databases on Cloud Filestore NFS:
- `uniref90` (`uniref90.fasta`)
- `mgnify` (`mgy_clusters_2022_05.fa`)
- `bfd` (full 1.8 TB `bfd_metaclust_clu_complete_id30_c90_final_seq.sorted_opt`)
- `uniref30` (206 GB `uniref30_2202`)
- `pdb70` / `pdb_seqres`
- `pdb_mmcif` structural templates (`mmcif_files/`)
- Pre-mounted 5.3 GB parameters at `/mnt/nfs/foldrun/alphafold2/params`

### 2. Drop-In Native CLI (`!run_alphafold.py`) & Host Flag Stripping
`foldrun-shim` intercepts standard `run_alphafold.py` invocations:
```bash
!run_alphafold.py \
  --fasta_paths=./inputs/target.fasta \
  --output_dir=./af2_results \
  --model_preset=monomer_ptm \
  --db_preset=full_dbs \
  --max_template_date=2025-04-01 \
  --models_to_relax=best \
  --data_dir=/mnt/nfs/foldrun \
  --use_gpu_relax=true
```
`_strip_local_alphafold2_args` strips host-specific paths (`--data_dir`, `--*_database_path`, `--*_binary_path`, `--use_gpu_relax`) and forwards clean scientific parameters to `foldrun-gateway`.

### 3. Programmatic Python Helper API (`alphafold2.predict`)
- Clean Python execution: `job = alphafold2.predict(fasta_paths="./inputs/target.fasta", output_dir="./af2_results", ...)`
- Displays rich `GatewayJobResult` HTML status card (`job._repr_html_()`).

### 4. Multi-Seed Vertex AI GPU Fan-Out & Disconnect Recovery
- Parallel execution on NVIDIA A100 GPUs with DWS `FLEX_START`.
- Interruption recovery via `<out_dir>/.foldrun_job.json` and server-side `input_sha256` deduplication.
