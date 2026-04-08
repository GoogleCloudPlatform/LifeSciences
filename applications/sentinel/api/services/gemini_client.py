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
Google Gemini AI client for video and image analysis.

This module provides a client interface to Google's Gemini API for analyzing
YouTube videos, images, and extracting medical literature review insights.
"""

import asyncio
import logging
import tempfile
import uuid
from typing import Optional

from google import genai
from google.cloud import storage
from google.genai import types

from api.config import settings
from api.services.prompts import (
    FIND_ISSUE_LOCATION_PROMPT,
    IMAGE_ANALYSIS_SINGLE_STEP_PROMPT,
    IMAGE_ANALYSIS_WITHOUT_LOCATION_PROMPT,
    VIDEO_ANALYSIS_PROMPT,
)

logger = logging.getLogger(__name__)


class GeminiClient:
    """
    Client for interacting with Google's Gemini API.

    This class handles all interactions with the Gemini API, including
    video and image analysis and content generation for medical literature review.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        project: Optional[str] = None,
        location: Optional[str] = None,
        storage_client: Optional[storage.Client] = None,
    ):
        """
        Initialize the Gemini client.

        Args:
            api_key: Google Gemini API key. If not provided, uses settings.
            project: Google Cloud Project ID. If not provided, uses settings.
            location: Google Cloud Location. If not provided, uses settings.
            storage_client: Optional Google Cloud Storage client. If provided, reuses this client.
        """
        self.api_key = api_key or settings.gemini_api_key
        self.project = project or settings.google_cloud_project
        self.location = location or settings.google_cloud_location

        if settings.google_genai_use_vertexai:
            logger.info(
                f"Initializing Gemini Client with Vertex AI (Project: {self.project}, Location: {self.location})"
            )
            self.client = genai.Client(
                vertexai=True, project=self.project, location=self.location
            ).aio
            self.storage_client = storage_client or storage.Client(project=self.project)
        else:
            logger.info("Initializing Gemini Client with API Key (AI Studio)")
            self.client = genai.Client(api_key=self.api_key).aio
            self.storage_client = None

    async def close(self):
        """
        Close the Gemini client and release resources.
        """
        if hasattr(self, "client"):
            logger.info("Closing Gemini API client session")
            await self.client.aclose()

    async def analyze_video(
        self,
        video_url: str,
        frame_rate: float = 1.0,
        mime_type: str = "video/mp4",
        model_name: Optional[str] = None,
        response_schema: Optional[type] = None,
    ) -> str:
        """
        Analyze a video (YouTube or GCS) for medical accuracy and potential issues.

        This method sends a video URL to Gemini with a specialized prompt
        for medical literature review, identifying potential issues, inaccuracies,
        or areas of concern with timestamps.

        Args:
            video_url: YouTube video URL or GCS URI (gs://...)
            frame_rate: Frame rate for video sampling in frames per second (default: 1.0)
            mime_type: MIME type of the video (default: "video/mp4")
            model_name: Optional model name override. If not provided, uses settings.gemini_model_fast.

        Returns:
            Raw analysis text from Gemini API

        Raises:
            Exception: If the API request fails
        """
        model = model_name or settings.gemini_model_fast
        logger.info(
            f"Analyzing video: {video_url} with model: {model} at {frame_rate} fps"
        )

        try:
            # Construct the content with video and prompt
            # Note: Frame rate control is handled by the model itself
            # Lower frame rates reduce token usage automatically
            contents = types.Content(
                role="user",
                parts=[
                    types.Part(
                        file_data=types.FileData(
                            file_uri=video_url, mime_type=mime_type
                        )
                    ),
                    types.Part(text=VIDEO_ANALYSIS_PROMPT),
                ],
            )

            # Configure generation with custom settings
            config = types.GenerateContentConfig(
                temperature=1.0,  # Lower temperature for more focused medical analysis
                response_mime_type="application/json" if response_schema else None,
                response_schema=response_schema,
            )

            # Generate content using Gemini API (Async)
            model = model_name or settings.gemini_model_fast
            response = await self.client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )

            # Extract text from response
            analysis_text = response.text

            logger.info(f"Successfully analyzed video: {video_url}")
            return analysis_text

        except Exception as e:
            logger.error(f"Error analyzing video {video_url}: {str(e)}")
            raise

    async def analyze_image_without_location(
        self,
        image_url: str = None,
        image_data: bytes = None,
        model_name: Optional[str] = None,
        response_schema: Optional[type] = None,
    ) -> str:
        """
        Analyze an image for medical accuracy without providing location coordinates.
        This is the first step in a two-step process.

        Args:
            image_url: HTTPS URL to a publicly accessible image (optional if image_data provided)
            image_data: Raw image bytes (optional if image_url provided)
            model_name: Optional model name override.

        Returns:
            Raw analysis text from Gemini API without location data

        Raises:
            Exception: If the API request fails
        """
        logger.info("Analyzing image without locations (step 1)...")

        return await self._analyze_image_with_prompt(
            image_url,
            image_data,
            IMAGE_ANALYSIS_WITHOUT_LOCATION_PROMPT,
            model_name=model_name,
            response_schema=response_schema,
        )

    async def find_issue_location(
        self,
        image_url: str = None,
        image_data: bytes = None,
        issue_description: str = "",
        issue_context: str = "",
        model_name: Optional[str] = None,
        response_schema: Optional[type] = None,
    ) -> str:
        """
        Find the location of a specific issue in an image.
        This is the second step in a two-step process.

        Args:
            image_url: HTTPS URL to a publicly accessible image (optional if image_data provided)
            image_data: Raw image bytes (optional if image_url provided)
            issue_description: Description of the issue to locate
            issue_context: Context about where the issue appears
            model_name: Optional model name override.

        Returns:
            Raw text containing location coordinates in JSON format

        Raises:
            Exception: If the API request fails
        """
        model = model_name or settings.gemini_model_fast
        logger.info(f"Finding location for issue with model: {model}...")

        prompt = FIND_ISSUE_LOCATION_PROMPT.format(
            issue_description=issue_description, issue_context=issue_context
        )

        return await self._analyze_image_with_prompt(
            image_url,
            image_data,
            prompt,
            model_name=model_name,
            response_schema=response_schema,
        )

    async def analyze_image(
        self,
        image_url: str = None,
        image_data: bytes = None,
        model_name: Optional[str] = None,
        response_schema: Optional[type] = None,
    ) -> str:
        """
        Analyze an image for medical accuracy and potential issues (with locations).
        This is the legacy single-step method that includes location coordinates.

        Args:
            image_url: HTTPS URL to a publicly accessible image (optional if image_data provided)
            image_data: Raw image bytes (optional if image_url provided)
            model_name: Optional model name override.

        Returns:
            Raw analysis text from Gemini API with location data

        Raises:
            Exception: If the API request fails
        """
        logger.info("Analyzing image with locations (single-step)...")

        return await self._analyze_image_with_prompt(
            image_url,
            image_data,
            IMAGE_ANALYSIS_SINGLE_STEP_PROMPT,
            model_name=model_name,
            response_schema=response_schema,
        )

    async def _upload_to_gcs(
        self, data: bytes, content_type: str = "image/jpeg"
    ) -> str:
        """
        Upload data to Google Cloud Storage and return the gs:// URI.
        """
        bucket_name = settings.gcs_bucket_name
        folder = settings.gcs_media_folder
        filename = f"{folder}/{uuid.uuid4()}.jpg"

        bucket = self.storage_client.bucket(bucket_name)
        blob = bucket.blob(filename)
        await asyncio.to_thread(
            blob.upload_from_string, data, content_type=content_type
        )

        uri = f"gs://{bucket_name}/{filename}"
        logger.info(f"Uploaded file to GCS: {uri}")
        return uri

    async def _analyze_image_with_prompt(
        self,
        image_url: str = None,
        image_data: bytes = None,
        prompt: str = "",
        model_name: Optional[str] = None,
        response_schema: Optional[type] = None,
    ) -> str:
        """
        Helper method to analyze an image with a custom prompt.

        Args:
            image_url: HTTPS URL to a publicly accessible image (optional if image_data provided)
            image_data: Raw image bytes (optional if image_url provided)
            prompt: The prompt to use for analysis
            model_name: Optional model name override.
            response_schema: Optional response schema for structured output.

        Returns:
            Raw analysis text from Gemini API

        Raises:
            Exception: If the API request fails
        """
        try:
            mime_type = "image/jpeg"

            # Handle Vertex AI with GCS
            if settings.google_genai_use_vertexai:
                if image_data:
                    file_uri = await self._upload_to_gcs(image_data, mime_type)
                elif image_url:
                    file_uri = image_url
                else:
                    raise ValueError("Either image_url or image_data must be provided")

                contents = types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            file_data=types.FileData(
                                file_uri=file_uri, mime_type=mime_type
                            )
                        ),
                        types.Part(text=prompt),
                    ],
                )
            # Handle AI Studio (Gemini API)
            else:
                if image_data:
                    logger.info("Uploading image to Gemini Files API")
                    # Save to temporary file
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=".jpg"
                    ) as tmp_file:
                        tmp_file.write(image_data)
                        tmp_path = tmp_file.name

                    try:
                        # Upload the file (corrected call for AI Studio)
                        uploaded_file = await self.client.files.upload(file=tmp_path)
                        logger.info(f"File uploaded: {uploaded_file.name}")

                        # Construct content with uploaded file
                        contents = types.Content(
                            role="user",
                            parts=[
                                types.Part(
                                    file_data=types.FileData(
                                        file_uri=uploaded_file.uri, mime_type=mime_type
                                    )
                                ),
                                types.Part(text=prompt),
                            ],
                        )
                    finally:
                        # Clean up temporary file
                        import os

                        if os.path.exists(tmp_path):
                            os.unlink(tmp_path)
                elif image_url:
                    # Use URL directly
                    contents = types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                file_data=types.FileData(
                                    file_uri=image_url, mime_type=mime_type
                                )
                            ),
                            types.Part(text=prompt),
                        ],
                    )
                else:
                    raise ValueError("Either image_url or image_data must be provided")

            # Configure generation with custom settings
            config = types.GenerateContentConfig(
                temperature=1.0,  # Lower temperature for more focused medical analysis
                response_mime_type="application/json" if response_schema else None,
                response_schema=response_schema,
            )

            # Generate content using Gemini API (Async)
            model = model_name or settings.gemini_model_fast
            response = await self.client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )

            # Extract text from response
            analysis_text = response.text

            logger.info("Successfully analyzed image")
            return analysis_text

        except Exception as e:
            logger.error(f"Error analyzing image: {str(e)}")
            raise

    def extract_video_id(self, video_url: str) -> str:
        """
        Extract video ID from URL (YouTube ID or GCS filename).

        Args:
            video_url: YouTube video URL or GCS URI

        Returns:
            Video ID or filename
        """
        url_str = str(video_url)

        # Handle GCS URIs
        if url_str.startswith("gs://"):
            return url_str.split("/")[-1]

        # Handle youtube.com URLs
        if "youtube.com/watch?v=" in url_str:
            return url_str.split("watch?v=")[1].split("&")[0]

        # Handle youtu.be URLs
        if "youtu.be/" in url_str:
            return url_str.split("youtu.be/")[1].split("?")[0]

        # If no pattern matches, return the URL as-is and let Gemini handle it
        logger.warning(f"Could not extract video ID from URL: {url_str}")
        return url_str
