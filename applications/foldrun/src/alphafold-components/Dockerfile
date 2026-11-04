# Copyright 2022 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

ARG CUDA=12.2.2
FROM nvidia/cuda:${CUDA}-cudnn8-runtime-ubuntu22.04
# FROM directive resets ARGS, so we specify again (the value is retained if
# previously set).
ARG CUDA
ARG AF2_VERSION="42719e135a62438aa651d2bc1d143626083c3703"

# Use bash to support string substitution.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update --quiet \
    && apt-get install --no-install-recommends --yes --quiet \
        build-essential \
        ca-certificates \
        cmake \
        cuda-command-line-tools-$(cut -f1,2 -d- <<< ${CUDA//./-}) \
        git \
        hmmer \
        kalign \
        tzdata \
        wget \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get autoremove --yes \
    && apt-get clean

# Compile HHsuite from source.
RUN git clone --branch v3.3.0 --single-branch https://github.com/soedinglab/hh-suite.git /tmp/hh-suite \
    && mkdir /tmp/hh-suite/build \
    && pushd /tmp/hh-suite/build \
    && cmake -DCMAKE_INSTALL_PREFIX=/opt/hhsuite .. \
    && make -j && make install \
    && ln -s /opt/hhsuite/bin/* /usr/bin \
    && popd \
    && rm -rf /tmp/hh-suite

# Install MMseqs2 (GPU-accelerated sequence search, alternative to JackHMMER).
ARG MMSEQS2_VERSION=18-8cc5c
ARG MMSEQS2_GPU_SHA256=83969dd5c7d4c32858c2fc9a4d1024c15e8fe5da768ce76e787ab0195ffd64e7
RUN wget -q -O /tmp/mmseqs-linux-gpu.tar.gz \
      https://github.com/soedinglab/MMseqs2/releases/download/${MMSEQS2_VERSION}/mmseqs-linux-gpu.tar.gz \
    && echo "${MMSEQS2_GPU_SHA256}  /tmp/mmseqs-linux-gpu.tar.gz" | sha256sum -c - \
    && tar xzf /tmp/mmseqs-linux-gpu.tar.gz -C /opt \
    && rm /tmp/mmseqs-linux-gpu.tar.gz \
    && ln -s /opt/mmseqs/bin/mmseqs /usr/local/bin/mmseqs

# Install Miniforge (conda-forge BSD-3-Clause; avoids Anaconda commercial TOS)
# into a temporary bootstrap prefix and create /opt/conda from conda-forge only.
ARG MINIFORGE_VERSION=26.9.4-1
ARG MINIFORGE_SHA256=571b410272cc434f28bc6c5c49ca8ffd4a4d1bcba9f57f1b6baccdb46446cfcc
RUN wget -q -O /tmp/Miniforge3-${MINIFORGE_VERSION}-Linux-x86_64.sh \
      https://github.com/conda-forge/miniforge/releases/download/${MINIFORGE_VERSION}/Miniforge3-${MINIFORGE_VERSION}-Linux-x86_64.sh \
    && echo "${MINIFORGE_SHA256}  /tmp/Miniforge3-${MINIFORGE_VERSION}-Linux-x86_64.sh" | sha256sum -c - \
    && bash /tmp/Miniforge3-${MINIFORGE_VERSION}-Linux-x86_64.sh -b -p /opt/miniforge \
    && rm /tmp/Miniforge3-${MINIFORGE_VERSION}-Linux-x86_64.sh \
    && /opt/miniforge/bin/mamba create --quiet --yes -p /opt/conda \
         --override-channels --channel conda-forge \
         python=3.11 \
         'ca-certificates>=2026.10' \
         'certifi>=2026.10.19' \
         'pip>=26.2.2' \
         cuda-nvcc="$(cut -f1,2 -d. <<< "${CUDA}")" \
         cuda-cudart="$(cut -f1,2 -d. <<< "${CUDA}")" \
         cuda-version="$(cut -f1,2 -d. <<< "${CUDA}")" \
    && /opt/miniforge/bin/mamba clean --all --force-pkgs-dirs --yes \
    && rm -rf /opt/miniforge

ENV PATH="/opt/conda/bin:$PATH"

# Symlink system libffi if needed to avoid undefined_symbol error before running system tools.
RUN if [ -f /usr/lib/x86_64-linux-gnu/libffi.so.8 ]; then ln -sf /usr/lib/x86_64-linux-gnu/libffi.so.8 /opt/conda/lib/libffi.so.8; elif [ -f /usr/lib/x86_64-linux-gnu/libffi.so.7 ]; then ln -sf /usr/lib/x86_64-linux-gnu/libffi.so.7 /opt/conda/lib/libffi.so.7; fi

# Set LD_LIBRARY_PATH after the fix.
ENV LD_LIBRARY_PATH="/opt/conda/lib:$LD_LIBRARY_PATH"

# Upgrade pip to >=26.2.2 with hashes (vendors patched certifi 2026.10.19)
COPY requirements-pip.txt .
RUN pip3 install --no-cache-dir --require-hashes -r requirements-pip.txt

RUN git clone https://github.com/deepmind/alphafold.git /app/alphafold
RUN git -C /app/alphafold reset --hard $AF2_VERSION

# Fetch OpenStructure stereo_chemical_props.txt (commit 7102c636) via
# content-addressed Software Heritage archive with GitHub/upstream fallbacks
# (git.scicore.unibas.ch blocks cloud egress IPs with HTTP 403) and enforce
# cryptographic SHA-256 verification regardless of source.
ARG STEREO_CHEMICAL_PROPS_SHA256=24510899eeb49167cffedec8fa45363a4d08279c0c637a403b452f7d0ac09451
RUN ( \
      wget -q --tries=2 -T 15 -O /app/alphafold/alphafold/common/stereo_chemical_props.txt \
        https://archive.softwareheritage.org/api/1/content/sha256:${STEREO_CHEMICAL_PROPS_SHA256}/raw/ \
      || wget -q --tries=2 -T 15 -O /app/alphafold/alphafold/common/stereo_chemical_props.txt \
        https://raw.githubusercontent.com/xrobin/openstructure/7102c63615b64735c4941278d92b554ec94415f8/modules/mol/alg/src/stereo_chemical_props.txt \
      || wget -q --tries=1 -T 10 -O /app/alphafold/alphafold/common/stereo_chemical_props.txt \
        https://git.scicore.unibas.ch/schwede/openstructure/-/raw/7102c63615b64735c4941278d92b554ec94415f8/modules/mol/alg/src/stereo_chemical_props.txt \
    ) \
    && echo "${STEREO_CHEMICAL_PROPS_SHA256}  /app/alphafold/alphafold/common/stereo_chemical_props.txt" | sha256sum -c -

COPY requirements.lock /app/alphafold/requirements_resolved.txt
RUN pip3 install --no-cache-dir --require-hashes \
    -f https://storage.googleapis.com/jax-releases/jax_cuda_releases.html \
    -r /app/alphafold/requirements_resolved.txt

# Patch: skip unparseable template descriptions instead of crashing.
# Some PDB entries (e.g. 5apu) have formats AF2's parser can't handle.
# Placed after pip install to preserve Docker layer cache.
ADD patches/fix_hmmsearch_parser.py /tmp/fix_hmmsearch_parser.py
RUN python3 /tmp/fix_hmmsearch_parser.py && rm /tmp/fix_hmmsearch_parser.py

WORKDIR /modules
ADD src/components/alphafold_utils.py .

ENV PYTHONPATH=/app/alphafold:/modules
RUN ldconfig
