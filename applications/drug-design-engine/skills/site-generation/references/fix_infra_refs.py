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

"""
Scan generated HTML for infrastructure-detail leakage.

Reports findings for the web-builder to act on — does NOT modify any files.
Run after ``dde site build`` and post-build processing:

    dde site build
    python3 postbuild.py _site/
    python3 references/fix_infra_refs.py _site/

Idempotent: scan-only, no side effects.  Safe to re-run after every build.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Infrastructure-detail patterns
# ---------------------------------------------------------------------------

# Each tuple: (pattern_name, compiled regex).
# Patterns are case-insensitive to catch varied capitalisation in prose.

INFRA_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("timeout", re.compile(r"\btimeout\b", re.IGNORECASE)),
    ("cold-start", re.compile(r"\bcold[\s-]?start\b", re.IGNORECASE)),
    ("retry", re.compile(r"\bretr(?:y|ies|ied|ying)\b", re.IGNORECASE)),
    (
        "gateway-error",
        re.compile(
            r"\b(?:502|503|504)\s*(?:bad\s*gateway|service\s*unavailable|gateway\s*timeout)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "endpoint-failure",
        re.compile(
            r"\bendpoint\s+(?:failure|failed|unavailable|error)\b", re.IGNORECASE
        ),
    ),
    ("api-error", re.compile(r"\bAPI\s+error\b", re.IGNORECASE)),
    ("connection-refused", re.compile(r"\bconnection\s+refused\b", re.IGNORECASE)),
    ("rate-limit", re.compile(r"\brate[\s-]?limit(?:ed|ing|s)?\b", re.IGNORECASE)),
    ("connection-reset", re.compile(r"\bconnection\s+reset\b", re.IGNORECASE)),
    ("http-error-code", re.compile(r"\bHTTP\s+(?:4\d{2}|5\d{2})\b", re.IGNORECASE)),
    (
        "stack-trace",
        re.compile(r"\bTraceback\s+\(most\s+recent\s+call\s+last\)", re.IGNORECASE),
    ),
    (
        "orchestration",
        re.compile(
            r"\b(?:agent\s+(?:stall|restart|crash)|scion(?:tool)?\s+(?:status|expose))\b",
            re.IGNORECASE,
        ),
    ),
]

# Tags whose content is considered intentional technical content.
# Matches inside these tags are classified as low severity.
CODE_BLOCK_RE = re.compile(
    r"<(?:code|pre|samp|kbd)[^>]*>.*?</(?:code|pre|samp|kbd)>",
    re.DOTALL | re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    """A single pattern match in a file."""

    file: Path
    line_num: int
    pattern: str
    snippet: str
    in_code_block: bool

    @property
    def severity(self) -> str:
        """Low when inside a code/pre block; high when in prose."""
        return "low" if self.in_code_block else "HIGH"


@dataclass
class ScanResult:
    """Aggregated scan output."""

    findings: list[Finding] = field(default_factory=list)

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "HIGH")

    @property
    def low_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "low")


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def _code_block_ranges(text: str) -> list[tuple[int, int]]:
    """Return (start, end) character offsets for code/pre/samp/kbd blocks."""
    return [(m.start(), m.end()) for m in CODE_BLOCK_RE.finditer(text)]


def _in_code_block(pos: int, ranges: list[tuple[int, int]]) -> bool:
    """Check whether a character offset falls inside a code block."""
    for start, end in ranges:
        if start <= pos < end:
            return True
        if start > pos:
            break
    return False


def _line_number(text: str, pos: int) -> int:
    """Return the 1-based line number for a character offset."""
    return text.count("\n", 0, pos) + 1


def _snippet(text: str, pos: int, context: int = 60) -> str:
    """Extract a short text snippet around *pos* for display."""
    start = max(0, pos - context)
    end = min(len(text), pos + context)
    fragment = text[start:end].replace("\n", " ").strip()
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return f"{prefix}{fragment}{suffix}"


def scan_file(html_file: Path) -> list[Finding]:
    """Scan a single HTML file for infrastructure-detail patterns."""
    text = html_file.read_text(encoding="utf-8", errors="replace")
    code_ranges = _code_block_ranges(text)
    findings: list[Finding] = []

    for pattern_name, regex in INFRA_PATTERNS:
        for match in regex.finditer(text):
            findings.append(
                Finding(
                    file=html_file,
                    line_num=_line_number(text, match.start()),
                    pattern=pattern_name,
                    snippet=_snippet(text, match.start()),
                    in_code_block=_in_code_block(match.start(), code_ranges),
                )
            )

    return findings


def scan(site_dir: Path) -> ScanResult:
    """Scan all HTML files under *site_dir*."""
    result = ScanResult()
    for html_file in sorted(site_dir.rglob("*.html")):
        result.findings.extend(scan_file(html_file))
    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def report(result: ScanResult) -> None:
    """Print human-readable findings grouped by file."""
    if not result.findings:
        print("scan: no infrastructure references found")
        return

    current_file: Path | None = None
    for f in result.findings:
        if f.file != current_file:
            current_file = f.file
            print(f"\n--- {f.file} ---")
        print(f"  [{f.severity}] line {f.line_num}: {f.pattern}")
        print(f"         {f.snippet}")

    print(
        f"\nscan: {result.high_count} HIGH, {result.low_count} low "
        f"across {len(result.findings)} match(es)"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python3 fix_infra_refs.py <site-dir>")
        sys.exit(2)

    site_dir = Path(sys.argv[1])
    if not site_dir.is_dir():
        print(f"error: {site_dir} is not a directory")
        sys.exit(2)

    result = scan(site_dir)
    report(result)

    if result.high_count:
        sys.exit(1)


if __name__ == "__main__":
    main()
