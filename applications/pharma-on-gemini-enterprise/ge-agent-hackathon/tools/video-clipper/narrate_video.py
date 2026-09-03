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
Overlay TTS narration on a trimmed demo video using Gemini TTS voices.
Includes a self-evaluation step using Gemini 3.1 Pro.
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
import wave

from dotenv import load_dotenv
from google import genai
from google.genai import types

from video_source import default_project, explain_api_error, resolve_video

# Model IDs and auth live in .env — load it before any os.getenv default below.
load_dotenv()

_TTS_MODEL = os.getenv("VIDEO_TTS_MODEL", "gemini-3.1-flash-tts-preview")
_VISION_MODEL = os.getenv("VIDEO_VISION_MODEL", "gemini-3.6-flash")
_TTS_SAMPLE_RATE = 24000
_TTS_CHANNELS = 1
_TTS_SAMPLE_WIDTH = 2  # 16-bit PCM


def parse_talk_track(talk_track_path, narrate_until=None):
    """Parse the TIMED SECTION BREAKDOWN from the talk track file."""
    with open(talk_track_path) as f:
        content = f.read()

    breakdown_match = re.search(
        r"TIMED SECTION BREAKDOWN\s*\n-+\n(.*?)(?:\n-{10,}|\Z)",
        content,
        re.DOTALL,
    )
    if not breakdown_match:
        raise ValueError("Could not find TIMED SECTION BREAKDOWN in talk track file")

    breakdown = breakdown_match.group(1).strip()
    sections = []
    parts = re.split(r"(?:^|\n)\[(\d+:\d+)\]\s*\n?", breakdown)

    i = 1
    while i < len(parts) - 1:
        timestamp = parts[i]
        narration = parts[i + 1].strip()
        ts_parts = timestamp.split(":")
        start_secs = int(ts_parts[0]) * 60 + int(ts_parts[1])
        sections.append(
            {
                "timestamp": timestamp,
                "start_secs": start_secs,
                "narration": narration,
            }
        )
        i += 2

    if narrate_until is not None:
        sections = [s for s in sections if s["start_secs"] < narrate_until]

    return sections


def create_client(api_key=None, project=None, api_version=None):
    """Create a genai client."""
    http_options = {}
    if api_version:
        http_options["api_version"] = api_version

    if project:
        # api_version matters on Vertex too — the TTS client asks for v1alpha.
        return genai.Client(
            vertexai=True,
            project=project,
            location="global",
            http_options=http_options,
        )

    return genai.Client(api_key=api_key, http_options=http_options)


def upload_video(client, video_path, staging_bucket=None):
    """Make `video_path` usable as a request part on whichever backend we're on.

    Returns `(part, cleanup)` — see video_source.resolve_video. Vertex has no
    Files API, so this is not always an upload.
    """
    return resolve_video(client, video_path, staging_bucket=staging_bucket)


def retime_sections(client, video_part, sections, video_duration):
    """Have Gemini watch the video and determine optimal start times for each narration."""

    sections_json = json.dumps(
        [
            {
                "index": i,
                "original_timestamp": s["timestamp"],
                "narration": s["narration"],
                "audio_duration_seconds": round(s.get("exact_duration", 0), 2),
            }
            for i, s in enumerate(sections)
        ],
        indent=2,
    )

    prompt = f"""Watch this screen recording carefully. I need to overlay narration audio
onto this video. Below are the narration segments I want to place.

Your job: Determine the EXACT second (float) in this video where each segment should START playing,
based on the visual cues in the recording.

CRITICAL RULES:
1. NO OVERLAPS: Narration segments must never overlap.
2. EXACT DURATIONS: You are provided the exact `audio_duration_seconds` for each segment.
   You MUST ensure that: start_seconds[i] + audio_duration_seconds[i] <= start_seconds[i+1].
   Leave a small natural pause (0.5 - 1s) between segments if possible.
3. TIMING: If a visual moment is too short for the planned narration, prioritize the start time of the
   visual cue, but push subsequent segments later if needed to avoid audio collision.
4. ORDER: Keep segments in the same order as provided.

The narration segments (in order):
{sections_json}

Return a JSON array of objects, one per segment:
[
  {{"index": 0, "start_seconds": <float>, "reason": "<precise visual cue you're syncing to>"}},
  ...
]"""

    max_retries = 5
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=_VISION_MODEL,
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            video_part,
                            types.Part.from_text(text=prompt),
                        ],
                    )
                ],
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_level="high"),
                    response_mime_type="application/json",
                ),
            )
            break
        except Exception as e:
            wait = 10 * (attempt + 1)
            print(f"  Error: {e}. Retrying in {wait}s...")
            time.sleep(wait)
            if attempt == max_retries - 1:
                raise

    content = response.text.strip()
    if content.startswith("```json"):
        content = content[7:-3]
    elif "```" in content:
        content = re.sub(r"```json|```", "", content).strip()
    return json.loads(content)


def generate_tts_single(client, script, voice=None, index=0):
    """Generate a single TTS segment. Returns raw WAV bytes."""
    voice = voice or os.getenv("VIDEO_TTS_VOICE", "Puck")
    response = client.models.generate_content(
        model=_TTS_MODEL,
        contents=script,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                )
            ),
        ),
    )

    audio_data = b""
    for candidate in response.candidates:
        if candidate.content and candidate.content.parts:
            for part in candidate.content.parts:
                if part.inline_data and part.inline_data.data:
                    audio_data += part.inline_data.data

    if not audio_data:
        raise RuntimeError(f"TTS returned no audio data for segment {index}")

    return audio_data


def tts_to_pcm(audio_bytes):
    """Return raw 16-bit PCM frames from whatever the TTS model handed back.

    The backends differ: some return a full RIFF/WAV container, some return bare
    PCM (mimeType audio/L16). Slicing a fixed `[44:]` is wrong for both — it
    eats 44 bytes of real audio in the bare-PCM case, and mis-cuts a WAV whose
    header is not the textbook 44 bytes (extended fmt, LIST/metadata chunks),
    which shifts every later frame and clicks. Sniff, then let `wave` find the
    data chunk.
    """
    if audio_bytes[:4] != b"RIFF":
        return audio_bytes

    with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
        if (wf.getframerate(), wf.getnchannels(), wf.getsampwidth()) != (
            _TTS_SAMPLE_RATE,
            _TTS_CHANNELS,
            _TTS_SAMPLE_WIDTH,
        ):
            raise RuntimeError(
                "TTS returned "
                f"{wf.getframerate()}Hz/{wf.getnchannels()}ch/"
                f"{wf.getsampwidth() * 8}-bit, but the pipeline concatenates raw "
                f"{_TTS_SAMPLE_RATE}Hz/{_TTS_CHANNELS}ch/{_TTS_SAMPLE_WIDTH * 8}-bit "
                "frames. Update _TTS_SAMPLE_RATE/_TTS_CHANNELS/_TTS_SAMPLE_WIDTH."
            )
        return wf.readframes(wf.getnframes())


def mux_audio_video(video_path, audio_path, output_path):
    """Mux the final audio track onto the original video."""
    print("  Muxing audio onto video (Final Render)...")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-i",
        audio_path,
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "22",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-shortest",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def evaluate_output(client, video_path, staging_bucket=None):
    """Pass 4: Self-Evaluation. Have Gemini watch the output and check for corruption."""
    print("4. Self-Evaluating Output Quality...")
    video_part, cleanup_video = upload_video(
        client, video_path, staging_bucket=staging_bucket
    )

    prompt = """Watch this final rendered video carefully.
    Perform a comprehensive quality and content evaluation across four areas.

    Part 1: Technical Quality
    1. Visual Stalls: Does the video freeze on a single frame while the file continues?
    2. Audio Corruption: Is there any digital static, harsh noise, or audio-visual desync?
    3. AUDIO OVERLAPS: Do segments of narration overlap? Is there any moment where two sentences
       play at once or the previous one hasn't finished before the next starts?
       This is a CRITICAL failure mode.

    Part 2: Content & Timing Audit
    4. Logical Timing: Does the narration accurately match what is happening on screen at that exact moment?
       Are there times when the audio refers to something before it appears or long after it has passed?
    5. Dead Air: Are there gaps longer than 8 seconds where something is happening on screen but no narration plays?
    6. Talk Track Improvements: Based on the visual flow, are there sentences that feel rushed, too slow, or contextually misaligned?

    Part 3: Visual Polish (for publicly shareable content)
    7. Sensitive data exposure: Does any screen recording show real customer names, internal files,
       other customers' data, or personal information in file browsers, dropdowns, or UI lists?
       Flag any frame where a file picker, storage browser, or dropdown reveals names that should not be public.
    8. UI consistency: Are slide borders, button colors, fonts, or design elements visually inconsistent
       across the video (e.g., mismatched border colors, wrong brand colors on slide elements)?
    9. Branding issues: Are there any watermarks, personal account names, dev/staging URLs,
       or draft content visible that would be inappropriate in a customer-facing video?

    Part 4: Overall Readiness
    10. Is this video ready to share externally with a customer or executive audience?

    Return your evaluation in JSON:
    {
      "status": "PASS" | "FAIL",
      "issues": ["list of technical issues found"],
      "details": "detailed observation of technical quality",
      "timing_audit": "evaluation of how well the audio matches the visual cues",
      "polish_issues": ["list of any sensitive data, branding, or visual consistency issues"],
      "externally_shareable": true | false,
      "suggested_improvements": ["list of actionable suggestions to improve the talk track, timing, or visual polish"]
    }"""

    response = client.models.generate_content(
        model=_VISION_MODEL,
        contents=[
            types.Content(
                role="user",
                parts=[
                    video_part,
                    types.Part.from_text(text=prompt),
                ],
            )
        ],
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level="high"),
            response_mime_type="application/json",
        ),
    )

    cleanup_video()

    content = response.text.strip()
    if content.startswith("```json"):
        content = content[7:-3]
    elif "```" in content:
        content = re.sub(r"```json|```", "", content).strip()
    eval_result = json.loads(content)

    print(f"   Evaluation Status: {eval_result['status']}")
    if eval_result["status"] == "FAIL":
        print(f"   CRITICAL ISSUES DETECTED: {', '.join(eval_result['issues'])}")
        print(f"   Details: {eval_result['details']}")
    else:
        print("   Technical quality check passed.")

    if "timing_audit" in eval_result:
        print(f"   Timing Audit: {eval_result['timing_audit']}")
    if eval_result.get("suggested_improvements"):
        print("   Suggested Improvements:")
        for imp in eval_result["suggested_improvements"]:
            print(f"    - {imp}")

    return eval_result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("video_path")
    parser.add_argument("talk_track_path")
    parser.add_argument("--output", default="final_output.mp4")
    parser.add_argument("--voice", default="Puck")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"),
    )
    parser.add_argument(
        "--gcs-bucket",
        default=os.environ.get("VIDEO_STAGING_BUCKET"),
        help="On Vertex, stage oversized videos into this bucket",
    )
    parser.add_argument(
        "--project",
        default=default_project(),
        help="GCP project for Vertex AI with ADC (default: gcloud config)",
    )
    parser.add_argument("--retime", action="store_true")
    parser.add_argument("--narrate-until", type=float)
    parser.add_argument("--skip-eval", action="store_true")
    args = parser.parse_args()

    client = create_client(api_key=args.api_key, project=args.project)
    tts_client = create_client(
        api_key=args.api_key, project=args.project, api_version="v1alpha"
    )

    print("1. Parsing talk track...")
    sections = parse_talk_track(args.talk_track_path, narrate_until=args.narrate_until)

    print("2. Generating TTS and calculating exact durations...")
    os.makedirs("temp_segments", exist_ok=True)
    bytes_per_sec = _TTS_SAMPLE_RATE * _TTS_CHANNELS * _TTS_SAMPLE_WIDTH

    for i, section in enumerate(sections):
        print(f"    Generating segment {i}...")
        audio_bytes = generate_tts_single(
            tts_client, section["narration"], voice=args.voice, index=i
        )

        # The segments are concatenated as raw frames, so unwrap the container
        # if there is one. See tts_to_pcm — the backends are not consistent.
        pcm_raw = tts_to_pcm(audio_bytes)

        seg_duration = len(pcm_raw) / bytes_per_sec
        section["exact_duration"] = seg_duration

        pcm_path = f"temp_segments/seg_{i}.pcm"
        with open(pcm_path, "wb") as f:
            f.write(pcm_raw)

    if args.retime:
        print("3. Re-timing sections with known precise durations...")
        video_part, cleanup_video = upload_video(
            client, args.video_path, staging_bucket=args.gcs_bucket
        )
        duration_cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            args.video_path,
        ]
        video_duration = float(subprocess.check_output(duration_cmd).decode().strip())
        timed_offsets = retime_sections(client, video_part, sections, video_duration)
        for offset in timed_offsets:
            sections[offset["index"]]["start_secs"] = offset["start_seconds"]
        cleanup_video()
    else:
        print("3. Using provided timestamps.")

    print("4. Assembling audio track...")
    current_pos = 0.0

    final_audio = "temp_final_audio.wav"

    # Filter complex labels for ffmpeg
    filter_parts = []

    for i, section in enumerate(sections):
        # Programmatic Overlap Check
        if section["start_secs"] < current_pos:
            print(
                f"    WARNING: Segment {i} overlaps with previous! (Intended start: {section['start_secs']:.2f}s, Current pos: {current_pos:.2f}s)"
            )
            print(
                f"    Shifted segment {i} to start at {current_pos:.2f}s to avoid collision."
            )
            section["start_secs"] = current_pos

        current_pos = section["start_secs"] + section["exact_duration"]

        ms = int(section["start_secs"] * 1000)
        filter_parts.append(f"[{i}:a]adelay={ms}|{ms}[d{i}]")

    inputs_labeled = "".join([f"[d{i}]" for i in range(len(sections))])
    filter_str = (
        ";".join(filter_parts)
        + f";{inputs_labeled}amix=inputs={len(sections)}:duration=longest[out]"
    )

    full_cmd = ["ffmpeg", "-y"]
    for i in range(len(sections)):
        full_cmd.extend(
            [
                "-f",
                "s16le",
                "-ar",
                str(_TTS_SAMPLE_RATE),
                "-ac",
                "1",
                "-i",
                f"temp_segments/seg_{i}.pcm",
            ]
        )
    full_cmd.extend(["-filter_complex", filter_str, "-map", "[out]", final_audio])

    print("  Assembling audio track with ffmpeg filter_complex...")
    subprocess.run(full_cmd, check=True, capture_output=True)

    print("Creating final video...")
    mux_audio_video(args.video_path, final_audio, args.output)

    # Cleanup
    import shutil

    shutil.rmtree("temp_segments")
    if os.path.exists(final_audio):
        os.remove(final_audio)

    if not args.skip_eval:
        evaluate_output(client, args.output, staging_bucket=args.gcs_bucket)

    print(f"\nSuccess! Narrated video saved to: {args.output}")


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
