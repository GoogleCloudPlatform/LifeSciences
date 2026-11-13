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


resource "google_service_account" "foldrun_build" {
  project      = var.project_id
  account_id   = "foldrun-build-sa"
  display_name = "Foldrun Build Service Account"
}

resource "google_project_iam_member" "foldrun_build" {
  for_each = toset([
    "roles/aiplatform.user", # For deploying the agent(TODO: Custom role)
    "roles/artifactregistry.writer",
    "roles/compute.viewer", # GPU quota check on agent deploy
    "roles/logging.logWriter",
    "roles/run.developer",
    "roles/storage.objectViewer",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.foldrun_build.email}"
}


