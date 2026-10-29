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

"""Config definitions for BOLTZGEN pipeline execution."""

import os

# Container image
BOLTZGEN_COMPONENTS_IMAGE = os.getenv("BOLTZGEN_COMPONENTS_IMAGE")

# NFS configuration
NFS_SERVER = os.getenv("NFS_SERVER")
NFS_PATH = os.getenv("NFS_PATH")
NFS_MOUNT_POINT = os.getenv("NFS_MOUNT_POINT", "/mnt/nfs/foldrun")
NETWORK = os.getenv("NETWORK")

# BoltzGen cache directory on NFS — contains checkpoints and inference data
BOLTZGEN_CACHE_PATH = os.getenv("BOLTZGEN_CACHE_PATH", "boltzgen/cache")

# Predict GPU hardware
PREDICT_MACHINE_TYPE = os.getenv("PREDICT_MACHINE_TYPE", "a2-highgpu-1g")
PREDICT_ACCELERATOR_TYPE = os.getenv("PREDICT_ACCELERATOR_TYPE", "NVIDIA_TESLA_A100")
PREDICT_ACCELERATOR_COUNT = int(os.getenv("PREDICT_ACCELERATOR_COUNT", "1"))

# DWS scheduling
DWS_MAX_WAIT_HOURS = int(os.getenv("DWS_MAX_WAIT_HOURS", "168"))  # 7 days default
