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

"""Tool for checking AlphaFold 3 Vertex AI Endpoint status and deployed models."""

import logging
from typing import Any

from ..base import AF3Tool

logger = logging.getLogger(__name__)


class AF3CheckEndpointTool(AF3Tool):
    """Inspects the health, deployment status, and hardware spec of the AF3 Vertex Endpoint."""

    def run(self, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Check status of the AlphaFold 3 Vertex AI Endpoint.

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
            for dm in getattr(endpoint, "deployed_models", []):
                dedicated = getattr(dm, "dedicated_resources", None)
                machine_spec = getattr(dedicated, "machine_spec", None)
                machine_type = getattr(machine_spec, "machine_type", None) if machine_spec else None
                acc_type = getattr(machine_spec, "accelerator_type", None) if machine_spec else None
                if acc_type and hasattr(acc_type, "name"):
                    acc_type = acc_type.name
                acc_count = (
                    getattr(machine_spec, "accelerator_count", None) if machine_spec else None
                )
                min_replicas = (
                    getattr(dedicated, "min_replica_count", None) if dedicated else None
                ) or 1
                max_replicas = (
                    getattr(dedicated, "max_replica_count", None) if dedicated else None
                ) or min_replicas

                deployed_models_info.append(
                    {
                        "id": getattr(dm, "id", None),
                        "model": getattr(dm, "model", None),
                        "display_name": getattr(dm, "display_name", None),
                        "machine_type": machine_type,
                        "accelerator_type": acc_type,
                        "accelerator_count": acc_count,
                        "min_replica_count": int(min_replicas),
                        "max_replica_count": int(max_replicas),
                    }
                )

            is_ready = len(deployed_models_info) > 0

            if is_ready:
                first_model = deployed_models_info[0]
                m_type = first_model.get("machine_type") or "GPU"
                acc_str = str(first_model.get("accelerator_type") or "")
                replicas = int(first_model.get("min_replica_count") or 1)
                max_rep = int(first_model.get("max_replica_count") or replicas)
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
                if unit_rate is not None:
                    total_rate = unit_rate * replicas
                    cost_estimate = (
                        f"~${total_rate:.2f}/hr ({replicas}x {tier_label}, max_replicas={max_rep})"
                    )
                else:
                    cost_estimate = f"dedicated GPU billing active ({replicas} replica(s))"
                msg = (
                    f"AlphaFold 3 endpoint '{endpoint.display_name or endpoint.resource_name}' is active "
                    f"with {len(deployed_models_info)} deployed model(s) ({m_type}, "
                    f"min_replicas={replicas}, max_replicas={max_rep}). "
                    f"Dedicated GPU billing is active ({cost_estimate}). "
                    "To scale replicas for a large backlog (>30 min queue), call deploy_af3_endpoint "
                    "with min_replica_count/max_replica_count. Run undeploy_af3_endpoint when your "
                    "session is finished to return to $0.00/hr."
                )
            else:
                cost_estimate = "$0.00/hr"
                msg = (
                    f"AlphaFold 3 endpoint '{endpoint.display_name or endpoint.resource_name}' is dormant "
                    f"with 0 deployed models ($0.00/hr idle cost). "
                    "Call deploy_af3_endpoint to allocate GPU resources before submitting predictions."
                )

            return {
                "status": "ready" if is_ready else "dormant",
                "endpoint_name": endpoint.resource_name,
                "display_name": endpoint.display_name,
                "location": self.config.endpoint_location,
                "project_id": self.config.project_id,
                "deployed_models_count": len(deployed_models_info),
                "deployed_models": deployed_models_info,
                "idle_cost": cost_estimate,
                "msa_free_supported": True,
                "message": msg,
            }
        except Exception as e:
            logger.exception(f"Failed to check AF3 endpoint: {e}")
            return {
                "status": "error",
                "endpoint_id": endpoint_id or self.config.endpoint_id,
                "message": f"Failed to inspect AlphaFold 3 Vertex Endpoint: {e!s}",
            }
