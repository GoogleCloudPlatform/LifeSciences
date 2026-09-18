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

"""Unit tests for OF3JobAnalysisTool._check_existing_analysis."""

from unittest.mock import MagicMock, patch

from foldrun_app.models.of3.tools.analyze_job import OF3JobAnalysisTool


def test_check_existing_analysis_uses_download_as_text():
    """Verify _check_existing_analysis calls download_as_text() instead of removed download_as_string()."""
    mock_config = MagicMock()
    mock_config.project_id = "test-project"
    tool = OF3JobAnalysisTool(tool_config={}, config=mock_config)

    mock_blob = MagicMock()
    mock_blob.exists.return_value = True
    mock_blob.download_as_string.side_effect = AttributeError(
        "'Blob' object has no attribute 'download_as_string'"
    )
    mock_blob.download_as_text.return_value = '{"status": "completed", "num_samples": 4}'

    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob

    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket

    with patch("foldrun_app.models.of3.tools.analyze_job.storage.Client", return_value=mock_client):
        exists, data = tool._check_existing_analysis("gs://test-bucket/pipeline-root/analysis/")

    assert exists is True
    assert data == {"status": "completed", "num_samples": 4}
    mock_blob.download_as_text.assert_called_once_with()
    mock_blob.download_as_string.assert_not_called()
