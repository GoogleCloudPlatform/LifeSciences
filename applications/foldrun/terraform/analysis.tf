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

resource "google_service_account" "foldrun_analysis" {
  account_id   = "foldrun-analysis-sa"
  display_name = "FoldRun Analysis Service Account"
  project      = var.project_id
}

resource "google_service_account_iam_member" "build_sa_actas_analysis" {
  service_account_id = google_service_account.foldrun_analysis.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.foldrun_build.email}"
}

resource "google_storage_bucket_iam_member" "foldrun_analysis_bucket_access" {
  bucket = google_storage_bucket.foldrun_bucket.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.foldrun_analysis.email}"
}

resource "google_project_iam_member" "foldrun_analysis_aiplatform_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.foldrun_analysis.email}"
}

resource "google_cloud_run_v2_job" "foldrun_analysis_job" {
  name     = "foldrun-analysis-job"
  project  = var.project_id
  location = var.region

  template {
    parallelism = 25
    task_count  = 25

    template {
      max_retries     = 0
      timeout         = "600s"
      service_account = google_service_account.foldrun_analysis.email
      vpc_access {
        network_interfaces {
          network    = local.network_id
          subnetwork = local.subnet_id
        }
        egress = "ALL_TRAFFIC"
      }
      containers {
        image = "us-docker.pkg.dev/cloudrun/container/hello"

        env {
          name  = "GCS_BUCKET"
          value = google_storage_bucket.foldrun_bucket.name
        }
        env {
          name  = "PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "REGION"
          value = var.region
        }

        resources {
          limits = {
            cpu    = "2"
            memory = "8Gi"
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image,
      template[0].task_count,
      client,
      client_version
    ]
  }

  depends_on = [google_project_service.apis]
}

# Allow the viewer to trigger the Cloud Run analysis job from the UI
resource "google_cloud_run_v2_job_iam_member" "foldrun_viewer_run_analysis" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.foldrun_analysis_job.name
  role     = "roles/run.jobsExecutorWithOverrides"
  member   = "serviceAccount:${google_service_account.foldrun_viewer.email}"
}