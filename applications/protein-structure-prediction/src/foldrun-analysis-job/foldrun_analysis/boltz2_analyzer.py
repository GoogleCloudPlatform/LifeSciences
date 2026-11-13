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

"""Boltz-2 specific prediction analyzer built on BaseStructureAnalyzer."""

from __future__ import annotations

import io
import logging
from typing import Any, ClassVar

import numpy as np
import yaml

from foldrun_common import resolve_boltz2_query_yaml_uri

from . import shared_utils
from .base_analyzer import BaseStructureAnalyzer, JobExtras, SampleMetrics
from .shared_utils import calculate_per_chain_plddt, parse_cif_chains

logger = logging.getLogger(__name__)

BOLTZ2_PROMPT_HEADER = """You are an expert structural biologist reviewing Boltz-2 prediction results.

**Boltz-2 Reference (use to interpret results):**
- Diffusion-based biomolecular structure prediction model (MIT, open-source). Supports proteins, RNA, DNA, ligands, covalent modifications, and glycans.
- Generates multiple diffusion samples per seed. Seeds = independent weight initializations (different conformations). Samples = different denoising trajectories from same seed (variations on a theme).
- **Metric interpretation thresholds:**
  - confidence_score/ranking_score (0-1): Overall quality. >0.7 = good, >0.8 = very good.
  - pTM (0-1): Global fold accuracy. >0.8 = high confidence fold. >0.9 = very high.
  - ipTM (0-1): Interface quality for complexes. >0.7 = reliable interface. >0.8 = high confidence.
  - pLDDT (0-100): Per-atom confidence. >80 = good, >90 = very high. <50 in a region = likely disordered (real biology, not prediction failure).
  - complex_pde: Global predicted distance error. Lower = better. Complements pTM.
  - has_clash: Steric clash detected. 0 = clean, 1 = problem (try more seeds).
- **Key interpretation patterns:**
  - ipTM low but pTM high → individual chains fold well but interface/binding pose is uncertain
  - Low ligand pLDDT (e.g. <60) → binding mode is uncertain, try more seeds/samples
  - High variance in ranking_score across samples → conformational flexibility or poorly defined binding site
  - Chain pTM on diagonal of ipTM matrix shows per-chain fold quality independently of interface

"""


def parse_affinity(affinity_data: dict[str, Any]) -> dict[str, Any]:
    """Parse Boltz-2 affinity JSON and compute human-readable metrics."""
    val = affinity_data.get("affinity_pred_value")
    prob = affinity_data.get("affinity_probability_binary")

    result: dict[str, Any] = {
        "affinity_pred_value": val,
        "affinity_probability_binary": prob,
        "affinity_pred_value1": affinity_data.get("affinity_pred_value1"),
        "affinity_probability_binary1": affinity_data.get(
            "affinity_probability_binary1"
        ),
        "affinity_pred_value2": affinity_data.get("affinity_pred_value2"),
        "affinity_probability_binary2": affinity_data.get(
            "affinity_probability_binary2"
        ),
    }

    if val is not None:
        ic50_um = 10**val
        ic50_nm = ic50_um * 1000
        pic50 = 6.0 - val
        delta_g = pic50 * 1.364

        result.update(
            {
                "ic50_um": round(ic50_um, 4),
                "ic50_nm": round(ic50_nm, 2),
                "pic50": round(pic50, 3),
                "delta_g_kcal_mol": round(delta_g, 3),
                "binding_classification": (
                    "very_strong"
                    if ic50_nm < 10
                    else "strong"
                    if ic50_nm < 100
                    else "moderate"
                    if ic50_nm < 1000
                    else "weak"
                    if ic50_nm < 10000
                    else "very_weak"
                ),
            }
        )

    if prob is not None:
        result["binding_likelihood"] = (
            "high" if prob > 0.8 else "moderate" if prob > 0.5 else "low"
        )

    return result


class Boltz2StructureAnalyzer(BaseStructureAnalyzer):
    """Boltz-2 post-processing analyzer and consolidator."""

    model_type: ClassVar[str] = "boltz2"
    display_name: ClassVar[str] = "Boltz2"
    extract_plddt_from_cif: ClassVar[bool] = True

    def _extract_sample_metrics(
        self,
        pred: dict[str, Any],
        aggregated: dict[str, Any],
        chain_info: list[dict[str, Any]],
        cif_plddt_scores: list[float],
    ) -> SampleMetrics:
        ranking_score = (
            aggregated.get("confidence_score")
            if aggregated.get("confidence_score") is not None
            else aggregated.get("sample_ranking_score")
        )

        raw_chain_ptm = aggregated.get("chains_ptm", {})
        raw_pair_iptm = aggregated.get("pair_chains_iptm", {})

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
            else:
                c1 = idx_to_letter.get(str(c1_idx), str(c1_idx))
                chain_pair_iptm[c1] = row

        pde_matrix: list[Any] | None = None
        pde_uri = pred.get("pde_uri", "")
        if pde_uri:
            try:
                pde_bytes = shared_utils.download_image_from_gcs(pde_uri)
                pde_data = np.load(io.BytesIO(pde_bytes))
                pde_matrix = pde_data["pde"].tolist()
            except Exception as exc:
                logger.warning(f"Could not load PDE: {exc}")

        return SampleMetrics(
            ranking_score=ranking_score,
            ptm=aggregated.get("ptm"),
            iptm=aggregated.get("iptm"),
            avg_plddt=aggregated.get("complex_plddt"),
            gpde=aggregated.get("complex_pde"),
            has_clash=aggregated.get("has_clash", 0),
            disorder=aggregated.get("disorder", 0),
            chain_ptm=chain_ptm,
            chain_pair_iptm=chain_pair_iptm,
            plddt_scores=cif_plddt_scores,
            pde_matrix=pde_matrix,
        )

    def _load_job_extras(
        self,
        job_metadata: dict[str, Any],
        analysis_path: str,
        task_config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> JobExtras:
        cfg = task_config or {}
        affinity_uri = kwargs.get("affinity_uri") or cfg.get("affinity_uri")
        query_yaml_uri = kwargs.get("query_yaml_uri") or cfg.get("query_yaml_uri")

        affinity_results: dict[str, Any] | None = None
        if affinity_uri:
            try:
                affinity_raw = shared_utils.download_json_from_gcs(affinity_uri)
                affinity_results = parse_affinity(affinity_raw)
            except Exception as exc:
                logger.warning(
                    f"Could not load affinity results from {affinity_uri}: {exc}"
                )

        fasta_sequence: str | None = None
        fasta_header: str | None = None
        input_query_yaml: str | None = None
        try:
            query_yaml_path = query_yaml_uri
            if not query_yaml_path:
                bucket_name = (
                    analysis_path[5:].split("/", 1)[0]
                    if analysis_path.startswith("gs://")
                    else ""
                )
                query_yaml_path = resolve_boltz2_query_yaml_uri(
                    bucket_name=bucket_name,
                    parameter_values=job_metadata.get("parameters"),
                    labels=job_metadata.get("labels"),
                )

            if query_yaml_path:
                raw_yaml = shared_utils.download_text_from_gcs(query_yaml_path)
                query_data = yaml.safe_load(raw_yaml) or {}
                input_query_yaml = raw_yaml

                seqs: list[str] = []
                for entry in query_data.get("sequences", []):
                    if isinstance(entry, dict) and "protein" in entry:
                        seq_id = entry["protein"].get("id", "A")
                        if isinstance(seq_id, list):
                            seq_id = seq_id[0]
                        if not fasta_header:
                            fasta_header = str(seq_id)
                        seqs.append(entry["protein"].get("sequence", ""))
                fasta_sequence = "/".join(seqs) if seqs else None
        except Exception as exc:
            logger.warning(f"Could not load input sequence: {exc}")

        return JobExtras(
            fasta_sequence=fasta_sequence,
            fasta_header=fasta_header,
            extra_protein_info={"input_query_yaml": input_query_yaml},
            extra_summary_fields={"affinity": affinity_results},
        )

    def _build_expert_prompt(self, summary_data: dict[str, Any]) -> str:
        affinity = summary_data.get("affinity") or summary_data.get("summary", {}).get(
            "affinity"
        )
        affinity_section = ""
        has_affinity = (
            affinity is not None and affinity.get("affinity_pred_value") is not None
        )
        if has_affinity and affinity is not None:
            affinity_section = (
                "\n**Binding Affinity Prediction (Boltz-2 Affinity Model):**\n"
                f"- affinity_pred_value: {affinity['affinity_pred_value']:.3f} (log10 IC50 in μM)\n"
                f"- IC50: {affinity.get('ic50_nm', 0):.1f} nM ({affinity.get('ic50_um', 0):.4f} μM)\n"
                f"- pIC50: {affinity.get('pic50', 0):.3f}\n"
                f"- ΔG (estimated): {affinity.get('delta_g_kcal_mol', 0):.2f} kcal/mol\n"
                f"- Binding probability: {affinity.get('affinity_probability_binary', 0):.3f} "
                f"({affinity.get('binding_likelihood', 'N/A')})\n"
                f"- Binding classification: "
                f"{affinity.get('binding_classification', 'N/A').replace('_', ' ').title()}\n"
            )

        body = self.format_common_prompt_sections(
            summary_data,
            model_label="Boltz-2",
            extra_section=affinity_section,
        )
        affinity_item = (
            "Interpret the IC50, pIC50, ΔG, and binding probability. Contextualize affinity: "
            "is this drug-like (<100 nM), moderate (100 nM–1 μM), or weak (>1 μM)? Note any caveats."
            if has_affinity
            else "Affinity was not predicted in this run. Consider whether affinity prediction would add value."
        )
        footer = f"""

**Expert Analysis Request:**

Provide a comprehensive assessment covering:

1. **Overall Prediction Quality**: Evaluate the ranking_score, pTM, ipTM, and pLDDT. How reliable is this prediction?
2. **Per-Chain Confidence Analysis**: Compare pLDDT across chains. For protein chains, identify well-folded vs disordered regions. For ligand chains, assess binding pose confidence — low ligand pLDDT suggests uncertain binding mode.
3. **Ligand Binding Assessment** (if ligand present): Evaluate the protein-ligand interface quality using chain_pair_iptm and per-chain pLDDT. Is the ligand binding pose reliable?
4. **Binding Affinity** {"(provided)" if has_affinity else "(not predicted)"}: {affinity_item}
5. **Sample Consistency**: How consistent are the top-ranked diffusion samples across seeds? High variability in ligand poses suggests the binding site is uncertain.
6. **Interface Analysis** (if multi-chain): Interpret the chain_pair_iptm matrix. Which interfaces are confident? Which are uncertain?
7. **Clash Assessment**: If clashes are present, what might cause them and how to address them?
8. **Biological Insights**: What can we infer about binding specificity and mechanism from these predictions?
9. **Limitations & Next Steps**: Key caveats and recommended follow-up (experimental validation, docking studies, additional seeds, etc.)

IMPORTANT: Begin your response directly with the analysis. Do NOT include any preamble."""

        return f"{BOLTZ2_PROMPT_HEADER}{body}{footer}"


ANALYZER = Boltz2StructureAnalyzer()


def generate_gemini_expert_analysis(summary_data: dict[str, Any]) -> dict[str, Any]:
    """Generate expert analysis using Gemini via Google GenAI SDK."""
    return ANALYZER.generate_gemini_expert_analysis(summary_data)


def consolidate_results(
    job_id: str,
    analysis_path: str,
    bucket_name: str,
    task_count: int,
    affinity_uri: str | None = None,
    query_yaml_uri: str | None = None,
) -> dict[str, Any] | None:
    """Consolidate per-sample Boltz-2 analysis results into summary.json."""
    return ANALYZER.consolidate_results(
        job_id=job_id,
        analysis_path=analysis_path,
        bucket_name=bucket_name,
        task_count=task_count,
        affinity_uri=affinity_uri,
        query_yaml_uri=query_yaml_uri,
    )


def run_task(
    task_index: int,
    task_count: int,
    task_config: dict[str, Any],
    bucket_name: str,
    analysis_path: str,
) -> None:
    """Execute Boltz-2 predictions analysis task."""
    ANALYZER.run_task(
        task_index=task_index,
        task_count=task_count,
        task_config=task_config,
        bucket_name=bucket_name,
        analysis_path=analysis_path,
    )


__all__ = [
    "ANALYZER",
    "Boltz2StructureAnalyzer",
    "calculate_per_chain_plddt",
    "consolidate_results",
    "generate_gemini_expert_analysis",
    "parse_affinity",
    "parse_cif_chains",
    "run_task",
]
