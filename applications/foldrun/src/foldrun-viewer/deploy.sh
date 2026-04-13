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

# Deploy FoldRun Structure Viewer to Cloud Run

set -e

# Configuration
PROJECT_ID=${PROJECT_ID:-$(gcloud config get-value project)}
REGION=${REGION:-us-central1}
SERVICE_NAME="foldrun-viewer"
IMAGE_NAME="foldrun-viewer"
BUCKET_NAME=${BUCKET_NAME:-${PROJECT_ID}-foldrun-data}
VPC_NAME=${VPC_NAME:-foldrun-network}
SUBNET_NAME=${SUBNET_NAME:-${VPC_NAME}-subnet}

# Artifact Registry repository
AR_REPO="foldrun-repo"
IMAGE_PATH="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${IMAGE_NAME}:latest"

echo "============================================"
echo "Deploying FoldRun Structure Viewer"
echo "============================================"
echo "Project: ${PROJECT_ID}"
echo "Region: ${REGION}"
echo "Service: ${SERVICE_NAME}"
echo "Image: ${IMAGE_PATH}"
echo ""

# Check if we're authenticated
echo "Checking gcloud authentication..."
if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" | grep -q .; then
    echo "❌ Not authenticated with gcloud. Please run:"
    echo "   gcloud auth login"
    exit 1
fi
echo "✅ Authenticated"
echo ""

# Navigate to current directory

cd "$(dirname "$0")"

# Build and push container
echo "Building and pushing container image..."
echo "This may take a few minutes..."
echo ""

gcloud builds submit \
    --config cloudbuild.yaml \
    --timeout=10m \
    --service-account="projects/${PROJECT_ID}/serviceAccounts/foldrun-build-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
    --substitutions=_IMAGE_PATH=${IMAGE_PATH} \
    .

echo ""
echo "✅ Container built and pushed"
echo ""

# Deploy to Cloud Run
echo "Deploying to Cloud Run..."
echo ""

gcloud run deploy ${SERVICE_NAME} \
    --image ${IMAGE_PATH} \
    --region ${REGION} \
    --platform managed \
    --ingress all \
    --iap \
    --service-account "foldrun-viewer-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
    --set-env-vars "PROJECT_ID=${PROJECT_ID},BUCKET_NAME=${BUCKET_NAME},REGION=${REGION}" \
    --memory 512Mi \
    --cpu 1 \
    --max-instances 10 \
    --min-instances 0 \
    --network ${VPC_NAME} \
    --subnet ${SUBNET_NAME} \
    --vpc-egress all-traffic \
    --timeout 300

echo ""
echo "✅ Cloud Run service deployed"
echo ""

# Get service URL
SERVICE_URL=$(gcloud run services describe ${SERVICE_NAME} \
    --region ${REGION} \
    --format 'value(status.url)')

echo "============================================"
echo "Deployment Complete!"
echo "============================================"
echo ""
echo "Service URL: ${SERVICE_URL}"
echo ""
echo "Test the viewer:"
echo ""
echo "Example URL:"
echo "${SERVICE_URL}/job/alphafold-inference-pipeline-20251011112213"
echo ""
