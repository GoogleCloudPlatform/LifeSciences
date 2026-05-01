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
# Agent Runtime Service Account & IAM
# ==============================================================================
resource "google_service_account" "agent_sa" {
  account_id   = "foldrun-agent-sa"
  display_name = "FoldRun Agent Service Account"
  project      = var.project_id
}

resource "google_project_iam_member" "agent_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/artifactregistry.reader",
    "roles/batch.jobsEditor",
    "roles/compute.instanceAdmin.v1",
    "roles/compute.viewer",
    "roles/file.viewer",
    "roles/logging.logWriter",
    "roles/run.developer",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/storage.bucketViewer",
    "roles/telemetry.writer",
  ])
  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.agent_sa.email}"
}

resource "google_service_account_iam_member" "build_sa_actas_agent" {
  service_account_id = google_service_account.agent_sa.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.foldrun_build.email}"
}

resource "google_storage_bucket_iam_member" "foldrun_agent_bucket_access" {
  bucket = google_storage_bucket.foldrun_bucket.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.agent_sa.email}"
}

resource "google_storage_bucket_iam_member" "foldrun_agent_databases_bucket_access" {
  bucket = google_storage_bucket.databases_bucket.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.agent_sa.email}"
}

# ==============================================================================
# Agent Runtime Deployment
# ==============================================================================
# Agent Runtime is deployed via Cloud Build (cloudbuild.yaml step 4) using the
# real FoldRun agent code, not via Terraform. Terraform only provisions the
# infrastructure (VPC, Filestore, buckets, IAM) that the agent depends on.
#
# To deploy the agent after terraform apply:
#   cd foldrun-agent && make deploy
# Or via Cloud Build:
#   ./deploy-all.sh PROJECT_ID --steps build
# ==============================================================================
