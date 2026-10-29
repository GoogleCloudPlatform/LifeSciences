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

"""Configuration management for BindCraft tools."""

import logging
import os

from foldrun_app.core.config import CoreConfig

logger = logging.getLogger(__name__)


class BINDCRAFTConfig(CoreConfig):
    """BindCraft-specific configuration class extending CoreConfig."""

    def _validate(self):
        """Validate required environment variables including BINDCRAFT-specific ones."""
        super()._validate()

        bindcraft_required = {
            "BINDCRAFT_COMPONENTS_IMAGE": ("BINDCRAFT_COMPONENTS_IMAGE",),
        }

        missing = []
        for name, alternatives in bindcraft_required.items():
            if not any(os.getenv(var) for var in alternatives):
                missing.append(name)

        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    @property
    def base_image(self) -> str:
        return os.getenv("BINDCRAFT_COMPONENTS_IMAGE")

    @property
    def af2_params_path(self) -> str:
        """NFS-relative path to AlphaFold2 params directory.

        Shared with AF2 pipeline to avoid duplicate weights on NFS.
        """
        return os.getenv("AF2_PARAMS_PATH", "alphafold2/params")

    @property
    def mpnn_weights_path(self) -> str:
        """NFS-relative path to SolubleMPNN / ProteinMPNN weights directory."""
        return os.getenv("BINDCRAFT_MPNN_WEIGHTS_PATH", "bindcraft/mpnn_weights")

    @property
    def viewer_url(self) -> str:
        """Cloud Run viewer service URL."""
        return os.getenv("FOLDRUN_VIEWER_URL", "")

    def to_dict(self) -> dict:
        """Convert configuration to dictionary."""
        d = super().to_dict()
        d.update(
            {
                "base_image": self.base_image,
                "af2_params_path": self.af2_params_path,
                "mpnn_weights_path": self.mpnn_weights_path,
            }
        )
        return d
