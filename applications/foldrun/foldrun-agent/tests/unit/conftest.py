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
from unittest.mock import patch

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

    # Optional generative binder models disabled by default in baseline unit tests
    for opt_var in (
        "BOLTZGEN_COMPONENTS_IMAGE",
        "RFD4_PROTEINA_COMPONENTS_IMAGE",
        "BINDCRAFT_COMPONENTS_IMAGE",
    ):
        os.environ.pop(opt_var, None)


@pytest.fixture(autouse=True)
def _isolate_gcp_clients():
    """Ensure unit tests never leak singleton clients, configs, env vars, or parent .env files."""
    from dotenv import load_dotenv as _real_load_dotenv

    import foldrun_app.core.base_tool as base_tool

    env_snapshot = dict(os.environ)

    def _reset_startup_configs():
        for mod_name in (
            "foldrun_app.models.af2.startup",
            "foldrun_app.models.af3.startup",
            "foldrun_app.models.of3.startup",
            "foldrun_app.models.boltz2.startup",
            "foldrun_app.models.boltzgen.startup",
            "foldrun_app.models.rfd4_proteina.startup",
            "foldrun_app.models.bindcraft.startup",
        ):
            mod = __import__(mod_name, fromlist=["_config"]) if mod_name in os.sys.modules else None
            if mod is not None and hasattr(mod, "_config"):
                mod._config = None

    _reset_startup_configs()
    base_tool._vertex_initialized = False
    base_tool._shared_storage_client = None
    with (
        patch(
            "foldrun_app.core.config.load_dotenv",
            side_effect=lambda dotenv_path=None, *a, **kw: (
                _real_load_dotenv(dotenv_path, *a, **kw) if dotenv_path else False
            ),
        ),
        patch("google.cloud.aiplatform.init"),
        patch("google.cloud.storage.Client"),
    ):
        yield
    os.environ.clear()
    os.environ.update(env_snapshot)
    _reset_startup_configs()
    base_tool._vertex_initialized = False
    base_tool._shared_storage_client = None
