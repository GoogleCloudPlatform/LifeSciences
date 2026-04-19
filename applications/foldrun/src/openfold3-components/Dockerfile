ARG OF3_VERSION="0.3.1"
FROM openfoldconsortium/openfold3:${OF3_VERSION}

# No GCS libraries needed — all I/O via NFS mounts and KFP artifacts,
# consistent with the AF2 container.

# Do NOT bake in weights — they come from NFS at runtime
# Do NOT bake in databases — they come from NFS
