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

"""Regression tests for repository paths used by fresh OASE bootstrap."""

from pathlib import Path

OASE_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAPPER = OASE_ROOT / "templates" / "bootstrapper" / "agents.md"
RUNTIME_INSTRUCTIONS = sorted((OASE_ROOT / "templates").glob("*/agents.md")) + sorted(
    (OASE_ROOT / "skills").glob("*/SKILL.md")
)


def test_bootstrapper_clones_public_repository_and_links_current_tools() -> None:
    """A blank Hub workspace must bootstrap without private GitHub credentials."""
    content = BOOTSTRAPPER.read_text(encoding="utf-8")

    assert (
        "git clone https://github.com/GoogleCloudPlatform/LifeSciences.git "
        "/scion-volumes/scratchpad/LifeSciences"
    ) in content
    assert (
        "ln -s /scion-volumes/scratchpad/LifeSciences/"
        "applications/open-agentic-science-engine/tools /workspace/tools"
    ) in content
    assert "gh auth status" not in content
    assert "GITHUB_TOKEN" not in content
    assert "gh repo clone" not in content
    assert "clone requires authentication" not in content


def test_runtime_instructions_do_not_reference_legacy_oase_paths() -> None:
    """Imported templates and skills must use the current repository layout."""
    stale_references = ("scion-frontiers/LifeSciences", "applications/DDE")
    violations: list[str] = []

    for path in RUNTIME_INSTRUCTIONS:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for stale_reference in stale_references:
                if stale_reference in line:
                    relative_path = path.relative_to(OASE_ROOT)
                    violations.append(
                        f"{relative_path}:{line_number}: {stale_reference}"
                    )

    assert not violations, "Legacy OASE bootstrap paths remain:\n" + "\n".join(
        violations
    )
