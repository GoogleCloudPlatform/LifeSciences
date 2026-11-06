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

"""Shared KFP model plugin base classes and post-submission tools."""

import contextlib
import json
import logging
import os
import re
import sys
import tempfile
import time
import urllib.parse
import webbrowser
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import quote_plus

from google.cloud import aiplatform as vertex_ai
from google.cloud import run_v2, storage

from foldrun_app.core.base_tool import BaseTool
from foldrun_app.core.compile_lock import (
    GLOBAL_COMPILE_LOCK,
    CompileEnvContext,
    setup_compile_env_vars,
)
from foldrun_app.core.download_utils import build_agent_downloads_dict
from foldrun_app.core.vertex_utils import extract_short_job_id, get_pipeline_job
from foldrun_common import (
    JOB_ID_PATTERN as _JOB_ID_PATTERN,
)
from foldrun_common import (
    AnalysisMetadata,
    cleanup_stale_analysis_blobs,
)

logger = logging.getLogger(__name__)


class KFPModelTool(BaseTool):
    """Shared base class for KFP-backed structure prediction model plugins (AF2, OF3, Boltz-2)."""

    _compile_env_lock = GLOBAL_COMPILE_LOCK

    # Canonical (machine_type, accelerator_type, default_count) per GPU tier
    GPU_SPECS: ClassVar[dict[str, tuple[str, str, int]]] = {
        "L4": ("g2-standard-12", "NVIDIA_L4", 1),
        "A100": ("a2-highgpu-1g", "NVIDIA_TESLA_A100", 1),
        "A100_80GB": ("a2-ultragpu-1g", "NVIDIA_A100_80GB", 1),
    }

    # Canonical predict -> relax GPU tier mapping (used by AF2 submission and cost estimation)
    RELAX_GPU_MAP: ClassVar[dict[str, str]] = {
        "H100": "A100",
        "A100_80GB": "A100",
        "A100": "A100",
        "L4": "L4",
    }

    DEFAULT_MSA_MACHINE: ClassVar[str] = "c2-standard-16"

    ACCEL_TO_LABEL: ClassVar[dict[str, str]] = {
        "NVIDIA_L4": "l4",
        "NVIDIA_TESLA_A100": "a100",
        "NVIDIA_A100_80GB": "a100-80gb",
    }

    @staticmethod
    def sanitize_job_name(job_name: str | None, default_name: str) -> str:
        """Sanitize ``job_name`` for safe use in temp file paths, GCS object names, and Vertex display names."""
        raw = str(job_name).strip() if job_name is not None else ""
        if not raw:
            return default_name
        cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", raw).strip("._-")
        return cleaned or default_name

    def resolve_local_input_path(self, file_path: str, *, allow_temp_dir: bool = False) -> str:
        """Validate that a local file path resides within an authorized directory.

        Resolves symlinks and relative segments via ``os.path.realpath`` before
        verifying directory containment with ``os.path.commonpath``.
        """
        cfg_allowed = getattr(self.config, "allowed_input_dir", None)
        allowed_dir = (
            cfg_allowed.strip()
            if isinstance(cfg_allowed, str) and cfg_allowed.strip()
            else os.environ.get("FOLDRUN_ALLOWED_INPUT_DIR", "").strip()
            or os.environ.get("FOLDRUN_STAGING_DIR", "").strip()
        )
        if not allowed_dir:
            if allow_temp_dir:
                allowed_dir = tempfile.gettempdir()
            else:
                raise ValueError(
                    "Local file input is not allowed unless FOLDRUN_ALLOWED_INPUT_DIR is explicitly configured "
                    "(authorized directory required). Provide inline content or a gs:// URI instead."
                )

        real_allowed = os.path.realpath(allowed_dir)
        real_path = os.path.realpath(file_path)

        if (
            os.path.commonpath([real_allowed, real_path]) != real_allowed
            or real_path == real_allowed
        ):
            raise ValueError(
                f"Local file path must be within the authorized directory and not outside the authorized directory ({real_allowed}): {file_path}"
            )
        if not os.path.isfile(real_path):
            raise ValueError(
                f"Local file path must be within the authorized directory ({real_allowed}) and exist as a file: {file_path}"
            )

        return real_path

    def _ensure_filestore_config(self) -> tuple[str, str]:
        """Return ``(filestore_ip, filestore_network)``, looking up Filestore if not yet cached."""
        if not self.config.filestore_ip:
            return self._get_filestore_info()
        return self.config.filestore_ip, self.config.filestore_network

    def _compile_and_submit_kfp_pipeline(
        self,
        *,
        pipeline_loader: Callable[[], Any],
        package_path: str,
        hardware_config: dict[str, Any],
        filestore_ip: str | None,
        filestore_network: str | None,
        job_kwargs: dict[str, Any],
        service_account: str | None = None,
        extra_temp_paths: list[str] | None = None,
    ) -> Any:
        """Compile a KFP pipeline under ``GLOBAL_COMPILE_LOCK``, submit the Vertex ``PipelineJob``, and clean up temp files."""
        from kfp import compiler

        try:
            with (
                GLOBAL_COMPILE_LOCK,
                self._setup_compile_env(hardware_config, filestore_ip, filestore_network)  # type: ignore[attr-defined]
                or contextlib.nullcontext(),
            ):
                pipeline = pipeline_loader()
                compiler.Compiler().compile(pipeline_func=pipeline, package_path=package_path)

            vertex_ai_mod = self._resolve_module_attr("vertex_ai", vertex_ai)
            pipeline_job = vertex_ai_mod.PipelineJob(**job_kwargs)
            pipeline_job.submit(
                network=filestore_network,
                service_account=service_account or self.config.pipelines_sa_email,
            )
            return pipeline_job
        finally:
            for path in [package_path, *(extra_temp_paths or [])]:
                if path and os.path.exists(path):
                    with contextlib.suppress(OSError):
                        os.remove(path)

    @staticmethod
    def _normalize_gpu_type(gpu_type: str | None) -> str:
        """Normalize GPU type strings (e.g. 'l4', 'a100-80gb', 'auto')."""
        normalized = (gpu_type or "auto").strip()
        if not normalized or normalized.lower() == "auto":
            return "auto"
        return normalized.upper().replace("-", "_")

    @classmethod
    def predict_spec_for_gpu(cls, gpu_type: str) -> dict[str, Any]:
        """Return predict hardware fields for ``gpu_type``."""
        machine, accel, count = cls.GPU_SPECS[gpu_type]
        return {
            "predict_machine": machine,
            "predict_accel": accel,
            "predict_count": count,
        }

    @classmethod
    def relax_spec_for_gpu(cls, gpu_type: str) -> dict[str, Any]:
        """Return relax hardware fields for ``gpu_type``."""
        machine, accel, count = cls.GPU_SPECS[gpu_type]
        return {
            "relax_machine": machine,
            "relax_accel": accel,
            "relax_count": count,
        }

    @staticmethod
    def _scale_multi_gpu_machine(hw_config: dict[str, Any], gpu_type: str, num_gpus: int) -> None:
        """Scale A2 machine type and accelerator count when ``num_gpus > 1``."""
        if num_gpus > 1 and gpu_type in ("A100", "A100_80GB"):
            base = "a2-highgpu" if gpu_type == "A100" else "a2-ultragpu"
            hw_config["predict_machine"] = f"{base}-{num_gpus}g"
            hw_config["predict_count"] = num_gpus

    def _resolve_supported_gpu(
        self,
        gpu_type: str,
        *,
        allowed_pool: list[str],
        empty_pool_error: str,
    ) -> str:
        """Resolve ``gpu_type`` against ``self.config.supported_gpus`` within ``allowed_pool``."""
        mod_logger = logging.getLogger(type(self).__module__)
        supported = [g for g in self.config.supported_gpus if g in allowed_pool]
        if not supported:
            raise ValueError(empty_pool_error)

        if gpu_type in supported:
            return gpu_type

        original_type = gpu_type
        try:
            start_idx = allowed_pool.index(original_type)
        except ValueError:
            start_idx = 0

        for candidate in allowed_pool[start_idx:]:
            if candidate in supported:
                mod_logger.warning(
                    f"Requested GPU '{original_type}' not supported. Auto-selecting '{candidate}'."
                )
                return candidate

        for candidate in reversed(allowed_pool[:start_idx]):
            if candidate in supported:
                mod_logger.warning(
                    f"Requested GPU '{original_type}' not supported. Auto-selecting '{candidate}'."
                )
                return candidate

        fallback = supported[-1]
        mod_logger.warning(
            f"Requested GPU '{original_type}' not supported. Falling back to '{fallback}'."
        )
        return fallback

    def _build_base_compile_env(
        self,
        hardware_config: dict[str, Any],
        filestore_ip: str | None = None,
        filestore_network: str | None = None,
        *,
        default_machine: str = "a2-highgpu-1g",
        default_accel: str = "NVIDIA_TESLA_A100",
    ) -> dict[str, str]:
        """Build common KFP compilation environment variables."""
        nfs_server = filestore_ip or self.config.filestore_ip or ""
        network = filestore_network or self.config.filestore_network or ""
        return {
            "NFS_SERVER": nfs_server,
            "NFS_PATH": self.config.nfs_share,
            "NFS_MOUNT_POINT": self.config.nfs_mount_point,
            "NETWORK": network,
            "PREDICT_MACHINE_TYPE": hardware_config.get("predict_machine", default_machine),
            "PREDICT_ACCELERATOR_TYPE": hardware_config.get("predict_accel", default_accel),
            "PREDICT_ACCELERATOR_COUNT": str(hardware_config.get("predict_count", 1)),
            "DWS_MAX_WAIT_HOURS": str(hardware_config.get("dws_max_wait_hours", 168)),
        }

    def _scoped_compile_env(
        self,
        env_vars: dict[str, str],
        *,
        keys_to_clear: tuple[str, ...] = (),
    ) -> CompileEnvContext:
        """Enter a thread-safe compile environment context."""
        return setup_compile_env_vars(
            env_vars,
            keys_to_clear=keys_to_clear,
            lock=self._compile_env_lock,
        )

    def _resolve_module_attr(self, attr_name: str, default: Any) -> Any:
        """Resolve an attribute from the concrete tool's module first (preserves test @patch targets)."""
        mod = sys.modules.get(self.__class__.__module__)
        if mod is not None and hasattr(mod, attr_name):
            return getattr(mod, attr_name)
        return default

    def _get_analysis_path(self, job: Any) -> str:
        """Extract and validate ``<gcs_output_directory>/analysis/`` from a PipelineJob."""
        pipeline_root = None
        if hasattr(job, "runtime_config") and job.runtime_config:
            if (
                hasattr(job.runtime_config, "gcs_output_directory")
                and job.runtime_config.gcs_output_directory
            ):
                pipeline_root = job.runtime_config.gcs_output_directory

        if not pipeline_root:
            raise ValueError("Job does not have gcs_output_directory in runtime_config")

        self._validate_gcs_uri(pipeline_root)
        if not pipeline_root.endswith("/"):
            pipeline_root += "/"

        analysis_path = f"{pipeline_root}analysis/"
        self._validate_gcs_uri(analysis_path)
        return analysis_path

    def _fetch_pipeline_job(self, job_id: str, preloaded_job: Any | None = None) -> Any:
        """Return ``preloaded_job`` if provided, otherwise look up the Vertex PipelineJob."""
        if preloaded_job is not None:
            return preloaded_job
        import foldrun_app.core.vertex_utils as vertex_utils_mod

        mod = sys.modules.get(self.__class__.__module__)
        mod_getter = getattr(mod, "get_pipeline_job", None) if mod is not None else None
        if mod_getter is not None and mod_getter is not get_pipeline_job:
            return mod_getter(job_id, self.config.project_id, self.config.region)
        if type(get_pipeline_job).__module__.startswith("unittest.mock"):
            return get_pipeline_job(job_id, self.config.project_id, self.config.region)
        return vertex_utils_mod.get_pipeline_job(job_id, self.config.project_id, self.config.region)


class KFPAnalyzeJobTool(KFPModelTool):
    """Shared base class for triggering Cloud Run Job parallel structure analysis."""

    model_type: ClassVar[str] = "alphafold2"
    display_label: ClassVar[str] = ""
    check_existing_before_discovery: ClassVar[bool] = True

    def __init__(
        self,
        tool_config: dict[str, Any] | None = None,
        config: Any = None,
        *,
        name: str | None = None,
        description: str | None = None,
    ):
        super().__init__(
            tool_config=tool_config,
            config=config,
            name=name,
            description=description,
        )
        self.job_name = os.getenv("ANALYSIS_JOB_NAME", "foldrun-analysis-job")

    def _storage_module(self) -> Any:
        return self._resolve_module_attr("storage", storage)

    def _run_v2_module(self) -> Any:
        return self._resolve_module_attr("run_v2", run_v2)

    def _write_to_gcs(self, gcs_uri: str, data: dict[str, Any]) -> None:
        """Write JSON data to an authorized GCS path."""
        bucket_name, blob_name = self._validate_gcs_uri(gcs_uri)
        storage_client = self._storage_module().Client(project=self.config.project_id)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(json.dumps(data, indent=2), content_type="application/json")
        logger.info(f"Wrote data to {gcs_uri}")

    def _check_existing_analysis(self, analysis_path: str) -> tuple[bool, dict | None]:
        """Check whether ``summary.json`` already exists under ``analysis_path``."""
        bucket_name, prefix = self._validate_gcs_uri(analysis_path)
        try:
            storage_client = self._storage_module().Client(project=self.config.project_id)
            bucket = storage_client.bucket(bucket_name)
            summary_blob = bucket.blob(f"{prefix}summary.json")
            if summary_blob.exists():
                logger.info(f"Found existing summary.json at {analysis_path}summary.json")
                content = summary_blob.download_as_text()
                return True, json.loads(content)
            return False, None
        except Exception as e:
            logger.warning(f"Could not check for existing analysis: {e}")
            return False, None

    def _cleanup_stale_analysis_blobs(self, analysis_path: str) -> int:
        """Delete stale per-prediction and summary blobs prior to re-analysis."""
        bucket_name, prefix = self._validate_gcs_uri(analysis_path)
        try:
            storage_client = self._storage_module().Client(project=self.config.project_id)
            bucket = storage_client.bucket(bucket_name)
            return cleanup_stale_analysis_blobs(bucket, prefix)
        except Exception as list_err:
            logger.warning(f"Could not list stale analysis blobs under {analysis_path}: {list_err}")
            return 0

    def _discover_and_build_task_config(
        self,
        job: Any,
        job_id: str,
        analysis_path: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], int, dict[str, Any]] | dict[str, Any]:
        """Discover prediction outputs and construct ``(task_config, total_predictions, extra_metadata)``.

        May return a status dict directly (e.g. ``{"status": "no_predictions", ...}``) to short-circuit.
        """
        raise NotImplementedError

    def _already_exists_response(
        self, analysis_path: str, existing_summary: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "status": "already_exists",
            "message": (
                "Analysis already exists. Use overwrite=true to re-analyze, "
                "or use get_analysis_results to retrieve the existing analysis."
            ),
            "analysis_path": analysis_path,
            "summary_uri": f"{analysis_path}summary.json",
            **existing_summary,
        }

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Trigger parallel analysis via Cloud Run Job."""
        job_id = arguments.get("job_id")
        overwrite = arguments.get("overwrite", False)
        preloaded_job = arguments.get("_pipeline_job")

        if not job_id:
            raise ValueError("job_id is required")

        label_prefix = f"{self.display_label} " if self.display_label else ""
        logger.info(f"Starting {label_prefix}parallel analysis for job {job_id}")

        try:
            job = self._fetch_pipeline_job(job_id, preloaded_job=preloaded_job)
        except Exception as e:
            return {"status": "error", "message": f"Could not find job {job_id}: {e!s}"}

        job_res_name = getattr(job, "name", None)
        job_id = (
            job_res_name.rsplit("/", 1)[-1]
            if isinstance(job_res_name, str) and job_res_name
            else str(job_id).rsplit("/", 1)[-1]
        )

        if self.check_existing_before_discovery:
            analysis_path = self._get_analysis_path(job)
            exists, existing_summary = self._check_existing_analysis(analysis_path)
            if not overwrite and exists:
                return self._already_exists_response(analysis_path, existing_summary or {})
            built = self._discover_and_build_task_config(job, job_id, analysis_path, arguments)
            if isinstance(built, dict):
                return built
            task_config, total_predictions, extra_metadata = built
        else:
            built = self._discover_and_build_task_config(job, job_id, "", arguments)
            if isinstance(built, dict):
                return built
            analysis_path = self._get_analysis_path(job)
            exists, existing_summary = self._check_existing_analysis(analysis_path)
            if not overwrite and exists:
                return self._already_exists_response(analysis_path, existing_summary or {})
            task_config, total_predictions, extra_metadata = built
            # Populate analysis_path-dependent fields when deferred until after discovery
            task_config["analysis_path"] = analysis_path
            task_config["task_config_uri"] = f"{analysis_path}task_config.json"
            for pred in task_config.get("predictions", []):
                pred["output_uri"] = f"{analysis_path}prediction_{pred['index']}_analysis.json"

        self._cleanup_stale_analysis_blobs(analysis_path)

        task_config_path = f"{analysis_path}task_config.json"
        self._write_to_gcs(task_config_path, task_config)

        metadata = AnalysisMetadata(
            job_id=job_id,
            total_predictions=total_predictions,
            started_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            status="running",
            execution_method="cloud_run_job",
            model_type=self.model_type,
            task_config_uri=task_config_path,
            **extra_metadata,
        ).model_dump(exclude_none=True)
        self._write_to_gcs(f"{analysis_path}analysis_metadata.json", metadata)

        run_mod = self._run_v2_module()
        client = run_mod.JobsClient()
        job_path = (
            f"projects/{self.config.project_id}/locations/{self.config.region}/jobs/{self.job_name}"
        )
        task_count = total_predictions

        try:
            request = run_mod.RunJobRequest(
                name=job_path,
                overrides=run_mod.RunJobRequest.Overrides(
                    task_count=task_count,
                    timeout={"seconds": 600},
                    container_overrides=[
                        run_mod.RunJobRequest.Overrides.ContainerOverride(
                            env=[
                                run_mod.EnvVar(name="ANALYSIS_PATH", value=analysis_path),
                                run_mod.EnvVar(name="MODEL_TYPE", value=self.model_type),
                                run_mod.EnvVar(
                                    name="GEMINI_MODEL",
                                    value=os.getenv("GEMINI_ANALYSIS_MODEL", "gemini-4-argon"),
                                ),
                            ]
                        )
                    ],
                ),
            )

            operation = client.run_job(request=request)

            execution_name = None
            execution_uid = None
            if hasattr(operation, "metadata") and operation.metadata:
                if hasattr(operation.metadata, "name"):
                    execution_name = operation.metadata.name
                    execution_uid = execution_name.split("/")[-1] if execution_name else None

            metadata["execution_name"] = execution_name
            metadata["execution_uid"] = execution_uid
            metadata["cloud_run_job"] = job_path
            self._write_to_gcs(f"{analysis_path}analysis_metadata.json", metadata)

            started_prefix = f"{self.display_label} analysis" if self.display_label else "Analysis"
            return {
                "status": "started",
                "job_id": job_id,
                "analysis_path": analysis_path,
                "gcs_console_url": self.gcs_console_url(analysis_path),
                "total_predictions": total_predictions,
                "execution_name": execution_name,
                "execution_uid": execution_uid,
                "cloud_run_job": job_path,
                "message": (
                    f"{started_prefix} started with {task_count} parallel tasks. "
                    "Use get_analysis_results to check status."
                ),
            }

        except Exception as e:
            logger.error(f"Failed to execute Cloud Run Job: {e}", exc_info=True)
            return {
                "status": "failed",
                "job_id": job_id,
                "error": str(e),
                "message": f"Failed to start {label_prefix}Cloud Run Job execution",
            }


class KFPGetAnalysisResultsTool(KFPModelTool):
    """Shared base class for retrieving and polling Cloud Run Job analysis results."""

    model_type: ClassVar[str] = "alphafold2"
    item_noun: ClassVar[str] = "predictions"

    def _storage_module(self) -> Any:
        return self._resolve_module_attr("storage", storage)

    def _run_v2_module(self) -> Any:
        return self._resolve_module_attr("run_v2", run_v2)

    def _read_from_gcs(self, gcs_uri: str) -> dict[str, Any]:
        """Read JSON data from an authorized GCS path."""
        bucket_name, blob_name = self._validate_gcs_uri(gcs_uri)
        storage_client = self._storage_module().Client(project=self.config.project_id)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        return json.loads(blob.download_as_text())

    def _check_cloudrun_execution_status(self, execution_name: str) -> dict[str, Any] | None:
        """Check the status of a Cloud Run job execution."""
        try:
            client = self._run_v2_module().ExecutionsClient()
            execution = client.get_execution(name=execution_name, timeout=10)

            failed_count = execution.failed_count if hasattr(execution, "failed_count") else 0
            succeeded_count = (
                execution.succeeded_count if hasattr(execution, "succeeded_count") else 0
            )
            running_count = execution.running_count if hasattr(execution, "running_count") else 0
            task_count = execution.task_count if hasattr(execution, "task_count") else 0
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

    def _list_completed_analyses(self, analysis_path: str) -> list[str]:
        """List completed per-prediction analysis JSON files in GCS."""
        bucket_name, prefix = self._validate_gcs_uri(analysis_path)
        storage_client = self._storage_module().Client(project=self.config.project_id)
        bucket = storage_client.bucket(bucket_name)
        blobs = bucket.list_blobs(prefix=prefix)
        return [
            f"gs://{bucket_name}/{blob.name}"
            for blob in blobs
            if "prediction_" in blob.name and blob.name.endswith("_analysis.json")
        ]

    def _build_viewer_url(
        self, job_id: str, summary_uri: str, summary: dict[str, Any] | None = None
    ) -> str | None:
        """Build the viewer URL from analysis results."""
        try:
            viewer_base = self.config.viewer_url
            if not viewer_base:
                return None

            base = viewer_base.rstrip("/")
            if self.model_type in (
                "openfold3",
                "boltz2",
                "alphafold3",
                "boltzgen",
                "rfd4_proteina",
                "bindcraft",
            ):
                return f"{base}/job/{quote_plus(job_id)}"

            summary_dict = summary or {}
            best = summary_dict.get("best_prediction", {})
            pkl_uri = best.get("uri")
            if not pkl_uri:
                return None

            if str(pkl_uri).endswith(".cif") or summary_dict.get("job_type") in (
                "alphafold3",
                "openfold3",
                "boltz2",
                "boltzgen",
                "rfd4_proteina",
                "bindcraft",
            ):
                return f"{base}/job/{quote_plus(job_id)}"

            pdb_uri = pkl_uri.rsplit("/", 1)[0] + "/unrelaxed_protein.pdb"
            model_name = best.get("model_name", "Best Model")
            params = [
                f"pdb_uri={quote_plus(pdb_uri)}",
                f"job_id={quote_plus(job_id)}",
                f"model={quote_plus(model_name)}",
            ]
            if summary_uri:
                params.append(f"summary_uri={quote_plus(summary_uri)}")
            return f"{base}/combined?{'&'.join(params)}"
        except Exception as e:
            logger.warning(f"Could not build viewer URL: {e}")
            return None

    def _trim_summary(self, summary: dict[str, Any]) -> dict[str, Any]:
        """Remove bulky per-residue score arrays from summary lists to reduce response size."""
        for key in ("top_predictions", "all_predictions_summary"):
            if key in summary and isinstance(summary[key], list):
                for pred in summary[key]:
                    if isinstance(pred, dict):
                        pred.pop("plddt_scores", None)
        return summary

    def _complete_response(
        self,
        job_id: str,
        analysis_path: str,
        summary_uri: str,
        cloud_run_summary: dict[str, Any],
        *,
        include_console_url: bool = True,
    ) -> dict[str, Any]:
        viewer_url = self._build_viewer_url(job_id, summary_uri, cloud_run_summary)
        downloads_builder = self._resolve_module_attr(
            "build_agent_downloads_dict", build_agent_downloads_dict
        )
        downloads = downloads_builder(
            job_id=job_id,
            analysis_path=analysis_path,
            summary_data=cloud_run_summary,
            project_id=self.config.project_id,
            ensure_bundle=False,
        )
        result: dict[str, Any] = {
            "status": "complete",
            "job_id": job_id,
            "analysis_path": analysis_path,
        }
        if include_console_url:
            result["gcs_console_url"] = self.gcs_console_url(analysis_path)
        result["downloads"] = downloads
        result.update(cloud_run_summary)
        if viewer_url:
            result["viewer_url"] = viewer_url
        return self._trim_summary(result)

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Retrieve or poll Cloud Run analysis results for a KFP job."""
        job_id = arguments.get("job_id")
        wait = arguments.get("wait", False)
        timeout = arguments.get("timeout", 10)
        poll_interval = arguments.get("poll_interval", 2)
        preloaded_job = arguments.get("_pipeline_job")

        if not job_id:
            raise ValueError("job_id is required")

        logger.info(f"Getting analysis results for job {job_id}")

        try:
            job = self._fetch_pipeline_job(job_id, preloaded_job=preloaded_job)
        except Exception as e:
            return {"status": "error", "message": f"Could not find job {job_id}: {e!s}"}

        job_res_name = getattr(job, "name", None)
        job_id = (
            job_res_name.rsplit("/", 1)[-1]
            if isinstance(job_res_name, str) and job_res_name
            else str(job_id).rsplit("/", 1)[-1]
        )

        analysis_path = self._get_analysis_path(job)
        summary_uri = f"{analysis_path}summary.json"

        try:
            cloud_run_summary = self._read_from_gcs(summary_uri)
            logger.info("Found summary.json — returning completed analysis")
            return self._complete_response(
                job_id, analysis_path, summary_uri, cloud_run_summary, include_console_url=True
            )
        except Exception:
            logger.info("No summary.json found, checking analysis status...")

        try:
            metadata = self._read_from_gcs(f"{analysis_path}analysis_metadata.json")
        except Exception as e:
            return {
                "status": "not_found",
                "message": f"No analysis found for job {job_id}. Run analyze_job_parallel first.",
                "error": str(e),
            }

        total_predictions = metadata["total_predictions"]

        execution_name = metadata.get("execution_name")
        if execution_name:
            cloudrun_status = self._check_cloudrun_execution_status(execution_name)
            if cloudrun_status and cloudrun_status["has_failures"]:
                failed = cloudrun_status["failed_count"]
                total = cloudrun_status["task_count"]
                return {
                    "status": "failed",
                    "message": f"Cloud Run analysis job failed. {failed}/{total} tasks failed.",
                    "failed_tasks": failed,
                    "succeeded_tasks": cloudrun_status["succeeded_count"],
                    "total_tasks": total,
                    "analysis_path": analysis_path,
                    "error_hint": (
                        "Cloud Run tasks encountered errors. Common causes: prediction files "
                        "not found (404), permission issues, or invalid file formats."
                    ),
                    "suggestion": (
                        "Check Cloud Run logs for detailed error messages. The prediction files "
                        "may not exist at the expected GCS paths."
                    ),
                }

        try:
            error_data = self._read_from_gcs(f"{analysis_path}error.json")
            logger.error(f"Analysis failed with error: {error_data}")
            return {
                "status": "failed",
                "message": "Analysis failed during execution.",
                "error": error_data.get("error", "Unknown error"),
                "error_details": error_data.get("details", "No additional details available"),
                "analysis_path": analysis_path,
                "suggestion": (
                    "Check the error details above. The Cloud Run job may have encountered "
                    "issues accessing prediction files or processing them."
                ),
            }
        except Exception:
            pass

        completed_files = self._list_completed_analyses(analysis_path)
        completed_count = len(completed_files)
        logger.info(f"Found {completed_count}/{total_predictions} completed analyses")

        if wait and completed_count < total_predictions:
            start_time = time.time()
            while completed_count < total_predictions and (time.time() - start_time) < timeout:
                time.sleep(poll_interval)
                completed_files = self._list_completed_analyses(analysis_path)
                completed_count = len(completed_files)

            try:
                cloud_run_summary = self._read_from_gcs(summary_uri)
                return self._complete_response(
                    job_id,
                    analysis_path,
                    summary_uri,
                    cloud_run_summary,
                    include_console_url=False,
                )
            except Exception:
                pass

        if completed_count < total_predictions:
            if completed_count == 0:
                created_at = metadata.get("started_at")
                if created_at:
                    try:
                        created_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        elapsed = (datetime.now(created_time.tzinfo) - created_time).total_seconds()
                        if elapsed > 300:
                            return {
                                "status": "likely_failed",
                                "message": (
                                    f"Analysis appears to have failed. 0/{total_predictions} "
                                    f"{self.item_noun} analyzed after {elapsed / 60:.1f} minutes."
                                ),
                                "completed": 0,
                                "total": total_predictions,
                                "analysis_path": analysis_path,
                                "error_hint": "The Cloud Run job may have failed to find prediction files.",
                                "suggestion": (
                                    "Check Cloud Run logs for detailed errors, or try running "
                                    "analyze_job_parallel again."
                                ),
                            }
                    except Exception:
                        pass

            return {
                "status": "running",
                "message": (
                    f"Analysis still running. {completed_count}/{total_predictions} "
                    f"{self.item_noun} analyzed."
                ),
                "completed": completed_count,
                "total": total_predictions,
                "analysis_path": analysis_path,
                "suggestion": (
                    "Wait for analysis to complete, or use get_analysis_results with wait=true "
                    "to poll until done."
                ),
            }

        return {
            "status": "incomplete",
            "message": (
                "Analysis jobs completed but summary.json not found. "
                "The Cloud Run consolidation step may still be running or may have failed."
            ),
            "completed": completed_count,
            "total": total_predictions,
            "analysis_path": analysis_path,
            "suggestion": (
                "Run analyze_job_parallel again with overwrite=true to regenerate the summary "
                "with expert analysis."
            ),
        }


class KFPOpenViewerTool(KFPModelTool):
    """Shared base class for opening the FoldRun 3D structure viewer for a KFP job."""

    def __init__(
        self,
        tool_config: dict[str, Any] | None = None,
        config: Any = None,
        *,
        name: str | None = None,
        description: str | None = None,
    ):
        super().__init__(
            tool_config=tool_config,
            config=config,
            name=name,
            description=description,
        )
        self.viewer_base_url = self.config.viewer_url

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Construct and optionally open the viewer URL for ``job_id``."""
        if (
            not self.viewer_base_url
            or not isinstance(self.viewer_base_url, str)
            or not (
                self.viewer_base_url.startswith("http://")
                or self.viewer_base_url.startswith("https://")
            )
        ):
            raise ValueError(
                "Invalid viewer_base_url: must be set and start with http:// or https://"
            )

        job_id = arguments.get("job_id")
        if not job_id:
            raise ValueError("job_id is required")

        if isinstance(job_id, str) and job_id.startswith("projects/"):
            try:
                job_id = extract_short_job_id(job_id, self.config.project_id, self.config.region)
            except ValueError as e:
                raise ValueError(f"Invalid job_id '{job_id}': {e}") from e

        if not isinstance(job_id, str) or not _JOB_ID_PATTERN.match(job_id):
            raise ValueError(f"Invalid job_id '{job_id}': must match ^[a-zA-Z0-9_-]+$")

        open_browser = arguments.get("open_browser", False)

        encoded_job_id = urllib.parse.quote(job_id, safe="")
        viewer_url = f"{self.viewer_base_url.rstrip('/')}/job/{encoded_job_id}"

        browser_opened = False
        if open_browser:
            try:
                wb_mod = self._resolve_module_attr("webbrowser", webbrowser)
                wb_mod.open(viewer_url)
                browser_opened = True
                logger.info(f"Opened viewer in browser for job {job_id}")
            except Exception as e:
                logger.warning(f"Failed to open browser: {e}")

        return {
            "job_id": job_id,
            "viewer_url": viewer_url,
            "browser_opened": browser_opened,
            "message": (
                "Viewer opened in your default browser"
                if browser_opened
                else "Copy the URL to view in your browser"
            ),
        }
