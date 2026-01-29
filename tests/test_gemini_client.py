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

import pytest

from api.services.gemini_client import GeminiClient


@pytest.fixture
def gemini_client(mock_storage_client):
    # We need to mock settings or provide mock values to constructor
    from api.config import settings

    original_api_key = settings.gemini_api_key
    settings.gemini_api_key = "test-key"
    client = GeminiClient(api_key="test-key", storage_client=mock_storage_client)
    yield client
    settings.gemini_api_key = original_api_key


def test_extract_video_id_youtube(gemini_client):
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert gemini_client.extract_video_id(url) == "dQw4w9WgXcQ"

    url = "https://youtu.be/dQw4w9WgXcQ?si=test"
    assert gemini_client.extract_video_id(url) == "dQw4w9WgXcQ"


def test_extract_video_id_gcs(gemini_client):
    url = "gs://my-bucket/video.mp4"
    assert gemini_client.extract_video_id(url) == "video.mp4"


def test_extract_video_id_unknown(gemini_client):
    url = "https://example.com/other"
    assert gemini_client.extract_video_id(url) == url
