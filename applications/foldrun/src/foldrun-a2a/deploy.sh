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

# Deploy FoldRun A2A agent to Cloud Run.
#
# This script copies the foldrun_app package from the agent directory,
# then deploys to Cloud Run using source-based builds.
#
# Usage:
#   bash deploy.sh <PROJECT_ID> [SERVICE_NAME] [REGION]
#
# Example:
#   bash deploy.sh losiern-foldrun6
#   bash deploy.sh losiern-foldrun6 foldrun-a2a us-central1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="${SCRIPT_DIR}/../../foldrun-agent"

# Configuration
PROJECT_ID="${1:?Usage: deploy.sh <PROJECT_ID> [SERVICE_NAME] [REGION]}"
SERVICE_NAME="${2:-foldrun-a2a}"
REGION="${3:-us-central1}"
SERVICE_ACCOUNT="foldrun-agent-sa@${PROJECT_ID}.iam.gserviceaccount.com"
VPC_NAME="${VPC_NAME:-foldrun-network}"
SUBNET_NAME="${SUBNET_NAME:-${VPC_NAME}-subnet}"
AR_REPO="${AR_REPO:-foldrun-repo}"
IMAGE_PATH="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${SERVICE_NAME}:latest"

# Source .env from the agent directory
if [[ -f "${AGENT_DIR}/.env" ]]; then
  set -a
  source "${AGENT_DIR}/.env"
  set +a
fi

# Copy foldrun_app package and requirements for Cloud Run build
echo "Syncing foldrun_app from ${AGENT_DIR}..."
rsync -a --delete \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='a2a_agent_card.py' \
  "${AGENT_DIR}/foldrun_app/" "${SCRIPT_DIR}/foldrun_app/"

# Copy requirements (the pinned export, not pyproject.toml deps)
cp "${AGENT_DIR}/foldrun_app/app_utils/.requirements.txt" "${SCRIPT_DIR}/requirements.txt"

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║   Deploying FoldRun A2A to Cloud Run                     ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "  Project:         ${PROJECT_ID}"
echo "  Service:         ${SERVICE_NAME}"
echo "  Region:          ${REGION}"
echo "  Image:           ${IMAGE_PATH}"
echo "  Service Account: ${SERVICE_ACCOUNT}"
echo "  Network:         ${VPC_NAME} / ${SUBNET_NAME}"
echo ""

# Set the project
gcloud config set project "${PROJECT_ID}"

# Build env vars string for Cloud Run
# Pull key vars from .env, skip reserved/empty ones
ENV_VARS="GOOGLE_GENAI_USE_VERTEXAI=1"
ENV_VARS="${ENV_VARS},GOOGLE_CLOUD_PROJECT=${PROJECT_ID}"
ENV_VARS="${ENV_VARS},GOOGLE_CLOUD_REGION=${REGION}"

# Forward optional env vars from .env if set
for var in GCP_PROJECT_ID GCP_REGION GCS_BUCKET_NAME GEMINI_MODEL \
           OPENFOLD3_COMPONENTS_IMAGE AF2_VIEWER_URL FOLDRUN_VIEWER_URL \
           ANALYSIS_VIEWER_BASE_URL VERTEX_PROJECT_ID VERTEX_LOCATION \
           VERTEX_STAGING_BUCKET NFS_SERVER_IP NFS_SHARE_PATH; do
  val="${!var:-}"
  if [[ -n "${val}" ]]; then
    ENV_VARS="${ENV_VARS},${var}=${val}"
  fi
done

echo "Step 1: Building container image..."
gcloud builds submit \
  --config cloudbuild.yaml \
  --project "${PROJECT_ID}" \
  --service-account "projects/${PROJECT_ID}/serviceAccounts/foldrun-build-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
  --substitutions "_IMAGE_PATH=${IMAGE_PATH}" \
  .

echo ""
echo "Step 2: Deploying to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE_PATH}" \
  --region "${REGION}" \
  --platform managed \
  --ingress all \
  --service-account "${SERVICE_ACCOUNT}" \
  --set-env-vars "${ENV_VARS}" \
  --memory 2Gi \
  --cpu 2 \
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

# Get project number for IAM setup
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')

echo ""
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║   Deployment successful!                                  ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "  Service URL:  ${AGENT_URL}"
echo "  Agent Card:   ${AGENT_URL}/.well-known/agent.json"
echo ""
echo "┌─────────────────────────────────────────────────────────────┐"
echo "│  Next Steps                                                 │"
echo "├─────────────────────────────────────────────────────────────┤"
echo "│                                                             │"
echo "│  1. Register with Gemini Enterprise:                        │"
echo "│     bash register_gemini.sh \\                               │"
echo "│       ${PROJECT_NUMBER} \\                                   │"
echo "│       <ENGINE_ID> \\                                         │"
echo "│       ${AGENT_URL}                                          │"
echo "│                                                             │"
echo "│  2. Grant Cloud Run Invoker to Discovery Engine SA:         │"
echo "│     gcloud run services add-iam-policy-binding \\            │"
echo "│       ${SERVICE_NAME} \\                                     │"
echo "│       --region=${REGION} \\                                  │"
echo "│       --member=serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-discoveryengine.iam.gserviceaccount.com \\  │"
echo "│       --role=roles/run.invoker                              │"
echo "│                                                             │"
echo "│  3. Test the agent card:                                    │"
echo "│     curl -H \"Authorization: Bearer \$(gcloud auth print-access-token)\" \\  │"
echo "│       ${AGENT_URL}/.well-known/agent.json                   │"
echo "│                                                             │"
echo "│  4. Gemini CLI setup:                                       │"
echo "│     ~/.gemini/settings.json:                                │"
echo "│       {\"experimental\": {\"enableAgents\": true}}              │"
echo "│                                                             │"
echo "│     ~/.gemini/agents/foldrun.md:                            │"
echo "│       ---                                                   │"
echo "│       kind: remote                                          │"
echo "│       name: foldrun                                         │"
echo "│       agent_card_url: ${AGENT_URL}/.well-known/agent.json   │"
echo "│       ---                                                   │"
echo "│                                                             │"
echo "└─────────────────────────────────────────────────────────────┘"
echo ""

# Write deployment metadata
cat > deployment_metadata_a2a.json <<EOF
{
  "deployment_target": "cloud_run",
  "service_name": "${SERVICE_NAME}",
  "project_id": "${PROJECT_ID}",
  "project_number": "${PROJECT_NUMBER}",
  "region": "${REGION}",
  "agent_url": "${AGENT_URL}",
  "agent_card_url": "${AGENT_URL}/.well-known/agent.json",
  "deployment_timestamp": "$(date -Iseconds)"
}
EOF

echo "Deployment metadata written to deployment_metadata_a2a.json"
