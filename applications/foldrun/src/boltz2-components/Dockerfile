FROM nvidia/cuda:12.2.2-cudnn8-runtime-ubuntu22.04

# Install base dependencies and python
RUN apt-get update && apt-get install -y \
    wget \
    git \
    python3 \
    python3-pip \
    hmmer \
    && rm -rf /var/lib/apt/lists/*

# Pin PyTorch to a cu121 wheel BEFORE installing Boltz-2.
# boltz[cuda] would otherwise resolve and install the latest PyTorch wheel,
# which as of Boltz-2 2.2.x is compiled against CUDA 12.4+. Agent Platform A100
# nodes report CUDA driver version 12.2 (12020 in PyTorch's encoding), which
# is too old for CUDA 12.4-compiled PyTorch and causes a hard RuntimeError at
# GPU initialization. Pinning here ensures boltz uses an already-installed
# compatible PyTorch rather than upgrading it.
RUN pip3 install --no-cache-dir \
    "torch>=2.2,<2.5" \
    --index-url https://download.pytorch.org/whl/cu121

# Install Boltz-2 package. Using boltz (not boltz[cuda]) so pip does not
# pull cuequivariance_ops_torch_cu12 — that package also links against
# a newer CUDA runtime. The --no_kernels flag at predict time disables
# the cuequivariance fast-path and falls back to standard PyTorch ops.
ARG BOLTZ_VERSION="2.2.1"
RUN pip3 install --no-cache-dir "boltz==${BOLTZ_VERSION}"

# No GCS libraries needed — all I/O via NFS mounts and KFP artifacts,
# consistent with the AF2 container.

# Do NOT bake in weights — they come from NFS at runtime
# Do NOT bake in databases — they come from NFS
