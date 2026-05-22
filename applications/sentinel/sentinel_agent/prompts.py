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

"""Prompt loader for the Sentinel sub-agents.

Prompts live as markdown files under ``prompts/`` so they can be edited
without touching Python source.
"""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def load(name: str) -> str:
    """Load a prompt markdown file by stem (without the ``.md`` extension)."""
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8").strip()


ROOT = load("root")
INTAKE = load("intake")

# Reviewer panel (parallel)
MEDICAL_REVIEWER = load("medical_reviewer")
LEGAL_REVIEWER = load("legal_reviewer")
REGULATORY_REVIEWER = load("regulatory_reviewer")
EDITORIAL_REVIEWER = load("editorial_reviewer")
SUBMITTER_ADVOCATE = load("submitter_advocate")
RULES_REVIEWER = load("rules_reviewer")

# Critic panel (parallel) + merger + loop decider
DEDUPE_CRITIC = load("dedupe_critic")
SEVERITY_CRITIC = load("severity_critic")
GAP_CRITIC = load("gap_critic")
CRITIC_MERGER = load("critic_merger")
LOOP_DECIDER = load("loop_decider")

SYNTHESIZER = load("synthesizer")
