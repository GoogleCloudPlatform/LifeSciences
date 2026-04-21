#!/bin/bash
# Test OpenMM CUDA compatibility using Cloud Batch with an A100 GPU VM.
# Submits a one-off batch job using the alphafold-components image and runs
# openmm.testInstallation to validate the CUDA platform works correctly.

set -e

PROJECT=${1:-$(gcloud config get-value project)}
REGION="us-central1"
IMAGE="us-central1-docker.pkg.dev/${PROJECT}/foldrun-repo/alphafold-components:latest"
JOB_NAME="test-openmm-cuda-$(date +%Y%m%d%H%M%S)"

echo "Project: $PROJECT"
echo "Image:   $IMAGE"
echo "Job:     $JOB_NAME"
echo ""

# Submit Cloud Batch job with A100 GPU
echo "=== Submitting Cloud Batch job with A100 GPU ==="
gcloud batch jobs submit "$JOB_NAME" \
  --project="$PROJECT" \
  --location="$REGION" \
  --config=- <<EOF
{
  "taskGroups": [{
    "taskSpec": {
      "runnables": [{
        "container": {
          "imageUri": "${IMAGE}",
          "commands": ["python3", "-m", "openmm.testInstallation"]
        }
      }],
      "computeResource": {
        "cpuMilli": 8000,
        "memoryMib": 16000
      }
    },
    "taskCount": 1,
    "parallelism": 1
  }],
  "allocationPolicy": {
    "instances": [{
      "installGpuDrivers": true,
      "policy": {
        "machineType": "a2-highgpu-1g",
        "accelerators": [{
          "type": "nvidia-tesla-a100",
          "count": 1
        }],
        "shieldedInstanceConfig": {
          "enableSecureBoot": true
        }
      }
    }],
    "network": {
      "networkInterfaces": [{
        "network": "projects/${PROJECT}/global/networks/foldrun-network",
        "subnetwork": "projects/${PROJECT}/regions/${REGION}/subnetworks/foldrun-network-subnet",
        "noExternalIpAddress": true
      }]
    }
  },
  "logsPolicy": {
    "destination": "CLOUD_LOGGING"
  }
}
EOF

echo ""
echo "=== Waiting for job to complete ==="
while true; do
  STATE=$(gcloud batch jobs describe "$JOB_NAME" \
    --project="$PROJECT" \
    --location="$REGION" \
    --format="value(status.state)" 2>/dev/null)
  echo "State: $STATE"
  if [[ "$STATE" == "SUCCEEDED" || "$STATE" == "FAILED" ]]; then
    break
  fi
  sleep 15
done

echo ""
echo "=== Logs ==="
gcloud logging read \
  "resource.type=batch.googleapis.com/Job resource.labels.job_id=${JOB_NAME}" \
  --project="$PROJECT" \
  --limit=50 \
  --order=asc \
  --format="value(textPayload)" 2>/dev/null | grep -v "^$" | tail -40

echo ""
echo "Final state: $STATE"
