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

"""Tool for analyzing AlphaFold2 prediction quality."""

import logging
import os
import tempfile
from typing import Any, Dict, List

from ..base import AF2Tool
from ..utils.vertex_utils import get_pipeline_job, get_task_details
from ..utils.viz_utils import (
    calculate_pae_stats,
    calculate_plddt_stats,
    get_quality_assessment,
    load_raw_prediction,
)

logger = logging.getLogger(__name__)


class AF2AnalysisTool(AF2Tool):
    """Tool for analyzing quality metrics of AlphaFold2 predictions."""

    def _get_gcs_file_size(self, gcs_uri: str) -> int:
        """Get file size in bytes from GCS URI."""
        from google.cloud import storage

        # Parse GCS URI
        if not gcs_uri.startswith("gs://"):
            raise ValueError(f"Invalid GCS URI: {gcs_uri}")

        parts = gcs_uri[5:].split("/", 1)
        bucket_name = parts[0]
        blob_name = parts[1] if len(parts) > 1 else ""

        # Get blob metadata
        storage_client = storage.Client(project=self.config.project_id)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.reload()

        return blob.size

    def _format_file_size(self, size_bytes: int) -> str:
        """Format file size in human-readable format."""
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} TB"

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze prediction quality.

        Args:
            arguments: {
                'job_id': Job ID (optional if raw_prediction_path provided),
                'model_index': Specific model to analyze (1-5), or null for best (optional),
                'raw_prediction_path': Direct path to raw prediction pickle (optional),
                'analyze_all': Analyze all predictions and compare (default: False),
                'top_n': Return top N predictions when analyze_all=True (default: 5)
            }

        Returns:
            Quality analysis (single prediction or comparison of all)
        """
        job_id = arguments.get("job_id")
        model_index = arguments.get("model_index")
        raw_prediction_path = arguments.get("raw_prediction_path")
        analyze_all = arguments.get("analyze_all", False)
        top_n = arguments.get("top_n", 5)

        if not job_id and not raw_prediction_path:
            raise ValueError("Either job_id or raw_prediction_path must be provided")

        # Batch analysis mode - analyze all predictions
        if analyze_all and job_id:
            return self._analyze_all_predictions(job_id, top_n)

        # If raw prediction path provided, analyze directly
        if raw_prediction_path:
            if raw_prediction_path.startswith("gs://"):
                # Download from GCS
                import tempfile

                local_path = os.path.join(tempfile.gettempdir(), "raw_prediction.pkl")
                self._download_from_gcs(raw_prediction_path, local_path)
                raw_prediction_path = local_path

            raw_prediction = load_raw_prediction(raw_prediction_path)

            # Calculate metrics
            plddt_stats = calculate_plddt_stats(raw_prediction)
            pae_stats = calculate_pae_stats(raw_prediction)
            quality = get_quality_assessment(plddt_stats["mean"])

            return {
                "model_name": "unknown",
                "plddt_scores": plddt_stats,
                "pae_scores": pae_stats,
                "quality_assessment": quality,
                "has_pae": pae_stats is not None,
            }

        # Otherwise, get from job
        job = get_pipeline_job(job_id, self.config.project_id, self.config.region)

        # Get task details
        task_details = get_task_details(job)
        predictions = task_details.get("predictions", [])

        if not predictions:
            return {"status": "no_predictions", "message": "No predictions found for this job"}

        # Select model to analyze
        if model_index is not None:
            if model_index < 1 or model_index > len(predictions):
                raise ValueError(f"model_index must be between 1 and {len(predictions)}")
            selected_pred = predictions[model_index - 1]
        else:
            # Use best model (highest ranking confidence)
            selected_pred = predictions[0]

        # Download raw prediction
        raw_pred_uri = selected_pred.get("uri")
        if not raw_pred_uri:
            return {"status": "error", "message": "Raw prediction URI not found"}

        import tempfile

        local_path = os.path.join(tempfile.gettempdir(), "raw_prediction.pkl")
        self._download_from_gcs(raw_pred_uri, local_path)

        # Load and analyze
        raw_prediction = load_raw_prediction(local_path)

        # Calculate metrics
        plddt_stats = calculate_plddt_stats(raw_prediction)
        pae_stats = calculate_pae_stats(raw_prediction)
        quality = get_quality_assessment(plddt_stats["mean"])

        # Cleanup
        os.remove(local_path)

        return {
            "job_id": job_id,
            "model_name": selected_pred["model_name"],
            "ranking_confidence": selected_pred["ranking_confidence"],
            "plddt_scores": plddt_stats,
            "pae_scores": pae_stats,
            "quality_assessment": quality,
            "has_pae": pae_stats is not None,
            "warnings": [],
        }

    def _analyze_single_prediction(self, index: int, prediction: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze a single prediction and clean up immediately.

        Args:
            index: Prediction index (0-based)
            prediction: Prediction metadata dict

        Returns:
            Analysis results or error dict
        """
        try:
            raw_pred_uri = prediction.get("uri")
            if not raw_pred_uri:
                return {
                    "rank": index + 1,
                    "model_name": prediction.get("model_name", "unknown"),
                    "error": "No URI found",
                }

            # Download to temp file
            local_path = os.path.join(tempfile.gettempdir(), "raw_prediction_temp.pkl")

            try:
                self._download_from_gcs(raw_pred_uri, local_path)

                # Load and analyze
                raw_prediction = load_raw_prediction(local_path)
                plddt_stats = calculate_plddt_stats(raw_prediction)
                pae_stats = calculate_pae_stats(raw_prediction)
                quality = get_quality_assessment(plddt_stats["mean"])

                analysis = {
                    "rank": index + 1,
                    "model_name": prediction["model_name"],
                    "ranking_confidence": prediction["ranking_confidence"],
                    "plddt_mean": plddt_stats["mean"],
                    "plddt_median": plddt_stats["median"],
                    "plddt_min": plddt_stats["min"],
                    "plddt_max": plddt_stats["max"],
                    "plddt_distribution": plddt_stats["distribution"],
                    "pae_mean": pae_stats["mean"] if pae_stats else None,
                    "pae_median": pae_stats["median"] if pae_stats else None,
                    "quality_assessment": quality,
                    "has_pae": pae_stats is not None,
                    "uri": raw_pred_uri,
                }

                return analysis

            finally:
                # Always cleanup the temp file
                if os.path.exists(local_path):
                    os.remove(local_path)

        except Exception as e:
            logger.error(f"Error analyzing prediction {index + 1}: {e}")
            return {
                "rank": index + 1,
                "model_name": prediction.get("model_name", "unknown"),
                "error": str(e),
            }

    def _analyze_all_predictions(self, job_id: str, top_n: int = 5) -> Dict[str, Any]:
        """
        Analyze all predictions from a job and compare them.

        Args:
            job_id: Job ID
            top_n: Number of top predictions to return detailed analysis for

        Returns:
            Comparison analysis of all predictions
        """
        logger.info(f"Analyzing all predictions for job {job_id}")

        # Get job and predictions
        job = get_pipeline_job(job_id, self.config.project_id, self.config.region)
        task_details = get_task_details(job)
        predictions = task_details.get("predictions", [])

        if not predictions:
            return {"status": "no_predictions", "message": "No predictions found for this job"}

        logger.info(f"Found {len(predictions)} predictions to analyze")

        # Get file sizes and show download info
        logger.info("Gathering file information...")
        total_size = 0
        file_info = []
        for i, pred in enumerate(predictions):
            raw_pred_uri = pred.get("uri")
            if raw_pred_uri:
                try:
                    size_bytes = self._get_gcs_file_size(raw_pred_uri)
                    total_size += size_bytes
                    file_info.append(
                        {
                            "rank": i + 1,
                            "model_name": pred["model_name"],
                            "uri": raw_pred_uri,
                            "size_bytes": size_bytes,
                            "size_formatted": self._format_file_size(size_bytes),
                        }
                    )
                except Exception as e:
                    logger.warning(f"Could not get size for {pred['model_name']}: {e}")
                    file_info.append(
                        {
                            "rank": i + 1,
                            "model_name": pred["model_name"],
                            "uri": raw_pred_uri,
                            "size_bytes": None,
                            "size_formatted": "Unknown",
                        }
                    )

        # Log download plan
        logger.info(
            f"Will process {len(file_info)} files sequentially, total size: {self._format_file_size(total_size)}"
        )
        logger.info("Processing one file at a time to minimize local storage usage")

        # Analyze predictions sequentially (one at a time)
        all_analyses = []
        print()  # Newline before progress
        for i, pred in enumerate(predictions):
            # Get file size for this prediction
            file_size = "Unknown"
            for info in file_info:
                if info["rank"] == i + 1:
                    file_size = info["size_formatted"]
                    break

            # Print progress to stdout (immediate feedback)
            print(
                f"[{i + 1}/{len(predictions)}] Processing: {pred['model_name']} (Size: {file_size})"
            )
            print("  Downloading from GCS...", flush=True)

            # Also log for debugging
            logger.info(
                f"[{i + 1}/{len(predictions)}] Processing: {pred['model_name']} (Size: {file_size})"
            )

            # Analyze this prediction
            result = self._analyze_single_prediction(i, pred)
            all_analyses.append(result)

            # Print and log progress with results
            if "error" in result:
                print(f"  ❌ Failed: {result['error']}")
                logger.warning(f"  Failed: {result['error']}")
            else:
                quality_display = result["quality_assessment"].replace("_", " ").title()
                print(
                    f"  ✓ Analyzed: pLDDT = {result['plddt_mean']:.2f}, Quality = {quality_display}"
                )
                print("  File cleaned up from local storage")
                logger.info(
                    f"  Analyzed: pLDDT = {result['plddt_mean']:.2f}, Quality = {quality_display}"
                )

            print()  # Blank line between files

        print(f"✅ Completed all {len(predictions)} predictions\n")
        logger.info(f"Completed all {len(predictions)} predictions")

        # Calculate comparison metrics
        successful_analyses = [a for a in all_analyses if "error" not in a]

        if not successful_analyses:
            return {
                "status": "error",
                "message": "Failed to analyze any predictions",
                "failed_count": len(all_analyses),
                "file_info": file_info,
            }

        # Sort by multiple criteria to break ties:
        # 1. pLDDT mean (higher is better)
        # 2. pLDDT min (higher minimum shows more consistent quality)
        # 3. PAE mean (lower is better for domain arrangement)
        # 4. Original ranking_confidence (AlphaFold's ranking as final tiebreaker)
        successful_analyses.sort(
            key=lambda x: (
                x["plddt_mean"],  # Primary: mean quality
                x["plddt_min"],  # Secondary: worst residue quality
                -x["pae_mean"]
                if x["pae_mean"] is not None
                else 0,  # Tertiary: domain arrangement (lower is better, so negate)
                x["ranking_confidence"],  # Final: original AF2 ranking
            ),
            reverse=True,
        )

        # Re-rank by pLDDT
        for i, analysis in enumerate(successful_analyses):
            analysis["plddt_rank"] = i + 1

        # Calculate summary statistics
        plddt_means = [a["plddt_mean"] for a in successful_analyses]
        pae_means = [a["pae_mean"] for a in successful_analyses if a["pae_mean"] is not None]

        summary_stats = {
            "total_predictions": len(predictions),
            "analyzed_successfully": len(successful_analyses),
            "failed": len(all_analyses) - len(successful_analyses),
            "total_download_size": self._format_file_size(total_size),
            "plddt_stats": {
                "mean": sum(plddt_means) / len(plddt_means),
                "min": min(plddt_means),
                "max": max(plddt_means),
                "range": max(plddt_means) - min(plddt_means),
            },
            "pae_stats": {
                "mean": sum(pae_means) / len(pae_means) if pae_means else None,
                "min": min(pae_means) if pae_means else None,
                "max": max(pae_means) if pae_means else None,
            }
            if pae_means
            else None,
            "quality_distribution": self._get_quality_distribution(successful_analyses),
        }

        # Generate recommendations
        recommendations = self._generate_recommendations(successful_analyses, summary_stats)

        # Return top N detailed + summary
        return {
            "job_id": job_id,
            "file_info": file_info,
            "summary": summary_stats,
            "best_prediction": successful_analyses[0] if successful_analyses else None,
            "top_predictions": successful_analyses[:top_n],
            "all_predictions_summary": [
                {
                    "rank": a["rank"],
                    "plddt_rank": a["plddt_rank"],
                    "model_name": a["model_name"],
                    "ranking_confidence": a["ranking_confidence"],
                    "plddt_mean": a["plddt_mean"],
                    "quality_assessment": a["quality_assessment"],
                    "uri": a["uri"],
                }
                for a in successful_analyses
            ],
            "recommendations": recommendations,
            "warnings": [a for a in all_analyses if "error" in a],
        }

    def _get_quality_distribution(self, analyses: List[Dict[str, Any]]) -> Dict[str, int]:
        """Calculate distribution of quality assessments."""
        distribution = {
            "very_high_confidence": 0,
            "high_confidence": 0,
            "low_confidence": 0,
            "very_low_confidence": 0,
        }

        for analysis in analyses:
            quality = analysis.get("quality_assessment")
            if quality in distribution:
                distribution[quality] += 1

        return distribution

    def _generate_recommendations(
        self, analyses: List[Dict[str, Any]], summary: Dict[str, Any]
    ) -> List[str]:
        """Generate recommendations based on analysis."""
        recommendations = []

        if not analyses:
            return ["No successful predictions to analyze"]

        best = analyses[0]
        worst = analyses[-1]

        # Best model recommendation
        recommendations.append(
            f"Best model: {best['model_name']} "
            f"(pLDDT: {best['plddt_mean']:.2f}, Quality: {best['quality_assessment'].replace('_', ' ').title()})"
        )

        # Quality assessment
        if best["plddt_mean"] >= 90:
            recommendations.append(
                "Excellent prediction quality - very high confidence across best model"
            )
        elif best["plddt_mean"] >= 70:
            recommendations.append("Good prediction quality - high confidence in best model")
        elif best["plddt_mean"] >= 50:
            recommendations.append("Moderate prediction quality - consider experimental validation")
        else:
            recommendations.append(
                "Low prediction quality - experimental validation strongly recommended"
            )

        # Consistency check
        plddt_range = summary["plddt_stats"]["range"]
        if plddt_range < 5:
            recommendations.append(
                f"High consistency across models (pLDDT range: {plddt_range:.2f}) - "
                "predictions agree well"
            )
        elif plddt_range > 20:
            recommendations.append(
                f"Large variation between models (pLDDT range: {plddt_range:.2f}) - "
                "consider multiple predictions"
            )

        # PAE recommendation
        if best["has_pae"] and best["pae_mean"] is not None:
            if best["pae_mean"] < 5:
                recommendations.append("Very low PAE - excellent domain packing and alignment")
            elif best["pae_mean"] < 15:
                recommendations.append("Low PAE - good inter-domain confidence")
            elif best["pae_mean"] > 25:
                recommendations.append(
                    "High PAE - uncertain domain arrangement, consider rigid-body domains"
                )

        # Model diversity
        unique_models = set(a["model_name"].split("_")[0] for a in analyses)
        if len(unique_models) > 1:
            recommendations.append(
                f"Analyzed {len(unique_models)} different model architectures for robustness"
            )

        # Distribution insight
        dist = summary["quality_distribution"]
        high_quality = dist["very_high_confidence"] + dist["high_confidence"]
        if high_quality > len(analyses) / 2:
            recommendations.append(
                f"{high_quality}/{len(analyses)} predictions show high/very high confidence"
            )

        return recommendations
