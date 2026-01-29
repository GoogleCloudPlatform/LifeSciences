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

"""
Storage management endpoints.

Handles listing and retrieving files from Google Cloud Storage.
"""

import asyncio
import datetime
import logging
from typing import List

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Path,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from google.cloud import storage
from pydantic import BaseModel

from api.config import settings
from api.dependencies import get_storage_client

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/storage",
    tags=["storage"],
)


class StorageItem(BaseModel):
    name: str
    uri: str
    url: str
    content_type: str
    created: datetime.datetime


class StorageListResponse(BaseModel):
    items: List[StorageItem]


@router.post(
    "/upload",
    response_model=StorageItem,
    summary="Upload file to GCS",
    description="Upload a file to the configured Google Cloud Storage bucket.",
)
async def upload_file(
    file: UploadFile = File(...), client: storage.Client = Depends(get_storage_client)
):
    """
    Upload a file to GCS.
    """
    try:
        bucket = client.bucket(settings.gcs_bucket_name)

        # Create a safe filename (you might want to add more sanitization or UUIDs here)
        file_path = f"{settings.gcs_media_folder}/{file.filename}"
        blob = bucket.blob(file_path)

        # Upload from file object
        await asyncio.to_thread(
            blob.upload_from_file, file.file, content_type=file.content_type
        )

        # Refresh to get metadata like size and updated time
        await asyncio.to_thread(blob.reload)

        proxy_url = f"/api/v1/storage/file/{file_path}"

        return StorageItem(
            name=file.filename,
            uri=f"gs://{settings.gcs_bucket_name}/{file_path}",
            url=proxy_url,
            content_type=blob.content_type,
            created=blob.time_created or datetime.datetime.now(),
        )

    except Exception as e:
        logger.error(f"Error uploading file: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to upload file: {str(e)}",
        )


@router.delete(
    "/file/{file_path:path}",
    summary="Delete file from GCS",
    description="Delete a file from the configured Google Cloud Storage bucket.",
)
async def delete_file(
    file_path: str = Path(..., description="Full path to the file in GCS bucket"),
    client: storage.Client = Depends(get_storage_client),
):
    """
    Delete a file from GCS.
    """
    try:
        bucket = client.bucket(settings.gcs_bucket_name)
        blob = bucket.blob(file_path)

        exists = await asyncio.to_thread(blob.exists)
        if not exists:
            raise HTTPException(status_code=404, detail="File not found")

        await asyncio.to_thread(blob.delete)

        return {"status": "success", "message": f"File {file_path} deleted"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting file {file_path}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete file")


@router.get(
    "/list",
    response_model=StorageListResponse,
    summary="List files in GCS bucket",
    description="List all images in the configured Google Cloud Storage bucket and folder.",
)
async def list_files(client: storage.Client = Depends(get_storage_client)):
    """
    List files in the configured GCS bucket.
    """
    try:
        if not settings.google_cloud_project:
            # Try to infer or fallback
            pass

        bucket = client.bucket(settings.gcs_bucket_name)

        # List blobs with prefix (convert iterator to list in thread to avoid blocking)
        blobs = await asyncio.to_thread(
            list, bucket.list_blobs(prefix=settings.gcs_media_folder)
        )

        items = []
        for blob in blobs:
            if blob.content_type and (
                blob.content_type.startswith("image/")
                or blob.content_type.startswith("video/")
            ):
                # Use proxy URL to avoid public access or signed URL issues with ADC
                # We encode the blob name to ensure it passes safely in the URL
                # but actually, if we just use the name, it should be fine if we match correctly.
                # blob.name includes the folder prefix e.g. "dev/image.jpg"

                proxy_url = f"/api/v1/storage/file/{blob.name}"

                items.append(
                    StorageItem(
                        name=blob.name.split("/")[-1],  # Just the filename
                        uri=f"gs://{settings.gcs_bucket_name}/{blob.name}",
                        url=proxy_url,
                        content_type=blob.content_type,
                        created=blob.time_created or datetime.datetime.now(),
                    )
                )

        # Sort by creation time, newest first
        items.sort(key=lambda x: x.created, reverse=True)

        return StorageListResponse(items=items)

    except Exception as e:
        logger.error(f"Error listing storage files: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list storage files: {str(e)}",
        )


@router.get(
    "/file/{file_path:path}",
    summary="Get GCS file content",
    description="Stream file content from GCS (proxy), supports Range requests for video.",
)
async def get_file(
    file_path: str = Path(..., description="Full path to the file in GCS bucket"),
    range_header: str = Header(None, alias="Range"),
    client: storage.Client = Depends(get_storage_client),
):
    """
    Stream file content from GCS with Range support.
    """
    try:
        bucket = client.bucket(settings.gcs_bucket_name)
        blob = bucket.blob(file_path)

        exists = await asyncio.to_thread(blob.exists)
        if not exists:
            raise HTTPException(status_code=404, detail="File not found")

        await asyncio.to_thread(blob.reload)  # Ensure metadata (size) is loaded
        file_size = blob.size

        if range_header:
            try:
                # Parse Range header (e.g., "bytes=0-1023")
                start_str, end_str = range_header.replace("bytes=", "").split("-")
                start = int(start_str)
                end = int(end_str) if end_str else file_size - 1
            except ValueError:
                # Fallback to full content if parsing fails
                start = 0
                end = file_size - 1

            if start >= file_size:
                raise HTTPException(status_code=416, detail="Range not satisfiable")

            # Ensure end is within bounds
            end = min(end, file_size - 1)
            chunk_size = end - start + 1

            async def iterfile():
                f = await asyncio.to_thread(blob.open, "rb")
                try:
                    await asyncio.to_thread(f.seek, start)
                    remaining = chunk_size
                    while remaining > 0:
                        read_size = min(64 * 1024, remaining)  # 64KB chunks
                        data = await asyncio.to_thread(f.read, read_size)
                        if not data:
                            break
                        yield data
                        remaining -= len(data)
                finally:
                    await asyncio.to_thread(f.close)

            headers = {
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(chunk_size),
                "Content-Type": blob.content_type or "application/octet-stream",
            }

            return StreamingResponse(
                iterfile(),
                status_code=206,
                headers=headers,
                media_type=blob.content_type or "application/octet-stream",
            )

        # Non-range request (200 OK)
        async def full_iterfile():
            f = await asyncio.to_thread(blob.open, "rb")
            try:
                while True:
                    data = await asyncio.to_thread(f.read, 64 * 1024)
                    if not data:
                        break
                    yield data
            finally:
                await asyncio.to_thread(f.close)

        return StreamingResponse(
            full_iterfile(),
            media_type=blob.content_type or "application/octet-stream",
            headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving file {file_path}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve file")
