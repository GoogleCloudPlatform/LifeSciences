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

"""Co-Scientist tournament ingest and analysis.

Two phases (docs/tool-design-guidance.md §3):

  ingest  — phase 1. Normalises a tournament export into a stable Layer 0
            artifact plus a provenance sidecar. The export itself is the
            expensive, non-reproducible act (~2h of tournament compute);
            we treat the file as the fetched payload and record its
            checksum so a reviewer can confirm which export a finding
            rests on.
  analyze — phase 2. Reads the normalised artifact, applies the
            `coscientist` threshold set, emits `.analysis.json` and a
            bounded summary.

  show    — a bounded reader for one idea's prose sections, written to a
            file rather than streamed into context.

## Why keys are resolved structurally

The tournament export is a serialised JavaScript object whose non-obvious
keys are minifier output: the idea list is `Ur` in the exports we hold,
`gs` in an earlier one; the executive report is `BVa`, previously `eOa`.
These names are not stable across builds and carry no meaning.

Binding to them by literal name is what broke the previous
implementation: against both real exports it reported "No ideas found",
and `overview` silently dropped its entire top-ideas table because an
absent `gs` key read as an empty list. We therefore locate each section
by its *shape* and fail loudly when a required one is absent.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import click

from ..common import (
    AppState,
    beside_or_out,
    load_thresholds,
    out_option,
    output_options,
    pass_state,
    resolve_artifact,
)
from ..core import provenance
from ..core.errors import ArtifactError, SchemaError, UsageError
from ..core.output import Emitter
from ..core.paths import sanitize_slug
from ..core.thresholds import COSCIENTIST_CLAIM_DENOMINATOR

TOOL = "co-scientist"
ARTIFACT_CLASS = "hypotheses"

# Attribute names that have carried the target gene across exports.
_GENE_ATTRS = ("Target Gene", "Gene Symbol", "Gene", "Target")

_INACCURATE = {"INACCURATE", "LEANING_INACCURATE"}


# ---------------------------------------------------------------------------
# Structural resolution
# ---------------------------------------------------------------------------


def _find_ideas(doc: dict) -> tuple[str, list[dict]]:
    """Locate the idea list by shape: a list of dicts with eloRating."""
    candidates = []
    for key, value in doc.items():
        if (
            isinstance(value, list)
            and value
            and isinstance(value[0], dict)
            and "eloRating" in value[0]
            and "ranking" in value[0]
        ):
            candidates.append((key, value))
    if not candidates:
        raise SchemaError(
            "no idea list found in this tournament export",
            detail=(
                "expected a top-level list of objects carrying 'eloRating' and "
                f"'ranking'; top-level keys present: {', '.join(sorted(doc))}"
            ),
            remedy="confirm this is a Co-Scientist tournament export and not another artifact",
        )
    if len(candidates) > 1:
        keys = ", ".join(k for k, _ in candidates)
        raise SchemaError(
            f"ambiguous tournament export: multiple idea-shaped lists ({keys})",
            remedy="report this export shape to the tooling lead",
        )
    return candidates[0]


def _find_report(doc: dict) -> tuple[str | None, dict]:
    """Locate the executive report by shape."""
    for key, value in doc.items():
        if isinstance(value, dict) and (
            "topRankingIdeasSummary" in value or "reviewsOverview" in value
        ):
            return key, value
    return None, {}


def _find_connections(kb: dict) -> dict:
    """Locate the 'unexpected connections' block inside the knowledge base."""
    for value in kb.values():
        if isinstance(value, dict) and "connections" in value:
            return value
    return {}


def _find_report_references(report: dict) -> tuple[str | None, list[dict]]:
    """Locate the references list inside the executive report by shape.

    Shape criterion: a value that is a non-empty list of dicts where the
    first item has a 'title' or 'source' key.  This is structurally
    distinct from the report's markdown sections (dicts with a 'markdown'
    key) and from scalar values.
    """
    for key, value in report.items():
        if (
            isinstance(value, list)
            and value
            and isinstance(value[0], dict)
            and ("title" in value[0] or "source" in value[0])
        ):
            return key, value
    return None, []


def _md(obj: Any, *keys: str) -> str:
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(key)
    if isinstance(cur, dict):
        cur = cur.get("markdown")
    return cur if isinstance(cur, str) else ""


def _gene(idea: dict) -> str | None:
    attrs = {
        a.get("name"): a.get("value")
        for a in idea.get("attributes", [])
        if isinstance(a, dict)
    }
    for name in _GENE_ATTRS:
        if attrs.get(name):
            return str(attrs[name])
    return None


def _attrs(idea: dict) -> dict[str, Any]:
    return {
        a.get("name"): a.get("value")
        for a in idea.get("attributes", [])
        if isinstance(a, dict) and a.get("name")
    }


def _claims(idea: dict) -> list[dict]:
    dv = idea.get("deepVerification")
    if not isinstance(dv, dict):
        return []
    claims = dv.get("claims")
    return claims if isinstance(claims, list) else []


def _extract_recommendation(top_ideas_summary: str) -> str | None:
    """Extract the Recommendation section from topRankingIdeasSummary markdown.

    Looks for a heading like "## Recommendation", "## Best Next Steps",
    or "## Recommendation and Best Next Steps" (case-insensitive, levels
    1-3).  Returns everything from that heading to the next heading of the
    same or higher level, or end of string.
    """
    if not top_ideas_summary:
        return None
    pattern = re.compile(
        r"^(#{1,3}\s+(?:recommendation|best next steps|recommendation and best next steps).*?)"
        r"(?=^#{1,3}\s|\Z)",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(top_ideas_summary)
    return match.group(1).strip() if match else None


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _normalise(doc: dict, source: Path) -> dict:
    """Build the stable Layer 0 record from a raw export."""
    ideas_key, raw_ideas = _find_ideas(doc)
    report_key, report = _find_report(doc)
    kb = doc.get("knowledgeBase") if isinstance(doc.get("knowledgeBase"), dict) else {}
    connections = _find_connections(kb)
    report_refs_key, report_refs_raw = _find_report_references(report)

    ideas = []
    for raw in raw_ideas:
        match = (
            raw.get("matchResult") if isinstance(raw.get("matchResult"), dict) else {}
        )
        claims = _claims(raw)
        reviews_raw = raw.get("reviews", []) or []
        review_refs: list[dict] = []
        for ri, rev in enumerate(reviews_raw):
            if not isinstance(rev, dict):
                continue
            for ref in rev.get("references", []) or []:
                if isinstance(ref, dict):
                    review_refs.append(
                        {
                            "title": ref.get("title", ""),
                            "source": ref.get("source", ""),
                            "review_index": ri,
                        }
                    )
        ideas.append(
            {
                "id": raw.get("id"),
                "ranking": raw.get("ranking"),
                "title": raw.get("title"),
                "category": raw.get("category"),
                "gene": _gene(raw),
                "elo_rating": raw.get("eloRating"),
                "attributes": _attrs(raw),
                "match": {
                    "total": match.get("numTotalMatches"),
                    "won": match.get("numMatchesWon"),
                    "lost": match.get("numMatchesLost"),
                    "win_rate": match.get("winRate"),
                },
                "n_reviews": len(reviews_raw),
                "review_references": review_refs,
                "claims": [
                    {
                        "claim": c.get("claim"),
                        "verdict": (c.get("verdict") or "").upper(),
                        "source_sentence": c.get("sourceSentence"),
                        "reasoning": c.get("reasoning"),
                        "n_references": len(c.get("references", []) or []),
                        "references": [
                            {"title": r.get("title", ""), "source": r.get("source", "")}
                            for r in (c.get("references", []) or [])
                            if isinstance(r, dict)
                        ],
                    }
                    for c in claims
                    if isinstance(c, dict)
                ],
                "prose": {
                    "summary": _md(raw, "summary"),
                    "description": _md(raw, "description"),
                    "reviews_summary": _md(raw, "reviewsSummary"),
                    "verification_summary": _md(
                        raw, "deepVerification", "verificationSummary"
                    ),
                },
            }
        )
    ideas.sort(key=lambda i: (i["ranking"] is None, i["ranking"]))

    stats = doc.get("stats") if isinstance(doc.get("stats"), dict) else {}
    config = doc.get("config") if isinstance(doc.get("config"), dict) else {}

    return {
        "schema": "dde.coscientist.v1",
        "source_file": source.name,
        "tournament": {
            "title": doc.get("title"),
            "goal": doc.get("goal"),
            "state": doc.get("state"),
            "stage": doc.get("stage"),
            "session_id": doc.get("sessionId"),
            "created": doc.get("createTime"),
            "ended": doc.get("dateEnded"),
        },
        "stats": {
            "n_ideas_generated": stats.get("numIdeas"),
            "n_categories": stats.get("numCategories"),
            "highest_elo": stats.get("highestEloRating"),
            "input_tokens": stats.get("inputTokenCount"),
            "output_tokens": stats.get("outputTokenCount"),
        },
        "preferences": config.get("preferences", []),
        "ideas": ideas,
        "knowledge_base": {
            "summary": _md(kb, "knowledgeSummary"),
            "n_references": len(kb.get("references", []) or []),
            "references": [
                {"title": r.get("title", ""), "source": r.get("source", "")}
                for r in (kb.get("references", []) or [])
                if isinstance(r, dict)
            ],
            "n_learned_claims": len(kb.get("learnedClaims", []) or []),
            "connections_summary": _md(connections, "summary"),
            "n_connections": len(connections.get("connections", []) or []),
            "connections": [
                {
                    "title": c.get("title", c.get("name", "")),
                    "description": c.get("description", c.get("markdown", "")),
                }
                for c in (connections.get("connections", []) or [])
                if isinstance(c, dict)
            ],
        },
        "report": {
            "overview": _md(report, "overview"),
            "top_ideas_summary": _md(report, "topRankingIdeasSummary"),
            "reviews_overview": _md(report, "reviewsOverview"),
            "references": [
                {"title": r.get("title", ""), "source": r.get("source", "")}
                for r in report_refs_raw
                if isinstance(r, dict)
            ],
        },
        "_resolved_keys": {
            "ideas": ideas_key,
            "report": report_key,
            "report_references": report_refs_key,
        },
    }


def _slug(text: str | None, fallback: str) -> str:
    if not text:
        return fallback
    keep = [c.lower() if c.isalnum() else "-" for c in text]
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:60] or fallback


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def coscientist() -> None:
    """Co-Scientist tournament exports: ingest, analyze, read."""


@coscientist.command()
@click.argument("export_file")
@click.option(
    "--top",
    type=int,
    default=None,
    help="Keep only the top N ideas by ranking. Default: all ideas in the export.",
)
@out_option
@output_options
@pass_state
def ingest(
    state: AppState,
    export_file: str,
    top: int | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Normalise a tournament export into a Layer 0 artifact + sidecar.

    EXPORT_FILE is the raw Co-Scientist JSON export. Unlike the paths
    taken by `analyze` and `show`, this one names a file that has not
    entered the project yet, so it is resolved against the current
    directory as well as the project root.

    By default all ideas in the export are carried into the normalised
    artifact. Pass --top N to keep only the highest-ranked N ideas; the
    coscientist.partial_export relay fires when --top reduces the set,
    so downstream consumers know the artifact is a filtered subset.
    """
    if top is not None and top < 1:
        raise UsageError(
            f"--top must be at least 1, got {top}",
            remedy="omit --top to keep all ideas, or pass a positive integer",
        )
    source = Path(export_file).expanduser()
    project = state.project()
    if not source.is_absolute():
        candidates = [Path.cwd() / source, project.root / source]
        source = next((c for c in candidates if c.is_file()), candidates[0])
    if not source.is_file():
        raise ArtifactError(
            f"tournament export not found: {export_file}",
            detail=f"looked in {Path.cwd()} and {project.root}",
            remedy="pass an absolute path to the raw Co-Scientist export",
        )
    source = source.resolve()
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)

    doc = provenance.read_json(source, "tournament export")
    if not isinstance(doc, dict):
        raise SchemaError(
            "tournament export must be a JSON object",
            detail=f"got {type(doc).__name__}",
        )

    record = _normalise(doc, source)

    if not record["ideas"]:
        raise SchemaError(
            "tournament export contains an idea list but it is empty",
            remedy="confirm the export completed; an empty result is never analysed",
        )

    # Apply --top filtering before writing the normalised artifact.
    # Ideas are already sorted by ranking in _normalise(), so slicing
    # keeps the highest-ranked ones.
    n_in_export = len(record["ideas"])
    if top is not None and top < n_in_export:
        record["ideas"] = record["ideas"][:top]

    session = record["tournament"].get("session_id")
    name = (
        f"cs-{sanitize_slug(session)}"
        if session
        else f"cs-{_slug(record['tournament'].get('title'), 'tournament')}"
    )

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="ingest",
        endpoint=None,
        parameters={"export_file": str(source), "top": top},
    )

    # Preserve the export verbatim alongside the normalised view: the
    # normalisation is lossy and the reviewer must be able to reach the
    # original bytes.
    verbatim = target_dir / f"{name}.export.json"
    verbatim.write_bytes(source.read_bytes())

    normalised = target_dir / f"{name}.tournament.json"
    normalised.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    keys = record["_resolved_keys"]
    sidecar.note("resolved_keys", keys)
    sidecar.note("source_sha256", provenance.sha256_file(source))
    sidecar.warn(
        f"Idea list resolved structurally from minified key {keys['ideas']!r}; "
        "these key names are not stable across Co-Scientist builds."
    )

    generated = record["stats"].get("n_ideas_generated")

    # The partial_export relay fires when the normalised artifact is a
    # genuine subset — either because the export itself was pre-filtered
    # by the Co-Scientist platform, or because the caller used --top to
    # reduce the set.  It must never fire unconditionally (§5.1, §8):
    # a relay that fires on every invocation trains the reader past the
    # conditional relays beside it.
    user_filtered = top is not None and top < n_in_export
    export_filtered = bool(generated and n_in_export < generated)

    if user_filtered and export_filtered:
        sidecar.warn(
            f"Normalised artifact carries {len(record['ideas'])} ideas "
            f"(--top {top} applied to an export that itself carried only "
            f"{n_in_export} of {generated} generated).",
            code="coscientist.partial_export",
        )
    elif user_filtered:
        sidecar.warn(
            f"--top {top} retained {len(record['ideas'])} of "
            f"{n_in_export} ideas in the export; lower-ranked ideas "
            f"are not present in the normalised artifact.",
            code="coscientist.partial_export",
        )
    elif export_filtered:
        sidecar.warn(
            f"Export carries only the top {len(record['ideas'])} ideas of "
            f"{generated} generated; rankings below the cut are not present.",
            code="coscientist.partial_export",
        )

    sidecar.add_output(verbatim)
    sidecar.add_output(normalised)
    meta = sidecar.write(target_dir / f"{name}.meta.json")

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("ideas", len(record["ideas"]))
    emit.data("warnings", sidecar.warnings)
    emit.path(project.relative(verbatim), "export")
    emit.path(project.relative(normalised), "normalised")
    emit.path(project.relative(meta), "sidecar")
    emit.flush()


@coscientist.command()
@click.argument("artifact")
@click.option(
    "--elo-decisive-gap", type=float, default=None, help="Override threshold."
)
@click.option("--min-win-rate", type=float, default=None, help="Override threshold.")
@click.option(
    "--max-contradicted-claims", type=int, default=None, help="Override threshold."
)
@out_option
@output_options
@pass_state
def analyze(
    state: AppState,
    artifact: str,
    elo_decisive_gap: float | None,
    min_win_rate: float | None,
    max_contradicted_claims: int | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Apply tournament thresholds to an ingested artifact.

    ARTIFACT is the `.tournament.json` produced by `ingest`.
    """
    project = state.project()
    path = resolve_artifact(state, artifact, "normalised tournament")
    record = provenance.read_json(path, "normalised tournament")
    if record.get("schema") != "dde.coscientist.v1":
        raise SchemaError(
            f"{path.name} is not a normalised tournament artifact",
            detail=f"expected schema dde.coscientist.v1, got {record.get('schema')!r}",
            remedy=(
                "this artifact's schema is "
                f"{record.get('schema')!r}; use the matching strategy's "
                "analyzer, or `dde hypothesis analyze` for cross-strategy reads"
            ),
        )

    thresholds = load_thresholds(
        state,
        "coscientist",
        {
            "elo_decisive_gap": elo_decisive_gap,
            "min_win_rate": min_win_rate,
            "max_contradicted_claims": max_contradicted_claims,
        },
    )
    gap_cutoff = thresholds.get("elo_decisive_gap")
    min_win = thresholds.get("min_win_rate")
    max_bad = thresholds.get("max_contradicted_claims")
    min_matches = thresholds.get("min_matches")

    ideas = record["ideas"]
    ranked = [i for i in ideas if i.get("elo_rating") is not None]
    ranked.sort(key=lambda i: i["elo_rating"], reverse=True)

    per_idea = []
    for idea in ranked:
        claims = idea["claims"]
        n_claims = len(claims)
        n_bad = sum(1 for c in claims if c["verdict"] in _INACCURATE)
        win_rate = idea["match"].get("win_rate")
        total_matches = idea["match"].get("total")

        advisories = []
        if n_bad > max_bad:
            advisories.append(
                f"{n_bad} deep-verification claim(s) contradicted "
                f"(> max_contradicted_claims)"
            )
        if win_rate is not None and win_rate < min_win:
            advisories.append(f"win rate {win_rate:.0%} below min_win_rate")
        if total_matches is not None and total_matches < min_matches:
            advisories.append(
                f"only {total_matches} matches played (< min_matches); "
                "ranking is not well supported"
            )
        if n_claims == 0:
            advisories.append(
                "no deep-verification claims present for this idea; absence of "
                "flagged claims is not evidence the idea was verified"
            )

        per_idea.append(
            {
                "ranking": idea["ranking"],
                "gene": idea["gene"],
                "title": idea["title"],
                "elo_rating": idea["elo_rating"],
                "win_rate": win_rate,
                "n_matches": total_matches,
                "n_claims_reported": n_claims,
                "n_contradicted_claims": n_bad,
                "advisories": advisories,
            }
        )

    elo_gap = None
    leader_clear = None
    if len(ranked) >= 2:
        elo_gap = ranked[0]["elo_rating"] - ranked[1]["elo_rating"]
        leader_clear = elo_gap >= gap_cutoff

    flagged = [i for i in per_idea if i["advisories"]]
    verdict_counts = Counter(
        c["verdict"] for idea in ideas for c in idea["claims"] if c["verdict"]
    )

    if not ranked:
        verdict = "unrankable"
    elif leader_clear is None:
        verdict = "single-candidate"
    elif leader_clear and not per_idea[0]["advisories"]:
        verdict = "clear-leader"
    elif leader_clear:
        verdict = "leader-with-advisories"
    else:
        verdict = "no-clear-leader"

    assessment = {
        "verdict": verdict,
        "leader": {
            "gene": per_idea[0]["gene"],
            "ranking": per_idea[0]["ranking"],
            "elo_rating": per_idea[0]["elo_rating"],
        }
        if per_idea
        else None,
        "elo_gap_to_runner_up": round(elo_gap, 2) if elo_gap is not None else None,
        "leader_gap_is_decisive": leader_clear,
        "n_ideas_flagged": len(flagged),
        "ideas": per_idea,
    }

    metrics = {
        "n_ideas": len(ideas),
        "n_ideas_generated": record["stats"].get("n_ideas_generated"),
        "elo_range": [ranked[-1]["elo_rating"], ranked[0]["elo_rating"]]
        if ranked
        else None,
        "claim_verdicts": dict(verdict_counts),
        "n_claims_reported_total": sum(len(i["claims"]) for i in ideas),
    }

    # The denominator caveat is a standing property of how the tournament
    # reports claims (true on every export), not a conditional warning
    # about a particular run's result.  It was an unconditional relay
    # (coscientist.claim_denominator) that fired on every analysis — a
    # signal that never varies carries no information and trains the
    # reader past the conditional relays beside it (partial_export).
    # Moved to a sidecar field on .analysis.json per the always-true rule
    # (tool-design-guidance §8, exit 2: relabel and move).  The finding
    # author reading the artifact still has the information; it no longer
    # competes with partial_export in the relay channel.
    metrics["claim_denominator"] = COSCIENTIST_CLAIM_DENOMINATOR

    # -- Review recommendation ------------------------------------------------
    recommendation = {
        "section": _extract_recommendation(
            record["report"].get("top_ideas_summary", "")
        ),
        "reviews_overview_available": bool(record["report"].get("reviews_overview")),
        "top_ideas_summary_available": bool(record["report"].get("top_ideas_summary")),
    }
    assessment["recommendation"] = recommendation

    # -- Assessment core (dde.hypothesis-assessment.v1) -------------------------
    # Vendor-neutral assessment block emitted alongside all existing keys.
    # Readers that understand only the shared core can ignore the rest;
    # existing consumers ignore this key and read the coscientist-specific
    # fields they already depend on.
    source_sha256 = provenance.sha256_file(path)
    assessment_candidates = []
    for idea in ideas:
        elo = idea.get("elo_rating")
        assessment_candidates.append(
            {
                "candidate_id": idea.get("id"),
                "statement": idea.get("title"),
                "rank": idea.get("ranking"),
                "score": (
                    {"value": elo, "basis": "coscientist-elo@1.1"}
                    if elo is not None
                    else None
                ),
                "origin": "generated",
            }
        )
    assessment["assessment_core"] = {
        "schema": "dde.hypothesis-assessment.v1",
        "strategy": "co-scientist",
        "source_artifact": str(project.relative(path)),
        "source_sha256": source_sha256,
        "candidates": assessment_candidates,
    }

    # Carry forward any relays recorded at ingest (e.g. partial_export).
    relays: list[dict[str, str]] = []
    meta_path = path.with_name(path.name.replace(".tournament.json", ".meta.json"))
    if meta_path.is_file():
        ingest_meta = provenance.read_json(meta_path, "provenance sidecar")
        for item in ingest_meta.get("mandatory_relays", []) or []:
            if not any(r["code"] == item.get("code") for r in relays):
                relays.append(item)

    # Mandatory relay: review recommendation available.
    # Fires when the export contains a structured recommendation section.
    # Per tool-design-guidance section 8, conditional relays must not fire
    # unconditionally — this one fires only when section is non-None.
    if recommendation["section"]:
        relay_code = "coscientist.review_recommendation_available"
        if not any(r["code"] == relay_code for r in relays):
            relays.append(
                provenance.relay(
                    relay_code,
                    "Co-scientist tournament produced a review recommendation. "
                    "Address it before proceeding with target selection.",
                )
            )

    # Mandatory relay: leader has worst contradiction profile.
    # Fires when the top-ranked idea has the highest (or joint-highest)
    # contradicted-claim count among all candidates AND at least one
    # contradicted claim.  This guards against recommending a target whose
    # foundational claims are the most disputed in the tournament.
    # (leader-contradiction relay from pre-migration design review).
    if len(per_idea) >= 2 and per_idea[0]["n_contradicted_claims"] > 0:
        leader_bad = per_idea[0]["n_contradicted_claims"]
        max_bad_any = max(i["n_contradicted_claims"] for i in per_idea)
        if leader_bad >= max_bad_any:
            relay_code = "coscientist.leader_worst_contradiction_profile"
            if not any(r["code"] == relay_code for r in relays):
                leader_gene = per_idea[0]["gene"] or "(unknown)"
                relays.append(
                    provenance.relay(
                        relay_code,
                        f"The recommended idea ({leader_gene}) has the worst "
                        f"contradiction profile in the tournament: "
                        f"{leader_bad} contradicted claim(s), the highest "
                        f"(or joint-highest) among all candidates. The Science "
                        f"Lead must acknowledge this finding, justify proceeding "
                        f"with this target, and consider a fast-fail foundational "
                        f"claim check before committing a full cohort.",
                    )
                )
            assessment["leader_has_worst_contradiction_profile"] = True

    analysis_path = beside_or_out(
        state, path, path.name.replace(".tournament.json", ".analysis.json"), out
    )
    provenance.write_analysis(
        analysis_path,
        source=path,
        threshold_set=thresholds.tag,
        thresholds_applied=thresholds.applied(),
        threshold_sources=thresholds.sources(),
        threshold_provenance=thresholds.provenance,
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("assessment", assessment)
    emit.data("metrics", metrics)
    emit.data("mandatory_relays", relays)
    emit.data("threshold_set", thresholds.tag)

    title = record["tournament"].get("title") or "(untitled)"
    emit.line(f"Tournament: {title[:70]}")
    emit.line(f"Verdict: {verdict}  [threshold_set {thresholds.tag}]")
    if per_idea:
        lead = per_idea[0]
        gap = f", +{elo_gap:.0f} ELO over #2" if elo_gap is not None else ""
        emit.line(f"Leader: {lead['gene']} (ELO {lead['elo_rating']:.0f}{gap})")
    emit.line(
        f"Ideas: {len(ideas)} carried of {record['stats'].get('n_ideas_generated')} generated"
    )
    if verdict_counts:
        emit.line(
            "Disputed claims: "
            + ", ".join(f"{v} {k.lower()}" for k, v in verdict_counts.most_common())
            + " (exception list, not a census — see analysis advisories)"
        )
    emit.line(f"Flagged ideas: {len(flagged)}")
    for idea in flagged[:5]:
        emit.line(f"  #{idea['ranking']} {idea['gene']}: {idea['advisories'][0]}")

    # Advisory: suggest foundational-claim-check template when the leader
    # has contradicted claims.  This is human-readable output only — does
    # not change the analysis engine, scoring, or machine-readable data.
    if (
        per_idea
        and per_idea[0]["n_contradicted_claims"] > 0
        and verdict in ("leader-with-advisories", "no-clear-leader")
    ):
        emit.line("")
        emit.line("Consider: foundational-claim-check template before full cohort")
        emit.line("  See tools/templates/work-orders/foundational-claim-check.yaml")

    if recommendation["section"]:
        emit.line("")
        emit.line("--- Review Recommendation ---")
        preview = recommendation["section"][:500]
        if len(recommendation["section"]) > 500:
            preview += "..."
        emit.line(preview)
        emit.line("")
        emit.line("Full recommendation available in the analysis artifact.")
    elif recommendation["top_ideas_summary_available"]:
        emit.line("")
        emit.line(
            "Review summary available but no structured recommendation section found."
        )
        emit.line(
            "See the topRankingIdeasSummary section in the export for the full review."
        )

    emit.path(project.relative(analysis_path), "analysis")
    emit.flush()


@coscientist.command()
@click.argument("artifact")
@click.option("--rank", type=int, help="Select idea by ranking.")
@click.option("--gene", help="Select idea by target gene symbol.")
@click.option(
    "--section",
    default="summary",
    type=click.Choice(
        ["summary", "description", "reviews-summary", "verification-summary", "all"]
    ),
    help="Prose section to extract.",
)
@out_option
@output_options
@pass_state
def show(
    state: AppState,
    artifact: str,
    rank: int | None,
    gene: str | None,
    section: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Write one idea's prose to a file. Prints the path, not the prose.

    Long-form content goes to a file so it does not consume context
    (§6). Read the file when you need the text.
    """
    if rank is None and gene is None:
        raise UsageError(
            "select an idea with --rank or --gene",
            remedy="run `dde coscientist analyze` to see available ranks and genes",
        )

    project = state.project()
    path = resolve_artifact(state, artifact, "normalised tournament")
    record = provenance.read_json(path, "normalised tournament")
    ideas = record.get("ideas", [])

    chosen = None
    if rank is not None:
        chosen = next((i for i in ideas if i.get("ranking") == rank), None)
        if chosen is None:
            raise UsageError(
                f"no idea with rank {rank}",
                detail="available ranks: "
                + ", ".join(str(i.get("ranking")) for i in ideas),
            )
    else:
        # A single idea may name several genes ("CCNE1, CCNE2"), so match
        # against each member rather than the whole field.
        target = gene.strip().upper()
        matches = [
            i
            for i in ideas
            if target
            in {
                g.strip().upper() for g in (i.get("gene") or "").split(",") if g.strip()
            }
        ]
        if not matches:
            raise UsageError(
                f"no idea with target gene {gene!r}",
                detail="available: "
                + "; ".join(
                    f"#{i.get('ranking')} {i.get('gene')}"
                    for i in ideas
                    if i.get("gene")
                ),
            )
        if len(matches) > 1:
            raise UsageError(
                f"gene {gene!r} appears in {len(matches)} ideas",
                detail="; ".join(
                    f"#{i.get('ranking')} {i.get('gene')}" for i in matches
                ),
                remedy="select by --rank instead",
            )
        chosen = matches[0]

    prose = chosen["prose"]
    wanted = (
        ["summary", "description", "reviews_summary", "verification_summary"]
        if section == "all"
        else [section.replace("-", "_")]
    )

    lines = [
        f"# Rank #{chosen['ranking']}: {chosen['title']}",
        "",
        f"- Target gene: {chosen['gene']}",
        f"- Category: {chosen['category']}",
        f"- ELO: {chosen['elo_rating']}",
        "",
    ]
    for key in wanted:
        body = prose.get(key) or "_(section not present in this export)_"
        lines.append(f"## {key.replace('_', ' ').title()}")
        lines.append("")
        lines.append(body)
        lines.append("")

    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)
    stem = path.name.replace(".tournament.json", "")
    label = _slug(chosen.get("gene") or str(chosen.get("ranking")), "idea")
    dest = target_dir / f"{stem}.idea-{label}.{section}.md"
    dest.write_text("\n".join(lines), encoding="utf-8")

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("rank", chosen["ranking"])
    emit.data("gene", chosen["gene"])
    emit.line(f"Rank #{chosen['ranking']} {chosen['gene']}: {chosen['title'][:60]}")
    emit.line(
        f"Wrote {section} section ({sum(len(prose.get(k) or '') for k in wanted)} chars)."
    )
    emit.path(project.relative(dest), "prose")
    emit.flush()


# ---------------------------------------------------------------------------
# Guards — extracted so tests can call them directly
# ---------------------------------------------------------------------------


def _validate_schema_tag(record: dict, name: str) -> None:
    """Raise ``SchemaError`` if *record* lacks the normalised schema tag."""
    if record.get("schema") != "dde.coscientist.v1":
        raise SchemaError(
            f"{name} is not a normalised tournament artifact",
            detail=f"expected schema dde.coscientist.v1, got {record.get('schema')!r}",
            remedy="run `dde coscientist ingest` on the raw export first",
        )


def _require_expanded_fields(record: dict, field: str) -> None:
    """Raise ``SchemaError`` if the normalised record's knowledge_base lacks *field*.

    Old artifacts ingested before the normalisation expansion will be missing
    the full-object lists (``references``, ``connections``).  This guard
    catches them early with a clear remedy.
    """
    if field not in record.get("knowledge_base", {}):
        raise SchemaError(
            f"artifact was ingested before {field} data was carried; "
            "re-run dde coscientist ingest",
        )


def _require_report_content(rpt: dict) -> None:
    """Raise ``SchemaError`` if the report dict has no content at all."""
    if not any(
        rpt.get(k) for k in ("overview", "top_ideas_summary", "reviews_overview")
    ):
        raise SchemaError(
            "no executive report found in this tournament artifact",
            remedy="confirm this tournament produced an executive report",
        )


def _require_nonempty_ideas(ideas: list[dict], name: str) -> None:
    """Raise ``SchemaError`` if *ideas* is empty."""
    if not ideas:
        raise SchemaError(
            f"tournament artifact {name} contains no ideas",
        )


def _render_knowledge_text(kb: dict, section: str) -> str:
    """Render knowledge-base sections as markdown text.

    Extracted from the ``knowledge`` Click command so that the rendering
    logic is independently testable (in particular, that empty KB data
    produces placeholder text rather than raising).
    """
    lines: list[str] = []
    if section in ("summary", "full"):
        lines.append("# Knowledge Base Summary\n")
        summary = kb.get("summary", "")
        lines.append(summary if summary else "(No knowledge summary available)")
        lines.append("")

    if section in ("connections", "full"):
        lines.append("# Unexpected Connections Analysis\n")
        conn_summary = kb.get("connections_summary", "")
        lines.append(
            conn_summary if conn_summary else "(No connections analysis available)"
        )
        lines.append("")
        conns = kb.get("connections", [])
        if conns:
            lines.append(f"## Individual Connections ({len(conns)} total)\n")
            for j, conn in enumerate(conns, 1):
                lines.append(f"### {j}. {conn.get('title', f'Connection {j}')}")
                desc = conn.get("description", "")
                if desc:
                    lines.append(desc)
                lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shared loader for read-only subcommands operating on normalised artifacts
# ---------------------------------------------------------------------------


def _load_normalised(state: AppState, artifact: str) -> tuple[Path, dict]:
    """Load and validate a normalised tournament artifact.

    Returns the resolved path and the parsed record.  Raises
    ``ArtifactError`` if the file is missing and ``SchemaError`` if the
    schema tag is wrong.
    """
    path = resolve_artifact(state, artifact, "normalised tournament")
    record = provenance.read_json(path, "normalised tournament")
    _validate_schema_tag(record, path.name)
    return path, record


# ---------------------------------------------------------------------------
# references
# ---------------------------------------------------------------------------


@coscientist.command()
@click.argument("artifact")
@click.option(
    "--source",
    type=click.Choice(["kb", "report", "reviews", "claims"]),
    default=None,
    help="Reference pool to show (default: all).",
)
@click.option("--rank", type=int, default=None, help="Filter to idea at this rank.")
@click.option("--gene", default=None, help="Filter to idea with this gene symbol.")
@click.option("--search", default=None, help="Case-insensitive search in title/source.")
@out_option
@output_options
@pass_state
def references(
    state: AppState,
    artifact: str,
    source: str | None,
    rank: int | None,
    gene: str | None,
    search: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Extract references from a normalised tournament artifact.

    Collects references from the selected pools (default: all), optionally
    filtered by --search (case-insensitive match on title/source) and
    --rank / --gene (for idea-scoped pools).  Writes the full reference
    list to a file and emits a bounded summary + path.
    """
    project = state.project()
    path, record = _load_normalised(state, artifact)

    # Check for expanded normalisation fields (backward compat with old artifacts).
    _require_expanded_fields(record, "references")

    ideas = record.get("ideas", [])

    # Scope ideas by --rank / --gene when filtering idea-level pools.
    if rank is not None:
        scoped = [i for i in ideas if i.get("ranking") == rank]
        if not scoped:
            raise UsageError(
                f"no idea with rank {rank}",
                detail="available ranks: "
                + ", ".join(str(i.get("ranking")) for i in ideas),
            )
        ideas = scoped
    elif gene is not None:
        target = gene.strip().upper()
        scoped = [
            i
            for i in ideas
            if target
            in {
                g.strip().upper() for g in (i.get("gene") or "").split(",") if g.strip()
            }
        ]
        if not scoped:
            raise UsageError(f"no idea with gene {gene!r}")
        ideas = scoped

    # Collect reference pools.
    ref_pools: list[tuple[str, list[dict]]] = []

    if source is None or source == "kb":
        ref_pools.append(
            ("Knowledge Base", record.get("knowledge_base", {}).get("references", []))
        )

    if source is None or source == "report":
        ref_pools.append(
            ("Executive Report", record.get("report", {}).get("references", []))
        )

    if source is None or source == "reviews":
        for idea in ideas:
            g = idea.get("gene") or "N/A"
            r = idea.get("ranking", "?")
            rev_refs = idea.get("review_references", [])
            if rev_refs:
                ref_pools.append((f"Idea #{r} ({g}) Reviews", rev_refs))

    if source is None or source == "claims":
        for idea in ideas:
            g = idea.get("gene") or "N/A"
            r = idea.get("ranking", "?")
            for claim in idea.get("claims", []):
                claim_refs = claim.get("references", [])
                if claim_refs:
                    ref_pools.append((f"Idea #{r} ({g}) Claim", claim_refs))

    # Apply search filter and flatten.
    all_refs: list[dict] = []
    for pool_name, refs in ref_pools:
        for ref in refs:
            title = ref.get("title", "")
            src = ref.get("source", "")
            if (
                search
                and search.lower() not in title.lower()
                and search.lower() not in src.lower()
            ):
                continue
            all_refs.append({"pool": pool_name, "title": title, "source": src})

    # Write output file.
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)
    stem = path.name.replace(".tournament.json", "")

    if as_json:
        dest = target_dir / f"{stem}.references.json"
        dest.write_text(json.dumps(all_refs, indent=2) + "\n", encoding="utf-8")
    else:
        lines = ["# References\n"]
        if not all_refs:
            qualifier = f" matching '{search}'" if search else ""
            lines.append(f"No references found{qualifier}.\n")
        else:
            current_pool = None
            for ref in all_refs:
                if ref["pool"] != current_pool:
                    current_pool = ref["pool"]
                    lines.append(f"\n## {current_pool}\n")
                lines.append(f"- [{ref['title'] or 'Untitled'}]({ref['source']})")
            lines.append(f"\n---\n**Total references**: {len(all_refs)}\n")
        dest = target_dir / f"{stem}.references.md"
        dest.write_text("\n".join(lines), encoding="utf-8")

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("total", len(all_refs))
    if not all_refs:
        qualifier = f" matching '{search}'" if search else ""
        emit.line(f"No references found{qualifier}.")
    else:
        emit.line(f"References: {len(all_refs)} found")
        if search:
            emit.line(f"Search: '{search}'")
    emit.path(project.relative(dest), "references")
    emit.flush()


# ---------------------------------------------------------------------------
# knowledge
# ---------------------------------------------------------------------------


@coscientist.command()
@click.argument("artifact")
@click.option(
    "--section",
    default="full",
    type=click.Choice(["summary", "connections", "full"]),
    help="Section to extract (default: full).",
)
@out_option
@output_options
@pass_state
def knowledge(
    state: AppState,
    artifact: str,
    section: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Extract knowledge base content from a normalised tournament artifact.

    Renders the requested KB section(s) to a file and emits a bounded
    summary + path.
    """
    project = state.project()
    path, record = _load_normalised(state, artifact)

    kb = record.get("knowledge_base", {})

    # Check for expanded normalisation fields.
    _require_expanded_fields(record, "connections")

    stem = path.name.replace(".tournament.json", "")
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)

    if as_json:
        payload: dict[str, Any] = {}
        if section in ("summary", "full"):
            payload["summary"] = kb.get("summary", "")
        if section in ("connections", "full"):
            payload["connections_summary"] = kb.get("connections_summary", "")
            payload["connections"] = kb.get("connections", [])
        dest = target_dir / f"{stem}.knowledge.{section}.json"
        dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    else:
        text = _render_knowledge_text(kb, section)
        dest = target_dir / f"{stem}.knowledge.{section}.md"
        dest.write_text(text, encoding="utf-8")

    n_conns = len(kb.get("connections", []))
    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("n_references", kb.get("n_references", 0))
    emit.data("n_connections", n_conns)
    emit.line(
        f"Knowledge base: {kb.get('n_references', 0)} references, {n_conns} connections"
    )
    emit.path(project.relative(dest), "knowledge")
    emit.flush()


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


@coscientist.command()
@click.argument("artifact")
@click.option(
    "--section",
    default="all",
    type=click.Choice(["overview", "top-ideas", "reviews", "recommendation", "all"]),
    help="Section to extract (default: all).",
)
@out_option
@output_options
@pass_state
def report(
    state: AppState,
    artifact: str,
    section: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Extract executive report sections from a normalised tournament artifact.

    Renders the requested report section(s) to a file and emits a bounded
    summary + path.
    """
    project = state.project()
    path, record = _load_normalised(state, artifact)

    rpt = record.get("report", {})

    # Report entirely empty.
    _require_report_content(rpt)

    stem = path.name.replace(".tournament.json", "")
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)

    if as_json:
        payload = {}
        if section in ("overview", "all"):
            payload["overview"] = rpt.get("overview", "")
        if section in ("top-ideas", "all"):
            payload["top_ideas_summary"] = rpt.get("top_ideas_summary", "")
        if section in ("reviews", "all"):
            payload["reviews_overview"] = rpt.get("reviews_overview", "")
        if section in ("recommendation", "all"):
            payload["recommendation"] = _extract_recommendation(
                rpt.get("top_ideas_summary", "")
            )
        dest = target_dir / f"{stem}.report.{section}.json"
        dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    else:
        lines: list[str] = []

        if section in ("overview", "all"):
            lines.append("# Executive Overview\n")
            lines.append(rpt.get("overview", "") or "_(section not present)_")
            lines.append("")

        if section in ("top-ideas", "all"):
            lines.append("# Top Ranking Ideas Summary\n")
            lines.append(rpt.get("top_ideas_summary", "") or "_(section not present)_")
            lines.append("")

        if section in ("reviews", "all"):
            lines.append("# Reviews Overview\n")
            lines.append(rpt.get("reviews_overview", "") or "_(section not present)_")
            lines.append("")

        if section in ("recommendation", "all"):
            rec = _extract_recommendation(rpt.get("top_ideas_summary", ""))
            if rec:
                lines.append("# Recommendation\n")
                lines.append(rec)
                lines.append("")
            else:
                lines.append(
                    "No structured recommendation section found in top-ideas summary."
                )
                lines.append("")

        ref_count = len(rpt.get("references", []))
        if ref_count:
            lines.append(f"---\n*{ref_count} references cited in executive report*\n")

        dest = target_dir / f"{stem}.report.{section}.md"
        dest.write_text("\n".join(lines), encoding="utf-8")

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data(
        "sections_available",
        {
            "overview": bool(rpt.get("overview")),
            "top_ideas_summary": bool(rpt.get("top_ideas_summary")),
            "reviews_overview": bool(rpt.get("reviews_overview")),
        },
    )
    emit.line(f"Report section: {section}")
    emit.path(project.relative(dest), "report")
    emit.flush()


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


@coscientist.command()
@click.argument("artifact1")
@click.argument("artifact2")
@click.option(
    "--aspect",
    default="genes",
    type=click.Choice(["genes", "rankings", "elo"]),
    help="What to compare (default: genes).",
)
@out_option
@output_options
@pass_state
def compare(
    state: AppState,
    artifact1: str,
    artifact2: str,
    aspect: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Compare two normalised tournament artifacts side by side.

    Requires prior ``ingest`` on both files.  Produces a side-by-side
    comparison written to a file and emits a bounded summary + path.
    This is a read-only viewer — it does not produce analysis artifacts
    or provenance sidecars.
    """
    project = state.project()
    path_a, record_a = _load_normalised(state, artifact1)
    path_b, record_b = _load_normalised(state, artifact2)

    ideas_a = record_a.get("ideas", [])
    ideas_b = record_b.get("ideas", [])

    _require_nonempty_ideas(ideas_a, path_a.name)
    _require_nonempty_ideas(ideas_b, path_b.name)

    title_a = record_a.get("tournament", {}).get("title") or "Tournament A"
    title_b = record_b.get("tournament", {}).get("title") or "Tournament B"
    stem_a = path_a.name.replace(".tournament.json", "")
    stem_b = path_b.name.replace(".tournament.json", "")
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)

    if as_json:
        payload = _compare_json(ideas_a, ideas_b, title_a, title_b, aspect)
        dest = target_dir / f"{stem_a}-vs-{stem_b}.compare.{aspect}.json"
        dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    else:
        text = _compare_markdown(ideas_a, ideas_b, title_a, title_b, aspect)
        dest = target_dir / f"{stem_a}-vs-{stem_b}.compare.{aspect}.md"
        dest.write_text(text, encoding="utf-8")

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("aspect", aspect)
    emit.data("n_ideas_a", len(ideas_a))
    emit.data("n_ideas_b", len(ideas_b))
    emit.line(f"Compare ({aspect}): {title_a[:30]} vs {title_b[:30]}")
    emit.line(f"Ideas: {len(ideas_a)} vs {len(ideas_b)}")
    emit.path(project.relative(dest), "comparison")
    emit.flush()


def _compare_json(
    ideas_a: list[dict],
    ideas_b: list[dict],
    title_a: str,
    title_b: str,
    aspect: str,
) -> dict:
    """Build the machine-readable comparison payload."""
    genes_a = {i["gene"] for i in ideas_a if i.get("gene")}
    genes_b = {i["gene"] for i in ideas_b if i.get("gene")}

    if aspect == "genes":
        return {
            "tournament_a": title_a,
            "tournament_b": title_b,
            "shared_genes": sorted(genes_a & genes_b),
            "only_in_a": sorted(genes_a - genes_b),
            "only_in_b": sorted(genes_b - genes_a),
            "ideas_a": [
                {
                    "ranking": i["ranking"],
                    "gene": i["gene"],
                    "elo_rating": i["elo_rating"],
                    "title": i["title"],
                }
                for i in ideas_a
            ],
            "ideas_b": [
                {
                    "ranking": i["ranking"],
                    "gene": i["gene"],
                    "elo_rating": i["elo_rating"],
                    "title": i["title"],
                }
                for i in ideas_b
            ],
        }
    elif aspect == "rankings":
        max_rank = max(
            max((i["ranking"] for i in ideas_a if i["ranking"] is not None), default=0),
            max((i["ranking"] for i in ideas_b if i["ranking"] is not None), default=0),
        )
        rows = []
        for r in range(1, max_rank + 1):
            a = next((i for i in ideas_a if i["ranking"] == r), None)
            b = next((i for i in ideas_b if i["ranking"] == r), None)
            rows.append(
                {
                    "rank": r,
                    "a": {"gene": a["gene"], "title": a["title"]} if a else None,
                    "b": {"gene": b["gene"], "title": b["title"]} if b else None,
                }
            )
        return {"tournament_a": title_a, "tournament_b": title_b, "rankings": rows}
    else:  # elo
        entries = []
        for i in ideas_a:
            entries.append(
                {
                    "source": "A",
                    "gene": i["gene"],
                    "elo_rating": i["elo_rating"],
                    "ranking": i["ranking"],
                    "title": i["title"],
                }
            )
        for i in ideas_b:
            entries.append(
                {
                    "source": "B",
                    "gene": i["gene"],
                    "elo_rating": i["elo_rating"],
                    "ranking": i["ranking"],
                    "title": i["title"],
                }
            )
        entries.sort(key=lambda x: x["elo_rating"] or 0, reverse=True)
        return {
            "tournament_a": title_a,
            "tournament_b": title_b,
            "leaderboard": entries,
        }


def _compare_markdown(
    ideas_a: list[dict],
    ideas_b: list[dict],
    title_a: str,
    title_b: str,
    aspect: str,
) -> str:
    """Render a markdown comparison of two tournament idea sets."""
    lines = [
        "# Tournament Comparison\n",
        f"**Tournament A**: {title_a}",
        f"**Tournament B**: {title_b}\n",
    ]

    if aspect == "genes":
        genes_a: set[str] = set()
        genes_b: set[str] = set()

        lines.append("## Tournament A - Top Ideas")
        lines.append("| Rank | Gene | ELO | Title |")
        lines.append("|------|------|-----|-------|")
        for idea in ideas_a:
            g = idea.get("gene") or "N/A"
            if idea.get("gene"):
                genes_a.add(idea["gene"])
            elo = idea.get("elo_rating")
            elo_s = f"{elo:.0f}" if elo is not None else "N/A"
            lines.append(
                f"| {idea['ranking']} | {g} | {elo_s} | {(idea['title'] or '')[:60]} |"
            )
        lines.append("")

        lines.append("## Tournament B - Top Ideas")
        lines.append("| Rank | Gene | ELO | Title |")
        lines.append("|------|------|-----|-------|")
        for idea in ideas_b:
            g = idea.get("gene") or "N/A"
            if idea.get("gene"):
                genes_b.add(idea["gene"])
            elo = idea.get("elo_rating")
            elo_s = f"{elo:.0f}" if elo is not None else "N/A"
            lines.append(
                f"| {idea['ranking']} | {g} | {elo_s} | {(idea['title'] or '')[:60]} |"
            )
        lines.append("")

        shared = genes_a & genes_b
        only_a = genes_a - genes_b
        only_b = genes_b - genes_a
        lines.append("## Overlap Analysis")
        lines.append(
            f"- **Shared genes**: {', '.join(sorted(shared)) if shared else 'None'}"
        )
        lines.append(
            f"- **Only in A**: {', '.join(sorted(only_a)) if only_a else 'None'}"
        )
        lines.append(
            f"- **Only in B**: {', '.join(sorted(only_b)) if only_b else 'None'}"
        )
        lines.append("")

    elif aspect == "rankings":
        lines.append("## Rankings Comparison\n")
        lines.append("| Rank | Tournament A | Tournament B |")
        lines.append("|------|-------------|-------------|")
        max_rank = max(
            max((i["ranking"] for i in ideas_a if i["ranking"] is not None), default=0),
            max((i["ranking"] for i in ideas_b if i["ranking"] is not None), default=0),
        )
        for r in range(1, max_rank + 1):
            a_name = ""
            for i in ideas_a:
                if i["ranking"] == r:
                    g = i.get("gene") or ""
                    a_name = f"{g}: {(i.get('title') or '')[:50]}"
            b_name = ""
            for i in ideas_b:
                if i["ranking"] == r:
                    g = i.get("gene") or ""
                    b_name = f"{g}: {(i.get('title') or '')[:50]}"
            lines.append(f"| {r} | {a_name} | {b_name} |")
        lines.append("")

    elif aspect == "elo":
        lines.append("## ELO Ratings Comparison\n")
        entries: list[dict] = []
        for idea in ideas_a:
            entries.append(
                {
                    "source": "A",
                    "gene": idea.get("gene") or "N/A",
                    "elo": idea.get("elo_rating") or 0,
                    "ranking": idea.get("ranking", "?"),
                    "title": (idea.get("title") or "")[:50],
                }
            )
        for idea in ideas_b:
            entries.append(
                {
                    "source": "B",
                    "gene": idea.get("gene") or "N/A",
                    "elo": idea.get("elo_rating") or 0,
                    "ranking": idea.get("ranking", "?"),
                    "title": (idea.get("title") or "")[:50],
                }
            )
        entries.sort(key=lambda x: x["elo"], reverse=True)
        lines.append("| Source | Rank | Gene | ELO | Title |")
        lines.append("|--------|------|------|-----|-------|")
        for e in entries:
            elo_s = f"{e['elo']:.0f}" if isinstance(e["elo"], (int, float)) else "N/A"
            lines.append(
                f"| {e['source']} | {e['ranking']} | {e['gene']} | {elo_s} | {e['title']} |"
            )
        lines.append("")

    return "\n".join(lines) + "\n"
