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

FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04
ARG RFD4_VERSION="aaccdd2ad0720c180468df5f9cf037a713d403e5"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install base dependencies and uv (including X11/Render libs required by openbabel-wheel)
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    git \
    build-essential \
    ca-certificates \
    kalign \
    libxrender1 \
    libxext6 \
    libsm6 \
    libice6 \
    libx11-6 \
    libcairo2 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.6.6 /uv /uvx /bin/

# Create Python 3.12 environment and install RFD4-Proteina + KFP runtime dependencies
RUN uv venv /opt/venv --python 3.12
ENV PATH="/opt/venv/bin:$PATH"

RUN git init /opt/rfd4-proteina && \
    git -C /opt/rfd4-proteina remote add origin https://github.com/RosettaCommons/RFD4-Proteina.git && \
    git -C /opt/rfd4-proteina fetch --depth 1 origin "${RFD4_VERSION}" && \
    git -C /opt/rfd4-proteina checkout FETCH_HEAD && \
    uv pip install --no-cache \
        torch \
        diffusers \
        safetensors \
        transformers \
        accelerate \
        hydra-core \
        rootutils \
        gemmi \
        biopython \
        google-cloud-storage \
        pyyaml \
        kfp && \
    (uv pip install --no-cache -e /opt/rfd4-proteina || uv pip install --no-cache rfd4-proteina) && \
    python -c "from openbabel import openbabel, pybel; from rfproteina.generate import main_hydra; print('RFD4-Proteina & OpenBabel imports OK')"

# Do NOT bake in weights — they come from /mnt/nfs/foldrun/rfd4_proteina/cache at runtime
# Do NOT bake in databases — they come from NFS
