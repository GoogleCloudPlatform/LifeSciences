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

module "agent_infra" {
  source = "git::https://github.com/GoogleCloudPlatform/LifeSciences.git//applications/pharma-on-gemini-enterprise/shared/terraform/agent-infrastructure?ref=main"

  project_id       = var.project_id
  region           = var.region
  agent_id         = "paperbanana-agent"
  display_name     = "PaperBanana on Gemini Enterprise"
  description      = "Academic figure generation agent: turns paper sections / data / sketches into publication-style figures via Nano Banana Pro, with planner-driven figure-spec authoring."
  logs_bucket_name = var.logs_bucket_name
}
