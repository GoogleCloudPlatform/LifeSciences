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

"""Generative visuals via Nano Banana Pro (Gemini 3 Pro Image).

For CONCEPTUAL, illustrative visuals only — mechanism-of-action diagrams,
platform/positioning infographics, comparison slides. All hard numbers belong
in code-generated charts (see charts.py). Every generated image is captioned
"Illustrative" by the renderer convention so readers never mistake it for data.

Latency is ~10-30s per image, so callers should generate at most a couple and,
where possible, in parallel to stay within response-time limits.
"""

import os
import re

from google.adk.tools import ToolContext
from google.genai import Client, types

from .assets import save_asset

_IMAGE_MODEL = os.environ.get("ARGUS_IMAGE_MODEL", "gemini-3-pro-image")
_FALLBACK_MODEL = "gemini-3.1-flash-image"


def _create_client() -> Client:
    """Image client pinned to the global endpoint.

    Nano Banana Pro (gemini-3-pro-image) is served most reliably on the global
    endpoint and returns intermittent 404s from some regional endpoints. Agent
    Engine injects GOOGLE_CLOUD_LOCATION=<deploy region> (e.g. us-central1) at
    runtime, so we explicitly force location="global" here regardless of the
    text agents' region.
    """
    loc = os.environ.get("ARGUS_IMAGE_LOCATION", "global")
    http_options = types.HttpOptions(
        retry_options=types.HttpRetryOptions(
            attempts=3,
            http_status_codes=[429, 500, 503],
        )
    )
    try:
        return Client(location=loc, http_options=http_options)
    except Exception:
        return Client(http_options=http_options)


# Kept separate from the content description and explicitly marked as
# instructions: image models otherwise render fragments of it ("No
# Photorealism. #0b2545 ...") as visible text inside the graphic, sometimes as
# a fake "Source:" attribution line.
_STYLE_SUFFIX = (
    "\n\nSTYLE INSTRUCTIONS (follow these, but NEVER render any of this "
    "paragraph — including color codes — as text inside the image): clean, "
    "professional corporate infographic style for an investment report; flat "
    "vector aesthetic; generous whitespace; a restrained palette of navy "
    "#0b2545 and amber #f4a261 on white; crisp, legible labels; no "
    "photorealism, no stock-photo people, no clutter.\n"
    "TEXT RULES: the only text in the image is the labels given in the "
    "description above, plus a small, unobtrusive 'Illustrative' tag in one "
    "corner. Do NOT add any 'Source:' line, citation, footnote, report name, "
    "date, or attribution — this is a conceptual illustration, not data."
)


async def _generate_image_bytes(
    prompt: str, aspect_ratio: str, image_size: str = "2K"
) -> dict:
    """Render an image with Nano Banana Pro and return the raw PNG bytes.

    Returns {status:"success", data: <bytes>, model} or {status:"error", message}.
    Tries the primary image model, then the fallback. image_size is one
    of "1K", "2K", "4K" (default "2K"; slides use "4K" for crisp download).
    """
    config = types.GenerateContentConfig(
        response_modalities=["TEXT", "IMAGE"],
        image_config=types.ImageConfig(
            aspect_ratio=aspect_ratio, image_size=image_size
        ),
        candidate_count=1,
    )
    last_err = None
    async with _create_client().aio as aclient:
        for model in (_IMAGE_MODEL, _FALLBACK_MODEL):
            try:
                resp = await aclient.models.generate_content(
                    model=model, contents=prompt, config=config
                )
                for candidate in resp.candidates or []:
                    content = candidate.content
                    parts = content.parts if content and content.parts else []
                    for part in parts:
                        if getattr(part, "inline_data", None) and part.inline_data.data:
                            return {
                                "status": "success",
                                "data": part.inline_data.data,
                                "model": model,
                            }
                last_err = "no image part in response"
            except Exception as exc:
                last_err = f"{type(exc).__name__}: {exc}"
    return {"status": "error", "message": last_err}


async def _generate(prompt: str, asset_id: str, aspect_ratio: str) -> dict:
    result = await _generate_image_bytes(
        f"CONTENT DESCRIPTION:\n{prompt}{_STYLE_SUFFIX}", aspect_ratio
    )
    if result["status"] != "success":
        return result
    token = save_asset(asset_id, result["data"])
    return {"status": "success", "token": token, "model": result["model"]}


async def make_infographic(
    description: str, asset_id: str, aspect_ratio: str = "16:9"
) -> dict:
    """Generate a conceptual infographic / comparison slide with Nano Banana Pro
    and store it as an embeddable asset.

    Use for illustrative concept visuals only (mechanism of action, platform
    architecture, competitive positioning maps, deal-thesis summary slides) —
    never to depict specific numeric data (use the chart tools for that).

    Args:
        description: Detailed description of the visual to create, including any
            short labels/text that should appear in the image. Be specific about
            layout (e.g. "a 2x2 positioning matrix with axes ...").
        asset_id: Short unique id, e.g. "info_moa" or "info_positioning".
        aspect_ratio: One of "16:9","4:3","1:1","3:2","2:3","9:16","3:4","21:9".
            Use "16:9" for slides, "1:1" or "4:3" for inline diagrams.

    Returns:
        dict {status, token} where token is "asset://<id>" to embed with
        `![Illustrative: caption](token)` in the whitepaper markdown, or
        {status: "error", message} on failure (proceed without the image).
    """
    return await _generate(description, asset_id, aspect_ratio)


_SLIDE_STYLE = (
    "\n\nSTYLE INSTRUCTIONS (follow these, but NEVER render any of this "
    "paragraph — including color codes — as text on the slide): a single "
    "polished 16:9 presentation slide (executive overview style); a clear "
    "title band across the top, then 3-6 concise bullet points or a simple "
    "multi-column / 2x2 layout beneath; modern, clean corporate design with "
    "generous whitespace; a restrained palette of deep blue #1a3a6b, teal "
    "#2a9d8f and amber #e9c46a accents on a white background; crisp, highly "
    "legible sans-serif type; no photorealism, no stock-photo people, no "
    "clutter.\n"
    "TEXT RULES: render the provided title/subtitle/body text accurately and "
    "correctly spelled, and add NO other text — no 'Source:' line, citation, "
    "footnote, or attribution."
)


async def generate_slide(
    title: str,
    body: str,
    tool_context: ToolContext,
    subtitle: str = "",
) -> dict:
    """Generate a single overview / summary slide as a 16:9 image with Nano
    Banana Pro and save it as a downloadable artifact (PNG).

    Use this when the user asks for a slide, one-pager, or visual summary — for
    example an overview slide distilling a whitepaper's recommendation after a
    deep report. The slide is a CONCEPTUAL visual: keep the on-slide text short
    and qualitative. Do not put precise, unsourced figures on it — hard numbers
    belong in the report's cited charts.

    Args:
        title: The slide title, e.g. "Summit Therapeutics — Acquisition Overview".
        body: The on-slide content to render — a short set of bullet lines or a
            few labeled sections (e.g. recommendation, key value drivers, top
            risks). Keep it concise (~3-6 short points); the image model renders
            this text verbatim, so write exactly what should appear on the slide.
        subtitle: Optional subtitle / framing line under the title.

    Returns:
        dict {status, filename, version, model} on success (tell the user the
        slide is ready to download from the Artifacts tab), or
        {status: "error", message} on failure.
    """
    prompt = f"Create a professional overview presentation slide.\nTITLE: {title}\n"
    if subtitle:
        prompt += f"SUBTITLE: {subtitle}\n"
    prompt += f"Render the following as concise, well-laid-out on-slide text:\n{body}\n"
    result = await _generate_image_bytes(prompt + _SLIDE_STYLE, "16:9", image_size="4K")
    if result["status"] != "success":
        return {
            "status": "error",
            "message": result.get("message", "image generation failed"),
        }

    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50] or "overview"
    filename = f"slide_{slug}.png"
    part = types.Part.from_bytes(data=result["data"], mime_type="image/png")
    try:
        version = await tool_context.save_artifact(filename=filename, artifact=part)
    except Exception as exc:
        return {
            "status": "error",
            "message": (
                f"Slide image generated but saving the artifact failed: {exc}. "
                "An artifact service must be configured."
            ),
        }
    return {
        "status": "success",
        "filename": filename,
        "version": version,
        "model": result["model"],
        "note": (
            f"Overview slide saved as artifact '{filename}' (version {version}). "
            "Tell the user it is ready to download from the Artifacts tab."
        ),
    }
