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

"""`dde triage` — Stage 0 bounded portfolio triage CLI.

Compares intervention concepts before final target/modality commitment
and authorizes a defined next investment.  Runs three parallel
workstreams (rationale verification, modality tractability, manufacturing
feasibility) across eligible alternatives.

Orchestration logic lives in ``core/triage.py``; this module is the
thin CLI wrapper following the project's tier separation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from ..common import (
    AppState,
    out_option,
    output_options,
    pass_state,
)
from ..core.errors import ProjectRootError, SchemaError
from ..core.output import Emitter
from ..core.triage import (
    TriageBudget,
    run_triage,
)


@click.group()
def triage() -> None:
    """Stage 0 bounded portfolio triage."""


@triage.command("run")
@click.argument("concept_paths", nargs=-1, required=True, type=click.Path(exists=True))
@click.option(
    "--max-seconds",
    type=float,
    default=None,
    help="Wall-clock budget in seconds (unbounded if omitted).",
)
@click.option(
    "--max-concepts",
    type=int,
    default=None,
    help="Maximum number of concepts to evaluate (unbounded if omitted).",
)
@click.option(
    "--max-invocations",
    type=int,
    default=None,
    help="Maximum workstream invocations (unbounded if omitted).",
)
@click.option(
    "--query-term",
    multiple=True,
    help=(
        "Query term for differentiation assessment, one per concept "
        "in the same order as CONCEPT_PATHS.  Omit to skip differentiation."
    ),
)
@click.option(
    "--structure",
    "structure_specs",
    multiple=True,
    help=(
        "Structure spec as CONCEPT_REF:STRUCTURE_ID for structure "
        "screening.  May be repeated.  Omit to skip structure screening."
    ),
)
@click.option(
    "--policy",
    "policy_paths",
    multiple=True,
    type=click.Path(exists=True),
    help="Path to a gate-policy JSON file.  May be repeated.",
)
@click.option(
    "--accept",
    "accepted_concept_ref",
    default=None,
    help=(
        "Accept a specific concept ref (e.g. IC-001-r1), cancelling "
        "competing alternatives.  Omit to leave all concepts for lead review."
    ),
)
@out_option
@output_options
@pass_state
def run_cmd(
    state: AppState,
    concept_paths: tuple[str, ...],
    max_seconds: float | None,
    max_concepts: int | None,
    max_invocations: int | None,
    query_term: tuple[str, ...],
    structure_specs: tuple[str, ...],
    policy_paths: tuple[str, ...],
    accepted_concept_ref: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Run Stage 0 bounded portfolio triage across concepts.

    Reads one or more concept record JSON files and runs manufacturing,
    differentiation, and structure screening workstreams across them,
    subject to budget constraints.

    \\b
    Inputs:
      CONCEPT_PATHS  One or more paths to concept record JSON files.

    \\b
    Budget controls:
      --max-seconds       Wall-clock limit
      --max-concepts      Concept count limit
      --max-invocations   Workstream invocation limit

    \\b
    Workstream inputs (optional):
      --query-term   Differentiation query terms (one per concept)
      --structure    Structure specs as CONCEPT_REF:STRUCTURE_ID
      --policy       Gate-policy files for constraint checking
    """
    from ..core import provenance

    emit = Emitter(as_json=as_json, quiet=quiet)

    # --- Load concepts ---
    concepts: list[dict[str, Any]] = []
    for cp in concept_paths:
        concepts.append(provenance.read_json(Path(cp), "concept record"))

    # --- Build budget ---
    budget = TriageBudget(
        max_wall_clock_seconds=max_seconds,
        max_concepts=max_concepts,
        max_workstream_invocations=max_invocations,
    )

    # --- Load policies ---
    policies: list[dict[str, Any]] = []
    for pp in policy_paths:
        policies.append(provenance.read_json(Path(pp), "gate policy"))

    # --- Build query-term map ---
    query_terms_by_concept: dict[str, str] = {}
    for i, qt in enumerate(query_term):
        if i < len(concepts):
            c = concepts[i]
            cid = c.get("id", f"IC-{i:03d}")
            crev = c.get("revision", 1)
            cref = f"{cid}-r{crev}"
            query_terms_by_concept[cref] = qt

    # --- Build structure map ---
    structures_by_concept: dict[str, list[str]] = {}
    for spec in structure_specs:
        if ":" not in spec:
            raise click.UsageError(
                f"Structure spec must be CONCEPT_REF:STRUCTURE_ID, got {spec!r}"
            )
        cref, struct_id = spec.split(":", 1)
        structures_by_concept.setdefault(cref, []).append(struct_id)

    # --- Resolve project root (optional — triage can run without one) ---
    project_root: str | None = None
    try:
        project_root = str(state.project().root)
    except ProjectRootError:
        # No project context available (no --project, no $DDE_PROJECT,
        # no .dde/ walk-up discovery).  Triage can still run without
        # one, but records will not be persisted to the control store.
        pass

    # --- Run triage ---
    from click.testing import CliRunner

    from ..cli import cli as dde_cli

    outcome = run_triage(
        concepts,
        budget=budget,
        policies=policies,
        structures_by_concept=structures_by_concept,
        query_terms_by_concept=query_terms_by_concept,
        project_root=project_root,
        accepted_concept_ref=accepted_concept_ref,
        runner=CliRunner(),
        cli=dde_cli,
    )

    # --- Emit results ---
    emit.data("n_concepts", len(outcome.concept_results))
    emit.data("n_shortlisted", len(outcome.shortlist))
    emit.data("budget_exhausted", outcome.budget_exhausted)

    if outcome.budget_exhausted:
        emit.data("budget_reason", outcome.budget_exhaustion_reason)

    emit.data("shortlist", outcome.shortlist)

    for cr in outcome.concept_results:
        emit.line(f"\n  Concept {cr.concept_ref}:")
        emit.line(f"    Disposition: {cr.disposition or '(pending lead review)'}")
        if cr.disposition_reason:
            emit.line(f"    Reason: {cr.disposition_reason}")
        for ws_name, ws_result in cr.workstream_results.items():
            n_assess = len(ws_result.assessments)
            n_err = len(ws_result.errors)
            cancelled = " [CANCELLED]" if ws_result.cancelled else ""
            emit.line(
                f"    {ws_name}: {n_assess} assessment(s), {n_err} error(s){cancelled}"
            )
            for a in ws_result.assessments:
                status = a.get("evidence_status", "?")
                emit.line(f"      status: {status}")

    # --- Surface persistence errors ---
    if outcome.persistence_errors:
        emit.line("")
        emit.line(
            f"WARNING: {len(outcome.persistence_errors)} record(s) failed "
            f"to persist to the control store:"
        )
        for perr in outcome.persistence_errors:
            emit.line(
                f"  - [{perr['type']}] {perr.get('record_type', '?')} "
                f"for {perr['concept_ref']}: {perr['message']}"
            )

    emit.line("")
    emit.line(
        "Stage 0 triage is a lead-orchestrated decision process.  "
        "Workstreams raise findings; the lead makes the reviewed decision."
    )

    # --- Write output if requested ---
    if out:
        target = Path(out)
    else:
        try:
            target = state.project().artifact_dir("triage", None)
        except (ProjectRootError, SchemaError):
            target = None

    if target is not None:
        target.mkdir(parents=True, exist_ok=True)
        output_path = target / "stage0-triage-outcome.json"

        # Serialize the outcome
        serializable: dict[str, Any] = {
            "schema": "dde.stage0-triage-outcome.v1",
            "n_concepts": len(outcome.concept_results),
            "shortlist": outcome.shortlist,
            "budget_exhausted": outcome.budget_exhausted,
            "budget_exhaustion_reason": outcome.budget_exhaustion_reason,
            "concept_results": [],
            "all_assessments": outcome.all_assessments,
            "all_decisions": outcome.all_decisions,
            "persistence_errors": outcome.persistence_errors,
        }
        for cr in outcome.concept_results:
            cr_data: dict[str, Any] = {
                "concept_ref": cr.concept_ref,
                "concept_id": cr.concept_id,
                "disposition": cr.disposition,
                "disposition_reason": cr.disposition_reason,
                "policy_ref": cr.policy_ref,
                "workstreams": {},
            }
            for ws_name, ws_result in cr.workstream_results.items():
                cr_data["workstreams"][ws_name] = {
                    "assessments": ws_result.assessments,
                    "errors": ws_result.errors,
                    "cancelled": ws_result.cancelled,
                    "cancel_reason": ws_result.cancel_reason,
                }
            serializable["concept_results"].append(cr_data)

        output_path.write_text(
            json.dumps(serializable, indent=2) + "\n", encoding="utf-8"
        )
        emit.path(output_path, role="triage-outcome")

    if as_json:
        emit.data(
            "concept_results",
            [
                {
                    "concept_ref": cr.concept_ref,
                    "disposition": cr.disposition,
                    "workstreams": {
                        ws: {
                            "n_assessments": len(r.assessments),
                            "evidence_statuses": r.evidence_statuses,
                            "cancelled": r.cancelled,
                        }
                        for ws, r in cr.workstream_results.items()
                    },
                }
                for cr in outcome.concept_results
            ],
        )
        if outcome.persistence_errors:
            emit.data("persistence_errors", outcome.persistence_errors)

    emit.flush()
