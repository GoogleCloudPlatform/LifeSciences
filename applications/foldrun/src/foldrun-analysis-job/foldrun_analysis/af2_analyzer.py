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

"""AlphaFold2 specific prediction analyzer and consolidator."""

import json
import logging
import os
import pickle
import sys
import time
from datetime import datetime, timezone

import matplotlib
import numpy as np
from google import genai
from google.cloud import storage
from google.genai import types

matplotlib.use("Agg")  # Use non-interactive backend for Cloud Run
import matplotlib.pyplot as plt

from .shared_utils import (
    calculate_plddt_stats,
    download_from_gcs,
    download_json_from_gcs,
    get_quality_assessment,
    upload_to_gcs,
)

logger = logging.getLogger(__name__)


def load_raw_prediction(pickle_path: str) -> dict:
    """Load raw prediction pickle file."""
    with open(pickle_path, "rb") as f:
        raw_prediction = pickle.load(f)
    return raw_prediction


def calculate_pae_stats(raw_prediction: dict) -> dict | None:
    """Calculate PAE statistics (if available)."""
    if "predicted_aligned_error" not in raw_prediction:
        return None

    pae = raw_prediction["predicted_aligned_error"]
    max_pae = raw_prediction.get("max_predicted_aligned_error", np.max(pae))

    stats = {
        "mean": float(np.mean(pae)),
        "median": float(np.median(pae)),
        "min": float(np.min(pae)),
        "max": float(np.max(pae)),
        "max_predicted": float(max_pae),
    }

    return stats


def plot_plddt(plddt_scores: np.ndarray, model_name: str, output_path: str) -> None:
    """Generate pLDDT per-residue plot."""
    fig, ax = plt.subplots(figsize=(12, 4))

    residues = np.arange(1, len(plddt_scores) + 1)
    ax.plot(residues, plddt_scores, linewidth=1.5, color="#1f77b4")

    # Add confidence band backgrounds
    ax.axhspan(90, 100, alpha=0.1, color="green", label="Very High (≥90)")
    ax.axhspan(70, 90, alpha=0.1, color="yellow", label="High (70-90)")
    ax.axhspan(50, 70, alpha=0.1, color="orange", label="Low (50-70)")
    ax.axhspan(0, 50, alpha=0.1, color="red", label="Very Low (<50)")

    # Formatting
    ax.set_xlabel("Residue Position", fontsize=10)
    ax.set_ylabel("pLDDT Score", fontsize=10)
    ax.set_title(
        f"Per-Residue Confidence (pLDDT) - {model_name}", fontsize=11, fontweight="bold"
    )
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.3, linestyle="--")
    ax.legend(loc="lower right", fontsize=8)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Generated pLDDT plot: {output_path}")


def plot_pae(
    pae_matrix: np.ndarray, model_name: str, output_path: str, max_pae: float = 31.0
) -> None:
    """Generate PAE heatmap plot."""
    fig, ax = plt.subplots(figsize=(8, 7))

    cmap = plt.cm.Greens_r
    im = ax.imshow(pae_matrix, vmin=0, vmax=max_pae, cmap=cmap)

    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label(
        "Expected Position Error (Å)", rotation=270, labelpad=20, fontsize=10
    )

    # Formatting
    ax.set_xlabel("Scored Residue", fontsize=10)
    ax.set_ylabel("Aligned Residue", fontsize=10)
    ax.set_title(
        f"Predicted Aligned Error (PAE) - {model_name}", fontsize=11, fontweight="bold"
    )

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Generated PAE plot: {output_path}")


def generate_gemini_expert_analysis(summary_data: dict) -> dict:
    """Generate expert analysis using Gemini via Google GenAI SDK."""
    try:
        best = summary_data["best_prediction"]
        summary = summary_data["summary"]
        top_preds = summary_data["top_predictions"][:3]

        pae_mean_str = (
            f"{best['pae_mean']:.2f}" if best["pae_mean"] is not None else "N/A"
        )

        is_multimer = "multimer" in best.get("model_name", "")
        model_type_str = "multimer" if is_multimer else "monomer"
        ranking_formula = "0.8 * ipTM + 0.2 * pTM" if is_multimer else "average pLDDT"

        plddt_means = [p["plddt_mean"] for p in top_preds]
        model_agreement = (
            max(plddt_means) - min(plddt_means) if len(plddt_means) > 1 else 0
        )

        prompt = f"""You are an expert structural biologist and genomics specialist reviewing AlphaFold2 prediction results.

**AlphaFold2 Reference (use to interpret results):**
- AF2 runs 5 distinct neural network architectures per prediction (model_1 through model_5). These are different trained models, not seeds.
- This is a **{model_type_str}** prediction. Ranking formula: {ranking_formula}.
- **Metric interpretation thresholds:**
  - pLDDT (0-100): Per-residue confidence. >90 = very high (drug design quality). 70-90 = good (backbone reliable). 50-70 = low (loops, disordered). <50 = likely intrinsically disordered region (real biology, not failure).
  - PAE (Angstroms): Predicted aligned error. <5A within a domain = well-defined fold. <5A between domains = reliable domain arrangement. >15A between domains = uncertain relative orientation.
  - ranking_confidence: {"0.8*ipTM + 0.2*pTM for multimer" if is_multimer else "average pLDDT for monomer"}. Higher is better.
- **Key interpretation patterns:**
  - Low pLDDT (<50) in known IDRs is correct — these regions are genuinely flexible
  - Multi-domain proteins: individual domains may be well-predicted but relative orientation uncertain (high inter-domain PAE)
  - Model agreement: pLDDT range of {model_agreement:.1f} across top models ({"high consistency" if model_agreement < 5 else "moderate variation" if model_agreement < 15 else "significant variation"})

**Prediction Summary:**
- Job ID: {summary_data["job_id"]}
- Prediction Type: {model_type_str}
- Sequence Length: {summary["protein_info"]["sequence_length"]} residues
- Total Predictions: {summary["protein_info"]["total_predictions"]}
- Best Model: {best["model_name"]} (Rank #{best["rank"]})

**Quality Metrics (Best Model):**
- Mean pLDDT: {best["plddt_mean"]:.2f}
- Median pLDDT: {best["plddt_median"]:.2f}
- pLDDT Range: {best["plddt_min"]:.2f} - {best["plddt_max"]:.2f}
- Mean PAE: {pae_mean_str} A
- Ranking Confidence: {best["ranking_confidence"]:.4f}
- Quality Assessment: {best["quality_assessment"].replace("_", " ").title()}

**Confidence Distribution (Best Model):**
- Very High (90-100): {best["plddt_distribution"]["very_high_confidence"]} residues
- High (70-90): {best["plddt_distribution"]["high_confidence"]} residues
- Low (50-70): {best["plddt_distribution"]["low_confidence"]} residues
- Very Low (<50): {best["plddt_distribution"]["very_low_confidence"]} residues

**Top {len(top_preds)} Models Comparison:**
"""
        for i, pred in enumerate(top_preds, 1):
            pred_pae_str = (
                f"{pred['pae_mean']:.2f}" if pred["pae_mean"] is not None else "N/A"
            )
            prompt += f"\n{i}. {pred['model_name']}: pLDDT {pred['plddt_mean']:.2f}, PAE {pred_pae_str} A, ranking {pred['ranking_confidence']:.4f}"

        if summary["protein_info"].get("fasta_sequence"):
            fasta_seq = summary["protein_info"]["fasta_sequence"]
            fasta_header = summary["protein_info"].get(
                "fasta_header", "Unknown protein"
            )
            prompt += f"""

**Input Protein Sequence:**
>{fasta_header}
{fasta_seq}
"""

        prompt += """

**Expert Analysis Request:**

Provide a comprehensive expert assessment covering these areas:

1. **Overall Structure Quality**: Evaluate the confidence and reliability of this prediction based on pLDDT scores and distribution
2. **Structural Confidence Regions**: Identify which parts of the protein are well-predicted vs uncertain
3. **Model Agreement**: Analyze the consistency between top-ranked models
4. **Predicted Aligned Error (PAE) Interpretation**: What does the PAE tell us about domain organization and interactions?
5. **Biological Insights**: What can we infer about the protein's structure and potential function? Consider the amino acid sequence composition and any recognizable motifs or domains.
6. **Limitations & Caveats**: What are the key limitations or areas of uncertainty in this prediction?
7. **Recommended Next Steps**: What experimental validation or computational follow-up would you recommend?

IMPORTANT: Begin your response directly with the report. Do NOT include any preamble such as "Of course", "As an expert", "I have reviewed", "Here is my assessment", etc. Start immediately with the title or first section of your analysis."""

        logger.info("Generating Gemini expert analysis...")

        content_parts = [prompt]

        if best.get("plots", {}).get("plddt_plot"):
            try:
                plddt_plot_uri = best["plots"]["plddt_plot"]
                content_parts.append("\n\n**pLDDT Plot (Best Model):**")
                content_parts.append(
                    types.Part.from_uri(file_uri=plddt_plot_uri, mime_type="image/png")
                )
            except Exception as e:
                logger.warning(f"Could not include pLDDT plot: {e}")

        if best.get("plots", {}).get("pae_plot"):
            try:
                pae_plot_uri = best["plots"]["pae_plot"]
                content_parts.append("\n\n**PAE Plot (Best Model):**")
                content_parts.append(
                    types.Part.from_uri(file_uri=pae_plot_uri, mime_type="image/png")
                )
            except Exception as e:
                logger.warning(f"Could not include PAE plot: {e}")

        gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.1-pro-preview")
        project_id = os.environ["PROJECT_ID"]
        location = (
            "global"
            if "preview" in gemini_model
            else os.getenv("REGION", "us-central1")
        )

        logger.info(
            f"Initializing Google GenAI: project={project_id}, location={location}, model={gemini_model}"
        )

        with genai.Client(
            vertexai=True, project=project_id, location=location
        ) as client:
            response = client.models.generate_content(
                model=gemini_model,
                contents=content_parts,
                config=types.GenerateContentConfig(temperature=1.0),
            )

        disclaimer = (
            "\n\n---\n"
            "*This analysis was generated by AI (Google Gemini) and has not been "
            "peer-reviewed. It is intended as a starting point for interpretation, "
            "not as expert biological advice. Predictions should be validated "
            "experimentally by qualified scientists before use in research or "
            "clinical decisions.*"
        )

        return {
            "status": "success",
            "analysis": response.text + disclaimer,
            "model": gemini_model,
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            + "Z",
            "ai_generated": True,
        }

    except Exception as e:
        logger.error(f"Error generating Gemini analysis: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def consolidate_results(
    job_id: str, analysis_path: str, bucket_name: str, task_count: int
):
    """Consolidate analysis results and generate summary.json with job metadata."""
    logger.info(f"Consolidating AF2 results from {analysis_path}")
    time.sleep(2)

    parts = analysis_path[5:].split("/", 1)
    prefix = parts[1] if len(parts) > 1 else ""

    if prefix and not prefix.endswith("/"):
        prefix += "/"
    if prefix.startswith("/"):
        prefix = prefix[1:]

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    blobs = bucket.list_blobs(prefix=prefix)
    completed_files = []

    for blob in blobs:
        if "prediction_" in blob.name and blob.name.endswith("_analysis.json"):
            completed_files.append(f"gs://{bucket_name}/{blob.name}")

    logger.info(f"Found {len(completed_files)} analysis files to consolidate")

    all_analyses = []
    for file_path in completed_files:
        try:
            analysis = download_json_from_gcs(file_path)
            all_analyses.append(analysis)
        except Exception as e:
            logger.error(f"Failed to read {file_path}: {e}")

    if not all_analyses:
        logger.error("No analyses found to consolidate")
        return

    all_labels = {}
    job_metadata = {"labels": {}, "parameters": {}}
    try:
        from google.cloud import aiplatform_v1 as vertex_ai

        project_id = os.environ["PROJECT_ID"]
        region = os.getenv("REGION", "us-central1")

        client = vertex_ai.PipelineServiceClient(
            client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"}
        )

        full_job_id = (
            job_id
            if job_id.startswith("projects/")
            else f"projects/{project_id}/locations/{region}/pipelineJobs/{job_id}"
        )

        request = vertex_ai.GetPipelineJobRequest(name=full_job_id)
        job = client.get_pipeline_job(request=request)

        all_labels = dict(job.labels) if hasattr(job, "labels") and job.labels else {}
        pipeline_parameters = {}
        if hasattr(job, "runtime_config") and hasattr(
            job.runtime_config, "parameter_values"
        ):
            pipeline_parameters = dict(job.runtime_config.parameter_values)

        job_metadata = {
            "display_name": job.display_name if hasattr(job, "display_name") else None,
            "state": job.state.name if hasattr(job, "state") else None,
            "labels": all_labels,
            "parameters": pipeline_parameters,
        }

        if hasattr(job, "create_time") and job.create_time:
            job_metadata["created"] = job.create_time.isoformat()
        if hasattr(job, "start_time") and job.start_time:
            job_metadata["started"] = job.start_time.isoformat()
        if hasattr(job, "end_time") and job.end_time:
            job_metadata["completed"] = job.end_time.isoformat()

        if (
            hasattr(job, "start_time")
            and hasattr(job, "end_time")
            and job.start_time
            and job.end_time
        ):
            duration = job.end_time - job.start_time
            total_seconds = duration.total_seconds()
            job_metadata["duration_seconds"] = total_seconds
            hours = int(total_seconds // 3600)
            minutes = int((total_seconds % 3600) // 60)
            job_metadata["duration_formatted"] = (
                f"{hours}h {minutes}m" if hours > 0 else f"{minutes:.1f}m"
            )

    except Exception as e:
        logger.error(f"Could not get job metadata: {e}", exc_info=True)
        all_labels = {}
        job_metadata = {"labels": {}, "parameters": {}}

    # Sort analyses by pLDDT
    all_analyses.sort(
        key=lambda x: (
            x["plddt_mean"],
            x["plddt_min"],
            -x["pae_mean"] if x["pae_mean"] is not None else 0,
            x["ranking_confidence"],
        ),
        reverse=True,
    )

    for i, analysis in enumerate(all_analyses):
        analysis["plddt_rank"] = i + 1

    best = all_analyses[0]
    plddt_means = [a["plddt_mean"] for a in all_analyses]

    fasta_sequence = None
    fasta_header = None

    try:
        if hasattr(job, "runtime_config") and hasattr(
            job.runtime_config, "parameter_values"
        ):
            sequence_path = job.runtime_config.parameter_values.get("sequence_path")
            if sequence_path and isinstance(sequence_path, str):
                bucket_obj = storage_client.bucket(bucket_name)
                blob_path = sequence_path.replace(f"gs://{bucket_name}/", "")
                blob = bucket_obj.blob(blob_path)

                if blob.exists():
                    fasta_text = blob.download_as_text()
                    lines = fasta_text.strip().split("\n")
                    if lines and lines[0].startswith(">"):
                        fasta_header = lines[0][1:].strip()
                        fasta_sequence = "".join(lines[1:])
    except Exception as e:
        logger.warning(f"Could not load FASTA sequence: {e}")

    summary = {
        "protein_info": {
            "sequence_length": len(best.get("plddt_scores", [])),
            "model_name": best.get("model_name"),
            "total_predictions": len(all_analyses),
            "fasta_sequence": fasta_sequence,
            "fasta_header": fasta_header,
            "job_metadata": job_metadata,
            "model_type": "alphafold2",
        },
        "quality_metrics": {
            "best_model_plddt": best["plddt_mean"],
            "best_model_pae": best["pae_mean"],
            "quality_assessment": best["quality_assessment"],
        },
        "statistics": {
            "total_predictions": len(all_analyses),
            "analyzed_successfully": len(all_analyses),
            "plddt_stats": {
                "mean": sum(plddt_means) / len(plddt_means),
                "min": min(plddt_means),
                "max": max(plddt_means),
                "range": max(plddt_means) - min(plddt_means),
            },
        },
    }

    summary_data = {
        "job_id": job_id,
        "model_type": "alphafold2",
        "analyzed_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        + "Z",
        "summary": summary,
        "best_prediction": best,
        "top_predictions": all_analyses[:10],
        "all_predictions_summary": [
            {
                "rank": a["rank"],
                "plddt_rank": a["plddt_rank"],
                "model_name": a["model_name"],
                "ranking_confidence": a["ranking_confidence"],
                "plddt_mean": a["plddt_mean"],
                "plddt_median": a["plddt_median"],
                "plddt_min": a["plddt_min"],
                "plddt_max": a["plddt_max"],
                "plddt_distribution": a["plddt_distribution"],
                "pae_mean": a["pae_mean"],
                "quality_assessment": a["quality_assessment"],
                "uri": a["uri"],
                "plots": a.get("plots", {}),
            }
            for a in all_analyses
        ],
    }

    expert_analysis = generate_gemini_expert_analysis(summary_data)
    summary_data["expert_analysis"] = expert_analysis

    summary_uri = f"{analysis_path}summary.json"
    local_summary = "/tmp/summary.json"

    with open(local_summary, "w") as f:
        json.dump(summary_data, f, indent=2)

    upload_to_gcs(local_summary, summary_uri)
    os.remove(local_summary)

    logger.info(f"✅ Consolidation complete! Summary written to {summary_uri}")


def run_task(
    task_index: int,
    task_count: int,
    task_config: dict,
    bucket_name: str,
    analysis_path: str,
):
    """Execute the analysis for a specific AF2 prediction task."""
    predictions = task_config.get("predictions", [])
    if task_index >= len(predictions):
        logger.error(
            f"Task index {task_index} out of range (total predictions: {len(predictions)})"
        )
        sys.exit(1)

    pred = predictions[task_index]
    job_id = task_config.get("job_id")

    prediction_index = pred["index"]
    prediction_uri = pred["uri"]
    model_name = pred["model_name"]
    ranking_confidence = pred["ranking_confidence"]
    output_uri = pred["output_uri"]

    logger.info(f"Starting analysis for prediction {prediction_index}: {model_name}")

    try:
        local_pickle = f"/tmp/raw_prediction_{prediction_index}.pkl"
        download_from_gcs(prediction_uri, local_pickle)

        raw_prediction = load_raw_prediction(local_pickle)
        plddt_stats = calculate_plddt_stats(raw_prediction["plddt"].tolist())
        pae_stats = calculate_pae_stats(raw_prediction)

        quality = get_quality_assessment(plddt_stats["mean"])

        # Generate plots
        plot_files = {}
        output_base = output_uri.rsplit("/", 1)[0]

        plddt_plot_path = f"/tmp/plddt_plot_{prediction_index}.png"
        plot_plddt(raw_prediction["plddt"], model_name, plddt_plot_path)

        plddt_plot_uri = f"{output_base}/plddt_plot_{prediction_index}.png"
        upload_to_gcs(plddt_plot_path, plddt_plot_uri)
        plot_files["plddt_plot"] = plddt_plot_uri
        os.remove(plddt_plot_path)

        if pae_stats is not None:
            pae_plot_path = f"/tmp/pae_plot_{prediction_index}.png"
            max_pae = raw_prediction.get("max_predicted_aligned_error", 31.0)
            plot_pae(
                raw_prediction["predicted_aligned_error"],
                model_name,
                pae_plot_path,
                max_pae,
            )

            pae_plot_uri = f"{output_base}/pae_plot_{prediction_index}.png"
            upload_to_gcs(pae_plot_path, pae_plot_uri)
            plot_files["pae_plot"] = pae_plot_uri
            os.remove(pae_plot_path)

        analysis = {
            "prediction_index": prediction_index,
            "model_name": model_name,
            "rank": prediction_index + 1,
            "ranking_confidence": ranking_confidence,
            "plddt_mean": plddt_stats["mean"],
            "plddt_median": plddt_stats["median"],
            "plddt_min": plddt_stats["min"],
            "plddt_max": plddt_stats["max"],
            "plddt_std": plddt_stats["std"],
            "plddt_distribution": plddt_stats["distribution"],
            "plddt_scores": plddt_stats["per_residue"],
            "pae_mean": pae_stats["mean"] if pae_stats else None,
            "pae_median": pae_stats["median"] if pae_stats else None,
            "pae_min": pae_stats["min"] if pae_stats else None,
            "pae_max": pae_stats["max"] if pae_stats else None,
            "quality_assessment": quality,
            "has_pae": pae_stats is not None,
            "uri": prediction_uri,
            "analyzed_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            + "Z",
            "plots": plot_files,
        }

        local_json = f"/tmp/analysis_{prediction_index}.json"
        with open(local_json, "w") as f:
            json.dump(analysis, f, indent=2)

        upload_to_gcs(local_json, output_uri)
        os.remove(local_pickle)
        os.remove(local_json)

        logger.info(f"✅ Completed analysis for prediction {prediction_index}")

        # Consolidation check
        if task_index == task_count - 1:
            max_wait = 120
            wait_interval = 2
            waited = 0
            storage_client = storage.Client()
            bucket_obj = storage_client.bucket(bucket_name)
            analysis_prefix = analysis_path.replace(f"gs://{bucket_name}/", "")

            while waited < max_wait:
                blobs = bucket_obj.list_blobs(prefix=analysis_prefix)
                completed_files = [
                    b.name
                    for b in blobs
                    if "prediction_" in b.name and b.name.endswith("_analysis.json")
                ]

                if len(completed_files) >= task_count:
                    logger.info("All task analysis files found, starting consolidation")
                    break

                time.sleep(wait_interval)
                waited += wait_interval

            consolidate_results(job_id, analysis_path, bucket_name, task_count)

    except Exception as e:
        logger.error(f"Error running AF2 analysis task: {e}", exc_info=True)
        raise
