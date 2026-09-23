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
import tempfile
import time
from datetime import UTC, datetime

import matplotlib
import numpy as np
from google import genai
from google.cloud import storage
from google.genai import types

matplotlib.use("Agg")  # Use non-interactive backend for Cloud Run

from .shared_utils import (
    calculate_plddt_stats,
    download_from_gcs,
    download_json_from_gcs,
    get_job_metadata,
    get_quality_assessment,
    plot_error_matrix,
    plot_plddt_distribution,
    upload_to_gcs,
    wait_for_sibling_tasks,
)

logger = logging.getLogger(__name__)


_ALLOWED_PICKLE_GLOBALS: set[tuple[str, str]] = {
    ("builtins", "dict"),
    ("builtins", "list"),
    ("builtins", "tuple"),
    ("builtins", "set"),
    ("builtins", "frozenset"),
    ("builtins", "int"),
    ("builtins", "float"),
    ("builtins", "str"),
    ("builtins", "bytes"),
    ("builtins", "bool"),
    ("builtins", "complex"),
    ("builtins", "slice"),
    ("numpy", "ndarray"),
    ("numpy", "dtype"),
    ("numpy.core.multiarray", "_reconstruct"),
    ("numpy.core.multiarray", "scalar"),
    ("numpy._core.multiarray", "_reconstruct"),
    ("numpy._core.multiarray", "scalar"),
    ("numpy.core.numeric", "_frombuffer"),
    ("numpy._core.numeric", "_frombuffer"),
    ("ml_dtypes", "bfloat16"),
}

_ALLOWED_NUMPY_RECONSTRUCTORS = {
    getattr(getattr(np, "_core", np.core).multiarray, "_reconstruct", None),
} - {None}


def _safe_reconstruct_jax_array(fun, args, arr_state, aval_state=None):
    """Safely reconstruct a pickled JAX ArrayImpl into a NumPy ndarray."""
    if fun not in _ALLOWED_NUMPY_RECONSTRUCTORS:
        raise pickle.UnpicklingError(
            f"Forbidden constructor in JAX array reconstruction: {fun!r}"
        )
    np_value = fun(*args)
    if not isinstance(np_value, np.ndarray):
        raise pickle.UnpicklingError(
            f"Expected ndarray from JAX array reconstruction, got {type(np_value).__name__}"
        )
    np_value.__setstate__(arr_state)
    if getattr(np_value.dtype, "hasobject", False):
        raise pickle.UnpicklingError("Object-dtype NumPy arrays are forbidden")
    return np_value


def _reject_object_dtype_arrays(obj) -> None:
    """Recursively ensure no object-dtype NumPy arrays exist in deserialized payload."""
    if isinstance(obj, np.ndarray):
        if getattr(obj.dtype, "hasobject", False):
            raise pickle.UnpicklingError("Object-dtype NumPy arrays are forbidden")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _reject_object_dtype_arrays(k)
            _reject_object_dtype_arrays(v)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for item in obj:
            _reject_object_dtype_arrays(item)


class _RestrictedPredictionUnpickler(pickle.Unpickler):
    """Restricted unpickler permitting only safe NumPy/JAX array and primitive types."""

    def find_class(self, module: str, name: str):
        if (module, name) == ("jax._src.array", "_reconstruct_array"):
            return _safe_reconstruct_jax_array
        if (module, name) in _ALLOWED_PICKLE_GLOBALS:
            return super().find_class(module, name)
        raise pickle.UnpicklingError(
            f"Forbidden global during prediction unpickling: {module}.{name}"
        )


def _validate_task_gcs_uri(gcs_uri: str, expected_bucket: str | None = None) -> None:
    """Validate that a task GCS URI targets the expected bucket and contains no path traversal."""
    if not gcs_uri or "\x00" in gcs_uri or not gcs_uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")

    parts = gcs_uri[5:].split("/", 1)
    bucket = parts[0]
    blob_path = parts[1] if len(parts) > 1 else ""

    if not bucket or not blob_path:
        raise ValueError(f"Invalid GCS URI (missing bucket or object path): {gcs_uri}")

    if ".." in blob_path.split("/"):
        raise ValueError(f"Path traversal detected in GCS URI: {gcs_uri}")

    if expected_bucket and bucket != expected_bucket:
        raise ValueError(
            f"Unauthorized GCS bucket '{bucket}'. Expected '{expected_bucket}'."
        )


def load_raw_prediction(pickle_path: str) -> dict:
    """Load raw prediction pickle file using restricted unpickler."""
    with open(pickle_path, "rb") as f:
        raw_prediction = _RestrictedPredictionUnpickler(f).load()
    if not isinstance(raw_prediction, dict):
        raise pickle.UnpicklingError(
            f"Expected prediction payload to be a dict, got {type(raw_prediction).__name__}"
        )
    _reject_object_dtype_arrays(raw_prediction)
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
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        location = os.getenv("GOOGLE_CLOUD_LOCATION")

        logger.info(
            f"Initializing Google GenAI: project={project_id}, location={location}, model={gemini_model}"
        )

        with genai.Client() as client:
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
            "generated_at": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
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

    job_metadata = get_job_metadata(job_id)

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
        sequence_path = job_metadata.get("parameters", {}).get("sequence_path")
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
        "analyzed_at": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
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
        _validate_task_gcs_uri(prediction_uri, expected_bucket=bucket_name)
        _validate_task_gcs_uri(output_uri, expected_bucket=bucket_name)

        with tempfile.TemporaryDirectory(
            prefix=f"af2_analysis_{prediction_index}_"
        ) as tmp_dir:
            local_pickle = os.path.join(tmp_dir, "raw_prediction.pkl")
            download_from_gcs(prediction_uri, local_pickle)

            raw_prediction = load_raw_prediction(local_pickle)
            plddt_stats = calculate_plddt_stats(raw_prediction["plddt"].tolist())
            pae_stats = calculate_pae_stats(raw_prediction)

            quality = get_quality_assessment(plddt_stats["mean"])

            # Generate plots
            plot_files = {}
            output_base = output_uri.rsplit("/", 1)[0]

            plddt_plot_path = os.path.join(
                tmp_dir, f"plddt_plot_{prediction_index}.png"
            )
            plot_plddt_distribution(
                raw_prediction["plddt"], model_name, plddt_plot_path
            )

            plddt_plot_uri = f"{output_base}/plddt_plot_{prediction_index}.png"
            upload_to_gcs(plddt_plot_path, plddt_plot_uri)
            plot_files["plddt_plot"] = plddt_plot_uri

            if pae_stats is not None:
                pae_plot_path = os.path.join(
                    tmp_dir, f"pae_plot_{prediction_index}.png"
                )
                max_pae = raw_prediction.get("max_predicted_aligned_error", 31.0)
                plot_error_matrix(
                    raw_prediction["predicted_aligned_error"],
                    model_name,
                    "Expected Position Error (Å)",
                    pae_plot_path,
                    max_value=max_pae,
                )

                pae_plot_uri = f"{output_base}/pae_plot_{prediction_index}.png"
                upload_to_gcs(pae_plot_path, pae_plot_uri)
                plot_files["pae_plot"] = pae_plot_uri

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
                "analyzed_at": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
                "plots": plot_files,
            }

            local_json = os.path.join(tmp_dir, f"analysis_{prediction_index}.json")
            with open(local_json, "w") as f:
                json.dump(analysis, f, indent=2)

            upload_to_gcs(local_json, output_uri)

        logger.info(f"✅ Completed analysis for prediction {prediction_index}")

        # Consolidation check
        if task_index == task_count - 1:
            wait_for_sibling_tasks(bucket_name, analysis_path, task_count)
            consolidate_results(job_id, analysis_path, bucket_name, task_count)

    except Exception as e:
        logger.error(f"Error running AF2 analysis task: {e}", exc_info=True)
        raise
