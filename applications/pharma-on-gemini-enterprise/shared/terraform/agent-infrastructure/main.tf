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
  create_bucket    = var.logs_bucket_name == null ? 1 : 0
  bucket_name      = var.logs_bucket_name == null ? "${var.project_id}-${var.agent_id}-logs" : var.logs_bucket_name
  dummy_source_b64 = trimspace(file("${path.module}/dummy_source.b64"))
}

resource "google_project_service" "services" {
  for_each = toset([
    "agentregistry.googleapis.com",
    "aiplatform.googleapis.com",
    "storage.googleapis.com"
  ])

  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

resource "google_storage_bucket" "logs_data_bucket" {
  count                       = local.create_bucket
  project                     = var.project_id
  name                        = local.bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true

  depends_on = [google_project_service.services]
}

resource "google_vertex_ai_reasoning_engine" "agent" {
  display_name = var.display_name
  description  = var.description
  region       = var.region
  project      = var.project_id

  spec {
    identity_type = "AGENT_IDENTITY"

    deployment_spec {
      env {
        name  = "LOGS_BUCKET_NAME"
        value = local.bucket_name
      }

      # GOOGLE_CLOUD_PROJECT is reserved by Agent Runtime (the platform injects
      # it) and rejected in deployment_spec.env; GOOGLE_CLOUD_LOCATION is allowed.
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = "global"
      }

      env {
        name  = "GOOGLE_GENAI_USE_ENTERPRISE"
        value = "True"
      }

      env {
        name  = "OTEL_SERVICE_NAME"
        value = var.agent_id
      }

      env {
        name  = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"
        value = "NO_CONTENT"
      }

      env {
        name  = "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS"
        value = "false"
      }

      env {
        name  = "OTEL_SEMCONV_STABILITY_OPT_IN"
        value = "gen_ai_latest_experimental"
      }

      env {
        name  = "OTEL_INSTRUMENTATION_GENAI_UPLOAD_FORMAT"
        value = "jsonl"
      }

      env {
        name  = "OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK"
        value = "upload"
      }

      env {
        name  = "OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH"
        value = "gs://${local.bucket_name}/completions"
      }

      env {
        name  = "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY"
        value = "true"
      }
    }

    source_code_spec {
      inline_source {
        source_archive = local.dummy_source_b64
      }
      image_spec {}
    }
  }

  lifecycle {
    ignore_changes = [
      spec,
    ]
  }

  depends_on = [google_project_service.services]
}

resource "google_project_iam_member" "agent_identity" {
  for_each = toset([
    "roles/agentregistry.viewer",
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
