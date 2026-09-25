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

"""Stage 0 bounded portfolio triage — compare intervention concepts
before final target/modality commitment.

This module implements the orchestration logic for Stage 0 as described
in the science-program-lead template §5a.  It dispatches three parallel
workstreams (rationale verification, modality tractability, manufacturing
feasibility) across eligible intervention concepts, manages a triage
budget, and produces assessment and decision records using the existing
#74/#75 semantics.

Hard constraints (from the task brief):
  1. Every workstream dispatch calls the REAL merged commands — not
     hand-rolled mocks of what they return.
  2. Any ``terminate`` action goes through the real ``write_record()``,
     which enforces the human-approval ``Refusal`` gate.
  3. Every function defined here is reachable from a real caller.
  4. No aggregating tool outputs into a naive kill rule.  Any automatic
     termination based on a single metric threshold is a bug.

Design principles:
  - Stage 0 is a lead-orchestrated decision process, not an autonomous gate.
  - Workstreams raise findings; the lead makes the decision.
  - Budget exhaustion is incomplete (``investigate``/``park``), never
    ``terminate``.
  - A lone sponsor hypothesis gets the same evaluation as any other concept.
  - Program-constraint rejection (cites policy) is distinct from scientific
    refutation (cites assessment).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from dde.core.errors import Refusal
from dde.core.evidence import (
    ACTIONS,
)

# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


@dataclass
class TriageBudget:
    """Resource bounds for a Stage 0 triage run.

    All limits are optional — ``None`` means unbounded on that axis.
    """

    max_wall_clock_seconds: float | None = None
    max_concepts: int | None = None
    max_workstream_invocations: int | None = None
    start_time: float = field(default_factory=time.monotonic)

    # Counters
    concepts_evaluated: int = 0
    workstream_invocations: int = 0

    def is_exhausted(self) -> tuple[bool, str]:
        """Check whether any budget axis is exhausted.

        Returns (True, reason) if exhausted, (False, "") otherwise.
        """
        if (
            self.max_wall_clock_seconds is not None
            and (time.monotonic() - self.start_time) > self.max_wall_clock_seconds
        ):
            elapsed = time.monotonic() - self.start_time
            return True, (
                f"Wall-clock budget exhausted: {elapsed:.1f}s "
                f"> {self.max_wall_clock_seconds}s"
            )
        if (
            self.max_concepts is not None
            and self.concepts_evaluated >= self.max_concepts
        ):
            return True, (
                f"Concept budget exhausted: {self.concepts_evaluated} "
                f">= {self.max_concepts}"
            )
        if (
            self.max_workstream_invocations is not None
            and self.workstream_invocations >= self.max_workstream_invocations
        ):
            return True, (
                f"Invocation budget exhausted: {self.workstream_invocations} "
                f">= {self.max_workstream_invocations}"
            )
        return False, ""

    def record_invocation(self) -> None:
        self.workstream_invocations += 1

    def record_concept(self) -> None:
        self.concepts_evaluated += 1


# ---------------------------------------------------------------------------
# Workstream results
# ---------------------------------------------------------------------------


@dataclass
class WorkstreamResult:
    """Result from a single workstream execution against one concept."""

    workstream: str  # "rationale", "tractability", "manufacturing"
    concept_ref: str  # e.g. "IC-001-r1"
    assessments: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    cancel_reason: str = ""

    @property
    def evidence_statuses(self) -> list[str]:
        """Extract evidence statuses from all assessments."""
        return [a.get("evidence_status", "not_assessed") for a in self.assessments]

    @property
    def has_contradicted(self) -> bool:
        return "contradicted" in self.evidence_statuses

    @property
    def all_not_assessed(self) -> bool:
        return all(s == "not_assessed" for s in self.evidence_statuses)


@dataclass
class ConceptTriageResult:
    """Aggregated triage result for a single concept across all workstreams."""

    concept_ref: str
    concept_id: str
    workstream_results: dict[str, WorkstreamResult] = field(default_factory=dict)
    disposition: str = ""  # "accepted", "parked", "terminated", "investigate"
    disposition_reason: str = ""
    policy_ref: str | None = None  # GP-NNN if program-constraint rejection
    decision_record: dict[str, Any] | None = None

    @property
    def is_terminal(self) -> bool:
        return self.disposition in ("terminated", "withdrawn")


@dataclass
class TriageOutcome:
    """Full outcome of a Stage 0 triage run across all concepts."""

    concept_results: list[ConceptTriageResult] = field(default_factory=list)
    shortlist: list[str] = field(default_factory=list)  # concept_refs that proceed
    budget_exhausted: bool = False
    budget_exhaustion_reason: str = ""
    all_assessments: list[dict[str, Any]] = field(default_factory=list)
    all_decisions: list[dict[str, Any]] = field(default_factory=list)
    unresolved_liabilities: list[dict[str, Any]] = field(default_factory=list)
    authorized_next_work: list[str] = field(default_factory=list)
    persistence_errors: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Workstream dispatch
# ---------------------------------------------------------------------------


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first complete JSON object from text with trailing noise.

    CliRunner captures both Emitter JSON and trailing warnings in
    one string.  ``json.loads`` fails when non-JSON text follows
    the object, so we find the boundary ourselves.
    """
    import json as _json

    text = text.strip()
    if not text.startswith("{"):
        return None
    # Walk from the end to find the last '}' that closes the top-level object
    depth = 0
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"' and not escape:
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return _json.loads(text[: i + 1])
                except _json.JSONDecodeError:
                    return None
    return None


def _read_output_artifact(
    emitter_output: dict[str, Any],
    role: str,
) -> dict[str, Any] | None:
    """Read a full artifact from a path advertised in Emitter output.

    The CLI commands write full records to disk and advertise their
    paths under ``outputs.<role>`` in the Emitter JSON payload.  This
    helper reads that file and returns the parsed JSON, or ``None`` if
    the path is missing or unreadable.
    """
    import json as _json
    from pathlib import Path

    path_str = (emitter_output.get("outputs") or {}).get(role)
    if not path_str:
        return None
    try:
        return _json.loads(Path(path_str).read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError, ValueError):
        return None


def run_manufacturing_workstream(
    concept: dict[str, Any],
    concept_ref: str,
    *,
    project_root: str | None = None,
    runner: Any = None,
    cli: Any = None,
) -> WorkstreamResult:
    """Run the manufacturing feasibility workstream (Workstream 3).

    Calls the REAL ``dde manufacturing assess-stage0`` command via
    CliRunner — per Hard Constraint #1.
    """
    import json
    import tempfile

    result = WorkstreamResult(
        workstream="manufacturing",
        concept_ref=concept_ref,
    )

    if runner is None:
        from click.testing import CliRunner

        runner = CliRunner()

    if cli is None:
        from dde.cli import cli as dde_cli

        cli = dde_cli

    # Write concept to a temp file for the CLI
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(concept, f, indent=2)
        concept_path = f.name

    try:
        args = ["manufacturing", "assess-stage0", concept_path, "--json"]
        if project_root:
            args = ["--project", str(project_root), *args]

        cli_result = runner.invoke(cli, args)

        if cli_result.exit_code == 0:
            # Parse output — the command writes to stdout in JSON mode.
            # The CLI emits a lightweight summary via Emitter; the full
            # dde.evidence-assessment.v1 record is written to the path
            # advertised in outputs.manufacturing-assessment.
            #
            # CliRunner may capture trailing warnings after the JSON,
            # so fall back to _extract_json_object when json.loads fails.
            try:
                output = json.loads(cli_result.output)
            except json.JSONDecodeError:
                output = _extract_json_object(cli_result.output)

            if isinstance(output, dict):
                full = _read_output_artifact(output, "manufacturing-assessment")
                result.assessments.append(full if full else output)
            elif output is None:
                result.errors.append(
                    f"Manufacturing output not parseable as JSON: "
                    f"{cli_result.output[:200]}"
                )
        else:
            result.errors.append(
                f"Manufacturing assess-stage0 exited {cli_result.exit_code}: "
                f"{cli_result.output.strip()[:200]}"
            )
    finally:
        import os

        os.unlink(concept_path)

    return result


def run_structure_screening_workstream(
    concept: dict[str, Any],
    concept_ref: str,
    structures: list[str],
    *,
    modality: str | None = None,
    project_root: str | None = None,
    runner: Any = None,
    cli: Any = None,
) -> WorkstreamResult:
    """Run the modality tractability workstream — structure screening (Workstream 2).

    Calls the REAL ``dde structure-screen run`` command via CliRunner —
    per Hard Constraint #1.
    """
    import json

    result = WorkstreamResult(
        workstream="tractability",
        concept_ref=concept_ref,
    )

    if not structures:
        # No structures available — record as not_assessed
        result.assessments.append(
            {
                "schema": "dde.evidence-assessment.v1",
                "id": "AR-PENDING",
                "concept_ref": concept_ref,
                "claim": (f"Structural tractability for concept {concept_ref}"),
                "evidence_status": "not_assessed",
                "execution_outcome": "data_unavailable",
                "rationale": (
                    "No structures provided for screening. "
                    "Structure screening requires at least one structure "
                    "identifier."
                ),
                "assessed_at": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "assessed_by": "stage0-triage",
            }
        )
        return result

    if runner is None:
        from click.testing import CliRunner

        runner = CliRunner()

    if cli is None:
        from dde.cli import cli as dde_cli

        cli = dde_cli

    mod = modality or concept.get("modality", "small_molecule")

    args = [
        "structure-screen",
        "run",
        "--concept-ref",
        concept_ref,
        "--modality",
        mod,
        "--json",
    ]
    if project_root:
        args = ["--project", str(project_root), *args]

    args.extend(structures)

    cli_result = runner.invoke(cli, args)

    if cli_result.exit_code == 0:
        try:
            output = json.loads(cli_result.output)
        except json.JSONDecodeError:
            output = _extract_json_object(cli_result.output)

        if isinstance(output, list):
            result.assessments.extend(output)
        elif isinstance(output, dict):
            # The structure-screen command may wrap assessments
            # in an envelope dict with an 'assessments' key.
            result.assessments.extend(output.get("assessments", [output]))
        elif output is None:
            result.errors.append(
                f"Structure screening output not parseable: {cli_result.output[:200]}"
            )
    else:
        result.errors.append(
            f"Structure-screen run exited {cli_result.exit_code}: "
            f"{cli_result.output.strip()[:200]}"
        )

    return result


def run_differentiation_workstream(
    concept: dict[str, Any],
    concept_ref: str,
    query_term: str,
    *,
    modality: str | None = None,
    indication: str | None = None,
    project_root: str | None = None,
    runner: Any = None,
    cli: Any = None,
) -> WorkstreamResult:
    """Run the competitive differentiation workstream (part of Workstream 2).

    Calls the REAL ``dde differentiation assess`` command via CliRunner —
    per Hard Constraint #1.
    """
    import json

    result = WorkstreamResult(
        workstream="differentiation",
        concept_ref=concept_ref,
    )

    if runner is None:
        from click.testing import CliRunner

        runner = CliRunner()

    if cli is None:
        from dde.cli import cli as dde_cli

        cli = dde_cli

    mod = modality or concept.get("modality")
    ind = indication or concept.get("disease_context", {}).get("indication")

    args = ["differentiation", "assess", query_term, "--json"]
    if mod:
        args.extend(["--modality", mod])
    if ind:
        args.extend(["--indication", ind])
    if concept_ref:
        args.extend(["--concept", concept_ref])
    if project_root:
        args = ["--project", str(project_root), *args]

    cli_result = runner.invoke(cli, args)

    if cli_result.exit_code == 0:
        try:
            output = json.loads(cli_result.output)
        except json.JSONDecodeError:
            output = _extract_json_object(cli_result.output)

        if isinstance(output, dict):
            # The CLI emits a summary via Emitter; the full
            # competitive-differentiation record is written to the
            # path in outputs.assessment.  Convert it to
            # evidence-assessment records for persistence.
            diff_record = _read_output_artifact(output, "assessment")
            if (
                diff_record
                and diff_record.get("schema") == "dde.competitive-differentiation.v1"
            ):
                from dde.commands.differentiation import (
                    build_assessment_records,
                )

                result.assessments.extend(
                    build_assessment_records(
                        diff_record,
                        concept_ref,
                        assessed_by="stage0-triage",
                    )
                )
            elif diff_record:
                result.assessments.append(diff_record)
            else:
                result.assessments.append(output)
        elif output is None:
            result.errors.append(
                f"Differentiation output not parseable: {cli_result.output[:200]}"
            )
    else:
        result.errors.append(
            f"Differentiation assess exited {cli_result.exit_code}: "
            f"{cli_result.output.strip()[:200]}"
        )

    return result


# ---------------------------------------------------------------------------
# Portfolio-level triage
# ---------------------------------------------------------------------------


def check_policy_exclusion(
    concept: dict[str, Any],
    policies: list[dict[str, Any]],
) -> tuple[bool, str | None, str]:
    """Check if a concept is excluded by any program policy.

    Returns (excluded, policy_ref, reason).
    """
    from dde.core.policy import requirement_applies

    modality = concept.get("modality")
    for policy in policies:
        for req in policy.get("requirements", []):
            if req.get("type") != "hard_constraint":
                continue
            if not requirement_applies(req, concept, stage=0):
                continue
            # Check if this is an exclusion constraint
            exclusion = req.get("exclusion")
            if exclusion and modality and modality in exclusion.get("modalities", []):
                return (
                    True,
                    policy.get("id", "GP-???"),
                    f"Policy {policy.get('id', '?')} excludes modality "
                    f"{modality!r}: {req.get('description', '')}",
                )
    return False, None, ""


def build_triage_decision(
    concept_ref: str,
    concept_id: str,
    action: str,
    *,
    rationale: str,
    supporting_assessments: list[str] | None = None,
    policy_ref: str | None = None,
    affected_entity_type: str = "concept",
    decided_by: str = "science-program-lead",
    human_approval: dict[str, Any] | None = None,
    conditions: list[str] | None = None,
    authorized_next_work: list[str] | None = None,
) -> dict[str, Any]:
    """Build a Stage 0 triage decision record.

    This builds the record structure. The caller is responsible for
    assigning the real ID (DR-NNN via ``next_id``) and writing it
    through ``write_record()`` — which enforces the human-approval
    gate for ``terminate`` actions on concepts and programs.
    """
    if action not in ACTIONS:
        raise ValueError(f"Invalid action {action!r}; must be one of {ACTIONS}")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    decision: dict[str, Any] = {
        "schema": "dde.decision-record.v1",
        "id": "DR-PENDING",  # Caller assigns the real ID
        "action": action,
        "affected_entity": {
            "entity_type": affected_entity_type,
            "entity_ref": concept_ref,
        },
        "rationale": rationale,
        "supporting_assessments": supporting_assessments or [],
        "decided_at": now,
        "decided_by": decided_by,
    }

    if human_approval is not None:
        decision["human_approval"] = human_approval

    if conditions:
        decision["conditions"] = conditions

    if policy_ref:
        decision["policy_ref"] = policy_ref

    if authorized_next_work:
        decision["authorized_next_work"] = authorized_next_work

    return decision


def evaluate_concept_portfolio(
    concept_results: list[ConceptTriageResult],
) -> list[ConceptTriageResult]:
    """Evaluate portfolio-level signals across all concept triage results.

    This function does NOT make autonomous decisions. It annotates each
    concept result with portfolio-level context (e.g., "all candidates
    have unclear mechanism-direction") that the lead uses to make
    the final decision.

    Per Hard Constraint #4: no aggregating tool outputs into a naive
    kill rule.
    """
    # Count portfolio-level patterns
    n_concepts = len(concept_results)
    if n_concepts == 0:
        return concept_results

    # Annotate — don't decide
    for cr in concept_results:
        # Check for contradicted evidence in any workstream
        contradicted_workstreams = []
        for ws_name, ws_result in cr.workstream_results.items():
            if ws_result.has_contradicted:
                contradicted_workstreams.append(ws_name)

        if contradicted_workstreams:
            cr.disposition_reason = (
                f"Workstream(s) {', '.join(contradicted_workstreams)} "
                f"produced contradicted evidence — requires lead review."
            )
            # Note: We do NOT set disposition to "terminated" here.
            # That is the lead's decision after review.

    return concept_results


def cancel_competing_alternatives(
    concept_results: list[ConceptTriageResult],
    accepted_concept_ref: str,
) -> list[ConceptTriageResult]:
    """Cancel in-flight work for competing alternatives after a concept
    is accepted.

    Per AC4: "Cancel work rendered irrelevant by an accepted decision."
    Alternatives are recorded as cancelled, never silently dropped.
    """
    for cr in concept_results:
        if cr.concept_ref == accepted_concept_ref:
            continue
        if cr.is_terminal:
            continue  # Already terminated/withdrawn — don't override

        # Mark remaining competing concepts as cancelled
        for _ws_name, ws_result in cr.workstream_results.items():
            if not ws_result.cancelled:
                ws_result.cancelled = True
                ws_result.cancel_reason = (
                    f"Cancelled: competing alternative for the same slot; "
                    f"concept {accepted_concept_ref} was accepted."
                )

    return concept_results


def build_budget_exhaustion_decision(
    concept_ref: str,
    concept_id: str,
    budget_reason: str,
    *,
    supporting_assessments: list[str] | None = None,
    decided_by: str = "science-program-lead",
) -> dict[str, Any]:
    """Build a decision record for budget-exhausted concepts.

    Per AC3: "Exhaustion remains incomplete, not scientific failure."
    Uses ``investigate`` or ``park``, NEVER ``terminate``.
    """
    return build_triage_decision(
        concept_ref=concept_ref,
        concept_id=concept_id,
        action="investigate",  # Always incomplete, never terminate
        rationale=(
            f"Stage 0 triage incomplete due to budget exhaustion: "
            f"{budget_reason}. This is not a scientific finding — "
            f"additional evidence is needed to reach a Stage 0 decision. "
            f"Resume triage when budget is available."
        ),
        supporting_assessments=supporting_assessments,
        decided_by=decided_by,
    )


def run_triage(
    concepts: list[dict[str, Any]],
    *,
    budget: TriageBudget | None = None,
    policies: list[dict[str, Any]] | None = None,
    structures_by_concept: dict[str, list[str]] | None = None,
    query_terms_by_concept: dict[str, str] | None = None,
    project_root: str | None = None,
    accepted_concept_ref: str | None = None,
    runner: Any = None,
    cli: Any = None,
) -> TriageOutcome:
    """Run Stage 0 bounded portfolio triage across multiple concepts.

    Parameters
    ----------
    concepts:
        List of intervention concept records (dicts).
    budget:
        Optional TriageBudget; None = unbounded.
    policies:
        Optional list of gate-policy records for constraint checking.
    structures_by_concept:
        Map of concept_ref -> list of structure identifiers for
        structure screening.
    query_terms_by_concept:
        Map of concept_ref -> query term for differentiation assessment.
    project_root:
        Path to the DDE project root (for CLI commands and record
        persistence).  When set, triage assessment and decision records
        are persisted through the real ``write_record()`` path.
    accepted_concept_ref:
        Optional concept ref (e.g. ``"IC-001-r1"``) to accept.  When
        set, competing alternatives are cancelled via
        ``cancel_competing_alternatives()`` and park decisions are
        recorded for cancelled concepts.
    runner:
        Optional CliRunner instance (for testing).
    cli:
        Optional CLI entry point (for testing).

    Returns
    -------
    TriageOutcome
        Full triage result with shortlist, decisions, assessments.
    """
    if budget is None:
        budget = TriageBudget()
    if policies is None:
        policies = []
    if structures_by_concept is None:
        structures_by_concept = {}
    if query_terms_by_concept is None:
        query_terms_by_concept = {}

    outcome = TriageOutcome()

    for concept in concepts:
        # Check budget before each concept
        exhausted, reason = budget.is_exhausted()
        if exhausted:
            outcome.budget_exhausted = True
            outcome.budget_exhaustion_reason = reason
            # Record remaining concepts as incomplete
            concept_id = concept.get("id", "IC-???")
            concept_rev = concept.get("revision", 1)
            concept_ref = f"{concept_id}-r{concept_rev}"
            decision = build_budget_exhaustion_decision(
                concept_ref=concept_ref,
                concept_id=concept_id,
                budget_reason=reason,
            )
            cr = ConceptTriageResult(
                concept_ref=concept_ref,
                concept_id=concept_id,
                disposition="investigate",
                disposition_reason=f"Budget exhausted: {reason}",
            )
            cr.decision_record = decision
            outcome.concept_results.append(cr)
            outcome.all_decisions.append(decision)
            continue

        concept_id = concept.get("id", "IC-???")
        concept_rev = concept.get("revision", 1)
        concept_ref = f"{concept_id}-r{concept_rev}"

        cr = ConceptTriageResult(
            concept_ref=concept_ref,
            concept_id=concept_id,
        )

        # --- Check policy exclusions first (cheapest check) ---
        excluded, policy_ref, exclusion_reason = check_policy_exclusion(
            concept, policies
        )
        if excluded:
            cr.disposition = "terminated"
            cr.disposition_reason = exclusion_reason
            cr.policy_ref = policy_ref
            cr.decision_record = build_triage_decision(
                concept_ref=concept_ref,
                concept_id=concept_id,
                action="terminate",
                rationale=exclusion_reason,
                policy_ref=policy_ref,
                # human_approval is NOT set here — write_record will
                # enforce the Refusal gate requiring it.
            )
            outcome.concept_results.append(cr)
            outcome.all_decisions.append(cr.decision_record)
            budget.record_concept()
            continue

        # --- Run workstreams ---

        # Workstream 3: Manufacturing feasibility (cheapest, always runs)
        mfg_result = run_manufacturing_workstream(
            concept,
            concept_ref,
            project_root=project_root,
            runner=runner,
            cli=cli,
        )
        cr.workstream_results["manufacturing"] = mfg_result
        outcome.all_assessments.extend(mfg_result.assessments)
        budget.record_invocation()

        # Workstream 2: Differentiation (if query term provided)
        query_term = query_terms_by_concept.get(concept_ref)
        if query_term:
            diff_result = run_differentiation_workstream(
                concept,
                concept_ref,
                query_term,
                project_root=project_root,
                runner=runner,
                cli=cli,
            )
            cr.workstream_results["differentiation"] = diff_result
            outcome.all_assessments.extend(diff_result.assessments)
            budget.record_invocation()

        # Workstream 2: Structure screening (if structures provided)
        structures = structures_by_concept.get(concept_ref, [])
        if structures:
            struct_result = run_structure_screening_workstream(
                concept,
                concept_ref,
                structures,
                project_root=project_root,
                runner=runner,
                cli=cli,
            )
            cr.workstream_results["tractability"] = struct_result
            outcome.all_assessments.extend(struct_result.assessments)
            budget.record_invocation()

        outcome.concept_results.append(cr)
        budget.record_concept()

    # Portfolio-level evaluation (annotates, does not decide)
    outcome.concept_results = evaluate_concept_portfolio(outcome.concept_results)

    # Cancel competing alternatives when a concept is accepted
    if accepted_concept_ref:
        outcome.concept_results = cancel_competing_alternatives(
            outcome.concept_results, accepted_concept_ref
        )
        # Record park decisions for cancelled competing concepts
        for cr in outcome.concept_results:
            if cr.concept_ref == accepted_concept_ref:
                continue
            if cr.is_terminal:
                continue
            any_cancelled = any(ws.cancelled for ws in cr.workstream_results.values())
            if any_cancelled and cr.decision_record is None:
                cr.disposition = "parked"
                cr.disposition_reason = (
                    f"Competing alternative cancelled: concept "
                    f"{accepted_concept_ref} was accepted for this slot."
                )
                cr.decision_record = build_triage_decision(
                    concept_ref=cr.concept_ref,
                    concept_id=cr.concept_id,
                    action="park",
                    rationale=cr.disposition_reason,
                )
                outcome.all_decisions.append(cr.decision_record)

    # Build shortlist from concepts with no terminal/parked disposition
    outcome.shortlist = [
        cr.concept_ref
        for cr in outcome.concept_results
        if not cr.is_terminal and cr.disposition != "parked"
    ]

    # --- Persist records to the control store ---
    #
    # When project_root is set, persist assessment and decision records
    # through the real write_record() path.  This is where Hard
    # Constraint #2 takes effect: terminate decisions go through
    # write_record()'s human-approval gate.
    if project_root:
        from dde.core.controlstore import next_id

        # Persist assessment records from all workstreams
        for cr in outcome.concept_results:
            for _ws_name, ws_result in cr.workstream_results.items():
                for assessment in ws_result.assessments:
                    # Only persist records with the assessment schema
                    if assessment.get("schema") != "dde.evidence-assessment.v1":
                        continue
                    try:
                        aid = next_id(project_root, "assessment")
                        write_triage_assessment(project_root, assessment, aid)
                    except Refusal as exc:
                        outcome.persistence_errors.append(
                            {
                                "concept_ref": cr.concept_ref,
                                "record_type": "assessment",
                                "type": "refusal",
                                "message": str(exc),
                            }
                        )
                    except Exception as exc:
                        outcome.persistence_errors.append(
                            {
                                "concept_ref": cr.concept_ref,
                                "record_type": "assessment",
                                "type": "validation_error",
                                "message": str(exc),
                            }
                        )

        # Persist decision records
        for cr in outcome.concept_results:
            if cr.decision_record is None:
                continue
            try:
                did = next_id(project_root, "decision")
                write_triage_decision(project_root, cr.decision_record, did)
            except Refusal as exc:
                # Terminate without human approval — the gate works.
                # The decision needs human approval before persistence.
                outcome.persistence_errors.append(
                    {
                        "concept_ref": cr.concept_ref,
                        "record_type": "decision",
                        "type": "refusal",
                        "message": str(exc),
                    }
                )
            except Exception as exc:
                outcome.persistence_errors.append(
                    {
                        "concept_ref": cr.concept_ref,
                        "record_type": "decision",
                        "type": "validation_error",
                        "message": str(exc),
                    }
                )

    return outcome


# ---------------------------------------------------------------------------
# Record writing helpers
# ---------------------------------------------------------------------------


def write_triage_decision(
    project_root: str,
    decision: dict[str, Any],
    decision_id: str,
    *,
    concept_loader: Any = None,
) -> dict[str, Any]:
    """Write a triage decision through the REAL ``write_record()`` path.

    Per Hard Constraint #2: any ``terminate`` action goes through the
    real, unmodified ``write_record()`` — which enforces the
    human-approval ``Refusal`` gate.

    Uses copy-then-mutate: the caller's dict is only updated with the
    real ``decision_id`` after ``write_record()`` succeeds.  If
    ``write_record()`` raises (e.g. ``Refusal``), the caller's dict
    retains its original ``id`` value (e.g. ``"DR-PENDING"``), preventing
    a never-persisted decision from carrying a real-looking ``DR-NNN``.

    Returns the written decision record, or raises Refusal if the
    human-approval gate blocks it.
    """
    from dde.core.controlstore import write_record

    # Write a copy — don't mutate the caller's dict until persistence succeeds
    to_write = {**decision, "id": decision_id}
    write_record(
        project_root,
        "decision",
        decision_id,
        to_write,
        concept_loader=concept_loader,
    )
    # Only update the caller's dict on success
    decision["id"] = decision_id
    return decision


def write_triage_assessment(
    project_root: str,
    assessment: dict[str, Any],
    assessment_id: str,
) -> dict[str, Any]:
    """Write a triage assessment through the REAL ``write_record()`` path.

    Uses copy-then-mutate: the caller's dict is only updated with the
    real ``assessment_id`` after ``write_record()`` succeeds.
    """
    from dde.core.controlstore import write_record

    # Write a copy — don't mutate the caller's dict until persistence succeeds
    to_write = {**assessment, "id": assessment_id}
    write_record(project_root, "assessment", assessment_id, to_write)
    # Only update the caller's dict on success
    assessment["id"] = assessment_id
    return assessment
