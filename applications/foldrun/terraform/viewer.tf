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
# Cloud Run Service (Viewer)
# ==============================================================================
resource "google_service_account" "foldrun_viewer" {
  account_id   = "foldrun-viewer-sa"
  display_name = "FoldRun Viewer Service Account"
  project      = var.project_id
}

resource "google_service_account_iam_member" "build_sa_actas_viewer" {
  service_account_id = google_service_account.foldrun_viewer.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.foldrun_build.email}"
}

resource "google_storage_bucket_iam_member" "foldrun_viewer_bucket_access" {
  bucket = google_storage_bucket.foldrun_bucket.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.foldrun_viewer.email}"
}

# Allow the viewer to write task_config.json and analysis_metadata.json to GCS
# when triggering analysis from the UI
resource "google_storage_bucket_iam_member" "foldrun_viewer_bucket_write" {
  bucket = google_storage_bucket.foldrun_bucket.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.foldrun_viewer.email}"
}

# Allow the viewer to list Vertex AI pipeline jobs for the job picker
resource "google_project_iam_member" "foldrun_viewer_aiplatform" {
  project = var.project_id
  role    = "roles/aiplatform.viewer"
  member  = "serviceAccount:${google_service_account.foldrun_viewer.email}"
}

# Allow the viewer SA to act as the analysis SA when triggering Cloud Run jobs.
# The Cloud Run v2 :run endpoint requires actAs on the job's service account
# in addition to run.jobs.run on the job resource.
resource "google_service_account_iam_member" "foldrun_viewer_actas_analysis" {
  service_account_id = google_service_account.foldrun_analysis.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.foldrun_viewer.email}"
}

# Allow the viewer to trigger the Cloud Run analysis jobs from the UI
resource "google_cloud_run_v2_job_iam_member" "foldrun_viewer_run_af2_analysis" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.af2_analysis_job.name
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.foldrun_viewer.email}"
}

resource "google_cloud_run_v2_job_iam_member" "foldrun_viewer_run_of3_analysis" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.of3_analysis_job.name
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.foldrun_viewer.email}"
}

resource "google_cloud_run_v2_job_iam_member" "foldrun_viewer_run_boltz2_analysis" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.boltz2_analysis_job.name
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.foldrun_viewer.email}"
}

resource "google_cloud_run_v2_service" "foldrun_viewer" {
  name        = "foldrun-viewer"
  project     = var.project_id
  location    = var.region
  ingress     = "INGRESS_TRAFFIC_ALL"
  iap_enabled = true

  template {
    containers {
      image = "us-docker.pkg.dev/cloudrun/container/hello"

      env {
        name  = "PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "BUCKET_NAME"
        value = google_storage_bucket.foldrun_bucket.name
      }
      env {
        name  = "REGION"
        value = var.region
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }
    vpc_access {
      network_interfaces {
        network    = local.network_id
        subnetwork = local.subnet_id

      }
      egress = "ALL_TRAFFIC"
    }
    scaling {
      min_instance_count = 0
      max_instance_count = 10
    }
    service_account = google_service_account.foldrun_viewer.email
    timeout         = "300s"
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version
    ]
  }

  depends_on = [google_project_service.apis]
}

# Grant the IAP service agent permission to invoke the Cloud Run service
resource "google_cloud_run_v2_service_iam_member" "foldrun_viewer_iap_invoker" {
  provider = google-beta
  project  = google_cloud_run_v2_service.foldrun_viewer.project
  location = google_cloud_run_v2_service.foldrun_viewer.location
  name     = google_cloud_run_v2_service.foldrun_viewer.name
  role     = "roles/run.invoker"
  member   = google_project_service_identity.iap_sa.member

  depends_on = [time_sleep.service_agent_creation_sleep]
}

# Grant domain users access through IAP
resource "google_iap_web_cloud_run_service_iam_member" "member" {
  project                = google_cloud_run_v2_service.foldrun_viewer.project
  location               = google_cloud_run_v2_service.foldrun_viewer.location
  cloud_run_service_name = google_cloud_run_v2_service.foldrun_viewer.name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = "domain:${var.iap_access_domain}"
}

# Grant the agent SA permission to invoke the viewer programmatically
resource "google_cloud_run_v2_service_iam_member" "foldrun_viewer_agent" {
  provider = google-beta
  project  = google_cloud_run_v2_service.foldrun_viewer.project
  location = google_cloud_run_v2_service.foldrun_viewer.location
  name     = google_cloud_run_v2_service.foldrun_viewer.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.agent_sa.email}"
}