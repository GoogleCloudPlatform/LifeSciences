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

FROM nvidia/cuda:12.2.2-cudnn8-runtime-ubuntu22.04
ARG COLABDESIGN_VERSION="8fd167defd1b0f98e29f00584ff4f3987e6935dc"
ARG FREEBINDCRAFT_VERSION="056ff605371ce46174f2cffb7ea44e7337df4ee5"

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# XLA/GPU memory configurations for ColabDesign / AF2 backpropagation
ENV XLA_PYTHON_CLIENT_PREALLOCATE=false
ENV TF_FORCE_GPU_ALLOW_GROWTH=true
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    git \
    build-essential \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install pinned Miniconda3 (Python 3.11)
RUN wget --quiet https://repo.anaconda.com/miniconda/Miniconda3-py311_24.11.1-0-Linux-x86_64.sh -O /tmp/miniconda.sh && \
    /bin/bash /tmp/miniconda.sh -b -p /opt/conda && \
    rm /tmp/miniconda.sh

ENV PATH=/opt/conda/bin:$PATH
ENV CONDA_PLUGINS_AUTO_ACCEPT_TOS="yes"

# Install CUDA 12.2.2 toolkit + OpenMM and PDBFixer via conda (matching alphafold-components)
RUN conda install --quiet --yes conda==24.11.1 pip python=3.11 && \
    conda install --quiet --yes --channel nvidia/label/cuda-12.2.2 cuda==12.2.2 && \
    conda install --quiet --yes -c conda-forge openmm pdbfixer && \
    conda clean --all --force-pkgs-dirs --yes

ENV LD_LIBRARY_PATH="/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/opt/conda/lib:$LD_LIBRARY_PATH"

RUN pip install --no-cache-dir \
    torch --index-url https://download.pytorch.org/whl/cu121 && \
    pip install --no-cache-dir \
    git+https://github.com/sokrypton/ColabDesign.git@${COLABDESIGN_VERSION} \
    freesasa \
    biopython \
    gemmi \
    scipy \
    pandas \
    matplotlib \
    seaborn \
    tqdm \
    google-cloud-storage \
    pyyaml \
    kfp && \
    pip install --no-cache-dir --upgrade \
    -f https://storage.googleapis.com/jax-releases/jax_cuda_releases.html \
    "numpy<2.0.0" \
    "pandas<2.2.0" \
    "scipy<1.13.0" \
    "dm-haiku==0.0.12" \
    "jax==0.4.26" \
    "jaxlib==0.4.26+cuda12.cudnn89" && \
    python3 -c "import jax, jaxlib, haiku, pandas, scipy, colabdesign; from colabdesign import mk_afdesign_model; print('JAX:', jax.__version__, 'jaxlib:', jaxlib.__version__, 'pandas:', pandas.__version__, 'ColabDesign OK')" && \
    ldconfig

# Clone FreeBindCraft (PyRosetta-free BindCraft distribution) and verify bundled scoring binaries (sc-rs, dssp, FASPR, DAlphaBall)
RUN git init /opt/bindcraft && \
    git -C /opt/bindcraft remote add origin https://github.com/cytokineking/FreeBindCraft.git && \
    git -C /opt/bindcraft fetch --depth 1 origin "${FREEBINDCRAFT_VERSION}" && \
    git -C /opt/bindcraft checkout FETCH_HEAD && \
    chmod +x /opt/bindcraft/functions/dssp /opt/bindcraft/functions/sc /opt/bindcraft/functions/FASPR /opt/bindcraft/functions/DAlphaBall.gcc && \
    ln -sf /opt/bindcraft/functions/FASPR /opt/bindcraft/functions/faspr && \
    ln -sf /opt/bindcraft/functions/sc /usr/local/bin/sc && \
    test -x /opt/bindcraft/functions/sc && \
    test -x /opt/bindcraft/functions/dssp && \
    test -x /opt/bindcraft/functions/FASPR && \
    test -x /opt/bindcraft/functions/DAlphaBall.gcc

# Do NOT bake in AlphaFold2 or ProteinMPNN weights — loaded from NFS at runtime:
# /mnt/nfs/foldrun/alphafold2/params and /mnt/nfs/foldrun/bindcraft/mpnn_weights
