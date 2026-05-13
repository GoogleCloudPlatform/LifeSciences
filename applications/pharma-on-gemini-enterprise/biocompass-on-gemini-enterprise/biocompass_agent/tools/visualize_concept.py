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

"""Concept visualizer powered by Nano Banana Pro (gemini-3-pro-image-preview).

Renders publication-style biomedical figures (mechanism-of-action diagrams,
pathway schematics, study designs, PRISMA flow diagrams) from a textual
description. The tool saves the rendered image as a session artifact and
returns the GE filename marker so it renders inline in the chat.
"""

from __future__ import annotations

import os
import uuid

from google import genai
from google.adk.tools.tool_context import ToolContext
from google.genai import types

_IMAGE_MODEL = os.getenv('IMAGE_MODEL_NAME', 'gemini-3-pro-image-preview')
_IMAGE_SIZE = os.getenv('IMAGE_SIZE', '2K')

# A condensed style guide tuned for biomedical / pharma figures. The Nano
# Banana Pro guide notes that explicit aesthetic direction substantially
# improves output quality vs. terse prompts.
_BIOMEDICAL_STYLE = (
    'Style: clean publication-quality scientific diagram suitable for a '
    'peer-reviewed pharma / biomedical journal. Use a calm, restrained '
    'color palette (muted blues, teals, warm grays, with one accent color '
    'for emphasis). Sans-serif labels, generous whitespace, clear left-to-'
    'right or top-to-bottom information flow with directional arrows. '
    'Avoid clipart, photorealistic textures, decorative gradients, and '
    'cartoon styling. When showing molecular structures, use schematic '
    'shapes (oval = protein, double-helix = DNA, single-strand = mRNA, '
    'lipid-bilayer = membrane). Render any text crisply and spell '
    'gene/drug/pathway names exactly as given.'
)


# `tool_context` is injected by the ADK runtime — intentionally NOT mentioned
# in the docstring (per ADK tool-design guidance) so the model never tries to
# pass it itself. It still needs to be in the signature for ADK to inject it.
async def visualize_concept(
    description: str,
    figure_type: str = 'diagram',
    aspect_ratio: str = '16:9',
    tool_context: ToolContext | None = None,
) -> dict[str, str]:
  """Render a biomedical concept as a publication-quality figure (Nano Banana Pro).

  Use to make abstract concepts visual for the user: mechanism-of-action
  diagrams, signaling-pathway schematics, study-design flows, PRISMA
  diagrams, anatomical relationships, target-engagement cartoons. The model
  has January-2025 knowledge of biology — verify any literal facts you need
  rendered (drug names, gene symbols, dose numbers) by including them
  verbatim in the description.

  Best practice: BEFORE calling this tool, draft an enriched description
  that names every entity, the relationships between them, the desired
  layout flow, the labels you want rendered, and the figure caption. The
  more concrete the description, the better the figure.

  Args:
    description: A specific, multi-sentence figure spec. Include: subject,
      entities + their roles, layout, every label/text element verbatim, any
      callouts. 2-6 sentences is the sweet spot.
    figure_type: One of "diagram" (mechanism / pathway / signaling),
      "study_design" (CONSORT / arm allocation / endpoints), "prisma_flow"
      (systematic review screening), "infographic" (comparison panel /
      quick reference), "anatomical" (organ / tissue diagram).
    aspect_ratio: One of "1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4",
      "9:16", "16:9", "21:9". Default "16:9" suits slide / poster use.

  Returns:
    `{filename, marker}` — `marker` is a `<start_of_user_uploaded_file: ...>`
    string that, when echoed verbatim in your reply, renders the image
    inline in the Gemini Enterprise chat. Always pass the marker through to
    the user.
  """
  if tool_context is None:
    return {'error': 'tool_context is required (provided by ADK runtime).'}

  preamble = {
      'diagram': 'Render a clean schematic diagram.',
      'study_design': 'Render a clinical trial study-design schematic '
                      '(arms, randomization, endpoints, follow-up).',
      'prisma_flow': 'Render a PRISMA 2020 systematic-review flow diagram '
                     '(identification -> screening -> eligibility -> '
                     'included), with the exclusion counts the description '
                     'specifies.',
      'infographic': 'Render a comparison-panel infographic (small-multiple '
                     'cards, consistent layout, clear headers).',
      'anatomical': 'Render a stylized anatomical / tissue diagram.',
  }.get(figure_type, 'Render a clean schematic diagram.')

  prompt = (
      f'{preamble}\n\nDo not include the figure title in the image itself.\n\n'
      f'Detailed description:\n{description}\n\n{_BIOMEDICAL_STYLE}'
  )

  client = genai.Client()
  response = client.models.generate_content(
      model=_IMAGE_MODEL,
      contents=prompt,
      config=types.GenerateContentConfig(
          response_modalities=['IMAGE'],
          image_config=types.ImageConfig(
              image_size=_IMAGE_SIZE,
              aspect_ratio=aspect_ratio,
          ),
      ),
  )

  candidates = getattr(response, 'candidates', None) or []
  for candidate in candidates:
    content = getattr(candidate, 'content', None)
    parts = getattr(content, 'parts', None) or []
    for part in parts:
      blob = getattr(part, 'inline_data', None)
      if not (blob and (blob.mime_type or '').startswith('image/')):
        continue
      ext = blob.mime_type.split('/', 1)[1]
      filename = f'figure_{uuid.uuid4().hex[:8]}.{ext}'
      await tool_context.save_artifact(
          filename,
          types.Part(inline_data=types.Blob(
              mime_type=blob.mime_type, data=blob.data,
          )),
      )
      return {
          'filename': filename,
          'marker': f'<start_of_user_uploaded_file: {filename}>',
      }

  return {'error': 'Image generation returned no inline image data.'}
