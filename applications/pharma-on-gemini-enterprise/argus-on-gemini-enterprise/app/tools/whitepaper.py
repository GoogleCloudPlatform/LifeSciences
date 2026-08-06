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

"""Tool that renders a markdown whitepaper to PDF and saves it as an ADK
artifact the user can download."""

import re

from google.adk.tools import ToolContext
from google.genai import types

from .pdf_renderer import render_whitepaper_pdf


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s or "target")[:60]


async def generate_whitepaper_pdf(
    markdown_body: str,
    target_name: str,
    subtitle: str,
    tool_context: ToolContext,
) -> dict:
    """Render a completed investment whitepaper (markdown) into a styled PDF and
    save it as a downloadable artifact.

    Call this only once the full whitepaper markdown is written (follow the
    whitepaper_template skill). Do not call it for quick questions.

    Args:
        markdown_body: The complete whitepaper in markdown (headings, tables,
            blockquotes). This becomes the PDF body verbatim.
        target_name: The acquisition target's name, used in the title and
            filename, e.g. "Mersana Therapeutics".
        subtitle: A short subtitle, e.g. "Acquisition Assessment for the
            Strategy Committee".

    Returns:
        dict with {status, filename, version, bytes} on success, or
        {status: "error", message} on failure.
    """
    try:
        pdf_bytes = render_whitepaper_pdf(
            markdown_body,
            title=f"Acquisition Assessment: {target_name}",
            subtitle=subtitle,
        )
    except Exception as exc:  # rendering is the only likely failure point
        return {"status": "error", "message": f"PDF render failed: {exc}"}

    filename = f"whitepaper_{_slug(target_name)}.pdf"
    part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
    try:
        version = await tool_context.save_artifact(filename=filename, artifact=part)
    except Exception as exc:
        return {
            "status": "error",
            "message": (
                f"PDF rendered ({len(pdf_bytes)} bytes) but saving the artifact "
                f"failed: {exc}. An artifact service must be configured."
            ),
        }
    return {
        "status": "success",
        "filename": filename,
        "version": version,
        "bytes": len(pdf_bytes),
        "note": (
            f"Whitepaper saved as artifact '{filename}' (version {version}). "
            "Tell the user it is ready to download and give them the executive "
            "summary inline."
        ),
    }
