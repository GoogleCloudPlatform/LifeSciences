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

"""Stage 0 evaluation harness — run fixtures through the Stage 0 triage workflow.

Runs each of the 8 existing fixtures through the Stage 0 bounded portfolio
triage workflow (``run_triage()`` from ``core/triage.py``) and collects
measurements using the same ``FixtureMetrics`` / ``BaselineReport``
structures as the baseline harness.

This module does NOT modify the existing baseline artifacts, fixture
definitions, or tools.  It measures the Stage 0 workflow as-is.

Usage (from the DDE application root):

    PYTHONPATH=tools python3 -m eval.run_comparison
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure the tools package is importable.
_TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from click.testing import CliRunner  # noqa: E402
from dde.cli import cli  # noqa: E402
from dde.core.triage import (  # noqa: E402
    run_triage,
)

from .fixtures.definitions import (  # noqa: E402
    ALL_FIXTURES,
    DECLINED_CANDIDATE_SAMPLE,
    FixtureDefinition,
)
from .harness import _make_project, _place_synthetic_artifacts  # noqa: E402
from .metrics import BaselineReport, FixtureMetrics  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture-to-concept conversion
# ---------------------------------------------------------------------------


def fixture_to_concept(fixture: FixtureDefinition) -> dict[str, Any]:
    """Convert a fixture's work order context into a Stage 0 concept record.

    Uses only data already present in the fixture — no fabricated fields.
    Fields that the fixture does not provide are left as None, matching
    how Stage 0 handles incomplete concept data (producing
    ``not_yet_applicable`` assessments rather than errors).
    """
    wo = fixture.work_order
    ctx = wo.get("context", {})

    target = ctx.get("target", "")
    modality = ctx.get("modality", "small_molecule")
    indication = ctx.get("indication", "evaluation_fixture")

    # Build entity_ref from available compound identifiers.
    # Empty strings are treated as absent (None).
    entity_ref = (
        ctx.get("compound_smiles")
        or ctx.get("compound_inchikey")
        or ctx.get("compound")
        or None
    )
    if entity_ref == "":
        entity_ref = None

    return {
        "schema": "dde.intervention-concept.v1",
        "id": f"IC-{fixture.fixture_id}",
        "revision": 1,
        "state": "active",
        "disease_context": {
            "indication": indication,
        },
        "target_pathway": {
            "gene": target if target else "UNKNOWN",
            "protein": target if target else "UNKNOWN",
            "pathway": f"Evaluation fixture — {fixture.category}",
            "mechanism_hypothesis": wo.get("decision_question", ""),
        },
        "modality": modality,
        "entity_ref": entity_ref,
        "charter_ref": "DEC-EVAL",
        "termination_authority": "human",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ---------------------------------------------------------------------------
# Stage 0 fixture runner
# ---------------------------------------------------------------------------


def run_fixture_stage0(fixture: FixtureDefinition) -> FixtureMetrics:
    """Run a single fixture through Stage 0 triage and return metrics.

    Calls the real ``run_triage()`` function, which in turn invokes
    real CLI workstream commands (``dde manufacturing assess-stage0``,
    etc.) via ``CliRunner``.  No mocked or hand-crafted results.
    """
    metrics = FixtureMetrics(
        fixture_id=fixture.fixture_id,
        fixture_label=fixture.label,
        scenario_category=fixture.category,
    )
    metrics.start()

    concept = fixture_to_concept(fixture)
    concept_ref = f"{concept['id']}-r{concept['revision']}"

    runner = CliRunner()

    try:
        with tempfile.TemporaryDirectory() as td:
            project = _make_project(Path(td))

            # Place synthetic artifacts if the fixture has them, so the
            # project mirrors what the baseline harness set up.
            if fixture.synthetic_artifacts:
                _place_synthetic_artifacts(
                    project,
                    fixture.synthetic_artifacts,
                    metrics,
                )

            # Run Stage 0 triage with the single concept.
            outcome = run_triage(
                [concept],
                project_root=str(project),
                runner=runner,
                cli=cli,
            )

            # --- Collect metrics from the triage outcome ---
            if not outcome.concept_results:
                metrics.error_messages.append(
                    "Stage 0 triage produced no concept results"
                )
            else:
                cr = outcome.concept_results[0]

                # Record per-workstream invocations as CLI calls.
                for ws_name, ws_result in cr.workstream_results.items():
                    exit_code = 0 if not ws_result.errors else 1
                    output_desc = (
                        f"{len(ws_result.assessments)} assessment(s), "
                        f"{len(ws_result.errors)} error(s)"
                    )
                    metrics.record_invocation(
                        f"triage/{ws_name}",
                        exit_code,
                        output_desc,
                    )

                    # Record assessment observations.
                    for assessment in ws_result.assessments:
                        status = assessment.get("evidence_status", "unknown")
                        execution = assessment.get("execution_outcome", "unknown")
                        metrics.observe(
                            f"[{ws_name}] evidence_status={status}, "
                            f"execution_outcome={execution}"
                        )

                    # Record workstream errors as observations.
                    for err in ws_result.errors:
                        metrics.observe(f"[{ws_name}] error: {err}")

                    # Record cancellations.
                    if ws_result.cancelled:
                        metrics.observe(
                            f"[{ws_name}] cancelled: {ws_result.cancel_reason}"
                        )

                # Record the triage disposition.
                disposition = cr.disposition or "(pending lead review)"
                metrics.observe(f"Stage 0 disposition: {disposition}")
                if cr.disposition_reason:
                    metrics.observe(f"Disposition reason: {cr.disposition_reason}")

                # Record disposition as a state transition.
                metrics.record_transition("active", disposition)

                # Record decision record details.
                if cr.decision_record:
                    action = cr.decision_record.get("action", "unknown")
                    metrics.observe(f"Decision action: {action}")
                    if cr.policy_ref:
                        metrics.observe(f"Policy ref: {cr.policy_ref}")

                # Record persistence errors as observations (not fixture
                # failures — persistence errors are expected when
                # assessments have non-standard schemas).
                for perr in outcome.persistence_errors:
                    metrics.observe(
                        f"Persistence {perr['type']}: "
                        f"{perr.get('record_type', '?')} for "
                        f"{perr['concept_ref']}: {perr['message']}"
                    )

                # Record budget state.
                if outcome.budget_exhausted:
                    metrics.observe(
                        f"Budget exhausted: {outcome.budget_exhaustion_reason}"
                    )

                # Record shortlist status.
                in_shortlist = concept_ref in outcome.shortlist
                metrics.observe(
                    f"Shortlisted: {in_shortlist} (shortlist: {outcome.shortlist})"
                )

    except Exception as exc:
        metrics.error_messages.append(f"Stage 0 fixture failed with exception: {exc}")
        traceback.print_exc()

    metrics.stop()
    return metrics


def run_all_fixtures_stage0() -> BaselineReport:
    """Run all fixtures through Stage 0 triage and produce a report.

    Uses the same ``BaselineReport`` structure as the baseline harness
    so the two can be directly compared.  Stage 0 specific behavior is
    captured in the ``observations`` field of each ``FixtureMetrics``.
    """
    report = BaselineReport(
        eval_version="1.0-stage0",
        run_timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    print(f"DDE Stage 0 Evaluation Harness v{report.eval_version}")
    print(f"Run timestamp: {report.run_timestamp}")
    print(f"Fixtures to run: {len(ALL_FIXTURES)}")
    print("=" * 60)

    for fixture in ALL_FIXTURES:
        print(f"\n--- {fixture.fixture_id}: {fixture.label} ---")
        metrics = run_fixture_stage0(fixture)
        report.add_result(metrics)

        status = "PASS" if metrics.completed else "FAIL"
        print(
            f"  {status}: {metrics.wall_clock_seconds}s, "
            f"{metrics.invocation_count} workstream calls"
        )
        if metrics.error_messages:
            for err in metrics.error_messages:
                print(f"  ERROR: {err}")

    print("\n" + "=" * 60)
    print(f"Results: {report.completed_fixtures}/{report.total_fixtures} completed")
    print(f"Total wall clock: {report.total_wall_clock}s")
    print(f"Total workstream invocations: {report.total_invocations}")

    print(
        f"\nDeclined candidate sample (selection-bias review): "
        f"{DECLINED_CANDIDATE_SAMPLE}"
    )

    return report
