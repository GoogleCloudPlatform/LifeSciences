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

"""`dde retro` -- one-step retrosynthetic analysis via ASKCOS.

Two phases:

  search   one SMILES -> retrosynthetic suggestions from ASKCOS
           (MIT), written verbatim to Layer 0 with a sidecar.
  analyze  reads stored search results and classifies synthetic
           accessibility. No network.

ASKCOS uses an async pattern: POST a SMILES string to the
template-relevance endpoint, receive a Celery task ID, then poll
a legacy task endpoint until the result is ready. The one-step
retrosynthesis endpoint requires no authentication.

Each suggestion is a set of reactant SMILES (dot-separated), a
confidence score, and a reaction template (SMARTS). The top
suggestion for aspirin (score 0.407) is acetic anhydride +
salicylic acid -- the textbook synthesis.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import click

from ..common import (
    AppState,
    emitter,
    from_option,
    out_option,
    output_options,
    pass_state,
)
from ..core import http, provenance
from ..core.errors import (
    ArtifactError,
    EndpointUnavailable,
    SchemaError,
)
from ..core.qps import qps_for_host

TOOL = "dde.retro"
ARTIFACT_CLASS = "retrosynthesis"

ASKCOS_API = "https://askcos.mit.edu"

# Async polling parameters.
_POLL_INTERVAL = 2.0  # seconds between polls
_POLL_TIMEOUT = 60.0  # total seconds before giving up


def _fetch_askcos_retro(
    smiles: str,
    max_results: int = 10,
) -> tuple[bytes, dict[str, Any]]:
    """Fetch one-step retrosynthetic analysis from ASKCOS.

    Async pattern:
      1. POST SMILES to the template-relevance endpoint.
      2. Extract the Celery task ID from the response.
      3. Poll the legacy celery task endpoint until complete.
      4. Parse results and build an artifact.

    Returns (verbatim response bytes, structured artifact dict).
    """
    # Step 1: Submit the async retrosynthesis request.
    submit_url = f"{ASKCOS_API}/api/retro/template-relevance/call-async"
    body = json.dumps({"smiles": [smiles]})
    submit_response = http.request(
        "POST",
        submit_url,
        qps=qps_for_host("askcos.mit.edu"),
        timeout=30.0,
        headers={"Content-Type": "application/json"},
        data=body.encode("utf-8"),
    )
    try:
        task_id = json.loads(submit_response.content.decode("utf-8"))
    except Exception as exc:
        raise SchemaError(
            "ASKCOS submit endpoint did not return valid JSON",
            detail=str(exc),
        ) from exc

    if not isinstance(task_id, str) or not task_id.strip():
        raise SchemaError(
            "ASKCOS submit endpoint did not return a task ID",
            detail=f"got: {task_id!r}",
        )

    # Step 2: Poll for results.
    poll_url = f"{ASKCOS_API}/api/legacy/celery/task/{task_id}/"
    start = time.monotonic()

    while True:
        elapsed = time.monotonic() - start
        if elapsed > _POLL_TIMEOUT:
            raise EndpointUnavailable(
                f"ASKCOS task {task_id} did not complete within "
                f"{_POLL_TIMEOUT:.0f} seconds",
                remedy="the ASKCOS server may be under heavy load; retry later",
            )

        poll_response = http.request(
            "GET",
            poll_url,
            qps=qps_for_host("askcos.mit.edu"),
            timeout=30.0,
        )
        try:
            poll_data = json.loads(poll_response.content.decode("utf-8"))
        except Exception as exc:
            raise SchemaError(
                "ASKCOS poll endpoint did not return valid JSON",
                detail=str(exc),
            ) from exc

        if poll_data.get("complete"):
            break

        if poll_data.get("failed"):
            raise EndpointUnavailable(
                f"ASKCOS task {task_id} failed",
                detail=poll_data.get("output", "no detail"),
                remedy="check the SMILES input and retry",
            )

        time.sleep(_POLL_INTERVAL)

    # Step 3: Parse the completed result.
    raw = json.dumps(poll_data, indent=2).encode("utf-8")

    output = poll_data.get("output") or {}
    results = output.get("result", [])
    if not results:
        # Valid response but no suggestions.
        return raw, _build_artifact(smiles, [])

    # The first (and typically only) result entry contains the suggestions.
    result_entry = results[0]
    reactants_list = result_entry.get("reactants", [])
    scores_list = result_entry.get("scores", [])
    templates_list = result_entry.get("templates", [])

    # Build suggestions, capped at max_results.
    suggestions: list[dict[str, Any]] = []
    n = min(len(reactants_list), len(scores_list), max_results)
    for i in range(n):
        reactant_smiles = reactants_list[i]
        score = scores_list[i]
        # Templates may be fewer than reactants/scores.
        template = templates_list[i] if i < len(templates_list) else {}

        suggestions.append(
            {
                "rank": i + 1,
                "reactants": reactant_smiles.split("."),
                "reactants_smiles": reactant_smiles,
                "score": score,
                "template_smarts": template.get("reaction_smarts", ""),
                "template_rank": template.get("template_rank"),
                "num_examples": template.get("num_examples"),
            }
        )

    artifact = _build_artifact(smiles, suggestions)
    return raw, artifact


def _build_artifact(
    smiles: str,
    suggestions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the structured dde.retro.v1 artifact."""
    return {
        "schema": "dde.retro.v1",
        "query": {
            "smiles": smiles,
            "source": "askcos",
        },
        "summary": {
            "n_suggestions": len(suggestions),
            "top_score": suggestions[0]["score"] if suggestions else None,
            "top_reactants": (suggestions[0]["reactants"] if suggestions else []),
        },
        "suggestions": suggestions,
    }


@click.group()
def retro() -> None:
    """Retrosynthetic analysis."""


@retro.command("search")
@click.argument("smiles")
@click.option(
    "--source",
    type=click.Choice(["askcos"]),
    default="askcos",
    help="Which retrosynthesis backend to query (default: askcos).",
)
@click.option(
    "--max-results",
    type=int,
    default=10,
    help="Max suggestions to include (default: 10).",
)
@out_option
@output_options
@pass_state
def search_cmd(
    state: AppState,
    smiles: str,
    source: str,
    max_results: int,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Retrosynthetic analysis for SMILES."""
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # SMILES contain special characters; use a hash-based slug.
    slug = hashlib.sha256(smiles.encode()).hexdigest()[:16]

    if source == "askcos":
        endpoint = f"{ASKCOS_API}/api/retro/template-relevance/call-async"
        raw, artifact = _fetch_askcos_retro(smiles, max_results=max_results)
    else:
        raise click.BadParameter(f"unsupported source: {source}")

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint=endpoint,
        parameters={
            "query_smiles": smiles,
            "source": source,
            "max_results": max_results,
        },
    )
    sidecar.note("source_db", source)
    sidecar.note("n_suggestions", artifact["summary"]["n_suggestions"])

    # Write verbatim response.
    verbatim_path = target_dir / f"{slug}.retro-{source}.json"
    verbatim_path.write_bytes(raw)
    sidecar.add_output(verbatim_path)

    # Write structured artifact.
    artifact_path = target_dir / f"{slug}.retro-{source}.artifact.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(artifact_path)

    # Write sidecar.
    meta_path = sidecar.write(target_dir / f"{slug}.retro-{source}.meta.json")

    emit.data("smiles", smiles)
    emit.data("source", source)
    emit.data("n_suggestions", artifact["summary"]["n_suggestions"])
    emit.data("top_score", artifact["summary"]["top_score"])
    emit.data("top_reactants", artifact["summary"]["top_reactants"])
    emit.path(verbatim_path, role="verbatim")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.flush()


@retro.command("analyze")
@click.argument("smiles")
@click.option(
    "--source",
    type=click.Choice(["askcos"]),
    default="askcos",
    help="Which source to analyze (must match a prior search).",
)
@click.option(
    "--threshold",
    "score_threshold",
    type=float,
    default=None,
    help="Override the minimum confidence score for 'accessible' verdict.",
)
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    smiles: str,
    source: str,
    score_threshold: float | None,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Assess synthetic accessibility from stored retro results. No network."""
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = hashlib.sha256(smiles.encode()).hexdigest()[:16]
    artifact_path = source_dir / f"{slug}.retro-{source}.artifact.json"
    if not artifact_path.is_file():
        raise ArtifactError(
            f"no retrosynthesis artifact for SMILES (source={source})",
            detail=f"expected: {artifact_path}",
            remedy=f"run `dde retro search '{smiles}' --source {source}` first",
        )

    artifact = provenance.read_json(artifact_path, "retrosynthesis artifact")
    if artifact.get("schema") != "dde.retro.v1":
        raise SchemaError(
            f"unexpected schema in {artifact_path.name}",
            detail=f"expected dde.retro.v1, got {artifact.get('schema')!r}",
        )

    suggestions = artifact.get("suggestions", [])
    top_score_cutoff = score_threshold if score_threshold is not None else 0.3

    # Compute metrics.
    scores = [s["score"] for s in suggestions if s.get("score") is not None]
    n_suggestions = len(suggestions)
    max_score = max(scores) if scores else 0.0
    mean_score = sum(scores) / len(scores) if scores else 0.0
    # Count routes above threshold as plausible single-step routes.
    n_above_threshold = sum(1 for s in scores if s >= top_score_cutoff)

    # Determine verdict.
    if n_suggestions == 0:
        verdict = "no_routes"
    elif max_score >= top_score_cutoff and n_above_threshold >= 2:
        verdict = "accessible"
    elif max_score >= top_score_cutoff or n_suggestions >= 1:
        verdict = "challenging"
    else:
        verdict = "no_routes"

    metrics = {
        "n_suggestions": n_suggestions,
        "max_score": max_score,
        "mean_score": round(mean_score, 4),
        "n_above_threshold": n_above_threshold,
        "threshold": top_score_cutoff,
        "source": source,
    }
    assessment = {
        "verdict": verdict,
        "smiles": smiles,
        "n_suggestions": n_suggestions,
        "max_score": max_score,
        "n_above_threshold": n_above_threshold,
    }

    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.retro-{source}.analysis.json",
        source=state.project().relative(artifact_path),
        threshold_set=f"retro-{source}",
        thresholds_applied={
            "score_cutoff": top_score_cutoff,
        },
        metrics=metrics,
        assessment=assessment,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("metrics", metrics)

    emit.line(
        f"retro({smiles}) -> {verdict.upper()} "
        f"({n_above_threshold}/{n_suggestions} above "
        f"threshold={top_score_cutoff})"
    )
    if suggestions:
        top = suggestions[0]
        emit.line(
            f"top route (score {top['score']:.4f}): {' + '.join(top['reactants'])}"
        )
    emit.path(analysis_path, role="analysis")
    emit.flush()
