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

locals {
  create_bucket = var.logs_bucket_name == null ? 1 : 0
  bucket_name   = var.logs_bucket_name == null ? "${var.project_id}-${var.agent_id}-logs" : var.logs_bucket_name
}

resource "google_storage_bucket" "logs_data_bucket" {
  count                       = local.create_bucket
  project                     = var.project_id
  name                        = local.bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
}

resource "google_vertex_ai_reasoning_engine" "agent" {
  display_name = var.display_name
  description  = var.description
  region       = var.region
  project      = var.project_id

  spec {
    identity_type = "AGENT_IDENTITY"
  }

  lifecycle {
    ignore_changes = [
      spec,
    ]
  }
}

resource "google_project_iam_member" "agent_identity" {
  for_each = toset([
    "roles/aiplatform.expressUser", 
    "roles/browser",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/telemetry.writer"
  ])

  project = var.project_id
  role    = each.key
  member  = "principal://${google_vertex_ai_reasoning_engine.agent.spec[0].effective_identity}"
}

resource "google_storage_bucket_iam_member" "logs_agent_identity" {
  bucket = local.bucket_name
  role   = "roles/storage.objectUser"
  member = "principal://${google_vertex_ai_reasoning_engine.agent.spec[0].effective_identity}"
}
