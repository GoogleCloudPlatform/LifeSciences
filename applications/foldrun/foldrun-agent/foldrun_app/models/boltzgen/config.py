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

"""Configuration management for BOLTZGEN tools."""

import logging
import os

from foldrun_app.core.config import CoreConfig

logger = logging.getLogger(__name__)


class BOLTZGENConfig(CoreConfig):
    """BoltzGen-specific configuration class extending CoreConfig."""

    def _validate(self):
        """Validate required environment variables including BOLTZGEN-specific ones."""
        super()._validate()

        boltzgen_required = {
            "BOLTZGEN_COMPONENTS_IMAGE": ("BOLTZGEN_COMPONENTS_IMAGE",),
        }

        missing = []
        for name, alternatives in boltzgen_required.items():
            if not any(os.getenv(var) for var in alternatives):
                missing.append(name)

        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    @property
    def base_image(self) -> str:
        return os.getenv("BOLTZGEN_COMPONENTS_IMAGE")

    @property
    def cache_path(self) -> str:
        """NFS-relative path to BoltzGen cache directory.

        Contains boltzgen checkpoints and inference data.
        Passed as --cache to `boltzgen run`.
        """
        return os.getenv("BOLTZGEN_CACHE_PATH", "boltzgen/cache")

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
                "cache_path": self.cache_path,
            }
        )
        return d
