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

"""Architecture contract for the Hypex-to-DDE port."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

DDE_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = DDE_ROOT / "templates"
SKILLS = DDE_ROOT / "skills"

ROLE_SKILLS = {
    "hypex-supervisor": {"run-protocol", "hypothesis-run-corpus"},
    "hypex-generation": {"literature-search", "hypothesis-schema", "debate-protocol"},
    "hypex-reflection": {"citation-verification", "review-rubric", "safety-screen"},
    "hypex-proximity": {"proximity-protocol"},
    "hypex-tournament": {"elo-tournament", "debate-protocol"},
    "hypex-evolution": {
        "literature-search",
        "evolution-operators",
        "hypothesis-schema",
    },
    "hypex-meta-review": {"citation-verification", "meta-review-protocol"},
}


def _granted_skill_names(role: str) -> set[str]:
    config = yaml.safe_load((TEMPLATES / role / "scion-agent.yaml").read_text())
    return {entry["uri"].rstrip("/").rsplit("/", 1)[-1] for entry in config["skills"]}


def test_complete_reference_role_set_is_ported() -> None:
    for role, required_skills in ROLE_SKILLS.items():
        role_dir = TEMPLATES / role
        assert (role_dir / "agents.md").is_file()
        assert (role_dir / "system-prompt.md").is_file()
        assert required_skills <= _granted_skill_names(role)
        assert "hypex-tool-setup" in _granted_skill_names(role)


def test_all_hypex_skill_grants_resolve_to_dde_skills() -> None:
    for role in ROLE_SKILLS:
        for skill in _granted_skill_names(role):
            assert (SKILLS / skill / "SKILL.md").is_file(), f"{role}: missing {skill}"


def test_ported_instructions_do_not_invoke_standalone_lit_cli() -> None:
    standalone_lit = re.compile(r"(?m)^\s*lit\s+")
    files = [TEMPLATES / role / "agents.md" for role in ROLE_SKILLS]
    files.extend(
        path
        for path in SKILLS.glob("*/SKILL.md")
        if path.parent.name
        in {
            "debate-protocol",
            "evolution-operators",
            "hypothesis-schema",
            "meta-review-protocol",
            "review-rubric",
            "safety-screen",
        }
    )
    for path in files:
        assert not standalone_lit.search(path.read_text()), str(path)


def test_supervisor_declares_termination_before_dde_ingest() -> None:
    instructions = (TEMPLATES / "hypex-supervisor" / "agents.md").read_text()
    termination = instructions.index(
        "Write `meta/termination.json` with the actual reason"
    )
    ingest = instructions.index("Run `dde hypex ingest")
    assert termination < ingest


def test_datastore_workers_do_not_rely_on_default_run_directory() -> None:
    for role in (
        "hypex-generation",
        "hypex-reflection",
        "hypex-tournament",
        "hypex-evolution",
    ):
        instructions = (TEMPLATES / role / "agents.md").read_text()
        assert "--run-dir <run-base>" in instructions, role
        assert "executable's default path" in instructions, role


def test_retired_single_epoch_pilot_is_not_part_of_the_port() -> None:
    corpus = (SKILLS / "hypothesis-run-corpus" / "SKILL.md").read_text()
    protocol = (SKILLS / "run-protocol" / "SKILL.md").read_text()
    assert "Phase B1" not in corpus
    assert "converged (budget exhaustion)" not in protocol
    assert "reason: budget_exhausted" in protocol


def test_dde_workorder_boundary_dispatches_only_the_supervisor() -> None:
    controller = (
        TEMPLATES / "research-operations-controller" / "agents.md"
    ).read_text()
    lead = (TEMPLATES / "science-program-lead" / "agents.md").read_text()

    for instructions in (controller, lead):
        assert "requested_role: hypex-supervisor" in instructions
        assert "resource_class: hypex-supervisor" in instructions
        assert "tournament-orchestration" in instructions

    assert "| `hypex-supervisor` | Complete Hypex exploration subgraph" in controller
    assert "| `hypex-tournament` | Hypothesis-explorer tournament" not in controller
