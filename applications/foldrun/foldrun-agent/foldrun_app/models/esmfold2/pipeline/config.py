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
"""Config definitions for ESMFold2 / ESMFold2-Fast pipeline execution."""

import os

# Container image
ESMFOLD2_COMPONENTS_IMAGE = os.getenv("ESMFOLD2_COMPONENTS_IMAGE")

# NFS configuration (shared with AF2/OF3/Boltz-2)
NFS_SERVER = os.getenv("NFS_SERVER")
NFS_PATH = os.getenv("NFS_PATH")
NFS_MOUNT_POINT = os.getenv("NFS_MOUNT_POINT", "/mnt/nfs/foldrun")
NETWORK = os.getenv("NETWORK")

# ESMFold2 cache directories on NFS
ESMFOLD2_FAST_CACHE_PATH = os.getenv("ESMFOLD2_FAST_CACHE_PATH", "esmfold2/fast")
ESMFOLD2_CACHE_PATH = os.getenv("ESMFOLD2_CACHE_PATH", "esmfold2/full")

# MSA machine type (CPU-only, used only when use_msa=True for full ESMFold2)
MSA_MACHINE_TYPE = os.getenv("MSA_MACHINE_TYPE", "n4-standard-8")

# Predict GPU hardware
PREDICT_MACHINE_TYPE = os.getenv("PREDICT_MACHINE_TYPE", "a2-highgpu-1g")
PREDICT_ACCELERATOR_TYPE = os.getenv("PREDICT_ACCELERATOR_TYPE", "NVIDIA_TESLA_A100")
PREDICT_ACCELERATOR_COUNT = int(os.getenv("PREDICT_ACCELERATOR_COUNT", "1"))

# DWS scheduling
DWS_MAX_WAIT_HOURS = int(os.getenv("DWS_MAX_WAIT_HOURS", "168"))  # 7 days default

# Shared database paths (relative to NFS mount point, used when use_msa=True)
UNIREF90_PATH = os.getenv("UNIREF90_PATH", "uniref90/uniref90.fasta")
MGNIFY_PATH = os.getenv("MGNIFY_PATH", "mgnify/mgy_clusters_2022_05.fa")
