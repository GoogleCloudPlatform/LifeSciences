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
# Cloud Run Service (FoldRun Direct HTTP Gateway — Optional: var.enable_gateway)
# ==============================================================================
variable "enable_gateway" {
  type        = bool
  description = "Deploy the Cloud Run HTTP Gateway (foldrun-gateway) for direct CLI/Notebook SDK submission"
  default     = true
}

variable "gateway_ingress" {
  type        = string
  description = "Cloud Run ingress setting for foldrun-gateway (INGRESS_TRAFFIC_ALL or INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER)"
  default     = "INGRESS_TRAFFIC_ALL"

  validation {
    condition = contains([
      "INGRESS_TRAFFIC_ALL",
      "INGRESS_TRAFFIC_INTERNAL_ONLY",
      "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER",
    ], var.gateway_ingress)
    error_message = "gateway_ingress must be INGRESS_TRAFFIC_ALL, INGRESS_TRAFFIC_INTERNAL_ONLY, or INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER."
  }
}

resource "google_project_service" "gateway_iamcredentials" {
  count              = var.enable_gateway ? 1 : 0
  project            = var.project_id
  service            = "iamcredentials.googleapis.com"
  disable_on_destroy = false
}

resource "google_service_account" "foldrun_gateway" {
  count        = var.enable_gateway ? 1 : 0
  account_id   = "foldrun-gateway-sa"
  display_name = "FoldRun HTTP Gateway Service Account"
  project      = var.project_id
}

resource "google_project_iam_member" "gateway_roles" {
  for_each = var.enable_gateway ? toset([
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
  ]) : toset([])

  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.foldrun_gateway[0].email}"
}

resource "google_service_account_iam_member" "build_sa_actas_gateway" {
  count              = var.enable_gateway ? 1 : 0
  service_account_id = google_service_account.foldrun_gateway[0].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.foldrun_build.email}"
}

# Allow the gateway SA to sign GCS V4 Signed URLs via IAM signBlob
resource "google_service_account_iam_member" "gateway_self_token_creator" {
  count              = var.enable_gateway ? 1 : 0
  service_account_id = google_service_account.foldrun_gateway[0].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.foldrun_gateway[0].email}"
}

# Allow the gateway SA to submit Vertex AI PipelineJobs running as pipelines-sa
resource "google_service_account_iam_member" "gateway_sa_actas_pipelines" {
  count              = var.enable_gateway ? 1 : 0
  service_account_id = google_service_account.pipelines.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.foldrun_gateway[0].email}"
}

# Allow the gateway SA to act as the analysis SA when triggering foldrun-analysis-job
resource "google_service_account_iam_member" "gateway_sa_actas_analysis" {
  count              = var.enable_gateway ? 1 : 0
  service_account_id = google_service_account.foldrun_analysis.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.foldrun_gateway[0].email}"
}

# Allow the gateway SA to execute foldrun-analysis-job with task overrides
resource "google_cloud_run_v2_job_iam_member" "gateway_sa_run_analysis" {
  count    = var.enable_gateway ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.foldrun_analysis_job.name
  role     = "roles/run.jobsExecutorWithOverrides"
  member   = "serviceAccount:${google_service_account.foldrun_gateway[0].email}"
}

resource "google_storage_bucket_iam_member" "foldrun_gateway_bucket_access" {
  count  = var.enable_gateway ? 1 : 0
  bucket = google_storage_bucket.foldrun_bucket.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.foldrun_gateway[0].email}"
}

resource "google_storage_bucket_iam_member" "foldrun_gateway_databases_bucket_access" {
  count  = var.enable_gateway ? 1 : 0
  bucket = google_storage_bucket.databases_bucket.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.foldrun_gateway[0].email}"
}

resource "google_cloud_run_v2_service_iam_member" "foldrun_viewer_gateway" {
  count    = var.enable_gateway ? 1 : 0
  provider = google-beta
  project  = google_cloud_run_v2_service.foldrun_viewer.project
  location = google_cloud_run_v2_service.foldrun_viewer.location
  name     = google_cloud_run_v2_service.foldrun_viewer.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.foldrun_gateway[0].email}"
}

resource "google_cloud_run_v2_service" "foldrun_gateway" {
  count                = var.enable_gateway ? 1 : 0
  name                 = "foldrun-gateway"
  project              = var.project_id
  location             = var.region
  ingress              = var.gateway_ingress
  invoker_iam_disabled = false
  deletion_protection  = false

  template {
    containers {
      # Placeholder image for initial `terraform apply` before Cloud Build runs.
      # `cloudbuild.yaml` (`deploy-gateway`) deploys the `foldrun-gateway:latest`
      # image built from `foldrun-agent/Dockerfile` and overrides command/args to:
      # `/code/.venv/bin/uvicorn foldrun_app.gateway.app:app --host 0.0.0.0 --port 8080`.
      image   = "us-docker.pkg.dev/cloudrun/container/hello"
      command = ["/code/.venv/bin/uvicorn"]
      args    = ["foldrun_app.gateway.app:app", "--host", "0.0.0.0", "--port", "8080"]

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "GCP_REGION"
        value = var.region
      }
      env {
        name  = "GCP_ZONE"
        value = local.zone
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.model_endpoint_location
      }
      env {
        name  = "GOOGLE_GENAI_USE_ENTERPRISE"
        value = "1"
      }
      env {
        name  = "DWS_MAX_WAIT_HOURS"
        value = "1"
      }
      env {
        name  = "GEMINI_MODEL"
        value = "gemini-3.8-flash"
      }
      env {
        name  = "GEMINI_ANALYSIS_MODEL"
        value = "gemini-3.1-pro-preview"
      }
      env {
        name  = "AF3_ENDPOINT"
        value = "auto"
      }
      env {
        name  = "AF3_MODEL_ID"
        value = "auto"
      }
      env {
        name  = "AF3_ENDPOINT_LOCATION"
        value = local.af3_endpoint_location
      }
      env {
        name  = "GCS_BUCKET_NAME"
        value = google_storage_bucket.foldrun_bucket.name
      }
      env {
        name  = "GCS_DATABASES_BUCKET"
        value = google_storage_bucket.databases_bucket.name
      }
      env {
        name  = "FILESTORE_ID"
        value = google_filestore_instance.foldrun_nfs.name
      }
      env {
        name  = "FILESTORE_LOCATION"
        value = local.filestore_location
      }
      env {
        name  = "ALPHAFOLD_COMPONENTS_IMAGE"
        value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.foldrun_repo.name}/alphafold-components:latest"
      }
      env {
        name  = "OPENFOLD3_COMPONENTS_IMAGE"
        value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.foldrun_repo.name}/openfold3-components:latest"
      }
      env {
        name  = "BOLTZ2_COMPONENTS_IMAGE"
        value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.foldrun_repo.name}/boltz2-components:latest"
      }
      env {
        name  = "FOLDRUN_VIEWER_URL"
        value = google_cloud_run_v2_service.foldrun_viewer.uri
      }
      env {
        name  = "ANALYSIS_JOB_NAME"
        value = google_cloud_run_v2_job.foldrun_analysis_job.name
      }
      env {
        name  = "PIPELINES_SA_EMAIL"
        value = google_service_account.pipelines.email
      }
      env {
        name  = "NETWORK"
        value = local.network_id
      }
      env {
        name  = "SUBNET_ID"
        value = local.subnet_id
      }
      env {
        name  = "NETWORK_PROJECT_NUMBER"
        value = local.network_project_number
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "4Gi"
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

    service_account = google_service_account.foldrun_gateway[0].email
    timeout         = "3600s"
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      template[0].containers[0].command,
      template[0].containers[0].args,
      client,
      client_version,
    ]
  }

  depends_on = [
    google_project_service.apis,
    google_project_service.gateway_iamcredentials,
    google_project_iam_member.gateway_roles,
    google_service_account_iam_member.gateway_self_token_creator,
    google_service_account_iam_member.gateway_sa_actas_pipelines,
    google_service_account_iam_member.gateway_sa_actas_analysis,
  ]
}

output "foldrun_gateway_url" {
  description = "The URL of the FoldRun HTTP Gateway Cloud Run service (empty if enable_gateway is false)"
  value       = var.enable_gateway ? google_cloud_run_v2_service.foldrun_gateway[0].uri : ""
}

output "gateway_sa_email" {
  description = "The email of the Gateway Service Account (empty if enable_gateway is false)"
  value       = var.enable_gateway ? google_service_account.foldrun_gateway[0].email : ""
}
