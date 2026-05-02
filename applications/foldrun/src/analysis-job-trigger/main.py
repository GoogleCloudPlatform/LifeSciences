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

import os
import base64
import json
import logging
import asyncio
from datetime import datetime
import functions_framework.aio
from google.cloud import storage
from google.cloud import run_v2

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID")
REGION = os.environ.get("REGION", "us-central1")

storage_client = storage.Client()

def parse_gcs_uri(uri: str) -> tuple:
    path_parts = uri.replace("gs://", "").split("/", 1)
    return path_parts[0], path_parts[1] if len(path_parts) > 1 else ""

def _discover_af2_predictions(pipeline_root: str) -> list:
    bucket_name, prefix = parse_gcs_uri(pipeline_root)
    if not prefix.endswith("/"):
        prefix += "/"
    bucket = storage_client.bucket(bucket_name)
    predictions = []
    for blob in bucket.list_blobs(prefix=prefix):
        if blob.name.endswith("/raw_prediction.pkl"):
            uri = f"gs://{bucket_name}/{blob.name}"
            parent = blob.name.split("/")[-2]
            predictions.append({"uri": uri, "model_name": parent, "ranking_confidence": 0})
    return predictions

def _discover_of3_predictions(pipeline_root: str) -> list:
    bucket_name, prefix = parse_gcs_uri(pipeline_root)
    if not prefix.endswith("/"):
        prefix += "/"
    bucket = storage_client.bucket(bucket_name)
    predictions = []
    seen = set()
    for blob in bucket.list_blobs(prefix=prefix):
        name = blob.name
        if name.endswith("_confidences_aggregated.json") and name not in seen:
            seen.add(name)
            base = name.replace("_confidences_aggregated.json", "")
            sample_name = name.split("/")[-1].replace("_confidences_aggregated.json", "")
            predictions.append(
                {
                    "cif_uri": f"gs://{bucket_name}/{base}_model.cif",
                    "confidences_uri": f"gs://{bucket_name}/{base}_confidences.json",
                    "aggregated_uri": f"gs://{bucket_name}/{name}",
                    "sample_name": sample_name,
                }
            )
    return predictions

def _discover_boltz2_predictions(pipeline_root: str) -> list:
    bucket_name, prefix = parse_gcs_uri(pipeline_root)
    if not prefix.endswith("/"):
        prefix += "/"
    bucket = storage_client.bucket(bucket_name)
    predictions = []
    seen = set()
    for blob in bucket.list_blobs(prefix=prefix):
        name = blob.name
        if "/predictions/" not in name or not name.endswith(".json"):
            continue
        parts_list = name.split("/")
        filename = parts_list[-1]
        if filename.startswith("confidence_") and name not in seen:
            seen.add(name)
            base_dir = "/".join(parts_list[:-1])
            base_name = filename.replace("confidence_", "").replace(".json", "")
            predictions.append(
                {
                    "sample_name": base_name,
                    "cif_uri": f"gs://{bucket_name}/{base_dir}/model_{base_name}.cif",
                    "confidences_uri": f"gs://{bucket_name}/{name}",
                    "aggregated_uri": f"gs://{bucket_name}/{name}",
                }
            )
    return predictions

def write_gcs_configs(analysis_path, task_config, raw_predictions, job_id, model_type):
    tc_bucket_name, tc_blob_path = parse_gcs_uri(f"{analysis_path}task_config.json")
    tc_bucket = storage_client.bucket(tc_bucket_name)

    tc_bucket.blob(tc_blob_path).upload_from_string(
        json.dumps(task_config, indent=2), content_type="application/json"
    )
    tc_bucket.blob(tc_blob_path.replace("task_config.json", "analysis_metadata.json")).upload_from_string(
        json.dumps(
            {
                "job_id": job_id,
                "total_predictions": len(raw_predictions),
                "started_at": datetime.utcnow().isoformat() + "Z",
                "status": "running",
                "model_type": model_type,
                "execution_method": "cloud_run_job",
                "triggered_by": "analysis-job-trigger-service",
            },
            indent=2,
        ),
        content_type="application/json",
    )

async def trigger_cloud_run_job(cr_job_name, analysis_path, raw_predictions, job_id):
    client = run_v2.JobsAsyncClient()
    job_path = f"projects/{PROJECT_ID}/locations/{REGION}/jobs/{cr_job_name}"
    
    container_override = run_v2.RunJobRequest.Overrides.ContainerOverride(
        env=[run_v2.EnvVar(name="ANALYSIS_PATH", value=analysis_path)]
    )
    overrides = run_v2.RunJobRequest.Overrides(
        container_overrides=[container_override],
        task_count=len(raw_predictions)
    )
    request = run_v2.RunJobRequest(
        name=job_path,
        overrides=overrides
    )
    await client.run_job(request=request)
    logger.info(f"Triggered analysis for {job_id}: {len(raw_predictions)} tasks, job={cr_job_name}")

@functions_framework.aio.cloud_event
async def trigger_analysis(cloud_event):
    """Receive and process Eventarc events from Pub/Sub."""
    try:
        event_data = cloud_event.data
        pubsub_message = event_data.get("message", {})
        
        if not pubsub_message or "data" not in pubsub_message:
            logger.error("No data in Pub/Sub message payload.")
            return
            
        data_str = base64.b64decode(pubsub_message["data"]).decode("utf-8").strip()
        payload = json.loads(data_str)
    except Exception as e:
        logger.error(f"Failed to parse CloudEvent/PubSub message: {e}", exc_info=True)
        return

    state = payload.get("state")
    model_type = payload.get("model_name")
    job_id = payload.get("job_id")
    gcs_output_dir = payload.get("gcs_output_dir")

    logger.info(f"Received event for {model_type} job {job_id}, state: {state}")

    if state != "SUCCEEDED":
        logger.info(f"Pipeline job {job_id} finished with state {state}, ignoring.")
        return

    if not gcs_output_dir:
        logger.error("No gcs_output_dir provided in message.")
        return

    if not gcs_output_dir.endswith("/"):
        gcs_output_dir += "/"

    analysis_path = f"{gcs_output_dir}analysis/"

    try:
        if model_type == "openfold3":
            raw_predictions = await asyncio.to_thread(_discover_of3_predictions, gcs_output_dir)
            cr_job_name = os.environ.get("OF3_ANALYSIS_JOB_NAME", "of3-analysis-job")
        elif model_type == "boltz2":
            raw_predictions = await asyncio.to_thread(_discover_boltz2_predictions, gcs_output_dir)
            cr_job_name = os.environ.get("BOLTZ2_ANALYSIS_JOB_NAME", "boltz2-analysis-job")
        else:
            raw_predictions = await asyncio.to_thread(_discover_af2_predictions, gcs_output_dir)
            cr_job_name = os.environ.get("AF2_ANALYSIS_JOB_NAME", "af2-analysis-job")

        if not raw_predictions:
            logger.warning(f"No prediction outputs found for job {job_id}")
            return

        if model_type == "alphafold2":
            predictions_cfg = [
                {
                    "index": i,
                    "uri": p["uri"],
                    "model_name": p["model_name"],
                    "ranking_confidence": p["ranking_confidence"],
                    "output_uri": f"{analysis_path}prediction_{i}_analysis.json",
                }
                for i, p in enumerate(raw_predictions)
            ]
        elif model_type == "openfold3":
            predictions_cfg = [
                {
                    "index": i,
                    "cif_uri": p["cif_uri"],
                    "confidences_uri": p["confidences_uri"],
                    "aggregated_uri": p["aggregated_uri"],
                    "sample_name": p["sample_name"],
                    "output_uri": f"{analysis_path}prediction_{i}_analysis.json",
                }
                for i, p in enumerate(raw_predictions)
            ]
        else:  # boltz2
            predictions_cfg = [
                {
                    "index": i,
                    "sample_name": p["sample_name"],
                    "cif_uri": p["cif_uri"],
                    "confidences_uri": p["confidences_uri"],
                    "aggregated_uri": p["aggregated_uri"],
                    "output_uri": f"{analysis_path}prediction_{i}_analysis.json",
                }
                for i, p in enumerate(raw_predictions)
            ]

        task_config = {
            "job_id": job_id,
            "analysis_path": analysis_path,
            "task_config_uri": f"{analysis_path}task_config.json",
            "predictions": predictions_cfg,
        }

        await asyncio.to_thread(write_gcs_configs, analysis_path, task_config, raw_predictions, job_id, model_type)
        await trigger_cloud_run_job(cr_job_name, analysis_path, raw_predictions, job_id)

    except Exception as e:
        logger.error(f"Error triggering analysis for {job_id}: {e}", exc_info=True)
