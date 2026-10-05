# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Modular instructions for results analysis skill."""

RESULTS_ANALYSIS_INSTRUCTION = """### Results & Analysis
- **Download artifacts & ZIP bundle**: Use `download_job_artifacts` to generate 60-minute GCS V4 Signed URLs for the complete job archive (`artifacts_bundle.zip`, containing ranked structures, plots, Gemini expert report `.md`, inputs, and `summary.json`) as well as individual structures and plots. Always present the signed HTTPS URLs (`artifacts_bundle_signed_url`, `best_structure_signed_url`) instead of raw `gs://` or GCS Console links so isolated end users can download directly in their browser.
- **Download results**: Use `get_prediction_results` to retrieve AF2 PDB structures and signed download links
- **Analyze quality**: Use analyze_prediction_quality for pLDDT and PAE metrics
- **Parallel analysis**: Use analyze_job_parallel for fast batch analysis (25 predictions in ~60s)
  - **IMPORTANT**: After starting analysis, DO NOT automatically check for results
  - Tell the user: "Analysis started. This will take 1-2 minutes. Ask me to check results in a few minutes."
  - Wait for the user to explicitly ask for results before checking
- **Get analysis results**: Use get_analysis_results to retrieve completed parallel analyses
  - Only call this when the user explicitly asks to check/get analysis results
  - Check the 'status' field in response: 'complete', 'running', 'failed', 'likely_failed', 'incomplete'
  - If status is 'failed' or 'likely_failed', STOP retrying and explain the error to the user with error_hint/error_details
  - DO NOT repeatedly call get_analysis_results if it returns 'failed' status - the analysis has permanently failed
  - Common failure: Cloud Run can't find prediction files (usually means AlphaFold job hasn't completed yet)
  - If status is 'running', you may check again ONCE after a brief wait, but not in a loop
- **OF3 analysis**: Use of3_analyze_job_parallel to analyze OF3 predictions (generates pLDDT plots, PDE heatmaps, ipTM matrix, Gemini analysis)
- **OF3 results**: Use of3_get_analysis_results to retrieve OF3 analysis results
- **Boltz-2 analysis**: Use boltz2_analyze_job_parallel to analyze Boltz-2 predictions
- **Boltz-2 results**: Use boltz2_get_analysis_results to retrieve Boltz-2 analysis results
- **Job analysis**: Use analyze_job for comprehensive analysis of any job (failed, successful, or running)
  - Use detail_level='summary' for quick overview without log fetching (default, recommended for initial checks)
  - Use detail_level='detailed' for deep troubleshooting with Cloud Logging error logs (fetches top 5 ERROR logs per failed task)

### AF2 Quality Metrics
**pLDDT (per-residue, 0-100)**:
- >90: Very high confidence — backbone and sidechain positions reliable. Drug design quality.
- 70-90: Good confidence — backbone reliable, some sidechain uncertainty. Suitable for most analyses.
- 50-70: Low confidence — often loops, disordered regions, or poorly sampled conformations.
- <50: Very low — likely intrinsically disordered regions (IDRs). These are real biology, not prediction failures.

**PAE (Predicted Aligned Error, Angstroms)**:
- Measures predicted error in position of residue X relative to residue Y
- Low PAE (<5Å) within a domain = well-defined fold
- Low PAE between domains = reliable domain arrangement
- High PAE between domains = domains may be correct individually but relative orientation is uncertain
- Critical for multimer interface quality — low inter-chain PAE = confident interface

**ranking_confidence**:
- Monomer: average pLDDT (higher is better)
- Multimer: 0.8 × ipTM + 0.2 × pTM (higher is better)
- Use this to pick the best model from the 5 (or 25) predictions

### When AF2 Struggles (help users understand)
- **Disordered regions**: Low pLDDT (<50) in known IDRs is correct — these regions are genuinely flexible
- **Multi-domain proteins with flexible linkers**: Individual domains may be well-predicted but relative orientation uncertain (high inter-domain PAE)
- **Membrane proteins**: Often good in transmembrane regions, uncertain in flexible loops
- **Novel folds**: If the protein has no homologs in training data, confidence will be lower
- **Multimer interfaces**: Some interfaces are poorly predicted even with high individual chain pLDDT

### OF3 Confidence Metrics Interpretation

**Two confidence files per sample** (important — know which to reference):

1. `*_confidences_aggregated.json` — summary scores:
   - **sample_ranking_score** (0-1): Overall quality. Use this to rank predictions. >0.7 is good.
   - **ptm** (0-1): Predicted TM-score. Global fold accuracy. >0.8 = high confidence fold.
   - **iptm** (0-1): Interface pTM. Complex interface quality. >0.7 = reliable interface.
   - **avg_plddt** (0-100): Average per-residue confidence. >80 = good, >90 = very high.
   - **gpde**: Global predicted distance error. Lower is better.
   - **has_clash** (0 or 1): Steric clash detected. 0 = clean structure.
   - **disorder** (0 or 1): Disorder prediction.
   - **chain_ptm**: Per-chain pTM dict (e.g., `{"A": 0.87, "B": 0.46}`) — identifies which chains are well-predicted.
   - **chain_pair_iptm**: Per-chain-pair interface dict (e.g., `{"(A, B)": 0.72}`) — the diagonal contains per-chain pTM scores.
   - **bespoke_iptm**: Per-chain-pair interface scores (same pairs as chain_pair_iptm).

2. `*_confidences.json` — per-residue/per-atom arrays (for plots):
   - **plddt**: Per-token pLDDT scores (array, 0-100). Plot as line chart per chain.
   - **pde**: Predicted distance error matrix (NxN). Plot as heatmap.

### OF3 Analysis Plots (available after analysis job runs)
- **ipTM matrix heatmap**: Chain×chain interface quality. Diagonal = per-chain pTM. Off-diagonal = pairwise ipTM. Helps identify which interfaces are confident.
- **Per-residue pLDDT plot**: Line chart per chain showing confidence along the sequence. Dips indicate loops, disorder, or uncertain regions.
- **PAE heatmap**: Residue×residue predicted aligned error. Low values (blue) = confident relative positions. High values (red) = uncertain. Critical for assessing domain arrangements.
- **Contact probability heatmap**: Predicted inter-residue contacts. Useful for identifying binding interfaces.

### Interpreting OF3 Results for Users
- Compare **sample_ranking_score** across seeds/samples to find the best prediction
- The **ipTM matrix diagonal** contains per-chain pTM — quick way to see which chains folded well
- If iptm is low but ptm is high → individual chains fold well but the interface is uncertain
- If has_clash = 1 → suggest trying more seeds or checking the input for steric issues
- Low pLDDT in a region → may be intrinsically disordered (real biology) or poorly sampled (try more seeds)
- For drug binding predictions → focus on chain_pair_iptm between protein and ligand chains
- **Per-chain pLDDT breakdown**: After analysis, results include per-chain mean pLDDT (e.g., protein chain A: 80.8, ligand chain B (ATP): 63.8). Low ligand pLDDT (<60) means the binding pose is uncertain — suggest more seeds

## Smart Retry Guidance for Failed Jobs

When a job fails, analyze the error and suggest targeted fixes — don't just resubmit blindly.

### Data Pipeline Failures
**Template parser errors** (e.g., `ValueError: Could not parse description`):
- This is a known AF2 bug where hmmsearch finds a PDB template with an unusual description format
- **Fix**: Resubmit with an earlier `max_template_date` (e.g., `2020-01-01`) to avoid the problematic PDB entry
- Tell the user: "The template search found a PDB entry with a format AF2 can't parse. I can resubmit with an earlier template date to skip it — prediction quality will still be good."

**Sequence parsing errors** (e.g., invalid FASTA, empty sequences):
- Check if the FASTA was malformed (missing newlines, merged headers)
- The agent auto-repairs common copy-paste issues, but if it still fails, show the user what was submitted and ask them to verify
- **Fix**: Reformat the sequence and resubmit

**Database not found errors** (e.g., missing uniref90, BFD):
- NFS databases may not be fully downloaded yet
- **Fix**: Check database download status, wait for completion, then resubmit

**Out of memory / OOM in data pipeline**:
- Rare, but can happen with very large sequences and full BFD
- **Fix**: Resubmit with `use_small_bfd=True`

### Predict Task Failures
**GPU OOM** (e.g., CUDA out of memory):
- Sequence too large for the selected GPU
- **Fix**: Upgrade GPU tier (L4 -> A100, A100 -> A100_80GB)

**GPU provisioning timeout** (e.g., max wait duration reached):
- No GPUs available in the region within the DWS timeout
- **Fix**: Resubmit (transient), or try a different GPU tier that has quota available
- Check quotas first with check_gpu_quota

### Relax Task Failures
**Relax OOM or timeout**:
- AMBER relaxation is less demanding — usually an L4 is sufficient
- **Fix**: If relax failed on L4, try with `relax_gpu_type='A100'`
- Alternative: Resubmit with `run_relaxation=False` (unrelaxed structures are still useful)

### General Retry Rules
1. Always use `get_job_details` first to retrieve the original sequence and parameters
2. Pipeline caching (`enable_caching=True`, the default) skips completed tasks — only failed tasks re-run
3. Tell the user: "Completed steps will be cached so only the failed task re-runs"
4. If the same error repeats after retry, escalate — don't retry the same thing more than twice
"""
