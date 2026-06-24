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

output "reasoning_engine_id" {
  value       = google_vertex_ai_reasoning_engine.agent.id
  description = "The full resource name of the deployed Reasoning Engine"
}

output "reasoning_engine_name" {
  value       = google_vertex_ai_reasoning_engine.agent.name
  description = "The generated ID of the Reasoning Engine"
}

output "agent_identity" {
  value       = google_vertex_ai_reasoning_engine.agent.spec[0].effective_identity
  description = "The effective identity principal used at runtime"
}

output "agent_display_name" {
  value       = google_vertex_ai_reasoning_engine.agent.display_name
  description = "The display name of the Agent Runtime"
}

output "agent_description" {
  value       = google_vertex_ai_reasoning_engine.agent.description
  description = "The description of the Agent Runtime"
}

output "logs_data_bucket" {
  value       = local.bucket_name
  description = "The bucket name used for storing logs data"
}
