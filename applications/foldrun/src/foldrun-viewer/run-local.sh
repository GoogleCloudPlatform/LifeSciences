#!/bin/bash
# Run the FoldRun Structure Viewer locally via Docker.
#
# Usage:
#   bash run-local.sh [PROJECT_ID]          # pull published image from Artifact Registry
#   bash run-local.sh [PROJECT_ID] --build  # build from local source (for testing changes)
set -e

PROJECT_ID=${1:-gnext26-foldrun}
BUCKET_NAME="${BUCKET_NAME:-${PROJECT_ID}-foldrun-data}"
REGION="${REGION:-us-central1}"
BUILD=false

# Parse flags (--build can appear anywhere in args)
for arg in "$@"; do
  if [ "$arg" = "--build" ]; then BUILD=true; fi
done

LOCAL_IMAGE="foldrun-viewer:local"
REGISTRY_IMAGE="us-central1-docker.pkg.dev/${PROJECT_ID}/foldrun-repo/foldrun-viewer:latest"

echo "Project:  $PROJECT_ID"
echo "Bucket:   $BUCKET_NAME"
echo "Region:   $REGION"
echo "Mode:     $([ "$BUILD" = true ] && echo 'local build' || echo 'published image')"
echo ""

gcloud config set project "$PROJECT_ID"

# Find gcloud config dir (handles Cloud Shell's non-standard paths)
GCLOUD_CONFIG_DIR=$(gcloud info --format="value(config.paths.global_config_dir)")
ADC_FILE="${GCLOUD_CONFIG_DIR}/application_default_credentials.json"

if [ ! -f "$ADC_FILE" ]; then
  echo "Application Default Credentials not found — running login..."
  gcloud auth application-default login
  GCLOUD_CONFIG_DIR=$(gcloud info --format="value(config.paths.global_config_dir)")
  ADC_FILE="${GCLOUD_CONFIG_DIR}/application_default_credentials.json"
fi

if [ ! -f "$ADC_FILE" ]; then
  echo "ERROR: Could not find ADC credentials at $ADC_FILE"
  echo "Try: gcloud auth application-default login"
  exit 1
fi

echo "Credentials: $ADC_FILE"
echo ""

if [ "$BUILD" = true ]; then
  echo "Building image from local source (no cache)..."
  docker build --no-cache -t "$LOCAL_IMAGE" "$(dirname "$0")"
  IMAGE="$LOCAL_IMAGE"
else
  echo "Configuring Docker for Artifact Registry..."
  gcloud auth configure-docker us-central1-docker.pkg.dev --quiet
  echo "Pulling latest viewer image..."
  docker pull "$REGISTRY_IMAGE"
  IMAGE="$REGISTRY_IMAGE"
fi

echo ""
echo "Starting viewer on http://localhost:8080"
echo "Use Cloud Shell Web Preview on port 8080 to open in browser."
echo "Press Ctrl+C to stop."
echo ""

docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e PROJECT_ID="$PROJECT_ID" \
  -e BUCKET_NAME="$BUCKET_NAME" \
  -e REGION="$REGION" \
  -e GOOGLE_CLOUD_PROJECT="$PROJECT_ID" \
  -e GOOGLE_CLOUD_QUOTA_PROJECT="$PROJECT_ID" \
  -e GOOGLE_APPLICATION_CREDENTIALS="/gcloud/application_default_credentials.json" \
  -p 8080:8080 \
  -v "${GCLOUD_CONFIG_DIR}:/gcloud:ro" \
  "$IMAGE"
