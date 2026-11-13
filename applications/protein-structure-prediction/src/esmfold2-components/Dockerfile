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

# Use NVIDIA CUDA 12.6 runtime on Ubuntu 24.04 (Python 3.12 native)
FROM nvidia/cuda:12.6.3-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

# Install system dependencies:
# - python3 / python3-pip / python3-venv: Python 3.12 runtime
# - hmmer: jackhmmer for protein MSA generation (full ESMFold2 mode)
# - git: required by KFP component runner
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    hmmer \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create a virtualenv so we don't hit PEP 668 externally-managed-environment errors
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Upgrade pip with hash verification
COPY requirements-pip.txt .
RUN pip3 install --no-cache-dir --require-hashes -r requirements-pip.txt

# Install ESMFold2 / ESMFold2-Fast and all dependencies from hash-pinned lock file.
# Note: --extra-index-url is required so pip can fetch the +cu126 PyTorch
# wheels pinned in requirements.lock (CUDA 12.6 compatible with GCP GPU node
# drivers, while remediating PyTorch CVEs).
COPY requirements.lock .
RUN pip3 install --no-cache-dir --require-hashes --no-deps \
    --extra-index-url https://download.pytorch.org/whl/cu126 \
    -r requirements.lock

# Purge any stale pip/wheel/setuptools copies shipped by Ubuntu's python3-venv
# seed or apt packages outside /opt/venv so container vulnerability scanners do
# not flag unreferenced OS-level metadata.
RUN apt-get purge -y python3-pip python3-venv && apt-get autoremove -y \
    && rm -rf /usr/lib/python3/dist-packages/pip* \
              /usr/lib/python3/dist-packages/wheel* \
              /usr/lib/python3/dist-packages/setuptools* \
              /usr/share/python-wheels

# Default cache directory (overridden at runtime via HF_HOME / ESMCFOLD_CCD_PATH)
# Disable Xet storage backend — crashes with "Cas service error: Request
# Cancelled" when writing to NFS (Filestore) mounts due to unsupported file
# locking / chunked writes in the Rust xet-core client.
ENV HF_HOME=/mnt/nfs/foldrun/esmfold2/cache \
    HF_HUB_DISABLE_XET=1
