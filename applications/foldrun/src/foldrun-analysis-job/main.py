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

"""Main entry point and orchestrator for the FoldRun Unified Analysis Job."""

import logging
import os
import sys

import af2_analyzer
import boltz2_analyzer
import of3_analyzer
from shared_utils import download_json_from_gcs

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def main():
    """Main entry point for Cloud Run Job task."""
    task_index = int(os.getenv("CLOUD_RUN_TASK_INDEX", "0"))
    task_count = int(os.getenv("CLOUD_RUN_TASK_COUNT", "1"))

    logger.info(f"Task {task_index}/{task_count}: Starting unified FoldRun analysis")

    bucket_name = os.getenv("GCS_BUCKET")
    analysis_path = os.getenv("ANALYSIS_PATH")

    if not bucket_name:
        logger.error("GCS_BUCKET environment variable not set")
        sys.exit(1)

    if not analysis_path:
        logger.error("ANALYSIS_PATH environment variable not set")
        sys.exit(1)

    logger.info(f"Bucket: {bucket_name}, Analysis Path: {analysis_path}")

    # Load task configuration
    task_config_uri = f"{analysis_path}task_config.json"
    logger.info(f"Task {task_index}: Loading configuration from {task_config_uri}")

    try:
        task_config = download_json_from_gcs(task_config_uri)
    except Exception as e:
        logger.error(f"Failed to load task configuration: {e}")
        sys.exit(1)

    # Determine model type (MODEL_TYPE env variable or task_config value or auto-detect)
    model_type = os.getenv("MODEL_TYPE")
    if not model_type:
        model_type = task_config.get("model_type")

    if not model_type:
        # Auto-detect based on configuration structure
        predictions = task_config.get("predictions", [])
        if predictions:
            first_pred = predictions[0]
            if "uri" in first_pred and first_pred["uri"].endswith(".pkl"):
                model_type = "alphafold2"
            elif (
                "affinity_uri" in task_config
                or "query_yaml_uri" in task_config
                or "pde_uri" in first_pred
            ):
                model_type = "boltz2"
            else:
                model_type = "openfold3"
        else:
            logger.error("Could not auto-detect model type: predictions list is empty")
            sys.exit(1)

    model_type = model_type.lower()
    logger.info(f"Task {task_index}: Routed to parser for model type '{model_type}'")

    try:
        if model_type in ["alphafold2", "af2"]:
            af2_analyzer.run_task(
                task_index, task_count, task_config, bucket_name, analysis_path
            )
        elif model_type in ["boltz2", "boltz"]:
            boltz2_analyzer.run_task(
                task_index, task_count, task_config, bucket_name, analysis_path
            )
        elif model_type in ["openfold3", "of3"]:
            of3_analyzer.run_task(
                task_index, task_count, task_config, bucket_name, analysis_path
            )
        else:
            logger.error(f"Unsupported model type: {model_type}")
            sys.exit(1)
        sys.exit(0)
    except Exception as e:
        logger.error(f"Task {task_index} failed with exception: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
