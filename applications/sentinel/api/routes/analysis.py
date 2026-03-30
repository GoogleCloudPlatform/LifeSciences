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
Video and image analysis endpoints.

Handles requests for analyzing YouTube videos and images for medical accuracy.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from api.config import settings
from api.dependencies import get_analyzer_service
from api.models.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
)
from api.services.analyzer_service import AnalyzerService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1",
    tags=["analysis"],
)


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    summary="Analyze Video or Image",
    description="Analyze a YouTube video or image for medical accuracy and potential issues",
    responses={
        200: {
            "description": "Successful analysis",
            "content": {
                "application/json": {
                    "example": {
                        "video_id": "dQw4w9WgXcQ",
                        "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                        "analysis_timestamp": "2025-10-02T12:00:00Z",
                        "issues": [
                            {
                                "start_timestamp": "00:02:15",
                                "end_timestamp": "00:02:45",
                                "severity": "high",
                                "category": "medical_accuracy",
                                "description": "Claim about dosage contradicts established guidelines",
                                "context": "Video segment discussing medication dosing",
                            }
                        ],
                        "summary": "Analysis identified 1 potential issue (1 high) requiring review.",
                        "total_issues": 1,
                    }
                }
            },
        },
        400: {"description": "Invalid request"},
        500: {"description": "Internal server error"},
    },
)
async def analyze(
    request: AnalyzeRequest,
    analyzer: AnalyzerService = Depends(get_analyzer_service),
) -> AnalyzeResponse:
    """
    Analyze a YouTube video or image for medical accuracy.

    This endpoint accepts a YouTube video URL or image URL and uses Google's Gemini AI
    to analyze the content for medical accuracy, identifying potential issues,
    inaccuracies, or areas of concern with specific timestamps (for videos) or
    location descriptions (for images).

    Args:
        request: Request containing either video_url or image_url, and optional frame rate
        analyzer: Injected AnalyzerService instance

    Returns:
        Analysis results with identified issues and timestamps/locations

    Raises:
        HTTPException: If analysis fails or URLs are invalid
    """
    try:
        # Determine what we're analyzing
        if request.video_url:
            logger.info(
                f"Received video analysis request for: {request.video_url} (frame_rate: {request.frame_rate} fps)"
            )
        elif request.image_url:
            logger.info(f"Received image analysis request for: {request.image_url}")
        else:
            raise HTTPException(
                status_code=400, detail="Either video_url or image_url must be provided"
            )

        # Determine model based on speed
        model_name = settings.gemini_model_fast
        if request.speed == "powerful":
            model_name = settings.gemini_model_powerful
            logger.info(f"Using powerful model: {model_name}")

        # Perform analysis
        result = await analyzer.analyze(
            video_url=str(request.video_url) if request.video_url else None,
            image_url=str(request.image_url) if request.image_url else None,
            frame_rate=request.frame_rate,
            model_name=model_name,
        )

        logger.info("Analysis complete")
        return result

    except ValueError as e:
        # Handle validation errors
        logger.error(f"Validation error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error analyzing content: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An internal error occured during analysis.",
        )


@router.post(
    "/analyze/upload",
    response_model=AnalyzeResponse,
    summary="Analyze Uploaded Image",
    description="Analyze an uploaded image file for medical accuracy and potential issues",
    responses={
        200: {"description": "Successful analysis"},
        400: {"description": "Invalid request or unsupported file type"},
        500: {"description": "Internal server error"},
    },
)
async def analyze_upload(
    file: UploadFile = File(..., description="Image file to analyze"),
    frame_rate: Optional[float] = Form(
        default=1.0, description="Frame rate (unused for images)"
    ),
    speed: str = Form(default="fast", description="Analysis speed (fast/powerful)"),
    analyzer: AnalyzerService = Depends(get_analyzer_service),
) -> AnalyzeResponse:
    """
    Analyze an uploaded image file for medical accuracy.

    This endpoint accepts an image file upload and uses Google's Gemini AI
    to analyze the content for medical accuracy, identifying potential issues,
    inaccuracies, or areas of concern.

    Args:
        file: Uploaded image file
        frame_rate: Frame rate parameter (unused for images, kept for compatibility)
        speed: Analysis speed/model selection
        analyzer: Injected AnalyzerService instance

    Returns:
        Analysis results with identified issues

    Raises:
        HTTPException: If analysis fails or file type is invalid
    """
    try:
        logger.info(f"Received upload analysis request for file: {file.filename}")

        # Validate file type
        if not file.content_type or not file.content_type.startswith("image/"):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type: {file.content_type}. Only image files are supported.",
            )

        # Read file content
        file_content = await file.read()

        # Determine model based on speed
        model_name = settings.gemini_model_fast
        if speed == "powerful":
            model_name = settings.gemini_model_powerful
            logger.info(f"Using powerful model: {model_name}")

        # Perform analysis with raw image data
        result = await analyzer.analyze(
            video_url=None,
            image_url=None,
            image_data=file_content,
            frame_rate=frame_rate,
            model_name=model_name,
        )

        logger.info(f"Upload analysis complete for: {file.filename}")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error analyzing uploaded file: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An internal error occured during analysis.",
        )
