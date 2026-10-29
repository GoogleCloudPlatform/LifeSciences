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

"""FastAPI router implementing the FoldRun Direct Cloud Run HTTP Gateway endpoints."""

import importlib
import importlib.util
import json
import logging
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from google.cloud import aiplatform as vertex_ai
from google.cloud import aiplatform_v1

from foldrun_app.core.hardware import check_gpu_quota
from foldrun_app.models.af2.config import Config as AF2Config
from foldrun_app.models.af2.tools.analyze_job import AF2JobAnalysisTool
from foldrun_app.models.af2.tools.submit_monomer import AF2SubmitMonomerTool
from foldrun_app.models.af2.tools.submit_multimer import AF2SubmitMultimerTool
from foldrun_app.models.af2.utils.fasta_utils import parse_fasta_content
from foldrun_app.models.af2.utils.vertex_utils import list_pipeline_jobs
from foldrun_app.models.af3.config import AF3Config
from foldrun_app.models.af3.tools.submit_prediction import AF3SubmitPredictionTool
from foldrun_app.models.boltz2.config import BOLTZ2Config
from foldrun_app.models.boltz2.tools.analyze_job import BOLTZ2JobAnalysisTool
from foldrun_app.models.boltz2.tools.submit_prediction import BOLTZ2SubmitPredictionTool
from foldrun_app.models.of3.config import OF3Config
from foldrun_app.models.of3.tools.analyze_job import OF3JobAnalysisTool
from foldrun_app.models.of3.tools.submit_prediction import OF3SubmitPredictionTool

from ._compat import (
    get_job_status,
    get_pipeline_job,
    get_task_details,
)
from .flag_allowlist import FlagValidationError, validate_and_partition_flags
from .output_packager import (
    STREAM_CHUNK_BYTES,
    _parse_gcs_uri,
    build_or_get_native_outputs_archive,
    generate_v4_signed_download_url,
)
from .schemas import (
    GatewayGPUStatusResponse,
    GPUTierStatus,
    JobCancelResponse,
    JobOutputsResponse,
    JobStatusResponse,
    JobSubmissionParams,
    JobSubmitResponse,
    SupportedModel,
    TaskProgressInfo,
)
from .staging import (
    BundleValidationError,
    compute_canonical_input_sha256,
    extract_and_validate_assets_tar,
    rewrite_and_validate_input_references,
    stage_job_inputs_to_gcs,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["foldrun-gateway"])

MAX_PRIMARY_INPUT_BYTES = 16 * 1024 * 1024  # 16 MiB
MAX_ASSETS_ARCHIVE_BYTES = 256 * 1024 * 1024  # 256 MiB
_UPLOAD_READ_CHUNK_BYTES = 1024 * 1024  # 1 MiB

_BINDCRAFT_BUILTIN_PRESETS = {
    "default_filters.json",
    "peptide_filters.json",
    "relaxed_filters.json",
    "peptide_relaxed_filters.json",
    "no_filters.json",
    "default_4stage_multimer.json",
    "default_4stage_multimer_mpnn.json",
    "default_4stage_multimer_hardtarget.json",
    "default_4stage_multimer_flexible.json",
    "beta_sheet_4stage_multimer.json",
    "peptide_3stage_multimer.json",
    "peptide_3stage_multimer_mpnn.json",
}

_MODEL_MIN_VRAM_GB: dict[str, int] = {
    "alphafold2": 24,
    "boltz": 24,
    "boltz2": 24,
    "bindcraft": 24,
    "boltzgen": 40,
    "openfold3": 40,
    "rfd4_proteina": 40,
    "rfd4-proteina": 40,
    "alphafold3": 80,
    "af3": 80,
}

_MODEL_TO_LABEL_TYPE = {
    "boltz": "boltz2",
    "openfold3": "openfold3",
    "alphafold2": "alphafold2",
    "boltzgen": "boltzgen",
    "rfd4_proteina": "rfd4-proteina",
    "bindcraft": "bindcraft",
    "alphafold3": "alphafold3",
    "af3": "alphafold3",
}

_OPTIONAL_BINDER_PACKAGES: dict[str, tuple[str, str, str, str]] = {
    "boltzgen": (
        "boltzgen",
        "BOLTZGENConfig",
        "BOLTZGENSubmitDesignTool",
        "BOLTZGENJobAnalysisTool",
    ),
    "rfd4_proteina": (
        "rfd4_proteina",
        "RFD4ProteinaConfig",
        "RFD4ProteinaSubmitDesignTool",
        "RFD4ProteinaJobAnalysisTool",
    ),
    "rfd4-proteina": (
        "rfd4_proteina",
        "RFD4ProteinaConfig",
        "RFD4ProteinaSubmitDesignTool",
        "RFD4ProteinaJobAnalysisTool",
    ),
    "bindcraft": (
        "bindcraft",
        "BINDCRAFTConfig",
        "BINDCRAFTSubmitDesignTool",
        "BINDCRAFTJobAnalysisTool",
    ),
}


def _load_optional_binder_symbol(model: str, submod: str, symbol: str) -> Any | None:
    entry = _OPTIONAL_BINDER_PACKAGES.get(model)
    if entry is None:
        return None
    pkg_name = entry[0]
    full_pkg = f"foldrun_app.models.{pkg_name}"
    if importlib.util.find_spec(full_pkg) is None:
        return None
    mod = importlib.import_module(f"{full_pkg}.{submod}")
    return getattr(mod, symbol)


def _get_optional_binder_config(model: str) -> Any:
    entry = _OPTIONAL_BINDER_PACKAGES.get(model)
    if entry is None:
        raise HTTPException(status_code=501, detail=f"Unsupported optional model '{model}'.")
    cfg_cls = _load_optional_binder_symbol(model, "config", entry[1])
    if cfg_cls is None:
        raise HTTPException(
            status_code=501,
            detail=(
                f"Model '{model}' requires the generative binder models package, "
                "which is not installed."
            ),
        )
    return cfg_cls()


def _get_optional_binder_submit_tool(model: str, cfg: Any) -> Any:
    entry = _OPTIONAL_BINDER_PACKAGES.get(model)
    if entry is None:
        raise HTTPException(status_code=501, detail=f"Unsupported optional model '{model}'.")
    tool_cls = _load_optional_binder_symbol(model, "tools.submit_prediction", entry[2])
    if tool_cls is None:
        raise HTTPException(
            status_code=501,
            detail=(
                f"Model '{model}' requires the generative binder models package, "
                "which is not installed."
            ),
        )
    return tool_cls({"name": f"{entry[0]}_submit"}, config=cfg)


def _get_optional_binder_analysis_tool(model_type: str) -> Any | None:
    entry = _OPTIONAL_BINDER_PACKAGES.get(model_type)
    if entry is None:
        return None
    cfg_cls = _load_optional_binder_symbol(model_type, "config", entry[1])
    tool_cls = _load_optional_binder_symbol(model_type, "tools.analyze_job", entry[3])
    if cfg_cls is None or tool_cls is None:
        return None
    return tool_cls({"name": f"{entry[0]}_analyze"}, cfg_cls())


async def _read_upload_bounded(upload: UploadFile, max_bytes: int, field_name: str) -> bytes:
    """Read an UploadFile in chunks and abort with HTTP 413 if it exceeds max_bytes."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Uploaded '{field_name}' exceeds maximum size of {max_bytes} bytes.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _build_viewer_url(job_id: str) -> str | None:
    viewer_base = os.environ.get("FOLDRUN_VIEWER_URL", "").rstrip("/")
    if not viewer_base:
        return None
    short_id = job_id.split("/")[-1]
    return f"{viewer_base}/job/{short_id}"


def _find_deduplicated_job(
    project_id: str,
    region: str,
    model_label: str,
    canonical_sha256: str,
) -> Any | None:
    """Return an active or succeeded PipelineJob matching input_sha256 if one exists."""
    sha_label = canonical_sha256[:63]
    filter_str = f'labels.model_type="{model_label}" AND labels.input_sha256="{sha_label}"'
    try:
        jobs = list_pipeline_jobs(
            project_id=project_id, region=region, filter_str=filter_str, page_size=10
        )
    except Exception as exc:
        logger.warning(f"Deduplication lookup failed (proceeding with new submission): {exc}")
        return None

    for job in jobs:
        state_name = getattr(getattr(job, "state", None), "name", "") or str(
            getattr(job, "state", "")
        )
        if state_name in ("PIPELINE_STATE_SUCCEEDED", "SUCCEEDED"):
            return job
    for job in jobs:
        state_name = getattr(getattr(job, "state", None), "name", "") or str(
            getattr(job, "state", "")
        )
        if state_name in (
            "PIPELINE_STATE_PENDING",
            "PIPELINE_STATE_RUNNING",
            "PENDING",
            "RUNNING",
        ):
            return job
    return None


def _inspect_custom_jobs(custom_job_names: list[str], region: str) -> dict[str, dict[str, Any]]:
    """Inspect child CustomJobs in parallel to retrieve actual states (e.g.

    JOB_STATE_PENDING), accelerator types, scheduling strategies, and queue wait times.
    """
    if not custom_job_names:
        return {}

    results: dict[str, dict[str, Any]] = {}

    def _fetch_one(cname: str) -> tuple[str, dict[str, Any] | None]:
        try:
            norm_name = cname
            if "customJobs/" in norm_name:
                idx = norm_name.find("projects/")
                if idx != -1:
                    norm_name = norm_name[idx:]
            client = aiplatform_v1.JobServiceClient(
                client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"}
            )
            cjob = client.get_custom_job(name=norm_name)
            state_val = getattr(getattr(cjob, "state", None), "name", None) or str(
                getattr(cjob, "state", "UNKNOWN")
            )

            # Scheduling strategy
            strategy = None
            if hasattr(cjob, "job_spec") and cjob.job_spec:
                if hasattr(cjob.job_spec, "scheduling") and cjob.job_spec.scheduling:
                    s_enum = getattr(cjob.job_spec.scheduling, "strategy", None)
                    if s_enum:
                        strategy = getattr(s_enum, "name", str(s_enum))

            # Accelerator type & machine type
            accel_type = None
            machine_type = None
            if hasattr(cjob, "job_spec") and cjob.job_spec and cjob.job_spec.worker_pool_specs:
                wp0 = cjob.job_spec.worker_pool_specs[0]
                if hasattr(wp0, "machine_spec") and wp0.machine_spec:
                    m_accel = getattr(wp0.machine_spec, "accelerator_type", None)
                    if m_accel:
                        accel_type = getattr(m_accel, "name", str(m_accel))
                    machine_type = getattr(wp0.machine_spec, "machine_type", None)

            # Timestamps & queue wait
            now = datetime.now(timezone.utc)
            create_dt = None
            if hasattr(cjob, "create_time") and cjob.create_time:
                c_t = cjob.create_time
                if isinstance(c_t, datetime):
                    create_dt = c_t if c_t.tzinfo else c_t.replace(tzinfo=timezone.utc)
                elif hasattr(c_t, "ToDatetime"):
                    create_dt = c_t.ToDatetime().replace(tzinfo=timezone.utc)
                elif hasattr(c_t, "timestamp"):
                    create_dt = datetime.fromtimestamp(c_t.timestamp(), tz=timezone.utc)

            start_dt = None
            if hasattr(cjob, "start_time") and cjob.start_time:
                s_t = cjob.start_time
                if isinstance(s_t, datetime):
                    start_dt = s_t if s_t.tzinfo else s_t.replace(tzinfo=timezone.utc)
                elif hasattr(s_t, "ToDatetime"):
                    start_dt = s_t.ToDatetime().replace(tzinfo=timezone.utc)
                elif hasattr(s_t, "timestamp"):
                    start_dt = datetime.fromtimestamp(s_t.timestamp(), tz=timezone.utc)

            if start_dt and start_dt.year <= 1970:
                start_dt = None

            queue_wait_sec = None
            if create_dt and create_dt.year > 1970:
                if state_val in ("JOB_STATE_PENDING", "PENDING", "JOB_STATE_QUEUED"):
                    queue_wait_sec = max(0.0, (now - create_dt).total_seconds())
                elif start_dt:
                    queue_wait_sec = max(0.0, (start_dt - create_dt).total_seconds())

            return cname, {
                "state": state_val,
                "scheduling_strategy": strategy,
                "accelerator_type": accel_type,
                "machine_type": machine_type,
                "create_time": create_dt.isoformat() if create_dt else None,
                "start_time": start_dt.isoformat() if start_dt else None,
                "queue_wait_sec": queue_wait_sec,
            }
        except Exception as exc:
            logger.debug(f"Failed to inspect CustomJob '{cname}': {exc}")
            return cname, None

    max_workers = min(len(custom_job_names), 8)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_fetch_one, name) for name in set(custom_job_names)]
        for fut in as_completed(futures):
            cname, res = fut.result()
            if res is not None:
                results[cname] = res

    return results


def _cancel_pipeline_and_custom_jobs(job: Any, region: str) -> tuple[list[str], list[str]]:
    """Cancel a PipelineJob and active child CustomJobs, returning (cancelled, failed) names."""
    cancelled: list[str] = []
    failed: list[str] = []
    job_name = getattr(job, "name", str(job))

    # 1. Cancel the pipeline job
    try:
        p_client = vertex_ai.PipelineServiceClient(
            client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"}
        )
        p_client.cancel_pipeline_job(name=job_name)
        cancelled.append(job_name)
        logger.info(f"Initiated cancellation for PipelineJob {job_name}")
    except Exception as exc:
        logger.warning(f"Error cancelling PipelineJob {job_name}: {exc}")
        failed.append(job_name)

    # 2. Extract child CustomJobs
    custom_job_names: list[str] = []
    try:
        details = get_task_details(job)
        for t in details.get("task_list") or list(details.get("tasks", {}).values()):
            cname = t.get("custom_job_name")
            if cname:
                norm = cname
                if "customJobs/" in norm:
                    idx = norm.find("projects/")
                    if idx != -1:
                        norm = norm[idx:]
                custom_job_names.append(norm)
    except Exception as exc:
        logger.debug(f"Could not retrieve task details for child custom jobs: {exc}")

    # 3. Cancel child CustomJobs
    if custom_job_names:
        j_client = aiplatform_v1.JobServiceClient(
            client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"}
        )
        for cname in sorted(set(custom_job_names)):
            try:
                j_client.cancel_custom_job(name=cname)
                cancelled.append(cname)
                logger.info(f"Cancelled child CustomJob {cname}")
            except Exception as exc:
                err_str = str(exc)
                # Terminal child jobs (SUCCEEDED/CANCELLED/FAILED) return FailedPrecondition; don't mark failed
                if "FAILED_PRECONDITION" in err_str or "already in state" in err_str.lower():
                    logger.debug(f"Child CustomJob {cname} already terminal during cancel: {exc}")
                else:
                    logger.warning(f"Failed cancelling child CustomJob {cname}: {exc}")
                    failed.append(cname)

    return cancelled, failed


def _summarize_current_step(
    state: str,
    tasks: list[TaskProgressInfo],
    execution_phase: str = "UNKNOWN",
    queued_gpu_tasks: int = 0,
    running_gpu_tasks: int = 0,
    completed_gpu_tasks: int = 0,
    total_gpu_tasks: int = 0,
    scheduling_strategy: str | None = None,
    accelerator_type: str | None = None,
    max_queue_wait_sec: float | None = None,
) -> str:
    if state in ("PIPELINE_STATE_SUCCEEDED", "SUCCEEDED"):
        return "Completed"
    if state in ("PIPELINE_STATE_FAILED", "FAILED"):
        return "Failed"
    if state in ("PIPELINE_STATE_CANCELLED", "CANCELLED"):
        return "Cancelled"
    if state in ("PIPELINE_STATE_CANCELLING", "CANCELLING"):
        return "Cancelling"
    if not tasks:
        return "Queued / Initializing Pipeline"

    if execution_phase == "QUEUED_GPU":
        strat = scheduling_strategy or "FLEX_START"
        accel = accelerator_type or "GPU"
        wait_s = int(max_queue_wait_sec or 0)
        return (
            f"Waiting for Dynamic Workload Scheduler capacity ({strat} on {accel}): "
            f"{queued_gpu_tasks} task(s) queued ({wait_s}s elapsed)"
        )

    if execution_phase == "RUNNING_GPU":
        accel = accelerator_type or "GPU"
        return (
            f"Executing GPU tasks ({accel}): "
            f"{running_gpu_tasks} running, {completed_gpu_tasks}/{total_gpu_tasks} completed"
        )

    running = [t.task_name for t in tasks if "RUNNING" in t.state]
    completed = [t for t in tasks if "SUCCEEDED" in t.state]
    if running:
        clean_running = [
            r for r in running if not r.startswith("for-loop-") and not r.startswith("condition-")
        ]
        display_running = list(dict.fromkeys(clean_running or running))
        return (
            f"Running {', '.join(display_running)} ({len(completed)}/{len(tasks)} tasks finished)"
        )
    return f"{state} ({len(completed)}/{len(tasks)} tasks finished)"


def _trigger_analysis_if_needed_async(job: Any, model_type: str) -> None:
    """Idempotently trigger foldrun-analysis-job in a background thread when a job succeeds."""
    if model_type in ("alphafold3", "af3"):
        # Upstream AlphaFold 3 KFP Stage 4 runs CPU analysis inline inside the pipeline.
        return

    def _worker() -> None:
        try:
            pipeline_root = (
                job.runtime_config.gcs_output_directory
                if hasattr(job, "runtime_config") and job.runtime_config
                else ""
            )
            if not pipeline_root or not pipeline_root.startswith("gs://"):
                return
            if not pipeline_root.endswith("/"):
                pipeline_root += "/"

            if model_type == "boltz2":
                tool: Any | None = BOLTZ2JobAnalysisTool({"name": "boltz2_analyze"}, BOLTZ2Config())
            elif model_type == "openfold3":
                tool = OF3JobAnalysisTool({"name": "of3_analyze"}, OF3Config())
            elif model_type in _OPTIONAL_BINDER_PACKAGES:
                tool = _get_optional_binder_analysis_tool(model_type)
            else:
                tool = AF2JobAnalysisTool({"name": "af2_analyze"}, AF2Config())

            if tool is None:
                return

            bucket_name, prefix = _parse_gcs_uri(f"{pipeline_root}analysis/")
            bucket = tool.storage_client.bucket(bucket_name)
            if bucket.blob(f"{prefix}summary.json").exists():
                return
            meta_blob = bucket.blob(f"{prefix}analysis_metadata.json")
            if meta_blob.exists():
                try:
                    meta = json.loads(meta_blob.download_as_text())
                    if meta.get("status") == "running":
                        return
                except Exception:
                    pass

            short_id = job.name.split("/")[-1]
            tool.run({"job_id": short_id, "overwrite": False})
        except Exception as exc:
            logger.warning(f"Background analysis trigger skipped/failed: {exc}")

    threading.Thread(target=_worker, daemon=True).start()


def _resolve_bindcraft_extra_cli_args(
    extra_cli_args: list[str],
    assets_map: dict[str, bytes],
    bucket_name: str,
    job_id: str,
) -> list[str]:
    """Validate and rewrite BindCraft --filters/--advanced flags to staged GCS URIs or presets."""
    resolved: list[str] = []
    for arg in extra_cli_args:
        if arg.startswith("--filters=") or arg.startswith("--advanced="):
            flag_prefix, raw_val = arg.split("=", 1)
            rel = raw_val.strip().replace("\\", "/")
            while rel.startswith("./"):
                rel = rel[2:]
            if rel in assets_map:
                resolved.append(
                    f"{flag_prefix}=gs://{bucket_name}/gateway_jobs/{job_id}/inputs/assets/{rel}"
                )
            elif "/" not in rel and ".." not in rel and rel in _BINDCRAFT_BUILTIN_PRESETS:
                resolved.append(f"{flag_prefix}={rel}")
            else:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"BindCraft {flag_prefix} value '{raw_val}' is neither a built-in preset "
                        f"nor included in assets_bundle."
                    ),
                )
        else:
            resolved.append(arg)
    return resolved


def _submit_model_job_sync(
    *,
    model: SupportedModel,
    primary_bytes: bytes,
    primary_text: str,
    orig_filename: str,
    parsed_params: JobSubmissionParams,
    validated_flags: Any,
    assets_map: dict[str, bytes],
) -> JSONResponse:
    """Synchronous worker for submit_model_job executed off the asyncio event loop."""
    canonical_sha256 = compute_canonical_input_sha256(
        model=model,
        primary_file_bytes=primary_bytes,
        assets_map=assets_map,
        normalized_flags=validated_flags.normalized_flags,
    )

    if model == "boltz":
        cfg: Any = BOLTZ2Config()
    elif model == "openfold3":
        cfg = OF3Config()
    elif model in ("boltzgen", "rfd4_proteina", "bindcraft"):
        cfg = _get_optional_binder_config(model)
    elif model in ("alphafold3", "af3"):
        try:
            cfg = AF3Config()
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    else:
        cfg = AF2Config()

    model_label = _MODEL_TO_LABEL_TYPE[model]
    if parsed_params.deduplicate or parsed_params.override:
        existing_job = _find_deduplicated_job(
            project_id=cfg.project_id,
            region=cfg.region,
            model_label=model_label,
            canonical_sha256=canonical_sha256,
        )
        if existing_job is not None:
            short_id = existing_job.name.split("/")[-1]
            state_name = getattr(getattr(existing_job, "state", None), "name", "") or str(
                getattr(existing_job, "state", "PIPELINE_STATE_RUNNING")
            )
            is_active = state_name in (
                "PIPELINE_STATE_PENDING",
                "PIPELINE_STATE_RUNNING",
                "PENDING",
                "RUNNING",
            )
            existing_labels = dict(getattr(existing_job, "labels", None) or {})
            existing_gpu = existing_labels.get("gpu_type", "").lower().replace("_", "-")
            requested_gpu = parsed_params.gpu_type.lower().replace("_", "-")
            existing_flex = existing_labels.get("flex_start")
            requested_flex = "true" if parsed_params.enable_flex_start else "false"

            hardware_mismatch = bool(
                existing_gpu and requested_gpu != "auto" and existing_gpu != requested_gpu
            )
            scheduling_mismatch = bool(
                existing_flex is not None and existing_flex != requested_flex
            )
            stale_flex_queue = False
            has_running_gpu_task = False

            if is_active and (scheduling_mismatch or existing_flex == "true"):
                try:
                    existing_details = get_task_details(existing_job)
                    existing_tasks = existing_details.get("task_list")
                    if existing_tasks is None:
                        existing_tasks = list((existing_details.get("tasks") or {}).values())
                    cnames = [
                        t.get("custom_job_name") for t in existing_tasks if t.get("custom_job_name")
                    ]
                    if cnames:
                        cinfos = _inspect_custom_jobs(cnames, cfg.region)
                        for ci in cinfos.values():
                            if ci.get("state") in ("JOB_STATE_RUNNING", "RUNNING"):
                                has_running_gpu_task = True
                            if (
                                existing_flex == "true"
                                and not scheduling_mismatch
                                and parsed_params.fallback_to_ondemand
                                and ci.get("state")
                                in ("JOB_STATE_PENDING", "PENDING", "JOB_STATE_QUEUED")
                                and (ci.get("queue_wait_sec") or 0.0) >= 180.0
                            ):
                                stale_flex_queue = True
                                parsed_params.enable_flex_start = False
                    else:
                        for t in existing_tasks:
                            tname_lower = str(t.get("task_name", "")).lower()
                            tstate = str(t.get("state", ""))
                            if "predict" in tname_lower and "RUNNING" in tstate:
                                has_running_gpu_task = True
                except Exception as exc:
                    logger.warning(
                        f"Could not inspect child tasks for existing job '{short_id}': {exc}"
                    )

            # Never auto-cancel an already-RUNNING GPU job solely due to scheduling_mismatch
            # unless the caller explicitly passed override=True.
            effective_scheduling_mismatch = scheduling_mismatch and not has_running_gpu_task
            is_superseded = parsed_params.override or (
                is_active
                and (hardware_mismatch or effective_scheduling_mismatch or stale_flex_queue)
            )

            if is_superseded and is_active:
                logger.info(
                    f"Auto-cancelling superseded active job '{short_id}' "
                    f"(override={parsed_params.override}, hardware_mismatch={hardware_mismatch}, "
                    f"scheduling_mismatch={effective_scheduling_mismatch}, "
                    f"stale_flex_queue={stale_flex_queue})"
                )
                try:
                    _cancel_pipeline_and_custom_jobs(existing_job, cfg.region)
                except Exception as exc:
                    logger.warning(f"Error auto-cancelling superseded job '{short_id}': {exc}")
            elif (
                parsed_params.deduplicate
                and not parsed_params.override
                and (state_name in ("PIPELINE_STATE_SUCCEEDED", "SUCCEEDED") or is_active)
            ):
                resp = JobSubmitResponse(
                    job_id=short_id,
                    pipeline_job_resource_name=existing_job.name,
                    model=model,
                    state=state_name,
                    input_sha256=canonical_sha256,
                    viewer_url=_build_viewer_url(short_id),
                    deduplicated=True,
                )
                return JSONResponse(status_code=202, content=resp.model_dump())

    # Generate viewer-compatible job_id ending in 14 digits YYYYMMDDHHMMSS
    # Vertex AI Pipelines job_id must match [a-z][-a-z0-9]{0,127} (no underscores)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ts_digits = ts.replace("_", "")
    nonce = uuid.uuid4().hex[:4]
    model_slug = model.replace("_", "-")
    job_id = f"{model_slug}-gw-{canonical_sha256[:4]}-{nonce}-{ts_digits}"
    pipeline_root = f"gs://{cfg.bucket_name}/pipeline_runs/{ts}_{nonce}"

    try:
        rewritten_input_text = rewrite_and_validate_input_references(
            model=model,
            input_text=primary_text,
            assets_map=assets_map,
            bucket_name=cfg.bucket_name,
            job_id=job_id,
        )
    except BundleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    effective_dws_max_wait_hours = (
        parsed_params.dws_max_wait_hours if parsed_params.dws_max_wait_hours is not None else 1
    )

    extra_labels = {
        "submitted_via": "foldrun-gateway",
        "input_sha256": canonical_sha256[:63],
        "flex_start": "true" if parsed_params.enable_flex_start else "false",
    }
    if parsed_params.gpu_type.lower() != "auto":
        extra_labels["gpu_type"] = parsed_params.gpu_type.lower().replace("_", "-")

    if model == "boltz":
        tool: Any = BOLTZ2SubmitPredictionTool({"name": "boltz2_submit"}, config=cfg)
        staged = stage_job_inputs_to_gcs(
            storage_client=tool.storage_client,
            bucket_name=cfg.bucket_name,
            job_id=job_id,
            model=model,
            input_filename=orig_filename,
            rewritten_input_text=rewritten_input_text,
            assets_map=assets_map,
            canonical_sha256=canonical_sha256,
            cli_args=parsed_params.cli_args,
            pipeline_root=pipeline_root,
            normalized_flags=validated_flags.normalized_flags,
        )
        # Store job-scoped Boltz-2 query copy so concurrent jobs never clobber each other
        extra_labels["query_yaml_key"] = job_id[:63]
        tool.storage_client.bucket(cfg.bucket_name).blob(
            f"boltz2_queries/{job_id}.yaml"
        ).upload_from_string(rewritten_input_text, content_type="text/plain; charset=utf-8")

        submit_args: dict[str, Any] = {
            "input": rewritten_input_text,
            "job_name": job_id,
            "query_name": staged.input_stem,
            "gpu_type": parsed_params.gpu_type,
            "enable_flex_start": parsed_params.enable_flex_start,
            "dws_max_wait_hours": effective_dws_max_wait_hours,
            "allow_local_files": False,
            "extra_cli_args": validated_flags.extra_cli_args,
            "custom_job_id": job_id,
            "pipeline_root": pipeline_root,
            "extra_labels": extra_labels,
            **validated_flags.pipeline_params,
        }
        try:
            result = tool.run(submit_args)
        except (ValueError, BundleValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    elif model == "openfold3":
        tool = OF3SubmitPredictionTool({"name": "of3_submit"}, config=cfg)
        staged = stage_job_inputs_to_gcs(
            storage_client=tool.storage_client,
            bucket_name=cfg.bucket_name,
            job_id=job_id,
            model=model,
            input_filename=orig_filename,
            rewritten_input_text=rewritten_input_text,
            assets_map=assets_map,
            canonical_sha256=canonical_sha256,
            cli_args=parsed_params.cli_args,
            pipeline_root=pipeline_root,
            normalized_flags=validated_flags.normalized_flags,
        )
        submit_args = {
            "input": rewritten_input_text,
            "job_name": job_id,
            "query_name": staged.input_stem,
            "gpu_type": parsed_params.gpu_type,
            "enable_flex_start": parsed_params.enable_flex_start,
            "dws_max_wait_hours": effective_dws_max_wait_hours,
            "allow_local_files": False,
            "extra_cli_args": validated_flags.extra_cli_args,
            "custom_runner_yaml_overrides": validated_flags.runner_yaml_overrides,
            "custom_job_id": job_id,
            "pipeline_root": pipeline_root,
            "extra_labels": extra_labels,
            **validated_flags.pipeline_params,
        }
        try:
            result = tool.run(submit_args)
        except (ValueError, BundleValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    elif model in ("boltzgen", "rfd4_proteina", "bindcraft"):
        extra_cli_for_submit = validated_flags.extra_cli_args
        tool = _get_optional_binder_submit_tool(model, cfg)
        if model == "bindcraft":
            extra_cli_for_submit = _resolve_bindcraft_extra_cli_args(
                validated_flags.extra_cli_args,
                assets_map=assets_map,
                bucket_name=cfg.bucket_name,
                job_id=job_id,
            )

        staged = stage_job_inputs_to_gcs(
            storage_client=tool.storage_client,
            bucket_name=cfg.bucket_name,
            job_id=job_id,
            model=model,
            input_filename=orig_filename,
            rewritten_input_text=rewritten_input_text,
            assets_map=assets_map,
            canonical_sha256=canonical_sha256,
            cli_args=parsed_params.cli_args,
            pipeline_root=pipeline_root,
            normalized_flags=validated_flags.normalized_flags,
        )
        submit_args = {
            "input": rewritten_input_text,
            "job_name": staged.input_stem,
            "query_name": staged.input_stem,
            "gpu_type": parsed_params.gpu_type,
            "enable_flex_start": parsed_params.enable_flex_start,
            "dws_max_wait_hours": effective_dws_max_wait_hours,
            "allow_local_files": False,
            "extra_cli_args": extra_cli_for_submit,
            "custom_job_id": job_id,
            "pipeline_root": pipeline_root,
            "extra_labels": extra_labels,
            **validated_flags.pipeline_params,
        }
        try:
            result = tool.run(submit_args)
        except (ValueError, BundleValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    elif model in ("alphafold3", "af3"):
        tool = AF3SubmitPredictionTool({"name": "af3_submit"}, config=cfg)
        staged = stage_job_inputs_to_gcs(
            storage_client=tool.storage_client,
            bucket_name=cfg.bucket_name,
            job_id=job_id,
            model=model,
            input_filename=orig_filename,
            rewritten_input_text=rewritten_input_text,
            assets_map=assets_map,
            canonical_sha256=canonical_sha256,
            cli_args=parsed_params.cli_args,
            pipeline_root=pipeline_root,
            normalized_flags=validated_flags.normalized_flags,
        )
        run_data_pipeline = bool(validated_flags.pipeline_params.get("run_data_pipeline", True))
        explicit_seed_flag = any(
            tok == f or tok.startswith(f"{f}=")
            for tok in parsed_params.cli_args
            for f in ("--model_seeds", "--num_seeds", "--num_model_seeds", "--seed", "--base_seed")
        )
        af3_model_seeds: list[int] | None = None
        if "model_seeds" in validated_flags.pipeline_params:
            af3_model_seeds = list(validated_flags.pipeline_params["model_seeds"])
        elif explicit_seed_flag:
            base_s = int(validated_flags.pipeline_params.get("base_seed", 1))
            n_s = max(1, int(validated_flags.pipeline_params.get("num_model_seeds", 1)))
            af3_model_seeds = [base_s + i for i in range(n_s)]

        submit_args = {
            "input": rewritten_input_text,
            "job_name": job_id,
            "msa_free": not run_data_pipeline,
            "num_diffusion_samples": int(
                validated_flags.pipeline_params.get("num_diffusion_samples", 5)
            ),
            "sync": False,
        }
        if af3_model_seeds is not None:
            submit_args["model_seeds"] = af3_model_seeds

        try:
            result = tool.run(submit_args)
        except (ValueError, BundleValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    else:
        # AlphaFold 2: route to monomer or multimer tool based on model_preset / chain count
        try:
            sequences = parse_fasta_content(rewritten_input_text)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid FASTA input: {exc}") from exc

        preset = validated_flags.pipeline_params.get("model_preset", "monomer")
        is_multimer = preset == "multimer" or len(sequences) > 1
        if is_multimer and len(sequences) <= 1:
            raise HTTPException(
                status_code=400,
                detail="model_preset=multimer requires a FASTA file with at least 2 chains.",
            )
        if not is_multimer and len(sequences) > 1:
            raise HTTPException(
                status_code=400,
                detail="Multi-chain FASTA requires --model_preset=multimer.",
            )

        tool = (
            AF2SubmitMultimerTool({"name": "af2_multimer"}, config=cfg)
            if is_multimer
            else AF2SubmitMonomerTool({"name": "af2_monomer"}, config=cfg)
        )
        staged = stage_job_inputs_to_gcs(
            storage_client=tool.storage_client,
            bucket_name=cfg.bucket_name,
            job_id=job_id,
            model=model,
            input_filename=orig_filename,
            rewritten_input_text=rewritten_input_text,
            assets_map=assets_map,
            canonical_sha256=canonical_sha256,
            cli_args=parsed_params.cli_args,
            pipeline_root=pipeline_root,
            normalized_flags=validated_flags.normalized_flags,
        )
        submit_args = {
            "sequence": rewritten_input_text,
            "job_name": job_id,
            "gpu_type": parsed_params.gpu_type,
            "enable_flex_start": parsed_params.enable_flex_start,
            "dws_max_wait_hours": effective_dws_max_wait_hours,
            "allow_local_files": False,
            "max_template_date": validated_flags.pipeline_params["max_template_date"],
            "use_small_bfd": validated_flags.pipeline_params["use_small_bfd"],
            "run_relaxation": validated_flags.pipeline_params["run_relaxation"],
            "msa_method": validated_flags.pipeline_params["msa_method"],
            "model_preset": preset,
            "num_predictions_per_model": validated_flags.pipeline_params[
                "num_predictions_per_model"
            ],
            "custom_job_id": job_id,
            "pipeline_root": pipeline_root,
            "extra_labels": extra_labels,
        }
        try:
            result = tool.run(submit_args)
        except (ValueError, BundleValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message", "Job submission failed"))

    resource_name = str(result.get("pipeline_job_resource") or result.get("job_id", job_id))
    short_job_id = resource_name.split("/")[-1]
    resolved_pipeline_root = str(result.get("pipeline_root") or pipeline_root)
    resolved_gcs_output_dir = str(result.get("gcs_output_dir") or "")

    if short_job_id != job_id or resolved_gcs_output_dir or resolved_pipeline_root != pipeline_root:
        updated_manifest = {
            "job_id": short_job_id,
            "requested_job_id": job_id,
            "model": model,
            "input_filename": orig_filename,
            "input_stem": staged.input_stem,
            "input_sha256": canonical_sha256,
            "cli_args": parsed_params.cli_args,
            "normalized_flags": validated_flags.normalized_flags,
            "pipeline_root": resolved_pipeline_root,
            "gcs_output_dir": resolved_gcs_output_dir,
            "asset_paths": sorted(assets_map.keys()),
        }
        try:
            tool.storage_client.bucket(cfg.bucket_name).blob(
                f"gateway_jobs/{short_job_id}/manifest.json"
            ).upload_from_string(
                json.dumps(updated_manifest, indent=2, sort_keys=True),
                content_type="application/json",
            )
        except Exception as exc:
            logger.warning(f"Failed to persist updated manifest for '{short_job_id}': {exc}")

    resp = JobSubmitResponse(
        job_id=short_job_id,
        pipeline_job_resource_name=resource_name,
        model=model,
        state="PIPELINE_STATE_PENDING",
        input_sha256=canonical_sha256,
        viewer_url=_build_viewer_url(short_job_id),
        deduplicated=False,
    )
    return JSONResponse(status_code=202, content=resp.model_dump())


@router.post("/{model}/jobs", response_model=JobSubmitResponse, status_code=202)
async def submit_model_job(
    model: SupportedModel,
    input_file: Annotated[UploadFile, File()],
    input_filename: Annotated[str | None, Form()] = None,
    params: Annotated[str, Form()] = "{}",
    assets_bundle: Annotated[UploadFile | None, File()] = None,
) -> JSONResponse:
    """Validate native CLI flags & assets, stage inputs to GCS, and submit Vertex AI PipelineJob."""
    try:
        parsed_params = JobSubmissionParams.model_validate_json(params)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid params JSON: {exc}") from exc

    try:
        validated_flags = validate_and_partition_flags(
            model=model,
            cli_args=parsed_params.cli_args,
            runner_yaml_overrides=parsed_params.runner_yaml_overrides,
        )
    except FlagValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    primary_bytes = await _read_upload_bounded(input_file, MAX_PRIMARY_INPUT_BYTES, "input_file")
    if not primary_bytes:
        raise HTTPException(status_code=400, detail="Uploaded input_file is empty")
    try:
        primary_text = primary_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="input_file must be UTF-8 text") from exc

    assets_bytes = (
        await _read_upload_bounded(assets_bundle, MAX_ASSETS_ARCHIVE_BYTES, "assets_bundle")
        if assets_bundle is not None
        else None
    )
    try:
        assets_map = await run_in_threadpool(extract_and_validate_assets_tar, assets_bytes)
    except BundleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    orig_filename = input_filename or input_file.filename or f"query.{model}"
    return await run_in_threadpool(
        _submit_model_job_sync,
        model=model,
        primary_bytes=primary_bytes,
        primary_text=primary_text,
        orig_filename=orig_filename,
        parsed_params=parsed_params,
        validated_flags=validated_flags,
        assets_map=assets_map,
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_gateway_job_status(job_id: str) -> JobStatusResponse:
    """Query Vertex AI PipelineJob state and task progress, triggering analysis upon completion."""
    cfg = AF2Config()
    try:
        job = get_pipeline_job(job_id, cfg.project_id, cfg.region)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found: {exc}") from exc

    status_dict = get_job_status(job)
    details_dict = get_task_details(job)
    labels = status_dict.get("labels") or {}
    model_type = labels.get("model_type", "unknown")

    raw_tasks = details_dict.get("task_list")
    if raw_tasks is None:
        raw_tasks = list((details_dict.get("tasks") or {}).values())

    # Collect custom job names to inspect child jobs
    custom_job_names = [t.get("custom_job_name") for t in raw_tasks if t.get("custom_job_name")]
    custom_jobs_info = (
        _inspect_custom_jobs(custom_job_names, cfg.region) if custom_job_names else {}
    )

    tasks_list: list[TaskProgressInfo] = []
    queued_gpu_tasks = 0
    running_gpu_tasks = 0
    completed_gpu_tasks = 0
    total_gpu_tasks = 0
    detected_strategies: list[str] = []
    detected_accels: list[str] = []
    max_queue_wait_sec: float | None = None

    for tinfo in raw_tasks:
        tname = tinfo.get("task_name", "")
        err_obj = tinfo.get("error")
        err_msg = err_obj.get("message") if isinstance(err_obj, dict) else None

        cname = tinfo.get("custom_job_name")
        cinfo = custom_jobs_info.get(cname, {}) if cname else {}

        cj_state = cinfo.get("state")
        accel = cinfo.get("accelerator_type")
        strategy = cinfo.get("scheduling_strategy")
        q_wait = cinfo.get("queue_wait_sec")

        # Fallback to execution_metadata if cinfo didn't have accel/strategy
        exec_meta = tinfo.get("execution_metadata") or {}
        if not accel and "input:accelerator_type" in exec_meta:
            accel = exec_meta["input:accelerator_type"]
        if not strategy and "input:scheduling_strategy" in exec_meta:
            strategy = exec_meta["input:scheduling_strategy"]

        is_gpu_task = bool(accel and accel not in ("ACCELERATOR_TYPE_UNSPECIFIED", "None"))
        if is_gpu_task:
            total_gpu_tasks += 1
            if accel:
                detected_accels.append(accel)
            if strategy:
                detected_strategies.append(strategy)

            kfp_state = str(tinfo.get("state", "UNKNOWN"))
            if cj_state in ("JOB_STATE_PENDING", "PENDING", "JOB_STATE_QUEUED"):
                queued_gpu_tasks += 1
                if q_wait is not None:
                    max_queue_wait_sec = max(max_queue_wait_sec or 0.0, q_wait)
            elif cj_state in ("JOB_STATE_RUNNING", "RUNNING"):
                running_gpu_tasks += 1
            elif cj_state in ("JOB_STATE_SUCCEEDED", "SUCCEEDED") or kfp_state in (
                "SUCCEEDED",
                "PIPELINE_STATE_SUCCEEDED",
            ):
                completed_gpu_tasks += 1
            elif kfp_state in ("RUNNING", "PIPELINE_STATE_RUNNING"):
                running_gpu_tasks += 1

        tasks_list.append(
            TaskProgressInfo(
                task_name=tname,
                state=str(tinfo.get("state", "UNKNOWN")),
                start_time=tinfo.get("start_time"),
                end_time=tinfo.get("end_time"),
                error_message=err_msg,
                custom_job_id=tinfo.get("custom_job_id"),
                custom_job_state=cj_state,
                accelerator_type=accel,
                scheduling_strategy=strategy,
                queue_wait_sec=q_wait,
            )
        )

    # Resolve dominant scheduling strategy and accelerator type
    scheduling_strategy = (
        detected_strategies[0]
        if detected_strategies
        else (
            "FLEX_START"
            if labels.get("flex_start") == "true"
            else "STANDARD"
            if labels.get("flex_start") == "false"
            else None
        )
    )
    accelerator_type = (
        detected_accels[0] if detected_accels else labels.get("gpu_type", "").upper() or None
    )

    state_str = str(status_dict.get("state", "UNKNOWN"))

    # Compute execution_phase
    if state_str in ("PIPELINE_STATE_SUCCEEDED", "SUCCEEDED"):
        execution_phase = "COMPLETED"
    elif state_str in ("PIPELINE_STATE_FAILED", "FAILED"):
        execution_phase = "FAILED"
    elif state_str in ("PIPELINE_STATE_CANCELLED", "CANCELLED"):
        execution_phase = "CANCELLED"
    elif state_str in ("PIPELINE_STATE_CANCELLING", "CANCELLING"):
        execution_phase = "CANCELLING"
    elif queued_gpu_tasks > 0:
        execution_phase = "QUEUED_GPU"
    elif running_gpu_tasks > 0:
        execution_phase = "RUNNING_GPU"
    elif any("RUNNING" in t.state for t in tasks_list):
        execution_phase = "PREPARING_INPUTS"
    elif state_str in ("PIPELINE_STATE_PENDING", "PENDING"):
        execution_phase = "PENDING"
    else:
        execution_phase = state_str

    if state_str in ("PIPELINE_STATE_SUCCEEDED", "SUCCEEDED"):
        _trigger_analysis_if_needed_async(job, model_type)

    short_id = str(status_dict.get("job_id", job_id)).split("/")[-1]

    # Compute actionable queue_notice
    queue_notice = None
    if execution_phase == "QUEUED_GPU" or queued_gpu_tasks > 0:
        wait_int = int(max_queue_wait_sec or 0)
        strat_display = scheduling_strategy or "FLEX_START"
        accel_display = accelerator_type or "GPU"
        queue_notice = (
            f"Job is queued waiting for Dynamic Workload Scheduler {strat_display} capacity "
            f"on {accel_display} in {cfg.region} ({wait_int}s elapsed). "
            f"To run immediately without queueing, re-run with --no-flex-start (standard on-demand) or use --gpu_type L4. "
            f"To cancel this job, run: foldrun cancel {short_id}"
        )

    current_step = _summarize_current_step(
        state=state_str,
        tasks=tasks_list,
        execution_phase=execution_phase,
        queued_gpu_tasks=queued_gpu_tasks,
        running_gpu_tasks=running_gpu_tasks,
        completed_gpu_tasks=completed_gpu_tasks,
        total_gpu_tasks=total_gpu_tasks,
        scheduling_strategy=scheduling_strategy,
        accelerator_type=accelerator_type,
        max_queue_wait_sec=max_queue_wait_sec,
    )

    # Calculate overall progress percentage across pipeline tasks
    if state_str in ("PIPELINE_STATE_SUCCEEDED", "SUCCEEDED"):
        progress_percent = 100.0
    elif tasks_list:
        completed_count = sum(
            1 for t in tasks_list if t.state in ("SUCCEEDED", "PIPELINE_STATE_SUCCEEDED")
        )
        total_count = len(tasks_list)
        progress_percent = round(100.0 * completed_count / total_count, 1)
    else:
        progress_percent = 0.0

    return JobStatusResponse(
        job_id=short_id,
        pipeline_job_resource_name=str(status_dict.get("job_id", job_id)),
        model=model_type,
        state=state_str,
        current_step=current_step,
        execution_phase=execution_phase,
        progress_percent=progress_percent,
        queued_gpu_tasks=queued_gpu_tasks,
        running_gpu_tasks=running_gpu_tasks,
        completed_gpu_tasks=completed_gpu_tasks,
        total_gpu_tasks=total_gpu_tasks,
        scheduling_strategy=scheduling_strategy,
        accelerator_type=accelerator_type,
        max_queue_wait_sec=max_queue_wait_sec,
        queue_notice=queue_notice,
        tasks=tasks_list,
        error_message=status_dict.get("error_message"),
        viewer_url=_build_viewer_url(short_id),
        create_time=status_dict.get("create_time"),
        start_time=status_dict.get("start_time"),
        end_time=status_dict.get("end_time"),
    )


@router.post("/jobs/{job_id}/cancel", response_model=JobCancelResponse)
def cancel_gateway_job(job_id: str) -> JobCancelResponse:
    """Cancel an active or queued PipelineJob and its child CustomJobs."""
    cfg = AF2Config()
    try:
        job = get_pipeline_job(job_id, cfg.project_id, cfg.region)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found: {exc}") from exc

    cancelled_resources, failed_resources = _cancel_pipeline_and_custom_jobs(job, cfg.region)
    short_id = str(getattr(job, "name", job_id)).split("/")[-1]
    if not cancelled_resources and failed_resources:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to cancel job '{short_id}': {', '.join(failed_resources)}",
        )
    status_val = "cancelled" if not failed_resources else "partial"
    msg = f"Successfully initiated cancellation for job '{short_id}'."
    if failed_resources:
        msg += f" Warning: failed to cancel {len(failed_resources)} child resource(s)."
    return JobCancelResponse(
        status=status_val,
        job_id=short_id,
        pipeline_job_resource_name=getattr(job, "name", job_id),
        cancelled_resources=cancelled_resources,
        failed_resources=failed_resources,
        message=msg,
    )


_TIER_CONFIGS = [
    {
        "gpu_type": "L4",
        "accelerator_type": "NVIDIA_L4",
        "machine_type": "g2-standard-12",
        "vram_gb": 24,
        "on_demand_metric": "NVIDIA_L4_GPUS",
        "flex_metric": "PREEMPTIBLE_NVIDIA_L4_GPUS",
        "typical_use": "Small-medium proteins (<500 residues), fast design & inference",
    },
    {
        "gpu_type": "A100",
        "accelerator_type": "NVIDIA_TESLA_A100",
        "machine_type": "a2-highgpu-1g",
        "vram_gb": 40,
        "on_demand_metric": "NVIDIA_A100_GPUS",
        "flex_metric": "PREEMPTIBLE_NVIDIA_A100_GPUS",
        "typical_use": "Medium-large proteins (500-2000 residues), predict tasks",
    },
    {
        "gpu_type": "A100_80GB",
        "accelerator_type": "NVIDIA_A100_80GB",
        "machine_type": "a2-ultragpu-1g",
        "vram_gb": 80,
        "on_demand_metric": "NVIDIA_A100_80GB_GPUS",
        "flex_metric": "PREEMPTIBLE_NVIDIA_A100_80GB_GPUS",
        "typical_use": "Large complexes (>2000 residues), high memory workflows",
    },
    {
        "gpu_type": "H100",
        "accelerator_type": "NVIDIA_H100_80GB",
        "machine_type": "a3-highgpu-1g",
        "vram_gb": 80,
        "on_demand_metric": "NVIDIA_H100_GPUS",
        "flex_metric": "PREEMPTIBLE_NVIDIA_H100_GPUS",
        "typical_use": "Ultra-large complexes, AlphaFold 3 Dedicated Endpoints, frontier diffusion",
    },
]


@router.get("/gpus", response_model=GatewayGPUStatusResponse)
def get_gateway_gpu_status(
    model: str | None = Query(
        default=None,
        description="Optional model name to filter recommended GPU tiers by minimum VRAM floor.",
    ),
) -> GatewayGPUStatusResponse:
    """Inspect live GPU availability, active/queued CustomJob counts, and regional GPU quotas."""
    project_id = os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT") or ""
    raw_loc = os.getenv("GOOGLE_CLOUD_LOCATION")
    region = (
        os.getenv("GCP_REGION")
        or (raw_loc if raw_loc and raw_loc != "global" else None)
        or "us-central1"
    )
    try:
        cfg = AF2Config()
        project_id = cfg.project_id
        region = cfg.region
    except Exception:
        pass
    if not project_id:
        try:
            import google.auth

            _, adc_project = google.auth.default()
            project_id = adc_project or ""
        except Exception:
            pass
    if not project_id:
        raise HTTPException(
            status_code=503,
            detail="GCP_PROJECT_ID or GOOGLE_CLOUD_PROJECT environment variable is not configured.",
        )

    # 1. Query GCE / Regional GPU Quotas
    on_demand_quotas: dict[str, int] = {}
    flex_quotas: dict[str, int] = {}
    try:
        quota_res = check_gpu_quota(project_id, region)
        od_gpus = quota_res.get("on_demand_gpus", {})
        fl_gpus = quota_res.get("preemptible_gpus", {})
        for metric, data in od_gpus.items():
            on_demand_quotas[metric] = int(data.get("limit", 0))
        for metric, data in fl_gpus.items():
            flex_quotas[metric] = int(data.get("limit", 0))
    except Exception as exc:
        logger.warning(f"Could not retrieve GPU quotas for {project_id}/{region}: {exc}")

    # 2. Query active/queued CustomJobs in Vertex AI
    tier_keys = [str(c["gpu_type"]) for c in _TIER_CONFIGS]
    running_od: dict[str, int] = dict.fromkeys(tier_keys, 0)
    queued_od: dict[str, int] = dict.fromkeys(tier_keys, 0)
    running_fl: dict[str, int] = dict.fromkeys(tier_keys, 0)
    queued_fl: dict[str, int] = dict.fromkeys(tier_keys, 0)

    try:
        job_client = aiplatform_v1.JobServiceClient(
            client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"}
        )
        parent = f"projects/{project_id}/locations/{region}"
        filter_expr = (
            'state="JOB_STATE_RUNNING" OR state="JOB_STATE_PENDING" OR state="JOB_STATE_QUEUED"'
        )
        try:
            req = aiplatform_v1.ListCustomJobsRequest(parent=parent, filter=filter_expr)
            custom_jobs = list(job_client.list_custom_jobs(request=req))
        except Exception:
            req = aiplatform_v1.ListCustomJobsRequest(parent=parent, page_size=100)
            all_jobs = list(job_client.list_custom_jobs(request=req))
            custom_jobs = [
                j
                for j in all_jobs
                if getattr(getattr(j, "state", None), "name", str(getattr(j, "state", "")))
                in (
                    "JOB_STATE_RUNNING",
                    "JOB_STATE_PENDING",
                    "JOB_STATE_QUEUED",
                    "RUNNING",
                    "PENDING",
                    "QUEUED",
                )
            ]

        for cj in custom_jobs:
            state_str = getattr(getattr(cj, "state", None), "name", str(getattr(cj, "state", "")))
            is_queued = state_str in ("JOB_STATE_PENDING", "PENDING", "JOB_STATE_QUEUED", "QUEUED")
            is_running = state_str in ("JOB_STATE_RUNNING", "RUNNING")
            if not (is_queued or is_running):
                continue

            strat_str = ""
            if hasattr(cj, "job_spec") and getattr(cj.job_spec, "scheduling", None):
                strat_obj = getattr(cj.job_spec.scheduling, "strategy", None)
                strat_str = getattr(strat_obj, "name", str(strat_obj))
            is_flex = "FLEX_START" in strat_str

            worker_specs = getattr(getattr(cj, "job_spec", None), "worker_pool_specs", []) or []
            for spec in worker_specs:
                machine_spec = getattr(spec, "machine_spec", None)
                if not machine_spec:
                    continue
                accel_obj = getattr(machine_spec, "accelerator_type", None)
                accel_name = getattr(accel_obj, "name", str(accel_obj))
                accel_count = getattr(machine_spec, "accelerator_count", 1) or 1

                tier_key = None
                if "H100" in accel_name:
                    tier_key = "H100"
                elif "L4" in accel_name:
                    tier_key = "L4"
                elif "80GB" in accel_name or "80G" in accel_name:
                    tier_key = "A100_80GB"
                elif "A100" in accel_name:
                    tier_key = "A100"

                if tier_key:
                    if is_flex:
                        if is_queued:
                            queued_fl[tier_key] += accel_count
                        elif is_running:
                            running_fl[tier_key] += accel_count
                    else:
                        if is_queued:
                            queued_od[tier_key] += accel_count
                        elif is_running:
                            running_od[tier_key] += accel_count
    except Exception as exc:
        logger.warning(f"Could not list custom jobs for GPU status: {exc}")

    # 3. Query active pipeline jobs count
    active_pipeline_jobs = 0
    try:
        pjobs = list_pipeline_jobs(
            project_id=project_id,
            region=region,
            filter_str='state="PIPELINE_STATE_RUNNING" OR state="PIPELINE_STATE_PENDING"',
            page_size=50,
        )
        active_pipeline_jobs = len(pjobs)
    except Exception as exc:
        logger.warning(f"Could not list active pipeline jobs: {exc}")

    # 4. Construct tier statuses
    tiers: list[GPUTierStatus] = []
    for conf in _TIER_CONFIGS:
        gtype = str(conf["gpu_type"])
        od_q = on_demand_quotas.get(str(conf["on_demand_metric"]))
        fl_q = flex_quotas.get(str(conf["flex_metric"]))

        r_od = running_od[gtype]
        q_od = queued_od[gtype]
        r_fl = running_fl[gtype]
        q_fl = queued_fl[gtype]

        if (od_q is not None and od_q == 0) and (fl_q is not None and fl_q == 0):
            tier_status = "NO_QUOTA"
        elif q_od > 0 or q_fl > 0:
            tier_status = "QUEUED"
        elif r_od > 0 or r_fl > 0:
            if od_q and r_od >= od_q:
                tier_status = "BUSY"
            else:
                tier_status = "AVAILABLE"
        else:
            tier_status = "AVAILABLE"

        tiers.append(
            GPUTierStatus(
                gpu_type=gtype,
                accelerator_type=str(conf["accelerator_type"]),
                machine_type=str(conf["machine_type"]),
                vram_gb=int(conf["vram_gb"]),
                on_demand_quota=od_q,
                flex_start_quota=fl_q,
                running_on_demand=r_od,
                queued_on_demand=q_od,
                running_flex_start=r_fl,
                queued_flex_start=q_fl,
                status=tier_status,
                typical_use=str(conf["typical_use"]),
            )
        )

    # 5. Compute recommendation honoring per-model minimum VRAM floor when model is specified
    min_vram_gb = _MODEL_MIN_VRAM_GB.get(model.lower().strip(), 24) if model else 24
    eligible_tiers = [t for t in tiers if t.vram_gb >= min_vram_gb] or tiers

    total_queued_flex = sum(t.queued_flex_start for t in eligible_tiers)
    available_od = next(
        (t for t in eligible_tiers if t.queued_on_demand == 0 and t.status != "NO_QUOTA"),
        None,
    )
    default_tier = (
        next((t for t in eligible_tiers if t.status != "NO_QUOTA"), None) or eligible_tiers[0]
    )

    if total_queued_flex > 0:
        if available_od is not None:
            recommended_gpu_type = available_od.gpu_type
            recommended_enable_flex_start = False
            recommendation_reason = (
                f"DWS FLEX_START currently has {total_queued_flex} queued task(s). "
                f"On-demand {available_od.gpu_type} ({available_od.machine_type}) has immediate "
                f"capacity with 0 queued tasks. Recommend --gpu_type={available_od.gpu_type} "
                f"--no-flex-start for instant execution."
            )
        else:
            recommended_gpu_type = default_tier.gpu_type
            recommended_enable_flex_start = False
            recommendation_reason = (
                f"DWS FLEX_START has {total_queued_flex} queued task(s). "
                f"Recommend running with on-demand scheduling (--no-flex-start)."
            )
    else:
        recommended_gpu_type = default_tier.gpu_type
        recommended_enable_flex_start = True
        recommendation_reason = (
            f"Zero queued tasks detected across eligible GPU tiers (min VRAM >= {min_vram_gb} GB). "
            f"Recommend DWS FLEX_START (--flex-start) with {recommended_gpu_type} for cost savings."
        )

    return GatewayGPUStatusResponse(
        project_id=project_id,
        region=region,
        tiers=tiers,
        active_pipeline_jobs=active_pipeline_jobs,
        recommended_gpu_type=recommended_gpu_type,
        recommended_enable_flex_start=recommended_enable_flex_start,
        recommendation_reason=recommendation_reason,
    )


@router.get("/jobs/{job_id}/outputs")
def get_gateway_job_outputs(
    job_id: str,
    request: Request,
    include_pkl: bool = Query(default=False),
    stream: bool = Query(default=False),
) -> Any:
    """Assemble native output archive on GCS and return a 15-minute V4 Signed URL or stream."""
    cfg = AF2Config()
    try:
        job = get_pipeline_job(job_id, cfg.project_id, cfg.region)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found: {exc}") from exc

    status_dict = get_job_status(job)
    state_str = str(status_dict.get("state", "UNKNOWN"))
    if state_str not in ("PIPELINE_STATE_SUCCEEDED", "SUCCEEDED"):
        raise HTTPException(
            status_code=409,
            detail=f"Job '{job_id}' is in state '{state_str}' and outputs are not yet available.",
        )

    labels = status_dict.get("labels") or {}
    model_type = labels.get("model_type", "boltz2")
    _trigger_analysis_if_needed_async(job, model_type)

    tool = AF2SubmitMonomerTool({"name": "af2_monomer"}, config=cfg)
    short_id = str(status_dict.get("job_id", job_id)).split("/")[-1]

    archive_uri = build_or_get_native_outputs_archive(
        storage_client=tool.storage_client,
        bucket_name=cfg.bucket_name,
        job_id=short_id,
        model=model_type,
        job=job,
        include_pkl=include_pkl,
    )

    if stream:
        b_name, b_path = _parse_gcs_uri(archive_uri)
        blob = tool.storage_client.bucket(b_name).blob(b_path)

        def _iter_blob():
            with blob.open("rb", chunk_size=STREAM_CHUNK_BYTES) as reader:
                while True:
                    chunk = reader.read(STREAM_CHUNK_BYTES)
                    if not chunk:
                        break
                    yield chunk

        return StreamingResponse(
            _iter_blob(),
            media_type="application/gzip",
            headers={
                "Content-Disposition": f'attachment; filename="{short_id}_native_outputs.tar.gz"'
            },
        )

    try:
        signed_url = generate_v4_signed_download_url(
            storage_client=tool.storage_client,
            archive_gcs_uri=archive_uri,
            expiration_minutes=15,
        )
    except Exception as exc:
        logger.warning(
            f"V4 Signed URL generation failed for '{short_id}' ({exc}); "
            f"falling back to authenticated Gateway streaming endpoint."
        )
        stream_query = "stream=true" + ("&include_pkl=true" if include_pkl else "")
        signed_url = (
            f"{str(request.base_url).rstrip('/')}/v1/jobs/{short_id}/outputs?{stream_query}"
        )

    return JobOutputsResponse(
        job_id=short_id,
        model=model_type,
        state=state_str,
        archive_gcs_uri=archive_uri,
        download_url=signed_url,
        expires_in_seconds=900,
        viewer_url=_build_viewer_url(short_id),
    )
