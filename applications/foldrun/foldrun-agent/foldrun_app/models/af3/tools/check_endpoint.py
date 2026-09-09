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

                deployed_models_info.append(
                    {
                        "id": getattr(dm, "id", None),
                        "model": getattr(dm, "model", None),
                        "display_name": getattr(dm, "display_name", None),
                        "machine_type": machine_type,
                        "accelerator_type": acc_type,
                        "accelerator_count": acc_count,
                    }
                )

            is_ready = len(deployed_models_info) > 0

            if is_ready:
                first_model = deployed_models_info[0]
                m_type = first_model.get("machine_type") or "GPU"
                cost_estimate = (
                    "~$1.01/hr (L4)"
                    if "g2" in m_type or "L4" in str(first_model.get("accelerator_type"))
                    else "dedicated GPU billing active"
                )
                msg = (
                    f"AlphaFold 3 endpoint '{endpoint.display_name or endpoint.resource_name}' is active "
                    f"with {len(deployed_models_info)} deployed model(s) ({m_type}). "
                    f"Dedicated GPU billing is active ({cost_estimate}). "
                    "Run undeploy_af3_endpoint when your session is finished to avoid ongoing idle costs."
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
