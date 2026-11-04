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

"""Standalone Markdown report and archive README builders for FoldRun."""

from __future__ import annotations

from typing import Any


def build_expert_analysis_markdown(summary_data: dict[str, Any]) -> str:
    """Format a standalone Markdown report from consolidated ``summary.json`` data."""
    job_id = summary_data.get("job_id", "unknown")
    model_type = summary_data.get("model_type", "unknown")
    analyzed_at = summary_data.get("analyzed_at", "")
    summary_inner = summary_data.get("summary") or {}
    protein_info = summary_inner.get("protein_info") or {}
    qm = summary_inner.get("quality_metrics") or {}
    job_meta = protein_info.get("job_metadata") or {}
    display_name = job_meta.get("display_name") or job_id

    lines = [
        f"# FoldRun Analysis Report: {display_name}",
        "",
        f"- **Job ID**: `{job_id}`",
        f"- **Model**: `{model_type}`",
        f"- **Analyzed At**: `{analyzed_at}`",
    ]
    if protein_info.get("sequence_length"):
        lines.append(f"- **Sequence / Token Length**: `{protein_info['sequence_length']}`")
    if job_meta.get("duration_formatted"):
        lines.append(f"- **Pipeline Duration**: `{job_meta['duration_formatted']}`")

    best = summary_data.get("best_prediction") or {}
    if best or qm:
        lines.extend(["", "## Top-Ranked Prediction Summary", ""])
        name = best.get("sample_name") or best.get("model_name") or qm.get("best_model") or "Rank 1"
        lines.append(f"- **Prediction**: `{name}`")

        plddt_mean = (
            best.get("plddt_mean")
            if best.get("plddt_mean") is not None
            else qm.get("best_model_plddt")
        )
        if isinstance(plddt_mean, (int, float)):
            lines.append(f"- **Mean pLDDT**: `{plddt_mean:.2f}`")

        ranking_score = (
            best.get("ranking_score")
            if best.get("ranking_score") is not None
            else qm.get("best_ranking_score")
        )
        if isinstance(ranking_score, (int, float)):
            lines.append(f"- **Ranking Score**: `{ranking_score:.4f}`")

        if isinstance(best.get("ranking_confidence"), (int, float)):
            lines.append(f"- **Ranking Confidence**: `{best['ranking_confidence']:.4f}`")

        ptm = best.get("ptm") if best.get("ptm") is not None else qm.get("best_ptm")
        if isinstance(ptm, (int, float)):
            lines.append(f"- **pTM**: `{ptm:.4f}`")

        iptm = best.get("iptm") if best.get("iptm") is not None else qm.get("best_iptm")
        if isinstance(iptm, (int, float)):
            lines.append(f"- **ipTM**: `{iptm:.4f}`")

        pae_mean = (
            best.get("pae_mean") if best.get("pae_mean") is not None else qm.get("best_model_pae")
        )
        if isinstance(pae_mean, (int, float)):
            lines.append(f"- **Mean PAE**: `{pae_mean:.2f} Å`")

        ipde_mean = best.get("ipde_mean")
        if isinstance(ipde_mean, (int, float)):
            lines.append(f"- **Mean iPDE**: `{ipde_mean:.2f} Å`")

        qual = best.get("quality_assessment") or qm.get("quality_assessment")
        if qual:
            lines.append(f"- **Quality Assessment**: `{qual}`")

    expert = summary_data.get("expert_analysis") or {}
    expert_text = expert.get("analysis") if isinstance(expert, dict) else None
    if expert_text and str(expert_text).strip():
        lines.extend(["", "## Gemini Expert Analysis", "", str(expert_text).strip(), ""])

    return "\n".join(lines) + "\n"


def build_archive_readme(summary_data: dict[str, Any], included_files: list[str]) -> str:
    """Create a human-readable ``README.md`` describing the contents of ``artifacts_bundle.zip``."""
    job_id = summary_data.get("job_id", "unknown")
    model_type = summary_data.get("model_type", "unknown")
    analyzed_at = summary_data.get("analyzed_at", "")
    protein_info = (summary_data.get("summary") or {}).get("protein_info") or {}
    display_name = (protein_info.get("job_metadata") or {}).get("display_name") or job_id

    lines = [
        f"# FoldRun Artifact Bundle — {display_name}",
        "",
        f"- **Job ID**: `{job_id}`",
        f"- **Model Type**: `{model_type}`",
        f"- **Generated At**: `{analyzed_at}`",
        f"- **Total Files in Bundle**: `{len(included_files)}`",
        "",
        "## Directory Layout",
        "",
        "- `report/expert_analysis.md` — Standalone Gemini expert structural analysis report",
        "- `input/` — Pipeline input sequence or query specification (`.fasta`, `.json`, or `.yaml`)",
        "- `structures/` — Ranked 3D structure predictions (`.cif` or `.pdb`)",
        "- `plots/` — Ranked confidence & error plots (`_plddt.png`, `_pae.png` / `_pde.png`, `_iptm_matrix.png`)",
        "- `metrics/summary.json` — Consolidated rankings, confidence statistics, and metadata",
        "",
        "> Note: Large intermediate matrices (such as raw `.pkl`, `.npz`, full per-token JSONs, and MSA alignments) are excluded to keep this archive compact.",
        "",
        "## Ranked Predictions",
        "",
        "| Rank | Prediction | Mean pLDDT | Score / Confidence | Quality |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]

    preds = summary_data.get("all_predictions_summary") or summary_data.get("top_predictions") or []
    for idx, pred in enumerate(preds):
        rank = pred.get("plddt_rank") or pred.get("rank") or (idx + 1)
        name = pred.get("sample_name") or pred.get("model_name") or f"pred_{rank}"
        plddt = (
            f"{pred['plddt_mean']:.1f}"
            if isinstance(pred.get("plddt_mean"), (int, float))
            else "N/A"
        )
        score_val = pred.get("ranking_score", pred.get("ranking_confidence"))
        score_str = f"{score_val:.4f}" if isinstance(score_val, (int, float)) else "N/A"
        qual = pred.get("quality_assessment", "N/A")
        lines.append(f"| {rank} | `{name}` | {plddt} | {score_str} | `{qual}` |")

    lines.append("")
    return "\n".join(lines)
