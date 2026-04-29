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

"""Tests for pipeline configuration: retry policies, params location, caching."""

import inspect
import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def mock_env_vars():
    env = {
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "GCP_PROJECT_ID": "test-project",
        "GCP_REGION": "us-central1",
        "GCP_ZONE": "us-central1-a",
        "GCS_BUCKET_NAME": "test-bucket",
        "GCS_DATABASES_BUCKET": "test-databases-bucket",
        "FILESTORE_ID": "test-nfs",
        "ALPHAFOLD_COMPONENTS_IMAGE": "test-image:latest",
        "GEMINI_MODEL": "gemini-3-flash-preview",
        "GOOGLE_CLOUD_PROJECT": "test-project",
        "GOOGLE_CLOUD_LOCATION": "global",
    }
    with patch.dict(os.environ, env, clear=False):
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            yield


# ------------------------------------------------------------------ #
# Pipeline retry policies
# ------------------------------------------------------------------ #


class TestPipelineRetryPolicies:
    """All pipeline tasks should have retry policies for transient errors."""

    @staticmethod
    def _read_pipeline_source():
        """Read pipeline source directly to avoid import issues with compile-time config module."""
        pipeline_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "foldrun_app",
            "models",
            "af2",
            "pipeline",
            "pipelines",
            "alphafold_inference_pipeline.py",
        )
        with open(pipeline_path) as f:
            return f.read()

    def test_pipeline_source_has_set_retry(self):
        """Pipeline definition includes set_retry on all task types."""
        source = self._read_pipeline_source()
        retry_count = source.count(".set_retry(")
        assert retry_count == 4, (
            f"Expected 4 set_retry() calls (config, data, predict, relax), found {retry_count}"
        )

    def test_retry_on_configure_run(self):
        """ConfigureRunOp has retry policy."""
        source = self._read_pipeline_source()
        config_idx = source.index("AF2 Configure Run")
        data_idx = source.index("DataPipelineOp")
        retry_after_config = source.index("set_retry", config_idx)
        assert retry_after_config < data_idx, (
            "ConfigureRunOp must have set_retry before DataPipelineOp"
        )

    def test_retry_on_predict(self):
        """PredictOp has retry policy."""
        source = self._read_pipeline_source()
        predict_idx = source.index("model_predict_task = JobPredictOp")
        relax_idx = source.index("relax_protein_task = JobRelaxOp")
        retry_after_predict = source.index("set_retry", predict_idx)
        assert retry_after_predict < relax_idx, "PredictOp must have set_retry"

    def test_retry_on_relax(self):
        """RelaxOp has retry policy."""
        source = self._read_pipeline_source()
        relax_idx = source.index("relax_protein_task = JobRelaxOp")
        retry_after_relax = source.index("set_retry", relax_idx)
        assert retry_after_relax > relax_idx, "RelaxOp must have set_retry"


# ------------------------------------------------------------------ #
# Model params location
# ------------------------------------------------------------------ #


class TestModelParamsLocation:
    """Model params should read from databases bucket with model namespace."""

    def test_params_use_databases_bucket(self):
        """MODEL_PARAMS_GCS_LOCATION points to databases bucket, not pipeline bucket."""
        from foldrun_app.models.af2.base import AF2Tool

        source = inspect.getsource(AF2Tool._setup_compile_env)
        assert "databases_bucket_name" in source, (
            "MODEL_PARAMS_GCS_LOCATION should use databases_bucket_name"
        )
        assert "alphafold2" in source, (
            "MODEL_PARAMS_GCS_LOCATION should include alphafold2 namespace"
        )

    def test_params_download_subdir_namespaced(self):
        """AlphaFold params download to alphafold2/params/ subdir."""
        from foldrun_app.models.af2.tools.download_database import DATABASE_SUBDIRS

        assert DATABASE_SUBDIRS["alphafold_params"] == "alphafold2/params"


# ------------------------------------------------------------------ #
# Pipeline caching for retry
# ------------------------------------------------------------------ #


class TestPipelineCaching:
    """Pipeline submissions should enable caching for efficient retries."""

    def test_monomer_enables_caching(self):
        """Monomer submission enables pipeline caching."""
        from foldrun_app.models.af2.tools.submit_monomer import AF2SubmitMonomerTool

        source = inspect.getsource(AF2SubmitMonomerTool.run)
        assert "'enable_caching': True" in source or '"enable_caching": True' in source

    def test_multimer_enables_caching(self):
        """Multimer submission enables pipeline caching."""
        from foldrun_app.models.af2.tools.submit_multimer import AF2SubmitMultimerTool

        source = inspect.getsource(AF2SubmitMultimerTool.run)
        assert "'enable_caching': True" in source or '"enable_caching": True' in source


# ------------------------------------------------------------------ #
# Agent instruction includes retry guidance
# ------------------------------------------------------------------ #


class TestAgentRetryGuidance:
    """Agent instruction should guide users on retrying failed jobs."""

    def test_instruction_mentions_retry(self):
        """Agent instruction includes retry guidance for failed jobs."""
        from foldrun_app.agent import AGENT_INSTRUCTION

        assert "Retry failed jobs" in AGENT_INSTRUCTION
        assert "caching" in AGENT_INSTRUCTION.lower()
        assert "get_job_details" in AGENT_INSTRUCTION
