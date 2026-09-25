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

"""`dde manufacturing` — progressive manufacturing assessment CLI.

Stage 0 (qualitative, no external APIs):
  assess-stage0  Assess production-platform fit for a concept record.

  stage-requirements  Print the stage-gated requirement structure.

Assessment logic lives in ``core/manufacturing.py``; this module is
the thin CLI wrapper following the project's tier separation (§1 of
tool-design-guidance.md).
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..common import (
    AppState,
    out_option,
    output_options,
    pass_state,
)
from ..core.manufacturing import (
    STAGE_REQUIREMENTS,
    assess_stage0,
)
from ..core.output import Emitter
from ..core.paths import sanitize_slug


@click.group()
def manufacturing() -> None:
    """Progressive manufacturing feasibility assessment."""


@manufacturing.command("assess-stage0")
@click.argument("concept_path", type=click.Path(exists=True))
@click.option(
    "--sa-score-path",
    type=click.Path(exists=True),
    default=None,
    help="Path to an existing SA-score record (from `dde compound sa-score`).",
)
@out_option
@output_options
@pass_state
def assess_stage0_cmd(
    state: AppState,
    concept_path: str,
    sa_score_path: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Assess Stage 0 manufacturing feasibility for a concept.

    Reads a concept record (JSON) and produces an evidence assessment
    record for manufacturing feasibility.  For small molecules with
    a structure (entity_ref), optionally incorporates SA-score data.

    \\b
    Inputs:
      CONCEPT_PATH  Path to a concept record JSON file.

    \\b
    Stage 0 assesses:
      - Production-platform fit (modality -> known platforms)
      - Delivery-manufacturing compatibility
      - SA-score integration (small molecules, if available)
      - Complexity heuristics (stereocenters, rings, step estimates)
      - Biologic feasibility (qualitative for biologic modalities)
      - NOT_YET_APPLICABLE for concepts without entity_ref
    """
    from ..core import provenance

    emit = Emitter(as_json=as_json, quiet=quiet)

    # Read concept record
    concept_data = provenance.read_json(Path(concept_path), "concept record")

    # Read SA-score if provided
    sa_score_data = None
    if sa_score_path is not None:
        sa_score_data = provenance.read_json(Path(sa_score_path), "SA-score record")

    # Run assessment
    assessment = assess_stage0(concept_data, sa_score_data)

    # Write output
    concept_id = sanitize_slug(concept_data.get("id", "IC-UNKNOWN"))
    if out:
        target_dir = Path(out)
    else:
        target_dir = state.project().artifact_dir("manufacturing", None)

    target_dir.mkdir(parents=True, exist_ok=True)
    output_path = target_dir / f"{concept_id}.manufacturing-stage0.json"
    output_path.write_text(json.dumps(assessment, indent=2) + "\n", encoding="utf-8")

    emit.data("concept_ref", assessment["concept_ref"])
    emit.data("evidence_status", assessment["evidence_status"])
    emit.data("claim", assessment["claim"])

    n_findings = len(assessment.get("findings", []))
    emit.data("n_findings", n_findings)

    for finding in assessment.get("findings", []):
        aspect = finding.get("aspect", "?")
        status = finding.get("status", "?")
        emit.line(f"  [{aspect}] {status}")
        if finding.get("flags"):
            for flag in finding["flags"]:
                emit.line(f"    FLAG: {flag}")

    emit.line("")
    emit.line(
        "IMPORTANT: Stage 0 assessment does NOT imply GMP readiness or "
        "manufacturing clearance."
    )
    emit.path(output_path, role="manufacturing-assessment")
    emit.flush()


@manufacturing.command("stage-requirements")
@output_options
def stage_requirements_cmd(as_json: bool, quiet: bool) -> None:
    """Print the progressive manufacturing stage requirement structure.

    Shows the stage-gated requirement definitions for Stages 0, 2, 3,
    and 4, including required evidence types and forward-looking
    placeholders for stages whose underlying tools do not yet exist.
    """
    emit = Emitter(as_json=as_json, quiet=quiet)

    if as_json:
        emit.data("stage_requirements", STAGE_REQUIREMENTS)
    else:
        for stage, req in sorted(STAGE_REQUIREMENTS.items()):
            emit.line(f"\nStage {stage}: {req['description']}")
            emit.line(f"  Name: {req['name']}")
            emit.line(f"  Required inputs: {', '.join(req['required_inputs'])}")
            if req.get("optional_inputs"):
                emit.line(f"  Optional inputs: {', '.join(req['optional_inputs'])}")
            emit.line(f"  Evidence types: {', '.join(req['evidence_types'])}")
            if req.get("status"):
                emit.line(f"  Status: {req['status']}")
    emit.flush()
