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
"""Config definitions for BOLTZ2 pipeline execution."""

import os

# Container image
BOLTZ2_COMPONENTS_IMAGE = os.getenv("BOLTZ2_COMPONENTS_IMAGE")

# NFS configuration (shared with AF2)
NFS_SERVER = os.getenv("NFS_SERVER")
NFS_PATH = os.getenv("NFS_PATH")
NFS_MOUNT_POINT = os.getenv("NFS_MOUNT_POINT", "/mnt/nfs/foldrun")
NETWORK = os.getenv("NETWORK")

# Boltz-2 cache directory on NFS — must contain boltz2_conf.ckpt and mols/
BOLTZ2_CACHE_PATH = os.getenv("BOLTZ2_CACHE_PATH", "boltz2/cache")

# MSA machine type (CPU-only, same as AF2 data pipeline)
MSA_MACHINE_TYPE = os.getenv("MSA_MACHINE_TYPE", "c2-standard-16")

# Predict GPU hardware
PREDICT_MACHINE_TYPE = os.getenv("PREDICT_MACHINE_TYPE", "a2-highgpu-1g")
PREDICT_ACCELERATOR_TYPE = os.getenv("PREDICT_ACCELERATOR_TYPE", "NVIDIA_TESLA_A100")
PREDICT_ACCELERATOR_COUNT = int(os.getenv("PREDICT_ACCELERATOR_COUNT", "1"))

# DWS scheduling
DWS_MAX_WAIT_HOURS = int(os.getenv("DWS_MAX_WAIT_HOURS", "168"))  # 7 days default

# Shared database paths (relative to NFS mount point)
UNIREF90_PATH = os.getenv("UNIREF90_PATH", "uniref90/uniref90.fasta")
MGNIFY_PATH = os.getenv("MGNIFY_PATH", "mgnify/mgy_clusters_2022_05.fa")
PDB_SEQRES_PATH = os.getenv("PDB_SEQRES_PATH", "pdb_seqres/pdb_seqres.txt")
UNIPROT_PATH = os.getenv("UNIPROT_PATH", "uniprot/uniprot.fasta")

# Note: RNA, DNA, ligand chains do not support external MSA in Boltz-2 schema
