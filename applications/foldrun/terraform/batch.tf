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

resource "google_service_account" "batch_compute" {
  project      = var.project_id
  account_id   = "batch-compute-sa"
  display_name = "Batch Compute Service Account"
}

resource "google_service_account_iam_member" "agent_sa_actas_compute" {
  service_account_id = google_service_account.batch_compute.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.agent_sa.email}"
}

resource "google_project_iam_member" "batch_compute_roles" {
  for_each = toset([
    "roles/batch.agentReporter",
    "roles/logging.logWriter",
    "roles/storage.bucketViewer",
  ])

  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.batch_compute.email}"
}

resource "google_storage_bucket_iam_member" "batch_compute_databases_bucket" {
  bucket = google_storage_bucket.databases_bucket.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.batch_compute.email}"
}