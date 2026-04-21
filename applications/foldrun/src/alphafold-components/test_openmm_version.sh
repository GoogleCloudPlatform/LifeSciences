#!/bin/bash
IMAGE="us-central1-docker.pkg.dev/losiern-foldrun7/foldrun-repo/alphafold-components:latest"

echo "=== Checking openmm version ==="
docker run --rm "$IMAGE" \
  python3 -c "import openmm; print('openmm:', openmm.__version__)"

echo ""
echo "=== Testing CUDA platform (requires GPU) ==="
docker run --rm --gpus all "$IMAGE" \
  python3 -m openmm.testInstallation
