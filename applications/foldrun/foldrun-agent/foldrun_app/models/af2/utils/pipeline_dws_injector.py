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

"""Utility to inject DWS scheduling into compiled pipeline specs."""

import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def inject_dws_scheduling(pipeline_spec_path: str) -> None:
    """
    Inject DWS FLEX_START scheduling into compiled pipeline spec.

    Modifies the pipeline JSON in-place to add scheduling strategy
    to all CustomJob worker pool specs.

    Args:
        pipeline_spec_path: Path to compiled pipeline JSON file
    """
    logger.info(f"Injecting DWS FLEX_START into pipeline spec: {pipeline_spec_path}")

    # Load the compiled pipeline spec
    with open(pipeline_spec_path, "r") as f:
        pipeline_spec = json.load(f)

    # Navigate to deployment config
    deployment_config = pipeline_spec.get("deploymentSpec", {})
    executors = deployment_config.get("executors", {})

    modified_count = 0

    # Iterate through all executors (CustomJobs)
    for executor_name, executor_spec in executors.items():
        if "container" in executor_spec:
            # This is a ContainerExecution (CustomJob)
            # Check if it has workerPoolSpecs
            custom_job = executor_spec.get("container", {})

            # The worker pool specs are in the executor's custom job template
            # We need to add scheduling at the right level
            logger.debug(f"Processing executor: {executor_name}")
            logger.debug(f"Executor spec keys: {executor_spec.keys()}")

            # Add scheduling to the executor
            # Note: This may need adjustment based on actual KFP structure
            if "container" in executor_spec:
                # Add scheduling parameter marker
                # KFP will need to translate this to the actual CustomJob spec
                executor_spec["scheduling"] = {"strategy": "FLEX_START"}
                modified_count += 1
                logger.info(f"Added DWS scheduling to executor: {executor_name}")

    if modified_count > 0:
        # Write back the modified spec
        with open(pipeline_spec_path, "w") as f:
            json.dump(pipeline_spec, f, indent=2)

        logger.info(f"Successfully injected DWS scheduling into {modified_count} executors")
    else:
        logger.warning("No executors found to modify. DWS scheduling not added.")


def inject_dws_into_runtime_config(pipeline_job_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Alternative approach: Inject DWS at the PipelineJob level via runtime config.

    This modifies the pipeline job dictionary before submission.

    Args:
        pipeline_job_dict: Dictionary representation of PipelineJob

    Returns:
        Modified pipeline job dictionary
    """
    # This approach would require modifying the actual Agent Platform API request
    # which is harder with the high-level SDK

    # For now, this is a placeholder for potential future implementation
    logger.warning("Runtime config injection not yet implemented")
    return pipeline_job_dict
