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

# Install system dependencies and create /opt/venv in a single layer:
# - python3: Python 3.12 runtime
# - gcc / libc6-dev / python3-dev: minimal C compiler + Python.h required at
#   runtime by Triton JIT (esm==3.4.2 vendors @triton.jit inference kernels for
#   both bf16 and fp8); avoids shipping g++/make/dpkg-dev from build-essential
# - hmmer: jackhmmer for protein MSA generation (full ESMFold2 mode)
# - git: required by KFP component runner
# - python3-venv is purged in the same layer after seeding /opt/venv so OS-level
#   pip/wheel/setuptools packages never enter committed image layers.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    python3-dev \
    gcc \
    libc6-dev \
    hmmer \
    git \
    ca-certificates \
    && python3 -m venv /opt/venv \
    && apt-get purge -y python3-venv \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* \
              /usr/lib/python3/dist-packages/pip* \
              /usr/lib/python3/dist-packages/wheel* \
              /usr/lib/python3/dist-packages/setuptools* \
              /usr/share/python-wheels

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

# Build-time contract check: verify KFP executor, ESMFold2/PyTorch imports,
# patched CA bundle, HMMER, and Triton runtime C-extension compilation (gcc + Python.h).
RUN python3 -m kfp.dsl.executor_main --help >/dev/null \
    && python3 -c "import certifi, esm, kfp, torch; from esm.models.esmfold2 import EsmFold2Model, ESMFold2InputBuilder; assert tuple(int(x) for x in certifi.__version__.split('.')) >= (2026, 10, 19)" \
    && command -v jackhmmer >/dev/null \
    && printf '#include <Python.h>\nint triton_cc_ok(void){return 0;}\n' | gcc -x c -shared -fPIC -I"$(python3 -c 'import sysconfig; print(sysconfig.get_path("include"))')" -o /tmp/triton_cc_check.so - \
    && rm -f /tmp/triton_cc_check.so

# Default cache directory (overridden at runtime via HF_HOME / ESMCFOLD_CCD_PATH)
# Disable Xet storage backend — crashes with "Cas service error: Request
# Cancelled" when writing to NFS (Filestore) mounts due to unsupported file
# locking / chunked writes in the Rust xet-core client.
ENV HF_HOME=/mnt/nfs/foldrun/esmfold2/cache \
    HF_HUB_DISABLE_XET=1
