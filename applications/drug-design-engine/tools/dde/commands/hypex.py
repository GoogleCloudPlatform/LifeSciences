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

"""Hypex tournament ingest and analysis.

Two phases (docs/tool-design-guidance.md §3):

  ingest  — phase 1. Walks a completed hypex run directory, validates
            files against the hypex schemas, computes integrity checks
            (what ``hypex validate`` does NOT check — §3.7), normalises
            everything into ``dde.hypex.v1`` JSON, archives the run
            directory as ``.tar.zst``, and writes to
            ``raw/hypotheses/hx-<slug>.hypex.json`` with a sidecar.
  analyze — phase 2. Reads the normalised artifact, applies the
            ``hypex@1.0`` threshold set, computes a verdict, fires
            relay codes conditionally, and emits
            ``dde.hypothesis-assessment.v1`` core alongside the
            analysis.

The coscientist.py command is the closest analogue — study it for the
two-phase pattern, sidecar handling, and emitter output.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
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
from ..core.errors import ArtifactError, SchemaError, ThresholdError
from ..core.output import Emitter
from ..core.paths import is_safe_to_open

TOOL = "hypex"
ARTIFACT_CLASS = "hypotheses"
MAX_INPUT_BYTES = 50 * 1024 * 1024  # 50 MiB per file


# ---------------------------------------------------------------------------
# Slug helper (same pattern as coscientist.py)
# ---------------------------------------------------------------------------


def _slug(text: str | None, fallback: str = "tournament") -> str:
    if not text:
        return fallback
    keep = [c.lower() if c.isalnum() else "-" for c in text]
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:60] or fallback


# ---------------------------------------------------------------------------
# Datastore walking helpers
# ---------------------------------------------------------------------------


def _read_json_file(path: Path, what: str) -> Any:
    """Read a JSON file, returning its parsed content."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SchemaError(
            f"{what} is not valid JSON: {path.name}",
            detail=str(exc),
        ) from exc


def _walk_json_dir(directory: Path) -> list[tuple[Path, dict]]:
    """Walk a directory and read all .json files, returning (path, data) pairs."""
    results = []
    if not directory.is_dir():
        return results
    for f in sorted(directory.iterdir()):
        if f.is_symlink():
            continue  # Do not follow symlinks
        if f.suffix == ".json" and f.is_file():
            if f.stat().st_size > MAX_INPUT_BYTES:
                raise ArtifactError(
                    f"{f.name} exceeds {MAX_INPUT_BYTES // (1024 * 1024)} MiB limit"
                )
            try:
                data = _read_json_file(f, f.name)
                if isinstance(data, dict):
                    results.append((f, data))
            except SchemaError:
                # Schema-invalid files are recorded in _schema_validation
                results.append((f, {}))
    return results


def _validate_hypothesis(data: dict) -> list[str]:
    """Validate a hypothesis file's required fields. Returns errors."""
    errors = []
    required = ["id", "title", "statement", "mechanism", "status", "lineage"]
    for field in required:
        if field not in data:
            errors.append(f"missing required field: {field}")
    return errors


def _validate_match(data: dict) -> list[str]:
    """Validate a match file's required fields. Returns errors."""
    errors = []
    required = ["id", "a", "b", "winner", "margin"]
    for field in required:
        if field not in data:
            errors.append(f"missing required field: {field}")
    return errors


def _validate_review(data: dict) -> list[str]:
    """Validate a review file's required fields. Returns errors."""
    errors = []
    required = ["id", "hypothesis_id", "scores", "verdict"]
    for field in required:
        if field not in data:
            errors.append(f"missing required field: {field}")
    return errors


# ---------------------------------------------------------------------------
# Integrity checks (what ``hypex validate`` does NOT check — §3.7)
# ---------------------------------------------------------------------------


def _compute_integrity(
    hypotheses: dict[str, dict],
    matches: list[dict],
    reviews: list[dict],
    ratings_per_h: dict[str, dict],
    quarantine: dict[str, dict],
) -> dict[str, list[str]]:
    """Compute referential integrity checks the ingest must surface."""
    h_ids = set(hypotheses.keys())

    # Dangling match refs — match.a or match.b not in hypotheses/
    dangling_match_refs = []
    for m in matches:
        for ref_field in ("a", "b"):
            ref = m.get(ref_field)
            if ref and ref != "draw" and ref not in h_ids:
                dangling_match_refs.append(f"{m.get('id', '?')}.{ref_field}={ref}")

    # Dangling review refs — review.hypothesis_id not in hypotheses/
    dangling_review_refs = []
    for r in reviews:
        ref = r.get("hypothesis_id")
        if ref and ref not in h_ids:
            dangling_review_refs.append(f"{r.get('id', '?')}.hypothesis_id={ref}")

    # Dangling lineage parents
    dangling_lineage_parents = []
    for h_id, h in hypotheses.items():
        lineage = h.get("lineage", {})
        parents = lineage.get("parents", []) if isinstance(lineage, dict) else []
        for p in parents:
            if p not in h_ids:
                dangling_lineage_parents.append(f"{h_id}.lineage.parents={p}")

    # ID-filename mismatches — H-0042.json should contain id: "H-0042"
    id_filename_mismatches = []
    for h_id, h in hypotheses.items():
        declared_id = h.get("id")
        if declared_id and declared_id != h_id:
            id_filename_mismatches.append(f"file {h_id}.json declares id={declared_id}")

    # Unrated hypotheses — in hypotheses/ but not in the ratings
    rated_ids = set(ratings_per_h.keys())
    unrated_hypotheses = sorted(h_ids - rated_ids)

    # Quarantine duplicates — present in both hypotheses/ and quarantine/
    quarantine_duplicates = sorted(h_ids & set(quarantine.keys()))

    return {
        "dangling_match_refs": dangling_match_refs,
        "dangling_review_refs": dangling_review_refs,
        "dangling_lineage_parents": dangling_lineage_parents,
        "id_filename_mismatches": id_filename_mismatches,
        "unrated_hypotheses": unrated_hypotheses,
        "quarantine_duplicates": quarantine_duplicates,
    }


# ---------------------------------------------------------------------------
# Build the dde.hypex.v1 record
# ---------------------------------------------------------------------------


def _build_hypex_record(
    run_dir: Path,
    hypotheses: dict[str, dict],
    matches: list[dict],
    reviews: list[dict],
    ratings_data: dict | None,
    quarantine: dict[str, dict],
    integrity: dict[str, list[str]],
    schema_validation: dict,
    termination: dict,
    roster: list[dict],
    pacing: dict | None,
    run_yaml: dict | None,
    progress: dict | None,
) -> dict[str, Any]:
    """Build the full dde.hypex.v1 normalised record."""
    run_id = os.environ.get("DDE_RUN_ID", "")
    work_order_id = os.environ.get("DDE_WORK_ORDER_ID", "")

    # Derive observed counts from the datastore, never from run.yaml
    n_hypotheses = len(hypotheses)
    n_quarantined = len(quarantine)
    n_reviews = len(reviews)
    n_matches = len(matches)

    # Compute epochs observed from matches
    epochs_in_matches = {m.get("epoch", 0) for m in matches}
    n_epochs_observed = max(epochs_in_matches) + 1 if epochs_in_matches else 0

    # Last match ID
    match_ids = [m.get("id", "") for m in matches]
    last_match_id = sorted(match_ids)[-1] if match_ids else None

    # Ratings per hypothesis
    ratings_per_h: dict[str, dict] = {}
    ratings_epoch = 0
    base_rating = 1500.0
    if ratings_data and isinstance(ratings_data.get("ratings"), dict):
        ratings_per_h = ratings_data["ratings"]
        ratings_epoch = ratings_data.get("epoch", 0)
        base_rating = ratings_data.get("base_rating", 1500.0)

    # Declared budgets from run.yaml (carried but non-authoritative)
    declared_budgets = {"max_hypotheses": 0, "max_matches": 0, "max_epochs": 0}
    if run_yaml and isinstance(run_yaml, dict):
        budgets = run_yaml.get("budgets", {})
        if isinstance(budgets, dict):
            declared_budgets["max_hypotheses"] = budgets.get(
                "max_hypotheses", budgets.get("hypotheses", 0)
            )
            declared_budgets["max_matches"] = budgets.get(
                "max_matches", budgets.get("matches", 0)
            )
            declared_budgets["max_epochs"] = budgets.get(
                "max_epochs", budgets.get("epochs", 0)
            )

    # Compute review score aggregates per hypothesis
    review_scores_by_h: dict[str, list[dict]] = {}
    for r in reviews:
        h_id = r.get("hypothesis_id", "")
        scores = r.get("scores", {})
        if isinstance(scores, dict):
            review_scores_by_h.setdefault(h_id, []).append(scores)

    def _avg_scores(h_id: str) -> dict[str, float | None]:
        """Average review scores for a hypothesis."""
        all_scores = review_scores_by_h.get(h_id, [])
        if not all_scores:
            return {
                "correctness": None,
                "novelty": None,
                "testability": None,
                "safety": None,
                "goal_alignment": None,
                "constraint_compliance": None,
            }
        axes = [
            "correctness",
            "novelty",
            "testability",
            "safety",
            "goal_alignment",
            "constraint_compliance",
        ]
        result: dict[str, float | None] = {}
        for axis in axes:
            vals = [s[axis] for s in all_scores if axis in s and s[axis] is not None]
            result[axis] = round(sum(vals) / len(vals), 2) if vals else None
        return result

    # Build per-hypothesis list
    hyp_list = []
    for h_id in sorted(hypotheses.keys()):
        h = hypotheses[h_id]
        rating_rec = ratings_per_h.get(h_id, {})
        avg = _avg_scores(h_id)

        # Citation info from reviews' citation data
        h_reviews = [r for r in reviews if r.get("hypothesis_id") == h_id]
        phantom_count = 0
        verified_count = 0
        total_citations = 0
        suspect_count = 0
        unverified_count = 0
        manifest_present = False

        for r in h_reviews:
            pc = r.get("phantom_citations", [])
            if isinstance(pc, list):
                phantom_count += len(pc)
            vc = r.get("verified_citations", [])
            if isinstance(vc, list):
                verified_count += len(vc)
            if r.get("citation_manifest"):
                manifest_present = True

        # Check for citation manifests in citations/ directory
        citations_dir = run_dir / "citations"
        cite_manifest_path = citations_dir / f"{h_id}.json"
        if cite_manifest_path.is_file() and is_safe_to_open(cite_manifest_path):
            try:
                manifest = _read_json_file(cite_manifest_path, "citation manifest")
                manifest_present = True  # Only after successful parse
                summary = manifest.get("summary", {})
                total_citations = summary.get("total", 0)
                verified_count = max(verified_count, summary.get("verified", 0))
                phantom_count = max(phantom_count, summary.get("phantom", 0))
                suspect_count = summary.get("suspect", 0) if "suspect" in summary else 0
                unverified_count = summary.get("unverified", 0)
            except SchemaError:
                pass  # manifest_present stays False — relay fires

        # Lineage
        lineage = h.get("lineage", {})
        if not isinstance(lineage, dict):
            lineage = {}
        citation_delta = lineage.get("citation_delta", {})
        citation_delta_compliant = (
            citation_delta.get("compliant", True)
            if isinstance(citation_delta, dict)
            else True
        )

        # Composite is only set when --composite was used; default null
        composite_val = None  # populated below if composite data exists

        hyp_list.append(
            {
                "id": h_id,
                "title": h.get("title", ""),
                "status": h.get("status", ""),
                "cluster": h.get("cluster"),
                "elo": rating_rec.get("elo"),
                "matches": rating_rec.get("matches", 0),
                "wins": rating_rec.get("wins", 0),
                "draws": rating_rec.get("draws", 0),
                "composite": composite_val,
                "scores": avg,
                "n_reviews": len(h_reviews),
                "citations": {
                    "total": total_citations,
                    "verified": verified_count,
                    "suspect": suspect_count,
                    "phantom": phantom_count,
                    "unverified": unverified_count,
                    "manifest_present": manifest_present,
                },
                "lineage": {
                    "parents": lineage.get("parents", []),
                    "operator": lineage.get("operator", "null"),
                    "citation_delta_compliant": citation_delta_compliant,
                },
                "prose": {
                    "statement": h.get("statement", ""),
                    "mechanism": h.get("mechanism", ""),
                },
            }
        )

    # Run info from run.yaml
    run_info: dict[str, Any] = {
        "goal": "",
        "constraints": [],
        "created_at": "",
        "tournament_strategy": "",
        "composite_preset": None,
    }
    if run_yaml and isinstance(run_yaml, dict):
        run_info["goal"] = run_yaml.get("goal", "")
        run_info["constraints"] = run_yaml.get("constraints", [])
        run_info["created_at"] = run_yaml.get("created_at", "")
        run_info["tournament_strategy"] = run_yaml.get(
            "tournament_strategy",
            run_yaml.get("strategy", ""),
        )
        run_info["composite_preset"] = run_yaml.get("composite_preset")

    # Build the run ID from the directory name
    hypex_run_id = run_dir.name

    return {
        "schema": "dde.hypex.v1",
        "hypex_run_id": hypex_run_id,
        "hypex_run_dir": str(run_dir),
        "run_id": run_id,
        "work_order_id": work_order_id,
        "run": run_info,
        "termination": termination,
        "observed": {
            "n_hypotheses": n_hypotheses,
            "n_quarantined": n_quarantined,
            "n_reviews": n_reviews,
            "n_matches": n_matches,
            "n_epochs_observed": n_epochs_observed,
            "last_match_id": last_match_id,
        },
        "declared_budgets": declared_budgets,
        "roster": roster,
        "ratings": {
            "epoch": ratings_epoch,
            "base_rating": base_rating,
            "per_hypothesis": ratings_per_h,
        },
        "pacing": pacing,
        "progress": progress,
        "hypotheses": hyp_list,
        "integrity": integrity,
        "_schema_validation": schema_validation,
    }


# ---------------------------------------------------------------------------
# Archive helper
# ---------------------------------------------------------------------------


def _archive_run_dir(run_dir: Path, dest: Path) -> str:
    """Archive the run directory as .tar.zst and return its sha256."""
    import tarfile

    try:
        import zstandard as zstd
    except ImportError as e:
        raise ArtifactError(
            "zstandard is required for run directory archival",
            remedy="pip install zstandard",
        ) from e

    def _safe_filter(tarinfo):
        if tarinfo.issym() or tarinfo.islnk():
            return None  # strip symlinks from archive
        return tarinfo

    buf = io.BytesIO()
    cctx = zstd.ZstdCompressor(level=3)
    zst_writer = cctx.stream_writer(buf, closefd=False)
    with tarfile.open(fileobj=zst_writer, mode="w|") as tar:
        tar.add(str(run_dir), arcname=run_dir.name, filter=_safe_filter)
    zst_writer.close()
    compressed = buf.getvalue()
    dest.write_bytes(compressed)
    return hashlib.sha256(compressed).hexdigest()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def hypex() -> None:
    """Hypex tournament runs: ingest, analyze."""


@hypex.command()
@click.argument("run_dir")
@out_option
@output_options
@pass_state
def ingest(
    state: AppState,
    run_dir: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Normalise a hypex run directory into a Layer 0 artifact + sidecar.

    RUN_DIR is the path to a completed hypex run directory containing
    hypotheses/, matches/, reviews/, and optionally quarantine/, meta/,
    ratings/, and citations/ subdirectories.
    """
    run_path = Path(run_dir).expanduser()
    project = state.project()
    if not run_path.is_absolute():
        candidates = [Path.cwd() / run_path, project.root / run_path]
        run_path = next((c for c in candidates if c.is_dir()), candidates[0])
    if not run_path.is_dir():
        raise ArtifactError(
            f"run directory not found: {run_dir}",
            detail=f"looked in {Path.cwd()} and {project.root}",
            remedy="pass an absolute path to the hypex run directory",
        )
    run_path = run_path.resolve()
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)

    # --- Walk the datastore ---

    # Hypotheses
    hypotheses: dict[str, dict] = {}
    schema_files_checked = 0
    schema_files_failed: list[str] = []

    hyp_dir = run_path / "hypotheses"
    for fpath, data in _walk_json_dir(hyp_dir):
        schema_files_checked += 1
        h_id = fpath.stem  # e.g. "H-0042"
        errors = (
            _validate_hypothesis(data) if data else [f"empty or invalid: {fpath.name}"]
        )
        if errors:
            schema_files_failed.append(f"{fpath.name}: {'; '.join(errors)}")
        if data:
            hypotheses[h_id] = data

    # Quarantine
    quarantine: dict[str, dict] = {}
    q_dir = run_path / "quarantine"
    for fpath, data in _walk_json_dir(q_dir):
        schema_files_checked += 1
        q_id = fpath.stem
        errors = (
            _validate_hypothesis(data) if data else [f"empty or invalid: {fpath.name}"]
        )
        if errors:
            schema_files_failed.append(f"quarantine/{fpath.name}: {'; '.join(errors)}")
        if data:
            quarantine[q_id] = data

    # Matches
    matches: list[dict] = []
    match_dir = run_path / "matches"
    for fpath, data in _walk_json_dir(match_dir):
        schema_files_checked += 1
        errors = _validate_match(data) if data else [f"empty or invalid: {fpath.name}"]
        if errors:
            schema_files_failed.append(f"matches/{fpath.name}: {'; '.join(errors)}")
        if data:
            matches.append(data)

    # Reviews
    reviews: list[dict] = []
    review_dir = run_path / "reviews"
    for fpath, data in _walk_json_dir(review_dir):
        schema_files_checked += 1
        errors = _validate_review(data) if data else [f"empty or invalid: {fpath.name}"]
        if errors:
            schema_files_failed.append(f"reviews/{fpath.name}: {'; '.join(errors)}")
        if data:
            reviews.append(data)

    schema_validation = {
        "files_checked": schema_files_checked,
        "files_failed": schema_files_failed,
    }

    # --- Read meta files ---

    # Ratings — look for the latest epoch-N.json
    ratings_data: dict | None = None
    ratings_dir = run_path / "ratings"
    if ratings_dir.is_dir():
        rating_files = sorted(
            [
                f
                for f in ratings_dir.iterdir()
                if f.name.startswith("epoch-")
                and f.suffix == ".json"
                and is_safe_to_open(f)
            ],
            key=lambda f: f.name,
        )
        if rating_files:
            try:
                ratings_data = _read_json_file(rating_files[-1], "ratings")
            except SchemaError:
                pass

    ratings_per_h = (
        ratings_data.get("ratings", {})
        if ratings_data and isinstance(ratings_data.get("ratings"), dict)
        else {}
    )

    # run.yaml
    run_yaml: dict | None = None
    run_yaml_path = run_path / "run.yaml"
    if run_yaml_path.is_file() and is_safe_to_open(run_yaml_path):
        try:
            import yaml

            run_yaml = yaml.safe_load(run_yaml_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # meta/termination.json
    termination: dict[str, Any] = {
        "reason": "aborted",
        "epoch_reached": 0,
        "declared": False,
    }
    term_path = run_path / "meta" / "termination.json"
    if term_path.is_file() and is_safe_to_open(term_path):
        try:
            term_data = _read_json_file(term_path, "termination")
            if isinstance(term_data, dict):
                termination = {
                    "reason": term_data.get("reason", "aborted"),
                    "epoch_reached": term_data.get("epoch_reached", 0),
                    "declared": True,
                }
        except SchemaError:
            pass

    # meta/roster.ndjson
    roster: list[dict] = []
    roster_path = run_path / "meta" / "roster.ndjson"
    if roster_path.is_file() and is_safe_to_open(roster_path):
        try:
            for line in roster_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    roster.append(json.loads(line))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    # meta/progress.json
    progress: dict | None = None
    progress_path = run_path / "meta" / "progress.json"
    if progress_path.is_file() and is_safe_to_open(progress_path):
        try:
            progress = _read_json_file(progress_path, "progress")
        except SchemaError:
            pass

    # meta/pacing.json — read but not used in ingest, carried into the record
    pacing: dict | None = None
    pacing_path = run_path / "meta" / "pacing.json"
    if pacing_path.is_file() and is_safe_to_open(pacing_path):
        try:
            pacing = _read_json_file(pacing_path, "pacing")
        except SchemaError:
            pass

    # --- Compute integrity ---
    integrity = _compute_integrity(
        hypotheses,
        matches,
        reviews,
        ratings_per_h,
        quarantine,
    )

    # --- Build the record ---
    record = _build_hypex_record(
        run_dir=run_path,
        hypotheses=hypotheses,
        matches=matches,
        reviews=reviews,
        ratings_data=ratings_data,
        quarantine=quarantine,
        integrity=integrity,
        schema_validation=schema_validation,
        termination=termination,
        roster=roster,
        pacing=pacing,
        run_yaml=run_yaml,
        progress=progress,
    )

    if not hypotheses:
        raise SchemaError(
            "run directory contains no hypotheses",
            detail=f"looked in {hyp_dir}",
            remedy="confirm this is a valid hypex run directory",
        )

    # --- Filenames ---
    _slug(record["run"].get("goal"), "tournament")
    name = f"hx-{record['hypex_run_id']}"

    # --- Write artifacts ---
    # Archive the run directory as .tar.zst
    archive_path = target_dir / f"{name}.run.tar.zst"
    try:
        archive_sha = _archive_run_dir(run_path, archive_path)
    except (ImportError, OSError):
        # If zstandard is not available, skip archival but note it
        archive_sha = None
        archive_path = None

    # Write the normalised artifact
    normalised = target_dir / f"{name}.hypex.json"
    normalised.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    # --- Sidecar ---
    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="ingest",
        endpoint=None,
        parameters={"run_dir": str(run_path)},
    )

    sidecar.note("hypex_run_id", record["hypex_run_id"])
    sidecar.note("source_sha256", provenance.sha256_file(normalised))

    if archive_path and archive_path.is_file():
        sidecar.note("archive_sha256", archive_sha)
        sidecar.add_output(archive_path)

    sidecar.add_output(normalised)
    meta = sidecar.write(target_dir / f"{name}.meta.json")

    # --- Output ---
    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("hypotheses", len(hypotheses))
    emit.data("matches", len(matches))
    emit.data("reviews", len(reviews))
    emit.data("quarantined", len(quarantine))
    emit.data("integrity_issues", sum(len(v) for v in integrity.values()))
    emit.data("warnings", sidecar.warnings)
    emit.path(project.relative(normalised), "normalised")
    if archive_path and archive_path.is_file():
        emit.path(project.relative(archive_path), "archive")
    emit.path(project.relative(meta), "sidecar")
    emit.flush()


@hypex.command()
@click.argument("artifact")
@click.option(
    "--elo-decisive-gap", type=float, default=None, help="Override threshold."
)
@click.option("--min-win-rate", type=float, default=None, help="Override threshold.")
@out_option
@output_options
@pass_state
def analyze(
    state: AppState,
    artifact: str,
    elo_decisive_gap: float | None,
    min_win_rate: float | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Apply tournament thresholds to an ingested hypex artifact.

    ARTIFACT is the ``.hypex.json`` produced by ``ingest``.
    """
    project = state.project()
    path = resolve_artifact(state, artifact, "normalised hypex artifact")
    record = provenance.read_json(path, "normalised hypex artifact")
    if record.get("schema") != "dde.hypex.v1":
        raise SchemaError(
            f"{path.name} is not a normalised hypex artifact",
            detail=f"expected schema dde.hypex.v1, got {record.get('schema')!r}",
            remedy=(
                "this artifact's schema is "
                f"{record.get('schema')!r}; use the matching strategy's "
                "analyzer, or `dde hypothesis analyze` for cross-strategy reads"
            ),
        )

    thresholds = load_thresholds(
        state,
        "hypex",
        {
            "elo_decisive_gap": elo_decisive_gap,
            "min_win_rate": min_win_rate,
        },
    )
    min_matches = thresholds.get("min_matches")
    min_win = thresholds.get("min_win_rate")
    max_phantom = thresholds.get("max_phantom_citations")

    # elo_decisive_gap may be UNRESOLVED — handle gracefully
    gap_cutoff: float | None = None
    try:
        gap_cutoff = thresholds.get("elo_decisive_gap")
    except ThresholdError:
        # UNRESOLVED — leave as None
        pass

    hypotheses = record.get("hypotheses", [])
    termination = record.get("termination", {})
    observed = record.get("observed", {})
    integrity = record.get("integrity", {})

    # Sort by ELO descending for ranked hypotheses
    ranked = [h for h in hypotheses if h.get("elo") is not None]
    ranked.sort(key=lambda h: h["elo"], reverse=True)

    # --- Per-hypothesis advisories ---
    per_idea: list[dict[str, Any]] = []
    for h in ranked:
        advisories: list[str] = []
        win_rate = (
            h["wins"] / h["matches"] if h.get("matches") and h["matches"] > 0 else None
        )
        if win_rate is not None and win_rate < min_win:
            advisories.append(f"win rate {win_rate:.0%} below min_win_rate")
        if h.get("matches") is not None and h["matches"] < min_matches:
            advisories.append(
                f"only {h['matches']} matches played (< min_matches); "
                "ranking is not well supported"
            )
        if h.get("citations", {}).get("phantom", 0) > max_phantom:
            advisories.append(
                f"{h['citations']['phantom']} phantom citation(s) present"
            )

        per_idea.append(
            {
                "id": h["id"],
                "title": h.get("title", ""),
                "elo": h["elo"],
                "win_rate": win_rate,
                "n_matches": h.get("matches", 0),
                "advisories": advisories,
            }
        )

    # --- ELO gap and leader_gap_is_decisive ---
    elo_gap: float | None = None
    leader_gap_is_decisive: bool | None = None  # tri-state: true/false/null

    if len(ranked) >= 2:
        elo_gap = ranked[0]["elo"] - ranked[1]["elo"]
        if gap_cutoff is not None:
            leader_gap_is_decisive = elo_gap >= gap_cutoff
        else:
            # UNRESOLVED — must be null, NOT fall back to 50.0
            leader_gap_is_decisive = None

    # --- Verdict ---
    term_reason = termination.get("reason", "aborted")
    term_declared = termination.get("declared", False)

    if term_reason in ("budget_exhausted", "aborted", "error") or not term_declared:
        verdict = "unconverged"
    elif not ranked:
        verdict = "unrankable"
    elif len(ranked) == 1:
        verdict = "single-candidate"
    elif leader_gap_is_decisive is True:
        flagged_leader = per_idea[0]["advisories"] if per_idea else []
        if not flagged_leader:
            verdict = "clear-leader"
        else:
            verdict = "leader-with-advisories"
    elif leader_gap_is_decisive is False:
        verdict = "no-clear-leader"
    else:
        # leader_gap_is_decisive is None (UNRESOLVED gap)
        # Cannot determine if leader is decisive
        verdict = "no-clear-leader"

    assessment: dict[str, Any] = {
        "verdict": verdict,
        "leader": {
            "id": per_idea[0]["id"],
            "title": per_idea[0]["title"],
            "elo": per_idea[0]["elo"],
        }
        if per_idea
        else None,
        "elo_gap_to_runner_up": round(elo_gap, 2) if elo_gap is not None else None,
        "leader_gap_is_decisive": leader_gap_is_decisive,
        "n_hypotheses_ranked": len(ranked),
        "ideas": per_idea,
    }

    metrics: dict[str, Any] = {
        "n_hypotheses": len(hypotheses),
        "n_matches": observed.get("n_matches", 0),
        "n_reviews": observed.get("n_reviews", 0),
        "n_quarantined": observed.get("n_quarantined", 0),
        "elo_range": ([ranked[-1]["elo"], ranked[0]["elo"]] if ranked else None),
        "termination": termination,
    }

    # --- Relay codes ---
    relays: list[dict[str, str]] = []

    # Carry forward relays from the ingest sidecar
    meta_path = path.with_name(path.name.replace(".hypex.json", ".meta.json"))
    if meta_path.is_file():
        ingest_meta = provenance.read_json(meta_path, "provenance sidecar")
        for item in ingest_meta.get("mandatory_relays", []) or []:
            if not any(r["code"] == item.get("code") for r in relays):
                relays.append(item)

    def _fire(code: str, message: str) -> None:
        if not any(r["code"] == code for r in relays):
            relays.append(provenance.relay(code, message))

    # run_not_converged — when termination.reason != "converged"
    if term_reason != "converged":
        _fire(
            "hypex.run_not_converged",
            f"Run terminated with reason={term_reason!r}. The ranking "
            "reflects where the run stopped, not where it settled.",
        )

    # run_aborted — when termination.declared == false or reason == "aborted"
    if not term_declared or term_reason == "aborted":
        _fire(
            "hypex.run_aborted",
            f"No termination record was written (declared={term_declared}). "
            f"Epoch reached: {termination.get('epoch_reached', 0)}.",
        )

    # phantom_citations_present — when any hypothesis has phantom > 0
    phantoms = [h for h in hypotheses if h.get("citations", {}).get("phantom", 0) > 0]
    if phantoms:
        ids = ", ".join(h["id"] for h in phantoms[:10])
        _fire(
            "hypex.phantom_citations_present",
            f"Phantom citations found in: {ids}.",
        )

    # citation_manifest_absent — when any hypothesis lacks a manifest
    no_manifest = [
        h
        for h in hypotheses
        if not h.get("citations", {}).get("manifest_present", False)
    ]
    if no_manifest:
        ids = ", ".join(h["id"] for h in no_manifest[:10])
        _fire(
            "hypex.citation_manifest_absent",
            f"No citation manifest for: {ids}.",
        )

    # integrity_violations — when any integrity array non-empty
    has_violations = any(len(v) > 0 for v in integrity.values() if isinstance(v, list))
    if has_violations:
        violation_summary = "; ".join(
            f"{k}: {len(v)}"
            for k, v in integrity.items()
            if isinstance(v, list) and len(v) > 0
        )
        _fire(
            "hypex.integrity_violations",
            f"Integrity violations found — {violation_summary}.",
        )

    # unrated_hypotheses — when integrity.unrated_hypotheses non-empty
    unrated = integrity.get("unrated_hypotheses", [])
    if unrated:
        ids = ", ".join(str(u) for u in unrated[:10])
        _fire(
            "hypex.unrated_hypotheses",
            f"Unrated hypotheses: {ids}.",
        )

    # composite_ranking — when standings taken with --composite
    composite_preset = record.get("run", {}).get("composite_preset")
    has_composite = any(h.get("composite") is not None for h in hypotheses)
    if has_composite or composite_preset:
        _fire(
            "hypex.composite_ranking",
            f"Standings use composite ranking (preset: {composite_preset}). "
            "The number is not an ELO.",
        )

    # quarantined_excluded — when observed.n_quarantined > 0
    n_quarantined = observed.get("n_quarantined", 0)
    if n_quarantined > 0:
        _fire(
            "hypex.quarantined_excluded",
            f"{n_quarantined} hypothesis(es) quarantined and excluded "
            "from the tournament standings.",
        )

    # pacing_uncoordinated — when pacing absent, tier != "shared",
    # or paths disagree.  Read from the record (persisted at ingest)
    # instead of from the filesystem — the run dir may be archived.
    pacing_data = record.get("pacing")

    pacing_uncoordinated = False
    if pacing_data is None:
        pacing_uncoordinated = True
    elif isinstance(pacing_data, dict):
        if pacing_data.get("tier") != "shared":
            pacing_uncoordinated = True
        # Check if all paths agree
        paths = pacing_data.get("paths", [])
        if isinstance(paths, list) and len({str(p) for p in paths}) > 1:
            pacing_uncoordinated = True

    if pacing_uncoordinated:
        roster_size = len(record.get("roster", []))
        tier = pacing_data.get("tier", "unknown") if pacing_data else "absent"
        _fire(
            "hypex.pacing_uncoordinated",
            f"Pacing was not coordinated (tier={tier}, roster_size={roster_size}). "
            "Retrieval rate may have exceeded upstream limits.",
        )

    # --- Assessment core (dde.hypothesis-assessment.v1) ---
    source_sha256 = provenance.sha256_file(path)
    assessment_candidates = []
    for h in hypotheses:
        elo = h.get("elo")
        # Rank based on ELO ordering (1-based)
        rank: int | None = None
        for i, rh in enumerate(ranked):
            if rh["id"] == h["id"]:
                rank = i + 1
                break

        assessment_candidates.append(
            {
                "candidate_id": h.get("id"),
                "statement": h.get("prose", {}).get("statement", h.get("title", "")),
                "rank": rank,
                "score": (
                    {"value": elo, "basis": "hypex-elo@1.0"}
                    if elo is not None
                    else None
                ),
                "origin": "generated",
            }
        )

    assessment["assessment_core"] = {
        "schema": "dde.hypothesis-assessment.v1",
        "strategy": "hypex",
        "source_artifact": str(project.relative(path)),
        "source_sha256": source_sha256,
        "candidates": assessment_candidates,
    }

    # --- Write analysis ---
    analysis_path = beside_or_out(
        state, path, path.name.replace(".hypex.json", ".analysis.json"), out
    )
    provenance.write_analysis(
        analysis_path,
        source=path,
        threshold_set=thresholds.tag,
        thresholds_applied=thresholds.applied(),
        threshold_sources=thresholds.sources(),
        threshold_provenance=thresholds.provenance,
        unresolved=thresholds.unresolved(),
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    # --- Output ---
    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("assessment", assessment)
    emit.data("metrics", metrics)
    emit.data("mandatory_relays", relays)
    emit.data("threshold_set", thresholds.tag)

    goal = record.get("run", {}).get("goal") or "(untitled)"
    emit.line(f"Tournament: {goal[:70]}")
    emit.line(f"Verdict: {verdict}  [threshold_set {thresholds.tag}]")
    if per_idea:
        lead = per_idea[0]
        gap_str = f", +{elo_gap:.0f} ELO over #2" if elo_gap is not None else ""
        emit.line(f"Leader: {lead['id']} (ELO {lead['elo']:.0f}{gap_str})")
    emit.line(
        f"Hypotheses: {len(hypotheses)} ranked, {observed.get('n_quarantined', 0)} quarantined"
    )
    if relays:
        emit.line(f"Relays fired: {len(relays)}")
        for r in relays[:5]:
            emit.line(f"  {r['code']}")

    emit.path(project.relative(analysis_path), "analysis")
    emit.flush()
