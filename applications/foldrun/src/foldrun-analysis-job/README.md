# FoldRun Unified Analysis Job — Cloud Run Job

Parallel prediction analysis service supporting AlphaFold2, Boltz-2, and OpenFold3 models.

## Overview

This service consolidates prediction post-processing, scoring metrics, quality breakdowns, static plots (pLDDT, PDE error, ipTM matrices), and Gemini expert evaluation into a single, unified post-prediction Post-Processing step.

Post-processing execution is performed asynchronously and concurrently (up to 25 parallel tasks per prediction run).

## Features

- **Unified Post-Processing Entrypoint**: Auto-detects or routes explicitly via the `MODEL_TYPE` environment variable.
- **Model-Specific Calculations**:
  - **AlphaFold2**: Aggregates pickle outputs, calculates standard pLDDT Stats/PAE stats.
  - **Boltz-2**: Parses atomic-level CIF details directly from B-factors, Remaps string index entities to letters, handles PDEs and affinity predictions.
  - **OpenFold3**: Post-processes JSON confidences and PDE heatmaps.
- **Gemini Expert Review**: Deep Gemini expert insight generation tailormade per architecture.
- **Consolidation**: Final task combines individual posts into a single structured post-run report (`summary.json`).

## Deployment

Deployed automatically via Cloud Build from the repository root. For standalone deployment:

```bash
./deploy.sh
```

## Environment Variables

| Variable | Description | Required | Default |
| --- | --- | --- | --- |
| `GCS_BUCKET` | Storage bucket name | Yes | — |
| `PROJECT_ID` | Google Cloud project | Yes | — |
| `REGION` | Location of Cloud Run Job | Yes | `us-central1` |
| `ANALYSIS_PATH` | GCS location of analysis files | Yes | — |
| `MODEL_TYPE` | Target model: `af2`, `boltz2`, `of3` | No | (Auto-detected) |
| `GEMINI_MODEL` | Model tier used for expert insights | No | `gemini-3.1-pro-preview` |
