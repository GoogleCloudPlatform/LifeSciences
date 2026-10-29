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
ARG BOLTZGEN_VERSION="0.3.2"

# Install base dependencies and python
RUN apt-get update && apt-get install -y \
    wget \
    git \
    python3 \
    python-is-python3 \
    python3-pip \
    kalign \
    && rm -rf /var/lib/apt/lists/*

# Install boltzgen and required dependencies
# Note: --ignore-installed avoids the Ubuntu 22.04 setuptools 59.6.0 uninstall failure,
# and omegaconf>=2.3.0 prevents PosixPath.read crash in boltzgen.
RUN pip3 install --no-cache-dir --ignore-installed \
    "boltzgen==${BOLTZGEN_VERSION}" \
    "omegaconf>=2.3.0" \
    gemmi \
    biopython \
    google-cloud-storage \
    pyyaml

# Do NOT bake in weights — they come from /mnt/nfs/foldrun/boltzgen/cache at runtime
# Do NOT bake in databases — they come from NFS
