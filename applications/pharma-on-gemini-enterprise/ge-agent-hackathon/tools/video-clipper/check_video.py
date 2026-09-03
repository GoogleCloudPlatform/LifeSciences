#!/usr/bin/env python3
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

"""Have Gemini watch a rendered video and report whether it's actually OK.

ffmpeg exits 0 on output that is visibly broken — a frozen frame, static, two
narration lines on top of each other. This is the check that catches that.

`narrate_video.py --retime` already runs this pass on its own output. Use this
script standalone: on a render you produced some other way, or to re-check one
after editing.

    python check_video.py staging/final.mp4
    python check_video.py staging/final.mp4 --project my-project-id

Exits 0 on PASS, 1 on FAIL, so it can gate a build.
"""

import argparse
import json
import os
import re
import sys

from dotenv import load_dotenv
from google import genai
from google.genai import types

from video_source import default_project, explain_api_error, resolve_video

# Model IDs and auth live in .env — load it before any os.getenv default below.
load_dotenv()

_VISION_MODEL = os.getenv("VIDEO_VISION_MODEL", "gemini-3.6-flash")

PROMPT = """Watch this rendered video carefully and evaluate it.

1. Visual stalls: does the video freeze on a single frame while playback continues?
2. Audio corruption: any digital static, harsh noise, or audio-visual desync?
3. Audio overlaps: do narration segments overlap — two sentences at once, or a
   line starting before the previous finishes? This is a CRITICAL failure.
4. Pacing: long confusing silences, or narration that doesn't match what is on
   screen at that moment?

Return JSON:
{
  "status": "PASS" | "FAIL",
  "issues": ["..."],
  "details": "what you observed"
}"""


def evaluate_output(client, video_path, model=_VISION_MODEL, staging_bucket=None):
    """Return the evaluation dict for `video_path`."""
    video_part, cleanup = resolve_video(
        client, video_path, staging_bucket=staging_bucket
    )
    try:
        response = client.models.generate_content(
            model=model,
            contents=[video_part, types.Part.from_text(text=PROMPT)],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
    finally:
        cleanup()

    content = re.sub(r"```json|```", "", response.text).strip()
    return json.loads(content)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "video_path", help="Rendered video to check (local path or gs:// URI)"
    )
    parser.add_argument(
        "--model",
        default=_VISION_MODEL,
        help="Vision model (default: VIDEO_VISION_MODEL)",
    )
    parser.add_argument(
        "--api_key",
        default=os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"),
        help="Gemini API key",
    )
    parser.add_argument(
        "--project",
        default=default_project(),
        help="Use Vertex AI in this GCP project (with ADC) instead of an API key",
    )
    parser.add_argument(
        "--gcs-bucket",
        default=os.environ.get("VIDEO_STAGING_BUCKET"),
        help="On Vertex, stage oversized videos into this bucket",
    )
    args = parser.parse_args()

    if not args.api_key and not args.project:
        parser.error("set GOOGLE_API_KEY (or GEMINI_API_KEY), or pass --project.")

    if args.project:
        client = genai.Client(vertexai=True, project=args.project, location="global")
    else:
        client = genai.Client(api_key=args.api_key)

    result = evaluate_output(
        client, args.video_path, model=args.model, staging_bucket=args.gcs_bucket
    )

    print(f"\nStatus: {result.get('status')}")
    for issue in result.get("issues") or []:
        print(f"  - {issue}")
    if result.get("details"):
        print(f"\n{result['details']}")

    sys.exit(0 if result.get("status") == "PASS" else 1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        hint = explain_api_error(e, default_project())
        if hint is None:
            raise
        print(f"\nError: {hint}", file=sys.stderr)
        sys.exit(2)
