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

resource "google_service_account" "pipelines" {
  project      = var.project_id
  account_id   = "pipelines-sa"
  display_name = "Pipelines Service Account"
}

resource "google_service_account_iam_member" "agent_sa_actas_pipelines" {
  service_account_id = google_service_account.pipelines.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.agent_sa.email}"
}

resource "google_project_iam_member" "pipelines_roles" {
  for_each = toset([
    "roles/aiplatform.user",
  ])

  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.pipelines.email}"
}

# Initialize the default Agent Platform metadata store — required for pipeline jobs
# to record lineage and artifacts. Not auto-created on programmatic deployments.
resource "google_vertex_ai_metadata_store" "default" {
  provider = google-beta
  project  = var.project_id
  region   = var.region
  name     = "default"

  depends_on = [
    google_project_service.apis,
    time_sleep.service_agent_creation_sleep,
  ]
}

resource "google_storage_bucket_iam_member" "pipelines_foldrun_bucket" {
  bucket = google_storage_bucket.foldrun_bucket.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.pipelines.email}"
}

resource "google_artifact_registry_repository_iam_member" "pipelines_sa" {
  project    = google_artifact_registry_repository.foldrun_repo.project
  location   = google_artifact_registry_repository.foldrun_repo.location
  repository = google_artifact_registry_repository.foldrun_repo.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.pipelines.email}"
}

resource "google_pubsub_topic" "pipeline_status" {
  project = var.project_id
  name    = "foldrun-pipeline-status"
}

resource "google_pubsub_topic_iam_member" "pipelines_pubsub_publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.pipeline_status.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.pipelines.email}"
}