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

import datetime
import io
from unittest.mock import MagicMock

import pytest

from api.config import settings


def test_list_files(client, mock_storage_client):
    mock_bucket = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket

    # We need to simulate a list of blobs
    mock_blob1 = MagicMock()
    mock_blob1.name = f"{settings.gcs_media_folder}/file1.mp4"
    mock_blob1.size = 1000
    mock_blob1.updated = datetime.datetime.now()
    mock_blob1.time_created = datetime.datetime.now()
    mock_blob1.content_type = "video/mp4"

    mock_bucket.list_blobs.return_value = [mock_blob1]

    response = client.get("/api/v1/storage/list")
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "file1.mp4"


def test_upload_file(client, mock_storage_client):
    mock_bucket = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket
    mock_blob = MagicMock()
    mock_blob.content_type = "video/mp4"
    mock_blob.time_created = datetime.datetime.now()
    mock_bucket.blob.return_value = mock_blob

    file_content = b"fake video content"
    files = {"file": ("test.mp4", file_content, "video/mp4")}

    response = client.post("/api/v1/storage/upload", files=files)

    assert response.status_code == 200, response.text
    data = response.json()
    assert "gs://" in data["uri"]
    mock_blob.upload_from_file.assert_called_once()


def test_delete_file(client, mock_storage_client):
    mock_bucket = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket
    mock_blob = MagicMock()
    mock_blob.exists.return_value = True
    mock_bucket.blob.return_value = mock_blob

    valid_path = f"{settings.gcs_media_folder}/test.mp4"
    response = client.delete(f"/api/v1/storage/file/{valid_path}")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "success"
    mock_blob.delete.assert_called_once()


def test_get_file_valid_media_path(client, mock_storage_client):
    mock_bucket = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket
    mock_blob = MagicMock()
    mock_blob.exists.return_value = True
    mock_blob.size = 11
    mock_blob.content_type = "video/mp4"
    mock_blob.open.side_effect = lambda mode="rb": io.BytesIO(b"hello world")
    mock_bucket.blob.return_value = mock_blob

    valid_path = f"{settings.gcs_media_folder}/nested/video.mp4"
    response = client.get(f"/api/v1/storage/file/{valid_path}")

    assert response.status_code == 200, response.text
    assert response.content == b"hello world"


@pytest.mark.parametrize(
    "malicious_path",
    [
        "backups/database.sql",
        "confidential/secrets.json",
        "config/credentials.json",
        f"{settings.gcs_media_folder}_extra/secret.txt",
        f"{settings.gcs_media_folder}/../backups/database.sql",
        f"{settings.gcs_media_folder}/sub/../../confidential/secrets.json",
        f"{settings.gcs_media_folder}\\..\\backups\\database.sql",
    ],
)
def test_get_file_rejects_out_of_scope_and_traversal_paths(
    client, mock_storage_client, malicious_path
):
    mock_bucket = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket

    response = client.get(f"/api/v1/storage/file/{malicious_path}")
    assert response.status_code in (400, 403), (
        f"Expected 400 or 403 for {malicious_path!r}, got {response.status_code}"
    )
    mock_bucket.blob.assert_not_called()


@pytest.mark.parametrize(
    "malicious_path",
    [
        "backups/database.sql",
        "confidential/secrets.json",
        "config/credentials.json",
        f"{settings.gcs_media_folder}_extra/secret.txt",
        f"{settings.gcs_media_folder}/../backups/database.sql",
        f"{settings.gcs_media_folder}/sub/../../confidential/secrets.json",
        f"{settings.gcs_media_folder}\\..\\backups\\database.sql",
    ],
)
def test_delete_file_rejects_out_of_scope_and_traversal_paths(
    client, mock_storage_client, malicious_path
):
    mock_bucket = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket

    response = client.delete(f"/api/v1/storage/file/{malicious_path}")
    assert response.status_code in (400, 403), (
        f"Expected 400 or 403 for {malicious_path!r}, got {response.status_code}"
    )
    mock_bucket.blob.assert_not_called()
