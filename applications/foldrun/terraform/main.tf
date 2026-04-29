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
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 7.0.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 7.0.0"
    }
    time = {
      source  = "hashicorp/time"
      version = ">= 0.11.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

data "google_project" "project" {}

# ==============================================================================
# APIs
# ==============================================================================
resource "google_project_service" "apis" {
  for_each = toset([
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "batch.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudtrace.googleapis.com",
    "compute.googleapis.com",
    "file.googleapis.com",
    "iam.googleapis.com",
    "iap.googleapis.com",
    "logging.googleapis.com",
    "run.googleapis.com",
    "servicenetworking.googleapis.com",
    "storage.googleapis.com",
    "telemetry.googleapis.com"
  ])
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

resource "google_project_service_identity" "aiplatform_sa" {
  provider = google-beta

  project = var.project_id
  service = "aiplatform.googleapis.com"

  depends_on = [google_project_service.apis]
}

resource "google_project_service_identity" "batch_sa" {
  provider = google-beta

  project = var.project_id
  service = "batch.googleapis.com"

  depends_on = [google_project_service.apis]
}

resource "google_project_service_identity" "iap_sa" {
  provider = google-beta

  project = var.project_id
  service = "iap.googleapis.com"

  depends_on = [google_project_service.apis]
}

resource "time_sleep" "service_agent_creation_sleep" {
  create_duration = "60s"

  depends_on = [
    google_project_service_identity.aiplatform_sa,
    google_project_service_identity.iap_sa,
  ]
}

# ==============================================================================
# VPC Network & Subnet
# ==============================================================================
locals {
  create_network = var.network_name == ""

  network_project_id     = var.network_project_id != "" ? var.network_project_id : var.project_id
  network_project_number = var.network_project_number != "" ? var.network_project_number : data.google_project.project.number

  network_name = local.create_network ? google_compute_network.foldrun_vpc[0].name : var.network_name
  network_id   = local.create_network ? google_compute_network.foldrun_vpc[0].id : data.google_compute_network.existing_vpc[0].id

  subnet_name = local.create_network ? google_compute_subnetwork.foldrun_subnet[0].name : var.subnet_name
  subnet_id   = local.create_network ? google_compute_subnetwork.foldrun_subnet[0].id : data.google_compute_subnetwork.existing_subnet[0].id
}

data "google_compute_network" "existing_vpc" {
  count   = local.create_network ? 0 : 1
  name    = var.network_name
  project = local.network_project_id
}

data "google_compute_subnetwork" "existing_subnet" {
  count   = local.create_network ? 0 : 1
  name    = var.subnet_name
  region  = var.region
  project = local.network_project_id
}

resource "google_compute_network" "foldrun_vpc" {
  count                   = local.create_network ? 1 : 0
  name                    = var.vpc_name
  project                 = var.project_id
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "foldrun_subnet" {
  count                    = local.create_network ? 1 : 0
  name                     = "${var.vpc_name}-subnet"
  project                  = var.project_id
  ip_cidr_range            = var.subnet_cidr
  region                   = var.region
  network                  = google_compute_network.foldrun_vpc[0].id
  private_ip_google_access = true
}

resource "google_compute_firewall" "foldrun_allow_internal" {
  count   = local.create_network ? 1 : 0
  name    = "${var.vpc_name}-allow-internal"
  project = var.project_id
  network = google_compute_network.foldrun_vpc[0].id

  allow {
    protocol = "tcp"
  }
  allow {
    protocol = "udp"
  }
  allow {
    protocol = "icmp"
  }

  source_ranges = ["10.0.0.0/8"]
}

# ==============================================================================
# Cloud NAT (required — org policy blocks external IPs on VMs)
# ==============================================================================
resource "google_compute_router" "foldrun_router" {
  count   = local.create_network ? 1 : 0
  name    = "${var.vpc_name}-router"
  project = var.project_id
  region  = var.region
  network = google_compute_network.foldrun_vpc[0].id
}

resource "google_compute_router_nat" "foldrun_nat" {
  count                              = local.create_network ? 1 : 0
  name                               = "${var.vpc_name}-nat"
  project                            = var.project_id
  router                             = google_compute_router.foldrun_router[0].name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# ==============================================================================
# VPC Peering (Private Service Access for Filestore)
# ==============================================================================
resource "google_compute_global_address" "private_ip_alloc" {
  count         = local.create_network ? 1 : 0
  name          = "${var.vpc_name}-peering-range"
  project       = var.project_id
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = split("/", var.peering_cidr)[1]
  address       = split("/", var.peering_cidr)[0]
  network       = google_compute_network.foldrun_vpc[0].id
  depends_on    = [google_project_service.apis]
}

resource "google_service_networking_connection" "default" {
  count                   = local.create_network ? 1 : 0
  network                 = google_compute_network.foldrun_vpc[0].id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_alloc[0].name]
  depends_on              = [google_project_service.apis]
}

# ==============================================================================
# Filestore (NFS)
# ==============================================================================
resource "google_filestore_instance" "foldrun_nfs" {
  name     = var.filestore_id
  project  = var.project_id
  tier     = var.filestore_tier
  location = var.zone

  file_shares {
    capacity_gb = var.filestore_capacity_gb
    name        = var.nfs_share
  }

  networks {
    network      = local.network_id
    modes        = ["MODE_IPV4"]
    connect_mode = "PRIVATE_SERVICE_ACCESS"
  }

  lifecycle {
    ignore_changes = [file_shares[0].capacity_gb]
  }

  depends_on = [
    google_service_networking_connection.default,
    google_project_service.apis
  ]
}


# ==============================================================================
# GCS Bucket
# ==============================================================================
resource "google_storage_bucket" "foldrun_bucket" {
  name                        = var.bucket_name
  project                     = var.project_id
  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true
  depends_on                  = [google_project_service.apis]
}

resource "google_storage_bucket" "databases_bucket" {
  name                        = "${var.project_id}-foldrun-gdbs"
  project                     = var.project_id
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true
  depends_on                  = [google_project_service.apis]

  lifecycle {
    prevent_destroy = true
  }
}

# ==============================================================================
# Artifact Registry
# ==============================================================================
resource "google_artifact_registry_repository" "foldrun_repo" {
  location      = var.region
  project       = var.project_id
  repository_id = var.artifact_repo_name
  description   = "FoldRun component images"
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

# Grant Agent Platform custom code SA read access to Artifact Registry (needed to pull pipeline images).
resource "google_artifact_registry_repository_iam_member" "aiplatform_cc_sa" {
  project    = google_artifact_registry_repository.foldrun_repo.project
  location   = google_artifact_registry_repository.foldrun_repo.location
  repository = google_artifact_registry_repository.foldrun_repo.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-aiplatform-cc.iam.gserviceaccount.com"

  depends_on = [time_sleep.service_agent_creation_sleep]
}
