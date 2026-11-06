# BoltzGen on FoldRun via `foldrun-shim` (`BoltzGen_FoldRun_Shim.ipynb`)

This directory contains [`BoltzGen_FoldRun_Shim.ipynb`](./BoltzGen_FoldRun_Shim.ipynb), a modified Jupyter / Colab notebook adapted from the official [**`HannesStark/boltzgen`**](https://github.com/HannesStark/boltzgen) ([`boltz-community/boltzgen`](https://huggingface.co/boltzgen/boltzgen-1)) design specification examples (`example/vanilla_protein/`, `example/vanilla_peptide_with_target_binding_site/`, `yaml_spec.md`) and public BoltzGen notebook workflows. It executes universal biomolecular binder design (`protein-anything`, `peptide-anything`, `protein-small_molecule`, `nanobody-anything`, `antibody-anything`) on FoldRun's auto-scaling Google Cloud Vertex AI A100 Pipelines via `foldrun-shim` and `foldrun-gateway`.


---

## Original Notebook Reference, Credits & Citations

- **Original Public Examples & Design Specifications**:
  - Official [`HannesStark/boltzgen` Design Spec (`yaml_spec.md`)](https://github.com/HannesStark/boltzgen/blob/main/yaml_spec.md) and Example Workflows ([`example/vanilla_protein/`](https://github.com/HannesStark/boltzgen/tree/main/example/vanilla_protein), [`example/vanilla_peptide_with_target_binding_site/`](https://github.com/HannesStark/boltzgen/tree/main/example/vanilla_peptide_with_target_binding_site), [`example/small_molecule/`](https://github.com/HannesStark/boltzgen/tree/main/example/small_molecule), [`example/nanobody/`](https://github.com/HannesStark/boltzgen/tree/main/example/nanobody))
  - Official HuggingFace Model & Dataset Cards: [`boltzgen/boltzgen-1`](https://huggingface.co/boltzgen/boltzgen-1) and [`boltzgen/inference-data`](https://huggingface.co/datasets/boltzgen/inference-data)
- **Upstream Repository**: [`HannesStark/boltzgen`](https://github.com/HannesStark/boltzgen) (`boltz-community/boltzgen`)
- **Authors & Credits**: Hannes Stark, Gabriele Corso, Jeremy Wohlwend, Saro Passaro, Regina Barzilay, Tommi Jaakkola, and the **Boltz Community** (**MIT Jameel Clinic / CSAIL**).
- **License**: MIT License.
- **Primary Citations**:
  1. Stark, H., Corso, G., Wohlwend, J., Passaro, S., Barzilay, R., & Jaakkola, T. "BoltzGen: All-Atom Generative Biomolecular Binder Design." (2025). [github.com/HannesStark/boltzgen](https://github.com/HannesStark/boltzgen)
  2. Passaro, S., Corso, G., Wohlwend, J. *et al.* "Boltz-2: Towards Accurate and Efficient Binding Affinity Prediction." *bioRxiv* (2025). [doi:10.1101/2025.06.14.659707](https://doi.org/10.1101/2025.06.14.659707)

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

### Original `HannesStark/boltzgen` Upstream Quickstart
The notebook writes out the exact upstream example `example/vanilla_protein/1g13prot.yaml`:
```yaml
entities:
  - protein:
      id: A
      sequence: 100..120
  - file:
      path: 1G13
```

### Verbatim CLI Command Execution
```bash
!boltzgen run example/vanilla_protein/1g13prot.yaml --output workbench/test_run --protocol protein-anything --num_designs 10 --budget 2
```

---

## Part B — FoldRun Additions (`foldrun-shim` Python API, `/mnt/nfs/foldrun` Shared Databases & Weights, Local Asset Bundling, and Multi-GPU Seed Parallelism)

### 1. Automatic 4-Character RCSB PDB Auto-Resolution (`path: 4Z18` / `path: 1G13`)
Server-side `target_resolver.py` resolves 4-character PDB codes directly against `/mnt/nfs/foldrun/pdb_mmcif`, eliminating manual structure downloads.

### 2. Local Asset Bundling (`assets.tar.gz`)
Packages custom local `.cif` or `.pdb` files (`path: ./custom_target.cif`) into `assets.tar.gz` and rewrites YAML paths to GCS FUSE paths.

### 3. Multi-Protocol Capabilities
Demonstrates all major BoltzGen design protocols:
- `protein-anything`: De novo protein binder design.
- `nanobody-anything` / `antibody-anything`: Single-domain antibody / CDR-loop design.
- `protein-small_molecule`: Designing protein pockets around small-molecule ligands (`ccd: SAH`).

### 4. Multi-Seed Parallel Vertex AI GPU Execution
Fans out `--num_model_seeds=5` in parallel across dedicated NVIDIA A100 GPUs with DWS `FLEX_START`.

### 5. Programmatic Python Helper API (`boltzgen.predict`)
- Clean Python execution with rich `GatewayJobResult` HTML status card and direct link to 3D FoldRun Viewer (`job.viewer_url`).
