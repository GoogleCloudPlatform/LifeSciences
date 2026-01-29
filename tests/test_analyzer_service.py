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

import json
from datetime import datetime

import pytest

from api.models.schemas import IssueCategory, Severity
from api.services.analyzer_service import AnalyzerService


@pytest.fixture
def analyzer_service(mock_gemini_client):
    return AnalyzerService(gemini_client=mock_gemini_client)


@pytest.mark.asyncio
async def test_analyze_video(analyzer_service, mock_gemini_client):
    # Mock data
    mock_issues_data = {
        "issues": [
            {
                "start_timestamp": "00:10",
                "end_timestamp": "00:15",
                "severity": "high",
                "category": "medical_accuracy",
                "description": "Incorrect dosage mentioned.",
                "context": "Context text",
            }
        ],
        "summary": "Found one issue.",
    }
    mock_gemini_client.analyze_video.return_value = json.dumps(mock_issues_data)
    mock_gemini_client.extract_video_id.return_value = "dQw4w9WgXcQ"

    video_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    response = await analyzer_service.analyze(video_url=video_url)

    assert response.video_id == "dQw4w9WgXcQ"
    assert response.total_issues == 1
    assert response.issues[0].severity == Severity.HIGH
    assert response.issues[0].category == IssueCategory.MEDICAL_ACCURACY
    assert response.summary == "Found one issue."
    assert isinstance(response.analysis_timestamp, datetime)


@pytest.mark.asyncio
async def test_analyze_image(analyzer_service, mock_gemini_client):
    # Mock data
    mock_issues_data = {
        "issues": [
            {
                "start_timestamp": "00:00",
                "end_timestamp": "00:00",
                "severity": "medium",
                "category": "citation_missing",
                "description": "Missing citation for claim.",
                "location": {"x": 0.5, "y": 0.5},
            }
        ],
        "summary": "Image issue found.",
    }
    mock_gemini_client.analyze_image.return_value = json.dumps(mock_issues_data)

    image_url = "https://example.com/image.jpg"
    response = await analyzer_service.analyze(image_url=image_url)

    assert response.video_id == "image.jpg"
    assert response.total_issues == 1
    assert response.issues[0].severity == Severity.MEDIUM
    assert response.issues[0].location.x == 0.5
    assert response.summary == "Image issue found."


@pytest.mark.asyncio
async def test_analyze_no_input(analyzer_service):
    with pytest.raises(
        ValueError, match="Either video_url, image_url, or image_data must be provided"
    ):
        await analyzer_service.analyze()


@pytest.mark.asyncio
async def test_analyze_multiple_inputs(analyzer_service):
    with pytest.raises(
        ValueError, match="Cannot analyze multiple inputs in the same request"
    ):
        await analyzer_service.analyze(video_url="vid", image_url="img")
