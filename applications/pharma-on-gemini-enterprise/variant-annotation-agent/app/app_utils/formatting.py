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

"""Output formatting helpers for Gemini Enterprise Markdown rendering.

Prevents two common Gemini Enterprise rendering issues:
1. Consecutive sub-agent A2A text events concatenating without blank lines
   (which glues `#` / `###` headings directly onto preceding URLs or DOIs).
2. Inline dollar-sign delimiters (`$CEBPA/CEBPB$`, `$H3K27ac, H3K4me1$`,
   `$SORT1$`) triggering KaTeX math rendering around biological identifiers.
"""

from __future__ import annotations

import re
import urllib.parse

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmResponse

DEFAULT_ATLAS_LITEMS = "avi,section:RNA_SEQ,section:DNASE,section:DNASE:REF_SCORES"
_ATLAS_BASE_URL = "https://deepmind.google.com/science/alphagenome/atlas"
_ATLAS_URL_RE = re.compile(
    r"https://deepmind\.google\.com/science/alphagenome/atlas\?[A-Za-z0-9_:%.,=&>-]+"
)
_CODE_SPAN_SPLIT_RE = re.compile(r"(```[\s\S]*?```|`[^`\n]+`)")

# Matches inline `$...$` spans that are not currency amounts (e.g. "$100 and $200")
# and contain at least one ASCII letter (genes, TFs, histone marks, HGVS, formulas).
_INLINE_DOLLAR_RE = re.compile(
    r"(?<!\\)\$\$?(?![0-9]+(?:\.[0-9]+)?\b\s)([^\$\n]{1,120}?)(?<!\\)\$\$?"
)


def strip_biological_math_delimiters(text: str) -> str:
    """Replaces inline `$...$` wrappers around biological names/formulas with backticks."""
    if not text or "$" not in text:
        return text

    segments = _CODE_SPAN_SPLIT_RE.split(text)
    cleaned_segments: list[str] = []
    for idx, segment in enumerate(segments):
        # Odd indices are fenced or inline code blocks; keep verbatim.
        if idx % 2 == 1:
            cleaned_segments.append(segment)
            continue

        def _replace_math(match: re.Match[str]) -> str:
            inner = match.group(1).strip()
            if not inner or not re.search(r"[A-Za-z]", inner):
                return match.group(0)
            return f"`{inner}`"

        cleaned_segments.append(_INLINE_DOLLAR_RE.sub(_replace_math, segment))

    return "".join(cleaned_segments)


def _normalize_coord_prefix(val: str) -> str:
    """Ensures genomic coordinates start with lowercase `chr` and uppercase sex/MT chroms."""
    return re.sub(
        r"^(?:chr)?([0-9]{1,2}|X|Y|M|MT)(?=:)",
        lambda m: f"chr{m.group(1).upper()}",
        val,
        flags=re.IGNORECASE,
    )


def normalize_alphagenome_atlas_urls(text: str) -> str:
    """Rewrites legacy `?variant=` / `?locus=` or unencoded `>` Atlas links into canonical `q`/`m`/`lItems` URLs."""
    if not text or _ATLAS_BASE_URL not in text:
        return text

    def _rewrite(match: re.Match[str]) -> str:
        raw_url = match.group(0)
        trimmed = raw_url.rstrip(".,")
        trailing = raw_url[len(trimmed) :]
        _, _, qs = trimmed.partition("?")
        pairs = urllib.parse.parse_qsl(qs, keep_blank_values=True)
        if not pairs:
            return raw_url

        params = dict(pairs)

        if "q" not in params and ("variant" in params or "locus" in params):
            is_variant = "variant" in params
            target = params.pop("variant") if is_variant else params.pop("locus")
            target = _normalize_coord_prefix(urllib.parse.unquote(target).strip())
            ordered: dict[str, str] = {
                "q": target,
                "m": params.pop("m", "") or ("variant" if is_variant else "locus"),
                "lItems": params.pop("lItems", "") or DEFAULT_ATLAS_LITEMS,
            }
            ordered.update(params)
        elif "q" in params:
            q_val = _normalize_coord_prefix(
                urllib.parse.unquote(params.pop("q")).strip()
            )
            m_default = "variant" if ">" in q_val else "locus"
            ordered = {
                "q": q_val,
                "m": params.pop("m", "") or m_default,
                "lItems": params.pop("lItems", "") or DEFAULT_ATLAS_LITEMS,
            }
            ordered.update(params)
        else:
            return raw_url

        encoded_qs = urllib.parse.urlencode(
            ordered,
            quote_via=urllib.parse.quote,
            safe=":,-",
        )
        return f"{_ATLAS_BASE_URL}?{encoded_qs}{trailing}"

    return _ATLAS_URL_RE.sub(_rewrite, text)


def ensure_block_newlines(text: str) -> str:
    """Ensures a visible Markdown block begins and ends with `\\n\\n`."""
    stripped = text.strip()
    if not stripped:
        return text
    return f"\n\n{stripped}\n\n"


def format_visible_markdown(text: str) -> str:
    """Sanitizes biological `$...$` math wrappers, Atlas URLs, and pads block boundaries with `\\n\\n`."""
    return ensure_block_newlines(
        normalize_alphagenome_atlas_urls(strip_biological_math_delimiters(text))
    )


def pad_and_sanitize_llm_response(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """ADK `after_model_callback` sanitizing `$...$` tokens, Atlas URLs, and adding `\\n\\n` padding on final turns."""
    del callback_context
    if getattr(llm_response, "partial", False):
        return None
    content = getattr(llm_response, "content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if not parts:
        return None
    if any(getattr(p, "function_call", None) is not None for p in parts):
        return None

    visible_indices = [
        i
        for i, p in enumerate(parts)
        if getattr(p, "text", None) and not getattr(p, "thought", False)
    ]
    if not visible_indices:
        return None

    for idx in visible_indices:
        cleaned = normalize_alphagenome_atlas_urls(
            strip_biological_math_delimiters(parts[idx].text or "")
        )
        parts[idx].text = cleaned

    first_idx = visible_indices[0]
    last_idx = visible_indices[-1]
    if first_idx == last_idx:
        parts[first_idx].text = ensure_block_newlines(parts[first_idx].text or "")
    else:
        first_txt = (parts[first_idx].text or "").lstrip()
        last_txt = (parts[last_idx].text or "").rstrip()
        if first_txt:
            parts[first_idx].text = f"\n\n{first_txt}"
        if last_txt:
            parts[last_idx].text = f"{last_txt}\n\n"
    return None
