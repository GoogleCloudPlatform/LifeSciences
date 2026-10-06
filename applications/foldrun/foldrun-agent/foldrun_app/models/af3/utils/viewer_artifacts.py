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

"""Utilities for generating FoldRun Viewer summary.json and diagnostic plots for AlphaFold 3."""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


def compute_residue_plddts_from_cif(
    cif_text: str,
    atom_plddts: list[float] | None = None,
) -> list[float]:
    """Compute per-residue pLDDT scores from an mmCIF structure and optional per-atom pLDDT array.

    If `atom_plddts` is absent or empty, attempts to extract B-factor (pLDDT) column
    values directly from `ATOM` / `HETATM` records in the mmCIF text.
    """
    atom_lines: list[tuple[str, str, float | None]] = []
    for line in (cif_text or "").splitlines():
        if line.startswith(("ATOM ", "HETATM ")):
            parts = line.split()
            if len(parts) >= 15:
                chain_id = parts[6]
                res_seq = parts[8]
                try:
                    bfac = float(parts[14])
                except (ValueError, IndexError):
                    bfac = None
                atom_lines.append((chain_id, res_seq, bfac))

    if not atom_lines:
        if atom_plddts:
            return [float(x) for x in atom_plddts]
        return []

    res_scores: dict[tuple[str, str], list[float]] = {}
    res_order: list[tuple[str, str]] = []
    use_atom_list = bool(atom_plddts) and len(atom_plddts) == len(atom_lines)

    for idx, (chain_id, res_seq, bfac) in enumerate(atom_lines):
        score = float(atom_plddts[idx]) if use_atom_list else bfac
        if score is None:
            continue
        key = (chain_id, res_seq)
        if key not in res_scores:
            res_scores[key] = []
            res_order.append(key)
        res_scores[key].append(score)

    if not res_order and atom_plddts:
        return [float(x) for x in atom_plddts]

    return [float(np.mean(res_scores[k])) for k in res_order]


def generate_af3_plots_bytes(
    res_plddts: list[float],
    pae_matrix: list[list[float]] | None,
    title_suffix: str,
) -> tuple[bytes | None, bytes | None]:
    """Render pLDDT line plot and PAE heatmap to PNG bytes."""
    plddt_png: bytes | None = None
    pae_png: bytes | None = None

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        if res_plddts:
            fig, ax = plt.subplots(figsize=(8, 3.6), dpi=140)
            xs = np.arange(1, len(res_plddts) + 1)
            ax.axhspan(90, 100, color="#0053D6", alpha=0.12, label="Very High (>90)")
            ax.axhspan(70, 90, color="#65CBF3", alpha=0.14, label="Confident (70-90)")
            ax.axhspan(50, 70, color="#FFDB13", alpha=0.14, label="Low (50-70)")
            ax.axhspan(0, 50, color="#FF7D45", alpha=0.12, label="Very Low (<50)")
            ax.plot(xs, res_plddts, color="#0053D6", linewidth=2.0)
            ax.set_xlim(1, max(len(res_plddts), 2))
            ax.set_ylim(0, 100)
            ax.set_xlabel("Residue / Token Position")
            ax.set_ylabel("pLDDT")
            ax.set_title(
                f"AlphaFold 3 Per-Residue Confidence (pLDDT) — {title_suffix}",
                fontsize=10,
                fontweight="bold",
            )
            ax.legend(loc="lower right", fontsize=8, framealpha=0.9)
            ax.grid(True, linestyle="--", alpha=0.3)
            fig.tight_layout()
            buf = io.BytesIO()
            fig.savefig(buf, format="png")
            plt.close(fig)
            plddt_png = buf.getvalue()

        if pae_matrix and len(pae_matrix) > 0:
            fig2, ax2 = plt.subplots(figsize=(5.2, 4.6), dpi=140)
            pae_arr = np.array(pae_matrix, dtype=float)
            im = ax2.imshow(pae_arr, cmap="Greens_r", vmin=0, vmax=31.75, origin="upper")
            cbar = fig2.colorbar(im, ax=ax2, fraction=0.046, pad=0.04)
            cbar.set_label("Expected Position Error (Å)")
            ax2.set_xlabel("Scored Residue")
            ax2.set_ylabel("Aligned Residue")
            ax2.set_title(
                f"Predicted Aligned Error (PAE) — {title_suffix}",
                fontsize=10,
                fontweight="bold",
            )
            fig2.tight_layout()
            buf2 = io.BytesIO()
            fig2.savefig(buf2, format="png")
            plt.close(fig2)
            pae_png = buf2.getvalue()
    except Exception as exc:
        logger.warning(f"Failed to render AF3 diagnostic plots: {exc}")

    return plddt_png, pae_png


def build_and_upload_af3_viewer_artifacts(
    *,
    bucket: Any,
    bucket_name: str,
    job_prefix: str,
    job_name: str,
    af3_query: dict[str, Any],
    cif_text: str,
    cif_uri: str,
    summary_confidences: dict[str, Any],
    atom_plddts: list[float] | None,
    pae_matrix: list[list[float]] | None,
    msa_free: bool,
    ranking_score: float | None,
    elapsed_sec: float | None = None,
) -> dict[str, Any]:
    """Generate and upload FoldRun Viewer summary.json and plots to GCS.

    Returns:
        Updated metrics dictionary including mean_plddt and mean_pae.
    """
    res_plddts = compute_residue_plddts_from_cif(cif_text, atom_plddts)
    if res_plddts:
        plddt_arr = np.array(res_plddts, dtype=float)
        plddt_mean = round(float(np.mean(plddt_arr)), 2)
        plddt_median = round(float(np.median(plddt_arr)), 2)
        plddt_min = round(float(np.min(plddt_arr)), 2)
        plddt_max = round(float(np.max(plddt_arr)), 2)
        plddt_dist = {
            "very_low_confidence": int(np.sum(plddt_arr < 50)),
            "low_confidence": int(np.sum((plddt_arr >= 50) & (plddt_arr < 70))),
            "high_confidence": int(np.sum((plddt_arr >= 70) & (plddt_arr < 90))),
            "very_high_confidence": int(np.sum(plddt_arr >= 90)),
        }
    else:
        raw_mean = summary_confidences.get("mean_plddt")
        plddt_mean = round(float(raw_mean), 2) if raw_mean is not None else 0.0
        plddt_median = plddt_mean
        plddt_min = plddt_mean
        plddt_max = plddt_mean
        plddt_dist = {
            "very_low_confidence": 0,
            "low_confidence": 0,
            "high_confidence": 0,
            "very_high_confidence": 0,
        }

    if pae_matrix and len(pae_matrix) > 0:
        pae_arr = np.array(pae_matrix, dtype=float)
        pae_mean = round(float(np.mean(pae_arr)), 2)
        pae_median = round(float(np.median(pae_arr)), 2)
        pae_min = round(float(np.min(pae_arr)), 2)
        pae_max = round(float(np.max(pae_arr)), 2)
    else:
        pae_mean = summary_confidences.get("mean_pae")
        pae_median = pae_mean
        pae_min = pae_mean
        pae_max = pae_mean

    mode_label = "Zero-MSA (--msa-free)" if msa_free else "Full MSA + Templates"
    plddt_png, pae_png = generate_af3_plots_bytes(
        res_plddts=res_plddts,
        pae_matrix=pae_matrix,
        title_suffix=f"{job_name} ({mode_label})",
    )

    plots_dict: dict[str, str] = {}
    if plddt_png:
        plddt_blob_path = f"{job_prefix}/analysis/plddt_plot_0.png"
        bucket.blob(plddt_blob_path).upload_from_string(plddt_png, content_type="image/png")
        plots_dict["plddt_plot"] = f"gs://{bucket_name}/{plddt_blob_path}"
    if pae_png:
        pae_blob_path = f"{job_prefix}/analysis/pae_plot_0.png"
        bucket.blob(pae_blob_path).upload_from_string(pae_png, content_type="image/png")
        plots_dict["pae_plot"] = f"gs://{bucket_name}/{pae_blob_path}"

    ptm = summary_confidences.get("ptm")
    iptm = summary_confidences.get("iptm")
    has_clash = float(summary_confidences.get("has_clash", 0.0) or 0.0)
    fraction_disordered = float(summary_confidences.get("fraction_disordered", 0.0) or 0.0)
    eff_rank_score = (
        round(float(ranking_score), 4)
        if ranking_score is not None
        else (round(float(ptm), 4) if ptm is not None else 0.0)
    )

    if plddt_mean >= 90:
        quality_assessment = "very_high_confidence"
    elif plddt_mean >= 70:
        quality_assessment = "high_confidence"
    elif plddt_mean >= 50:
        quality_assessment = "low_confidence"
    else:
        quality_assessment = "very_low_confidence"

    chain_composition = []
    total_residues = 0
    for entity in af3_query.get("sequences", []):
        for mol_type in ("protein", "rna", "dna"):
            if mol_type in entity:
                c_data = entity[mol_type]
                seq_str = c_data.get("sequence", "")
                total_residues += len(seq_str)
                chain_composition.append(
                    {
                        "chain_id": c_data.get("id", "A"),
                        "molecule_type": mol_type,
                        "residue_count": len(seq_str),
                    }
                )
        if "ligand" in entity:
            l_data = entity["ligand"]
            comp_ids = l_data.get("ccdCodes") or (
                [l_data["smiles"]] if l_data.get("smiles") else ["LIG"]
            )
            chain_composition.append(
                {
                    "chain_id": l_data.get("id", "L"),
                    "molecule_type": "ligand",
                    "comp_ids": comp_ids,
                }
            )
        if "ion" in entity:
            i_data = entity["ion"]
            chain_composition.append(
                {
                    "chain_id": i_data.get("id", "I"),
                    "molecule_type": "ligand",
                    "comp_ids": [i_data.get("ion", "ION")],
                }
            )

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    duration_str = f"{int(elapsed_sec // 60)}m {int(elapsed_sec % 60)}s" if elapsed_sec else "N/A"

    pred_entry = {
        k: v
        for k, v in {
            "rank": 1,
            "sample_name": "af3_seed1_sample0" if not msa_free else "af3_msa_free_sample0",
            "model_name": "AlphaFold 3",
            "cif_uri": cif_uri,
            "uri": cif_uri,
            "ranking_score": eff_rank_score,
            "ranking_confidence": eff_rank_score,
            "ptm": round(float(ptm), 4) if ptm is not None else None,
            "iptm": round(float(iptm), 4) if iptm is not None else None,
            "has_clash": has_clash,
            "fraction_disordered": fraction_disordered,
            "quality_assessment": quality_assessment,
            "plddt_mean": plddt_mean,
            "plddt_median": plddt_median,
            "plddt_min": plddt_min,
            "plddt_max": plddt_max,
            "plddt_distribution": plddt_dist,
            "pae_mean": pae_mean,
            "pae_median": pae_median,
            "pae_min": pae_min,
            "pae_max": pae_max,
            "chain_pair_iptm": summary_confidences.get("chain_pair_iptm"),
            "chain_pair_pae_min": summary_confidences.get("chain_pair_pae_min"),
            "plots": plots_dict,
        }.items()
        if v is not None
    }

    expert_md = (
        f"### AlphaFold 3 Structural Quality Assessment — `{job_name}`\n\n"
        f"- **Execution Mode**: {mode_label}\n"
        f"- **Turnaround Time**: `{duration_str}`\n"
        f"- **Global Confidence**: Ranking Score = `{eff_rank_score}`, "
        f"pTM = `{ptm if ptm is not None else 'N/A'}`, "
        f"Mean pLDDT = `{plddt_mean}`, "
        f"Mean PAE = `{pae_mean if pae_mean is not None else 'N/A'} Å`\n"
        f"- **Stereochemical Quality**: Clash indicator = `{has_clash}`, "
        f"Fraction disordered = `{fraction_disordered}`\n"
    )

    quality_metrics = {
        k: v
        for k, v in {
            "best_model": pred_entry["sample_name"],
            "best_model_plddt": plddt_mean,
            "best_model_pae": pae_mean,
            "best_ranking_score": eff_rank_score,
            "best_ptm": round(float(ptm), 4) if ptm is not None else None,
            "best_iptm": round(float(iptm), 4) if iptm is not None else None,
            "quality_assessment": quality_assessment,
            "mean_plddt_across_models": plddt_mean,
        }.items()
        if v is not None
    }

    viewer_summary = {
        "job_id": job_name,
        "model_type": "alphafold3",
        "analyzed_at": now_iso,
        "total_predictions": 1,
        "summary": {
            "statistics": {
                "total_predictions": 1,
                "analyzed_successfully": 1,
            },
            "quality_metrics": quality_metrics,
            "protein_info": {
                "model_type": "alphafold3",
                "sequence_length": total_residues or len(res_plddts),
                "num_chains": len(chain_composition) or 1,
                "chain_composition": chain_composition,
                "input_query_json": af3_query,
                "job_metadata": {
                    "job_id": job_name,
                    "display_name": f"{job_name} ({mode_label})",
                    "state": "PIPELINE_STATE_SUCCEEDED",
                    "duration_formatted": duration_str,
                    "create_time": now_iso,
                    "labels": {
                        "mode": "msa-free" if msa_free else "full-msa-630gb",
                        "engine": "alphafold3-v3-0-4",
                    },
                },
            },
            "recommendations": [
                "Inspect per-residue pLDDT and PAE matrices before downstream docking or free-energy perturbation.",
                "Compare --msa-free rapid screening vs. Full-MSA template-guided refinement for flexible loop regions.",
            ],
        },
        "best_prediction": pred_entry,
        "top_predictions": [pred_entry],
        "all_predictions_summary": [pred_entry],
        "expert_analysis": {
            "status": "success",
            "model": "gemini-2.5-flash",
            "generated_at": now_iso,
            "analysis": expert_md,
        },
    }

    summary_blob = bucket.blob(f"{job_prefix}/analysis/summary.json")
    summary_blob.upload_from_string(
        json.dumps(viewer_summary, indent=2), content_type="application/json"
    )

    return {
        "mean_plddt": plddt_mean,
        "mean_pae": pae_mean,
        "summary_uri": f"gs://{bucket_name}/{job_prefix}/analysis/summary.json",
    }
