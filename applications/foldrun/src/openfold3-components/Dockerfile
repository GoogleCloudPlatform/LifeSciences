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

ARG OF3_VERSION="0.4.0"

FROM openfoldconsortium/openfold3:${OF3_VERSION}

# The base OF3 image installs jackhmmer via conda (at /opt/conda/bin/jackhmmer).
# That binary is dynamically linked to libopenblas.so.0, but the conda environment
# doesn't include OpenBLAS, causing exit code 127 at runtime.
# Installing libopenblas0 via apt puts the library in the system path (/usr/lib/...)
# where the dynamic linker finds it for the conda jackhmmer binary.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libopenblas0 \
    && rm -rf /var/lib/apt/lists/*

# Do NOT bake in weights — they come from NFS at runtime
# Do NOT bake in databases — they come from NFS
