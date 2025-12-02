"""
Video and image analysis service for processing Gemini API responses.

This module handles the business logic for analyzing videos, images and parsing
the results from the Gemini API into structured data.
"""

import json
import logging
import re
from datetime import datetime
from typing import Optional

from api.models.schemas import AnalyzeResponse, Issue, IssueCategory, Location, Severity
from api.services.gemini_client import GeminiClient

logger = logging.getLogger(__name__)


class VideoAnalyzer:
    """
    Service for analyzing videos, images and parsing results.

    This class coordinates video and image analysis by using the GeminiClient
    and parsing the results into structured response models.
    """

    def __init__(self, gemini_client: Optional[GeminiClient] = None, model_name: str = "gemini-flash-latest"):
        """
        Initialize the video analyzer.

        Args:
            gemini_client: GeminiClient instance. If not provided, creates a new one.
            model_name: Name of the Gemini model to use (if creating new client).
        """
        self.gemini_client = gemini_client or GeminiClient(model_name=model_name)

    async def analyze(
        self,
        video_url: Optional[str] = None,
        image_url: Optional[str] = None,
        image_data: Optional[bytes] = None,
        frame_rate: float = 1.0
    ) -> AnalyzeResponse:
        """
        Analyze a YouTube video or image and return structured results.

        Args:
            video_url: YouTube video URL to analyze (optional if image_url/image_data provided)
            image_url: Image URL to analyze (optional if video_url/image_data provided)
            image_data: Raw image bytes to analyze (optional if video_url/image_url provided)
            frame_rate: Frame rate for video sampling in frames per second (default: 1.0)

        Returns:
            Structured analysis response with identified issues

        Raises:
            ValueError: If no input is provided or multiple inputs are provided
            Exception: If analysis fails
        """
        # Validate input
        inputs = sum([bool(video_url), bool(image_url), bool(image_data)])
        if inputs == 0:
            raise ValueError("Either video_url, image_url, or image_data must be provided")
        if inputs > 1:
            raise ValueError("Cannot analyze multiple inputs in the same request. Please provide only one.")

        if video_url:
            logger.info(f"Starting analysis for video: {video_url}")
            raw_analysis = self.gemini_client.analyze_video(video_url, frame_rate)
            content_id = self.gemini_client.extract_video_id(video_url)
            content_url = str(video_url)
        elif image_url:
            logger.info(f"Starting analysis for image: {image_url}")
            raw_analysis = self.gemini_client.analyze_image(image_url=image_url)
            # Extract filename or use full URL as ID for images
            content_id = image_url.split("/")[-1].split("?")[0] if "/" in image_url else str(image_url)
            content_url = str(image_url)
        else:  # image_data
            logger.info(f"Starting analysis for uploaded image")
            raw_analysis = self.gemini_client.analyze_image(image_data=image_data)
            content_id = "uploaded_image"
            content_url = "uploaded_image"

        # Parse the raw analysis into structured issues
        issues = self._parse_issues(raw_analysis)

        # Generate summary
        summary = self._generate_summary(issues, raw_analysis)

        # Construct response
        response = AnalyzeResponse(
            video_id=content_id,
            video_url=content_url,
            analysis_timestamp=datetime.utcnow(),
            issues=issues,
            summary=summary,
            total_issues=len(issues),
        )

        logger.info(f"Analysis complete. Found {len(issues)} issues.")
        return response

    def _parse_issues(self, raw_analysis: str) -> list[Issue]:
        """
        Parse raw Gemini analysis text into structured Issue objects.

        This method attempts to extract issues from the Gemini response by
        looking for patterns that indicate timestamps, severity levels, etc.

        Args:
            raw_analysis: Raw text response from Gemini API

        Returns:
            List of structured Issue objects
        """
        issues = []

        # Check if analysis indicates no issues found
        if "NO ISSUES FOUND" in raw_analysis:
            logger.info("Analysis indicates no issues found")
            return issues

        # Split analysis into issue blocks using "ISSUE:" as delimiter
        issue_blocks = raw_analysis.split("ISSUE:")

        # Timestamp pattern (HH:MM:SS or MM:SS)
        timestamp_pattern = r"(\d{1,2}:\d{2}(?::\d{2})?)"

        for block in issue_blocks:
            if not block.strip() or "Example:" in block:
                continue

            current_issue = {}
            lines = block.strip().split("\n")

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Parse Start timestamp
                if line.lower().startswith("start:"):
                    timestamp_match = re.search(timestamp_pattern, line)
                    if timestamp_match:
                        current_issue["start_timestamp"] = timestamp_match.group(1)
                    # For images, handle "N/A" timestamps
                    elif "n/a" in line.lower():
                        current_issue["start_timestamp"] = "N/A"

                # Parse End timestamp
                elif line.lower().startswith("end:"):
                    timestamp_match = re.search(timestamp_pattern, line)
                    if timestamp_match:
                        current_issue["end_timestamp"] = timestamp_match.group(1)
                    # For images, handle "N/A" timestamps
                    elif "n/a" in line.lower():
                        current_issue["end_timestamp"] = "N/A"

                # Parse Severity
                elif line.lower().startswith("severity:"):
                    severity_text = line.split(":", 1)[1].strip().lower()
                    if "critical" in severity_text:
                        current_issue["severity"] = Severity.CRITICAL
                    elif "high" in severity_text:
                        current_issue["severity"] = Severity.HIGH
                    elif "medium" in severity_text:
                        current_issue["severity"] = Severity.MEDIUM
                    elif "low" in severity_text:
                        current_issue["severity"] = Severity.LOW

                # Parse Category
                elif line.lower().startswith("category:"):
                    category_text = line.split(":", 1)[1].strip().lower().replace(" ", "_")
                    for cat in IssueCategory:
                        if cat.value in category_text:
                            current_issue["category"] = cat
                            break

                # Parse Description
                elif line.lower().startswith("description:"):
                    current_issue["description"] = line.split(":", 1)[1].strip()

                # Parse Context
                elif line.lower().startswith("context:"):
                    current_issue["context"] = line.split(":", 1)[1].strip()

                # Parse Location (for images)
                elif line.lower().startswith("location:"):
                    try:
                        location_text = line.split(":", 1)[1].strip()
                        # Try to parse JSON location
                        location_data = json.loads(location_text)
                        if "x" in location_data and "y" in location_data:
                            current_issue["location"] = Location(
                                x=float(location_data["x"]),
                                y=float(location_data["y"])
                            )
                    except (json.JSONDecodeError, ValueError, KeyError) as e:
                        logger.warning(f"Failed to parse location from line: {line}. Error: {e}")

            # Add the parsed issue if it has required fields
            if current_issue and "start_timestamp" in current_issue:
                issue = self._create_issue_from_dict(current_issue)
                if issue:
                    issues.append(issue)

        logger.info(f"Parsed {len(issues)} issues from Gemini response")
        return issues

    def _create_issue_from_dict(self, issue_dict: dict) -> Optional[Issue]:
        """
        Create an Issue object from a dictionary of parsed fields.

        Args:
            issue_dict: Dictionary containing issue fields

        Returns:
            Issue object if all required fields present, None otherwise
        """
        try:
            # Set defaults for missing fields
            start_timestamp = issue_dict.get("start_timestamp", "00:00")
            end_timestamp = issue_dict.get("end_timestamp")

            # If end_timestamp is missing, set it to start_timestamp (required field)
            if not end_timestamp:
                end_timestamp = start_timestamp

            severity = issue_dict.get("severity", Severity.MEDIUM)
            category = issue_dict.get("category", IssueCategory.OTHER)
            description = issue_dict.get("description", "Issue identified - details in context")
            context = issue_dict.get("context")
            location = issue_dict.get("location")

            # Ensure description is long enough
            if len(description) < 10:
                description = f"Potential issue identified at {start_timestamp}"

            return Issue(
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
                severity=severity,
                category=category,
                description=description,
                context=context,
                location=location,
            )
        except Exception as e:
            logger.warning(f"Failed to create issue from dict: {e}")
            return None

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
            return "No significant medical accuracy issues identified in this video."

        # Count by severity
        severity_counts = {}
        for issue in issues:
            severity_counts[issue.severity] = severity_counts.get(issue.severity, 0) + 1

        # Build summary
        summary_parts = [f"Analysis identified {len(issues)} potential issue{'s' if len(issues) != 1 else ''}"]

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
