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

"""Render markdown whitepapers to investment-grade styled PDF bytes.

Pure-Python pipeline (markdown -> HTML -> xhtml2pdf) so it runs unmodified on
Agent Runtime — no native rendering dependencies.
"""

import io
import re
from datetime import date

import markdown
from xhtml2pdf import pisa

from .assets import resolve_tokens_to_paths

# The LLM sometimes emits LaTeX/MathJax, which xhtml2pdf cannot render. Convert
# the common tokens to plain Unicode/text so figures read correctly in the PDF.
# Applied after braces/fracs are expanded; \$ and \% are handled last so they
# don't interfere with math-delimiter stripping.
_LATEX_TOKENS = {
    r"\ge": "≥",
    r"\geq": "≥",
    r"\le": "≤",
    r"\leq": "≤",
    r"\times": "×",
    r"\pm": "±",
    r"\approx": "≈",
    r"\neq": "≠",
    r"\sim": "~",
    r"\rightarrow": "→",
    r"\to": "→",
    r"\Rightarrow": "⇒",
    r"\alpha": "α",
    r"\beta": "β",
    r"\gamma": "γ",
    r"\delta": "δ",
    r"\mu": "µ",
    r"\sigma": "σ",
    r"\Delta": "Δ",
    r"\,": " ",
    r"\;": " ",
    r"\!": "",
    r"\left": "",
    r"\right": "",
    r"\cdot": "·",
}


def _sanitize_latex(text: str) -> str:
    """Convert LaTeX math fragments the model may emit into plain text/Unicode."""
    # 0. Protect escaped literals so they survive math-delimiter stripping.
    _DOLLAR = "\x00D\x00"
    text = text.replace(r"\$", _DOLLAR).replace(r"\%", "%").replace(r"\&", "&")
    # 1. Strip math delimiters FIRST, while the LaTeX indicators (\, ^, _, {)
    #    that mark a span as math are still present. $$...$$ blocks are always
    #    math; inline $...$ is only math when its content has an indicator —
    #    otherwise it is currency in prose and the $ signs must be preserved
    #    (e.g. "$56M and $22M").
    text = re.sub(r"\$\$(.+?)\$\$", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"\$([^$\n]*[\\^_{][^$\n]*)\$", lambda m: m.group(1), text)
    # 2. Expand text-wrapping commands (so their braces don't defeat \frac).
    for _ in range(4):
        new = text
        for cmd in ("text", "mathbf", "mathrm", "textbf", "mathit", "textit"):
            new = re.sub(r"\\" + cmd + r"\s*\{([^{}]*)\}", r"\1", new)
        if new == text:
            break
        text = new
    # 3. \frac{a}{b} -> (a / b); loop for nested fractions.
    for _ in range(4):
        new = re.sub(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"(\1 / \2)", text)
        if new == text:
            break
        text = new
    # 4. Symbol tokens.
    for tok, rep in _LATEX_TOKENS.items():
        text = text.replace(tok, rep)
    # 5. Superscripts/subscripts like x^{2} or x_{i} -> x2 / xi.
    text = re.sub(r"[\^_]\{([^{}]*)\}", r"\1", text)
    # 6. Restore protected dollar signs.
    text = text.replace(_DOLLAR, "$")
    return text


def _dedupe_stutters(text: str) -> str:
    """Collapse immediately repeated acronyms ("SEC SEC Filings" -> "SEC
    Filings"), a stutter the model occasionally produces in source lists."""
    return re.sub(r"\b([A-Z]{2,6}) (?=\1\b)", "", text)


_BACKREF_RE = re.compile(r'\s*<a class="footnote-backref".*?</a>', re.DOTALL)
_FN_BLOCK_RE = re.compile(r'<div class="footnote">.*?</div>', re.DOTALL)
_FN_LI_RE = re.compile(r'<li id="fn:[^"]*">(.*?)</li>', re.DOTALL)


def _format_footnotes(html: str) -> str:
    """Rewrite Python-Markdown's footnote block into explicitly numbered lines.

    xhtml2pdf does not render <ol> markers reliably when list items wrap block
    <p>, and it draws the ↩ backlink glyph as a tofu box. We replace the block
    with `N. <text>` paragraphs numbered in first-reference order, which matches
    the inline <sup> numbers the extension already emitted.
    """
    m = _FN_BLOCK_RE.search(html)
    if not m:
        return html
    items = _FN_LI_RE.findall(m.group(0))
    if not items:
        return html
    out = ['<div class="footnote"><hr />']
    for i, item in enumerate(items, 1):
        text = _BACKREF_RE.sub("", item).strip()
        text = re.sub(r"^<p>(.*)</p>$", r"\1", text, flags=re.DOTALL).strip()
        out.append(f'<p class="footnote-item">{i}. {text}</p>')
    out.append("</div>")
    return html[: m.start()] + "\n".join(out) + html[m.end() :]


_CSS = """
@page {
    size: a4 portrait;
    margin: 2.2cm 1.9cm 2.4cm 1.9cm;
    @frame footer_frame {
        -pdf-frame-content: footer;
        bottom: 0.8cm; left: 1.9cm; width: 17.2cm; height: 1cm;
    }
}
body { font-family: Helvetica, Arial, sans-serif; font-size: 9.5pt;
       color: #1a1f2b; line-height: 1.45; }
h1 { font-size: 20pt; color: #0b2545; margin: 0 0 4pt 0; }
h2 { font-size: 13pt; color: #0b2545; border-bottom: 1.2pt solid #0b2545;
     padding-bottom: 3pt; margin-top: 18pt; }
h3 { font-size: 11pt; color: #13315c; margin-top: 12pt; }
h4 { font-size: 10pt; color: #13315c; }
p { margin: 5pt 0; }
li { margin: 2.5pt 0; }
table { width: 100%; border-collapse: collapse; margin: 8pt 0; font-size: 8.5pt; }
th { background-color: #0b2545; color: #ffffff; padding: 4.5pt 5pt;
     text-align: left; }
td { border-bottom: 0.6pt solid #ccd3de; padding: 4pt 5pt;
     vertical-align: top; }
blockquote { border-left: 2.5pt solid #f4a261; margin: 8pt 0;
             padding: 4pt 10pt; background-color: #fdf6ee; color: #5a4a33; }
code { font-family: Courier; font-size: 8.5pt; }
hr { border: 0.6pt solid #ccd3de; }
img { width: 15.5cm; }
.figure-caption { color: #5b6472; font-size: 8pt; font-style: italic;
                  margin: 0 0 10pt 0; }
.cover-meta { color: #5b6472; font-size: 10pt; margin-bottom: 2pt; }
.confidential { color: #a4243b; font-size: 8pt; letter-spacing: 1.5pt; }
#footer { color: #8a93a1; font-size: 7.5pt; border-top: 0.6pt solid #ccd3de;
          padding-top: 3pt; }
sup { font-size: 6.5pt; color: #13315c; }
.footnote { font-size: 8pt; color: #3c4655; margin-top: 16pt; }
.footnote hr { border: 0.8pt solid #0b2545; margin-bottom: 4pt; }
.footnote-item { margin: 2pt 0; font-size: 8pt; color: #3c4655; }
"""


def render_whitepaper_pdf(markdown_text: str, title: str, subtitle: str = "") -> bytes:
    """Convert a markdown whitepaper into styled PDF bytes.

    Args:
        markdown_text: Full whitepaper body in GitHub-style markdown
            (headings, tables, lists, blockquotes supported).
        title: Document title placed on the header block.
        subtitle: Optional subtitle (e.g. "Acquisition Assessment: <target>").

    Returns:
        PDF file contents as bytes. Raises RuntimeError on render failure.
    """
    prepared = resolve_tokens_to_paths(_dedupe_stutters(_sanitize_latex(markdown_text)))
    body_html = _format_footnotes(
        markdown.markdown(
            prepared,
            extensions=[
                "tables",
                "sane_lists",
                "smarty",
                "toc",
                "fenced_code",
                "footnotes",
            ],
        )
    )
    today = date.today().strftime("%d %B %Y")
    html = f"""<html><head><style>{_CSS}</style></head><body>
    <p class="confidential">CONFIDENTIAL &mdash; PREPARED BY ARGUS DILIGENCE AGENT</p>
    <h1>{title}</h1>
    <p class="cover-meta">{subtitle}</p>
    <p class="cover-meta">{today}</p>
    <hr/>
    {body_html}
    <div id="footer">Argus &mdash; Life Sciences M&amp;A Diligence &nbsp;|&nbsp;
    {title} &nbsp;|&nbsp; Generated {today}. AI-generated analysis: verify all
    figures against primary sources before making investment decisions.</div>
    </body></html>"""

    buf = io.BytesIO()
    result = pisa.CreatePDF(io.StringIO(html), dest=buf, encoding="utf-8")
    if result.err:
        raise RuntimeError(f"PDF rendering failed with {result.err} error(s)")
    return buf.getvalue()
