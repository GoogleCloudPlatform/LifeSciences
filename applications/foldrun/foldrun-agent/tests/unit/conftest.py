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

import os
import pytest

def pytest_configure(config):
    """Set up environment variables for testing."""
    os.environ["GCP_PROJECT_ID"] = "test-project"
    os.environ["GCP_REGION"] = "us-central1"
    os.environ["GCS_BUCKET_NAME"] = "test-bucket"
    os.environ["FILESTORE_ID"] = "test-filestore"
    
    # AF2 required variables
    os.environ["ALPHAFOLD_COMPONENTS_IMAGE"] = "test-af2-image"
    
    # OpenFold3 required variables
    os.environ["OPENFOLD3_COMPONENTS_IMAGE"] = "test-of3-image"

    # Boltz-2 required variables
    os.environ["BOLTZ2_COMPONENTS_IMAGE"] = "test-boltz2-image"
    os.environ["BOLTZ2_CACHE_PATH"] = "boltz2/cache"
