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

"""Unit tests for target screening prompts and skill guardrails."""

import re

from app import prompts
from app.app_utils.skills_loader import get_local_skill


def _normalize(text: str) -> str:
    """Normalize whitespace in text for robust substring matching."""
    return re.sub(r"\s+", " ", text)


def test_target_screening_skill_contains_independence_check() -> None:
    """Verify target-screening skill has mandatory independence check, funnel steps, and guardrails."""
    skill = get_local_skill("target-screening")
    assert skill is not None
    content = _normalize(skill.instructions)

    # Funnel step checks
    assert "First-pass filter" in content
    assert "8–12 names" in content
    assert (
        "Verify corporate independence & M&A status (CRITICAL - BATCH PARALLEL)"
        in content
    )
    assert "in parallel within a single turn" in content
    assert "Immediately discard" in content
    assert "acquired/merged/delisted" in content

    # Guardrails
    assert "Target Acquirability Rule" in content
    assert "No Assumed/Fabricated Filings" in content
    assert "Never recommend companies that have already been acquired" in content


def test_diligence_playbook_red_flags_contain_acquired_status() -> None:
    """Verify diligence-playbook red-flag checklist includes acquired status."""
    skill = get_local_skill("diligence-playbook")
    assert skill is not None
    content = _normalize(skill.instructions)

    assert (
        "Target already acquired, merged, delisted, or subject to a definitive buyout agreement"
        in content
    )


def test_shared_evidence_rules_contain_independence_and_anti_hallucination() -> None:
    """Verify SHARED_EVIDENCE_RULES contain independence and anti-hallucination rules."""
    rules = _normalize(prompts.SHARED_EVIDENCE_RULES)

    assert "Corporate Independence Verification:" in rules
    assert "Never recommend companies that have already been acquired" in rules
    assert (
        "Never extrapolate, assume, or fabricate quarterly/annual filing periods"
        in rules
    )


def test_financial_instruction_delisting_and_merger_check() -> None:
    """Verify FINANCIAL_INSTRUCTION guides checking Form 15/25 and 8-K/6-K when filings cease."""
    instruction = _normalize(prompts.FINANCIAL_INSTRUCTION)

    assert "check edgar_recent_filings for Form 15/25" in instruction
    assert "delisting/deregistration" in instruction
    assert "merger completion/acquisition" in instruction


def test_coordinator_instruction_target_screening_independence() -> None:
    """Verify COORDINATOR_INSTRUCTION mandates parallel batch independence verification in target screening."""
    instruction = _normalize(prompts.COORDINATOR_INSTRUCTION)

    assert "8–12 candidate survivors" in instruction
    assert "edgar_full_text_search via financial_analyst" in instruction
    assert "MANDATORY BATCH PARALLEL INDEPENDENCE VERIFICATION" in instruction
    assert "CONCURRENTLY IN PARALLEL in a SINGLE response turn" in instruction
    assert (
        "DISCARD ACQUIRED ENTITIES IMMEDIATELY from the parallel results" in instruction
    )
    assert "Never fabricate unverified quarterly filing periods" in instruction


def test_corporate_independence_rule_constant() -> None:
    """Verify CORPORATE_INDEPENDENCE_RULE and MNA_VERIFICATION_QUERY_TEMPLATE are exported and integrated."""
    assert hasattr(prompts, "CORPORATE_INDEPENDENCE_RULE")
    assert hasattr(prompts, "MNA_VERIFICATION_QUERY_TEMPLATE")

    rule = _normalize(prompts.CORPORATE_INDEPENDENCE_RULE)
    assert "Corporate Independence Verification:" in rule
    assert "Never recommend companies that have already been acquired" in rule
    assert "Every candidate company MUST be explicitly checked" in rule

    # Verify rule is included in SHARED_EVIDENCE_RULES
    shared_rules = _normalize(prompts.SHARED_EVIDENCE_RULES)
    assert rule in shared_rules

    # Verify query template structure and integration in coordinator & search agent instructions
    template = prompts.MNA_VERIFICATION_QUERY_TEMPLATE
    assert "<Company>" in template
    assert "acquired" in template
    assert template in prompts.COORDINATOR_INSTRUCTION
    assert template in prompts.SEARCH_AGENT_INSTRUCTION


def test_search_agent_instruction_corporate_status() -> None:
    """Verify SEARCH_AGENT_INSTRUCTION mandates checking acquisitions, buyouts, and active status."""
    instruction = _normalize(prompts.SEARCH_AGENT_INSTRUCTION)

    assert "Mandatory Corporate Independence & M&A Verification:" in instruction
    assert "ALWAYS check whether the company has been acquired" in instruction
    assert "CORPORATE STATUS: ACQUIRED / INACTIVE / SUBSIDIARY" in instruction
    assert "CORPORATE STATUS: ACTIVE / INDEPENDENT" in instruction
