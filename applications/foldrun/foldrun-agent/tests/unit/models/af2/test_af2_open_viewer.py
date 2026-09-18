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

"""Unit tests for AF2OpenViewerTool URL validation and security checks (Finding 4.21)."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from foldrun_app.models.af2.tools.open_viewer import AF2OpenViewerTool


def _make_tool(viewer_url: str | None = "https://viewer.example.com") -> AF2OpenViewerTool:
    config = SimpleNamespace(
        viewer_url=viewer_url,
        project_id="test-project",
        region="us-central1",
        bucket_name="test-bucket",
        filestore_id="test-fs",
        filestore_ip="10.0.0.2",
        filestore_network="default",
    )
    tool_config = {"name": "af2_open_viewer", "description": "Open viewer"}
    return AF2OpenViewerTool(tool_config=tool_config, config=config)


class TestAF2OpenViewerSecurity:
    """Security validation tests for AF2OpenViewerTool."""

    def test_valid_url_and_job_id_defaults_open_browser_false(self):
        tool = _make_tool("https://viewer.example.com")
        with patch("foldrun_app.models.af2.tools.open_viewer.webbrowser.open") as mock_open:
            result = tool.run({"job_id": "af2-job_123"})
            assert result["viewer_url"] == "https://viewer.example.com/job/af2-job_123"
            assert result["browser_opened"] is False
            mock_open.assert_not_called()

    def test_explicit_open_browser_true(self):
        tool = _make_tool("https://viewer.example.com")
        with patch("foldrun_app.models.af2.tools.open_viewer.webbrowser.open") as mock_open:
            result = tool.run({"job_id": "af2-job_123", "open_browser": True})
            assert result["viewer_url"] == "https://viewer.example.com/job/af2-job_123"
            assert result["browser_opened"] is True
            mock_open.assert_called_once_with("https://viewer.example.com/job/af2-job_123")

    @pytest.mark.parametrize(
        "bad_base_url",
        [
            None,
            "",
            "file:///etc/passwd",
            "/local/path",
            "ftp://viewer.example.com",
            "javascript:alert(1)",
        ],
    )
    def test_rejects_invalid_viewer_base_url(self, bad_base_url):
        tool = _make_tool(bad_base_url)
        with patch("foldrun_app.models.af2.tools.open_viewer.webbrowser.open") as mock_open:
            with pytest.raises(ValueError, match="viewer_base_url"):
                tool.run({"job_id": "valid_job_123", "open_browser": True})
            mock_open.assert_not_called()

    @pytest.mark.parametrize(
        "bad_job_id",
        [
            "../../../etc/passwd",
            "job/../../secret",
            "job?foo=bar",
            "job#fragment",
            "job with spaces",
            "file:///etc/passwd",
        ],
    )
    def test_rejects_traversal_and_invalid_job_id(self, bad_job_id):
        tool = _make_tool("https://viewer.example.com")
        with patch("foldrun_app.models.af2.tools.open_viewer.webbrowser.open") as mock_open:
            with pytest.raises(ValueError, match="Invalid job_id"):
                tool.run({"job_id": bad_job_id, "open_browser": True})
            mock_open.assert_not_called()
