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

"""RFD4-Proteina specific prediction analyzer and consolidator.

Analyzes generated protein and binder designs, evaluating:
- Self-consistency (sc_score)
- Steric clash score
- Designed sequence
- pLDDT (per-residue and mean)
- ipTM (interface quality)
- pTM (fold confidence)
- Ranking scores
- Explicit fast-mode (`~metrics` / `fast_no_self_consistency`) detection where
  self-consistency folding metrics are intentionally omitted (`None`).
"""

import io
import json
import logging
import math
import os
import tempfile
from datetime import UTC, datetime
from typing import Any

import numpy as np
from google import genai
from google.cloud import storage
from google.genai import types

try:
    import matplotlib

    matplotlib.use("Agg")  # Non-interactive backend for Cloud Run
    import matplotlib.pyplot as plt
except ImportError:  # pragma: no cover - optional in lightweight unit test envs
    matplotlib = None  # type: ignore[assignment]
    plt = None  # type: ignore[assignment]

from ._compat import (
    _sanitize_data,
    calculate_per_chain_plddt,
    calculate_plddt_stats,
    download_json_from_gcs,
    download_text_from_gcs,
    get_job_metadata,
    get_quality_assessment,
    is_prediction_analysis_blob,
    parse_cif_chains,
    plot_plddt_distribution,
    upload_to_gcs,
    validate_gcs_uri_in_bucket,
    wait_for_sibling_tasks,
)

logger = logging.getLogger(__name__)

FAST_MODE_LABEL = "Not computed (fast mode ~metrics)"


def _safe_float(val: Any) -> float | None:
    """Convert optional numeric value to float, returning None for NaN/Inf/None."""
    if val is None:
        return None
    try:
        fval = float(val)
        if math.isnan(fval) or math.isinf(fval):
            return None
        return fval
    except (TypeError, ValueError):
        return None


def _fmt(val: Any, spec: str = ".4f", fallback: str = "N/A") -> str:
    """Safely format an optional numeric value without fabricating placeholders."""
    fval = _safe_float(val)
    if fval is None:
        return fallback
    return format(fval, spec)


def _numeric_stats(values: list[float | None]) -> dict[str, float | None]:
    """Compute mean/min/max/range over non-None finite floats."""
    clean = [v for v in (_safe_float(x) for x in values) if v is not None]
    if not clean:
        return {"mean": None, "min": None, "max": None, "range": None}
    mn = float(np.min(clean))
    mx = float(np.max(clean))
    return {
        "mean": float(np.mean(clean)),
        "min": mn,
        "max": mx,
        "range": mx - mn,
    }


def _upload_json(data: dict[str, Any], gcs_uri: str, expected_bucket: str) -> None:
    """Serialize sanitized JSON to a temporary file and upload to GCS."""
    validate_gcs_uri_in_bucket(gcs_uri, expected_bucket=expected_bucket)
    sanitized = _sanitize_data(data)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tf:
        json.dump(sanitized, tf, indent=2)
        tmp_path = tf.name
    try:
        upload_to_gcs(tmp_path, gcs_uri, expected_bucket=expected_bucket)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def generate_gemini_expert_analysis(summary_data: dict) -> dict:
    """Generate expert analysis using Gemini via Google GenAI SDK for RFD4-Proteina designs."""
    try:
        best = summary_data["best_prediction"]
        summary = summary_data["summary"]
        top_preds = summary_data["top_predictions"][:3]

        is_fast_mode = (
            best.get("metrics_mode") == "fast_no_self_consistency"
            or best.get("metrics_computed") is False
            or summary.get("metrics_mode") == "fast_no_self_consistency"
        )
        na_label = FAST_MODE_LABEL if is_fast_mode else "N/A"

        chain_desc = ""
        for ci in best.get("chain_info", []):
            chain_desc += (
                f"- Chain {ci['chain_id']}: {ci['molecule_type']}, "
                f"{ci['residue_count']} residues, {ci['atom_count']} atoms\n"
            )

        design_mode = (
            summary.get("protein_info", {}).get("design_mode")
            or summary_data.get("protein_info", {}).get("design_mode")
            or "binder_design"
        )
        total_preds = summary.get("statistics", {}).get(
            "total_predictions", summary.get("total_predictions", 0)
        )
        mode_note = (
            "\n- **Execution Mode**: Fast backbone/sequence generation (`~metrics` / `fast_no_self_consistency`). "
            "Self-consistency refolding metrics (pLDDT, ipTM, pTM, sc_score) were intentionally skipped; "
            "evaluate structural geometry, chain composition, and run orthogonal validation (AF3/OF3/Boltz-2) to score candidates."
            if is_fast_mode
            else ""
        )

        prompt = f"""You are an expert computational structural biologist reviewing RFD4-Proteina generative protein/binder design results.

**RFD4-Proteina Framework Overview:**
- Generative protein modeling leveraging latent structural diffusion representations.
- Co-designs backbone conformations and sequences for unconditional monomers, motif scaffolds, and target binders.{mode_note}
- **Metric interpretation thresholds (when self-consistency metrics are enabled):**
  - ranking_score (0-1): Overall candidate quality. >0.7 = strong candidate, >0.8 = exceptional candidate.
  - sc_score (self-consistency score, lower is better): <0.5 = high structural reproducibility, <1.0 = acceptable.
  - clash_score (steric clash score, lower is better): <1.0 = well-packed interface, >2.0 = sterically strained.
  - ipTM (0-1): Interface quality between designed binder and target. >0.7 = reliable interface, >0.8 = high confidence.
  - pTM (0-1): Global fold accuracy of the designed complex. >0.8 = high confidence fold.
  - pLDDT (0-100): Per-residue confidence. >80 = well-folded core, >90 = highly stable.

**Design Run Summary:**
- Design Mode: {design_mode}
- Metrics Mode: {best.get("metrics_mode", summary.get("metrics_mode", "self_consistency"))}
- Total candidate designs: {total_preds}
- Best Ranking Score: {_fmt(best.get("ranking_score"), ".4f", na_label)}
- Best Self-Consistency (sc_score): {_fmt(best.get("sc_score"), ".4f", na_label)}
- Best Clash Score: {_fmt(best.get("clash_score"), ".4f", na_label)}
- Best ipTM: {_fmt(best.get("iptm"), ".4f", na_label)}
- Best pTM: {_fmt(best.get("ptm"), ".4f", na_label)}
- Best Mean pLDDT: {_fmt(best.get("plddt_mean"), ".1f", na_label)}
- Designed Sequence: `{best.get("designed_sequence") or "N/A"}`

**Complex Composition:**
{chain_desc}

**Top Candidates:**
"""
        for i, pred in enumerate(top_preds, 1):
            prompt += (
                f"{i}. {pred['sample_name']}: "
                f"ranking_score={_fmt(pred.get('ranking_score'), '.4f', na_label)}, "
                f"sc_score={_fmt(pred.get('sc_score'), '.4f', na_label)}, "
                f"ipTM={_fmt(pred.get('iptm'), '.4f', na_label)}, "
                f"mean_pLDDT={_fmt(pred.get('plddt_mean'), '.1f', na_label)}, "
                f"sequence=`{pred.get('designed_sequence') or ''}`\n"
            )

        prompt += """
Provide an expert evaluation structured into:
1. Executive Summary & Design Feasibility
2. Target Engagement & Interface Quality (ipTM, clash_score, binding mode — or orthogonal co-folding plan if run in fast mode)
3. Binder Folding & Structural Stability (pLDDT, sc_score)
4. Recommended Wet-Lab Candidates & Experimental Follow-Ups
"""

        gemini_api_key = os.environ.get("GEMINI_API_KEY")
        client = (
            genai.Client(api_key=gemini_api_key) if gemini_api_key else genai.Client()
        )

        response = client.models.generate_content(
            model=os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview"),
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=1500,
            ),
        )

        return {
            "status": "success",
            "analysis_text": response.text,
            "model": os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview"),
            "generated_at": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        logger.warning(f"Failed to generate Gemini expert analysis: {e}")
        return {
            "status": "skipped",
            "reason": str(e),
            "generated_at": datetime.now(UTC).isoformat(),
        }


def plot_ranking_comparison(
    top_predictions: list[dict],
    output_uri: str,
    expected_bucket: str | None = None,
) -> None:
    """Generate a bar plot comparing ranking scores and interface quality for top candidates."""
    if plt is None or not top_predictions:
        return
    try:
        bucket_name, object_path = validate_gcs_uri_in_bucket(
            output_uri, expected_bucket=expected_bucket
        )
        subset = top_predictions[:10]
        raw_ranking = [_safe_float(p.get("ranking_score")) for p in subset]
        raw_iptms = [_safe_float(p.get("iptm")) for p in subset]
        if all(v is None for v in raw_ranking) and all(v is None for v in raw_iptms):
            logger.info(
                "Skipping RFD4-Proteina ranking comparison plot: metrics not computed (fast mode)"
            )
            return

        names = [p.get("sample_name", f"sample_{i}") for i, p in enumerate(subset)]
        ranking_scores = [v if v is not None else 0.0 for v in raw_ranking]
        iptms = [v if v is not None else 0.0 for v in raw_iptms]

        x = np.arange(len(names))
        width = 0.35

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.bar(
            x - width / 2,
            ranking_scores,
            width,
            label="Ranking Score",
            color="#1a73e8",
        )
        ax.bar(x + width / 2, iptms, width, label="ipTM (Interface)", color="#34a853")

        ax.set_ylabel("Score (0 - 1)")
        ax.set_title("RFD4-Proteina Top Candidates Quality Comparison")
        ax.set_xticks(x)
        ax.set_xticklabels(names, rotation=30, ha="right")
        ax.set_ylim(0, 1.05)
        ax.legend(loc="upper right")
        ax.grid(axis="y", linestyle="--", alpha=0.5)

        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=150)
        plt.close(fig)
        buf.seek(0)

        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(object_path)
        blob.upload_from_file(buf, content_type="image/png")
        logger.info(f"Uploaded ranking comparison plot to {output_uri}")
    except Exception as e:
        logger.warning(f"Could not generate ranking comparison plot: {e}")


def analyze_sample(
    task_index: int,
    sample: dict[str, Any],
    bucket_name: str,
    analysis_path: str,
) -> None:
    """Analyze a single RFD4-Proteina design sample without fabricating metrics."""
    validate_gcs_uri_in_bucket(analysis_path, expected_bucket=bucket_name)
    if not analysis_path.endswith("/"):
        analysis_path += "/"

    sample_name = sample.get("sample_name", f"sample_{task_index}")
    cif_uri = sample.get("cif_uri")
    aggregated_uri = sample.get("aggregated_uri")
    output_uri = (
        sample.get("output_uri")
        or f"{analysis_path}prediction_{task_index}_analysis.json"
    )
    validate_gcs_uri_in_bucket(output_uri, expected_bucket=bucket_name)

    logger.info(f"Analyzing RFD4-Proteina sample {sample_name} (CIF: {cif_uri})")

    conf_data: dict[str, Any] = {}
    if aggregated_uri:
        validate_gcs_uri_in_bucket(aggregated_uri, expected_bucket=bucket_name)
        try:
            conf_data = download_json_from_gcs(
                aggregated_uri, expected_bucket=bucket_name
            )
        except Exception as e:
            logger.warning(
                f"Could not download confidence data from {aggregated_uri}: {e}"
            )

    chain_info: list[dict] = []
    plddt_scores: list[float] = []
    if cif_uri:
        validate_gcs_uri_in_bucket(cif_uri, expected_bucket=bucket_name)
        try:
            cif_content = download_text_from_gcs(cif_uri, expected_bucket=bucket_name)
            chain_info, plddt_scores = parse_cif_chains(cif_content, extract_plddt=True)
        except Exception as e:
            logger.warning(f"Could not parse CIF structure from {cif_uri}: {e}")

    plddt_stats = calculate_plddt_stats(plddt_scores)
    per_chain_plddt = calculate_per_chain_plddt(plddt_scores, chain_info)

    raw_plddt = _safe_float(conf_data.get("plddt"))
    if raw_plddt is not None:
        plddt_mean = raw_plddt * 100.0 if 0.0 < raw_plddt <= 1.0 else raw_plddt
    else:
        plddt_mean = plddt_stats.get("mean")

    iptm = _safe_float(conf_data.get("iptm"))
    ptm = _safe_float(conf_data.get("ptm"))
    sc_score = _safe_float(conf_data.get("sc_score"))
    clash_score = _safe_float(conf_data.get("clash_score"))

    raw_ranking = _safe_float(conf_data.get("ranking_score"))
    if raw_ranking is not None:
        ranking_score = raw_ranking
    elif plddt_mean is not None and iptm is not None:
        ranking_score = (plddt_mean / 100.0) * 0.5 + iptm * 0.5
    elif plddt_mean is not None and ptm is not None:
        ranking_score = (plddt_mean / 100.0) * 0.5 + ptm * 0.5
    else:
        ranking_score = None

    is_fast_mode = (
        conf_data.get("metrics_computed") is False
        or conf_data.get("metrics_mode") == "fast_no_self_consistency"
        or all(v is None for v in (plddt_mean, iptm, sc_score, ranking_score))
    )
    metrics_computed = not is_fast_mode
    metrics_mode = (
        "fast_no_self_consistency"
        if is_fast_mode
        else str(conf_data.get("metrics_mode", "self_consistency"))
    )

    designed_sequence = conf_data.get("designed_sequence", "")
    design_mode = conf_data.get("design_mode", "binder_design")
    quality_assessment = get_quality_assessment(plddt_mean)

    analysis_result = {
        "sample_name": sample_name,
        "sample_index": task_index,
        "cif_uri": cif_uri,
        "aggregated_uri": aggregated_uri,
        "metrics_computed": metrics_computed,
        "metrics_mode": metrics_mode,
        "ranking_score": ranking_score,
        "sc_score": sc_score,
        "clash_score": clash_score,
        "iptm": iptm,
        "ptm": ptm,
        "plddt_mean": plddt_mean,
        "plddt_min": plddt_stats.get("min"),
        "plddt_max": plddt_stats.get("max"),
        "designed_sequence": designed_sequence,
        "design_mode": design_mode,
        "chain_info": chain_info,
        "plddt_scores": plddt_stats.get("per_residue", plddt_scores),
        "plddt_distribution": plddt_stats.get("distribution", {}),
        "per_chain_plddt": per_chain_plddt,
        "quality_assessment": quality_assessment,
        "analyzed_at": datetime.now(UTC).isoformat(),
    }

    _upload_json(analysis_result, output_uri, bucket_name)
    logger.info(f"Uploaded sample analysis to {output_uri}")


def consolidate_analyses(
    task_config: dict[str, Any],
    bucket_name: str,
    analysis_path: str,
) -> None:
    """Consolidate individual sample analyses into summary.json."""
    validate_gcs_uri_in_bucket(analysis_path, expected_bucket=bucket_name)
    if not analysis_path.endswith("/"):
        analysis_path += "/"

    logger.info("Consolidating RFD4-Proteina sample analyses into summary.json")

    predictions = task_config.get("predictions", [])
    total_samples = len(predictions)
    job_id = task_config.get("job_id", "unknown")
    query_yaml_uri = task_config.get("query_yaml_uri")

    _, prefix = validate_gcs_uri_in_bucket(analysis_path, expected_bucket=bucket_name)
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    results: list[dict[str, Any]] = []
    blobs = list(bucket.list_blobs(prefix=prefix))
    blob_uris = [
        f"gs://{bucket_name}/{b.name}"
        for b in blobs
        if is_prediction_analysis_blob(b.name)
    ]

    if blob_uris:
        for uri in blob_uris:
            try:
                results.append(download_json_from_gcs(uri, expected_bucket=bucket_name))
            except Exception as e:
                logger.warning(f"Error reading {uri}: {e}")
    else:
        for i in range(total_samples):
            blob_name = f"{prefix}prediction_{i}_analysis.json"
            blob = bucket.blob(blob_name)
            if blob.exists():
                try:
                    raw = blob.download_as_text()
                    if isinstance(raw, (bytes, str)):
                        data = json.loads(raw, parse_constant=lambda _: None)
                        results.append(_sanitize_data(data))
                except Exception as e:
                    logger.warning(f"Error reading {blob_name}: {e}")

    job_metadata = get_job_metadata(job_id)

    input_query_yaml = None
    if query_yaml_uri:
        try:
            validate_gcs_uri_in_bucket(query_yaml_uri, expected_bucket=bucket_name)
            input_query_yaml = download_text_from_gcs(
                query_yaml_uri, expected_bucket=bucket_name
            )
        except Exception as e:
            logger.warning(
                f"Could not read RFD4-Proteina query YAML {query_yaml_uri}: {e}"
            )

    if not results:
        logger.warning(
            f"No RFD4-Proteina analysis results found at {analysis_path} "
            f"(expected={total_samples}); writing diagnostic summary.json"
        )
        protein_info = {
            "model_type": "rfd4_proteina",
            "design_mode": "binder_design",
            "sequence_length": 0,
            "total_predictions": 0,
            "job_metadata": job_metadata,
            "chains": [],
            "chain_composition": [],
            "input_query_yaml": input_query_yaml,
            "metrics_mode": "fast_no_self_consistency",
        }
        empty_summary = {
            "job_id": job_id,
            "model_type": "rfd4_proteina",
            "status": "empty",
            "created_at": datetime.now(UTC).isoformat(),
            "summary": {
                "total_predictions": 0,
                "metrics_mode": "fast_no_self_consistency",
                "metrics_computed": False,
                "protein_info": protein_info,
                "quality_metrics": {
                    "best_ranking_score": None,
                    "best_sc_score": None,
                    "best_clash_score": None,
                    "best_iptm": None,
                    "best_ptm": None,
                    "best_plddt": None,
                    "quality_assessment": "uncomputed",
                },
                "statistics": {
                    "total_predictions": 0,
                    "analyzed_successfully": 0,
                    "ranking_score_stats": _numeric_stats([]),
                    "plddt_stats": _numeric_stats([]),
                },
            },
            "input_Parameters": {"protein_info": protein_info},
            "protein_info": protein_info,
            "job_metadata": job_metadata,
            "best_prediction": None,
            "top_predictions": [],
            "all_predictions_summary": [],
            "all_predictions": [],
            "plots": {},
            "expert_analysis": {
                "status": "skipped",
                "reason": "No design outputs found for analysis.",
            },
        }
        _upload_json(empty_summary, f"{analysis_path}summary.json", bucket_name)
        return

    results.sort(
        key=lambda r: (
            r.get("ranking_score") is not None,
            r["ranking_score"] if r.get("ranking_score") is not None else -1e9,
        ),
        reverse=True,
    )
    for i, r in enumerate(results):
        r["rank"] = i + 1
    best_prediction = results[0]

    ranking_stats = _numeric_stats([r.get("ranking_score") for r in results])
    sc_stats = _numeric_stats([r.get("sc_score") for r in results])
    clash_stats = _numeric_stats([r.get("clash_score") for r in results])
    iptm_stats = _numeric_stats([r.get("iptm") for r in results])
    plddt_stats_all = _numeric_stats([r.get("plddt_mean") for r in results])

    all_fast = all(
        r.get("metrics_mode") == "fast_no_self_consistency"
        or r.get("metrics_computed") is False
        for r in results
    )
    any_fast = any(
        r.get("metrics_mode") == "fast_no_self_consistency"
        or r.get("metrics_computed") is False
        for r in results
    )
    metrics_mode = (
        "fast_no_self_consistency"
        if all_fast
        else ("mixed" if any_fast else "self_consistency")
    )

    total_residues = sum(
        ci.get("residue_count", 0) for ci in best_prediction.get("chain_info", [])
    )
    protein_info = {
        "model_type": "rfd4_proteina",
        "design_mode": best_prediction.get("design_mode", "binder_design"),
        "sequence_length": total_residues,
        "total_predictions": len(results),
        "job_metadata": job_metadata,
        "chains": best_prediction.get("chain_info", []),
        "chain_composition": best_prediction.get("chain_info", []),
        "input_query_yaml": input_query_yaml,
        "metrics_mode": metrics_mode,
    }

    summary = {
        "total_predictions": len(results),
        "metrics_mode": metrics_mode,
        "metrics_computed": not all_fast,
        "mean_ranking_score": ranking_stats["mean"],
        "max_ranking_score": ranking_stats["max"],
        "min_ranking_score": ranking_stats["min"],
        "mean_sc_score": sc_stats["mean"],
        "best_sc_score": sc_stats["min"],  # lower is better
        "mean_clash_score": clash_stats["mean"],
        "mean_iptm": iptm_stats["mean"],
        "max_iptm": iptm_stats["max"],
        "mean_plddt": plddt_stats_all["mean"],
        "max_plddt": plddt_stats_all["max"],
        "protein_info": protein_info,
        "quality_metrics": {
            "best_ranking_score": best_prediction.get("ranking_score"),
            "best_sc_score": best_prediction.get("sc_score"),
            "best_clash_score": best_prediction.get("clash_score"),
            "best_iptm": best_prediction.get("iptm"),
            "best_ptm": best_prediction.get("ptm"),
            "best_plddt": best_prediction.get("plddt_mean"),
            "best_designed_sequence": best_prediction.get("designed_sequence", ""),
            "quality_assessment": best_prediction.get(
                "quality_assessment", "uncomputed"
            ),
        },
        "statistics": {
            "total_predictions": len(results),
            "analyzed_successfully": len(results),
            "ranking_score_stats": ranking_stats,
            "sc_score_stats": sc_stats,
            "iptm_stats": iptm_stats,
            "plddt_stats": plddt_stats_all,
        },
    }

    summary_data: dict[str, Any] = {
        "job_id": job_id,
        "model_type": "rfd4_proteina",
        "metrics_mode": metrics_mode,
        "created_at": datetime.now(UTC).isoformat(),
        "summary": summary,
        "input_Parameters": {"protein_info": protein_info},
        "best_prediction": best_prediction,
        "top_predictions": results[:10],
        "all_predictions_summary": results,
        "all_predictions": [
            {
                "sample_name": r["sample_name"],
                "metrics_computed": r.get("metrics_computed", True),
                "metrics_mode": r.get("metrics_mode", metrics_mode),
                "ranking_score": r.get("ranking_score"),
                "sc_score": r.get("sc_score"),
                "clash_score": r.get("clash_score"),
                "iptm": r.get("iptm"),
                "ptm": r.get("ptm"),
                "plddt_mean": r.get("plddt_mean"),
                "designed_sequence": r.get("designed_sequence", ""),
                "cif_uri": r.get("cif_uri"),
            }
            for r in results
        ],
        "protein_info": protein_info,
        "job_metadata": job_metadata,
    }

    gemini_eval = generate_gemini_expert_analysis(summary_data)
    summary_data["gemini_analysis"] = gemini_eval
    summary_data["gemini_expert_analysis"] = gemini_eval
    summary_data["expert_analysis"] = {
        "status": gemini_eval.get("status", "success"),
        "analysis": gemini_eval.get("analysis_text") or gemini_eval.get("analysis", ""),
        "model": gemini_eval.get("model", "gemini-3.1-pro-preview"),
        "generated_at": gemini_eval.get("generated_at"),
    }

    summary_data["plots"] = {}
    if any(
        _safe_float(r.get("ranking_score")) is not None
        or _safe_float(r.get("iptm")) is not None
        for r in results[:10]
    ):
        plot_uri = f"{analysis_path}ranking_comparison.png"
        plot_ranking_comparison(results, plot_uri, expected_bucket=bucket_name)
        summary_data["plots"]["ranking_comparison"] = plot_uri

    if best_prediction.get("plddt_scores"):
        plddt_plot_uri = f"{analysis_path}best_candidate_plddt.png"
        plot_plddt_distribution(
            best_prediction["plddt_scores"],
            best_prediction.get("sample_name", "Best Candidate"),
            plddt_plot_uri,
            best_prediction.get("chain_info"),
            expected_bucket=bucket_name,
        )
        summary_data["plots"]["best_plddt_distribution"] = plddt_plot_uri

    summary_uri = f"{analysis_path}summary.json"
    _upload_json(summary_data, summary_uri, bucket_name)
    logger.info(f"Consolidated analysis written to {summary_uri}")


def run_task(
    task_index: int,
    task_count: int,
    task_config: dict[str, Any],
    bucket_name: str,
    analysis_path: str,
) -> None:
    """Run analysis task (handles both shard worker and master consolidator)."""
    validate_gcs_uri_in_bucket(analysis_path, expected_bucket=bucket_name)
    if not analysis_path.endswith("/"):
        analysis_path += "/"

    predictions = task_config.get("predictions", [])
    total_samples = len(predictions)

    logger.info(
        f"Task {task_index}/{task_count}: Processing RFD4-Proteina ({total_samples} total designs)"
    )

    stride = max(1, task_count)
    assigned_indices = list(range(task_index, total_samples, stride))
    logger.info(f"Task {task_index}: Assigned sample indices: {assigned_indices}")

    for idx in assigned_indices:
        analyze_sample(idx, predictions[idx], bucket_name, analysis_path)

    if task_index == 0:
        if task_count > 1 and total_samples > 1:
            logger.info("Task 0: Waiting for sibling tasks to complete...")
            wait_for_sibling_tasks(bucket_name, analysis_path, total_samples)
        consolidate_analyses(task_config, bucket_name, analysis_path)

    logger.info(f"Task {task_index}: RFD4-Proteina analysis task finished")
