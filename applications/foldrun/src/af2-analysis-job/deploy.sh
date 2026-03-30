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

# Deploy AF2 Prediction Analyzer as Cloud Run Job

set -e

# Configuration
PROJECT_ID=${PROJECT_ID:-$(gcloud config get-value project)}
REGION=${REGION:-us-central1}
JOB_NAME=${JOB_NAME:-af2-analysis-job}
ARTIFACT_REGISTRY_REPO=${ARTIFACT_REGISTRY_REPO:-foldrun-repo}

# Artifact Registry image path
IMAGE_PATH="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REGISTRY_REPO}/${JOB_NAME}"

echo "================================================"
echo "Deploying AF2 Analysis Job to Cloud Run"
echo "================================================"
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo "Job: $JOB_NAME"
echo "Image: $IMAGE_PATH"
echo ""

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo "Error: gcloud CLI not found. Please install Google Cloud SDK."
    exit 1
fi

# Check if project is set
if [ -z "$PROJECT_ID" ]; then
    echo "Error: PROJECT_ID not set. Set it with: export PROJECT_ID=your-project-id"
    exit 1
fi

# Navigate to current directory (we're already in af2-analysis-job)
cd "$(dirname "$0")"

echo "Step 1: Building container image..."
gcloud builds submit --tag $IMAGE_PATH --project $PROJECT_ID

echo ""
echo "Step 2: Creating/Updating Cloud Run Job..."

# Get bucket name from environment or use default
BUCKET_NAME=${GCS_BUCKET:-${PROJECT_ID}-foldrun-data}

echo "Gemini expert analysis will use Vertex AI with Application Default Credentials"

# Create or update the job
gcloud run jobs deploy $JOB_NAME \
  --image $IMAGE_PATH \
  --region $REGION \
  --memory 8Gi \
  --cpu 2 \
  --max-retries 0 \
  --task-timeout 600 \
  --parallelism 25 \
  --set-env-vars GCS_BUCKET=$BUCKET_NAME,PROJECT_ID=$PROJECT_ID,REGION=$REGION \
  --project $PROJECT_ID

echo ""
echo "================================================"
echo "Deployment Complete!"
echo "================================================"
echo "Job Name: $JOB_NAME"
echo "Region: $REGION"
echo ""
echo "To execute the job, use the MCP tool:"
echo "  analyze_job_async_parallel(job_id='your-job-id')"
echo "================================================"
