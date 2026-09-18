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

"""Unit tests for AF2GetResultsTool path traversal protection."""

import os
from unittest.mock import MagicMock, patch

import pytest

from foldrun_app.models.af2.tools.get_results import AF2GetResultsTool


def _make_mock_job(state_name: str = "PIPELINE_STATE_SUCCEEDED") -> MagicMock:
    job = MagicMock()
    job.state.name = state_name
    job.runtime_config.gcs_output_directory = "gs://test-bucket/pipeline-root"
    job.job_detail = None
    return job


class TestAF2GetResultsPathTraversal:
    """Tests for CWE-22 path traversal prevention in AF2GetResultsTool."""

    def _make_tool(self) -> AF2GetResultsTool:
        tool_config = MagicMock()
        config = MagicMock()
        config.project_id = "test-project"
        config.region = "us-central1"
        return AF2GetResultsTool(tool_config=tool_config, config=config)

    @patch("foldrun_app.models.af2.tools.get_results.get_task_details")
    @patch("foldrun_app.models.af2.tools.get_results.get_pipeline_job")
    def test_rejects_output_dir_traversal_sequence(
        self, mock_get_job: MagicMock, mock_get_tasks: MagicMock, tmp_path
    ):
        """output_dir containing '..' traversal sequences must be rejected."""
        mock_get_job.return_value = _make_mock_job()
        mock_get_tasks.return_value = {
            "predictions": [
                {
                    "model_name": "model_1",
                    "ranking_confidence": 0.95,
                    "uri": "gs://test-bucket/unrelaxed_model_1.pdb",
                }
            ]
        }
        tool = self._make_tool()
        traversal_dir = os.path.join(str(tmp_path), "..", "escaped_dir")

        with pytest.raises(ValueError, match=r"traversal|output_dir"):
            tool.run({"job_id": "job-123", "output_dir": traversal_dir})

    @patch("foldrun_app.models.af2.tools.get_results.get_task_details")
    @patch("foldrun_app.models.af2.tools.get_results.get_pipeline_job")
    def test_rejects_output_dir_outside_allowed_roots(
        self, mock_get_job: MagicMock, mock_get_tasks: MagicMock
    ):
        """output_dir outside allowed directories (e.g. /etc/...) must be rejected."""
        mock_get_job.return_value = _make_mock_job()
        mock_get_tasks.return_value = {
            "predictions": [
                {
                    "model_name": "model_1",
                    "ranking_confidence": 0.95,
                    "uri": "gs://test-bucket/unrelaxed_model_1.pdb",
                }
            ]
        }
        tool = self._make_tool()

        with pytest.raises(ValueError, match="output_dir"):
            tool.run({"job_id": "job-123", "output_dir": "/etc/foldrun_evil"})

    @patch("foldrun_app.models.af2.tools.get_results.get_task_details")
    @patch("foldrun_app.models.af2.tools.get_results.get_pipeline_job")
    def test_sanitizes_model_name_with_basename(
        self, mock_get_job: MagicMock, mock_get_tasks: MagicMock, tmp_path
    ):
        """Malicious model_name containing path separators must be sanitized with os.path.basename."""
        mock_get_job.return_value = _make_mock_job()
        mock_get_tasks.return_value = {
            "predictions": [
                {
                    "model_name": "../../outside_model",
                    "ranking_confidence": 0.91,
                    "uri": "gs://test-bucket/unrelaxed_model.pdb",
                }
            ]
        }
        tool = self._make_tool()
        downloaded_paths = []
        tool._download_from_gcs = lambda uri, dest: downloaded_paths.append(dest)

        out_dir = str(tmp_path / "results")
        result = tool.run({"job_id": "job-123", "output_dir": out_dir})

        assert result["status"] == "success"
        assert len(downloaded_paths) == 1
        assert downloaded_paths[0] == os.path.join(
            os.path.realpath(out_dir), "unrelaxed_outside_model.pdb"
        )
