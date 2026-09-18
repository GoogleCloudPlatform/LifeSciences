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
import posixpath
import tempfile
import urllib.parse
import uuid

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
        api_key: str | None = None,
        project: str | None = None,
        location: str | None = None,
        storage_client: storage.Client | None = None,
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

        if settings.google_genai_use_enterprise:
            logger.info(
                f"Initializing Gemini Client with Agent Platform (Project: {self.project}, Location: {self.location})"
            )
            self.client = genai.Client(
                enterprise=True, project=self.project, location=self.location
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

    @staticmethod
    def _append_custom_rules(prompt: str, custom_rules: str | None) -> str:
        """Append a user-supplied rules file to an analyzer prompt.

        Rules are added as a clearly delimited section so the model treats
        them as additional, equally-weighted criteria rather than an aside.
        Returns the prompt unchanged when ``custom_rules`` is None / empty.
        """
        if not custom_rules or not custom_rules.strip():
            return prompt
        return (
            prompt
            + "\n\n## Additional review rules (user-supplied)\n\n"
            + "The following rules were supplied by the submitter and apply "
            + "in addition to the categories above. Treat each rule as a "
            + "first-class compliance check and flag any violation, partial "
            + "follow, or ambiguity as an ISSUE using the same format. Use "
            + "the `other` category when no standard category fits.\n\n"
            + "```\n"
            + custom_rules.strip()
            + "\n```\n"
        )

    @staticmethod
    def _validate_media_uri(uri: str) -> str:
        """
        Validate that a user-supplied media URI is safe and authorized.

        For ``gs://`` URIs, enforces that the URI points strictly to the configured
        ``settings.gcs_bucket_name`` and ``settings.gcs_media_folder`` prefix without
        any path traversal (``..``), null bytes, or backslash characters.
        For external URLs, allows only ``http`` or ``https`` schemes with a valid host.

        Args:
            uri: The media URI to validate.

        Returns:
            The validated URI string.

        Raises:
            ValueError: If the URI is malformed, uses an unauthorized bucket/prefix,
                contains path traversal sequences, or uses an unsupported scheme.
        """
        if not uri or not isinstance(uri, str):
            raise ValueError("Invalid media URI: must be a non-empty string")

        unquoted = urllib.parse.unquote(uri)
        if "\x00" in unquoted or "\\" in unquoted:
            raise ValueError(
                f"Invalid media URI '{uri}': disallowed characters detected"
            )

        if ".." in uri.split("/") or ".." in unquoted.split("/"):
            raise ValueError(
                f"Invalid media URI '{uri}': path traversal ('..') is not allowed"
            )

        parsed = urllib.parse.urlparse(uri)
        if parsed.scheme == "gs":
            bucket_name = settings.gcs_bucket_name
            if not bucket_name:
                raise ValueError("Unauthorized GCS URI: GCS bucket is not configured")
            folder = settings.gcs_media_folder.strip("/")
            expected_prefix = (
                f"gs://{bucket_name}/{folder}/" if folder else f"gs://{bucket_name}/"
            )
            if not uri.startswith(expected_prefix) or not unquoted.startswith(
                expected_prefix
            ):
                raise ValueError(
                    f"Unauthorized GCS URI '{uri}': must start with '{expected_prefix}'"
                )

            object_rel_path = unquoted[len(expected_prefix) :]
            if not object_rel_path or object_rel_path.startswith("/"):
                raise ValueError(
                    f"Unauthorized GCS URI '{uri}': missing object path under '{expected_prefix}'"
                )

            bucket_path = unquoted[len(f"gs://{bucket_name}/") :]
            normalized_bucket_path = posixpath.normpath(bucket_path)
            expected_folder_prefix = f"{folder}/" if folder else ""
            if expected_folder_prefix and not normalized_bucket_path.startswith(
                expected_folder_prefix
            ):
                raise ValueError(
                    f"Unauthorized GCS URI '{uri}': path escapes authorized folder '{folder}'"
                )
            return uri

        if parsed.scheme in ("http", "https"):
            if not parsed.netloc:
                raise ValueError(f"Invalid HTTP(S) URI '{uri}': missing host")
            return uri

        raise ValueError(
            f"Unsupported or unauthorized URI scheme in '{uri}'. "
            "Only authorized gs:// or http(s):// URIs are allowed."
        )

    async def analyze_video(
        self,
        video_url: str,
        frame_rate: float = 1.0,
        mime_type: str = "video/mp4",
        model_name: str | None = None,
        response_schema: type | None = None,
        custom_rules: str | None = None,
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

        validated_video_url = self._validate_media_uri(video_url)

        try:
            # Construct the content with video and prompt
            # Note: Frame rate control is handled by the model itself
            # Lower frame rates reduce token usage automatically
            contents = types.Content(
                role="user",
                parts=[
                    types.Part(
                        file_data=types.FileData(
                            file_uri=validated_video_url, mime_type=mime_type
                        )
                    ),
                    types.Part(
                        text=self._append_custom_rules(
                            VIDEO_ANALYSIS_PROMPT, custom_rules
                        )
                    ),
                ],
            )

            # Configure generation with custom settings
            config = types.GenerateContentConfig(
                temperature=1.0,  # Lower temperature for more focused medical analysis
                response_mime_type="application/json" if response_schema else None,
                response_schema=response_schema,
                max_output_tokens=65535,
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
            logger.error(f"Error analyzing video {video_url}: {e!s}")
            raise

    async def analyze_image_without_location(
        self,
        image_url: str | None = None,
        image_data: bytes | None = None,
        model_name: str | None = None,
        response_schema: type | None = None,
        custom_rules: str | None = None,
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
            custom_rules=custom_rules,
        )

    async def find_issue_location(
        self,
        image_url: str | None = None,
        image_data: bytes | None = None,
        issue_description: str = "",
        issue_context: str = "",
        model_name: str | None = None,
        response_schema: type | None = None,
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
        image_url: str | None = None,
        image_data: bytes | None = None,
        model_name: str | None = None,
        response_schema: type | None = None,
        custom_rules: str | None = None,
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
            custom_rules=custom_rules,
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
        image_url: str | None = None,
        image_data: bytes | None = None,
        prompt: str = "",
        model_name: str | None = None,
        response_schema: type | None = None,
        custom_rules: str | None = None,
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
            prompt = self._append_custom_rules(prompt, custom_rules)

            # Handle Agent Platform with GCS
            if settings.google_genai_use_enterprise:
                if image_data:
                    file_uri = await self._upload_to_gcs(image_data, mime_type)
                elif image_url:
                    file_uri = self._validate_media_uri(image_url)
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
                    validated_image_url = self._validate_media_uri(image_url)
                    # Use URL directly
                    contents = types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                file_data=types.FileData(
                                    file_uri=validated_image_url, mime_type=mime_type
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
                max_output_tokens=65535,
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
            logger.error(f"Error analyzing image: {e!s}")
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
