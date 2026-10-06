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

"""Tool for deploying the AlphaFold 3 model to an Agent Platform Endpoint (spin up GPU)."""

import logging
import os
from typing import Any

from google.api_core.exceptions import GoogleAPICallError

from foldrun_app.core.hardware import (
    is_capacity_or_reservation_error,
    resolve_af3_hardware_plan,
)

from ..base import AF3Tool

logger = logging.getLogger(__name__)


class AF3DeployEndpointTool(AF3Tool):
    """Deploys an AlphaFold 3 model to the Agent Platform Endpoint, allocating dedicated GPU resources."""

    def run(self, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Deploy AF3 model to the Agent Platform Endpoint.

        Args:
            arguments: {
                'model_id': Optional Agent Platform Model resource name or ID,
                'endpoint_id': Optional Agent Platform Endpoint resource name or ID,
                'machine_type': Machine type (default: 'a3-highgpu-1g'),
                'accelerator_type': Accelerator type (default: 'NVIDIA_H100_80GB'),
                'accelerator_count': GPU count (default: 1),
                'min_replica_count': Minimum replicas (default: 1),
                'max_replica_count': Maximum replicas (default: 1),
                'traffic_percentage': Percentage of traffic (default: 100),
                'reservation_affinity_type': Optional ('NO_RESERVATION', 'ANY_RESERVATION', 'SPECIFIC_RESERVATION'),
                'reservation_names': Optional list of GCE reservation names or resource paths,
                'spot': Optional bool to deploy on Spot/Preemptible GPU capacity,
                'auto_fallback_gpu': Optional bool (default True) to fall back across H100 -> A100_80GB -> A100 -> L4 on quota/stockout/reservation errors,
                'sync': Whether to wait synchronously for deployment completion (default: False),
            }

        Returns:
            Dictionary with deployment status, endpoint info, reservation status, and estimated spin-up time.
        """
        args = arguments or {}
        endpoint_id = args.get("endpoint_id")
        model_id = args.get("model_id") or getattr(self.config, "model_id", "")
        requested_machine_type = args.get("machine_type") or getattr(
            self.config, "machine_type", "a3-highgpu-1g"
        )
        requested_acc_type = args.get("accelerator_type") or getattr(
            self.config, "accelerator_type", "NVIDIA_H100_80GB"
        )
        requested_acc_count = int(
            args.get("accelerator_count") or getattr(self.config, "accelerator_count", 1)
        )
        min_replicas = int(args.get("min_replica_count", 1))
        max_replicas = int(args.get("max_replica_count", min_replicas))
        if max_replicas < min_replicas:
            max_replicas = min_replicas
        max_allowed_replicas = int(os.environ.get("AF3_MAX_ALLOWED_REPLICAS", "4"))
        if min_replicas < 1 or max_replicas > max_allowed_replicas:
            return {
                "status": "error",
                "message": (
                    f"Invalid replica count: min_replica_count={min_replicas}, "
                    f"max_replica_count={max_replicas}. Must be between 1 and "
                    f"{max_allowed_replicas} (override via AF3_MAX_ALLOWED_REPLICAS)."
                ),
            }
        explicit_scaling = "min_replica_count" in args or "max_replica_count" in args
        sync = args.get("sync", False)
        spot = bool(args.get("spot", False))
        auto_fallback_gpu = bool(
            args.get("auto_fallback_gpu", getattr(self.config, "auto_fallback_gpu", True))
        )
        res_affinity_type = args.get("reservation_affinity_type") or getattr(
            self.config, "reservation_affinity_type", ""
        )
        raw_res_names = args.get("reservation_names") or getattr(
            self.config, "reservation_names", []
        )
        if isinstance(raw_res_names, str):
            res_names = [r.strip() for r in raw_res_names.split(",") if r.strip()]
        else:
            res_names = list(raw_res_names or [])

        try:
            endpoint = self.get_endpoint(endpoint_id)

            # Check if a model is already deployed
            deployed_models = self.get_deployed_models(endpoint)
            if deployed_models:
                dm = deployed_models[0]
                dedicated = getattr(dm, "dedicated_resources", None)
                cur_min = int(
                    (getattr(dedicated, "min_replica_count", None) if dedicated else None) or 1
                )
                cur_max = int(
                    (getattr(dedicated, "max_replica_count", None) if dedicated else None)
                    or cur_min
                )
                if explicit_scaling and (min_replicas != cur_min or max_replicas != cur_max):
                    from google.cloud.aiplatform_v1 import (
                        DedicatedResources,
                        DeployedModel,
                        EndpointServiceClient,
                        MutateDeployedModelRequest,
                    )
                    from google.protobuf import field_mask_pb2

                    location = self.config.endpoint_location
                    client = EndpointServiceClient(
                        client_options={"api_endpoint": f"{location}-aiplatform.googleapis.com"}
                    )
                    dm_id = getattr(dm, "id", None)
                    logger.info(
                        f"Scaling deployed model {dm_id} on {endpoint.resource_name} "
                        f"from replicas ({cur_min}, {cur_max}) -> ({min_replicas}, {max_replicas})"
                    )
                    op = client.mutate_deployed_model(
                        request=MutateDeployedModelRequest(
                            endpoint=endpoint.resource_name,
                            deployed_model=DeployedModel(
                                id=dm_id,
                                dedicated_resources=DedicatedResources(
                                    min_replica_count=min_replicas,
                                    max_replica_count=max_replicas,
                                ),
                            ),
                            update_mask=field_mask_pb2.FieldMask(
                                paths=[
                                    "dedicated_resources.min_replica_count",
                                    "dedicated_resources.max_replica_count",
                                ]
                            ),
                        )
                    )
                    if sync:
                        op.result()
                    unit_rate = (
                        11.06
                        if "a3" in requested_machine_type or "H100" in requested_acc_type
                        else 1.01
                    )
                    scaled_rate = f"~${unit_rate * min_replicas:.2f}/hr"
                    return {
                        "status": "scaled" if sync else "scaling",
                        "endpoint_name": endpoint.resource_name,
                        "console_url": self.get_endpoint_console_url(endpoint.resource_name),
                        "logs_url": self.get_endpoint_logs_url(endpoint.resource_name),
                        "deployed_model_id": dm_id,
                        "previous_min_replica_count": cur_min,
                        "previous_max_replica_count": cur_max,
                        "min_replica_count": min_replicas,
                        "max_replica_count": max_replicas,
                        "idle_cost": scaled_rate,
                        "message": (
                            f"Scaled AlphaFold 3 endpoint '{endpoint.resource_name}' (deployed model {dm_id}) "
                            f"from {cur_min}–{cur_max} replica(s) to {min_replicas}–{max_replicas} replica(s) "
                            f"({scaled_rate} across {min_replicas} active replica(s)). "
                            "Vertex AI automatically load-balances concurrent predictions across all replicas."
                        ),
                    }

                return {
                    "status": "already_deployed",
                    "endpoint_name": endpoint.resource_name,
                    "console_url": self.get_endpoint_console_url(endpoint.resource_name),
                    "logs_url": self.get_endpoint_logs_url(endpoint.resource_name),
                    "deployed_models_count": len(deployed_models),
                    "min_replica_count": cur_min,
                    "max_replica_count": cur_max,
                    "message": (
                        f"Endpoint already has {len(deployed_models)} active deployed model(s) "
                        f"(min_replicas={cur_min}, max_replicas={cur_max}). "
                        f"Console: {self.get_endpoint_console_url(endpoint.resource_name)}. "
                        "AF3 is ready for predictions. Pass min_replica_count / max_replica_count "
                        "to scale replicas up or down on this endpoint."
                    ),
                }

            active_deploy_ops = self.get_active_deploy_operations(endpoint)
            if active_deploy_ops:
                stage = active_deploy_ops[0].get("deployment_stage", "DEPLOYING")
                started = active_deploy_ops[0].get("create_time", "recently")
                console_url = self.get_endpoint_console_url(endpoint.resource_name)
                logs_url = self.get_endpoint_logs_url(endpoint.resource_name)
                return {
                    "status": "deploying",
                    "endpoint_name": endpoint.resource_name,
                    "console_url": console_url,
                    "logs_url": logs_url,
                    "active_deploy_operations": active_deploy_ops,
                    "message": (
                        f"A deployment is already in progress on '{endpoint.resource_name}' "
                        f"(stage: {stage}, started: {started}). Skipping duplicate deployment. "
                        f"Monitor at {console_url} or check_af3_endpoint."
                    ),
                }

            model = self.get_model(model_id)
            hw_plan = resolve_af3_hardware_plan(
                project_id=self.config.project_id,
                region=self.config.endpoint_location,
                requested_machine_type=requested_machine_type,
                requested_accelerator_type=requested_acc_type,
                requested_accelerator_count=requested_acc_count,
                min_replicas=min_replicas,
                reservation_affinity_type=res_affinity_type or None,
                reservation_names=res_names or None,
                auto_fallback=auto_fallback_gpu,
            )

            candidates = hw_plan["candidates"]
            chosen_candidate = candidates[0]
            fallback_used = False
            fallback_reason: str | None = None
            last_deploy_exc: Exception | None = None

            for idx, cand in enumerate(candidates):
                c_machine = cand["machine_type"]
                c_acc_type = cand["accelerator_type"]
                c_acc_count = int(cand["accelerator_count"])
                c_res_type = cand.get("reservation_affinity_type")
                c_res_key = cand.get("reservation_affinity_key")
                c_res_vals = cand.get("reservation_affinity_values")

                deploy_kwargs: dict[str, Any] = {
                    "model": model,
                    "machine_type": c_machine,
                    "accelerator_type": c_acc_type,
                    "accelerator_count": c_acc_count,
                    "min_replica_count": min_replicas,
                    "max_replica_count": max_replicas,
                    "traffic_percentage": 100,
                    "sync": sync,
                }
                if c_res_type and (
                    res_affinity_type or res_names or c_res_type == "SPECIFIC_RESERVATION"
                ):
                    deploy_kwargs["reservation_affinity_type"] = c_res_type
                    if c_res_key:
                        deploy_kwargs["reservation_affinity_key"] = c_res_key
                    if c_res_vals:
                        deploy_kwargs["reservation_affinity_values"] = c_res_vals
                if spot:
                    deploy_kwargs["spot"] = True

                logger.info(
                    f"Deploying model {model.resource_name} to endpoint {endpoint.resource_name} "
                    f"({c_machine} + {c_acc_count}x {c_acc_type}, replicas={min_replicas}..{max_replicas}, "
                    f"reservation={c_res_type or 'DEFAULT'}, sync={sync})"
                )
                try:
                    endpoint.deploy(**deploy_kwargs)
                    chosen_candidate = cand
                    fallback_used = idx > 0
                    break
                except Exception as deploy_exc:
                    last_deploy_exc = deploy_exc
                    if (
                        auto_fallback_gpu
                        and idx + 1 < len(candidates)
                        and is_capacity_or_reservation_error(deploy_exc)
                    ):
                        next_cand = candidates[idx + 1]
                        fallback_reason = (
                            f"{c_machine} ({c_acc_type}) unavailable or requires reservation "
                            f"({deploy_exc}); falling back to {next_cand['machine_type']} "
                            f"({next_cand['accelerator_type']})"
                        )
                        logger.warning(f"[AF3 Deploy Fallback] {fallback_reason}")
                        continue
                    raise
            else:
                if last_deploy_exc is not None:
                    raise last_deploy_exc

            machine_type = chosen_candidate["machine_type"]
            acc_type = chosen_candidate["accelerator_type"]
            acc_count = int(chosen_candidate["accelerator_count"])
            unit_rate = float(chosen_candidate.get("hourly_rate", 11.06))
            hourly_rate = f"~${unit_rate * min_replicas:.2f}/hr"
            console_url = self.get_endpoint_console_url(endpoint.resource_name)
            logs_url = self.get_endpoint_logs_url(endpoint.resource_name)
            fallback_suffix = f" [Fallback applied: {fallback_reason}]" if fallback_used else ""
            if sync:
                return {
                    "status": "ready",
                    "endpoint_name": endpoint.resource_name,
                    "console_url": console_url,
                    "logs_url": logs_url,
                    "model_name": model.resource_name,
                    "requested_machine_type": requested_machine_type,
                    "machine_type": machine_type,
                    "accelerator_type": acc_type,
                    "accelerator_count": acc_count,
                    "min_replica_count": min_replicas,
                    "max_replica_count": max_replicas,
                    "fallback_used": fallback_used,
                    "fallback_reason": fallback_reason,
                    "reservation_status": hw_plan["reservation_note"],
                    "message": (
                        f"AlphaFold 3 model deployed successfully to {endpoint.resource_name}. "
                        f"Running on {machine_type} with {acc_count}x {acc_type} GPU "
                        f"({min_replicas}–{max_replicas} replica(s), {hourly_rate}; "
                        f"reservation: {hw_plan['reservation_note']}).{fallback_suffix} "
                        f"Endpoint Console: {console_url} | Logs: {logs_url}. "
                        "Remember to run undeploy_af3_endpoint when finished."
                    ),
                }
            else:
                return {
                    "status": "deploying",
                    "endpoint_name": endpoint.resource_name,
                    "console_url": console_url,
                    "logs_url": logs_url,
                    "model_name": model.resource_name,
                    "requested_machine_type": requested_machine_type,
                    "machine_type": machine_type,
                    "accelerator_type": acc_type,
                    "accelerator_count": acc_count,
                    "min_replica_count": min_replicas,
                    "max_replica_count": max_replicas,
                    "fallback_used": fallback_used,
                    "fallback_reason": fallback_reason,
                    "reservation_status": hw_plan["reservation_note"],
                    "estimated_wait_minutes": "5-8 minutes (L4) / 10-12 minutes (H100/A100 with 630 GB MSA bundle)",
                    "message": (
                        f"Deployment initiated for AlphaFold 3 on {machine_type} with {acc_count}x {acc_type} GPU "
                        f"({min_replicas}–{max_replicas} replica(s), {hourly_rate}; "
                        f"reservation: {hw_plan['reservation_note']}).{fallback_suffix} "
                        "Agent Platform takes ~5–8 minutes (inference-only) or ~10–12 minutes (with 630 GB MSA bundle). "
                        f"Monitor at {console_url} or check_af3_endpoint."
                    ),
                }

        except GoogleAPICallError as e:
            logger.exception(f"Agent Platform API error during AF3 endpoint deployment: {e}")
            return {
                "status": "error",
                "message": f"Agent Platform deployment failed: {e.message or str(e)}",
            }
        except Exception as e:
            logger.exception(f"Failed to deploy AF3 endpoint: {e}")
            return {
                "status": "error",
                "message": f"Failed to deploy AlphaFold 3 endpoint: {e!s}",
            }
