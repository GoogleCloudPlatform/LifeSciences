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

from . import (
    af2_analyzer,
    af3_analyzer,
    bindcraft_analyzer,
    boltz2_analyzer,
    boltzgen_analyzer,
    of3_analyzer,
    rfd4_proteina_analyzer,
)
from ._compat import download_json_from_gcs, validate_gcs_uri_in_bucket

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

_MODEL_TYPE_ALIASES: dict[str, str] = {
    "af2": "alphafold2",
    "alphafold2": "alphafold2",
    "af3": "alphafold3",
    "alphafold3": "alphafold3",
    "of3": "openfold3",
    "openfold3": "openfold3",
    "boltz": "boltz2",
    "boltz2": "boltz2",
    "boltzgen": "boltzgen",
    "rfd4-proteina": "rfd4_proteina",
    "rfd4_proteina": "rfd4_proteina",
    "rfd4": "rfd4_proteina",
    "proteina": "rfd4_proteina",
    "bindcraft": "bindcraft",
}


def normalize_model_type(raw: str | None) -> str | None:
    """Normalize model_type labels/env values to canonical analyzer names."""
    if not raw or not isinstance(raw, str):
        return None
    cleaned = raw.strip().lower()
    return _MODEL_TYPE_ALIASES.get(cleaned, cleaned)


def _auto_detect_model_type(task_config: dict, analysis_path: str = "") -> str | None:
    """Infer model_type from task_config fields when not explicitly supplied."""
    predictions = task_config.get("predictions", [])
    if not predictions:
        return None
    first_pred = predictions[0]
    uri = first_pred.get("uri", "")
    summary_uri = (
        first_pred.get("summary_uri")
        or first_pred.get("summary_confidences_uri")
        or first_pred.get("aggregated_uri", "")
    )
    if uri and uri.endswith(".pkl"):
        return "alphafold2"
    if (
        "summary_uri" in first_pred
        or "summary_confidences_uri" in first_pred
        or "summary_confidences" in summary_uri
        or "/af3_predictions/" in analysis_path
        or "instance_payload" in task_config
    ):
        return "alphafold3"
    if (
        "affinity_uri" in task_config
        or "query_yaml_uri" in task_config
        or "pde_uri" in first_pred
    ):
        return "boltz2"
    return "openfold3"


def main() -> None:
    """Main entry point for Cloud Run Job task."""
    task_index = int(os.getenv("CLOUD_RUN_TASK_INDEX", "0"))
    task_count = int(os.getenv("CLOUD_RUN_TASK_COUNT", "1"))

    logger.info(f"Task {task_index}/{task_count}: Starting unified FoldRun analysis")

    bucket_name = os.getenv("GCS_BUCKET")
    analysis_path = os.getenv("ANALYSIS_PATH")

    if not analysis_path:
        logger.error("ANALYSIS_PATH environment variable not set")
        sys.exit(1)

    if not bucket_name and analysis_path.startswith("gs://"):
        bucket_name = analysis_path[5:].split("/", 1)[0]

    if not bucket_name:
        logger.error("GCS_BUCKET environment variable not set")
        sys.exit(1)

    if not analysis_path.endswith("/"):
        analysis_path += "/"

    logger.info(f"Bucket: {bucket_name}, Analysis Path: {analysis_path}")

    try:
        validate_gcs_uri_in_bucket(analysis_path, bucket_name)
        task_config_uri = (
            os.getenv("TASK_CONFIG_URI") or f"{analysis_path}task_config.json"
        )
        validate_gcs_uri_in_bucket(task_config_uri, bucket_name)
        logger.info(f"Task {task_index}: Loading configuration from {task_config_uri}")
        task_config = download_json_from_gcs(
            task_config_uri, expected_bucket=bucket_name
        )
    except Exception as e:
        logger.error(f"Failed to load task configuration: {e}")
        sys.exit(1)

    raw_model_type = os.getenv("MODEL_TYPE") or task_config.get("model_type")
    model_type = normalize_model_type(raw_model_type)
    if not model_type:
        model_type = _auto_detect_model_type(task_config, analysis_path)

    if not model_type:
        logger.error("Could not determine or auto-detect model type")
        sys.exit(1)

    logger.info(f"Task {task_index}: Routed to parser for model type '{model_type}'")

    dispatch = {
        "alphafold2": af2_analyzer.run_task,
        "alphafold3": af3_analyzer.run_task,
        "openfold3": of3_analyzer.run_task,
        "boltz2": boltz2_analyzer.run_task,
        "boltzgen": boltzgen_analyzer.run_task,
        "rfd4_proteina": rfd4_proteina_analyzer.run_task,
        "bindcraft": bindcraft_analyzer.run_task,
    }

    runner = dispatch.get(model_type)
    if runner is None:
        logger.error(f"Unsupported model type: {model_type}")
        sys.exit(1)

    try:
        runner(task_index, task_count, task_config, bucket_name, analysis_path)
        sys.exit(0)
    except Exception as e:
        logger.error(f"Task {task_index} failed with exception: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
