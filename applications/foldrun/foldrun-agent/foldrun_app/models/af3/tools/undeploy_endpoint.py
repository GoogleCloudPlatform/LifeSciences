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

"""Tool for undeploying AlphaFold 3 model(s) from a Vertex AI Endpoint (teardown GPU to $0/hr)."""

import logging
from typing import Any

from google.api_core.exceptions import GoogleAPICallError

from ..base import AF3Tool

logger = logging.getLogger(__name__)


class AF3UndeployEndpointTool(AF3Tool):
    """Undeploys models from the AF3 Vertex Endpoint, deallocating GPUs and reverting cost to $0/hr."""

    def run(self, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Undeploy models from the AlphaFold 3 Vertex AI Endpoint.

        Args:
            arguments: {
                'deployed_model_id': Optional specific deployed model ID. If omitted, undeploys all models.
                'endpoint_id': Optional Vertex AI Endpoint override,
                'sync': Whether to wait synchronously (default: True),
            }

        Returns:
            Dictionary with undeployment status and confirmation of zero idle cost.
        """
        args = arguments or {}
        endpoint_id = args.get("endpoint_id")
        deployed_model_id = args.get("deployed_model_id")
        sync = args.get("sync", True)

        try:
            endpoint = self.get_endpoint(endpoint_id)
            deployed_models = self.get_deployed_models(endpoint)

            if not deployed_models:
                return {
                    "status": "already_undeployed",
                    "endpoint_name": endpoint.resource_name,
                    "deployed_models_count": 0,
                    "idle_cost": "$0.00/hr",
                    "message": (
                        f"AlphaFold 3 endpoint '{endpoint.resource_name}' already has 0 deployed models. "
                        "Current idle cost is $0.00/hr."
                    ),
                }

            if deployed_model_id:
                logger.info(
                    f"Undeploying model ID {deployed_model_id} from {endpoint.resource_name}"
                )
                endpoint.undeploy(deployed_model_id=deployed_model_id, sync=sync)
                msg = f"Undeployed model '{deployed_model_id}' from AF3 endpoint."
            else:
                logger.info(f"Undeploying all models from {endpoint.resource_name}")
                endpoint.undeploy_all(sync=sync)
                msg = (
                    f"Successfully undeployed all models from AF3 endpoint '{endpoint.resource_name}'. "
                    "GPU resources have been released. Ongoing idle cost is now $0.00/hr."
                )

            return {
                "status": "succeeded",
                "endpoint_name": endpoint.resource_name,
                "deployed_models_count": 0
                if not deployed_model_id
                else max(0, len(deployed_models) - 1),
                "idle_cost": "$0.00/hr",
                "message": msg,
            }

        except GoogleAPICallError as e:
            logger.exception(f"Vertex AI API error during undeployment: {e}")
            return {
                "status": "error",
                "message": f"Vertex AI undeploy failed: {e.message or str(e)}",
            }
        except Exception as e:
            logger.exception(f"Failed to undeploy AF3 endpoint: {e}")
            return {
                "status": "error",
                "message": f"Failed to undeploy AlphaFold 3 endpoint: {e!s}",
            }
