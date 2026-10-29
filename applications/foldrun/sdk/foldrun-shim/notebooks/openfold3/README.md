# OpenFold 3 on FoldRun via `foldrun-shim` (`OpenFold3_FoldRun_Shim.ipynb`)

This directory contains [`OpenFold3_FoldRun_Shim.ipynb`](./OpenFold3_FoldRun_Shim.ipynb), an end-to-end Jupyter / Google Colab notebook adapted from the official [**AlQuraishi Lab OpenFold 3 repository (`aqlaboratory/openfold-3`)**](https://github.com/aqlaboratory/openfold-3) (`examples/example_inference_inputs/`, `examples/example_runner_yamls/`) and the public [OpenFold Colab Notebook (`aqlaboratory/openfold`)](https://github.com/aqlaboratory/openfold/blob/main/notebooks/OpenFold.ipynb).


---

## Original Notebook Reference, Credits & Citations

- **Original Public Notebook & Examples**:
  - Official OpenFold Colab Notebook: [`aqlaboratory/openfold` `notebooks/OpenFold.ipynb`](https://github.com/aqlaboratory/openfold/blob/main/notebooks/OpenFold.ipynb) ([Open Original in Colab](https://colab.research.google.com/github/aqlaboratory/openfold/blob/main/notebooks/OpenFold.ipynb))
  - Official OpenFold 3 Inference Walkthrough & Examples: [`aqlaboratory/openfold-3/examples/example_inference_inputs/`](https://github.com/aqlaboratory/openfold-3/tree/main/examples/example_inference_inputs)
- **Upstream Repository**: [`aqlaboratory/openfold-3`](https://github.com/aqlaboratory/openfold-3)
- **Authors & Credits**: Gustaf Ahdritz, Nazim Bouatta, Christina Floristean, Sachin Kadyan, Qisheng Xia, William Gerecke, Timothy J. O'Donnell, Daniel Berenberg, Ian Fisk, Niccolò Zanichelli, Bo Zhang, Arkadiusz Nowaczynski, Bei Wang, Marta M. Stepniewska-Dziubinska, Shang Zhang, Adegoke Ojewole, Murat Efe Guney, Stella Biderman, Andrew M. Watkins, Stephen Ra, Pablo Ribalta Lorenzo, Lucas Nivon, Brian Weitzner, Yih-En Andrew Ban, Mohammed AlQuraishi, and the **OpenFold Consortium** (**AlQuraishi Lab, Columbia University**).
- **License**: Apache License 2.0.
- **Primary Citations**:
  1. AlQuraishi Lab & OpenFold Consortium. "OpenFold3: An Open-Source All-Atom Biomolecular Structure Prediction Model." (2025). [github.com/aqlaboratory/openfold-3](https://github.com/aqlaboratory/openfold-3)
  2. Ahdritz, G. *et al.* "OpenFold: retraining AlphaFold2 yields new insights into its learning mechanisms and capacity for generalization." *Nature Methods* 21, 1514–1524 (2024). [doi:10.1038/s41592-024-02272-z](https://doi.org/10.1038/s41592-024-02272-z)

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

### Original `aqlaboratory/openfold-3` Upstream Examples
The notebook writes out the exact upstream examples:
- `query_ubiquitin.json`: Monomer protein query (`MQIFVKTL...`).
- `query_protein_ligand.json`: Protein + small-molecule ligand SMILES query (`CC(=O)OC1C[NH+]2CCC1CC2`).

### Verbatim CLI Command Execution
```bash
!run_openfold predict --query_json=query_ubiquitin.json --runner_yaml=runner.yaml --output_dir=./of3_ubiquitin_out
```

---

## Part B — FoldRun Additions (`foldrun-shim` Python API, `/mnt/nfs/foldrun` Shared Databases & Weights, Local Asset Bundling, and Multi-GPU Seed Parallelism)

### 1. Full 9-Database Complex on Filestore NFS (`/mnt/nfs/foldrun`)
Demonstrates the full multi-modal `rnp_atp_complex` query exercising all 9 reference databases:
- Protein: `jackhmmer` against `uniref90`, `mgnify`, and `pdb_seqres`
- RNA: `nhmmer` against `rfam` (`rfam.fasta`) and `rnacentral` (`rnacentral.fasta`)
- Structural templates: `pdb_mmcif/mmcif_files`
- Chemical Component Dictionary: `of3/ccd/components.bcif` (e.g. `ccd_codes: ["ATP"]`)
- Pre-mounted model weights: `/mnt/nfs/foldrun/of3/params/of3-p2-155k.pt`

### 2. Local Pre-Computed MSA (`.a3m`) & Stockholm (`.sto`) Bundling
Automatically bundles local alignments into `assets.tar.gz` and rewrites query JSON paths to GCS FUSE paths.

### 3. Multi-Seed Parallel Vertex AI GPU Execution
Fans out `--num_model_seeds=5` in parallel across dedicated NVIDIA A100 GPUs with DWS `FLEX_START`.

### 4. Programmatic Python Helper API (`openfold3.predict`)
- Clean Python execution with rich `GatewayJobResult` HTML status card and direct link to 3D FoldRun Viewer (`job.viewer_url`).
