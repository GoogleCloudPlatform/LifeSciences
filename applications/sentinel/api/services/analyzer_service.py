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
Analysis service for processing Gemini API responses.

This module handles the business logic for analyzing videos, images and parsing
the results from the Gemini API into structured data.
"""

import logging
from datetime import UTC, datetime
from typing import Optional

from api.models.schemas import AnalyzeResponse, Issue, Severity
from api.services.gemini_client import GeminiClient

logger = logging.getLogger(__name__)


class AnalyzerService:
    """
    Service for analyzing various media types (videos, images) and parsing results.

    This class coordinates media analysis by using the GeminiClient
    and parsing the results into structured response models.
    """

    def __init__(
        self,
        gemini_client: GeminiClient,
    ):
        """
        Initialize the analyzer service.

        Args:
            gemini_client: GeminiClient instance.
        """
        self.gemini_client = gemini_client

    async def analyze(
        self,
        video_url: Optional[str] = None,
        image_url: Optional[str] = None,
        image_data: Optional[bytes] = None,
        frame_rate: float = 1.0,
        model_name: Optional[str] = None,
    ) -> AnalyzeResponse:
        """
        Analyze a YouTube video or image and return structured results.

        Args:
            video_url: YouTube video URL to analyze (optional if image_url/image_data provided)
            image_url: Image URL to analyze (optional if video_url/image_data provided)
            image_data: Raw image bytes to analyze (optional if video_url/image_url provided)
            frame_rate: Frame rate for video sampling in frames per second (default: 1.0)
            model_name: Optional model name override.

        Returns:
            Structured analysis response with identified issues

        Raises:
            ValueError: If no input is provided or multiple inputs are provided
            Exception: If analysis fails
        """
        # Validate input
        inputs = sum([bool(video_url), bool(image_url), bool(image_data)])
        if inputs == 0:
            raise ValueError(
                "Either video_url, image_url, or image_data must be provided"
            )
        if inputs > 1:
            raise ValueError(
                "Cannot analyze multiple inputs in the same request. Please provide only one."
            )

        from api.models.schemas import AnalysisResponseSchema

        if video_url:
            logger.info(f"Starting analysis for video: {video_url}")
            # Determine mime type for GCS videos
            mime_type = "video/mp4"
            if str(video_url).startswith("gs://"):
                # Simple extension-based mime type detection
                ext = str(video_url).split(".")[-1].lower()
                if ext in ["mp4", "mpeg", "mov", "avi", "wmv", "mpg"]:
                    mime_type = f"video/{ext}" if ext != "mov" else "video/quicktime"

            raw_analysis = await self.gemini_client.analyze_video(
                video_url,
                frame_rate,
                mime_type=mime_type,
                model_name=model_name,
                response_schema=AnalysisResponseSchema,
            )
            content_id = self.gemini_client.extract_video_id(video_url)
            content_url = str(video_url)
        elif image_url:
            logger.info(f"Starting analysis for image: {image_url}")
            raw_analysis = await self.gemini_client.analyze_image(
                image_url=image_url,
                model_name=model_name,
                response_schema=AnalysisResponseSchema,
            )
            # Extract filename or use full URL as ID for images
            content_id = (
                image_url.split("/")[-1].split("?")[0]
                if "/" in image_url
                else str(image_url)
            )
            content_url = str(image_url)
        else:  # image_data
            logger.info("Starting analysis for uploaded image")
            raw_analysis = await self.gemini_client.analyze_image(
                image_data=image_data,
                model_name=model_name,
                response_schema=AnalysisResponseSchema,
            )
            content_id = "uploaded_image"
            content_url = "uploaded_image"

        # Parse the raw analysis into structured issues
        structured_data = AnalysisResponseSchema.model_validate_json(raw_analysis)
        issues = structured_data.issues
        summary = (
            structured_data.summary
            if structured_data.summary
            else self._generate_summary(issues, raw_analysis)
        )

        # Construct response
        response = AnalyzeResponse(
            video_id=content_id,
            video_url=content_url,
            analysis_timestamp=datetime.now(UTC),
            issues=issues,
            summary=summary,
            total_issues=len(issues),
        )

        logger.info(f"Analysis complete. Found {len(issues)} issues.")
        return response

    def _generate_summary(self, issues: list[Issue], raw_analysis: str) -> str:
        """
        Generate a summary of the analysis results.

        Args:
            issues: List of identified issues
            raw_analysis: Raw analysis text from Gemini

        Returns:
            Summary string
        """
        if not issues:
            return "No significant medical accuracy issues identified in this content."

        # Count by severity
        severity_counts = {}
        for issue in issues:
            severity_counts[issue.severity] = severity_counts.get(issue.severity, 0) + 1

        # Build summary
        summary_parts = [
            f"Analysis identified {len(issues)} potential issue{'s' if len(issues) != 1 else ''}"
        ]

        if severity_counts:
            severity_summary = ", ".join(
                f"{count} {severity.value}"
                for severity, count in sorted(
                    severity_counts.items(),
                    key=lambda x: list(Severity).index(x[0]),
                    reverse=True,
                )
            )
            summary_parts.append(f" ({severity_summary})")

        summary_parts.append(" requiring review.")

        return "".join(summary_parts)
