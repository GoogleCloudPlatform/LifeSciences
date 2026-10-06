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

"""Verify all model submit tools use config.pipelines_sa_email, not raw os.environ."""

import inspect


class TestAF2SubmitServiceAccount:
    def test_monomer_uses_config_pipelines_sa_email(self):
        """AF2 monomer submit passes service_account via config, not raw env var."""
        from foldrun_app.models.af2.tools.submit_monomer import AF2SubmitMonomerTool

        source = inspect.getsource(AF2SubmitMonomerTool.run)
        assert "self.config.pipelines_sa_email" in source
        assert 'os.environ.get("PIPELINES_SA_EMAIL")' not in source

    def test_multimer_uses_config_pipelines_sa_email(self):
        """AF2 multimer submit passes service_account via config, not raw env var."""
        from foldrun_app.models.af2.tools.submit_multimer import AF2SubmitMultimerTool

        source = inspect.getsource(AF2SubmitMultimerTool.run)
        assert "self.config.pipelines_sa_email" in source
        assert 'os.environ.get("PIPELINES_SA_EMAIL")' not in source


class TestOF3SubmitServiceAccount:
    def test_uses_config_pipelines_sa_email(self):
        """OF3 submit passes service_account via config, not raw env var."""
        from foldrun_app.models.of3.tools.submit_prediction import OF3SubmitPredictionTool

        source = inspect.getsource(OF3SubmitPredictionTool.run)
        assert "self.config.pipelines_sa_email" in source
        assert 'os.environ.get("PIPELINES_SA_EMAIL")' not in source

    def test_rejects_legacy_checkpoint(self, monkeypatch):
        """OF3 submit refuses deprecated preview-2 checkpoints before hitting GCS or Vertex."""
        from unittest.mock import MagicMock, patch

        from foldrun_app.models.of3.config import OF3Config
        from foldrun_app.models.of3.tools.submit_prediction import OF3SubmitPredictionTool

        monkeypatch.setenv("OPENFOLD3_COMPONENTS_IMAGE", "test-of3:latest")
        monkeypatch.setenv("OF3_PARAMS_PATH", "of3/params/of3-p2-155k.pt")
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            tool = OF3SubmitPredictionTool(
                tool_config={"name": "submit_of3_prediction", "description": "test"},
                config=OF3Config(),
            )
            tool.storage_client = MagicMock()
            result = tool.run({"input": ">seq\nACDEFGHIK\n"})

        assert result["status"] == "error"
        assert "deprecated" in result["message"]
        tool.storage_client.bucket.assert_not_called()


class TestBoltz2SubmitServiceAccount:
    def test_uses_config_pipelines_sa_email(self):
        """Boltz2 submit passes service_account via config, not raw env var."""
        from foldrun_app.models.boltz2.tools.submit_prediction import BOLTZ2SubmitPredictionTool

        source = inspect.getsource(BOLTZ2SubmitPredictionTool.run)
        assert "self.config.pipelines_sa_email" in source
        assert 'os.environ.get("PIPELINES_SA_EMAIL")' not in source
