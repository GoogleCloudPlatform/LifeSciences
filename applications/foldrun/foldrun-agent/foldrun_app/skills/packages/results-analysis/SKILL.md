---
name: results-analysis
description: Quality assessment, confidence scoring (pLDDT, PAE, ipTM), and parallel evaluation of predicted protein structures
metadata:
  adk_additional_tools:
    - af2_analyze_job_parallel
    - af2_get_analysis_results
    - of3_analyze_job_parallel
    - of3_get_analysis_results
    - boltz2_analyze_job_parallel
    - boltz2_get_analysis_results
    - get_af3_results
---

# Results Analysis & Quality Assessment

- **Download results**: Use get_prediction_results to retrieve PDB files
- **Analyze quality**: Use analyze_prediction_quality for pLDDT and PAE metrics
- **AF3 results**: Use get_af3_results to retrieve completed AlphaFold 3 prediction results from GCS (ranking_score, pTM, ipTM, mean pLDDT, and mmCIF path)
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

### OpenFold3 (OF3) Metrics Reference
For deep metric interpretations, consult the attached reference: `references/of3-metrics.md`.
Includes details on `sample_ranking_score`, `ptm`, `iptm`, `gpde`, `chain_pair_iptm`, and analysis plot interpretation.

### AlphaFold 3 (AF3) Metrics Reference
For deep metric interpretations, consult the attached reference: `references/af3-metrics.md`.
Includes details on `ranking_score` (0.8·ipTM + 0.2·pTM), `ptm`, `iptm`, `mean_plddt`, `has_clash`, `fraction_disordered`, and mmCIF all-atom interpretation.

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
