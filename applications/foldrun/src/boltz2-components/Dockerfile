FROM nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04

# Install base dependencies and python
RUN apt-get update && apt-get install -y \
    wget \
    git \
    python3 \
    python3-pip \
    hmmer \
    && rm -rf /var/lib/apt/lists/*

# Install Boltz-2 package and dependencies
ARG BOLTZ_VERSION="2.2.1"
RUN pip3 install --no-cache-dir "boltz[cuda]==${BOLTZ_VERSION}"

# No GCS libraries needed — all I/O via NFS mounts and KFP artifacts,
# consistent with the AF2 container.

# Do NOT bake in weights — they come from NFS at runtime
# Do NOT bake in databases — they come from NFS
