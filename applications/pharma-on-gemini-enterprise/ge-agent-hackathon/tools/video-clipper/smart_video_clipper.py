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

import argparse
import json
import os
import subprocess
import sys

from dotenv import load_dotenv
from google import genai
from google.genai import types

from video_source import default_project, explain_api_error, resolve_video

# Model IDs and auth live in .env — load it before any os.getenv default below.
load_dotenv()


def format_time(seconds):
    h = int(seconds / 3600)
    m = int((seconds % 3600) / 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_mmss(seconds):
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}:{s:02d}"


def get_video_duration(video_path):
    """Get video duration in seconds via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            video_path,
        ],
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def configure_gemini(api_key, project=None):
    """Return a google-genai Client.

    Pass --project to use Vertex AI (with ADC) instead of an API key.
    """
    if project:
        return genai.Client(vertexai=True, project=project, location="global")
    base_url = os.environ.get("GOOGLE_GEMINI_BASE_URL")
    if base_url:
        return genai.Client(
            api_key=api_key, http_options=types.HttpOptions(base_url=base_url)
        )
    return genai.Client(api_key=api_key)


def has_subtitles_filter():
    """Is ffmpeg built with libass?

    The `subtitles` filter needs it, and plenty of builds (including some
    Homebrew ones) ship without. Without this check the run dies mid-render
    with an opaque non-zero exit, several minutes and two model calls in.
    """
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except Exception:
        return False
    return any(line.split()[1:2] == ["subtitles"] for line in out.splitlines())


def analyze_video(
    client,
    video_part,
    target_duration_secs,
    model_name,
    video_duration,
    talk_track=None,
):
    """Pass 1: Watch the video and produce an optimal clipping plan."""

    print("Generating clipping plan...")
    target_minutes = target_duration_secs / 60.0

    duration_rule = (
        f"STRICT CONSTRAINTS:\n"
        f"- The source video is exactly {video_duration:.1f} seconds long. "
        f"All start_seconds and end_seconds values MUST be between 0 and {video_duration:.1f}. "
        f"Never reference a timestamp beyond {video_duration:.1f}s.\n"
        f"- The total duration of all clips MUST be between "
        f"{target_duration_secs - 5} and {target_duration_secs + 5} seconds. "
        f"This is non-negotiable — the presenter has a fixed time slot."
    )

    if talk_track:
        prompt = f"""You are a professional video editor. Watch this screen recording of a product demo carefully.

I have a draft talk track that describes the story I want to tell over this video.
Use it as a GUIDE — not a rigid script. You have creative liberty to:
- Reorder moments if the video flows better that way.
- Include compelling moments you spot that the talk track missed.
- Skip talk track beats that don't have strong visual support in the video.
- Adjust pacing so high-impact moments breathe and transitions feel natural.

Your goal: produce the best possible {target_minutes:.1f}-minute demo reel from this raw footage.

Draft talk track (use as guide):
---
{talk_track}
---

{duration_rule}

Return a JSON object with:
{{
  "clips": [
    {{
      "start_seconds": <float>,
      "end_seconds": <float>,
      "subtitle": "<short professional subtitle for on-screen display>",
      "narration_note": "<what the presenter should be saying during this clip>"
    }}
  ],
  "total_duration_seconds": <float — sum of all clip durations>,
  "editorial_notes": "<brief explanation of what you kept, cut, or reordered and why>"
}}

Order clips for the best narrative flow. Trim dead time, loading spinners, and typing delays aggressively. Keep moments where results appear on screen — those are the payoff."""
    else:
        prompt = f"""You are a professional video editor. Watch this screen recording carefully.

Identify the most important and visually compelling segments. Cut dead time, loading,
repetitive actions, and errors. Keep the moments that demonstrate value.

{duration_rule}

Return a JSON object with:
{{
  "clips": [
    {{
      "start_seconds": <float>,
      "end_seconds": <float>,
      "subtitle": "<short professional subtitle>",
      "narration_note": "<what a presenter could say during this clip>"
    }}
  ],
  "total_duration_seconds": <float — sum of all clip durations>,
  "editorial_notes": "<brief explanation of choices>"
}}"""

    response = client.models.generate_content(
        model=model_name,
        contents=[video_part, types.Part.from_text(text=prompt)],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )

    content = response.text.strip()
    if content.startswith("```json"):
        content = content[7:-3]
    clip_plan = json.loads(content)

    # --- Validate and clamp clip boundaries ---
    for clip in clip_plan["clips"]:
        clip["start_seconds"] = max(0, min(clip["start_seconds"], video_duration))
        clip["end_seconds"] = max(0, min(clip["end_seconds"], video_duration))
        if clip["end_seconds"] <= clip["start_seconds"]:
            clip["end_seconds"] = min(clip["start_seconds"] + 1, video_duration)

    # Recalculate total after clamping
    clip_plan["total_duration_seconds"] = sum(
        c["end_seconds"] - c["start_seconds"] for c in clip_plan["clips"]
    )

    return clip_plan


def generate_talk_track(
    client, model_name, clip_plan, target_duration_secs, talk_track=None
):
    """Pass 2: Generate a polished, timed talk track matched to the final clips."""

    print("Generating final talk track...")
    target_minutes = target_duration_secs / 60.0

    # Calculate per-clip duration and word budget (~2.5 words/sec for natural TTS pace)
    clips_with_budget = []
    running_time = 0.0
    for i, clip in enumerate(clip_plan["clips"]):
        clip_dur = clip["end_seconds"] - clip["start_seconds"]
        word_budget = int(clip_dur * 2.5)
        clips_with_budget.append(
            {
                **clip,
                "clip_index": i,
                "clip_duration_seconds": round(clip_dur, 1),
                "word_budget": word_budget,
                "output_timestamp": format_mmss(running_time),
            }
        )
        running_time += clip_dur

    clips_summary = json.dumps(clips_with_budget, indent=2)
    editorial = clip_plan.get("editorial_notes", "")

    reference_section = ""
    if talk_track:
        reference_section = f"""
The presenter provided this draft talk track as a style/tone reference:
---
{talk_track}
---
Match the voice and energy of this draft. You may adapt wording freely but keep the same presenter persona."""

    prompt = f"""You are a presentation coach writing the final talk track for a live product demo.

Here is the clipping plan — these are the exact video segments that will play, in order.
Each clip includes its duration and a WORD BUDGET (max words for that section's narration):
{clips_summary}

Editor's notes on what was kept/cut: {editorial}
{reference_section}

Write a polished talk track the presenter will read/memorize while the trimmed video plays.
Total video duration: ~{target_minutes:.1f} minutes ({target_duration_secs} seconds).

Requirements:
- Include timestamps showing when each section starts (e.g., [0:00], [0:35], [1:12]).
  Use the output_timestamp values provided above.
- CRITICAL: Each section's narration MUST be within its word_budget. This ensures the
  TTS voiceover fits within each clip without overlapping the next section. A section with
  word_budget 25 means AT MOST 25 words of narration. Count carefully.
- Write in a natural, conversational presenter voice — not robotic or over-polished.
- Do NOT include [bracketed stage directions] — they interfere with TTS generation.
  Weave visual callouts into the narration naturally instead (e.g., "as you can see here").
- End with a brief closer/transition line.

Return a JSON object with:
{{
  "talk_track": "<the full talk track as a single string with newlines>",
  "sections": [
    {{
      "timestamp": "<M:SS>",
      "clip_index": <int>,
      "narration": "<what to say during this clip — must respect word_budget>"
    }}
  ]
}}"""

    response = client.models.generate_content(
        model=model_name,
        contents=[types.Part.from_text(text=prompt)],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )

    content = response.text.strip()
    if content.startswith("```json"):
        content = content[7:-3]
    return json.loads(content)


def format_talk_track_file(talk_track_result, clip_plan, target_duration_secs):
    """Format the talk track into a readable text file."""

    lines = []
    lines.append("=" * 60)
    lines.append("FINAL TALK TRACK — LIVE DEMO")
    lines.append(f"Total duration: {format_mmss(target_duration_secs)}")
    lines.append("=" * 60)
    lines.append("")

    # If the model returned a pre-formatted talk_track string, use it
    if talk_track_result.get("talk_track"):
        lines.append(talk_track_result["talk_track"])
        lines.append("")

    # Also include the timed section breakdown
    if "sections" in talk_track_result:
        lines.append("-" * 60)
        lines.append("TIMED SECTION BREAKDOWN")
        lines.append("-" * 60)
        lines.append("")
        for section in talk_track_result["sections"]:
            ts = section.get("timestamp", "?:??")
            narration = section.get("narration", "")
            lines.append(f"[{ts}]")
            lines.append(narration)
            lines.append("")

    # Append editorial notes from the clip plan
    if clip_plan.get("editorial_notes"):
        lines.append("-" * 60)
        lines.append("EDITORIAL NOTES (what was kept/cut/reordered)")
        lines.append("-" * 60)
        lines.append(clip_plan["editorial_notes"])
        lines.append("")

    # Append clip details for reference
    lines.append("-" * 60)
    lines.append("CLIP REFERENCE (source timecodes from raw video)")
    lines.append("-" * 60)
    running = 0.0
    for i, clip in enumerate(clip_plan["clips"]):
        start = clip["start_seconds"]
        end = clip["end_seconds"]
        dur = end - start
        lines.append(
            f"  Clip {i + 1}: raw {start:.1f}s–{end:.1f}s "
            f"({dur:.1f}s) → plays at {format_mmss(running)} in final video"
        )
        lines.append(f"          {clip.get('subtitle', '')}")
        running += dur
    lines.append("")
    lines.append(f"Total: {running:.1f}s ({format_mmss(running)})")

    return "\n".join(lines)


def process_video(input_video, output_video, clips, no_subtitles=False):
    workspace = os.path.dirname(output_video) or "."
    tmp_dir = os.path.join(workspace, "tmp_clips")
    os.makedirs(tmp_dir, exist_ok=True)

    clip_files = []
    try:
        for i, clip in enumerate(clips):
            start = clip.get("start_seconds", 0)
            end = clip.get("end_seconds", 0)
            subtitle = clip.get("subtitle", "")
            duration = end - start

            if duration <= 0:
                print(f"Skipping invalid clip {i} (duration <= 0)")
                continue

            out_clip = os.path.join(tmp_dir, f"clip_{i}.mp4")

            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                str(start),
                "-i",
                input_video,
                "-t",
                str(duration),
            ]

            if not no_subtitles and subtitle:
                srt_content = f"1\n{format_time(0.5)} --> {format_time(duration - 0.5)}\n{subtitle}\n"
                srt_path = os.path.join(tmp_dir, f"clip_{i}.srt")
                with open(srt_path, "w") as f:
                    f.write(srt_content)
                # ffmpeg's filtergraph parser treats ':' as the option separator
                # and '\' as an escape, so a path like /tmp/a:b/clip_0.srt is read
                # as a truncated filename plus a bogus option. Escape both.
                srt_arg = srt_path.replace("\\", "\\\\").replace(":", "\\:")
                cmd.extend(
                    [
                        "-vf",
                        f"subtitles={srt_arg}:force_style='FontSize=24,PrimaryColour=&H00FFFFFF,"
                        f"OutlineColour=&H00000000,BorderStyle=1,Outline=2'",
                    ]
                )

            cmd.extend(
                [
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-c:a",
                    "aac",
                    out_clip,
                ]
            )

            print(f"Rendering clip {i + 1}/{len(clips)} ({duration:.1f}s)...")
            subprocess.run(
                cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            clip_files.append(out_clip)

        # Concatenate
        concat_file = os.path.join(tmp_dir, "concat.txt")
        with open(concat_file, "w") as f:
            for clip in clip_files:
                f.write(f"file '{os.path.abspath(clip)}'\n")

        print("Concatenating clips into final video...")
        cmd_concat = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_file,
            "-c",
            "copy",
            output_video,
        ]

        subprocess.run(
            cmd_concat, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print(f"Final video: {output_video}")

    finally:
        print("Cleaning up temporary files...")
        for clip in clip_files:
            if os.path.exists(clip):
                os.remove(clip)
        for file in os.listdir(tmp_dir):
            os.remove(os.path.join(tmp_dir, file))
        os.rmdir(tmp_dir)


def main():
    parser = argparse.ArgumentParser(
        description="Use Gemini to intelligently clip and subtitle screen recordings, "
        "optionally guided by a talk track."
    )
    parser.add_argument("input_video", help="Path to the source video file")
    parser.add_argument("output_video", help="Path for the final clipped video")
    parser.add_argument(
        "--duration",
        type=int,
        default=180,
        help="Target duration in seconds (default: 180). This is a hard constraint.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=os.getenv("VIDEO_CLIPPER_MODEL", "gemini-3.1-pro-preview"),
        help="Gemini model to use",
    )
    parser.add_argument(
        "--api_key",
        type=str,
        default=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"),
        help="Google Gemini API Key",
    )
    parser.add_argument(
        "--project",
        type=str,
        default=default_project(),
        help="Use Vertex AI in this GCP project (with ADC) instead of an API key.",
    )
    parser.add_argument(
        "--gcs-bucket",
        type=str,
        default=os.environ.get("VIDEO_STAGING_BUCKET"),
        help="On Vertex, stage videos over the inline cap into this bucket "
        "(default: VIDEO_STAGING_BUCKET). Staged objects are deleted after.",
    )
    parser.add_argument(
        "--talk-track",
        type=str,
        default=None,
        help="Path to a draft talk track. Gemini uses it as a guide (not rigid) "
        "and produces an optimized final talk track timed to the output.",
    )
    parser.add_argument(
        "--no-subtitles",
        action="store_true",
        help="Disable generating on-screen subtitles.",
    )

    args = parser.parse_args()

    if not args.api_key and not args.project:
        print(
            "Error: set GOOGLE_API_KEY (or GEMINI_API_KEY) for AI Studio, or "
            "--project / GOOGLE_CLOUD_PROJECT to use Vertex AI with ADC."
        )
        exit(1)

    if not os.path.exists(args.input_video):
        print(f"Error: Input video '{args.input_video}' not found.")
        exit(1)

    if not args.no_subtitles and not has_subtitles_filter():
        print(
            "Note: this ffmpeg has no 'subtitles' filter (built without "
            "libass), so on-screen subtitles are unavailable. Continuing "
            "without them. To enable: install an ffmpeg built with libass."
        )
        args.no_subtitles = True

    talk_track_text = None
    if args.talk_track:
        if not os.path.exists(args.talk_track):
            print(f"Error: Talk track file '{args.talk_track}' not found.")
            exit(1)
        with open(args.talk_track) as f:
            talk_track_text = f.read()
        print(f"Loaded draft talk track from '{args.talk_track}'")

    # --- Configure ---
    print("=== Smart Video Clipper ===")
    client = configure_gemini(args.api_key, project=args.project)

    # --- Get source video duration ---
    video_duration = get_video_duration(args.input_video)
    print(
        f"Source video duration: {video_duration:.1f}s ({format_mmss(video_duration)})"
    )

    if args.duration > video_duration:
        print(
            f"Warning: Target duration ({args.duration}s) exceeds source video "
            f"({video_duration:.1f}s). Clamping to {int(video_duration)}s."
        )
        args.duration = int(video_duration)

    # --- Pass 1: Upload video and generate clipping plan ---
    video_part, cleanup_video = resolve_video(
        client, args.input_video, staging_bucket=args.gcs_bucket
    )

    try:
        clip_plan = analyze_video(
            client,
            video_part,
            args.duration,
            args.model,
            video_duration,
            talk_track=talk_track_text,
        )
    finally:
        cleanup_video()

    # --- Validate duration ---
    total = sum(c["end_seconds"] - c["start_seconds"] for c in clip_plan["clips"])
    print(f"\nClipping plan: {len(clip_plan['clips'])} clips, {total:.1f}s total")
    if clip_plan.get("editorial_notes"):
        print(f"Editorial notes: {clip_plan['editorial_notes']}")

    print("\nClip breakdown:")
    for i, c in enumerate(clip_plan["clips"]):
        dur = c["end_seconds"] - c["start_seconds"]
        print(
            f"  {i + 1}. [{c['start_seconds']:.1f}s → {c['end_seconds']:.1f}s] "
            f"({dur:.1f}s) {c['subtitle']}"
        )

    # --- Pass 2: Generate final talk track ---
    talk_track_result = generate_talk_track(
        client, args.model, clip_plan, args.duration, talk_track=talk_track_text
    )

    # --- Save talk track ---
    output_base = os.path.splitext(args.output_video)[0]
    talk_track_path = output_base + "_talk_track.txt"

    talk_track_content = format_talk_track_file(
        talk_track_result, clip_plan, args.duration
    )
    with open(talk_track_path, "w") as f:
        f.write(talk_track_content)
    print(f"\nFinal talk track saved: {talk_track_path}")

    # --- Render video ---
    process_video(
        args.input_video,
        args.output_video,
        clip_plan["clips"],
        no_subtitles=args.no_subtitles,
    )

    print("\n=== Done ===")
    print(f"  Video:      {args.output_video}")
    print(f"  Talk track: {talk_track_path}")
    print(f"  Duration:   ~{format_mmss(args.duration)}")


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
