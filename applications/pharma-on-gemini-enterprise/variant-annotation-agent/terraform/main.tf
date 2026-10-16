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
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 7.28.0"
    }
  }
  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  create_alphagenome_bucket = var.alphagenome_bucket_name == null ? 1 : 0
  alphagenome_bucket        = var.alphagenome_bucket_name == null ? "${var.project_id}-alphagenome-outputs" : var.alphagenome_bucket_name
}

module "agent_infra" {
  source = "git::https://github.com/GoogleCloudPlatform/LifeSciences.git//applications/pharma-on-gemini-enterprise/shared/terraform/agent-infrastructure?ref=main"

  project_id       = var.project_id
  region           = var.region
  agent_id         = "alphagenome-variant-agent"
  display_name     = "Annotation Agent for AlphaGenome"
  description      = "Genomic variant impact scoring and multi-database annotation agent. Imports AlphaGenome outputs from Google Cloud Storage (gs://) buckets or chat uploads, computes AlphaGenome Variant Impact (AVI) Phred and 18-modality attribution scores, builds AlphaGenome Atlas deep-links, and annotates variants in parallel across ClinVar (v.2024-06-03) (Landrum et al. 2018), gnomAD (v4) (Karczewski et al. 2020), GWAS Catalog (v.1.0) (Sollis et al. 2023), and PharmGKB (Whirl-Carrillo et al. 2012, 2021)."
  logs_bucket_name = var.logs_bucket_name
}

# Dedicated GCS bucket for customer AlphaGenome output files (created if an existing bucket is not passed)
resource "google_storage_bucket" "alphagenome_outputs_bucket" {
  count                       = local.create_alphagenome_bucket
  project                     = var.project_id
  name                        = local.alphagenome_bucket
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  depends_on = [module.agent_infra]
}

# Grant the Agent Runtime Identity access to read, import, and upload objects/dossiers in the AlphaGenome bucket
resource "google_storage_bucket_iam_member" "alphagenome_bucket_agent_identity" {
  bucket = local.alphagenome_bucket
  role   = "roles/storage.objectAdmin"
  member = "principal://${module.agent_infra.agent_identity}"

  depends_on = [google_storage_bucket.alphagenome_outputs_bucket]
}

resource "google_storage_bucket_iam_member" "alphagenome_bucket_reader_identity" {
  bucket = local.alphagenome_bucket
  role   = "roles/storage.legacyBucketReader"
  member = "principal://${module.agent_infra.agent_identity}"

  depends_on = [google_storage_bucket.alphagenome_outputs_bucket]
}
