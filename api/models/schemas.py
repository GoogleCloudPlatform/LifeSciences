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
Pydantic schemas for API requests and responses.

This module defines the data models used for validating and serializing
API requests and responses.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Severity(str, Enum):
    """Severity levels for identified issues."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IssueCategory(str, Enum):
    """Categories for medical literature review issues."""

    MEDICAL_ACCURACY = "medical_accuracy"
    CITATION_MISSING = "citation_missing"
    MISLEADING_CLAIM = "misleading_claim"
    OUTDATED_INFORMATION = "outdated_information"
    UNVERIFIED_STATEMENT = "unverified_statement"
    CONTRAINDICATION = "contraindication"
    DOSAGE_CONCERN = "dosage_concern"
    PRESENTATION_STYLE = "presentation_style"
    WORDING_CONCERN = "wording_concern"
    VISUAL_QUALITY = "visual_quality"
    AUDIO_QUALITY = "audio_quality"
    ACCESSIBILITY = "accessibility"
    PROFESSIONALISM = "professionalism"
    OTHER = "other"


class Location(BaseModel):
    """
    Represents a location in an image using normalized coordinates.

    Attributes:
        x: Normalized x coordinate (0.0 = left, 1.0 = right)
        y: Normalized y coordinate (0.0 = top, 1.0 = bottom)
    """

    x: float = Field(
        ...,
        description="Normalized x coordinate (0.0 to 1.0)",
        ge=0.0,
        le=1.0,
    )
    y: float = Field(
        ...,
        description="Normalized y coordinate (0.0 to 1.0)",
        ge=0.0,
        le=1.0,
    )


class Issue(BaseModel):
    """
    Represents a single issue identified in the video or image.

    Attributes:
        start_timestamp: Start time of the issue in video (format: HH:MM:SS or MM:SS)
        end_timestamp: End time of the issue in video (format: HH:MM:SS or MM:SS)
        severity: Severity level of the issue
        category: Category classification of the issue
        description: Detailed description of the issue
        context: Relevant context from the video segment
        location: Location of the issue in the image (for image analysis only)
    """

    start_timestamp: str = Field(
        ...,
        description="Start timestamp in video (HH:MM:SS or MM:SS format)",
    )
    end_timestamp: str = Field(
        ...,
        description="End timestamp in video (HH:MM:SS or MM:SS format)",
    )
    severity: Severity = Field(
        ...,
        description="Severity level of the identified issue",
    )
    category: IssueCategory = Field(
        ...,
        description="Category of the issue",
    )
    description: str = Field(
        ...,
        description="Detailed description of the issue",
        min_length=10,
    )
    context: Optional[str] = Field(
        None,
        description="Relevant context or quote from the video",
    )
    location: Optional[Location] = Field(
        None,
        description="Location in image (for image analysis only)",
    )


class AnalysisSpeed(str, Enum):
    """Analysis speed/model selection."""

    FAST = "fast"
    POWERFUL = "powerful"


class AnalyzeRequest(BaseModel):
    """
    Request model for video analysis endpoint.

    Attributes:
        video_url: YouTube video URL to analyze (optional if image_url provided)
        image_url: Image URL to analyze (optional if video_url provided)
        frame_rate: Frame rate for video sampling (frames per second)
        speed: Analysis speed/model selection (fast=Flash, powerful=Pro)
    """

    video_url: Optional[str] = Field(
        None,
        description="YouTube video URL or GCS URI (gs://...)",
        examples=[
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "gs://bucket/video.mp4",
        ],
    )
    image_url: Optional[str] = Field(
        None,
        description="Image URL to analyze (HTTPS URL or gs:// URI)",
        examples=["https://example.com/medical-diagram.jpg", "gs://bucket/image.jpg"],
    )
    frame_rate: Optional[float] = Field(
        default=1.0,
        description="Frame rate for video sampling in frames per second (default: 1.0). Lower values reduce token usage. Only applies to video analysis.",
        ge=0.1,
        le=10.0,
        examples=[1.0, 0.5, 0.25],
    )
    speed: AnalysisSpeed = Field(
        default=AnalysisSpeed.FAST,
        description="Analysis speed/model selection. 'fast' uses Gemini Flash, 'powerful' uses Gemini Pro.",
    )


class AnalysisResponseSchema(BaseModel):
    """
    Schema for the LLM to return a list of issues and an optional summary.
    This model is used as a response_schema for Gemini API calls.
    """

    issues: list[Issue] = Field(
        ..., description="List of medical/content issues identified"
    )
    summary: Optional[str] = Field(None, description="A brief summary of the findings")


class AnalyzeResponse(BaseModel):
    """
    Response model for video analysis endpoint.

    Attributes:
        video_id: YouTube video ID
        video_url: Original YouTube video URL
        analysis_timestamp: ISO timestamp when analysis was performed
        issues: List of identified issues with timestamps
        summary: Brief summary of the analysis
        total_issues: Total number of issues found
    """

    video_id: str = Field(
        ...,
        description="YouTube video identifier",
    )
    video_url: str = Field(
        ...,
        description="Original YouTube video URL",
    )
    analysis_timestamp: datetime = Field(
        ...,
        description="ISO timestamp when analysis was completed",
    )
    issues: list[Issue] = Field(
        default_factory=list,
        description="List of identified issues",
    )
    summary: str = Field(
        ...,
        description="Brief summary of the analysis results",
    )
    total_issues: int = Field(
        ...,
        description="Total count of issues identified",
        ge=0,
    )


class HealthResponse(BaseModel):
    """
    Health check response model.

    Attributes:
        status: Service status (healthy/unhealthy)
        version: API version
        timestamp: Current server timestamp
    """

    status: str = Field(
        ...,
        description="Service health status",
        examples=["healthy"],
    )
    version: str = Field(
        ...,
        description="API version",
    )
    timestamp: datetime = Field(
        ...,
        description="Current server timestamp",
    )
