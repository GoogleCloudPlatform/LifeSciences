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

terraform {
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 7.28.0"
    }
  }
  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
}

module "agent_infra" {
  source = "git::https://github.com/GoogleCloudPlatform/LifeSciences.git//applications/pharma-on-gemini-enterprise/shared/terraform/agent-infrastructure?ref=main"

  project_id         = var.project_id
  region             = var.region
  agent_id           = "argus-agent"
  display_name       = "Argus — Life Sciences M&A Diligence"
  description        = "Due-diligence agent for pharma/biotech M&A. Answers quick questions about a potential acquisition, produces investment-grade acquisition whitepapers (PDF) on a named target, and recommends acquisition targets from a thesis. Draws on regulatory and scientific document corpora, SEC EDGAR financials, and web search. Use for acquisition screening, target due diligence, drug pipeline, clinical, regulatory, and financial assessment questions."
  logs_bucket_name   = var.logs_bucket_name
}


resource "google_project_service" "api" {
  for_each = toset([
    "discoveryengine.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

locals {
  create_artifact_bucket = var.artifact_bucket_name == null ? 1 : 0
  artifact_bucket_name   = var.artifact_bucket_name == null ? "${var.project_id}-argus-artifacts" : var.artifact_bucket_name
}

resource "google_storage_bucket" "artifacts" {
  count                       = local.create_artifact_bucket
  name                        = local.artifact_bucket_name
  project                     = var.project_id
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true

  depends_on = [google_project_service.api]
}

resource "google_storage_bucket_iam_member" "artifacts_agent_identity" {
  bucket = local.artifact_bucket_name
  role   = "roles/storage.objectUser"
  member = "principal://${module.agent_infra.agent_identity}"

  depends_on = [google_storage_bucket.artifacts]
}

