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

"""Tool for aggregating async analysis results from GCS."""

import json
import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from google.cloud import run_v2, storage

from ..base import AF2Tool

logger = logging.getLogger(__name__)


class AF2GetAnalysisResultsTool(AF2Tool):
    """Tool for retrieving and aggregating Cloud Run analysis results."""

    def _get_analysis_path(self, job: Any) -> str:
        """
        Get GCS analysis path for a job.

        Stores analysis results alongside AlphaFold results:
        gs://bucket/pipeline_runs/YYYYMMDD_HHMMSS/analysis/

        Args:
            job: Vertex AI PipelineJob object (not just job_id)

        Returns:
            GCS path for analysis files (e.g., gs://bucket/pipeline_runs/.../analysis/)
        """
        # Get the pipeline root from the job's runtime_config
        pipeline_root = None
        if hasattr(job, "runtime_config") and job.runtime_config:
            if (
                hasattr(job.runtime_config, "gcs_output_directory")
                and job.runtime_config.gcs_output_directory
            ):
                pipeline_root = job.runtime_config.gcs_output_directory

        # Fallback if no runtime_config found
        if not pipeline_root:
            raise ValueError(
                "Job does not have gcs_output_directory in runtime_config. "
                "Cannot determine analysis path."
            )

        # Ensure trailing slash
        if not pipeline_root.endswith("/"):
            pipeline_root += "/"

        return f"{pipeline_root}analysis/"

    def _check_cloudrun_execution_status(self, execution_name: str) -> Dict[str, Any]:
        """Check the status of a Cloud Run job execution.

        Args:
            execution_name: Full execution name from Cloud Run

        Returns:
            Dict with status info including: state, failed_count, succeeded_count, running_count
        """
        try:
            client = run_v2.ExecutionsClient()
            execution = client.get_execution(
                name=execution_name,
                timeout=10,  # 10s timeout to avoid stalling
            )

            # Get task counts
            failed_count = execution.failed_count if hasattr(execution, "failed_count") else 0
            succeeded_count = (
                execution.succeeded_count if hasattr(execution, "succeeded_count") else 0
            )
            running_count = execution.running_count if hasattr(execution, "running_count") else 0
            task_count = execution.task_count if hasattr(execution, "task_count") else 0

            # Get completion status
            completed = (
                execution.completion_time is not None
                if hasattr(execution, "completion_time")
                else False
            )

            return {
                "state": "completed" if completed else "running",
                "failed_count": failed_count,
                "succeeded_count": succeeded_count,
                "running_count": running_count,
                "task_count": task_count,
                "has_failures": failed_count > 0,
            }
        except Exception as e:
            logger.warning(f"Could not check Cloud Run execution status: {e}")
            return None

    def _read_from_gcs(self, gcs_uri: str) -> Dict[str, Any]:
        """Read JSON data from GCS."""
        if not gcs_uri.startswith("gs://"):
            raise ValueError(f"Invalid GCS URI: {gcs_uri}")

        parts = gcs_uri[5:].split("/", 1)
        bucket_name = parts[0]
        blob_name = parts[1] if len(parts) > 1 else ""

        storage_client = storage.Client(project=self.config.project_id)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        content = blob.download_as_text()
        return json.loads(content)

    def _write_to_gcs(self, gcs_uri: str, data: Dict[str, Any]) -> None:
        """Write JSON data to GCS."""
        if not gcs_uri.startswith("gs://"):
            raise ValueError(f"Invalid GCS URI: {gcs_uri}")

        parts = gcs_uri[5:].split("/", 1)
        bucket_name = parts[0]
        blob_name = parts[1] if len(parts) > 1 else ""

        storage_client = storage.Client(project=self.config.project_id)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        blob.upload_from_string(json.dumps(data, indent=2), content_type="application/json")

    def _list_completed_analyses(self, analysis_path: str) -> List[str]:
        """List all completed analysis files in GCS."""
        if not analysis_path.startswith("gs://"):
            raise ValueError(f"Invalid GCS URI: {analysis_path}")

        parts = analysis_path[5:].split("/", 1)
        bucket_name = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""

        storage_client = storage.Client(project=self.config.project_id)
        bucket = storage_client.bucket(bucket_name)

        # List all prediction analysis files
        blobs = bucket.list_blobs(prefix=prefix)
        completed = []

        for blob in blobs:
            if "prediction_" in blob.name and blob.name.endswith("_analysis.json"):
                completed.append(f"gs://{bucket_name}/{blob.name}")

        return completed

    def _get_job_metadata(self, job_id: str) -> Dict[str, Any]:
        """Extract job metadata including accelerator, machine type, timing, etc."""
        try:
            from ..utils.vertex_utils import get_pipeline_job

            job = get_pipeline_job(job_id, self.config.project_id, self.config.region)

            # Get all labels (includes gpu_type, job_type, seq_len, seq_name, etc.)
            all_labels = dict(job.labels) if hasattr(job, "labels") and job.labels else {}

            metadata = {
                "job_id": job_id,
                "display_name": job.display_name if hasattr(job, "display_name") else None,
                "state": job.state.name if hasattr(job, "state") else None,
                "labels": all_labels,
            }

            # Extract common label fields to top level for convenience
            if "gpu_type" in all_labels:
                metadata["gpu_type"] = all_labels["gpu_type"]
            if "job_type" in all_labels:
                metadata["job_type"] = all_labels["job_type"]
            if "seq_len" in all_labels:
                metadata["seq_len"] = (
                    int(all_labels["seq_len"])
                    if all_labels["seq_len"].isdigit()
                    else all_labels["seq_len"]
                )
            if "seq_name" in all_labels:
                metadata["seq_name"] = all_labels["seq_name"]

            # Extract timing information
            if hasattr(job, "create_time") and job.create_time:
                metadata["created"] = job.create_time.isoformat()
            if hasattr(job, "start_time") and job.start_time:
                metadata["started"] = job.start_time.isoformat()
            if hasattr(job, "end_time") and job.end_time:
                metadata["completed"] = job.end_time.isoformat()

            # Calculate total duration
            if (
                hasattr(job, "start_time")
                and hasattr(job, "end_time")
                and job.start_time
                and job.end_time
            ):
                duration = job.end_time - job.start_time
                total_seconds = duration.total_seconds()
                metadata["duration_seconds"] = total_seconds
                metadata["duration_formatted"] = self._format_duration(total_seconds)

            # Extract resource configuration from pipeline spec if available
            machine_type_found = False
            if hasattr(job, "pipeline_spec") and job.pipeline_spec:
                spec = job.pipeline_spec

                # Try to extract machine type and accelerator from deploymentSpec
                if "deploymentSpec" in spec:
                    deployment = spec["deploymentSpec"]

                    # Check executors
                    if "executors" in deployment:
                        for executor_name, executor_config in deployment["executors"].items():
                            # Look for predict or relax tasks (these use GPUs)
                            if any(
                                keyword in executor_name.lower() for keyword in ["predict", "relax"]
                            ):
                                container = executor_config.get("container", {})
                                resources = container.get("resources", {})

                                # Extract machine spec
                                if "machineSpec" in resources:
                                    machine_spec = resources["machineSpec"]
                                    if "machineType" in machine_spec:
                                        metadata["machine_type"] = machine_spec["machineType"]
                                        machine_type_found = True
                                    if "acceleratorType" in machine_spec:
                                        metadata["accelerator_type"] = machine_spec[
                                            "acceleratorType"
                                        ]
                                    if "acceleratorCount" in machine_spec:
                                        metadata["accelerator_count"] = machine_spec[
                                            "acceleratorCount"
                                        ]

                                if machine_type_found:
                                    break

            # If not found in spec but gpu_type label exists, add it
            if "gpu_type" in all_labels and "accelerator_type" not in metadata:
                # Map gpu_type label to accelerator naming
                gpu_map = {
                    "l4": "NVIDIA_L4",
                    "t4": "NVIDIA_TESLA_T4",
                    "a100": "NVIDIA_TESLA_A100",
                    "v100": "NVIDIA_TESLA_V100",
                }
                gpu_label = all_labels["gpu_type"].lower()
                if gpu_label in gpu_map:
                    metadata["accelerator_type"] = gpu_map[gpu_label]
                else:
                    metadata["accelerator_type"] = all_labels["gpu_type"].upper()

            return metadata

        except Exception as e:
            logger.warning(f"Could not extract job metadata: {e}")
            return {"job_id": job_id}

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable format."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f}m"
        else:
            hours = seconds / 3600
            minutes = (seconds % 3600) / 60
            return f"{hours:.0f}h {minutes:.0f}m"

    def _calculate_summary(
        self, all_analyses: List[Dict[str, Any]], top_n: int, job_id: str = None
    ) -> Dict[str, Any]:
        """Calculate summary statistics from all analyses (AlphaFold DB style)."""
        if not all_analyses:
            return {}

        # Sort by multiple criteria (same as local analysis)
        all_analyses.sort(
            key=lambda x: (
                x["plddt_mean"],
                x["plddt_min"],
                -x["pae_mean"] if x["pae_mean"] is not None else 0,
                x["ranking_confidence"],
            ),
            reverse=True,
        )

        # Re-rank by pLDDT
        for i, analysis in enumerate(all_analyses):
            analysis["plddt_rank"] = i + 1

        # Get best prediction
        best = all_analyses[0]

        # Get job metadata and sequence
        job_metadata = self._get_job_metadata(job_id) if job_id else {}
        sequence = self._get_sequence_from_job(job_id) if job_id else None

        # Calculate statistics
        plddt_means = [a["plddt_mean"] for a in all_analyses]
        pae_means = [a["pae_mean"] for a in all_analyses if a["pae_mean"] is not None]

        # Build enhanced summary (AlphaFold DB style)
        summary_stats = {
            "total_predictions": len(all_analyses),
            "analyzed_successfully": len(all_analyses),
            "failed": 0,  # Failed analyses won't have files
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
            "quality_distribution": self._get_quality_distribution(all_analyses),
        }

        # Generate recommendations
        recommendations = self._generate_recommendations(all_analyses, summary_stats)

        # Build AlphaFold DB-style top-level summary
        confidence_breakdown = self._calculate_confidence_breakdown(best)
        downloads = self._get_download_urls(job_id, best) if job_id else {}

        # Enhanced summary structure
        sequence_length = len(best.get("plddt_scores", []))
        if not sequence_length and sequence:
            sequence_length = len(sequence)

        protein_info = {
            "sequence_length": sequence_length,
            "model_name": best.get("model_name"),
            "total_predictions": len(all_analyses),
            "model_identifier": f"{job_id.split('/')[-1] if job_id else 'unknown'}-{best.get('model_name', 'unknown')}",
            "created": best.get("analyzed_at"),
            "coverage": f"{(sequence_length / sequence_length * 100):.1f}%"
            if sequence_length > 0
            else "0.0%",
            "segment_start": 1,
            "segment_end": sequence_length,
            "confidence_type": "pLDDT",
            "provider": "Vertex AI AlphaFold2",
        }

        # Add sequence if available
        if sequence:
            protein_info["sequence"] = sequence
            # Add FASTA format too for easy copying
            seq_name = job_metadata.get("labels", {}).get("seq_name", "sequence")
            protein_info["fasta"] = f">{seq_name}\n{sequence}"

        # Add job metadata
        if job_metadata:
            protein_info["job_metadata"] = {
                "display_name": job_metadata.get("display_name"),
                "state": job_metadata.get("state"),
                "created": job_metadata.get("created"),
                "started": job_metadata.get("started"),
                "completed": job_metadata.get("completed"),
                "duration_seconds": job_metadata.get("duration_seconds"),
                "duration_formatted": job_metadata.get("duration_formatted"),
                "machine_type": job_metadata.get("machine_type"),
                "accelerator_type": job_metadata.get("accelerator_type"),
                "accelerator_count": job_metadata.get("accelerator_count"),
                # Add commonly used label fields at top level
                "gpu_type": job_metadata.get("gpu_type"),
                "job_type": job_metadata.get("job_type"),
                "seq_len": job_metadata.get("seq_len"),
                "seq_name": job_metadata.get("seq_name"),
                # All labels
                "labels": job_metadata.get("labels", {}),
            }

        enhanced_summary = {
            # Quick overview (like AlphaFold DB)
            "protein_info": protein_info,
            # Quality metrics (like AlphaFold DB global metrics)
            "quality_metrics": {
                "best_model_plddt": best["plddt_mean"],
                "best_model_pae": best["pae_mean"],
                "quality_assessment": best["quality_assessment"],
                "confidence_avg_local_score": best["plddt_mean"],  # AlphaFold DB naming
                "confidence_breakdown": confidence_breakdown,
            },
            # Direct file access (like AlphaFold DB downloads)
            "downloads": downloads,
            # Detailed stats
            "statistics": summary_stats,
        }

        return {
            "summary": enhanced_summary,
            "best_prediction": best,
            "top_predictions": all_analyses[:top_n],
            "all_predictions_summary": [
                {
                    "rank": a["rank"],
                    "plddt_rank": a["plddt_rank"],
                    "model_name": a["model_name"],
                    "ranking_confidence": a["ranking_confidence"],
                    "plddt_mean": a["plddt_mean"],
                    "plddt_min": a["plddt_min"],
                    "pae_mean": a["pae_mean"],
                    "quality_assessment": a["quality_assessment"],
                    "uri": a["uri"],
                    "plots": a.get("plots", {}),  # Include plot URLs if available
                }
                for a in all_analyses
            ],
            "recommendations": recommendations,
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

    def _calculate_confidence_breakdown(self, best_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate confidence breakdown as percentages (AlphaFold DB style).

        Args:
            best_analysis: Analysis dict containing plddt_scores array

        Returns:
            Dict with confidence levels as percentages and counts
        """
        if "plddt_scores" not in best_analysis or not best_analysis["plddt_scores"]:
            return None

        plddt_scores = best_analysis["plddt_scores"]
        total_residues = len(plddt_scores)

        # Count residues in each confidence category
        very_high = sum(1 for score in plddt_scores if score > 90)
        confident = sum(1 for score in plddt_scores if 70 <= score <= 90)
        low = sum(1 for score in plddt_scores if 50 <= score < 70)
        very_low = sum(1 for score in plddt_scores if score < 50)

        return {
            "very_high (>90)": {
                "count": very_high,
                "percentage": f"{(very_high / total_residues * 100):.1f}%",
            },
            "confident (70-90)": {
                "count": confident,
                "percentage": f"{(confident / total_residues * 100):.1f}%",
            },
            "low (50-70)": {"count": low, "percentage": f"{(low / total_residues * 100):.1f}%"},
            "very_low (<50)": {
                "count": very_low,
                "percentage": f"{(very_low / total_residues * 100):.1f}%",
            },
        }

    def _get_download_urls(self, job_id: str, best_analysis: Dict[str, Any]) -> Dict[str, str]:
        """
        Generate direct download URLs for prediction files (AlphaFold DB style).

        Args:
            job_id: Pipeline job ID
            best_analysis: Best prediction analysis

        Returns:
            Dict with labeled download URLs
        """
        # Get the actual pipeline root from the job's runtime_config
        pipeline_root = None
        try:
            from ..utils.vertex_utils import get_pipeline_job

            job = get_pipeline_job(job_id, self.config.project_id, self.config.region)
            if hasattr(job, "runtime_config") and job.runtime_config:
                if (
                    hasattr(job.runtime_config, "gcs_output_directory")
                    and job.runtime_config.gcs_output_directory
                ):
                    pipeline_root = job.runtime_config.gcs_output_directory.rstrip("/")
        except Exception as e:
            logger.warning(f"Could not get pipeline root from job: {e}")

        downloads = {}

        # Best model PDB (ranked)
        if "uri" in best_analysis:
            downloads["best_model_pdb"] = best_analysis["uri"]

        # Plots (if available)
        plots = best_analysis.get("plots", {})
        if plots.get("plddt_plot"):
            downloads["plddt_plot"] = plots["plddt_plot"]
        if plots.get("pae_plot"):
            downloads["pae_plot"] = plots["pae_plot"]

        # Only add path-based downloads if we resolved the pipeline root
        if pipeline_root:
            model_name = best_analysis.get("model_name", "model_1_pred_0")
            downloads["best_model_relaxed"] = f"{pipeline_root}/predict/relaxed_{model_name}.pdb"
            downloads["raw_prediction"] = f"{pipeline_root}/predict/{model_name}.pkl"
            downloads["features"] = f"{pipeline_root}/data_pipeline/features.pkl"
            downloads["analysis_summary"] = f"{pipeline_root}/analysis/summary.json"
            downloads["gcs_console_url"] = self.gcs_console_url(pipeline_root)

        return downloads

    def _generate_recommendations(
        self, analyses: List[Dict[str, Any]], summary: Dict[str, Any]
    ) -> List[str]:
        """Generate recommendations based on analysis."""
        recommendations = []

        if not analyses:
            return ["No successful predictions to analyze"]

        best = analyses[0]

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
                f"High consistency across models (pLDDT range: {plddt_range:.2f}) - predictions agree well"
            )
        elif plddt_range > 20:
            recommendations.append(
                f"Large variation between models (pLDDT range: {plddt_range:.2f}) - consider multiple predictions"
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

        return recommendations

    def _get_sequence_from_job(self, job_id: str) -> Optional[str]:
        """Extract FASTA sequence from job labels or GCS."""
        try:
            from ..utils.vertex_utils import get_pipeline_job

            job = get_pipeline_job(job_id, self.config.project_id, self.config.region)

            # Try to get from labels first
            if job.labels and "sequence" in job.labels:
                return job.labels["sequence"]

            # Try to read from GCS input fasta using the job's pipeline root
            fasta_path = None
            if hasattr(job, "runtime_config") and job.runtime_config:
                if (
                    hasattr(job.runtime_config, "gcs_output_directory")
                    and job.runtime_config.gcs_output_directory
                ):
                    pipeline_root = job.runtime_config.gcs_output_directory.rstrip("/")
                    fasta_path = f"{pipeline_root}/input.fasta"
            if not fasta_path:
                return None

            try:
                storage_client = storage.Client(project=self.config.project_id)
                parts = fasta_path[5:].split("/", 1)
                bucket = storage_client.bucket(parts[0])
                blob = bucket.blob(parts[1])

                if blob.exists():
                    content = blob.download_as_text()
                    # Parse FASTA (skip header line)
                    lines = content.strip().split("\n")
                    sequence = "".join(line for line in lines if not line.startswith(">"))
                    return sequence
            except Exception as e:
                logger.debug(f"Could not read FASTA from GCS: {e}")

            return None

        except Exception as e:
            logger.warning(f"Could not extract sequence from job: {e}")
            return None

    def _build_viewer_url(
        self, job_id: str, summary_uri: str, summary: Dict[str, Any]
    ) -> str | None:
        """Build the viewer URL from analysis results.

        Extracts the PDB URI from the best prediction and constructs
        the full viewer URL so the agent can present it directly.
        """
        try:
            viewer_base = self.config.viewer_url
            if not viewer_base:
                return None

            # Get PDB URI from best prediction
            best = summary.get("best_prediction", {})
            pkl_uri = best.get("uri")
            if not pkl_uri:
                return None

            # Convert pickle URI to PDB: .../raw_prediction.pkl -> .../unrelaxed_protein.pdb
            pdb_uri = pkl_uri.rsplit("/", 1)[0] + "/unrelaxed_protein.pdb"
            model_name = best.get("model_name", "Best Model")

            params = [
                f"pdb_uri={quote_plus(pdb_uri)}",
                f"job_id={quote_plus(job_id)}",
                f"model={quote_plus(model_name)}",
            ]
            if summary_uri:
                params.append(f"summary_uri={quote_plus(summary_uri)}")

            return f"{viewer_base}/combined?{'&'.join(params)}"
        except Exception as e:
            logger.warning(f"Could not build viewer URL: {e}")
            return None

    def _trim_summary(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        """Remove bulky per-residue score arrays from top_predictions to reduce response size.

        Keeps plddt_scores only in best_prediction (for confidence breakdown).
        """
        if "top_predictions" in summary:
            for pred in summary["top_predictions"]:
                pred.pop("plddt_scores", None)
        if "all_predictions_summary" in summary:
            for pred in summary["all_predictions_summary"]:
                pred.pop("plddt_scores", None)
        return summary

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get analysis results from Cloud Run.

        Args:
            arguments: {
                'job_id': Job ID (required),
                'wait': Poll until complete (default: False - NEVER blocks by default),
                'timeout': Max wait time in seconds (default: 10 - very short to avoid blocking),
                'poll_interval': Seconds between polls (default: 2)
            }

        Returns:
            Aggregated analysis results
        """
        job_id = arguments.get("job_id")
        # NEVER wait by default - return immediately with current status
        wait = arguments.get("wait", False)
        # Very short timeout to prevent blocking the agent (max 10s)
        timeout = arguments.get("timeout", 10)
        poll_interval = arguments.get("poll_interval", 2)

        if not job_id:
            raise ValueError("job_id is required")

        logger.info(f"Getting analysis results for job {job_id}")

        # Get the job object to extract pipeline root
        from ..utils.vertex_utils import get_pipeline_job

        try:
            job = get_pipeline_job(job_id, self.config.project_id, self.config.region)
        except Exception as e:
            return {"status": "error", "message": f"Could not find job {job_id}: {str(e)}"}

        # Get analysis path
        analysis_path = self._get_analysis_path(job)

        # Fast path: check for summary.json first — if it exists, analysis is done
        # and we can skip all intermediate checks (Cloud Run status, error.json, etc.)
        summary_uri = f"{analysis_path}summary.json"
        try:
            cloud_run_summary = self._read_from_gcs(summary_uri)
            logger.info("Found summary.json — returning completed analysis")

            # Build viewer URL so the agent can present it directly
            viewer_url = self._build_viewer_url(job_id, summary_uri, cloud_run_summary)

            result = {
                "status": "complete",
                "job_id": job_id,
                "analysis_path": analysis_path,
                "gcs_console_url": self.gcs_console_url(analysis_path),
                **cloud_run_summary,
            }
            if viewer_url:
                result["viewer_url"] = viewer_url
            return self._trim_summary(result)
        except Exception:
            # No summary yet — fall through to detailed status checks
            logger.info("No summary.json found, checking analysis status...")

        # Read metadata
        try:
            metadata = self._read_from_gcs(f"{analysis_path}analysis_metadata.json")
        except Exception as e:
            return {
                "status": "not_found",
                "message": f"No analysis found for job {job_id}. Run analyze_job_parallel first.",
                "error": str(e),
            }

        total_predictions = metadata["total_predictions"]

        # Check Cloud Run execution status if available
        execution_name = metadata.get("execution_name")
        cloudrun_status = None
        if execution_name:
            cloudrun_status = self._check_cloudrun_execution_status(execution_name)
            if cloudrun_status:
                logger.info(f"Cloud Run execution status: {cloudrun_status}")

                # If Cloud Run job has failures, report them immediately
                if cloudrun_status["has_failures"]:
                    failed = cloudrun_status["failed_count"]
                    total = cloudrun_status["task_count"]
                    return {
                        "status": "failed",
                        "message": f"Cloud Run analysis job failed. {failed}/{total} tasks failed.",
                        "failed_tasks": failed,
                        "succeeded_tasks": cloudrun_status["succeeded_count"],
                        "total_tasks": total,
                        "analysis_path": analysis_path,
                        "error_hint": "Cloud Run tasks encountered errors. Common causes: prediction files not found (404), permission issues, or invalid file formats.",
                        "suggestion": "Check Cloud Run logs for detailed error messages. The prediction files may not exist at the expected GCS paths.",
                    }

        # Check for errors (Cloud Run writes error.json if analysis fails)
        try:
            error_data = self._read_from_gcs(f"{analysis_path}error.json")
            logger.error(f"Analysis failed with error: {error_data}")
            return {
                "status": "failed",
                "message": "Analysis failed during execution.",
                "error": error_data.get("error", "Unknown error"),
                "error_details": error_data.get("details", "No additional details available"),
                "analysis_path": analysis_path,
                "suggestion": "Check the error details above. The Cloud Run job may have encountered issues accessing prediction files or processing them.",
            }
        except Exception:
            pass

        # Count completed analyses
        completed_files = self._list_completed_analyses(analysis_path)
        completed_count = len(completed_files)

        logger.info(f"Found {completed_count}/{total_predictions} completed analyses")

        # Wait if requested
        if wait and completed_count < total_predictions:
            start_time = time.time()
            logger.info(f"Waiting for analysis to complete (timeout: {timeout}s)...")

            while completed_count < total_predictions and (time.time() - start_time) < timeout:
                time.sleep(poll_interval)
                completed_files = self._list_completed_analyses(analysis_path)
                new_count = len(completed_files)

                if new_count > completed_count:
                    logger.info(f"Progress: {new_count}/{total_predictions} completed")
                    completed_count = new_count

            if completed_count < total_predictions:
                logger.warning(
                    f"Timeout reached. Only {completed_count}/{total_predictions} completed."
                )

            # Re-check for summary after waiting
            try:
                cloud_run_summary = self._read_from_gcs(summary_uri)
                viewer_url = self._build_viewer_url(job_id, summary_uri, cloud_run_summary)
                result = {
                    "status": "complete",
                    "job_id": job_id,
                    "analysis_path": analysis_path,
                    **cloud_run_summary,
                }
                if viewer_url:
                    result["viewer_url"] = viewer_url
                return self._trim_summary(result)
            except Exception:
                pass

        # Summary not ready
        if completed_count < total_predictions:
            if completed_count == 0:
                created_at = metadata.get("started_at")
                if created_at:
                    try:
                        from datetime import datetime

                        created_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        elapsed = (datetime.now(created_time.tzinfo) - created_time).total_seconds()
                        if elapsed > 300:  # 5 minutes
                            return {
                                "status": "likely_failed",
                                "message": f"Analysis appears to have failed. 0/{total_predictions} predictions analyzed after {elapsed / 60:.1f} minutes.",
                                "completed": 0,
                                "total": total_predictions,
                                "analysis_path": analysis_path,
                                "error_hint": "The Cloud Run job may have failed to find prediction files.",
                                "suggestion": "Check Cloud Run logs for detailed errors, or try running analyze_job_parallel again.",
                            }
                    except Exception:
                        pass

            return {
                "status": "running",
                "message": f"Analysis still running. {completed_count}/{total_predictions} predictions analyzed.",
                "completed": completed_count,
                "total": total_predictions,
                "analysis_path": analysis_path,
                "suggestion": "Wait for analysis to complete, or use get_analysis_results with wait=true to poll until done.",
            }
        else:
            return {
                "status": "incomplete",
                "message": "Analysis jobs completed but summary.json not found. The Cloud Run consolidation step may have failed.",
                "completed": completed_count,
                "total": total_predictions,
                "analysis_path": analysis_path,
                "suggestion": "Run analyze_job_parallel again with overwrite=true to regenerate the summary with expert analysis.",
            }
