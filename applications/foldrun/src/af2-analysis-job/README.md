# AF2 Analysis Job — Cloud Run Jobs Service

Parallel analysis service for AlphaFold2 predictions using Cloud Run Jobs.

## Overview

This Cloud Run Jobs service performs parallel batch analysis of AlphaFold2 predictions. It analyzes multiple prediction files simultaneously (up to 25 tasks in parallel), calculating quality metrics and generating comprehensive reports.

**Performance**: Analyzes 25 predictions in ~60 seconds (vs. ~52 minutes sequential).

## Features

- **Per-Residue Metrics**: pLDDT scores and confidence distributions
- **PAE Analysis**: Predicted Aligned Error matrices
- **Quality Assessment**: Automated quality categorization
- **Expert Analysis**: Gemini-powered structural insights
- **Consolidation**: Aggregates results and generates summary with recommendations

## Deployment

This service is deployed automatically by `deploy-all.sh` via Cloud Build. For standalone deployment:

```bash
./deploy.sh
```

The script reads `PROJECT_ID` from environment variables or `gcloud config`.

## Environment Variables

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `GCS_BUCKET` | GCS bucket for data | Yes | — |
| `PROJECT_ID` | Google Cloud project | Yes | — |
| `REGION` | Compute region | Yes | `us-central1` |
| `ANALYSIS_PATH` | GCS path to analysis directory | Yes | — |
| `GEMINI_MODEL` | Gemini model for expert analysis | No | `gemini-3-pro-preview` |

## Output Structure

Analysis results are written to GCS alongside the AlphaFold predictions:

```
gs://BUCKET/pipeline_runs/TIMESTAMP/JOB_ID/alphafold-inference-pipeline-TIMESTAMP/
├── alphafold_prediction.pkl
├── alphafold_prediction_*.pdb
├── ranking_debug.json
└── analysis/
    ├── task_config.json
    ├── prediction_0_analysis.json
    ├── prediction_1_analysis.json
    ├── ...
    └── summary.json                 # Consolidated summary with expert analysis
```

## How It Works

1. **Task Distribution**: Each Cloud Run task reads `task_config.json`, selects its prediction via `CLOUD_RUN_TASK_INDEX`, and runs independently
2. **Analysis**: Loads raw prediction pickle, calculates pLDDT/PAE metrics, categorizes quality
3. **Consolidation**: The last task aggregates all results, ranks predictions, generates Gemini expert analysis, and writes `summary.json`

## Related Services

- [foldrun-agent](../../foldrun-agent) — AI agent that triggers analysis jobs
- [foldrun-viewer](../foldrun-viewer) — Web viewer for analysis results (AF2 + OF3)
