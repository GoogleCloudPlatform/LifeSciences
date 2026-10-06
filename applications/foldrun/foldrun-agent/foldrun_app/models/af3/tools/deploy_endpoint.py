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
            self.config, "machine_type", "a3-highgpu-1g"
        )
        acc_type = args.get("accelerator_type") or getattr(
            self.config, "accelerator_type", "NVIDIA_H100_80GB"
        )
        acc_count = int(
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

        try:
            endpoint = self.get_endpoint(endpoint_id)

            # Check if a model is already deployed
            deployed_models = getattr(endpoint, "deployed_models", [])
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
                    unit_rate = 11.06 if "a3" in machine_type or "H100" in acc_type else 1.01
                    scaled_rate = f"~${unit_rate * min_replicas:.2f}/hr"
                    return {
                        "status": "scaled" if sync else "scaling",
                        "endpoint_name": endpoint.resource_name,
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
                    "deployed_models_count": len(deployed_models),
                    "min_replica_count": cur_min,
                    "max_replica_count": cur_max,
                    "message": (
                        f"Endpoint already has {len(deployed_models)} active deployed model(s) "
                        f"(min_replicas={cur_min}, max_replicas={cur_max}). "
                        "AF3 is ready for predictions. Pass min_replica_count / max_replica_count "
                        "to scale replicas up or down on this endpoint."
                    ),
                }

            model = self.get_model(model_id)

            logger.info(
                f"Deploying model {model.resource_name} to endpoint {endpoint.resource_name} "
                f"({machine_type} + {acc_count}x {acc_type}, replicas={min_replicas}..{max_replicas}, sync={sync})"
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

            unit_rate = 11.06 if "a3" in machine_type or "H100" in acc_type else 1.01
            hourly_rate = f"~${unit_rate * min_replicas:.2f}/hr"
            if sync:
                return {
                    "status": "ready",
                    "endpoint_name": endpoint.resource_name,
                    "model_name": model.resource_name,
                    "machine_type": machine_type,
                    "accelerator_type": acc_type,
                    "accelerator_count": acc_count,
                    "min_replica_count": min_replicas,
                    "max_replica_count": max_replicas,
                    "message": (
                        f"AlphaFold 3 model deployed successfully to {endpoint.resource_name}. "
                        f"Running on {machine_type} with {acc_count}x {acc_type} GPU "
                        f"({min_replicas}–{max_replicas} replica(s), {hourly_rate}). "
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
                    "min_replica_count": min_replicas,
                    "max_replica_count": max_replicas,
                    "estimated_wait_minutes": "5-8 minutes (L4) / 10-12 minutes (H100 with 630 GB MSA bundle)",
                    "message": (
                        f"Deployment initiated for AlphaFold 3 on {machine_type} with {acc_count}x {acc_type} GPU "
                        f"({min_replicas}–{max_replicas} replica(s), {hourly_rate}). "
                        "Agent Platform takes ~5–8 minutes (inference-only) or ~10–12 minutes (with 630 GB MSA bundle on H100 NVMe SSD). "
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
