"""
Video and image analysis endpoints.

Handles requests for analyzing YouTube videos and images for medical accuracy.
"""

import base64
import json
import logging
from typing import Optional
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from api.models.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    AnalyzeInitialResponse,
    LocationRequest,
    LocationResponse,
    IssueWithoutLocation,
)
from api.services import VideoAnalyzer
from api.services.gemini_client import GeminiClient

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1",
    tags=["analysis"],
)

# In-memory storage for uploaded images (in production, use cloud storage)
uploaded_images = {}


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
async def analyze_video(request: AnalyzeRequest) -> AnalyzeResponse:
    """
    Analyze a YouTube video or image for medical accuracy.

    This endpoint accepts a YouTube video URL or image URL and uses Google's Gemini AI
    to analyze the content for medical accuracy, identifying potential issues,
    inaccuracies, or areas of concern with specific timestamps (for videos) or
    location descriptions (for images).

    Args:
        request: Request containing either video_url or image_url, and optional frame rate

    Returns:
        Analysis results with identified issues and timestamps/locations

    Raises:
        HTTPException: If analysis fails or URLs are invalid
    """
    try:
        # Determine what we're analyzing
        if request.video_url:
            logger.info(f"Received video analysis request for: {request.video_url} (frame_rate: {request.frame_rate} fps)")
        elif request.image_url:
            logger.info(f"Received image analysis request for: {request.image_url}")
        else:
            raise HTTPException(
                status_code=400,
                detail="Either video_url or image_url must be provided"
            )

        # Determine model based on speed
        model_name = "gemini-flash-latest"
        if request.speed == "powerful":
            model_name = "gemini-3-pro-preview"
            logger.info(f"Using powerful model: {model_name}")

        # Create analyzer and perform analysis
        analyzer = VideoAnalyzer(model_name=model_name)
        result = await analyzer.analyze(
            video_url=str(request.video_url) if request.video_url else None,
            image_url=str(request.image_url) if request.image_url else None,
            frame_rate=request.frame_rate
        )

        logger.info(f"Analysis complete")
        return result

    except ValueError as e:
        # Handle validation errors
        logger.error(f"Validation error: {str(e)}")
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error analyzing content: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to analyze content: {str(e)}",
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
    frame_rate: Optional[float] = Form(default=1.0, description="Frame rate (unused for images)"),
    speed: str = Form(default="fast", description="Analysis speed (fast/powerful)")
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

    Returns:
        Analysis results with identified issues

    Raises:
        HTTPException: If analysis fails or file type is invalid
    """
    try:
        logger.info(f"Received upload analysis request for file: {file.filename}")

        # Validate file type
        if not file.content_type or not file.content_type.startswith('image/'):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type: {file.content_type}. Only image files are supported."
            )

        # Read file content
        file_content = await file.read()

        # Determine model based on speed
        model_name = "gemini-flash-latest"
        if speed == "powerful":
            model_name = "gemini-3-pro-preview"
            logger.info(f"Using powerful model: {model_name}")

        # Create analyzer and perform analysis with raw image data
        analyzer = VideoAnalyzer(model_name=model_name)
        result = await analyzer.analyze(
            video_url=None,
            image_url=None,
            image_data=file_content,
            frame_rate=frame_rate
        )

        logger.info(f"Upload analysis complete for: {file.filename}")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error analyzing uploaded file: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to analyze uploaded file: {str(e)}",
        )


@router.post(
    "/analyze/initial",
    response_model=AnalyzeInitialResponse,
    summary="Analyze Image - Get Issues Without Locations",
    description="First step: Analyze an image and get all issues without location coordinates. Use /analyze/location to get coordinates for each issue.",
    responses={
        200: {"description": "Successful analysis with issues but no locations"},
        400: {"description": "Invalid request or unsupported file type"},
        500: {"description": "Internal server error"},
    },
)
async def analyze_initial(
    file: UploadFile = File(..., description="Image file to analyze"),
    speed: str = Form(default="fast", description="Analysis speed (fast/powerful)")
) -> AnalyzeInitialResponse:
    """
    Analyze an uploaded image for medical accuracy without location coordinates.
    This is step 1 of a two-step process.

    Args:
        file: Uploaded image file
        speed: Analysis speed/model selection

    Returns:
        Analysis results with issues but without location coordinates

    Raises:
        HTTPException: If analysis fails or file type is invalid
    """
    try:
        logger.info(f"Received initial analysis request for file: {file.filename}")

        # Validate file type
        if not file.content_type or not file.content_type.startswith('image/'):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type: {file.content_type}. Only image files are supported."
            )

        # Read file content
        file_content = await file.read()

        # Store uploaded image with unique ID for later location requests
        image_id = str(uuid.uuid4())
        uploaded_images[image_id] = file_content
        logger.info(f"Stored image with ID: {image_id}")

        # Determine model based on speed
        model_name = "gemini-flash-latest"
        if speed == "powerful":
            model_name = "gemini-3-pro-preview"
            logger.info(f"Using powerful model: {model_name}")

        # Create Gemini client and analyze without locations
        gemini_client = GeminiClient(model_name=model_name)
        raw_analysis = gemini_client.analyze_image_without_location(image_data=file_content)

        # Parse issues without locations
        from datetime import datetime
        from api.services.video_analyzer import VideoAnalyzer

        analyzer = VideoAnalyzer(gemini_client=gemini_client)
        issues_with_loc = analyzer._parse_issues(raw_analysis)

        # Convert to IssueWithoutLocation format
        issues = []
        for idx, issue in enumerate(issues_with_loc):
            issue_id = f"{image_id}_{idx}"
            issues.append(IssueWithoutLocation(
                issue_id=issue_id,
                start_timestamp=issue.start_timestamp,
                end_timestamp=issue.end_timestamp,
                severity=issue.severity,
                category=issue.category,
                description=issue.description,
                context=issue.context,
            ))

        # Generate summary
        summary = analyzer._generate_summary(issues_with_loc, raw_analysis)

        response = AnalyzeInitialResponse(
            video_id=image_id,
            video_url=f"uploaded_image_{image_id}",
            analysis_timestamp=datetime.utcnow(),
            issues=issues,
            summary=summary,
            total_issues=len(issues),
        )

        logger.info(f"Initial analysis complete. Found {len(issues)} issues (no locations yet)")
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in initial analysis: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to analyze file: {str(e)}",
        )


@router.post(
    "/analyze/location",
    response_model=LocationResponse,
    summary="Get Location for Specific Issue",
    description="Second step: Get location coordinates for a specific issue identified in the initial analysis.",
    responses={
        200: {"description": "Successfully found issue location"},
        400: {"description": "Invalid request"},
        404: {"description": "Image not found"},
        500: {"description": "Internal server error"},
    },
)
async def get_issue_location(request: LocationRequest) -> LocationResponse:
    """
    Find the location coordinates for a specific issue in an image.
    This is step 2 of a two-step process.

    Args:
        request: Request containing issue ID and description

    Returns:
        Location coordinates for the issue

    Raises:
        HTTPException: If location finding fails
    """
    try:
        logger.info(f"Finding location for issue: {request.issue_id}")

        # Extract image_id from issue_id (format: image_id_index)
        image_id = "_".join(request.issue_id.split("_")[:-1])

        # Retrieve image data
        image_data = uploaded_images.get(image_id)

        if not image_data:
            # Try using image_url if provided
            if not request.image_url:
                raise HTTPException(
                    status_code=404,
                    detail=f"Image not found. Image ID: {image_id}"
                )
            image_url = str(request.image_url)
            image_data = None
        else:
            image_url = None

        # Create Gemini client and find location
        gemini_client = GeminiClient()
        raw_location = gemini_client.find_issue_location(
            image_url=image_url,
            image_data=image_data,
            issue_description=request.issue_description,
            issue_context=""
        )

        # Parse location JSON from response
        # Clean up the response to extract JSON
        raw_location = raw_location.strip()
        # Find JSON object in response
        import re
        json_match = re.search(r'\{[^}]+\}', raw_location)
        if not json_match:
            raise ValueError(f"No JSON found in response: {raw_location}")

        location_data = json.loads(json_match.group())

        if "x" not in location_data or "y" not in location_data:
            raise ValueError(f"Invalid location data: {location_data}")

        from api.models.schemas import Location
        location = Location(
            x=float(location_data["x"]),
            y=float(location_data["y"])
        )

        response = LocationResponse(
            issue_id=request.issue_id,
            location=location
        )

        logger.info(f"Successfully found location for issue {request.issue_id}: ({location.x}, {location.y})")
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error finding issue location: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to find issue location: {str(e)}",
        )
