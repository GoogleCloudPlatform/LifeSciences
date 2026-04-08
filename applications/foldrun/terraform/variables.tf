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

variable "zone" {
  description = "The GCP zone"
  type        = string
  default     = "us-central1-a"
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

variable "filestore_tier" {
  description = "Tier for the Filestore instance"
  type        = string
  default     = "ZONAL"
}

variable "filestore_capacity_gb" {
  description = "Capacity for Filestore in GB"
  type        = number
  default     = 4096
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
}
