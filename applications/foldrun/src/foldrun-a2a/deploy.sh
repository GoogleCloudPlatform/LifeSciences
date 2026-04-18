#!/bin/bash
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

# Deploy FoldRun A2A proxy to Cloud Run.
#
# Thin proxy that exposes A2A protocol and forwards requests to
# the FoldRun Agent Engine deployment.
#
# Usage:
#   bash deploy.sh <PROJECT_ID> [SERVICE_NAME] [REGION]
#
# Example:
#   bash deploy.sh your-project-id
#   bash deploy.sh your-project-id foldrun-a2a us-central1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Configuration
PROJECT_ID="${1:?Usage: deploy.sh <PROJECT_ID> [SERVICE_NAME] [REGION]}"
SERVICE_NAME="${2:-foldrun-a2a}"
REGION="${3:-us-central1}"
SERVICE_ACCOUNT="foldrun-agent-sa@${PROJECT_ID}.iam.gserviceaccount.com"
VPC_NAME="${VPC_NAME:-foldrun-network}"
SUBNET_NAME="${SUBNET_NAME:-${VPC_NAME}-subnet}"
AR_REPO="${AR_REPO:-foldrun-repo}"
IMAGE_PATH="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${SERVICE_NAME}:latest"

# Agent Engine resource (read from deployment metadata or override)
AGENT_ENGINE_RESOURCE="${AGENT_ENGINE_RESOURCE:-}"
if [[ -z "${AGENT_ENGINE_RESOURCE}" ]]; then
  METADATA_FILE="${SCRIPT_DIR}/../../foldrun-agent/deployment_metadata.json"
  if [[ -f "${METADATA_FILE}" ]]; then
    AGENT_ENGINE_RESOURCE=$(python3 -c "import json; print(json.load(open('${METADATA_FILE}'))['remote_agent_engine_id'])")
  fi
fi

if [[ -z "${AGENT_ENGINE_RESOURCE}" ]]; then
  echo "ERROR: AGENT_ENGINE_RESOURCE not set and deployment_metadata.json not found."
  echo "Set AGENT_ENGINE_RESOURCE=projects/<num>/locations/<region>/reasoningEngines/<id>"
  exit 1
fi

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║   Deploying FoldRun A2A Proxy to Cloud Run               ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "  Project:         ${PROJECT_ID}"
echo "  Service:         ${SERVICE_NAME}"
echo "  Region:          ${REGION}"
echo "  Image:           ${IMAGE_PATH}"
echo "  Service Account: ${SERVICE_ACCOUNT}"
echo "  Network:         ${VPC_NAME} / ${SUBNET_NAME}"
echo "  Agent Engine:    ${AGENT_ENGINE_RESOURCE}"
echo ""

# Set the project
gcloud config set project "${PROJECT_ID}"

# Get existing URL if available to pass to the agent card
EXISTING_URL=$(gcloud run services describe "${SERVICE_NAME}" --region "${REGION}" --format 'value(status.url)' 2>/dev/null || echo "")

# Env vars — only what the proxy needs
ENV_VARS="GOOGLE_CLOUD_PROJECT=${PROJECT_ID}"
ENV_VARS="${ENV_VARS},GOOGLE_CLOUD_REGION=${REGION}"
ENV_VARS="${ENV_VARS},AGENT_ENGINE_RESOURCE=${AGENT_ENGINE_RESOURCE}"
if [[ -n "${EXISTING_URL}" ]]; then
  ENV_VARS="${ENV_VARS},AGENT_BASE_URL=${EXISTING_URL}"
fi

echo "Step 1: Building container image..."
gcloud builds submit \
  --config cloudbuild.yaml \
  --project "${PROJECT_ID}" \
  --service-account "projects/${PROJECT_ID}/serviceAccounts/foldrun-build-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
  --substitutions "_IMAGE_PATH=${IMAGE_PATH}" \
  "${SCRIPT_DIR}"

echo ""
echo "Step 2: Deploying to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE_PATH}" \
  --region "${REGION}" \
  --platform managed \
  --ingress all \
  --service-account "${SERVICE_ACCOUNT}" \
  --set-env-vars "${ENV_VARS}" \
  --memory 512Mi \
  --cpu 1 \
  --timeout 300 \
  --min-instances 0 \
  --max-instances 5 \
  --network "${VPC_NAME}" \
  --subnet "${SUBNET_NAME}" \
  --vpc-egress all-traffic \
  --no-allow-unauthenticated

# Get the service URL
AGENT_URL=$(gcloud run services describe "${SERVICE_NAME}" \
  --region "${REGION}" \
  --format 'value(status.url)')

echo ""
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║   Deployment successful!                                  ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "  Service URL:  ${AGENT_URL}"
echo "  Agent Card:   ${AGENT_URL}/.well-known/agent.json"
echo ""
echo "  Test the agent card:"
echo "    curl -H \"Authorization: Bearer \$(gcloud auth print-identity-token --audiences=${AGENT_URL})\" \\"
echo "      ${AGENT_URL}/.well-known/agent.json"
echo ""
echo "  ─── Gemini CLI Setup ───"
echo ""
echo "  To use FoldRun as a remote agent in Gemini CLI, create:"
echo "    ~/.gemini/agents/foldrun.md"
echo ""
echo "  With this content:"
echo ""
echo "    ---"
echo "    kind: remote"
echo "    name: FoldRun"
echo "    description: Protein structure prediction agent"
echo "    agent_card_url: ${AGENT_URL}/.well-known/agent.json"
echo "    auth:"
echo "      type: google-credentials"
echo "    ---"
echo ""
echo "  Then use it in Gemini CLI:"
echo "    gemini -a foldrun \"Predict the structure of ubiquitin\""
echo ""
