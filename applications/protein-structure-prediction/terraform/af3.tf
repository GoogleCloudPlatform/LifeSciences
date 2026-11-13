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

# ==============================================================================
# AlphaFold 3 Model Garden Dedicated Endpoint (Optional: var.enable_af3 = true)
#
# Deploys `google/alphafold3@v3_0_4` from Gemini platform Model Garden
# to a Dedicated Endpoint (`af3-dedicated-ep`, `use_dedicated_endpoint=True`,
# `accept_eula=True`, `serving_container_deployment_timeout=3600`) and configures
# `clientConnectionConfig.inferenceTimeout.seconds = 3600`.
#
# Region alignment:
# - Defaults to `var.region` (e.g. `us-central1` or `us-west1`).
# - Can be overridden via `var.af3_endpoint_location` if H100 quota is in a
#   different region, and automatically guards against `"global"` (since Gemini platform
#   Prediction Endpoints are strictly regional resources).
# ==============================================================================
locals {
  af3_endpoint_location = (
    var.af3_endpoint_location != "" && lower(var.af3_endpoint_location) != "global"
    ? var.af3_endpoint_location
    : var.region
  )
}

resource "terraform_data" "af3_model_garden_endpoint" {
  count = var.enable_af3 ? 1 : 0

  triggers_replace = [
    var.project_id,
    local.af3_endpoint_location,
    var.af3_publisher_model_id,
    var.af3_endpoint_display_name,
    var.af3_model_display_name,
    var.af3_machine_type,
    var.af3_accelerator_type,
    tostring(var.af3_accelerator_count),
    var.af3_capacity_pool,
    tostring(var.af3_use_dws),
    tostring(var.af3_dws_max_runtime_seconds),
    var.af3_reservation_affinity_type,
    tostring(var.af3_min_replica_count),
    tostring(var.af3_max_replica_count),
    tostring(var.af3_initial_replica_count),
    tostring(var.af3_min_scaleup_period_seconds),
    tostring(var.af3_idle_scaledown_period_seconds),
    tostring(var.af3_inference_timeout_seconds),
  ]

  provisioner "local-exec" {
    command = <<-EOT
      uv run --project "${path.module}/../foldrun-agent" \
        python "${path.module}/../foldrun-agent/scripts/setup_af3_endpoint.py" \
        --project-id "${var.project_id}" \
        --location "${local.af3_endpoint_location}" \
        --model-id "${var.af3_publisher_model_id}" \
        --endpoint-display-name "${var.af3_endpoint_display_name}" \
        --model-display-name "${var.af3_model_display_name}" \
        --machine-type "${var.af3_machine_type}" \
        --accelerator-type "${var.af3_accelerator_type}" \
        --accelerator-count "${var.af3_accelerator_count}" \
        --capacity-pool "${var.af3_capacity_pool}" \
        ${var.af3_use_dws ? "--dws" : "--no-dws"} \
        --dws-max-runtime-seconds "${var.af3_dws_max_runtime_seconds}" \
        --reservation-affinity-type "${var.af3_reservation_affinity_type}" \
        --min-replica-count "${var.af3_min_replica_count}" \
        --max-replica-count "${var.af3_max_replica_count}" \
        --initial-replica-count "${var.af3_initial_replica_count}" \
        --min-scaleup-period "${var.af3_min_scaleup_period_seconds}" \
        --idle-scaledown-period "${var.af3_idle_scaledown_period_seconds}" \
        --inference-timeout "${var.af3_inference_timeout_seconds}" \
        --bucket-name "${google_storage_bucket.foldrun_bucket.name}" \
        ${var.af3_undeploy_after_setup ? "--undeploy-after-setup" : ""}
    EOT
  }

  depends_on = [
    google_project_service.apis,
    google_project_service_identity.aiplatform_sa,
    time_sleep.service_agent_creation_sleep,
    google_storage_bucket_iam_member.aiplatform_service_agents_foldrun_bucket,
  ]
}
