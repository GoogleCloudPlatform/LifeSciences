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

"""ESMFold2 / ESMFold2-Fast specific prediction analyzer built on BaseStructureAnalyzer."""

from __future__ import annotations

import io
import json
import logging
from typing import Any, ClassVar

import numpy as np

from foldrun_common import resolve_esmfold2_query_json_uri

from . import shared_utils
from .base_analyzer import BaseStructureAnalyzer, JobExtras, SampleMetrics
from .shared_utils import calculate_per_chain_plddt, parse_cif_chains

logger = logging.getLogger(__name__)

ESMFOLD2_PROMPT_HEADER = """You are an expert structural biologist reviewing ESMFold2 / ESMFold2-Fast prediction results.

**ESMFold2 Reference (use to interpret results):**
- EvolutionaryScale / Biohub diffusion-based biomolecular structure prediction model (`esm>=3.4.0`) built on an ESMC protein language model backbone.
- Supports proteins, RNA, DNA, ligands (CCD codes and SMILES), and covalent residue modifications.
- Two variants:
  - **ESMFold2-Fast** (`biohub/ESMFold2-Fast`): 24-block trunk, single-sequence inference (no MSA step required).
  - **ESMFold2** (`biohub/ESMFold2`): 48-block trunk, optional MSA support for maximum accuracy.
- **Metric interpretation thresholds:**
  - ranking_score (0-1): Overall quality (0.8*ipTM + 0.2*pTM for complexes, or pTM/pLDDT weighted for monomers). >0.7 = good, >0.8 = very good.
  - pTM (0-1): Global fold accuracy. >0.8 = high confidence fold. >0.9 = very high.
  - ipTM (0-1): Interface quality for complexes. >0.7 = reliable interface. >0.8 = high confidence.
  - pLDDT (0-100): Per-token/atom confidence. >80 = good, >90 = very high. <50 = likely disordered or flexible loop.
  - PAE (Predicted Aligned Error, 0-32 Å): Pairwise positional uncertainty. Low off-diagonal PAE (<5 Å) between domains or chains indicates a well-defined relative orientation.

"""


class ESMFold2StructureAnalyzer(BaseStructureAnalyzer):
    """ESMFold2 / ESMFold2-Fast post-processing analyzer and consolidator."""

    model_type: ClassVar[str] = "esmfold2"
    display_name: ClassVar[str] = "ESMFold2"
    extract_plddt_from_cif: ClassVar[bool] = True

    def _extract_sample_metrics(
        self,
        pred: dict[str, Any],
        aggregated: dict[str, Any],
        chain_info: list[dict[str, Any]],
        cif_plddt_scores: list[float],
    ) -> SampleMetrics:
        ptm = aggregated.get("ptm")
        iptm = aggregated.get("iptm")
        avg_plddt = aggregated.get("plddt_mean")
        if avg_plddt is not None and float(avg_plddt) <= 1.0001:
            avg_plddt = float(avg_plddt) * 100.0

        plddt_scores = list(cif_plddt_scores)
        if not plddt_scores and isinstance(aggregated.get("plddt"), list):
            raw_plddt = [float(v) for v in aggregated["plddt"]]
            if raw_plddt and max(raw_plddt) <= 1.0001:
                raw_plddt = [v * 100.0 for v in raw_plddt]
            plddt_scores = raw_plddt

        if avg_plddt is None and plddt_scores:
            avg_plddt = float(np.mean(plddt_scores))

        ranking_score = aggregated.get("ranking_score")
        if ranking_score is None:
            if iptm is not None and ptm is not None and len(chain_info) > 1:
                ranking_score = 0.8 * float(iptm) + 0.2 * float(ptm)
            elif ptm is not None:
                eff_plddt = (
                    (float(avg_plddt) / 100.0) if avg_plddt is not None else float(ptm)
                )
                ranking_score = 0.8 * float(ptm) + 0.2 * eff_plddt

        raw_chain_ptm = aggregated.get("chain_ptm", {})
        raw_pair_iptm = aggregated.get("chain_pair_iptm", {})

        idx_to_letter = {str(i): ci["chain_id"] for i, ci in enumerate(chain_info)}
        chain_ptm = {
            idx_to_letter.get(str(k), str(k)): v for k, v in raw_chain_ptm.items()
        }

        chain_pair_iptm: dict[str, Any] = {}
        for c1_idx, row in raw_pair_iptm.items():
            if isinstance(row, dict):
                for c2_idx, score in row.items():
                    c1 = idx_to_letter.get(str(c1_idx), str(c1_idx))
                    c2 = idx_to_letter.get(str(c2_idx), str(c2_idx))
                    if c1 != c2:
                        chain_pair_iptm[f"({c1}, {c2})"] = score
                    elif c1 not in chain_ptm:
                        chain_ptm[c1] = score
            else:
                c1 = idx_to_letter.get(str(c1_idx), str(c1_idx))
                chain_pair_iptm[c1] = row

        pae_matrix: list[Any] | None = None
        pae_mean = aggregated.get("pae_mean")
        pae_max = aggregated.get("pae_max")
        pae_uri = pred.get("pae_uri", "")
        if pae_uri:
            try:
                pae_bytes = shared_utils.download_image_from_gcs(pae_uri)
                pae_data = np.load(io.BytesIO(pae_bytes))
                pae_arr = pae_data["pae"]
                if pae_arr.ndim == 3:
                    pae_arr = pae_arr[0]
                pae_matrix = pae_arr.tolist()
                if pae_mean is None:
                    pae_mean = float(np.mean(pae_arr))
                if pae_max is None:
                    pae_max = float(np.max(pae_arr))
            except Exception as exc:
                logger.warning(f"Could not load PAE from {pae_uri}: {exc}")

        return SampleMetrics(
            ranking_score=ranking_score,
            ptm=ptm,
            iptm=iptm,
            avg_plddt=avg_plddt,
            pae_mean=pae_mean,
            pae_max=pae_max,
            has_clash=aggregated.get("has_clash", 0),
            disorder=aggregated.get("disorder", 0),
            chain_ptm=chain_ptm,
            chain_pair_iptm=chain_pair_iptm,
            plddt_scores=plddt_scores,
            pae_matrix=pae_matrix,
        )

    def _load_job_extras(
        self,
        job_metadata: dict[str, Any],
        analysis_path: str,
        task_config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> JobExtras:
        cfg = task_config or {}
        query_json_uri = kwargs.get("query_json_uri") or cfg.get("query_json_uri")

        fasta_sequence: str | None = None
        fasta_header: str | None = None
        input_query_json: dict[str, Any] | None = None
        try:
            query_json_path = query_json_uri
            if not query_json_path:
                bucket_name = (
                    analysis_path[5:].split("/", 1)[0]
                    if analysis_path.startswith("gs://")
                    else ""
                )
                query_json_path = resolve_esmfold2_query_json_uri(
                    bucket_name=bucket_name,
                    parameter_values=job_metadata.get("parameters"),
                    labels=job_metadata.get("labels"),
                )

            if query_json_path:
                raw_json = shared_utils.download_text_from_gcs(query_json_path)
                query_data = json.loads(raw_json) or {}
                input_query_json = query_data

                seqs: list[str] = []
                for entry in query_data.get("sequences", []):
                    if not isinstance(entry, dict):
                        continue
                    for mol_key in ("protein", "rna", "dna"):
                        if mol_key in entry and isinstance(entry[mol_key], dict):
                            seq_id = entry[mol_key].get("id", "A")
                            if isinstance(seq_id, list):
                                seq_id = seq_id[0]
                            if not fasta_header:
                                fasta_header = str(seq_id)
                            seqs.append(str(entry[mol_key].get("sequence", "")))
                fasta_sequence = "/".join(seqs) if seqs else None
        except Exception as exc:
            logger.warning(f"Could not load ESMFold2 input query JSON: {exc}")

        return JobExtras(
            fasta_sequence=fasta_sequence,
            fasta_header=fasta_header,
            extra_protein_info={"input_query_json": input_query_json},
        )

    def _build_expert_prompt(self, summary_data: dict[str, Any]) -> str:
        best = summary_data.get("best_prediction") or {}
        pae_section = ""
        if best.get("pae_mean") is not None:
            pae_section = (
                f"\n**Predicted Aligned Error (PAE):**\n"
                f"- Mean PAE: {best['pae_mean']:.2f} Å\n"
                f"- Max PAE: {best.get('pae_max', 0.0):.2f} Å\n"
            )

        body = self.format_common_prompt_sections(
            summary_data,
            model_label="ESMFold2",
            extra_section=pae_section,
        )
        footer = """

**Expert Analysis Request:**

Provide a comprehensive assessment covering:

1. **Overall Prediction Quality**: Evaluate the ranking_score, pTM, ipTM, mean pLDDT, and PAE. How reliable is this prediction?
2. **Per-Chain & Domain Confidence Analysis**: Compare pLDDT across chains and identify well-folded core domains vs flexible loops or disordered termini.
3. **Ligand / Nucleic Acid Binding Assessment** (if present): Evaluate interface quality using chain_pair_iptm, PAE, and per-chain pLDDT.
4. **Interface & Relative Orientation Analysis** (if multi-chain or multi-domain): Interpret the PAE matrix and chain_pair_iptm matrix.
5. **Sample Consistency**: If multiple seeds or diffusion samples were generated, how consistent are the top-ranked structures?
6. **Biological Insights & Next Steps**: Key caveats and recommended follow-up (e.g., comparing ESMFold2-Fast single-sequence results with full MSA-enabled ESMFold2, OpenFold3, Boltz-2, or AlphaFold 3).

IMPORTANT: Begin your response directly with the analysis. Do NOT include any preamble."""

        return f"{ESMFOLD2_PROMPT_HEADER}{body}{footer}"


ANALYZER = ESMFold2StructureAnalyzer()


def generate_gemini_expert_analysis(summary_data: dict[str, Any]) -> dict[str, Any]:
    """Generate expert analysis using Gemini via Google GenAI SDK."""
    return ANALYZER.generate_gemini_expert_analysis(summary_data)


def consolidate_results(
    job_id: str,
    analysis_path: str,
    bucket_name: str,
    task_count: int,
    query_json_uri: str | None = None,
) -> dict[str, Any] | None:
    """Consolidate per-sample ESMFold2 analysis results into summary.json."""
    return ANALYZER.consolidate_results(
        job_id=job_id,
        analysis_path=analysis_path,
        bucket_name=bucket_name,
        task_count=task_count,
        query_json_uri=query_json_uri,
    )


def run_task(
    task_index: int,
    task_count: int,
    task_config: dict[str, Any],
    bucket_name: str,
    analysis_path: str,
) -> None:
    """Execute ESMFold2 predictions analysis task."""
    ANALYZER.run_task(
        task_index=task_index,
        task_count=task_count,
        task_config=task_config,
        bucket_name=bucket_name,
        analysis_path=analysis_path,
    )


__all__ = [
    "ANALYZER",
    "ESMFold2StructureAnalyzer",
    "calculate_per_chain_plddt",
    "consolidate_results",
    "generate_gemini_expert_analysis",
    "parse_cif_chains",
    "run_task",
]
