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


@pytest.mark.asyncio
async def test_analyze_video_and_image_authorized_gcs_uri_enterprise(mocker):
    from unittest.mock import AsyncMock, MagicMock

    from api.config import settings

    mocker.patch.object(settings, "google_genai_use_enterprise", True)
    mocker.patch.object(settings, "google_cloud_project", "test-project")
    mocker.patch.object(settings, "google_cloud_location", "us-central1")
    mocker.patch.object(settings, "gcs_bucket_name", "authorized-bucket")
    mocker.patch.object(settings, "gcs_media_folder", "dev")

    mock_genai_client = MagicMock()
    mock_generate = AsyncMock()
    mock_generate.return_value = MagicMock(text="ok")
    mock_genai_client.aio.models.generate_content = mock_generate
    mocker.patch(
        "api.services.gemini_client.genai.Client", return_value=mock_genai_client
    )

    client = GeminiClient(storage_client=MagicMock())

    # Authorized video URI
    video_uri = "gs://authorized-bucket/dev/video.mp4"
    result = await client.analyze_video(video_url=video_uri)
    assert result == "ok"
    call_contents = mock_generate.call_args.kwargs["contents"]
    assert call_contents.parts[0].file_data.file_uri == video_uri

    # Authorized image URI
    image_uri = "gs://authorized-bucket/dev/sub/image.jpg"
    result_img = await client.analyze_image(image_url=image_uri)
    assert result_img == "ok"
    call_contents_img = mock_generate.call_args.kwargs["contents"]
    assert call_contents_img.parts[0].file_data.file_uri == image_uri


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_uri",
    [
        "gs://unauthorized-bucket/dev/video.mp4",
        "gs://authorized-bucket/other-folder/video.mp4",
        "gs://authorized-bucket/dev/../other-folder/video.mp4",
        "gs://authorized-bucket/dev/%2e%2e/other-folder/video.mp4",
        "gs://authorized-bucket/dev/",
        "file:///etc/passwd",
        "s3://some-bucket/video.mp4",
    ],
)
async def test_rejects_unauthorized_or_traversal_uris(mocker, bad_uri):
    from unittest.mock import AsyncMock, MagicMock

    from api.config import settings

    mocker.patch.object(settings, "google_genai_use_enterprise", True)
    mocker.patch.object(settings, "google_cloud_project", "test-project")
    mocker.patch.object(settings, "google_cloud_location", "us-central1")
    mocker.patch.object(settings, "gcs_bucket_name", "authorized-bucket")
    mocker.patch.object(settings, "gcs_media_folder", "dev")

    mock_genai_client = MagicMock()
    mock_generate = AsyncMock()
    mock_genai_client.aio.models.generate_content = mock_generate
    mocker.patch(
        "api.services.gemini_client.genai.Client", return_value=mock_genai_client
    )

    client = GeminiClient(storage_client=MagicMock())

    with pytest.raises(ValueError):
        await client.analyze_video(video_url=bad_uri)

    with pytest.raises(ValueError):
        await client.analyze_image(image_url=bad_uri)

    mock_generate.assert_not_called()
