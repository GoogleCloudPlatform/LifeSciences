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

  project_id       = var.project_id
  region           = var.region
  agent_id         = "biocompass-agent"
  display_name     = "BioCompass on Gemini Enterprise"
  description      = "Biomedical literature research assistant for pharma R&D, medical affairs, and clinical / HEOR teams. Searches PubMed + Europe PMC + bioRxiv/medRxiv + ClinicalTrials.gov in parallel, looks up small-molecule chemistry via PubChem, extracts entities + relationships via PubTator3, renders publication-style figures with Nano Banana Pro, and orchestrates seven pharma methodology skills (PICO, PRISMA, MoA, target dossier, competitive scan, safety signal scan, AOP construction)."
  logs_bucket_name = var.logs_bucket_name
}

resource "google_project_service" "api" {
  for_each = toset([
    "bigquery.googleapis.com",
    "bigqueryconnection.googleapis.com"
  ])
  project = var.project_id
  service = each.value
  disable_on_destroy = false
}

resource "google_bigquery_connection" "cloud_resource" {
  connection_id = "cloud_resource_connection"
  project       = var.project_id
  location      = var.region
  friendly_name = "cloud-resource-connection"
  description   = "BigQuery Connection for Agent Platform remote models"
  cloud_resource {}
  depends_on = [google_project_service.api]
}

resource "google_bigquery_connection_iam_member" "member" {
  project       = google_bigquery_connection.cloud_resource.project
  location      = google_bigquery_connection.cloud_resource.location
  connection_id = google_bigquery_connection.cloud_resource.connection_id
  role          = "roles/bigquery.connectionUser"
  member        = "principal://${module.agent_infra.agent_identity}"
}

resource "google_project_iam_member" "connection_agent_platform_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_bigquery_connection.cloud_resource.cloud_resource[0].service_account_id}"
  depends_on = [google_bigquery_connection.cloud_resource]
}

resource "google_bigquery_dataset" "remote_models" {
  dataset_id                 = "remote_models"
  project                    = var.project_id
  location                   = var.region
  friendly_name              = "Remote Models"
  description                = "Remote models to Agent Platform"
  delete_contents_on_destroy = true
  depends_on = [google_project_service.api]
}

locals {
  create_model_query = <<EOF
CREATE OR REPLACE MODEL `${var.project_id}.${google_bigquery_dataset.remote_models.dataset_id}.text_embedding_model`
REMOTE WITH CONNECTION `${var.project_id}.${var.region}.${google_bigquery_connection.cloud_resource.connection_id}`
OPTIONS (ENDPOINT = 'text-embedding-005');
EOF
}

resource "google_bigquery_job" "create_embedding_model" {
  job_id   = "create_text_embedding_model_job_${md5(local.create_model_query)}"
  project  = var.project_id
  location = var.region
  query {
    query              = local.create_model_query
    create_disposition = ""
    write_disposition  = ""
  }
  depends_on = [
    google_project_iam_member.connection_agent_platform_user,
    google_bigquery_dataset.remote_models
  ]
}

resource "google_project_iam_member" "agent_identity" {
  for_each = toset([
    "roles/bigquery.jobUser",
  ])
  project = var.project_id
  role    = each.value
  member  = "principal://${module.agent_infra.agent_identity}"
}