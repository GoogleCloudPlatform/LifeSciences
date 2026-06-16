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

FROM nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04


# Install base dependencies and python
RUN apt-get update && apt-get install -y \
    wget \
    git \
    python3 \
    python3-pip \
    hmmer \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies from locked requirements with hashes
COPY requirements.txt .
RUN pip3 install --no-cache-dir --require-hashes --extra-index-url https://download.pytorch.org/whl/cu121 -r requirements.txt

# No GCS libraries needed — all I/O via NFS mounts and KFP artifacts,
# consistent with the AF2 container.

# Do NOT bake in weights — they come from NFS at runtime
# Do NOT bake in databases — they come from NFS
