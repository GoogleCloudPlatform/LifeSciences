# AlphaFold 3 on FoldRun via `foldrun-shim` (`AlphaFold3_FoldRun_Shim.ipynb`)

This directory contains [`AlphaFold3_FoldRun_Shim.ipynb`](./AlphaFold3_FoldRun_Shim.ipynb), an end-to-end Jupyter / Google Colab notebook demonstrating **AlphaFold 3 structure prediction** via **`foldrun-shim`** and **Vertex AI Model Garden**.

---

## Model & License Reference

- **Upstream Model**: AlphaFold 3 (`publishers/google/models/1000376@v3_0_4`) on Google Cloud Vertex AI Model Garden.
- **Upstream Authors**: Josh Abramson, Jonas Adler, Jack Dunger, Richard Evans, Tim Green, Alexander Pritzel, Olaf Ronneberger, Lindsay Willmore, Andrew J. Ballard, Joshua Bambrick, et al., and the **AlphaFold 3 Team** (**Google DeepMind** & **Isomorphic Labs**).
- **Primary Citation**:
  - Abramson, J. *et al.* "Accurate structure prediction of biomolecular interactions with AlphaFold 3." *Nature* 630, 493–500 (2024). [doi:10.1038/s41586-024-07487-w](https://doi.org/10.1038/s41586-024-07487-w)
- **License & Terms of Use**:
  - Predicted structures are governed by the **AlphaFold 3 Output Terms of Use** (CC BY-NC-SA 4.0, non-commercial research use).
  - Model weights are governed by the **AlphaFold 3 Model Parameters Terms of Use**.

---

## 1. Prerequisites, Authentication & Installing `foldrun-shim` from GCS

1. **Authenticate to Google Cloud**:
   ```bash
   gcloud auth login
   ```
2. **Download & Install the Shim**:
   ```bash
   gcloud storage cp gs://<PROJECT_ID>-foldrun-data/sdk/foldrun_shim-0.1.0-py3-none-any.whl /tmp/
   pip install /tmp/foldrun_shim-0.1.0-py3-none-any.whl
   ```
3. **Configure the Gateway URL**:
   ```bash
   export FOLDRUN_GATEWAY_URL="https://foldrun-gateway-<hash>-uc.a.run.app"
   ```

---

## Part A — Upstream Input & CLI Execution

### Input Format
AlphaFold 3 expects standard JSON inputs (`dialect: "alphafold3"`, `version: 1`). When running inference-only with `--run_data_pipeline=false` (`--norun_data_pipeline`), AlphaFold 3 requires explicit empty `"unpairedMsa": ""`, `"pairedMsa": ""`, and `"templates": []` fields on protein chains (or local `unpairedMsaPath`, `pairedMsaPath`, and `templates[*].mmcifPath` files, which `foldrun-shim` automatically bundles into `assets.tar.gz`):
```json
{
  "name": "troponin_c_human",
  "modelSeeds": [1],
  "sequences": [
    {
      "protein": {
        "id": "A",
        "sequence": "MDDIYKAAVEQLTEEQKNEFKAAFDIFVLGAEDGCISTKELGKVMRMLGQNPTPEELQEMIDEVDEDGSGTVDFDEFLVMMVRCMKDDSKGKSEEELSDLFRMFDKNADGYIDLDELKIMLQATGETITEDDIEELMKDGDKNNDGRIDYDEFLEFMKGVE",
        "unpairedMsa": "",
        "pairedMsa": "",
        "templates": []
      }
    }
  ],
  "dialect": "alphafold3",
  "version": 1
}
```

### CLI Execution
```bash
# Via dedicated alphafold3 entrypoint or upstream-compatible run_alphafold.py --json_path=...
alphafold3 --json_path=troponin_c.json --output_dir=./af3_troponin_results --num_diffusion_samples=1 --run_data_pipeline=false
run_alphafold.py --json_path=troponin_c.json --output_dir=./af3_troponin_results --num_diffusion_samples=1 --norun_data_pipeline
```

---

## Part B — FoldRun Python Helper API (`alphafold3.predict`)

```python
from foldrun_shim import alphafold3

# Run prediction programmatically using the Python helper
result = alphafold3.predict(
    json_path="troponin_c.json",
    output_dir="./af3_troponin_results",
    num_diffusion_samples=1,
    run_data_pipeline=False,
)

# Display result in Jupyter with direct FoldRun 3D Viewer link
result
```
