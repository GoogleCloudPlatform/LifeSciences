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

# Upstream only publishes floating minor tags (e.g. 0.5-pixi); pin by digest.
# openfoldconsortium/openfold3:0.5-pixi corresponds to v0.5.0 ("OpenBind v0").
ARG OF3_VERSION="0.5-pixi@sha256:8764f7320ff12c52e9b9ec6bb0047f71df0ab197d401d04a78a51f48e9913335"

FROM openfoldconsortium/openfold3:${OF3_VERSION}

# HMMER (jackhmmer/nhmmer) is required by the FoldRun MSA component.
# The pixi openfold3-cuda12 environment does not include hmmer (unlike the legacy conda image).
# Also configure /etc/ld.so.conf.d/nvidia.conf to match nvidia/cuda base images, because
# upstream built 0.5-pixi FROM ubuntu:22.04 instead of FROM nvidia/cuda.
RUN apt-get update && apt-get install -y --no-install-recommends \
        hmmer \
    && rm -rf /var/lib/apt/lists/* \
    && printf "/usr/local/nvidia/lib\n/usr/local/nvidia/lib64\n" > /etc/ld.so.conf.d/nvidia.conf

# Vertex AI custom training jobs:
# 1. Override ENTRYPOINT when KFP sets `command:`, so the upstream `/opt/activate.sh`
#    wrapper does not run -> bake the pixi openfold3-cuda12 environment into ENV.
# 2. Rely on NVIDIA_VISIBLE_DEVICES and NVIDIA_DRIVER_CAPABILITIES in the image ENV
#    for nvidia-container-runtime to mount /dev/nvidia* and /usr/local/nvidia/lib64
#    (omitted upstream because 0.5-pixi is built FROM ubuntu:22.04).
ENV OF3_ENV_PREFIX=/opt/pixi-project/.pixi/envs/openfold3-cuda12
ENV NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    PATH=${OF3_ENV_PREFIX}/bin:/root/.pixi/bin:/usr/local/nvidia/bin:/usr/local/cuda/bin:${PATH} \
    LD_LIBRARY_PATH=${OF3_ENV_PREFIX}/lib:/usr/local/nvidia/lib:/usr/local/nvidia/lib64 \
    CONDA_PREFIX=${OF3_ENV_PREFIX} \
    CONDA_DEFAULT_ENV=openfold3:openfold3-cuda12 \
    CC=${OF3_ENV_PREFIX}/bin/x86_64-conda-linux-gnu-cc \
    CXX=${OF3_ENV_PREFIX}/bin/x86_64-conda-linux-gnu-c++ \
    NVCC_PREPEND_FLAGS=-ccbin=${OF3_ENV_PREFIX}/bin/x86_64-conda-linux-gnu-cc \
    MKL_INTERFACE_LAYER=LP64,GNU \
    TRITON_PTXAS_PATH=${OF3_ENV_PREFIX}/bin/ptxas \
    TRITON_PTXAS_BLACKWELL_PATH=${OF3_ENV_PREFIX}/bin/ptxas \
    TRITON_CACHE_DIR=${OF3_ENV_PREFIX}/caches/triton \
    TORCH_EXTENSIONS_DIR=${OF3_ENV_PREFIX}/caches/torch_extensions \
    CUTLASS_PATH=DS_IGNORE_CUTLASS_DETECTION

# Build-time contract check (executed without ENTRYPOINT, matching KFP runtime):
# Verify pip, openfold3, torch, CLI binaries, and that all libtorch_cuda.so shared
# libraries resolve cleanly via LD_LIBRARY_PATH.
RUN python3 -m pip --version \
    && python3 -c "import importlib.metadata, os, openfold3, torch; print('openfold3', importlib.metadata.version('openfold3'), 'torch', torch.__version__); lib = os.path.join(os.path.dirname(torch.__file__), 'lib', 'libtorch_cuda.so'); assert os.path.isfile(lib), lib" \
    && ! ldd "${OF3_ENV_PREFIX}"/lib/python*/site-packages/torch/lib/libtorch_cuda.so | grep -q "not found" \
    && command -v run_openfold jackhmmer nhmmer

# Do NOT bake in weights — they come from NFS at runtime
# Do NOT bake in databases — they come from NFS
