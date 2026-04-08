# FoldRun Structure Viewer

Flask web application for visualizing protein structure predictions from AlphaFold2 and OpenFold3 with interactive 3D rendering, confidence metrics, and Gemini expert analysis.

## Features

- **Multi-Model Support:** Auto-detects AF2 vs OF3 from analysis summary
- **Interactive 3D Visualization:** Structures rendered with 3Dmol.js (PDB for AF2, CIF for OF3)
- **Ligand Rendering:** Protein as cartoon, ligands as ball+stick (auto-detected from HETATM)
- **pLDDT Confidence Coloring:** Color-coded by prediction confidence
- **Per-Chain Confidence Table:** Protein vs ligand pLDDT breakdown (OF3)
- **Analysis Plots:** pLDDT per-chain, PDE/PAE heatmaps with chain boundaries, ipTM matrix
- **Gemini Expert Analysis:** AI-generated structural assessment (rendered Markdown)
- **Input Query JSON:** Copyable OF3 query JSON for resubmission (OF3)
- **Compact Job Summary:** Pill-style header with model type, status, duration, labels

## Architecture

```
foldrun-viewer/
├── app.py                 # Flask app with GCS integration + /api/cif endpoint
├── templates/
│   ├── index.html        # Landing page
│   └── combined.html     # Combined structure + analysis viewer (AF2 + OF3)
├── requirements.txt      # Python dependencies
├── Dockerfile           # Cloud Run container
└── deploy.sh            # Deployment script
```

## Deployment

```bash
cd src/foldrun-viewer

# Auto-deploy (reads PROJECT_ID from gcloud config)
./deploy.sh

# Or explicit project
PROJECT_ID=my-project-id ./deploy.sh
```

## Usage

```
# Short URL (auto-resolves analysis from job ID)
https://foldrun-viewer-HASH.run.app/job/alphafold-inference-pipeline-20260307165005
https://foldrun-viewer-HASH.run.app/job/openfold3-inference-pipeline-20260308054830
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `/job/<job_id>` | Short URL — redirects to combined viewer |
| `/combined` | Combined structure + analysis viewer |
| `/api/pdb?uri=gs://...` | Fetch PDB content from GCS (AF2) |
| `/api/cif?uri=gs://...` | Fetch CIF content from GCS (OF3) |
| `/api/analysis?job_id=...` | Fetch analysis summary JSON |
| `/api/image?uri=gs://...` | Fetch plot images from GCS |
| `/health` | Health check |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PROJECT_ID` | Yes | Google Cloud project ID |
| `BUCKET_NAME` | Yes | GCS bucket for prediction results |
| `REGION` | No | GCP region (default: us-central1) |
| `PORT` | No | Server port (default: 8080, set by Cloud Run) |
