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

"""OpenFold3 specific prediction analyzer built on BaseStructureAnalyzer."""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from . import shared_utils
from .base_analyzer import BaseStructureAnalyzer, JobExtras, SampleMetrics
from .shared_utils import calculate_per_chain_plddt, parse_cif_chains

logger = logging.getLogger(__name__)

OF3_PROMPT_HEADER = """You are an expert structural biologist reviewing OpenFold3 prediction results.

**OpenFold3 Reference (use to interpret results):**
- Diffusion-based structure prediction model (similar to AlphaFold3). Supports proteins, RNA, DNA, and ligands.
- Generates multiple diffusion samples per seed. Seeds = independent weight initializations (different conformations). Samples = different denoising trajectories from same seed (variations on a theme).
- **Metric interpretation thresholds:**
  - sample_ranking_score (0-1): Overall quality. >0.7 = good, >0.8 = very good.
  - pTM (0-1): Global fold accuracy. >0.8 = high confidence fold. >0.9 = very high.
  - ipTM (0-1): Interface quality for complexes. >0.7 = reliable interface. >0.8 = high confidence.
  - pLDDT (0-100): Per-atom confidence. >80 = good, >90 = very high. <50 in a region = likely disordered (real biology, not prediction failure).
  - gpde: Global predicted distance error. Lower = better. Complements pTM.
  - has_clash: Steric clash detected. 0 = clean, 1 = problem (try more seeds).
- **Key interpretation patterns:**
  - ipTM low but pTM high → individual chains fold well but interface/binding pose is uncertain
  - Low ligand pLDDT (e.g. <60) → binding mode is uncertain, try more seeds/samples
  - High variance in ranking_score across samples → conformational flexibility or poorly defined binding site
  - Chain pTM on diagonal of ipTM matrix shows per-chain fold quality independently of interface

"""

OF3_PROMPT_FOOTER = """

**Expert Analysis Request:**

Provide a comprehensive assessment covering:

1. **Overall Prediction Quality**: Evaluate the ranking_score, pTM, ipTM, and pLDDT. How reliable is this prediction?
2. **Per-Chain Confidence Analysis**: Compare pLDDT across chains. For protein chains, identify well-folded vs disordered regions. For ligand chains, assess binding pose confidence — low ligand pLDDT suggests uncertain binding mode.
3. **Ligand Binding Assessment** (if ligand present): Evaluate the protein-ligand interface quality using chain_pair_iptm and per-chain pLDDT. Is the ligand binding pose reliable? What does the protein-ligand PDE/PAE tell us about the binding site geometry?
4. **Sample Consistency**: How consistent are the top-ranked diffusion samples across seeds? High variability in ligand poses specifically suggests the binding site is uncertain.
5. **Interface Analysis** (if multi-chain): Interpret the chain_pair_iptm matrix. Which interfaces are confident? Which are uncertain?
6. **Clash Assessment**: If clashes are present, what might cause them and how to address them?
7. **Biological Insights**: What can we infer about binding affinity, specificity, and mechanism from these predictions?
8. **Limitations & Next Steps**: Key caveats and recommended follow-up (experimental validation, docking studies, additional seeds, etc.)

IMPORTANT: Begin your response directly with the analysis. Do NOT include any preamble."""


class OF3StructureAnalyzer(BaseStructureAnalyzer):
    """OpenFold3 post-processing analyzer and consolidator."""

    model_type: ClassVar[str] = "openfold3"
    display_name: ClassVar[str] = "OF3"
    extract_plddt_from_cif: ClassVar[bool] = False

    def _extract_sample_metrics(
        self,
        pred: dict[str, Any],
        aggregated: dict[str, Any],
        chain_info: list[dict[str, Any]],
        cif_plddt_scores: list[float],
    ) -> SampleMetrics:
        confidences_uri = pred.get("confidences_uri", "")
        confidences = (
            shared_utils.download_json_from_gcs(confidences_uri)
            if confidences_uri
            else {}
        )

        ptm = aggregated.get("ptm")
        iptm = aggregated.get("iptm")
        sample_ranking_score = (
            aggregated.get("sample_ranking_score")
            if aggregated.get("sample_ranking_score") is not None
            else aggregated.get("ranking_score")
        )

        is_monomer = len(chain_info) <= 1
        if is_monomer:
            ranking_score = (
                ptm if (ptm is not None and ptm > 0.0) else sample_ranking_score
            )
        else:
            ranking_score = sample_ranking_score

        return SampleMetrics(
            ranking_score=ranking_score,
            ptm=ptm,
            iptm=iptm,
            avg_plddt=aggregated.get("avg_plddt"),
            gpde=aggregated.get("gpde"),
            has_clash=aggregated.get("has_clash", 0),
            disorder=aggregated.get("disorder", 0),
            chain_ptm=aggregated.get("chain_ptm", {}),
            chain_pair_iptm=aggregated.get("chain_pair_iptm", {}),
            plddt_scores=confidences.get("plddt", []),
            pde_matrix=confidences.get("pde"),
        )

    def _load_job_extras(
        self,
        job_metadata: dict[str, Any],
        analysis_path: str,
        task_config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> JobExtras:
        fasta_sequence: str | None = None
        fasta_header: str | None = None
        input_query_json: dict[str, Any] | None = None

        try:
            query_json_path = job_metadata.get("parameters", {}).get("query_json_path")
            if query_json_path and isinstance(query_json_path, str):
                query_data = shared_utils.download_json_from_gcs(query_json_path)
                queries = query_data.get("queries", {})
                if queries:
                    query_name = next(iter(queries.keys()))
                    fasta_header = query_name
                    chains = queries[query_name].get("chains", [])
                    seqs = [
                        c.get("sequence", "")
                        for c in chains
                        if c.get("molecule_type") == "protein"
                    ]
                    fasta_sequence = "/".join(seqs) if seqs else None

                    clean_queries: dict[str, Any] = {}
                    for qname, qdata in queries.items():
                        clean_chains = []
                        for chain in qdata.get("chains", []):
                            clean_chain: dict[str, Any] = {
                                "molecule_type": chain.get("molecule_type"),
                                "chain_ids": chain.get("chain_ids"),
                            }
                            if chain.get("sequence"):
                                clean_chain["sequence"] = chain["sequence"]
                            if chain.get("smiles"):
                                clean_chain["smiles"] = chain["smiles"]
                            if chain.get("ccd_codes"):
                                clean_chain["ccd_codes"] = chain["ccd_codes"]
                            clean_chains.append(clean_chain)
                        clean_queries[qname] = {"chains": clean_chains}
                    input_query_json = {"queries": clean_queries}
        except Exception as exc:
            logger.warning(f"Could not load input sequence: {exc}")

        return JobExtras(
            fasta_sequence=fasta_sequence,
            fasta_header=fasta_header,
            extra_protein_info={"input_query_json": input_query_json},
        )

    def _build_expert_prompt(self, summary_data: dict[str, Any]) -> str:
        body = self.format_common_prompt_sections(summary_data)
        return f"{OF3_PROMPT_HEADER}{body}{OF3_PROMPT_FOOTER}"


ANALYZER = OF3StructureAnalyzer()


def generate_gemini_expert_analysis(summary_data: dict[str, Any]) -> dict[str, Any]:
    """Generate expert analysis using Gemini via Google GenAI SDK."""
    return ANALYZER.generate_gemini_expert_analysis(summary_data)


def consolidate_results(
    job_id: str, analysis_path: str, bucket_name: str, task_count: int
) -> dict[str, Any] | None:
    """Consolidate per-sample OF3 analysis results into summary.json."""
    return ANALYZER.consolidate_results(
        job_id=job_id,
        analysis_path=analysis_path,
        bucket_name=bucket_name,
        task_count=task_count,
    )


def run_task(
    task_index: int,
    task_count: int,
    task_config: dict[str, Any],
    bucket_name: str,
    analysis_path: str,
) -> None:
    """Execute OpenFold3 predictions analysis task."""
    ANALYZER.run_task(
        task_index=task_index,
        task_count=task_count,
        task_config=task_config,
        bucket_name=bucket_name,
        analysis_path=analysis_path,
    )


__all__ = [
    "ANALYZER",
    "OF3StructureAnalyzer",
    "calculate_per_chain_plddt",
    "consolidate_results",
    "generate_gemini_expert_analysis",
    "parse_cif_chains",
    "run_task",
]
