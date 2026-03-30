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

"""Configuration management for OF3 tools."""

import logging
import os

from foldrun_app.core.config import CoreConfig

logger = logging.getLogger(__name__)


class OF3Config(CoreConfig):
    """OpenFold3-specific configuration class extending CoreConfig."""

    def _validate(self):
        """Validate required environment variables including OF3-specific ones."""
        super()._validate()

        of3_required = {
            "OPENFOLD3_COMPONENTS_IMAGE": ("OPENFOLD3_COMPONENTS_IMAGE",),
        }

        missing = []
        for name, alternatives in of3_required.items():
            if not any(os.getenv(var) for var in alternatives):
                missing.append(name)

        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    @property
    def base_image(self) -> str:
        return os.getenv("OPENFOLD3_COMPONENTS_IMAGE")

    @property
    def params_path(self) -> str:
        """NFS-relative path to OF3 model checkpoint file.

        Must point to the .pt file directly (not the directory), because
        OF3's checkpoint loader treats directories as DeepSpeed checkpoints
        requiring a 'latest' pointer file.
        """
        return os.getenv("OF3_PARAMS_PATH", "of3/params/of3_ft3_v1.pt")

    @property
    def ccd_path(self) -> str:
        """NFS-relative path to CCD (Chemical Component Dictionary)."""
        return os.getenv("OF3_CCD_PATH", "of3/ccd")

    @property
    def viewer_url(self) -> str:
        """Cloud Run viewer service URL."""
        return os.getenv("AF2_VIEWER_URL", "")

    def to_dict(self) -> dict:
        """Convert configuration to dictionary."""
        d = super().to_dict()
        d.update(
            {
                "base_image": self.base_image,
                "params_path": self.params_path,
                "ccd_path": self.ccd_path,
            }
        )
        return d
