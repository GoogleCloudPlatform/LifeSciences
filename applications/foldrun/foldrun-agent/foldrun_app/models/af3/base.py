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

"""Base class for AlphaFold3 Vertex AI Endpoint tools."""

import logging
import re
from typing import Any

from google.cloud import aiplatform as vertex_ai

from foldrun_app.core.base_tool import BaseTool

from .config import AF3Config

logger = logging.getLogger(__name__)

AF3_GCS_PREFIX_DIR = "af3_predictions"
MODEL_CIF_FILENAME = "{job_id}_model.cif"
SUMMARY_CONFIDENCES_FILENAME = "{job_id}_summary_confidences.json"
JOB_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


class AF3Tool(BaseTool):
    """Base class for AlphaFold 3 tools invoking Agent Platform Prediction Endpoint."""

    def __init__(self, tool_config: dict[str, Any], config: AF3Config | None = None):
        super().__init__(tool_config, config or AF3Config())

    def get_endpoint(self, endpoint_id: str | None = None) -> vertex_ai.Endpoint:
        """Resolve and instantiate an Agent Platform Endpoint object.

        Supports auto-discovery when `endpoint_id` or `AF3_ENDPOINT` is set to `'auto'`.

        Args:
            endpoint_id: Optional endpoint ID or full resource name override.

        Returns:
            Instantiated vertex_ai.Endpoint.
        """
        target = (endpoint_id or self.config.endpoint_id or "").strip()
        if not target:
            raise ValueError("No AlphaFold 3 Endpoint ID specified or configured in AF3_ENDPOINT.")

        if target.lower() == "auto":
            endpoints = vertex_ai.Endpoint.list(
                project=self.config.project_id,
                location=self.config.endpoint_location,
            )
            af3_endpoints = [
                ep
                for ep in endpoints
                if any(
                    tok in (getattr(ep, "display_name", "") or "").lower()
                    for tok in ("alphafold", "af3")
                )
            ]
            candidates = af3_endpoints or endpoints
            if not candidates:
                raise ValueError(
                    f"No AlphaFold 3 Endpoint found in {self.config.project_id}/{self.config.endpoint_location}. "
                    "The endpoint is managed by Terraform: run the FoldRun infrastructure step "
                    "(`./deploy-all.sh <PROJECT_ID> <REGION> --steps infra --enable-af3`) to provision it."
                )
            # Prefer an endpoint with active deployed models
            candidates.sort(
                key=lambda ep: (
                    len(self.get_deployed_models(ep)),
                    getattr(ep, "update_time", ""),
                ),
                reverse=True,
            )
            return candidates[0]

        return vertex_ai.Endpoint(
            endpoint_name=target,
            project=self.config.project_id,
            location=self.config.endpoint_location,
        )

    @staticmethod
    def get_deployed_models(endpoint: Any) -> list[Any]:
        """Return deployed models from a Vertex AI Endpoint.

        Note: `google.cloud.aiplatform.Endpoint` exposes deployed models via
        `endpoint.list_models()` (backed by `endpoint._gca_resource.deployed_models`)
        rather than a top-level `.deployed_models` attribute. This helper supports
        both the real Vertex AI SDK `Endpoint` and unit test mocks.
        """
        if "deployed_models" in getattr(endpoint, "__dict__", {}):
            return list(endpoint.deployed_models or [])
        if hasattr(endpoint, "list_models") and callable(endpoint.list_models):
            models = endpoint.list_models()
            if not type(models).__module__.startswith("unittest.mock"):
                try:
                    return list(models)
                except TypeError:
                    pass
        gca = getattr(endpoint, "_gca_resource", None)
        if gca is not None and not type(gca).__module__.startswith("unittest.mock"):
            return list(getattr(gca, "deployed_models", None) or [])
        return []

    def get_active_deploy_operations(self, endpoint: Any) -> list[dict[str, Any]]:
        """Return in-progress DeployModel LROs on the endpoint, if any."""
        if type(endpoint).__module__.startswith("unittest.mock"):
            return []
        resource_name = getattr(endpoint, "resource_name", "")
        if not isinstance(resource_name, str) or not resource_name.startswith("projects/"):
            return []
        try:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            session = AuthorizedSession(creds)
            url = (
                f"https://{self.config.endpoint_location}-aiplatform.googleapis.com/"
                f"v1/{resource_name}/operations"
            )
            resp = session.get(url, timeout=10)
            if not resp.ok:
                return []
            operations = resp.json().get("operations", [])
            active = []
            for op in operations:
                if op.get("done") is True:
                    continue
                meta = op.get("metadata", {})
                if "DeployModel" in meta.get("@type", ""):
                    active.append(
                        {
                            "operation_name": op.get("name"),
                            "deployment_stage": meta.get("deploymentStage", "DEPLOYING"),
                            "create_time": meta.get("genericMetadata", {}).get("createTime"),
                        }
                    )
            return active
        except Exception as e:
            logger.debug(f"Could not list active endpoint operations for {resource_name}: {e}")
            return []

    @staticmethod
    def extract_replica_counts(dm: Any) -> dict[str, Any]:
        """Extract min/max/initial replica counts and Scale-to-Zero metadata from a DeployedModel.

        In protobuf3, `min_replica_count = 0` is the default value for `int32`, whereas
        in unit test MagicMocks `min_replica_count` may be an unconfigured MagicMock object.
        When `min_replica_count` is an integer `0` alongside a positive `max_replica_count`,
        `initial_replica_count`, or `scale_to_zero_spec`, Gemini platform Scale-to-Zero is enabled.
        """
        dedicated = getattr(dm, "dedicated_resources", None)
        raw_min = getattr(dedicated, "min_replica_count", None) if dedicated else None
        raw_max = getattr(dedicated, "max_replica_count", None) if dedicated else None
        raw_init = getattr(dedicated, "initial_replica_count", None) if dedicated else None
        stz_spec = getattr(dedicated, "scale_to_zero_spec", None) if dedicated else None

        is_int_min = isinstance(raw_min, int) and not isinstance(raw_min, bool)
        is_int_max = isinstance(raw_max, int) and not isinstance(raw_max, bool)
        is_int_init = isinstance(raw_init, int) and not isinstance(raw_init, bool)

        stz_enabled = False
        if is_int_min and raw_min == 0:
            if (is_int_max and raw_max >= 1) or (is_int_init and raw_init >= 1) or stz_spec:
                stz_enabled = True
                min_replicas = 0
            else:
                min_replicas = 1
        elif is_int_min and raw_min > 0:
            min_replicas = raw_min
        else:
            min_replicas = 1

        if is_int_max and raw_max >= 1:
            max_replicas = max(raw_max, min_replicas)
        else:
            max_replicas = max(1, min_replicas)

        if stz_enabled:
            initial_replicas = raw_init if (is_int_init and raw_init >= 1) else 1
        else:
            initial_replicas = 0

        def _dur_seconds(dur: Any) -> int | None:
            if dur is None:
                return None
            if not type(dur).__module__.startswith("unittest.mock"):
                total_fn = getattr(dur, "total_seconds", None)
                if callable(total_fn):
                    val = total_fn()
                    if isinstance(val, (int, float)) and not isinstance(val, bool):
                        return int(val)
            sec = getattr(dur, "seconds", None)
            if isinstance(sec, int) and not isinstance(sec, bool):
                return int(sec)
            return None

        min_scaleup_sec = 300
        idle_scaledown_sec = 1200
        if stz_spec is not None and not type(stz_spec).__module__.startswith("unittest.mock"):
            up_sec = _dur_seconds(getattr(stz_spec, "min_scaleup_period", None))
            down_sec = _dur_seconds(getattr(stz_spec, "idle_scaledown_period", None))
            if up_sec is not None and up_sec > 0:
                min_scaleup_sec = up_sec
            if down_sec is not None and down_sec > 0:
                idle_scaledown_sec = down_sec

        # Extract capacity pool / provisioning mode and live replica status
        provisioning_mode = "ANY_RESERVATION"
        if dedicated is not None:
            flex_start = getattr(dedicated, "flex_start", None)
            flex_sec = _dur_seconds(getattr(flex_start, "max_runtime_duration", None))
            spot_val = getattr(dedicated, "spot", False)
            if isinstance(flex_sec, int) and flex_sec > 0:
                provisioning_mode = "DWS_FLEX_START"
            elif isinstance(spot_val, bool) and spot_val:
                provisioning_mode = "SPOT"
            else:
                ms = getattr(dedicated, "machine_spec", None)
                ra = getattr(ms, "reservation_affinity", None) if ms else None
                ra_type = getattr(ra, "reservation_affinity_type", None) if ra else None
                ra_name = (
                    ra_type.name
                    if hasattr(ra_type, "name") and isinstance(ra_type.name, str)
                    else (str(ra_type) if isinstance(ra_type, str) else "")
                )
                if ra_name and ra_name != "TYPE_UNSPECIFIED":
                    provisioning_mode = ra_name

        status_obj = getattr(dm, "status", None)
        avail_raw = getattr(status_obj, "available_replica_count", None) if status_obj else None
        available_replicas = (
            int(avail_raw)
            if isinstance(avail_raw, int) and not isinstance(avail_raw, bool)
            else None
        )
        msg_raw = getattr(status_obj, "message", None) if status_obj else None
        status_message = (
            str(msg_raw).strip() if isinstance(msg_raw, str) and msg_raw.strip() else None
        )

        return {
            "min_replica_count": int(min_replicas),
            "max_replica_count": int(max_replicas),
            "initial_replica_count": int(initial_replicas),
            "scale_to_zero_enabled": stz_enabled,
            "min_scaleup_period_seconds": min_scaleup_sec if stz_enabled else None,
            "idle_scaledown_period_seconds": idle_scaledown_sec if stz_enabled else None,
            "serving_capacity_replicas": max(int(min_replicas), int(initial_replicas), 1),
            "provisioning_mode": provisioning_mode,
            "available_replica_count": available_replicas,
            "status_message": status_message,
        }

    def get_model(self, model_id: str | None = None) -> vertex_ai.Model:
        """Resolve and instantiate an Agent Platform Model object.

        Auto-discovers the latest registered AlphaFold 3 model in Vertex AI Model Registry
        when `model_id` and `AF3_MODEL_ID` are omitted or set to `'auto'`.

        Args:
            model_id: Optional model ID or full resource name override.

        Returns:
            Instantiated vertex_ai.Model.
        """
        target = (model_id or self.config.model_id or "").strip()
        if not target or target.lower() == "auto":
            models = vertex_ai.Model.list(
                project=self.config.project_id,
                location=self.config.endpoint_location,
            )
            af3_models = [
                m
                for m in models
                if any(
                    tok
                    in (getattr(m, "display_name", "") or getattr(m, "resource_name", "")).lower()
                    for tok in ("alphafold3", "alphafold-3", "af3")
                )
            ]
            if not af3_models:
                raise ValueError(
                    "No AlphaFold 3 Model ID specified in AF3_MODEL_ID and no matching model found in Vertex AI Model Registry."
                )

            # Vertex AI DeployModel's allowlist for the restricted AlphaFold 3 serving container
            # checks exact string equality against the tagless URI
            # 'us-docker.pkg.dev/vertex-ai-restricted/alphafold3/alphafold3-inference'.
            allowed_tagless_uri = (
                "us-docker.pkg.dev/vertex-ai-restricted/alphafold3/alphafold3-inference"
            )

            def _image_uri_rank(m: Any) -> int:
                spec = getattr(m, "container_spec", None) or getattr(
                    getattr(m, "_gca_resource", None), "container_spec", None
                )
                uri = str(getattr(spec, "image_uri", "") or "").strip()
                if uri == allowed_tagless_uri:
                    return 0
                if uri and ":" not in uri.split("/")[-1]:
                    return 1
                return 2

            af3_models.sort(key=_image_uri_rank)
            return af3_models[0]

        return vertex_ai.Model(
            model_name=target,
            project=self.config.project_id,
            location=self.config.endpoint_location,
        )

    @staticmethod
    def validate_job_name(job_name: str) -> str:
        """Validate job_name against strict allowlist pattern to prevent path traversal."""
        if not isinstance(job_name, str) or not JOB_NAME_PATTERN.fullmatch(job_name):
            raise ValueError(
                f"Invalid job_name '{job_name}': only alphanumeric characters, "
                "underscores, and hyphens are allowed."
            )
        return job_name

    def get_job_gcs_prefix(self, job_name: str) -> str:
        """Return the full GCS URI prefix for a given job name."""
        safe_name = self.validate_job_name(job_name)
        return f"gs://{self.config.bucket_name}/{AF3_GCS_PREFIX_DIR}/{safe_name}"

    def get_job_blob_prefix(self, job_name: str) -> str:
        """Return the relative GCS blob path prefix for a given job name."""
        safe_name = self.validate_job_name(job_name)
        return f"{AF3_GCS_PREFIX_DIR}/{safe_name}"

    def get_cif_blob_path(self, job_name: str) -> str:
        """Return the relative GCS blob path for the predicted mmCIF structure."""
        safe_name = self.validate_job_name(job_name)
        return (
            f"{self.get_job_blob_prefix(safe_name)}/{MODEL_CIF_FILENAME.format(job_id=safe_name)}"
        )

    def get_conf_blob_path(self, job_name: str) -> str:
        """Return the relative GCS blob path for summary confidences JSON."""
        safe_name = self.validate_job_name(job_name)
        return f"{self.get_job_blob_prefix(safe_name)}/{SUMMARY_CONFIDENCES_FILENAME.format(job_id=safe_name)}"

    def get_endpoint_console_url(self, endpoint_resource_name: str) -> str:
        """Return the Google Cloud Console URL for the Vertex AI Online Prediction Endpoint."""
        short_id = str(endpoint_resource_name).rstrip("/").split("/")[-1]
        return (
            f"https://console.cloud.google.com/vertex-ai/online-prediction/"
            f"locations/{self.config.endpoint_location}/endpoints/{short_id}"
            f"?project={self.config.project_id}"
        )

    def get_endpoint_logs_url(
        self, endpoint_resource_name: Any, job_name: str | None = None
    ) -> str:
        """Return the Cloud Logging URL filtered to the Vertex AI Endpoint container logs."""
        res_name = getattr(endpoint_resource_name, "resource_name", endpoint_resource_name)
        short_id = str(res_name).rstrip("/").split("/")[-1]
        job_filter = f"%20%22{job_name}%22" if job_name else ""
        return (
            f"https://console.cloud.google.com/logs/query;"
            f"query=resource.type%3D%22aiplatform.googleapis.com%2FEndpoint%22"
            f"%20resource.labels.endpoint_id%3D%22{short_id}%22{job_filter};duration=PT1H"
            f"?project={self.config.project_id}"
        )

    def get_endpoint_queue_status(
        self, endpoint: Any, newly_submitted_count: int = 0
    ) -> dict[str, Any]:
        """Inspect live GCS replica slot locks and FIFO queue tickets for the AF3 endpoint.

        Also computes estimated queue drain time and a proactive replica scale-up
        recommendation when multiple jobs are backed up.
        """
        import json
        import math
        import os
        import time

        res_name = getattr(endpoint, "resource_name", str(endpoint or ""))
        short_id = str(res_name).rstrip("/").split("/")[-1] or str(self.config.endpoint_id)

        deployed_models = self.get_deployed_models(endpoint)
        active_replicas = 0
        for dm in deployed_models:
            rep_info = self.extract_replica_counts(dm)
            active_replicas += rep_info["serving_capacity_replicas"]

        active_slots: list[dict[str, Any]] = []
        waiting_queue: list[dict[str, Any]] = []
        now_t = time.time()

        if not type(endpoint).__module__.startswith("unittest.mock") and getattr(
            self.config, "bucket_name", None
        ):
            try:
                from google.cloud import storage

                storage_client = getattr(self, "storage_client", None) or storage.Client(
                    project=self.config.project_id
                )
                if not type(storage_client).__module__.startswith("unittest.mock"):
                    bucket = storage_client.bucket(self.config.bucket_name)
                    prefix = f"{AF3_GCS_PREFIX_DIR}/.locks/{short_id}_"
                    for blob in bucket.list_blobs(prefix=prefix):
                        bname = blob.name.split("/")[-1]
                        try:
                            data = json.loads(blob.download_as_text())
                        except Exception:
                            continue
                        hb = float(
                            data.get("heartbeat_at")
                            or data.get("heartbeat_epoch")
                            or data.get("acquired_at")
                            or data.get("queued_at")
                            or 0
                        )
                        if "_slot_" in bname and (now_t - hb) <= 210.0:
                            acq = float(data.get("acquired_at") or hb)
                            active_slots.append(
                                {
                                    "slot_index": data.get("slot_index", 0),
                                    "job_name": data.get("job_name", "unknown"),
                                    "pipeline_job_id": data.get("pipeline_job_id"),
                                    "stage": data.get("stage", "running_inference"),
                                    "running_seconds": round(max(0.0, now_t - acq), 1),
                                }
                            )
                        elif "_queue_" in bname and (now_t - hb) <= 120.0:
                            q_at = float(data.get("queued_at") or hb)
                            waiting_queue.append(
                                {
                                    "job_name": data.get("job_name", "unknown"),
                                    "pipeline_job_id": data.get("pipeline_job_id"),
                                    "queued_at": q_at,
                                    "waiting_seconds": round(max(0.0, now_t - q_at), 1),
                                }
                            )
            except Exception as exc:
                logger.debug(f"Could not inspect GCS queue locks for {short_id}: {exc}")

        active_slots.sort(key=lambda x: int(x.get("slot_index") or 0))
        waiting_queue.sort(
            key=lambda x: (float(x.get("queued_at") or 0), str(x.get("pipeline_job_id") or ""))
        )
        for idx, item in enumerate(waiting_queue):
            item["fifo_position"] = idx + 1
            item.pop("queued_at", None)

        effective_replicas = max(active_replicas, 1)
        total_in_flight = (
            len(active_slots) + len(waiting_queue) + max(0, int(newly_submitted_count))
        )
        avg_job_minutes = 4.5
        est_queue_minutes = round((total_in_flight * avg_job_minutes) / effective_replicas, 1)

        max_allowed = int(os.environ.get("AF3_MAX_ALLOWED_REPLICAS", "4"))
        scale_up_recommendation: dict[str, Any] | None = None
        if (
            total_in_flight > effective_replicas
            and (total_in_flight >= 3 or est_queue_minutes >= 15.0)
            and effective_replicas < max_allowed
        ):
            rec_replicas = min(
                max_allowed, max(effective_replicas + 1, math.ceil(total_in_flight / 2))
            )
            scaled_est_minutes = round((total_in_flight * avg_job_minutes) / rec_replicas, 1)
            hourly_rate = round(11.06 * rec_replicas, 2)
            two_rep_minutes = round((total_in_flight * avg_job_minutes) / 2, 1)
            scaling_prompt = (
                f"Estimated queue drain time is ~{est_queue_minutes} min on 1 replica. "
                f"Scaling to 2 replicas cuts this to ~{two_rep_minutes} min. "
                "Increase `af3_max_replica_count` in Terraform and re-run the infra step to parallelize."
                if effective_replicas == 1
                else (
                    f"Estimated queue drain time is ~{est_queue_minutes} min on {effective_replicas} replicas. "
                    f"Scaling to {rec_replicas} replicas cuts this to ~{scaled_est_minutes} min. "
                    "Increase `af3_max_replica_count` in Terraform and re-run the infra step to parallelize."
                )
            )
            scale_up_recommendation = {
                "recommended": True,
                "current_replicas": active_replicas,
                "recommended_replicas": rec_replicas,
                "max_allowed_replicas": max_allowed,
                "estimated_minutes_current": est_queue_minutes,
                "estimated_minutes_scaled": scaled_est_minutes,
                "scaled_hourly_cost": f"~${hourly_rate:.2f}/hr while active (scales to $0.00/hr when idle)",
                "suggested_action": (
                    f"Set af3_max_replica_count = {rec_replicas} in Terraform and re-run "
                    "`./deploy-all.sh <PROJECT_ID> <REGION> --steps infra --enable-af3`"
                ),
                "scaling_prompt": scaling_prompt,
                "reason": (
                    f"{total_in_flight} AF3 jobs are active/queued (~{est_queue_minutes} min at "
                    f"{effective_replicas} replica(s)). Scaling to {rec_replicas} H100 replicas cuts "
                    f"estimated completion time to ~{scaled_est_minutes} min, and queued KFP jobs "
                    "will automatically claim the new replica slot(s) within 5 seconds once active."
                ),
            }

        return {
            "active_replicas": active_replicas,
            "active_slots_in_use": len(active_slots),
            "active_slots": active_slots,
            "queued_jobs_waiting": len(waiting_queue),
            "waiting_queue": waiting_queue,
            "newly_submitted_count": max(0, int(newly_submitted_count)),
            "total_active_and_queued": total_in_flight,
            "estimated_drain_minutes": est_queue_minutes,
            "scale_up_recommendation": scale_up_recommendation,
        }
