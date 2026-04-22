#!/bin/bash
# Run the FoldRun Structure Viewer locally via Docker (Cloud Shell workaround).
# Usage: bash run-local.sh [PROJECT_ID]
set -e

PROJECT_ID=${1:-gnext26-foldrun}
BUCKET_NAME="${2:-${PROJECT_ID}-foldrun-data}"
REGION=${3:-us-central1}
IMAGE="us-central1-docker.pkg.dev/${PROJECT_ID}/foldrun-repo/foldrun-viewer:latest"
ADC_FILE="${HOME}/.config/gcloud/application_default_credentials.json"

echo "Project:  $PROJECT_ID"
echo "Bucket:   $BUCKET_NAME"
echo "Region:   $REGION"
echo ""

gcloud config set project "$PROJECT_ID"

if [ ! -f "$ADC_FILE" ]; then
  echo "Application Default Credentials not found — running login..."
  gcloud auth application-default login
fi

echo "Pulling latest viewer image..."
docker pull "$IMAGE"

echo ""
echo "Starting viewer on http://localhost:8080"
echo "Use Cloud Shell Web Preview on port 8080 to open in browser."
echo "Press Ctrl+C to stop."
echo ""

docker run --rm \
  -e PROJECT_ID="$PROJECT_ID" \
  -e BUCKET_NAME="$BUCKET_NAME" \
  -e REGION="$REGION" \
  -e GOOGLE_APPLICATION_CREDENTIALS="/root/.config/gcloud/application_default_credentials.json" \
  -p 8080:8080 \
  -v "${HOME}/.config/gcloud:/root/.config/gcloud:ro" \
  "$IMAGE"
