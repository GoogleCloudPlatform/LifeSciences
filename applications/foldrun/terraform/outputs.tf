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

output "foldrun_viewer_url" {
  description = "The URL of the FoldRun viewer Cloud Run service"
  value       = google_cloud_run_v2_service.foldrun_viewer.uri
}

output "gcs_bucket_name" {
  description = "The created GCS bucket"
  value       = google_storage_bucket.foldrun_bucket.name
}

output "filestore_id" {
  description = "The Filestore ID"
  value       = google_filestore_instance.foldrun_nfs.name
}

output "artifact_registry_repo" {
  description = "The Artifact Registry repository name"
  value       = google_artifact_registry_repository.foldrun_repo.name
}

output "agent_sa_email" {
  description = "The email of the Agent Service Account"
  value       = google_service_account.agent_sa.email
}

output "databases_bucket_name" {
  description = "GCS bucket for genetic database backups"
  value       = google_storage_bucket.databases_bucket.name
}

output "build_sa_email" {
  description = "The email of the Build Service Account"
  value       = google_service_account.foldrun_build.email
}

output "viewer_sa_email" {
  description = "The email of the Viewer Service Account"
  value       = google_service_account.foldrun_viewer.email
}

output "analysis_sa_email" {
  description = "The email of the Analysis Service Account"
  value       = google_service_account.foldrun_analysis.email
}

output "pipelines_sa_email" {
  description = "The email of the Pipelines Service Account"
  value       = google_service_account.pipelines.email
}

output "subnet_id" {
  description = "The resolved subnetwork ID (full path)"
  value       = local.subnet_id
}

output "network_id" {
  description = "The resolved network ID (full path)"
  value       = local.network_id
}

output "network_project_number" {
  description = "The resolved network project number"
  value       = local.network_project_number
}



