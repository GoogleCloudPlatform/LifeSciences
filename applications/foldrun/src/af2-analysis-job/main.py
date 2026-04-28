#!/usr/bin/env python3
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

"""Cloud Run Job for analyzing AlphaFold2 predictions in parallel."""

import json
import logging
import os
import pickle
import sys
from datetime import datetime, timezone

import matplotlib
import numpy as np
from google import genai
from google.cloud import storage
from google.genai import types

from gcs_retry import GCS_RETRY

matplotlib.use("Agg")  # Use non-interactive backend for Cloud Run
import matplotlib.pyplot as plt

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# pLDDT confidence bands
PLDDT_BANDS = [
    (0, 50, "very_low_confidence"),
    (50, 70, "low_confidence"),
    (70, 90, "high_confidence"),
    (90, 100, "very_high_confidence"),
]


def download_from_gcs(gcs_uri: str, local_path: str) -> None:
    """Download file from GCS."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")

    parts = gcs_uri[5:].split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1] if len(parts) > 1 else ""

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    # Reload to get metadata including size
    blob.reload()

    blob.download_to_filename(local_path, retry=GCS_RETRY)

    size_mb = blob.size / 1024 / 1024 if blob.size else 0
    logger.info(f"Downloaded {gcs_uri} ({size_mb:.2f} MB)")


def upload_to_gcs(local_path: str, gcs_uri: str) -> None:
    """Upload file to GCS."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")

    parts = gcs_uri[5:].split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1] if len(parts) > 1 else ""

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(local_path, retry=GCS_RETRY)

    logger.info(f"Uploaded {local_path} to {gcs_uri}")


def load_raw_prediction(pickle_path: str) -> dict:
    """Load raw prediction pickle file."""
    with open(pickle_path, "rb") as f:
        raw_prediction = pickle.load(f)
    return raw_prediction


def calculate_plddt_stats(raw_prediction: dict) -> dict:
    """Calculate pLDDT statistics."""
    plddt_scores = raw_prediction["plddt"]

    stats = {
        "mean": float(np.mean(plddt_scores)),
        "median": float(np.median(plddt_scores)),
        "min": float(np.min(plddt_scores)),
        "max": float(np.max(plddt_scores)),
        "std": float(np.std(plddt_scores)),
        "per_residue": plddt_scores.tolist(),
    }

    # Distribution by confidence band
    distribution = {}
    for min_val, max_val, label in PLDDT_BANDS:
        count = np.sum((plddt_scores >= min_val) & (plddt_scores <= max_val))
        distribution[label] = int(count)

    stats["distribution"] = distribution

    return stats


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


def get_quality_assessment(plddt_mean: float) -> str:
    """Get quality assessment based on mean pLDDT."""
    if plddt_mean >= 90:
        return "very_high_confidence"
    elif plddt_mean >= 70:
        return "high_confidence"
    elif plddt_mean >= 50:
        return "low_confidence"
    else:
        return "very_low_confidence"


def download_json_from_gcs(gcs_uri: str) -> dict:
    """Download JSON file from GCS."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")

    parts = gcs_uri[5:].split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1] if len(parts) > 1 else ""

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    content = blob.download_as_string(retry=GCS_RETRY)
    return json.loads(content)


def plot_plddt(plddt_scores: np.ndarray, model_name: str, output_path: str) -> None:
    """Generate pLDDT per-residue plot.

    Args:
        plddt_scores: Array of per-residue pLDDT scores
        model_name: Name of the model for plot title
        output_path: Local path to save plot PNG
    """
    fig, ax = plt.subplots(figsize=(12, 4))

    # Plot pLDDT scores
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
    """Generate PAE heatmap plot.

    Args:
        pae_matrix: 2D array of predicted aligned error
        model_name: Name of the model for plot title
        output_path: Local path to save plot PNG
        max_pae: Maximum PAE value for color scale
    """
    fig, ax = plt.subplots(figsize=(8, 7))

    # Use reversed Greens colormap (like in the notebook)
    # Lower PAE (green) = better, Higher PAE (white) = worse
    cmap = plt.cm.Greens_r

    # Plot heatmap
    im = ax.imshow(pae_matrix, vmin=0, vmax=max_pae, cmap=cmap)

    # Add colorbar
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


def main():
    """Main entry point for Cloud Run Job task."""

    # Cloud Run automatically sets these environment variables
    task_index = int(os.getenv("CLOUD_RUN_TASK_INDEX", "0"))
    task_count = int(os.getenv("CLOUD_RUN_TASK_COUNT", "1"))

    logger.info(f"Task {task_index}/{task_count}: Starting")

    # Get configuration from environment variables
    bucket_name = os.getenv("GCS_BUCKET")

    # AF2_JOB_ID can be passed via env var OR via task_config.json
    # ANALYSIS_PATH can be passed via env var OR derived from task_config.json
    job_id_from_env = os.getenv("AF2_JOB_ID")
    analysis_path_from_env = os.getenv("ANALYSIS_PATH")

    if not bucket_name:
        logger.error("GCS_BUCKET environment variable not set")
        sys.exit(1)

    logger.info(f"Bucket: {bucket_name}")

    # Determine task_config location
    # Priority 1: Use ANALYSIS_PATH from env var if provided
    # Priority 2: Use AF2_JOB_ID from env var to construct path
    # Priority 3: Read from a well-known location (e.g., execution-specific path)

    task_config_uri = None

    if analysis_path_from_env:
        # Use provided analysis path
        analysis_path = analysis_path_from_env
        task_config_uri = f"{analysis_path}task_config.json"
        logger.info(f"Using ANALYSIS_PATH from env: {analysis_path}")
    elif job_id_from_env:
        # AF2_JOB_ID alone is not enough to resolve the GCS path — the analysis
        # launcher should always set ANALYSIS_PATH. Log a clear error.
        logger.error(
            f"AF2_JOB_ID is set ({job_id_from_env}) but ANALYSIS_PATH is not. "
            "Cannot determine GCS path from job_id alone. "
            "The analysis launcher must set ANALYSIS_PATH."
        )
        sys.exit(1)
    else:
        logger.error(
            "Either AF2_JOB_ID or ANALYSIS_PATH environment variable must be set"
        )
        sys.exit(1)

    logger.info(f"Task {task_index}: Loading configuration from {task_config_uri}")

    # Load task configuration
    try:
        task_config = download_json_from_gcs(task_config_uri)
    except Exception as e:
        logger.error(f"Failed to load task configuration: {e}")
        sys.exit(1)

    # Get this task's prediction
    predictions = task_config.get("predictions", [])
    if task_index >= len(predictions):
        logger.error(
            f"Task index {task_index} out of range (total predictions: {len(predictions)})"
        )
        sys.exit(1)

    pred = predictions[task_index]
    job_id = task_config.get("job_id")

    # Get analysis_path from task_config (if not already set from env var)
    if not analysis_path_from_env:
        analysis_path = task_config.get("analysis_path")
        if not analysis_path:
            logger.error("analysis_path not found in task_config.json")
            sys.exit(1)
        logger.info(f"Using analysis_path from task_config: {analysis_path}")

    prediction_index = pred["index"]
    prediction_uri = pred["uri"]
    model_name = pred["model_name"]
    ranking_confidence = pred["ranking_confidence"]
    output_uri = pred["output_uri"]

    logger.info(f"Starting analysis for prediction {prediction_index}: {model_name}")
    logger.info(f"Job ID: {job_id}")
    logger.info(f"Analysis Path: {analysis_path}")
    logger.info(f"Prediction URI: {prediction_uri}")

    try:
        # Download raw prediction
        local_pickle = f"/tmp/raw_prediction_{prediction_index}.pkl"
        download_from_gcs(prediction_uri, local_pickle)

        # Load and analyze
        logger.info("Loading raw prediction...")
        raw_prediction = load_raw_prediction(local_pickle)

        logger.info("Calculating pLDDT statistics...")
        plddt_stats = calculate_plddt_stats(raw_prediction)

        logger.info("Calculating PAE statistics...")
        pae_stats = calculate_pae_stats(raw_prediction)

        quality = get_quality_assessment(plddt_stats["mean"])

        # Generate plots
        logger.info("Generating visualizations...")
        plot_files = {}

        # pLDDT plot
        plddt_plot_path = f"/tmp/plddt_plot_{prediction_index}.png"
        plot_plddt(raw_prediction["plddt"], model_name, plddt_plot_path)

        # Determine GCS path for plots (same directory as analysis JSON)
        output_base = output_uri.rsplit("/", 1)[0]  # Remove filename
        plddt_plot_uri = f"{output_base}/plddt_plot_{prediction_index}.png"
        upload_to_gcs(plddt_plot_path, plddt_plot_uri)
        plot_files["plddt_plot"] = plddt_plot_uri

        # PAE plot (if available)
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

        # Build analysis result
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
            "plddt_scores": plddt_stats[
                "per_residue"
            ],  # Used for confidence breakdown calculation
            "pae_mean": pae_stats["mean"] if pae_stats else None,
            "pae_median": pae_stats["median"] if pae_stats else None,
            "pae_min": pae_stats["min"] if pae_stats else None,
            "pae_max": pae_stats["max"] if pae_stats else None,
            "quality_assessment": quality,
            "has_pae": pae_stats is not None,
            "uri": prediction_uri,
            "analyzed_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            + "Z",
            "plots": plot_files,  # Add plot URLs to analysis result
        }

        # Write to JSON
        local_json = f"/tmp/analysis_{prediction_index}.json"
        with open(local_json, "w") as f:
            json.dump(analysis, f, indent=2)

        # Upload to GCS
        logger.info(f"Uploading results to {output_uri}")
        upload_to_gcs(local_json, output_uri)

        # Cleanup
        os.remove(local_pickle)
        os.remove(local_json)
        os.remove(plddt_plot_path)
        if pae_stats is not None:
            os.remove(pae_plot_path)

        logger.info(f"✅ Completed analysis for prediction {prediction_index}")
        logger.info(f"   pLDDT mean: {plddt_stats['mean']:.2f}")
        logger.info(f"   Quality: {quality}")

        # If this is the last task, trigger consolidation
        # Wait for all other tasks to complete before consolidating
        if task_index == task_count - 1:
            logger.info(
                f"This is the last task ({task_index + 1}/{task_count}), waiting for all tasks to complete..."
            )

            # Wait for all analysis files to be present
            import time

            max_wait = 120  # Maximum 2 minutes to wait
            wait_interval = 2  # Check every 2 seconds
            waited = 0

            while waited < max_wait:
                # Check if all analysis files exist
                storage_client = storage.Client()
                bucket_obj = storage_client.bucket(bucket_name)
                analysis_prefix = analysis_path.replace(f"gs://{bucket_name}/", "")

                blobs = bucket_obj.list_blobs(prefix=analysis_prefix, retry=GCS_RETRY)
                completed_files = [
                    b.name
                    for b in blobs
                    if "prediction_" in b.name and b.name.endswith("_analysis.json")
                ]

                if len(completed_files) >= task_count:
                    logger.info(
                        f"All {task_count} analysis files found, starting consolidation"
                    )
                    break

                logger.info(
                    f"Waiting for all tasks... ({len(completed_files)}/{task_count} files found)"
                )
                time.sleep(wait_interval)
                waited += wait_interval

            if len(completed_files) < task_count:
                logger.warning(
                    f"Timeout waiting for all tasks. Found {len(completed_files)}/{task_count} files. Consolidating anyway..."
                )

            try:
                consolidate_results(job_id, analysis_path)
            except Exception as e:
                logger.error(f"Consolidation failed: {e}", exc_info=True)
                # Don't fail the task if consolidation fails
                pass

        sys.exit(0)

    except Exception as e:
        logger.error(
            f"❌ Error analyzing prediction {prediction_index}: {e}", exc_info=True
        )
        sys.exit(1)


def download_image_from_gcs(gcs_uri: str) -> bytes:
    """Download image bytes from GCS."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")

    parts = gcs_uri[5:].split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1] if len(parts) > 1 else ""

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    return blob.download_as_bytes(retry=GCS_RETRY)


def generate_gemini_expert_analysis(summary_data: dict) -> dict:
    """Generate expert analysis using Gemini via Google GenAI SDK.

    Args:
        summary_data: Complete summary data including predictions and metrics

    Returns:
        Dictionary with expert analysis or error information
    """
    try:
        # Build expert analysis prompt
        best = summary_data["best_prediction"]
        summary = summary_data["summary"]
        top_preds = summary_data["top_predictions"][:3]  # Top 3 for analysis

        # Format PAE value safely
        pae_mean_str = (
            f"{best['pae_mean']:.2f}" if best["pae_mean"] is not None else "N/A"
        )

        # Determine if monomer or multimer from model name
        is_multimer = "multimer" in best.get("model_name", "")
        model_type_str = "multimer" if is_multimer else "monomer"
        ranking_formula = "0.8 * ipTM + 0.2 * pTM" if is_multimer else "average pLDDT"

        # Calculate model agreement stats
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

        # Add FASTA sequence if available
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

        # Build multimodal content with images
        content_parts = [prompt]

        # Add pLDDT plot image if available
        if best.get("plots", {}).get("plddt_plot"):
            try:
                plddt_plot_uri = best["plots"]["plddt_plot"]
                logger.info(f"Including pLDDT plot: {plddt_plot_uri}")

                # Use from_uri for GCS images (more efficient than downloading)
                content_parts.append("\n\n**pLDDT Plot (Best Model):**")
                content_parts.append(
                    types.Part.from_uri(file_uri=plddt_plot_uri, mime_type="image/png")
                )
            except Exception as e:
                logger.warning(f"Could not include pLDDT plot: {e}")

        # Add PAE plot image if available
        if best.get("plots", {}).get("pae_plot"):
            try:
                pae_plot_uri = best["plots"]["pae_plot"]
                logger.info(f"Including PAE plot: {pae_plot_uri}")

                content_parts.append("\n\n**PAE Plot (Best Model):**")
                content_parts.append(
                    types.Part.from_uri(file_uri=pae_plot_uri, mime_type="image/png")
                )
            except Exception as e:
                logger.warning(f"Could not include PAE plot: {e}")

        gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.1-pro-preview")
        # Initialize GenAI Client with Vertex AI
        project_id = os.environ["PROJECT_ID"]
        # Preview models require the global endpoint
        location = (
            "global"
            if "preview" in gemini_model
            else os.getenv("REGION", "us-central1")
        )

        logger.info(
            f"Initializing Google GenAI (Vertex AI): project={project_id}, location={location}, model={gemini_model}"
        )

        with genai.Client(
            vertexai=True, project=project_id, location=location
        ) as client:
            # Generate content using Gemini API
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


def consolidate_results(job_id: str, analysis_path: str):
    """Consolidate analysis results and generate summary.json with job metadata.

    Args:
        job_id: Vertex AI job ID
        analysis_path: Full GCS path to analysis directory (e.g., gs://bucket/pipeline_runs/.../analysis/)
    """
    import time

    logger.info(f"Consolidating results from {analysis_path}")

    # Wait a moment for all files to be fully written
    time.sleep(2)

    # Extract bucket and prefix from analysis_path
    # Format: gs://bucket/path/to/analysis/
    if not analysis_path.startswith("gs://"):
        raise ValueError(f"Invalid analysis_path format: {analysis_path}")

    parts = analysis_path[5:].split("/", 1)
    bucket_name = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""

    # Ensure prefix ends with / but doesn't start with /
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    if prefix.startswith("/"):
        prefix = prefix[1:]

    # List all prediction analysis files
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    blobs = bucket.list_blobs(prefix=prefix, retry=GCS_RETRY)
    completed_files = []

    for blob in blobs:
        if "prediction_" in blob.name and blob.name.endswith("_analysis.json"):
            completed_files.append(f"gs://{bucket_name}/{blob.name}")

    logger.info(f"Found {len(completed_files)} analysis files to consolidate")

    # Read all analyses
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

    # Get job metadata from Vertex AI (includes labels)
    all_labels = {}  # Initialize to avoid UnboundLocalError
    try:
        from google.cloud import aiplatform_v1 as vertex_ai

        project_id = os.environ["PROJECT_ID"]
        region = os.getenv("REGION", "us-central1")

        logger.info(f"Fetching job metadata from Vertex AI: {job_id}")
        logger.info(f"Project: {project_id}, Region: {region}")

        client = vertex_ai.PipelineServiceClient(
            client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"}
        )

        full_job_id = (
            job_id
            if job_id.startswith("projects/")
            else f"projects/{project_id}/locations/{region}/pipelineJobs/{job_id}"
        )
        logger.info(f"Full job ID: {full_job_id}")

        request = vertex_ai.GetPipelineJobRequest(name=full_job_id)
        job = client.get_pipeline_job(request=request)

        # Extract all labels
        all_labels = dict(job.labels) if hasattr(job, "labels") and job.labels else {}
        logger.info(f"Successfully retrieved {len(all_labels)} labels from Vertex AI")

        # Extract pipeline parameters
        pipeline_parameters = {}
        if hasattr(job, "runtime_config") and hasattr(
            job.runtime_config, "parameter_values"
        ):
            pipeline_parameters = dict(job.runtime_config.parameter_values)
            logger.info(
                f"Successfully retrieved {len(pipeline_parameters)} pipeline parameters"
            )

        job_metadata = {
            "display_name": job.display_name if hasattr(job, "display_name") else None,
            "state": job.state.name if hasattr(job, "state") else None,
            "labels": all_labels,
            "parameters": pipeline_parameters,
        }

        # Add timing info
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

        logger.info(
            f"Retrieved job metadata with {len(all_labels)} labels and {len(pipeline_parameters)} parameters"
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

    # Re-rank
    for i, analysis in enumerate(all_analyses):
        analysis["plddt_rank"] = i + 1

    best = all_analyses[0]

    # Calculate summary statistics
    plddt_means = [a["plddt_mean"] for a in all_analyses]

    # Try to get the input FASTA sequence from job parameters
    fasta_sequence = None
    fasta_header = None

    try:
        # Get sequence_path from job parameters
        if hasattr(job, "runtime_config") and hasattr(
            job.runtime_config, "parameter_values"
        ):
            sequence_path = job.runtime_config.parameter_values.get("sequence_path")
            if sequence_path and isinstance(sequence_path, str):
                logger.info(f"Found sequence_path in job parameters: {sequence_path}")

                # Load FASTA from the sequence_path
                bucket_obj = storage_client.bucket(bucket_name)
                blob_path = sequence_path.replace(f"gs://{bucket_name}/", "")
                blob = bucket_obj.blob(blob_path)

                if blob.exists():
                    fasta_text = blob.download_as_text(retry=GCS_RETRY)
                    lines = fasta_text.strip().split("\n")
                    if lines and lines[0].startswith(">"):
                        fasta_header = lines[0][1:].strip()  # Remove '>'
                        fasta_sequence = "".join(lines[1:])  # Join all sequence lines
                        logger.info(
                            f"Successfully loaded FASTA sequence ({len(fasta_sequence)} residues)"
                        )
                else:
                    logger.warning(f"FASTA file not found at {sequence_path}")
            else:
                logger.warning("sequence_path not found in job parameters")
        else:
            logger.warning("Job runtime_config or parameter_values not available")

    except Exception as e:
        logger.warning(f"Could not load FASTA sequence: {e}", exc_info=True)

    # Build summary with job metadata
    summary = {
        "protein_info": {
            "sequence_length": len(best.get("plddt_scores", [])),
            "model_name": best.get("model_name"),
            "total_predictions": len(all_analyses),
            "fasta_sequence": fasta_sequence,
            "fasta_header": fasta_header,
            "job_metadata": job_metadata,  # Include labels here
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

    # Build final summary
    summary_data = {
        "job_id": job_id,
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

    # Generate Gemini expert analysis
    logger.info("Generating Gemini expert analysis...")
    expert_analysis = generate_gemini_expert_analysis(summary_data)
    summary_data["expert_analysis"] = expert_analysis

    if expert_analysis["status"] == "success":
        logger.info("✓ Expert analysis generated successfully")
    elif expert_analysis["status"] == "skipped":
        logger.warning(
            f"Expert analysis skipped: {expert_analysis.get('reason', 'Unknown')}"
        )
    else:
        logger.error(
            f"Expert analysis failed: {expert_analysis.get('error', 'Unknown')}"
        )

    # Write summary to GCS
    summary_uri = f"{analysis_path}summary.json"
    local_summary = "/tmp/summary.json"

    with open(local_summary, "w") as f:
        json.dump(summary_data, f, indent=2)

    upload_to_gcs(local_summary, summary_uri)
    os.remove(local_summary)

    logger.info(f"✅ Consolidation complete! Summary written to {summary_uri}")
    logger.info(
        f"   Best model: {best['model_name']} (pLDDT: {best['plddt_mean']:.2f})"
    )
    logger.info(f"   Labels captured: {list(all_labels.keys())}")


if __name__ == "__main__":
    main()
