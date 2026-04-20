ARG OF3_VERSION="0.3.1"
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
