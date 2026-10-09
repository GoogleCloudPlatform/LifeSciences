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

"""Tool for checking AlphaFold 3 Gemini platform Endpoint status and deployed models."""

import logging
from typing import Any

from foldrun_app.core.hardware import resolve_af3_hardware_plan

from ..base import AF3Tool

logger = logging.getLogger(__name__)


class AF3CheckEndpointTool(AF3Tool):
    """Inspects the health, deployment status, and hardware spec of the AF3 Gemini platform Endpoint."""

    @staticmethod
    def _explain_deployment_stage(stage: str) -> str:
        """Return user-facing guidance for a Gemini platform Endpoint deployment stage."""
        normalized = (stage or "").strip().upper()
        if normalized in ("STARTING_DEPLOYMENT", "PREPARING_MODEL", "CREATING_SERVING_CLUSTER"):
            return (
                f"Gemini platform is initializing the serving environment and GKE cluster ({normalized}). "
                "Do NOT cancel or resubmit."
            )
        if normalized == "PENDING_NODE_ALLOCATION":
            return (
                "The request is waiting in the Google Cloud cluster queue for GPU capacity "
                "(DWS queueing). If this persists for >10 minutes, the requested pool may be "
                "congested — check preemptible ('Custom model serving preemptible Nvidia H100 "
                "80GB GPUs per region') vs on-demand quotas."
            )
        if normalized in (
            "ADDING_NODES_TO_CLUSTER",
            "GETTING_CONTAINER_IMAGE",
            "STARTING_MODEL_SERVER",
            "FINISHING_UP",
        ):
            return (
                "Capacity has been secured! Google Cloud's cluster scheduler has successfully "
                f"granted the physical a3-highgpu-1g node (stage: {normalized}). The host is "
                "currently booting, pulling the container, and mounting/unpacking the 630 GB "
                "genetic databases onto the 3 TB local NVMe SSD. Do NOT cancel or resubmit."
            )
        if normalized in ("ACTIVE", "READY", "DEPLOYED", "SUCCESSFULLY_DEPLOYED"):
            return (
                "Container health check has passed (/v1/models/alphafold3:predict), and replica "
                "slots are open to process queued predictions."
            )
        if normalized == "DORMANT":
            return "Endpoint has 0 deployed models ($0.00/hr idle cost)."
        return (
            f"Endpoint deployment is in progress (stage: {stage}). "
            "Do NOT cancel or resubmit while provisioning completes."
        )

    def run(self, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Check status of the AlphaFold 3 Gemini platform Endpoint.

        Args:
            arguments: Optional dictionary with 'endpoint_id' override.

        Returns:
            Status summary including active deployed models, hardware, and cost state.
        """
        args = arguments or {}
        endpoint_id = args.get("endpoint_id")

        try:
            endpoint = self.get_endpoint(endpoint_id)

            deployed_models_info = []
            for dm in self.get_deployed_models(endpoint):
                dedicated = getattr(dm, "dedicated_resources", None)
                machine_spec = getattr(dedicated, "machine_spec", None)
                machine_type = getattr(machine_spec, "machine_type", None) if machine_spec else None
                acc_type = getattr(machine_spec, "accelerator_type", None) if machine_spec else None
                if acc_type and hasattr(acc_type, "name"):
                    acc_type = acc_type.name
                acc_count = (
                    getattr(machine_spec, "accelerator_count", None) if machine_spec else None
                )
                rep_info = self.extract_replica_counts(dm)

                deployed_models_info.append(
                    {
                        "id": getattr(dm, "id", None),
                        "model": getattr(dm, "model", None),
                        "display_name": getattr(dm, "display_name", None),
                        "machine_type": machine_type,
                        "accelerator_type": acc_type,
                        "accelerator_count": acc_count,
                        "min_replica_count": rep_info["min_replica_count"],
                        "max_replica_count": rep_info["max_replica_count"],
                        "initial_replica_count": rep_info["initial_replica_count"],
                        "scale_to_zero_enabled": rep_info["scale_to_zero_enabled"],
                        "min_scaleup_period_seconds": rep_info["min_scaleup_period_seconds"],
                        "idle_scaledown_period_seconds": rep_info["idle_scaledown_period_seconds"],
                    }
                )

            is_ready = len(deployed_models_info) > 0
            active_deploy_ops = self.get_active_deploy_operations(endpoint)
            console_url = self.get_endpoint_console_url(endpoint.resource_name)
            logs_url = self.get_endpoint_logs_url(endpoint.resource_name)

            if is_ready:
                status_str = "ready"
                deployment_stage = "ACTIVE"
                deployment_stage_explanation = self._explain_deployment_stage(deployment_stage)
                first_model = deployed_models_info[0]
                m_type = first_model.get("machine_type") or "GPU"
                acc_str = str(first_model.get("accelerator_type") or "")
                replicas = int(first_model.get("min_replica_count", 1))
                max_rep = int(first_model.get("max_replica_count") or max(1, replicas))
                init_rep = int(first_model.get("initial_replica_count") or 1)
                stz_enabled = bool(first_model.get("scale_to_zero_enabled", replicas == 0))
                idle_sec = int(first_model.get("idle_scaledown_period_seconds") or 1200)
                idle_min = max(1, idle_sec // 60)
                if "g2" in m_type or "L4" in acc_str:
                    unit_rate = 1.01
                    tier_label = "L4"
                elif "a3" in m_type or "H100" in acc_str:
                    unit_rate = 11.06
                    tier_label = "H100 80GB"
                elif "a2" in m_type or "A100" in acc_str:
                    unit_rate = 3.67
                    tier_label = "A100"
                else:
                    unit_rate = None
                    tier_label = "GPU"
                if stz_enabled:
                    if unit_rate is not None:
                        active_rate = unit_rate * max(1, init_rep)
                        cost_estimate = (
                            f"$0.00/hr when scaled to 0 (~${active_rate:.2f}/hr when active on "
                            f"{max(1, init_rep)}x {tier_label}; Scale-to-Zero after {idle_min}m idle, "
                            f"max_replicas={max_rep})"
                        )
                    else:
                        cost_estimate = (
                            f"$0.00/hr when scaled to 0 (Scale-to-Zero after {idle_min}m idle, "
                            f"max_replicas={max_rep})"
                        )
                elif unit_rate is not None:
                    total_rate = unit_rate * replicas
                    cost_estimate = (
                        f"~${total_rate:.2f}/hr ({replicas}x {tier_label}, max_replicas={max_rep})"
                    )
                else:
                    cost_estimate = f"dedicated GPU billing active ({replicas} replica(s))"
                msg = (
                    f"AlphaFold 3 endpoint '{endpoint.display_name or endpoint.resource_name}' is active "
                    f"with {len(deployed_models_info)} deployed model(s) ({m_type}, "
                    f"min_replicas={replicas}, max_replicas={max_rep}"
                    + (f", scale_to_zero=True [{idle_min}m idle]" if stz_enabled else "")
                    + "). "
                    f"Cost state: {cost_estimate}. "
                    f"Console: {console_url} | Logs: {logs_url}. "
                    "Replica counts are managed by Terraform (`af3_max_replica_count`)."
                )
            elif active_deploy_ops:
                status_str = "deploying"
                stage = active_deploy_ops[0].get("deployment_stage", "DEPLOYING")
                deployment_stage = stage
                deployment_stage_explanation = self._explain_deployment_stage(stage)
                started = active_deploy_ops[0].get("create_time", "recently")
                cost_estimate = "provisioning (~$11.06/hr once active)"
                msg = (
                    f"AlphaFold 3 endpoint '{endpoint.display_name or endpoint.resource_name}' is currently "
                    f"deploying (stage: {stage}, started: {started}). "
                    f"{deployment_stage_explanation} "
                    "Wait for the deployment to finish before submitting predictions. "
                    f"Monitor at {console_url} or {logs_url}."
                )
            else:
                status_str = "dormant"
                deployment_stage = "DORMANT"
                deployment_stage_explanation = self._explain_deployment_stage(deployment_stage)
                cost_estimate = "$0.00/hr"
                msg = (
                    f"AlphaFold 3 endpoint '{endpoint.display_name or endpoint.resource_name}' has no "
                    "deployed model. Model deployment is managed by Terraform: re-run the FoldRun "
                    "infrastructure step (`./deploy-all.sh <PROJECT_ID> <REGION> --steps infra --enable-af3`)."
                )

            hw_plan = resolve_af3_hardware_plan(
                project_id=self.config.project_id,
                region=self.config.endpoint_location,
                requested_machine_type=getattr(self.config, "machine_type", "a3-highgpu-1g"),
                requested_accelerator_type=getattr(
                    self.config, "accelerator_type", "NVIDIA_H100_80GB"
                ),
                requested_accelerator_count=int(getattr(self.config, "accelerator_count", 1)),
                reservation_affinity_type=getattr(self.config, "reservation_affinity_type", "")
                or None,
                reservation_names=getattr(self.config, "reservation_names", []) or None,
                auto_fallback=bool(getattr(self.config, "auto_fallback_gpu", True)),
            )

            queue_status = self.get_endpoint_queue_status(endpoint)
            scale_rec = queue_status.get("scale_up_recommendation")
            idle_teardown_advisory: str | None = None
            if scale_rec:
                msg += f" [Queue Advisory: {scale_rec['reason']} To scale up: {scale_rec['suggested_action']}.]"
            elif (
                is_ready
                and queue_status.get("active_slots_in_use", 0) == 0
                and queue_status.get("queued_jobs_waiting", 0) == 0
            ):
                if stz_enabled:
                    idle_teardown_advisory = (
                        f"All jobs in this batch are complete! Scale-to-Zero "
                        f"(min_replica_count=0, max_replica_count={max_rep}) is enabled and will "
                        f"automatically scale the endpoint to 0 replicas ($0.00/hr) after {idle_min} "
                        f"minutes of inactivity."
                    )
                else:
                    rep_label = f"{replicas} replica" if replicas == 1 else f"{replicas} replicas"
                    rate_str = (
                        f"~${unit_rate * replicas:.2f}/hr"
                        if unit_rate is not None
                        else "active GPU billing"
                    )
                    idle_teardown_advisory = (
                        f"All jobs in this batch are complete! The H100 endpoint is currently idling at "
                        f"{rate_str} ({rep_label}) because scale-to-zero is not enabled. Set "
                        "af3_min_replica_count = 0 in Terraform and re-run the infrastructure step to "
                        "stop idle billing."
                    )

            result: dict[str, Any] = {
                "status": status_str,
                "deployment_stage": deployment_stage,
                "deployment_stage_explanation": deployment_stage_explanation,
                "endpoint_name": endpoint.resource_name,
                "display_name": endpoint.display_name,
                "location": self.config.endpoint_location,
                "project_id": self.config.project_id,
                "console_url": console_url,
                "logs_url": logs_url,
                "deployed_models_count": len(deployed_models_info),
                "deployed_models": deployed_models_info,
                "idle_cost": cost_estimate,
                "msa_free_supported": True,
                "queue_status": queue_status,
                "scale_up_recommendation": scale_rec,
                "idle_teardown_advisory": idle_teardown_advisory,
                "hardware_readiness": {
                    "primary_machine_type": hw_plan["primary"]["machine_type"],
                    "primary_accelerator_type": hw_plan["primary"]["accelerator_type"],
                    "dws_preemptible_quota_metric": (
                        "aiplatform.googleapis.com/custom_model_serving_preemptible_nvidia_h100_80gb_gpus"
                    ),
                    "dws_preemptible_quota_display_name": (
                        "Custom model serving preemptible Nvidia H100 80GB GPUs per region"
                    ),
                    "hardware_allocation_strategy": [
                        "Tier 1: Active GCE Capacity Reservation (ANY_RESERVATION / SPECIFIC_RESERVATION)",
                        "Tier 2: DWS Preemptible / Spot Serving Pool (aiplatform.googleapis.com/custom_model_serving_preemptible_nvidia_h100_80gb_gpus)",
                        "Tier 3: Standard On-Demand Pool (Unreserved execution guarantee fallback)",
                    ],
                    "reservation_status": hw_plan["reservation_note"],
                    "active_reservations": hw_plan["reservations"],
                    "fallback_tiers": [
                        f"{c['machine_type']} ({c['accelerator_type']})"
                        for c in hw_plan["candidates"][1:]
                    ],
                },
                "message": msg,
            }
            if active_deploy_ops:
                result["active_deploy_operations"] = active_deploy_ops
            return result
        except Exception as e:
            logger.exception(f"Failed to check AF3 endpoint: {e}")
            return {
                "status": "error",
                "endpoint_id": endpoint_id or self.config.endpoint_id,
                "message": f"Failed to inspect AlphaFold 3 Gemini platform Endpoint: {e!s}",
            }
