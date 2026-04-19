# Plan: Model Upgrades and Testing Enhancements

This document outlines the next set of tasks to improve maintainability, configurability, and testing coverage for the FoldRun platform.

## 1. Document the Model Upgrade Process

Create a clear, step-by-step guide (e.g., `docs/upgrades.md` or a section in `DEPLOYMENT_GUIDE.md`) explaining how an administrator can upgrade the underlying models (AlphaFold2, OpenFold3, and Boltz-2) when new versions are released.

**Key areas to cover:**
- **Pip Packages / Git Repositories:** How to identify the new version tags or commit hashes.
- **Configurable Deployment:** How to use the newly added environment variables in `deploy-all.sh` to trigger an upgrade without modifying the codebase (e.g., `BOLTZ_VERSION="2.3.0" ./deploy-all.sh --steps build`).
- **Database/Weights Updates:** Instructions on how to fetch new model weights or updated genomic databases if a model upgrade requires them (and where to place them on the NFS mount).
- **Validation:** How to run the offline unit tests to verify the integrations still work before deploying to production.

## 2. Configurable Versions for AlphaFold2 and OpenFold3

Apply the same `ARG` and substitution pattern used for Boltz-2 to the AlphaFold2 and OpenFold3 containers, allowing their versions to be centrally managed and overridden at deployment time.

**Implementation Steps:**
- **OpenFold3:**
  - Update `src/openfold3-components/Dockerfile` to use an `ARG OF3_VERSION` (or commit hash) to checkout/install the specific version.
  - Update `cloudbuild.yaml` to pass `--build-arg OF3_VERSION=${_OF3_VERSION}`.
  - Add `_OF3_VERSION` to the `substitutions` block in `cloudbuild.yaml`.
  - Add `OF3_VERSION` to `deploy-all.sh` variables and usage documentation, and pass it to `gcloud builds submit`.
- **AlphaFold2:**
  - Update `src/alphafold-components/Dockerfile` to use an `ARG AF2_VERSION`.
  - Update `cloudbuild.yaml` to pass `--build-arg AF2_VERSION=${_AF2_VERSION}`.
  - Add `_AF2_VERSION` to the `substitutions` block.
  - Add `AF2_VERSION` to `deploy-all.sh` variables and pass it down.

## 3. Viewer: Show PDE Score for Boltz-2 and OF3

The `complex_pde` (global Predicted Distance Error, lower = better) scalar is already extracted by the analysis jobs and stored in each prediction's JSON as `gpde`. It is not currently displayed in the viewer — the `quality_grid` shows ranking_score, pTM, ipTM, and pLDDT, but omits PDE.

**Implementation Steps:**
- In `combined.html` `renderAnalysis()`, add a fifth quality tile for `gpde` (show as `N/A` when null/undefined).
- For OF3, the field is `gpde` in each prediction; for Boltz-2, same.
- Add a tooltip or label note: "PDE — lower is better. Complements pTM."
- Only display when `isOF3` (Boltz-2 and OF3 share this path); skip for AF2.
- The quality_grid currently uses `grid-template-columns: repeat(2, 1fr)` — update to `repeat(3, 1fr)` or add a second row.

**Boltz-2 exclusive fields** also worth surfacing in a future pass:
- `ligand_iptm` (protein-ligand interface ipTM, from confidence JSON) — highly relevant for drug discovery use cases
- `protein_iptm` (protein-protein interface ipTM) — useful for multimer analysis

These are in the raw confidence JSON (`aggregated_uri`) but are not currently extracted by the analysis job. To expose them: add extraction in `main.py` alongside the existing `chain_ptm` / `chain_pair_iptm` parsing, store in the per-prediction analysis JSON, and render in the viewer.

## 4. Reconsider AF2 Default GPU Tier: L4 → A100 40GB

**Observation:** L4 GPUs take significantly longer to provision than A100s, even with DWS FLEX_START enabled. In practice, jobs requesting A100s through FLEX_START tend to start faster because A100 capacity in most regions is deeper. The current AF2 auto-selection logic recommends L4 for proteins ≤500 tokens, which prioritizes cost over throughput — but if the L4 sits in the FLEX_START queue for 30+ minutes anyway, the cost saving is illusory.

**Proposal:** Change the AF2 default minimum GPU tier from L4 to A100 40GB. L4 would become an explicit opt-in (`gpu_type='L4'`) rather than the auto-selected default for small proteins.

**Files to change:**

- `foldrun_app/models/af2/base.py` — `_recommend_gpu()`: raise the threshold so `'auto'` never returns `L4`; or simply remove `L4` from the auto-selection path entirely.
- `foldrun_app/models/af2/base.py` — `_get_hardware_config()`: keep L4 as a valid explicit choice, but document it as slower-to-provision.
- `foldrun_app/agent.py` — `AGENT_INSTRUCTION`: update the hardware selection table and guidance. Change "L4 GPUs: Recommended for proteins <500 residues" to note that A100 is the default and L4 is available but may queue longer.
- `README.md` and `foldrun-agent/README.md` — GPU requirements table: update "AF2 minimum: L4" to "AF2 minimum: A100 40GB (default); L4 available as explicit opt-in".
- `foldrun_app/skills/cost_estimation/pricing.py` — verify the `_auto_select_gpu` function for AF2 also reflects the new default.

**Testing:** The existing `test_boltz2_hardware.py` pattern confirms no-L4 behavior for OF3/Boltz-2; similar assertions should be added (or existing ones updated) in `tests/unit/models/af2/` once those tests are created (see item 5 below).

**Trade-off note:** A100 is ~3× more expensive per hour than L4. For proteins where prediction takes 15–20 min, the cost difference is $0.30–0.50 per job — acceptable given the provisioning time saved. Users who genuinely want L4 (e.g. batch cost optimization with patience) can still pass `gpu_type='L4'` explicitly.

## 5. Rename AF2 Pipeline: `alphafold-inference-pipeline` → `alphafold2-inference-pipeline`

**Motivation:** The KFP pipeline `name` in `@dsl.pipeline(name=...)` determines the display name prefix of every Vertex AI pipeline run. Currently AF2 jobs appear as `alphafold-inference-pipeline-{ts}` while OF3 and Boltz-2 use `openfold3-inference-pipeline-{ts}` and `boltz2-inference-pipeline-{ts}`. Making AF2 consistent — `alphafold2-inference-pipeline-{ts}` — makes all three models visually distinct and version-explicit in the Vertex AI console.

**Files to change:**

- `foldrun_app/models/af2/pipeline/pipelines/alphafold_inference_pipeline.py` line ~54:
  ```python
  # Before
  @dsl.pipeline(name="alphafold-inference-pipeline", ...)
  # After
  @dsl.pipeline(name="alphafold2-inference-pipeline", ...)
  ```

- `foldrun_app/models/af2/tools/cleanup_gcs_files.py` line ~213:
  ```python
  # Before
  if "alphafold-inference-pipeline-" in job_id.lower():
  # After — handle both old and new names for backward compatibility
  if "alphafold-inference-pipeline-" in job_id.lower() or \
     "alphafold2-inference-pipeline-" in job_id.lower():
  ```

- `foldrun_app/models/af2/tools/analyze_job_deep.py` line ~408 — update the docstring example from `'alphafold-inference-pipeline-20251110082144'` to `'alphafold2-inference-pipeline-20251110082144'`.

- Any other docstrings, agent instructions, or README examples that reference the old prefix.

**Backward compatibility:** The viewer's `get_analysis_summary` resolves jobs by extracting the `YYYYMMDD` timestamp from the job_id suffix via regex — it does not match on the pipeline name prefix — so existing `alphafold-inference-pipeline-{ts}` jobs remain viewable unchanged. The only tool that pattern-matches on the prefix is `cleanup_gcs_files.py`, which should handle both strings as shown above.

**Note:** This is a cosmetic rename with no pipeline logic changes. New jobs get the new name; existing jobs keep the old name in Vertex AI history.

## 6. Add Unit Tests for AlphaFold2 (AF2)

Bring AlphaFold2 testing coverage up to par with OpenFold3 and Boltz-2. Currently, OF3 and Boltz-2 have dedicated unit test suites in `foldrun-agent/tests/unit/models/`.

**Implementation Steps:**
- Create a new directory: `foldrun-agent/tests/unit/models/af2/`.
- **Compile/CLI Tests:** Write tests to ensure the AF2 components (`predict_monomer`, `predict_multimer`, `relax`, etc.) use the correct CLI entrypoints and pass the correct arguments (e.g., `--fasta_paths`, `--model_preset`, `--db_preset`).
- **Pipeline Compilation Tests:** Write tests to load and compile the `af2_monomer_pipeline` and `af2_multimer_pipeline` into JSON to ensure the DAGs are structurally sound and variables resolve correctly.
- **Config & Hardware Tests:** Add tests to verify AF2-specific configuration loading and GPU auto-detection logic (e.g., ensuring `A100` vs `L4` is selected correctly based on sequence length).
- Integrate the new tests into the main `pytest` execution path and verify they all pass.
