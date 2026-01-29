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

import pytest


@pytest.mark.asyncio
async def test_analyze_endpoint_video(client, mock_gemini_client):
    # Mock data
    mock_issues_data = {
        "issues": [
            {
                "start_timestamp": "00:10",
                "end_timestamp": "00:15",
                "severity": "high",
                "category": "medical_accuracy",
                "description": "Incorrect dosage.",
            }
        ],
        "summary": "Summary",
    }
    mock_gemini_client.analyze_video.return_value = json.dumps(mock_issues_data)
    mock_gemini_client.extract_video_id.return_value = "dQw4w9WgXcQ"

    response = client.post(
        "/api/v1/analyze",
        json={
            "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "speed": "fast",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total_issues"] == 1
    assert data["issues"][0]["severity"] == "high"
