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

"""Compatibility shims for core compilation locks and Vertex AI job inspection.

Allows the Cloud Run Gateway and model submission tools to run cleanly both before
and after PR #225 (`foldrun_app.core.compile_lock` / `model_pipeline_load_context`)
is merged into `main`.
"""

import importlib
import json
import logging
import os
import re
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import NonCallableMock

from google.cloud import aiplatform_v1 as vertex_ai

try:
    from foldrun_app.core.pipeline_utils import PIPELINE_COMPILE_LOCK
except ImportError:
    PIPELINE_COMPILE_LOCK = threading.RLock()

logger = logging.getLogger(__name__)

try:
    from foldrun_app.core.compile_lock import (
        GLOBAL_COMPILE_LOCK,
        CompileEnvContext,
        compile_env_context,
        setup_compile_env_vars,
    )
except ImportError:
    GLOBAL_COMPILE_LOCK = PIPELINE_COMPILE_LOCK

    class CompileEnvContext:
        """Re-entrant context manager that serializes compile env access and restores os.environ on exit."""

        def __init__(
            self,
            lock: threading.RLock,
            env_vars: dict[str, str],
            keys_to_clear: tuple[str, ...] = (),
            previous_env: dict[str, str | None] | None = None,
        ):
            self._lock = lock
            self._env_vars = env_vars
            self._keys_to_clear = keys_to_clear
            self._previous_env = previous_env if previous_env is not None else {}

        def __enter__(self) -> dict[str, str]:
            self._lock.acquire()
            try:
                for key in self._keys_to_clear:
                    os.environ.pop(key, None)
                os.environ.update(self._env_vars)
                return self._env_vars
            except Exception:
                self._lock.release()
                raise

        def __exit__(self, exc_type, exc_val, exc_tb) -> None:
            try:
                for key, prev_val in self._previous_env.items():
                    if prev_val is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = prev_val
            finally:
                self._lock.release()

    def setup_compile_env_vars(
        env_vars: dict[str, str],
        keys_to_clear: tuple[str, ...] = (),
        lock: threading.RLock = GLOBAL_COMPILE_LOCK,
    ) -> CompileEnvContext:
        """Apply env_vars under lock immediately and return a CompileEnvContext for `with` restoration."""
        with lock:
            tracked_keys = set(env_vars.keys()) | set(keys_to_clear)
            previous_env = {key: os.environ.get(key) for key in tracked_keys}
            for key in keys_to_clear:
                os.environ.pop(key, None)
            os.environ.update(env_vars)
        return CompileEnvContext(
            lock=lock,
            env_vars=env_vars,
            keys_to_clear=keys_to_clear,
            previous_env=previous_env,
        )

    @contextmanager
    def compile_env_context(
        env_overrides: dict[str, str | None] | None = None,
        keys_to_clear: tuple[str, ...] = (),
    ) -> Iterator[dict[str, str]]:
        """Acquire GLOBAL_COMPILE_LOCK, apply env_overrides, and restore os.environ on exit."""
        overrides = env_overrides or {}
        with GLOBAL_COMPILE_LOCK:
            tracked_keys = set(overrides.keys()) | set(keys_to_clear)
            previous_env = {key: os.environ.get(key) for key in tracked_keys}
            try:
                for key in keys_to_clear:
                    os.environ.pop(key, None)
                applied: dict[str, str] = {}
                for key, val in overrides.items():
                    if val is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = str(val)
                        applied[key] = str(val)
                yield applied
            finally:
                for key, prev_val in previous_env.items():
                    if prev_val is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = prev_val


try:
    from foldrun_app.core.pipeline_utils import model_pipeline_load_context
except ImportError:

    @contextmanager
    def model_pipeline_load_context(
        pkg_prefix: str,
        vertex_pipeline_dir: str,
        pipeline_module_name: str,
    ) -> Iterator[object]:
        """Thread-safely reload a model's pipeline config, components, and pipeline module in-place."""
        abs_pipeline_dir = os.path.abspath(vertex_pipeline_dir)
        with GLOBAL_COMPILE_LOCK:
            had_pipeline_dir = abs_pipeline_dir in sys.path
            while abs_pipeline_dir in sys.path:
                sys.path.remove(abs_pipeline_dir)
            sys.path.insert(0, abs_pipeline_dir)

            try:
                parent_pkg = importlib.import_module(pkg_prefix)
                cfg_mod_name = f"{pkg_prefix}.config"
                if cfg_mod_name in sys.modules and sys.modules[cfg_mod_name] is not None:
                    cfg_mod = importlib.reload(sys.modules[cfg_mod_name])
                else:
                    cfg_mod = importlib.import_module(cfg_mod_name)
                parent_pkg.config = cfg_mod
                sys.modules["config"] = cfg_mod

                comp_prefix = f"{pkg_prefix}.components."
                for mod_name in sorted(sys.modules):
                    if (
                        mod_name.startswith(comp_prefix)
                        and not mod_name.endswith(".alphafold_utils")
                        and sys.modules.get(mod_name) is not None
                    ):
                        importlib.reload(sys.modules[mod_name])

                comp_init_name = f"{pkg_prefix}.components"
                if comp_init_name in sys.modules and sys.modules[comp_init_name] is not None:
                    comp_pkg = importlib.reload(sys.modules[comp_init_name])
                    parent_pkg.components = comp_pkg

                pipe_full_name = f"{pkg_prefix}.pipelines.{pipeline_module_name}"
                if pipe_full_name in sys.modules and sys.modules[pipe_full_name] is not None:
                    existing_pipe_mod = sys.modules[pipe_full_name]
                    mocked_attrs = {
                        k: v
                        for k, v in vars(existing_pipe_mod).items()
                        if isinstance(v, NonCallableMock)
                    }
                    pipe_mod = importlib.reload(existing_pipe_mod)
                    for k, v in mocked_attrs.items():
                        setattr(pipe_mod, k, v)
                else:
                    pipe_mod = importlib.import_module(pipe_full_name)

                yield pipe_mod
            finally:
                if not had_pipeline_dir:
                    while abs_pipeline_dir in sys.path:
                        sys.path.remove(abs_pipeline_dir)
                sys.modules.pop("config", None)


def _install_model_lock_and_loader_shims() -> None:
    """Ensure AF2/Boltz2/OF3 base classes and pipeline loaders share GLOBAL_COMPILE_LOCK."""
    try:
        from foldrun_app.models.af2.base import AF2Tool

        AF2Tool._compile_env_lock = GLOBAL_COMPILE_LOCK
    except Exception:
        pass
    try:
        from foldrun_app.models.boltz2.base import BOLTZ2Tool

        BOLTZ2Tool._compile_env_lock = GLOBAL_COMPILE_LOCK
    except Exception:
        pass
    try:
        from foldrun_app.models.of3.base import OF3Tool

        OF3Tool._compile_env_lock = GLOBAL_COMPILE_LOCK
    except Exception:
        pass

    # Patch load_vertex_pipeline on Boltz-2 and OF3 if core.pipeline_utils lacks model_pipeline_load_context
    try:
        from foldrun_app.models.boltz2.utils import pipeline_utils as b2_pu

        if not getattr(b2_pu.load_vertex_pipeline, "_uses_model_pipeline_load_context", False):
            b2_dir = os.path.abspath(
                os.path.join(os.path.dirname(b2_pu.__file__), "..", "pipeline")
            )

            def _load_boltz2_pipeline(enable_flex_start: bool = False):
                strategy = "FLEX_START" if enable_flex_start else "STANDARD"
                with model_pipeline_load_context(
                    "foldrun_app.models.boltz2.pipeline",
                    b2_dir,
                    "boltz2_inference_pipeline",
                ) as pipe_mod:
                    return pipe_mod.create_boltz2_inference_pipeline(strategy=strategy)

            _load_boltz2_pipeline._uses_model_pipeline_load_context = True  # type: ignore[attr-defined]
            b2_pu.load_vertex_pipeline = _load_boltz2_pipeline
    except Exception:
        pass

    try:
        from foldrun_app.models.of3.utils import pipeline_utils as of3_pu

        if not getattr(of3_pu.load_vertex_pipeline, "_uses_model_pipeline_load_context", False):
            of3_dir = os.path.abspath(
                os.path.join(os.path.dirname(of3_pu.__file__), "..", "pipeline")
            )

            def _load_of3_pipeline(enable_flex_start: bool = False):
                strategy = "FLEX_START" if enable_flex_start else "STANDARD"
                with model_pipeline_load_context(
                    "foldrun_app.models.of3.pipeline",
                    of3_dir,
                    "of3_inference_pipeline",
                ) as pipe_mod:
                    return pipe_mod.create_of3_inference_pipeline(strategy=strategy)

            _load_of3_pipeline._uses_model_pipeline_load_context = True  # type: ignore[attr-defined]
            of3_pu.load_vertex_pipeline = _load_of3_pipeline
    except Exception:
        pass


_install_model_lock_and_loader_shims()


_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def resolve_pipeline_job_name(job_id: str, project_id: str, region: str) -> str:
    """Validate a pipeline job ID and return its full resource name."""
    if not isinstance(job_id, str) or not job_id.strip() or "\x00" in job_id:
        raise ValueError("Invalid job_id: must be a non-empty string without null bytes")

    expected_prefix = f"projects/{project_id}/locations/{region}/pipelineJobs/"
    if job_id.startswith("projects/"):
        if not job_id.startswith(expected_prefix):
            raise ValueError(
                f"Cross-project or cross-region pipeline job access denied: '{job_id}' does not match expected prefix '{expected_prefix}'"
            )
        short_id = job_id[len(expected_prefix) :]
        if not _JOB_ID_PATTERN.match(short_id):
            raise ValueError(
                f"Malformed pipeline job resource name: invalid short job ID '{short_id}'"
            )
        return job_id
    if not _JOB_ID_PATTERN.match(job_id):
        raise ValueError(f"Malformed pipeline job ID: '{job_id}'")
    return f"{expected_prefix}{job_id}"


def get_pipeline_job(job_id: str, project_id: str, region: str) -> vertex_ai.PipelineJob:
    """Get a Vertex AI PipelineJob by resource name, short ID, or display_name fallback."""
    full_name = resolve_pipeline_job_name(job_id, project_id, region)
    raw_id = full_name.rsplit("/", 1)[-1]

    client = vertex_ai.PipelineServiceClient(
        client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"}
    )

    try:
        request = vertex_ai.GetPipelineJobRequest(name=full_name)
        return client.get_pipeline_job(request=request)
    except Exception as orig_exc:
        parent = f"projects/{project_id}/locations/{region}"
        try:
            list_req = vertex_ai.ListPipelineJobsRequest(
                parent=parent,
                filter=f'display_name="{raw_id}"',
                page_size=10,
            )
            matches = list(client.list_pipeline_jobs(request=list_req))
            if matches:
                return matches[0]
        except Exception:
            pass
        raise orig_exc


def _is_valid_proto_timestamp(dt: Any) -> bool:
    """Return True if dt is a non-null timestamp after the unset protobuf epoch (year > 1970)."""
    if not dt:
        return False
    year = getattr(dt, "year", None)
    if isinstance(year, int) and year <= 1970:
        return False
    return True


def _format_proto_timestamp(dt: Any) -> str | None:
    """Format a protobuf/datetime timestamp as ISO-8601, ignoring unset epoch (<= 1970)."""
    if not _is_valid_proto_timestamp(dt):
        return None
    return dt.isoformat()


def _convert_proto_to_dict(obj: Any) -> Any:
    """Recursively convert protobuf objects to native Python types for JSON serialization."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_convert_proto_to_dict(item) for item in obj]
    if isinstance(obj, dict) or hasattr(obj, "items"):
        return {str(key): _convert_proto_to_dict(value) for key, value in obj.items()}
    if hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes)):
        try:
            return [_convert_proto_to_dict(item) for item in obj]
        except TypeError:
            pass
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    try:
        return str(obj)
    except Exception:
        return None


def get_job_status(job: vertex_ai.PipelineJob) -> dict[str, Any]:
    """Extract status information from a PipelineJob, normalizing unset epoch timestamps."""
    create_ts = getattr(job, "create_time", None)
    start_ts = getattr(job, "start_time", None)
    end_ts = getattr(job, "end_time", None)

    status = {
        "job_id": job.name,
        "display_name": job.display_name,
        "state": job.state.name,
        "create_time": _format_proto_timestamp(create_ts),
        "start_time": _format_proto_timestamp(start_ts),
        "end_time": _format_proto_timestamp(end_ts),
        "labels": dict(job.labels) if job.labels else {},
        "pipeline_spec": job.pipeline_spec.get("pipelineInfo", {}).get("name", "unknown")
        if job.pipeline_spec
        else "unknown",
    }

    if _is_valid_proto_timestamp(end_ts) and _is_valid_proto_timestamp(start_ts):
        duration = end_ts - start_ts
        status["duration_seconds"] = duration.total_seconds()

    if hasattr(job, "error") and job.error and getattr(job.error, "message", None):
        status["error_message"] = job.error.message

    return status


def get_task_details(job: vertex_ai.PipelineJob) -> dict[str, Any]:
    """Extract task-level execution details, custom_job_name/id, and filtered task_list."""
    if not hasattr(job, "job_detail") or not job.job_detail:
        return {"tasks": {}, "task_list": [], "predictions": [], "best_prediction": None}

    tasks: dict[str, Any] = {}
    task_list: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []
    job_display_name = getattr(job, "display_name", "") or ""

    for task in job.job_detail.task_details:
        t_create = getattr(task, "create_time", None)
        t_start = getattr(task, "start_time", None)
        t_end = getattr(task, "end_time", None)
        task_info: dict[str, Any] = {
            "task_id": task.task_id,
            "task_name": task.task_name,
            "state": task.state.name,
            "create_time": _format_proto_timestamp(t_create),
            "start_time": _format_proto_timestamp(t_start),
            "end_time": _format_proto_timestamp(t_end),
        }

        if _is_valid_proto_timestamp(t_end) and _is_valid_proto_timestamp(t_start):
            task_info["duration_seconds"] = (t_end - t_start).total_seconds()

        custom_job_name = None
        if hasattr(task, "executor_detail") and task.executor_detail:
            cjd = getattr(task.executor_detail, "custom_job_detail", None)
            if cjd and getattr(cjd, "job", ""):
                custom_job_name = str(cjd.job)
            if not custom_job_name:
                cd = getattr(task.executor_detail, "container_detail", None)
                if cd and getattr(cd, "main_job", ""):
                    custom_job_name = str(cd.main_job)

        if hasattr(task, "execution") and task.execution:
            execution_metadata = {}
            if hasattr(task.execution, "metadata") and task.execution.metadata:
                execution_metadata = _convert_proto_to_dict(task.execution.metadata)
            task_info["execution_metadata"] = execution_metadata
            if not custom_job_name and "main_job" in execution_metadata:
                custom_job_name = str(execution_metadata["main_job"])
            elif not custom_job_name:
                gcp_res_raw = execution_metadata.get(
                    "output:gcp_resources"
                ) or execution_metadata.get("gcp_resources")
                if gcp_res_raw:
                    try:
                        resources = json.loads(gcp_res_raw)
                        for res in resources.get("resources", []):
                            if res.get("resourceType") == "CustomJob":
                                custom_job_name = res.get("resourceUri")
                                break
                    except Exception:
                        pass

        if custom_job_name:
            task_info["custom_job_name"] = custom_job_name
            task_info["custom_job_id"] = custom_job_name.split("/")[-1]

        if hasattr(task, "error") and task.error:
            task_info["error"] = {
                "message": task.error.message if hasattr(task.error, "message") else None,
                "code": task.error.code if hasattr(task.error, "code") else None,
            }

        tasks[task.task_name] = task_info

        tname = task.task_name
        is_wrapper = (
            tname.startswith("for-loop-")
            or tname.startswith("condition-")
            or (job_display_name and tname == job_display_name)
            or "-gw-" in tname
            or "-inference-pipeline" in tname
        )
        if not is_wrapper:
            task_list.append(task_info)

        if task.task_name == "predict" and hasattr(task, "outputs"):
            if "raw_prediction" in task.outputs:
                raw_pred = task.outputs["raw_prediction"]
                if hasattr(raw_pred, "artifacts") and len(raw_pred.artifacts) > 0:
                    artifact = raw_pred.artifacts[0]
                    if hasattr(artifact, "metadata"):
                        predictions.append(
                            {
                                "task_id": task.task_id,
                                "model_name": task.execution.metadata.get(
                                    "input:model_name", "unknown"
                                )
                                if hasattr(task, "execution")
                                else "unknown",
                                "ranking_confidence": artifact.metadata.get(
                                    "ranking_confidence", 0
                                ),
                                "uri": artifact.uri if hasattr(artifact, "uri") else None,
                            }
                        )

    predictions.sort(key=lambda x: x["ranking_confidence"], reverse=True)

    return {
        "tasks": tasks,
        "task_list": task_list,
        "predictions": predictions,
        "best_prediction": predictions[0] if predictions else None,
    }
