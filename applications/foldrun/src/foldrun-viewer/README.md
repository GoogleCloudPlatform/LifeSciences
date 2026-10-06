# FoldRun Structure Viewer

Flask web application for visualizing biomolecular structure predictions from **AlphaFold 2**, **AlphaFold 3**, **OpenFold 3**, and **Boltz-2** with interactive 3D rendering, confidence metrics, multimodal **Gemini 3.1 Pro** Expert Analysis, and 1-click ZIP artifact bundle downloads.

## Features

- **4-Model Support:** Auto-detects `alphafold2`, `alphafold3`, `openfold3`, and `boltz2` from `analysis/summary.json`
- **Interactive 3D Visualization:** Structures rendered with 3Dmol.js (`.pdb` for AF2, all-atom `.cif` / mmCIF for AF3, OF3, and Boltz-2)
- **Ligand, Ion & Nucleic Acid Rendering:** Proteins/nucleic acids as cartoon, small-molecule ligands (CCD/SMILES) as ball+stick, and metal ions (`MG`, `ZN`, `CA`, `FE`) as spacefill spheres
- **pLDDT Confidence Coloring:** Standard AlphaFold color scale (`>90` dark blue, `70–90` light blue, `50–70` yellow, `<50` orange)
- **Per-Chain Confidence & Composition Table:** Polymer residue counts and ligand CCD/SMILES breakdown
- **Analysis Plots:** Per-residue pLDDT profiles, PAE/PDE heatmaps, and chain-pair ipTM matrices
- **Multimodal Gemini 3.1 Pro Expert Analysis:** 8-section AI structural biology assessment (`status: "success"`) rendered as formatted Markdown
- **1-Click Artifact Downloads:** Download the complete job bundle (`artifacts_bundle.zip` — structure, plots, `expert_analysis.md`, `execution.log`, `input.json`, `summary.json`) or individual files

## Architecture

```
foldrun-viewer/
├── app.py                 # Flask app with GCS integration, AF3 resolution & ZIP bundle streaming
├── templates/
│   ├── index.html         # Job dashboard landing page
│   └── combined.html      # Unified structure + analysis + Gemini Expert Analysis viewer
├── requirements.txt       # Python dependencies
├── Dockerfile             # Cloud Run container
└── deploy.sh              # Deployment script
```

## Deployment

```bash
cd src/foldrun-viewer

# Auto-deploy (reads PROJECT_ID from gcloud config)
./deploy.sh

# Or via top-level deploy-all.sh
./deploy-all.sh YOUR_PROJECT_ID us-central1 --steps build --build-target viewer
```

## Usage

```
# Short URLs (auto-resolve analysis from KFP pipeline job ID or AF3 job name)
https://foldrun-viewer-HASH.run.app/job/alphafold3-inference-pipeline-20261006171144
https://foldrun-viewer-HASH.run.app/job/af3_zinc_finger_dna
https://foldrun-viewer-HASH.run.app/job/alphafold-inference-pipeline-20260307165005
https://foldrun-viewer-HASH.run.app/job/openfold3-inference-pipeline-20260308054830
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `/job/<job_id>` | Short URL — resolves `pipeline_runs/` or `af3_predictions/` and redirects to `/combined` |
| `/combined` | Combined 3D structure + plots + Gemini Expert Analysis viewer |
| `/api/jobs` | List all pipeline jobs and AF3 predictions with quality badges |
| `/api/pdb?uri=gs://...` | Fetch PDB content from GCS (AF2) |
| `/api/cif?uri=gs://...` | Fetch mmCIF content from GCS (AF3, OF3, Boltz-2) |
| `/api/analysis?job_id=...` | Fetch `analysis/summary.json` |
| `/api/image?uri=gs://...` | Fetch `plddt_plot` / `pae_plot` PNGs from GCS |
| `/api/download/bundle?job_id=...` | Download complete `artifacts_bundle.zip` (cached in GCS) |
| `/api/download/file?job_id=...&kind=...` | Download individual artifact (`structure`, `plddt_plot`, `pae_plot`, `expert_report`, `summary_json`) |
| `/health` | Health check |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PROJECT_ID` | Yes | Google Cloud project ID |
| `BUCKET_NAME` | Yes | GCS bucket for prediction results |
| `REGION` | No | GCP region (default: `us-central1`) |
| `PORT` | No | Server port (default: `8080`, set by Cloud Run) |
