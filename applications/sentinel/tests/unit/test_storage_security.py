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

"""Unit and regression tests for storage upload allowlist and streaming security headers (CWE-434)."""

import datetime
import io
import os
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("GEMINI_API_KEY", "test-key")

from api.dependencies import get_storage_client
from api.routes.storage import router as storage_router


@pytest.fixture
def mock_storage_client():
    """Create a mock Google Cloud Storage client."""
    client = MagicMock()
    bucket = MagicMock()
    blob = MagicMock()

    blob.content_type = "image/png"
    blob.time_created = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    blob.size = 12
    blob.exists.return_value = True
    blob.open.side_effect = lambda mode="rb": io.BytesIO(b"test content")

    bucket.blob.return_value = blob
    client.bucket.return_value = bucket
    return client


@pytest.fixture
def test_client(mock_storage_client):
    """Create a FastAPI TestClient with mocked storage dependency."""
    app = FastAPI()
    app.include_router(storage_router)
    app.dependency_overrides[get_storage_client] = lambda: mock_storage_client
    return TestClient(app)


def test_upload_html_rejected(test_client):
    """Uploading an HTML file must be rejected with HTTP 400 (CWE-434)."""
    response = test_client.post(
        "/api/v1/storage/upload",
        files={"file": ("xss.html", b"<script>alert(1)</script>", "text/html")},
    )
    assert response.status_code == 400


def test_upload_svg_rejected(test_client):
    """Uploading an SVG file must be rejected with HTTP 400 (CWE-434)."""
    svg_payload = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'
    response = test_client.post(
        "/api/v1/storage/upload",
        files={"file": ("xss.svg", svg_payload, "image/svg+xml")},
    )
    assert response.status_code == 400


def test_upload_mismatched_mime_or_extension_rejected(test_client):
    """Disallowed extension with safe MIME or safe extension with disallowed MIME must be rejected."""
    # Allowed extension, disallowed MIME type
    resp1 = test_client.post(
        "/api/v1/storage/upload",
        files={"file": ("image.png", b"<script>alert(1)</script>", "text/html")},
    )
    assert resp1.status_code == 400

    # Disallowed extension, allowed MIME type
    resp2 = test_client.post(
        "/api/v1/storage/upload",
        files={"file": ("xss.html", b"fake-png", "image/png")},
    )
    assert resp2.status_code == 400


def test_upload_valid_image_and_video_succeeds(test_client, mock_storage_client):
    """Valid image and video uploads continue to succeed with HTTP 200."""
    img_resp = test_client.post(
        "/api/v1/storage/upload",
        files={"file": ("photo.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )
    assert img_resp.status_code == 200
    assert img_resp.json()["name"] == "photo.png"

    vid_resp = test_client.post(
        "/api/v1/storage/upload",
        files={"file": ("clip.mp4", b"fake-mp4-data", "video/mp4")},
    )
    assert vid_resp.status_code == 200
    assert vid_resp.json()["name"] == "clip.mp4"


def test_get_file_includes_security_headers(test_client):
    """GET /api/v1/storage/file/{file_path} includes protective security headers."""
    response = test_client.get("/api/v1/storage/file/dev/photo.png")
    assert response.status_code == 200
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("Content-Security-Policy") == "default-src 'none'"


def test_get_file_range_includes_security_headers(test_client):
    """GET /api/v1/storage/file/{file_path} with Range header includes protective security headers."""
    response = test_client.get(
        "/api/v1/storage/file/dev/photo.png",
        headers={"Range": "bytes=0-3"},
    )
    assert response.status_code == 206
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("Content-Security-Policy") == "default-src 'none'"


@pytest.mark.parametrize(
    "malicious_filename",
    [
        "../traversal_test.png",
        "..\\traversal_test.png",
        "sub/../../traversal_test.png",
        "subdir/photo.png",
        "/etc/photo.png",
    ],
)
def test_upload_rejects_path_traversal_filenames(
    test_client, mock_storage_client, malicious_filename
):
    """Uploading a file with directory traversal or path separators must be rejected (CWE-22)."""
    response = test_client.post(
        "/api/v1/storage/upload",
        files={"file": (malicious_filename, b"\x89PNG\r\n\x1a\n", "image/png")},
    )
    assert response.status_code in (400, 403), (
        f"Expected 400 or 403 for {malicious_filename!r}, got {response.status_code}"
    )
    mock_storage_client.bucket.return_value.blob.assert_not_called()


@pytest.mark.parametrize(
    "invalid_range",
    [
        "bytes=100-50",
        "bytes=10-5",
        "bytes=1000-1050",
        "bytes=-5-10",
        "bytes=-5",
        "items=0-5",
    ],
)
def test_get_file_rejects_invalid_or_reversed_range_headers(
    test_client, mock_storage_client, invalid_range
):
    """GET /api/v1/storage/file/{file_path} with reversed or invalid Range header returns HTTP 416 (CWE-1284)."""
    blob = mock_storage_client.bucket.return_value.blob.return_value
    blob.size = 1000
    response = test_client.get(
        "/api/v1/storage/file/dev/photo.png",
        headers={"Range": invalid_range},
    )
    assert response.status_code == 416
    assert response.headers.get("Content-Range") == "bytes */1000"
