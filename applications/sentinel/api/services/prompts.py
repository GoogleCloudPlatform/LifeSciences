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
Prompts for medical literature review analysis using Gemini.
This module loads prompts from markdown files in the prompts/ directory.
"""

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(filename: str) -> str:
    """Helper to load prompt from markdown file."""
    path = PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


# Load prompts from markdown files
VIDEO_ANALYSIS_PROMPT = _load_prompt("video_analysis.md")
IMAGE_ANALYSIS_WITHOUT_LOCATION_PROMPT = _load_prompt(
    "image_analysis_without_location.md"
)
FIND_ISSUE_LOCATION_PROMPT = _load_prompt("find_issue_location.md")
IMAGE_ANALYSIS_SINGLE_STEP_PROMPT = _load_prompt("image_analysis_single_step.md")
