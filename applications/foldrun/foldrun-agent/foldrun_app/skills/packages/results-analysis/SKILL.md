---
name: results-analysis
description: Evaluates predicted structures using pLDDT, PAE, PDE, pTM, ipTM, and binding affinity metrics, triggers parallel Cloud Run analysis jobs, and generates signed download URLs for structure archives. Use when the user asks to analyze a completed job, interpret confidence or affinity scores, troubleshoot a failed run, or download PDB/CIF/ZIP artifacts.
metadata:
  adk_additional_tools:
    - get_prediction_results
    - download_job_artifacts
    - analyze_prediction_quality
    - analyze_job_parallel
    - get_analysis_results
    - analyze_job
---

# Results & Analysis — Quality Assessment

## Analysis & Download Tools
- **Trigger Parallel Analysis (`analyze_job_parallel`)**: Dispatches parallel Cloud Run analysis across AF2, OF3, Boltz-2, and AF3 (`job_id`) to generate pLDDT plots, PAE/PDE heatmaps, ipTM matrices, and Gemini Expert Analysis.
  - **IMPORTANT**: After starting `analyze_job_parallel`, DO NOT poll in a loop. Tell the user: *"Analysis started (takes 1–2 minutes). Ask me to check results in a few minutes."*
- **Retrieve Analysis Results (`get_analysis_results`)**: Fetches completed `summary.json` metrics and expert analysis across AF2, OF3, Boltz-2, and AF3.
  - Only call when the user asks to check/retrieve analysis results.
  - If `status` is `'failed'` or `'likely_failed'`, stop retrying and explain `error_hint` / `error_details`.
  - **AF3 Lookup Rule**: AF3 stores artifacts under the sanitized `job_name` (`gs://.../af3_predictions/<job_name>/`). If `get_analysis_results(pipeline_job_id)` returns `not_found`, retry once with the human-readable `job_name`.
- **Download Artifacts & ZIP Bundle (`download_job_artifacts`)**: Generates 60-minute GCS V4 Signed HTTPS URLs (`artifacts_bundle_signed_url` for `artifacts_bundle.zip`, `best_structure_signed_url`, and plot URLs). Always present signed HTTPS URLs instead of raw `gs://` URIs.
- **AF2 Direct Helpers**: `get_prediction_results` (AF2 PDB files) and `analyze_prediction_quality` (direct pLDDT/PAE stats).
- **Job Troubleshooting (`analyze_job`)**: Analyzes failed or completed jobs (`detail_level='summary'` default; `detail_level='detailed'` fetches top Cloud Logging ERROR entries per failed task).

## Confidence & Affinity Metrics Summary
- **pLDDT (0–100, per-residue/atom)**: `>90` very high confidence (sidechains reliable, drug-design quality); `70–90` confident backbone; `50–70` low confidence / flexible loops; `<50` likely intrinsically disordered regions (IDRs — real biology, not failure).
- **PAE / PDE (Å)**: Predicted position/distance error between residue pairs. `<5 Å` within/between domains indicates rigid packing; `>15 Å` off-diagonal indicates uncertain relative domain or chain orientation.
- **pTM & ipTM (0–1)**: Global fold (`pTM > 0.8` high confidence) and interface quality (`ipTM > 0.75` reliable complex interface; `<0.6` uncertain binding pose).
- **Ranking Confidence / Score**:
  - **AF2**: Average `pLDDT` (monomer) or `0.8 * ipTM + 0.2 * pTM` (multimer).
  - **OF3 & AF3**: `0.8 * ipTM + 0.2 * pTM + 0.5 * fraction_disordered - 100 * has_clash` (consult references below for full details).
- **Boltz-2 Binding Affinity (`affinity` object in `summary.json`)**:
  - `affinity_pred_value`: Predicted `log10(IC50 uM)` (lower/more negative = stronger binding; e.g., `-2.0` corresponds to `IC50 ~ 10 nM`, `0.0` corresponds to `1 uM`).
  - `affinity_probability_binary` (`0–1`): Probability the ligand is an active binder (`>0.7` strong binder candidate).

## Deep Metric References (Load Only for Edge Cases)
The **Confidence & Affinity Metrics Summary** above is sufficient for standard `pLDDT`, `PAE`, `PDE`, `pTM`, `ipTM`, and Boltz-2 affinity interpretation—do **not** call `load_skill_resource` for general metric questions. Load these only when the user asks about model-specific edge cases:
- **OpenFold3 Metrics & Plots**: `load_skill_resource(skill_name="results-analysis", file_path="references/of3-metrics.md")` — `sample_ranking_score`, `gpde`, `chain_pair_iptm`, per-chain ligand pLDDT breakdown, and heatmap interpretation.
- **AlphaFold 3 Metrics & Benchmarks**: `load_skill_resource(skill_name="results-analysis", file_path="references/af3-metrics.md")` — `ranking_score` (>1.0 behavior with disordered tails), short-chain RNA `pTM` scaling caveats (`<50 nt`), `chain_pair_pae_min`, and H100 benchmarks.

## Smart Retry Guidance for Failed Jobs
When `analyze_job(detail_level='detailed')` reveals a task failure, recommend the targeted fix (using `get_job_details` + pipeline caching so completed steps are skipped; do not retry the same error more than twice):
- **Template parser error (`ValueError: Could not parse description`)**: Known AF2 hmmsearch PDB header issue → resubmit with an earlier `max_template_date` (e.g., `'2020-01-01'`).
- **Data pipeline OOM**: Large sequence with full BFD → resubmit with `use_small_bfd=True`.
- **Database not found**: NFS dataset download still running → check database status and wait before resubmitting.
- **Predict GPU OOM (`CUDA out of memory`)**: Sequence too large for GPU VRAM → upgrade `gpu_type` (`L4` → `A100` → `A100_80GB`).
- **GPU provisioning timeout (`max wait duration reached`)**: Regional GPU contention → check `check_gpu_quota` and resubmit or switch GPU tier.
- **Relax OOM or timeout**: Resubmit with `relax_gpu_type='A100'` or `run_relaxation=False`.
