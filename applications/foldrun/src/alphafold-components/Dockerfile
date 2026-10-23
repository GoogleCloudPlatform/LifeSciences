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
RUN wget -q -O- https://mmseqs.com/latest/mmseqs-linux-gpu.tar.gz | tar xz -C /opt \
    && ln -s /opt/mmseqs/bin/mmseqs /usr/local/bin/mmseqs

# Install Miniconda package manager.
ARG MINICONDA_VERSION=py311_24.11.1-0
RUN wget -q -P /tmp \
  https://repo.anaconda.com/miniconda/Miniconda3-${MINICONDA_VERSION}-Linux-x86_64.sh \
    && bash /tmp/Miniconda3-${MINICONDA_VERSION}-Linux-x86_64.sh -b -p /opt/conda \
    && rm /tmp/Miniconda3-${MINICONDA_VERSION}-Linux-x86_64.sh

# Install conda packages.
ENV PATH="/opt/conda/bin:$PATH"
ENV CONDA_PLUGINS_AUTO_ACCEPT_TOS="yes"
RUN conda install --quiet --yes --channel nvidia/label/cuda-${CUDA} \
      conda==24.11.1 'ca-certificates>=2026.10' 'certifi>=2026.10.19' 'pip>=26.2.2' python=3.11 cuda==${CUDA} \
    && conda clean --all --force-pkgs-dirs --yes

# Symlink system libffi if needed to avoid undefined_symbol error before running system tools.
RUN if [ -f /usr/lib/x86_64-linux-gnu/libffi.so.8 ]; then ln -sf /usr/lib/x86_64-linux-gnu/libffi.so.8 /opt/conda/lib/libffi.so.8; elif [ -f /usr/lib/x86_64-linux-gnu/libffi.so.7 ]; then ln -sf /usr/lib/x86_64-linux-gnu/libffi.so.7 /opt/conda/lib/libffi.so.7; fi

# Set LD_LIBRARY_PATH after the fix.
ENV LD_LIBRARY_PATH="/opt/conda/lib:$LD_LIBRARY_PATH"

# Upgrade pip to >=26.2.2 with hashes (vendors patched certifi 2026.10.19)
COPY requirements-pip.txt .
RUN pip3 install --no-cache-dir --require-hashes -r requirements-pip.txt

RUN git clone https://github.com/deepmind/alphafold.git /app/alphafold
RUN git -C /app/alphafold reset --hard $AF2_VERSION
RUN wget -q -P /app/alphafold/alphafold/common/ \
  https://git.scicore.unibas.ch/schwede/openstructure/-/raw/7102c63615b64735c4941278d92b554ec94415f8/modules/mol/alg/src/stereo_chemical_props.txt

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
