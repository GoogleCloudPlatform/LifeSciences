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

"""Configuration management for ESMFold2 / ESMFold2-Fast tools."""

import logging
import os

from foldrun_app.core.config import CoreConfig

logger = logging.getLogger(__name__)


class ESMFOLD2Config(CoreConfig):
    """ESMFold2-specific configuration class extending CoreConfig."""

    def _validate(self):
        """Validate required environment variables including ESMFold2-specific ones."""
        super()._validate()

        esmfold2_required = {
            "ESMFOLD2_COMPONENTS_IMAGE": ("ESMFOLD2_COMPONENTS_IMAGE",),
        }

        missing = []
        for name, alternatives in esmfold2_required.items():
            if not any(os.getenv(var) for var in alternatives):
                missing.append(name)

        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    @property
    def base_image(self) -> str:
        return os.getenv("ESMFOLD2_COMPONENTS_IMAGE", "")

    @property
    def fast_cache_path(self) -> str:
        """NFS-relative path to ESMFold2-Fast checkpoint cache directory."""
        return os.getenv("ESMFOLD2_FAST_CACHE_PATH", "esmfold2/fast")

    @property
    def full_cache_path(self) -> str:
        """NFS-relative path to full ESMFold2 checkpoint cache directory."""
        return os.getenv("ESMFOLD2_CACHE_PATH", "esmfold2/full")

    @property
    def cache_path(self) -> str:
        """Default NFS-relative cache path (ESMFold2-Fast)."""
        return self.fast_cache_path

    def get_cache_path(self, model_variant: str = "fast") -> str:
        """Return NFS-relative checkpoint cache path for ``fast`` or ``full`` variant."""
        if str(model_variant).strip().lower() == "full":
            return self.full_cache_path
        return self.fast_cache_path

    def to_dict(self) -> dict:
        """Convert configuration to dictionary."""
        d = super().to_dict()
        d.update(
            {
                "base_image": self.base_image,
                "cache_path": self.cache_path,
                "fast_cache_path": self.fast_cache_path,
                "full_cache_path": self.full_cache_path,
            }
        )
        return d
