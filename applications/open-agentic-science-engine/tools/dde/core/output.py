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

"""Stdout discipline.

Tools write outputs and share paths. They do not stream content into
context. See docs/tool-design-guidance.md §6.

Budget:
  fetch / run  -> the output paths, one per line, nothing else
  analyze      -> a bounded human summary, hard-capped
  anything more-> to file, with the path printed

The cap is enforced here, in shared code, so no subcommand can exceed it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

# Hard caps for an `analyze` summary (§6: target 20 lines / ~2 KB).
MAX_LINES = 20
MAX_BYTES = 2048
MAX_LINE_CHARS = 150


def clip(text: str, limit: int = MAX_LINE_CHARS) -> str:
    """Shorten a line at a word boundary, marking that it was shortened.

    Cutting mid-word produces text that reads as if it were complete
    ("...is a developer fallback and do"), which is the failure mode this
    exists to prevent: a caveat that looks finished but isn't. The
    ellipsis is the signal to go read the artifact.
    """
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    space = head.rfind(" ")
    if space > limit * 0.6:
        head = head[:space]
    return head.rstrip(" ,;:.") + "…"


class Emitter:
    """Per-invocation output channel honouring --json / --quiet."""

    def __init__(self, as_json: bool = False, quiet: bool = False):
        self.as_json = as_json
        self.quiet = quiet
        self._paths: list[str] = []
        self._summary: list[str] = []
        self._payload: dict[str, Any] = {}

    # -- paths -------------------------------------------------------------

    def path(self, path: Path | str, role: str | None = None) -> None:
        """Record a produced artifact path.

        The path is the primary output and is printed in a stable
        position so the next invocation can chain without the agent
        reconstructing it.
        """
        text = str(path)
        self._paths.append(text)
        if role:
            self._payload.setdefault("outputs", {})[role] = text

    # -- bounded summary ---------------------------------------------------

    def line(self, text: str = "") -> None:
        """Add one line to the bounded human summary.

        Clipping happens here rather than at each call site so no
        subcommand can smuggle a wall of text out through one long line.
        """
        self._summary.append(clip(text) if text else text)

    def data(self, key: str, value: Any) -> None:
        """Add a field to the --json payload (never counted against the cap)."""
        self._payload[key] = value

    # -- rendering ---------------------------------------------------------

    def _capped(self) -> tuple[list[str], bool]:
        lines = self._summary
        truncated = False
        if len(lines) > MAX_LINES:
            lines = lines[:MAX_LINES]
            truncated = True
        out: list[str] = []
        total = 0
        for line in lines:
            encoded = len(line.encode("utf-8")) + 1
            if total + encoded > MAX_BYTES:
                truncated = True
                break
            out.append(line)
            total += encoded
        return out, truncated

    def flush(self) -> None:
        if self.as_json:
            payload = dict(self._payload)
            if self._paths:
                payload.setdefault("paths", self._paths)
            click.echo(json.dumps(payload, indent=2))
            return

        if self.quiet:
            for path in self._paths:
                click.echo(path)
            return

        lines, truncated = self._capped()
        for line in lines:
            click.echo(line)
        if truncated:
            click.echo(
                "… summary truncated to the stdout budget; read the analysis "
                "artifact for the full record."
            )
        for path in self._paths:
            click.echo(path)


def json_or_quiet_options(func):
    """Attach the two output-mode flags every subcommand carries."""
    func = click.option(
        "--json", "as_json", is_flag=True, help="Emit a machine-readable JSON record."
    )(func)
    func = click.option("--quiet", is_flag=True, help="Emit output paths only.")(func)
    return func


def warn(message: str) -> None:
    """Operator-facing warning. Goes to stderr, never counted in the budget."""
    click.echo(f"warning: {message}", err=True)


def fail(message: str, code: int = 1) -> None:
    click.echo(f"error: {message}", err=True)
    sys.exit(code)
