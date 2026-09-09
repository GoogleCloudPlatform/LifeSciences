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
from typing import Any

from google.api_core.exceptions import GoogleAPICallError

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
                'machine_type': Machine type (default: 'g2-standard-16'),
                'accelerator_type': Accelerator type (default: 'NVIDIA_L4'),
                'accelerator_count': GPU count (default: 1),
                'min_replica_count': Minimum replicas (default: 1),
                'max_replica_count': Maximum replicas (default: 1),
                'traffic_percentage': Percentage of traffic (default: 100),
                'sync': Whether to wait synchronously for deployment completion (default: False),
            }

        Returns:
            Dictionary with deployment status, endpoint info, and estimated spin-up time.
        """
        args = arguments or {}
        endpoint_id = args.get("endpoint_id")
        model_id = args.get("model_id") or getattr(self.config, "model_id", "")
        machine_type = args.get("machine_type") or getattr(
            self.config, "machine_type", "g2-standard-16"
        )
        acc_type = args.get("accelerator_type") or getattr(
            self.config, "accelerator_type", "NVIDIA_L4"
        )
        acc_count = int(
            args.get("accelerator_count") or getattr(self.config, "accelerator_count", 1)
        )
        min_replicas = int(args.get("min_replica_count", 1))
        max_replicas = int(args.get("max_replica_count", 1))
        sync = args.get("sync", False)

        try:
            endpoint = self.get_endpoint(endpoint_id)

            # Check if a model is already deployed
            deployed_models = getattr(endpoint, "deployed_models", [])
            if deployed_models:
                return {
                    "status": "already_deployed",
                    "endpoint_name": endpoint.resource_name,
                    "deployed_models_count": len(deployed_models),
                    "message": (
                        f"Endpoint already has {len(deployed_models)} active deployed model(s). "
                        "AF3 is ready for predictions."
                    ),
                }

            model = self.get_model(model_id)

            logger.info(
                f"Deploying model {model.resource_name} to endpoint {endpoint.resource_name} "
                f"({machine_type} + {acc_count}x {acc_type}, sync={sync})"
            )

            endpoint.deploy(
                model=model,
                machine_type=machine_type,
                accelerator_type=acc_type,
                accelerator_count=acc_count,
                min_replica_count=min_replicas,
                max_replica_count=max_replicas,
                traffic_percentage=100,
                sync=sync,
            )

            if sync:
                return {
                    "status": "ready",
                    "endpoint_name": endpoint.resource_name,
                    "model_name": model.resource_name,
                    "machine_type": machine_type,
                    "accelerator_type": acc_type,
                    "accelerator_count": acc_count,
                    "message": (
                        f"AlphaFold 3 model deployed successfully to {endpoint.resource_name}. "
                        f"Running on {machine_type} with {acc_count}x {acc_type} GPU (~$1.01/hr). "
                        "Endpoint is ready for predictions. Remember to run undeploy_af3_endpoint when finished."
                    ),
                }
            else:
                return {
                    "status": "deploying",
                    "endpoint_name": endpoint.resource_name,
                    "model_name": model.resource_name,
                    "machine_type": machine_type,
                    "accelerator_type": acc_type,
                    "accelerator_count": acc_count,
                    "estimated_wait_minutes": "5-8 minutes",
                    "message": (
                        f"Deployment initiated for AlphaFold 3 on {machine_type} with {acc_count}x {acc_type} GPU. "
                        "Agent Platform typically takes 5–8 minutes to provision GPU nodes and load model weights. "
                        "Use check_af3_endpoint to monitor status."
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
