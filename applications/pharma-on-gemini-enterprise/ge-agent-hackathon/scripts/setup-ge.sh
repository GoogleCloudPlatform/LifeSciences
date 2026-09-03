#!/usr/bin/env bash
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

# Create a Gemini Enterprise app + default assistant over the API.
#
# NOTE: prefer the Gemini Enterprise console. A console-created app provisions the
# app, its assistant and the licence association together. This script cannot grant
# you a licence — without one, publishing an agent will fail.
set -euo pipefail
PROJECT="${1:?usage: setup-ge.sh <project> [app-id]}"
APP="${2:-ge-hackathon}"
NUM=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
TOK=$(gcloud auth print-access-token)
B="https://discoveryengine.googleapis.com/v1alpha/projects/$NUM/locations/global/collections/default_collection"

# POST and stop on failure. Plain `curl -s` exits 0 on a 4xx/5xx, so without this
# a rejected dataStore would sail on to the engine call and fail there instead,
# with the real error already scrolled off. 409 ALREADY_EXISTS is success: the
# script is meant to be re-runnable.
api_post() {
  local what="$1" url="$2" body="$3" out code
  out=$(curl -s -w '\n%{http_code}' -X POST \
    -H "Authorization: Bearer $TOK" -H "x-goog-user-project: $PROJECT" \
    -H "Content-Type: application/json" "$url" -d "$body")
  code="${out##*$'\n'}"
  out="${out%$'\n'*}"
  case "$code" in
    2*) echo "${out:0:300}" ;;
    409) echo "  (already exists — reusing)" ;;
    *)  echo "ERROR: $what failed with HTTP $code" >&2
        echo "${out:0:800}" >&2
        exit 1 ;;
  esac
}

echo "==> Enabling Discovery Engine API"
gcloud services enable discoveryengine.googleapis.com --project="$PROJECT" -q

echo "==> Creating data store (an app requires at least one)"
api_post "data store creation" "$B/dataStores?dataStoreId=${APP}-ds" \
  '{"displayName":"'"$APP"' data store","industryVertical":"GENERIC","solutionTypes":["SOLUTION_TYPE_SEARCH"],"contentConfig":"NO_CONTENT"}'
sleep 15

echo "==> Creating app"
api_post "app creation" "$B/engines?engineId=$APP" \
  '{"displayName":"'"$APP"'","solutionType":"SOLUTION_TYPE_SEARCH","industryVertical":"GENERIC","dataStoreIds":["'"$APP"'-ds"],"searchEngineConfig":{"searchTier":"SEARCH_TIER_ENTERPRISE","searchAddOns":["SEARCH_ADD_ON_LLM"]}}'
sleep 10

echo "==> Creating default_assistant (API-created apps do NOT get one automatically)"
api_post "assistant creation" "$B/engines/$APP/assistants?assistantId=default_assistant" \
  '{"displayName":"Default Assistant"}'

echo
echo "App ID for publishing:"
echo "  projects/$NUM/locations/global/collections/default_collection/engines/$APP"
echo
echo "Reminder: you still need an active Gemini Enterprise LICENCE on your user."
