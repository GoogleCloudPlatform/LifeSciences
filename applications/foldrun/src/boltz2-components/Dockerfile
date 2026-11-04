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

FROM nvidia/cuda:12.6.3-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive

# Install base dependencies and Python 3.12 virtual environment support
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    wget \
    git \
    python3 \
    python3-venv \
    hmmer \
    && python3 -m venv /opt/venv \
    && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# Upgrade pip to >=26.2.2 with hash verification (vendors patched certifi >=2026.10.19)
COPY requirements-pip.txt .
RUN pip3 install --no-cache-dir --require-hashes -r requirements-pip.txt

# Install locked dependencies (including boltz and kfp) with strict hash verification.
# --no-deps is used because overrides.txt upgrades exact pins declared in upstream
# boltz==2.2.1 metadata to patched versions already resolved in requirements.lock.
COPY requirements.lock .
RUN pip3 install --no-cache-dir --require-hashes --no-deps \
    --extra-index-url https://download.pytorch.org/whl/cu126 \
    -r requirements.lock

# Allowlist OmegaConf config nodes in boltz so PyTorch weights_only=True loads
# boltz2_conf.ckpt and boltz2_aff.ckpt safely without disabling WeightsUnpickler.
COPY patches/register_checkpoint_safe_globals.py /tmp/register_checkpoint_safe_globals.py
RUN python3 /tmp/register_checkpoint_safe_globals.py && rm /tmp/register_checkpoint_safe_globals.py

# Build-time contract check for Boltz-2 CLI, KFP executor, and patched CA bundle
RUN boltz predict --help >/dev/null \
    && python3 -m kfp.dsl.executor_main --help >/dev/null \
    && python3 -c "import boltz, certifi, kfp, torch; assert tuple(int(x) for x in certifi.__version__.split('.')) >= (2026, 10, 19)"

# No GCS libraries needed — all I/O via NFS mounts and KFP artifacts,
# consistent with the AF2 container.

# Do NOT bake in weights — they come from NFS at runtime
# Do NOT bake in databases — they come from NFS
