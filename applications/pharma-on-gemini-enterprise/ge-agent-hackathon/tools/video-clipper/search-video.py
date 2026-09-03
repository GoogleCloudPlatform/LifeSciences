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

# rick_roll_gemini_search_video.py
import argparse
import json
import logging
import os

from dotenv import load_dotenv
from google import genai
from google.genai.types import GenerateContentConfig, Part

from video_source import default_project  # Added to handle command line arguments

# Load environment variables from .env file
load_dotenv()


def configure_logging():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )


configure_logging()


def generate_youtube_event_timestamps(
    youtube_url: str,
    event_description: str,
    project_id: str,
    location: str,
    model_name: str = "model_name",
) -> list[dict[str, str]]:
    """
    Identifies all timestamps of an event in a YouTube video using Vertex AI
    with controlled generation for structured output.

    Args:
        youtube_url: The YouTube video URL.
        event_description: Description of the event.
        project_id: GCP project ID for Vertex AI.
        location: GCP location for Vertex AI.
        model_name: The model name (must support video).

    Returns:
        List of timestamp objects with 'timestamp' and 'description' fields,
        or empty list if none found.
    """
    # Initialize the Vertex AI client
    client = genai.Client(vertexai=True, project=project_id, location=location)

    # Define schema for controlled generation
    response_schema = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "timestamp": {
                    "type": "STRING",
                    "description": "Timestamp in HH:MM:SS format",
                },
                "description": {
                    "type": "STRING",
                    "description": "Brief description of what happens at this timestamp",
                },
            },
            "required": ["timestamp", "description"],
        },
    }

    prompt = f"""Analyze this YouTube video carefully and identify ALL timestamps where the following event occurs:

EVENT TO FIND: {event_description}

INSTRUCTIONS:
- Watch the entire video and find every instance of this event
- For each occurrence, note the exact timestamp in HH:MM:SS or MM:SS format
- Provide a brief description of what happens at each timestamp
- If the event occurs multiple times, list ALL occurrences
- Be thorough and don't miss any instances
- If you're not certain, include it anyway with a description explaining your reasoning

Return your findings as a JSON array where each item has:
- "timestamp": the time in HH:MM:SS format
- "description": what happens at that moment

If no matching events are found, return an empty array []."""

    try:
        # Create the video part from the YouTube URL
        video_part = Part.from_uri(file_uri=youtube_url, mime_type="video/webm")

        # Configure generation with controlled output
        generation_config = GenerateContentConfig(
            temperature=0.2,  # Slightly higher to allow for more creative interpretation
            max_output_tokens=8192,
            response_mime_type="application/json",
            response_schema=response_schema,
        )

        # Make the API call
        response = client.models.generate_content(
            model=model_name, contents=[prompt, video_part], config=generation_config
        )

        logging.debug(f"Raw response: {response}")

        # Parse response with None check
        response_text = None
        if hasattr(response, "text") and response.text:
            response_text = response.text.strip()
        elif response.candidates and len(response.candidates) > 0:
            candidate = response.candidates[0]
            if (
                candidate.content
                and candidate.content.parts
                and len(candidate.content.parts) > 0
            ):
                text = candidate.content.parts[0].text
                if text:
                    response_text = text.strip()

        if not response_text:
            logging.warning("No text in response")
            return []

        logging.debug(f"Raw response text: {response_text}")

        # Try to parse as JSON
        try:
            result = json.loads(response_text)
            return result
        except json.JSONDecodeError:
            logging.error("Failed to parse JSON response")
            return []

    except Exception as e:
        logging.error(f"API call failed: {e}")
        print(f"An error occurred: {e}")
        return []


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Identify timestamps of an event in a YouTube video."
    )
    parser.add_argument("youtube_url", help="The YouTube video URL.")
    parser.add_argument("event_description", help="Description of the event.")
    parser.add_argument(
        "--project_id",
        default=default_project(),
        help="GCP project ID for Vertex AI (default: from GOOGLE_CLOUD_PROJECT env var).",
    )
    parser.add_argument(
        "--location",
        default=os.getenv("MODEL_LOCATION", os.getenv("VERTEX_AI_LOCATION", "global")),
        help=(
            "GCP location for Vertex AI (default: from MODEL_LOCATION / "
            "VERTEX_AI_LOCATION env var, else 'global'). Gemini 3.x models are "
            "served only from 'global' — a regional value 404s."
        ),
    )
    parser.add_argument(
        "--model_name",
        default=os.getenv("VIDEO_SEARCH_MODEL", "gemini-3.5-flash-lite"),
        help=(
            "The model name (must support video) (default: from "
            "VIDEO_SEARCH_MODEL env var or 'gemini-3.5-flash-lite')."
        ),
    )

    args = parser.parse_args()

    # Validate required arguments
    if not args.project_id:
        parser.error(
            "project_id is required. Set GOOGLE_CLOUD_PROJECT environment variable or use --project_id"
        )

    timestamps = generate_youtube_event_timestamps(
        args.youtube_url,
        args.event_description,
        args.project_id,
        args.location,
        args.model_name,
    )

    print(json.dumps(timestamps, indent=4))
