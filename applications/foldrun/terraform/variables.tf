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

variable "project_id" {
  description = "The GCP project ID"
  type        = string
}

variable "region" {
  description = "The GCP region"
  type        = string
  default     = "us-central1"
}

variable "model_endpoint_location" {
  description = "The GCP location for the model endpoint"
  type        = string
  default     = "global"
}

variable "zone" {
  description = "The GCP zone (defaults to <region>-a when empty)"
  type        = string
  default     = ""
}

variable "vpc_name" {
  description = "Name of the VPC network"
  type        = string
  default     = "foldrun-network"
}

variable "subnet_cidr" {
  description = "CIDR block for the subnet"
  type        = string
  default     = "10.0.0.0/24"
}

variable "peering_cidr" {
  description = "CIDR block for VPC Peering (Private Services Access)"
  type        = string
  default     = "10.1.0.0/16"
}

variable "network_name" {
  description = "Name of the existing VPC network to use. If not provided, a new VPC will be created."
  type        = string
  default     = ""
}

variable "subnet_name" {
  description = "Name of the existing subnetwork to use. If not provided, a new subnetwork will be created."
  type        = string
  default     = ""
}

variable "network_project_id" {
  description = "The ID of the project where the existing VPC network is located. Useful for Shared VPC. Defaults to var.project_id."
  type        = string
  default     = ""
}

variable "network_project_number" {
  description = "The number of the project where the existing VPC network is located. Required by Agent Platform for Shared VPC."
  type        = string
  default     = ""
}



variable "filestore_tier" {
  description = <<-EOT
    Tier for the Filestore instance. Defaults to REGIONAL: replicated across zones in
    var.region, resizable up or down in 1 GiB steps, with no 2.5 TiB minimum
    (1 TiB is ~$461/mo in us-central1, compared with ~$768/mo for the 2.5 TiB legacy BASIC_SSD
    minimum). REGIONAL and ENTERPRISE instances are created in var.region; BASIC_HDD,
    BASIC_SSD, and ZONAL instances are created in local.zone (var.zone or <region>-a).
    WARNING: changing the tier on an existing deployment makes Terraform replace the
    instance, which erases all NFS data. Restore it from the GCS databases bucket
    afterwards (see README "Migrating an existing Basic SSD Filestore").
  EOT
  type        = string
  default     = "REGIONAL"

  validation {
    condition     = contains(["REGIONAL", "ZONAL", "ENTERPRISE", "BASIC_SSD", "BASIC_HDD"], var.filestore_tier)
    error_message = "filestore_tier must be one of REGIONAL, ZONAL, ENTERPRISE, BASIC_SSD, BASIC_HDD."
  }
}

variable "filestore_capacity_gb" {
  description = "Initial Filestore capacity in GiB. REGIONAL: 1024 to 10239 in 1 GiB steps (sizes under 1024 also require filestore_max_iops > 0); ZONAL: min 1024; BASIC_HDD: min 1024; BASIC_SSD: min 2560. AF2 reduced + OF3 use ~944 GB and full BFD adds ~272 GB. Terraform ignores capacity changes after provisioning, so you can resize with the Console or gcloud without drift. REGIONAL and ZONAL can also shrink."
  type        = number
  default     = 1024
}

variable "filestore_max_iops" {
  description = "Optional fixed IOPS for REGIONAL/ZONAL custom performance. 0 (default) keeps capacity-based performance (REGIONAL 1 TiB: ~12,000 read IOPS, ~120 MiB/s read). If > 0, billing changes to a per-instance fee + a lower per-GiB rate + a per-IOPS rate (see README cost table). Allowed range: 4,000-17,000 IOPS per TiB of capacity, and at least 2,000."
  type        = number
  default     = 0

  validation {
    condition     = var.filestore_max_iops == 0 || var.filestore_max_iops >= 2000
    error_message = "filestore_max_iops must be 0 (capacity-based performance) or >= 2000."
  }
}

variable "filestore_id" {
  description = "ID for the Filestore instance"
  type        = string
  default     = "foldrun-nfs"
}

variable "nfs_share" {
  description = "Name of the NFS share"
  type        = string
  default     = "datasets"
}

variable "bucket_name" {
  description = "Name of the GCS bucket for artifacts and logs"
  type        = string
}

variable "artifact_repo_name" {
  description = "Name of the Artifact Registry repository"
  type        = string
  default     = "foldrun-repo"
}

variable "iap_access_domain" {
  description = "Domain to grant IAP access to the viewer (e.g., your-company.com)"
  type        = string
  default     = ""
}

variable "enable_viewer_iap" {
  description = "Enable Identity-Aware Proxy (IAP) for the viewer"
  type        = bool
  default     = true
}

variable "enable_af3" {
  description = "Whether to provision the AlphaFold 3 Model Garden Dedicated Endpoint (google/alphafold3@v3_0_4) and configure its 3,600s inference timeout during Terraform install. Requires AlphaFold 3 Model Garden allowlist access and GPU quota in af3_endpoint_location (defaults to var.region)."
  type        = bool
  default     = false
}

variable "af3_endpoint_location" {
  description = "GCP region for the AlphaFold 3 Model Garden Dedicated Endpoint. Defaults to var.region when empty (must be a regional location with GPU quota such as us-central1 or us-west1, never 'global')."
  type        = string
  default     = ""
}

variable "af3_publisher_model_id" {
  description = "Gemini platform Model Garden publisher model ID for AlphaFold 3"
  type        = string
  default     = "google/alphafold3@v3_0_4"
}

variable "af3_endpoint_display_name" {
  description = "Display name for the AlphaFold 3 Gemini platform Dedicated Endpoint"
  type        = string
  default     = "af3-dedicated-ep"
}

variable "af3_model_display_name" {
  description = "Display name for the AlphaFold 3 model imported into Gemini platform Model Registry"
  type        = string
  default     = "af3-on-mg"
}

variable "af3_machine_type" {
  description = "Machine type for the AlphaFold 3 Dedicated Endpoint (a3-highgpu-1g provides 3 TB local NVMe SSD for the 630 GB MSA bundle)"
  type        = string
  default     = "a3-highgpu-1g"
}

variable "af3_accelerator_type" {
  description = "GPU accelerator type for the AlphaFold 3 Dedicated Endpoint"
  type        = string
  default     = "NVIDIA_H100_80GB"
}

variable "af3_accelerator_count" {
  description = "GPU accelerator count per replica for the AlphaFold 3 Dedicated Endpoint"
  type        = number
  default     = 1
}

variable "af3_capacity_pool" {
  description = "Optional capacity pool override for the AlphaFold 3 Dedicated Endpoint: 'DWS' (default, Dynamic Workload Scheduler FlexStart), 'ANY_RESERVATION', 'NO_RESERVATION', 'SPECIFIC_RESERVATION', or 'SPOT'. When empty, uses var.af3_use_dws (true by default)."
  type        = string
  default     = ""
}

variable "af3_reservation_affinity_type" {
  description = "GCE reservation affinity type for AlphaFold 3 Dedicated Endpoint when af3_use_dws=false or af3_capacity_pool='ANY_RESERVATION' ('ANY_RESERVATION', 'NO_RESERVATION', or 'SPECIFIC_RESERVATION')"
  type        = string
  default     = "ANY_RESERVATION"
}

variable "af3_use_dws" {
  description = "Use Dynamic Workload Scheduler (DWS FlexStart) to provision the a3-highgpu-1g sub-shape for the AlphaFold 3 Dedicated Endpoint (recommended because A3 sub-shapes are not in the standard GCE on-demand pool)"
  type        = bool
  default     = true
}

variable "af3_dws_max_runtime_seconds" {
  description = "Max runtime duration in seconds for DWS FlexStart endpoint deployment (default 561600 = 6.5 days, matching Cloud Console Model Garden UI)"
  type        = number
  default     = 561600
}

variable "af3_inference_timeout_seconds" {
  description = "Client connection inference timeout in seconds on the AlphaFold 3 Dedicated Endpoint"
  type        = number
  default     = 3600
}

variable "af3_min_replica_count" {
  description = "Minimum replica count for the AlphaFold 3 Dedicated Endpoint. Defaults to 0 (Gemini platform native Scale-to-Zero enabled, scaling down to $0.00/hr after af3_idle_scaledown_period_seconds of inactivity). Set to >= 1 to keep warm GPU replicas running continuously."
  type        = number
  default     = 0
}

variable "af3_max_replica_count" {
  description = "Maximum replica count for the AlphaFold 3 Dedicated Endpoint"
  type        = number
  default     = 1
}

variable "af3_initial_replica_count" {
  description = "Initial replica count when scaling up from 0 replicas (only applies when af3_min_replica_count = 0; must be >= 1 and <= af3_max_replica_count)"
  type        = number
  default     = 1
}

variable "af3_min_scaleup_period_seconds" {
  description = "Duration in seconds (300 to 28800) that a replica scaled up from zero stays active before scale-to-zero evaluation begins (only applies when af3_min_replica_count = 0)"
  type        = number
  default     = 300
}

variable "af3_idle_scaledown_period_seconds" {
  description = "Idle duration in seconds (300 to 28800, default 1200 = 20 minutes) with zero prediction traffic before Gemini platform scales the endpoint down to 0 replicas ($0.00/hr) (only applies when af3_min_replica_count = 0)"
  type        = number
  default     = 1200
}

variable "af3_undeploy_after_setup" {
  description = "If true, immediately undeploys active GPU replicas after registering the AF3 model and endpoint so idle cost is $0.00/hr until the first prediction job runs"
  type        = bool
  default     = false
}

