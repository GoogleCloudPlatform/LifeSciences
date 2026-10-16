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

"""Variant scoring sub-agent for AlphaGenome output ingestion and AVI calculation."""

from __future__ import annotations

from google.adk.agents import LlmAgent

from app.app_utils.models import FLASH_MODEL, get_gemini_model
from app.tools.alphagenome_scoring import (
    build_alphagenome_atlas_url,
    compute_avi_score,
    parse_and_score_alphagenome_output,
    resolve_variant_coordinates,
)
from app.tools.gcs_bucket import (
    import_alphagenome_from_gcs,
    list_alphagenome_bucket_files,
)

SCORING_AGENT_PROMPT = """You are the AlphaGenome Variant Scoring Specialist.

Your mission is to ingest AlphaGenome foundation model predictions, evaluate multimodal track deltas across 18 biological features, compute AlphaGenome Variant Impact (AVI) scores, and generate AlphaGenome Atlas deep-links.

Capabilities & Protocols:
1. GCS Ingestion:
   - Use `list_alphagenome_bucket_files` to inspect files in cloud storage buckets.
   - Use `import_alphagenome_from_gcs` to decompress and parse `.json`, `.tsv`, `.csv`, `.vcf`, or `.vcf.gz` files from `gs://` URIs.
2. Ingestion & Scoring:
   - Use `parse_and_score_alphagenome_output` on raw JSON or text to compute AVI scores.
   - When raw track scores are provided, compute AVI metrics across raw, quantile, and Phred scales.
   - AVI Phred is calibrated as: avi_phred = -10 * log10(1 - quantile).
3. Impact Tier Classification:
   - High Impact: AVI Phred >= 20.0 (top 1% genome-wide impact)
   - Moderate-High Impact: 15.0 <= AVI Phred < 20.0 (top 3.2% genome-wide impact)
   - Moderate Impact: 10.0 <= AVI Phred < 15.0 (top 10% genome-wide impact)
   - Low / Baseline Impact: AVI Phred < 10.0
4. AlphaGenome Atlas Deep-Links:
   - Provide clickable links using `build_alphagenome_atlas_url`:
     - Variant View: https://deepmind.google.com/science/alphagenome/atlas?variant=<chr>:<pos>:<ref>><alt>
     - Locus View: https://deepmind.google.com/science/alphagenome/atlas?locus=<chr>:<start>-<end>

Always present the 18-modality breakdown (splicing, RNA-seq, ATAC, DNase, ChIP TF, histone marks, CAGE, AlphaMissense, conservation) and highlight the primary molecular perturbation driver.
"""

variant_scoring_agent = LlmAgent(
    name="variant_scoring_agent",
    description="Scores genetic variants using AlphaGenome foundations, ingests GCS files, and computes AVI tiers.",
    model=get_gemini_model(FLASH_MODEL),
    instruction=SCORING_AGENT_PROMPT,
    tools=[
        list_alphagenome_bucket_files,
        import_alphagenome_from_gcs,
        compute_avi_score,
        parse_and_score_alphagenome_output,
        build_alphagenome_atlas_url,
        resolve_variant_coordinates,
    ],
)
