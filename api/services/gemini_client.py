"""
Google Gemini AI client for video and image analysis.

This module provides a client interface to Google's Gemini API for analyzing
YouTube videos, images, and extracting medical literature review insights.
"""

import io
import logging
import tempfile
from typing import Optional

from google import genai
from google.genai import types
from PIL import Image

from api.config import settings

logger = logging.getLogger(__name__)


class GeminiClient:
    """
    Client for interacting with Google's Gemini API.

    This class handles all interactions with the Gemini API, including
    video and image analysis and content generation for medical literature review.
    """

    def __init__(self, api_key: Optional[str] = None, model_name: str = "gemini-flash-latest"):
        """
        Initialize the Gemini client.

        Args:
            api_key: Google Gemini API key. If not provided, uses settings.
            model_name: Name of the Gemini model to use.
        """
        self.api_key = api_key or settings.gemini_api_key
        self.client = genai.Client(api_key=self.api_key)
        self.model = model_name  # Model selection based on user preference

    def analyze_video(self, video_url: str, frame_rate: float = 1.0) -> str:
        """
        Analyze a YouTube video for medical accuracy and potential issues.

        This method sends a YouTube video URL to Gemini with a specialized prompt
        for medical literature review, identifying potential issues, inaccuracies,
        or areas of concern with timestamps.

        Args:
            video_url: YouTube video URL to analyze
            frame_rate: Frame rate for video sampling in frames per second (default: 1.0)

        Returns:
            Raw analysis text from Gemini API

        Raises:
            Exception: If the API request fails
        """
        logger.info(f"Analyzing video: {video_url} with frame_rate: {frame_rate} fps")

        # Specialized prompt for medical literature review with strict formatting
        prompt = """You are a comprehensive video review expert analyzing this medical/health content for ANY potential concerns.

Please identify and list ALL potential issues including:

MEDICAL & CONTENT CONCERNS:
1. Medical inaccuracies or misleading claims - including if its making claims for things the treatment is not proven to do concentrate on what it is approved for.
2. Statements lacking proper citations or sources
3. Outdated medical information
4. Unverified or anecdotal claims presented as fact
5. Potential contraindications or safety concerns
6. Dosage or treatment recommendations that may be concerning

PRESENTATION & QUALITY CONCERNS:
7. Poor presentation style or unprofessional delivery
8. Problematic wording, phrasing, or terminology
9. Visual quality issues (poor lighting, unclear graphics, confusing charts)
10. Audio quality problems (unclear speech, background noise, volume issues)
11. Accessibility concerns (no captions, poor contrast, fast speech)
12. Unprofessional conduct or inappropriate behavior

IMPORTANT: Be VERY thorough and critical. Flag ANYTHING that could be improved, questioned, or raises ANY concern - no matter how minor. This includes style, tone, word choice, presentation quality, visual clarity, audio quality, professionalism, etc.

Format each issue EXACTLY as shown below (one issue per block):

ISSUE:
Start: [MM:SS]
End: [MM:SS]
Severity: [low/medium/high/critical]
Category: [medical_accuracy/citation_missing/misleading_claim/outdated_information/unverified_statement/contraindication/dosage_concern/presentation_style/wording_concern/visual_quality/audio_quality/accessibility/professionalism/other]
Description: [Detailed explanation of the issue]
Context: [Quote or context from video]

Example:
ISSUE:
Start: 02:15
End: 02:45
Severity: high
Category: medical_accuracy
Description: The speaker claims that vitamin C cures cancer, which contradicts established medical evidence.
Context: "Vitamin C has been proven to cure all types of cancer"

ISSUE:
Start: 05:20
End: 05:35
Severity: low
Category: wording_concern
Description: The speaker uses casual language ("like", "um", "you know") excessively, which reduces perceived authority and professionalism.
Context: Speaker says "So, like, you know, the thing is, um, antibiotics are, like, really important"

Be extremely thorough and list ALL concerns - even minor style, wording, or quality issues. If absolutely no issues exist, state: "NO ISSUES FOUND"."""

        try:
            # Construct the content with video and prompt
            # Note: Frame rate control is handled by the model itself
            # Lower frame rates reduce token usage automatically
            contents = types.Content(
                role="user",
                parts=[
                    types.Part(
                        file_data=types.FileData(file_uri=video_url)
                    ),
                    types.Part(text=prompt),
                ],
            )

            # Configure generation with custom settings
            config = types.GenerateContentConfig(
                temperature=1.0,  # Lower temperature for more focused medical analysis
            )

            # Generate content using Gemini API
            response = self.client.models.generate_content(
                model=self.model,
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

    def analyze_image_without_location(self, image_url: str = None, image_data: bytes = None) -> str:
        """
        Analyze an image for medical accuracy without providing location coordinates.
        This is the first step in a two-step process.

        Args:
            image_url: HTTPS URL to a publicly accessible image (optional if image_data provided)
            image_data: Raw image bytes (optional if image_url provided)

        Returns:
            Raw analysis text from Gemini API without location data

        Raises:
            Exception: If the API request fails
        """
        logger.info(f"Analyzing image without locations (step 1)...")

        # Specialized prompt for image analysis WITHOUT locations
        prompt = """You are a comprehensive medical image review expert analyzing this medical/health content for ANY potential concerns.

Please identify and list ALL potential issues including:

MEDICAL & CONTENT CONCERNS:
1. Medical inaccuracies or misleading information in diagrams/charts
2. Missing or incorrect labels, annotations, or citations
3. Outdated medical information or deprecated terminology
4. Unverified or questionable data presented as fact
5. Potential safety concerns or contraindications shown
6. Dosage or treatment information that may be concerning

PRESENTATION & QUALITY CONCERNS:
7. Poor visual quality (resolution, clarity, lighting)
8. Confusing or misleading visual design
9. Problematic wording, phrasing, or terminology in text/labels
10. Accessibility concerns (poor contrast, small text, unclear symbols)
11. Unprofessional or inappropriate content
12. Missing context or explanatory information

IMPORTANT: Be VERY thorough and critical. Flag ANYTHING that could be improved, questioned, or raises ANY concern - no matter how minor. This includes accuracy, clarity, design quality, accessibility, professionalism, etc.

Format each issue EXACTLY as shown below (one issue per block):

ISSUE:
Start: N/A
End: N/A
Severity: [low/medium/high/critical]
Category: [medical_accuracy/citation_missing/misleading_claim/outdated_information/unverified_statement/contraindication/dosage_concern/presentation_style/wording_concern/visual_quality/audio_quality/accessibility/professionalism/other]
Description: [Detailed explanation of the issue]
Context: [Description of where in the image the issue appears]

Example:
ISSUE:
Start: N/A
End: N/A
Severity: high
Category: medical_accuracy
Description: The anatomical diagram shows the heart with incorrect chamber labeling - left and right ventricles are reversed.
Context: Main diagram in center of image, ventricle labels

Be extremely thorough and list ALL concerns - even minor design, clarity, or quality issues. If absolutely no issues exist, state: "NO ISSUES FOUND"."""

        return self._analyze_image_with_prompt(image_url, image_data, prompt)

    def find_issue_location(self, image_url: str = None, image_data: bytes = None, issue_description: str = "", issue_context: str = "") -> str:
        """
        Find the location of a specific issue in an image.
        This is the second step in a two-step process.

        Args:
            image_url: HTTPS URL to a publicly accessible image (optional if image_data provided)
            image_data: Raw image bytes (optional if image_url provided)
            issue_description: Description of the issue to locate
            issue_context: Context about where the issue appears

        Returns:
            Raw text containing location coordinates in JSON format

        Raises:
            Exception: If the API request fails
        """
        logger.info(f"Finding location for issue...")

        # Specialized prompt for finding a specific issue's location
        prompt = f"""You are analyzing this medical/health image to locate a specific issue that was previously identified.

ISSUE TO LOCATE:
Description: {issue_description}
Context: {issue_context}

Your task is to identify the location of this specific issue in the image and provide normalized coordinates (x, y) where the issue appears.

Use values between 0.0 and 1.0, where:
- x: 0.0 is the left edge, 1.0 is the right edge
- y: 0.0 is the top edge, 1.0 is the bottom edge

For example, if the issue is in the center of the image, use {{"x": 0.5, "y": 0.5}}.

Respond with ONLY a JSON object in this exact format:
{{"x": 0.5, "y": 0.3}}

Do not include any other text or explanation."""

        return self._analyze_image_with_prompt(image_url, image_data, prompt)

    def analyze_image(self, image_url: str = None, image_data: bytes = None) -> str:
        """
        Analyze an image for medical accuracy and potential issues (with locations).
        This is the legacy single-step method that includes location coordinates.

        Args:
            image_url: HTTPS URL to a publicly accessible image (optional if image_data provided)
            image_data: Raw image bytes (optional if image_url provided)

        Returns:
            Raw analysis text from Gemini API with location data

        Raises:
            Exception: If the API request fails
        """
        logger.info(f"Analyzing image with locations (single-step)...")

        # Specialized prompt for image analysis with strict formatting including bounding boxes
        prompt = """You are a comprehensive medical image review expert analyzing this medical/health content for ANY potential concerns.

Please identify and list ALL potential issues including:

MEDICAL & CONTENT CONCERNS:
1. Medical inaccuracies or misleading information in diagrams/charts
2. Missing or incorrect labels, annotations, or citations
3. Outdated medical information or deprecated terminology
4. Unverified or questionable data presented as fact
5. Potential safety concerns or contraindications shown
6. Dosage or treatment information that may be concerning

PRESENTATION & QUALITY CONCERNS:
7. Poor visual quality (resolution, clarity, lighting)
8. Confusing or misleading visual design
9. Problematic wording, phrasing, or terminology in text/labels
10. Accessibility concerns (poor contrast, small text, unclear symbols)
11. Unprofessional or inappropriate content
12. Missing context or explanatory information

IMPORTANT: Be VERY thorough and critical. Flag ANYTHING that could be improved, questioned, or raises ANY concern - no matter how minor. This includes accuracy, clarity, design quality, accessibility, professionalism, etc.

For each issue, provide the location as normalized coordinates (x, y) where the issue appears in the image. Use values between 0.0 and 1.0, where (0, 0) is top-left and (1, 1) is bottom-right.

Format each issue EXACTLY as shown below (one issue per block):

ISSUE:
Start: N/A
End: N/A
Severity: [low/medium/high/critical]
Category: [medical_accuracy/citation_missing/misleading_claim/outdated_information/unverified_statement/contraindication/dosage_concern/presentation_style/wording_concern/visual_quality/audio_quality/accessibility/professionalism/other]
Description: [Detailed explanation of the issue]
Context: [Description of where in the image the issue appears]
Location: {"x": 0.5, "y": 0.3}

Example:
ISSUE:
Start: N/A
End: N/A
Severity: high
Category: medical_accuracy
Description: The anatomical diagram shows the heart with incorrect chamber labeling - left and right ventricles are reversed.
Context: Main diagram in center of image, ventricle labels
Location: {"x": 0.5, "y": 0.4}

Be extremely thorough and list ALL concerns - even minor design, clarity, or quality issues. If absolutely no issues exist, state: "NO ISSUES FOUND"."""

        return self._analyze_image_with_prompt(image_url, image_data, prompt)

    def _analyze_image_with_prompt(self, image_url: str = None, image_data: bytes = None, prompt: str = "") -> str:
        """
        Helper method to analyze an image with a custom prompt.

        Args:
            image_url: HTTPS URL to a publicly accessible image (optional if image_data provided)
            image_data: Raw image bytes (optional if image_url provided)
            prompt: The prompt to use for analysis

        Returns:
            Raw analysis text from Gemini API

        Raises:
            Exception: If the API request fails
        """
        try:
            # Upload file if we have raw data
            if image_data:
                logger.info("Uploading image to Gemini Files API")
                # Save to temporary file
                with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp_file:
                    tmp_file.write(image_data)
                    tmp_path = tmp_file.name

                try:
                    # Upload the file
                    uploaded_file = self.client.files.upload(path=tmp_path)
                    logger.info(f"File uploaded: {uploaded_file.name}")

                    # Construct content with uploaded file
                    contents = types.Content(
                        role="user",
                        parts=[
                            types.Part(file_data=types.FileData(file_uri=uploaded_file.uri)),
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
                        types.Part(file_data=types.FileData(file_uri=image_url)),
                        types.Part(text=prompt),
                    ],
                )
            else:
                raise ValueError("Either image_url or image_data must be provided")

            # Configure generation with custom settings
            config = types.GenerateContentConfig(
                temperature=1.0,  # Lower temperature for more focused medical analysis
            )

            # Generate content using Gemini API
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )

            # Extract text from response
            analysis_text = response.text

            logger.info(f"Successfully analyzed image")
            return analysis_text

        except Exception as e:
            logger.error(f"Error analyzing image: {str(e)}")
            raise

    def extract_video_id(self, video_url: str) -> str:
        """
        Extract YouTube video ID from URL.

        Args:
            video_url: YouTube video URL

        Returns:
            YouTube video ID

        Examples:
            >>> extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            'dQw4w9WgXcQ'
            >>> extract_video_id("https://youtu.be/dQw4w9WgXcQ")
            'dQw4w9WgXcQ'
        """
        url_str = str(video_url)

        # Handle youtube.com URLs
        if "youtube.com/watch?v=" in url_str:
            return url_str.split("watch?v=")[1].split("&")[0]

        # Handle youtu.be URLs
        if "youtu.be/" in url_str:
            return url_str.split("youtu.be/")[1].split("?")[0]

        # If no pattern matches, return the URL as-is and let Gemini handle it
        logger.warning(f"Could not extract video ID from URL: {url_str}")
        return url_str
