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

"""Get a video into a Gemini request, whichever backend you're on.

There are three transports and they are not interchangeable:

| Backend            | Transport                        | Size limit |
| ------------------ | -------------------------------- | ---------- |
| AI Studio (API key)| Files API — upload, poll, delete | 2 GB       |
| Vertex (--project) | `gs://` URI                      | large      |
| Vertex (--project) | inline bytes                     | ~20 MB     |

The trap: `client.files.upload()` raises `ValueError: This method is only
supported in the Gemini Developer client` on a Vertex client. Vertex has no
Files API at all. So a script that only knows how to upload works with an API
key and dies with `--project`.

Use `resolve_video()` and you don't have to care which one you're on.
"""

import os
import re
import subprocess

from google.genai import types

# Vertex inlines the bytes into the request body, so this has to stay well
# under the request ceiling.
INLINE_LIMIT_BYTES = 20 * 1024 * 1024

_MIME_BY_EXT = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    # Audio too: segmenting a long recording by ear is much cheaper than
    # sampling its frames, and the same staging logic applies.
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
}


def default_project():
    """The GCP project to use with ADC, or None.

    Checks `GOOGLE_CLOUD_PROJECT`, then whatever `gcloud config` has set — so a
    developer who has already run `gcloud config set project` needs no env var
    and no flag.
    """
    env = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if env:
        return env
    try:
        out = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except Exception:
        return None
    return out if out and out != "(unset)" else None


def guess_mime(path):
    return _MIME_BY_EXT.get(os.path.splitext(path)[1].lower(), "video/mp4")


def _stage_to_gcs(video_path, bucket, size):
    """Copy `video_path` into `bucket` and return a (part, cleanup) for it."""
    bucket = bucket if bucket.startswith("gs://") else f"gs://{bucket}"
    uri = f"{bucket.rstrip('/')}/video-clipper/{os.path.basename(video_path)}"
    print(f"  {size / 1e6:.0f} MB is over the Vertex inline cap — staging to {uri}...")
    subprocess.run(["gcloud", "storage", "cp", video_path, uri], check=True)

    def cleanup():
        try:
            subprocess.run(
                ["gcloud", "storage", "rm", uri], check=True, capture_output=True
            )
        except Exception as e:
            print(f"  Warning: failed to remove staged object {uri}: {e}")

    return types.Part.from_uri(file_uri=uri, mime_type=guess_mime(video_path)), cleanup


def resolve_video(client, video_path, poll_seconds=5, staging_bucket=None):
    """Return `(part, cleanup)` for `video_path`.

    `part` is a `types.Part` ready to drop into `contents`. `cleanup` is a
    zero-arg callable — call it in a `finally` block. It's a no-op for the
    transports that leave nothing behind.

    On Vertex, a file over the inline cap is copied to `staging_bucket` (or
    `$VIDEO_STAGING_BUCKET`) and referenced by URI. The staged object is
    deleted by `cleanup`.
    """
    # gs:// works on Vertex only, but pass it through either way and let the
    # API give the authoritative error rather than guessing here.
    if video_path.startswith("gs://"):
        print(f"  Using GCS object '{video_path}'")
        return (
            types.Part.from_uri(file_uri=video_path, mime_type=guess_mime(video_path)),
            lambda: None,
        )

    if getattr(client, "vertexai", False):
        size = os.path.getsize(video_path)
        if size <= INLINE_LIMIT_BYTES:
            print(f"  Inlining '{video_path}' ({size / 1e6:.1f} MB) for Vertex...")
            with open(video_path, "rb") as f:
                data = f.read()
            return (
                types.Part.from_bytes(data=data, mime_type=guess_mime(video_path)),
                lambda: None,
            )

        bucket = staging_bucket or os.environ.get("VIDEO_STAGING_BUCKET")
        if bucket:
            return _stage_to_gcs(video_path, bucket, size)

        raise ValueError(
            f"'{video_path}' is {size / 1e6:.0f} MB. Vertex AI has no Files API, "
            f"and inline video is capped around "
            f"{INLINE_LIMIT_BYTES / 1e6:.0f} MB.\n"
            f"Pick one:\n"
            f"  a) Set VIDEO_STAGING_BUCKET (or pass --gcs-bucket) and the file "
            f"is copied to GCS automatically.\n"
            f"  b) Stage it yourself and pass the URI as the input path:\n"
            f"       gcloud storage cp '{video_path}' gs://YOUR_BUCKET/\n"
            f"  c) Use an API key (GOOGLE_API_KEY) instead of --project — that "
            f"path has the Files API and a 2 GB limit."
        )

    # AI Studio: Files API.
    import time

    print(f"  Uploading '{video_path}'...")
    uploaded = client.files.upload(file=video_path)
    while uploaded.state.name == "PROCESSING":
        print("  Waiting for video processing...")
        time.sleep(poll_seconds)
        uploaded = client.files.get(name=uploaded.name)
    if uploaded.state.name == "FAILED":
        raise ValueError("Video processing failed on Gemini's end.")
    print("  Video processed.")

    def cleanup():
        try:
            client.files.delete(name=uploaded.name)
        except Exception as e:  # deleting is best-effort; files expire anyway
            print(f"  Warning: failed to delete uploaded file: {e}")

    return (
        types.Part.from_uri(file_uri=uploaded.uri, mime_type=uploaded.mime_type),
        cleanup,
    )


def explain_api_error(exc, project=None):
    """Turn a genai APIError into one actionable line, or return None.

    A raw 403 traceback tells a first-time user nothing. The two that actually
    happen: the auto-discovered gcloud project isn't the one with Vertex access,
    and a model ID has been withdrawn.
    """
    code = getattr(exc, "code", None)
    msg = str(exc)
    # The API echoes the project it rejected; trust that over the caller's guess.
    named = re.search(r"projects/([\w-]+)/", msg)
    project = named.group(1) if named else project
    if code == 403 or "PERMISSION_DENIED" in msg:
        where = f"project '{project}'" if project else "the current project"
        return (
            f"Permission denied on {where}.\n"
            f"  The project came from `gcloud config get-value project` unless "
            f"you passed --project.\n"
            f"  Fix: gcloud config set project THE_RIGHT_ONE   (or pass "
            f"--project / set GOOGLE_CLOUD_PROJECT)\n"
            f"  Also check: gcloud services enable aiplatform.googleapis.com"
        )
    if code == 404 or "NOT_FOUND" in msg:
        return (
            "Model not found — the ID has probably been withdrawn (a -preview "
            "suffix disappears at GA).\n"
            "  Fix: update the model in .env. See README, "
            "'Checking a model still exists'."
        )
    if code == 429 or "RESOURCE_EXHAUSTED" in msg:
        return "Rate limited. Wait and retry, or use a smaller model for this step."
    return None
