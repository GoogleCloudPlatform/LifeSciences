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

"""`dde ppi` — protein-protein interaction lookup via STRING.

Two phases:

  search   one gene/protein name -> interaction partners from the STRING
           database, written verbatim to Layer 0 with a sidecar and a
           structured artifact.
  analyze  reads stored search results and classifies whether the gene
           has significant interaction partners. No network.

STRING (https://string-db.org) aggregates protein-protein interaction
evidence from multiple channels: experimental data, curated databases,
text mining, co-expression, genomic context (neighbourhood, fusion,
co-occurrence), and homology transfer. Each channel contributes a
sub-score; the combined score integrates all channels.

STRING's REST API returns clean HTTP status codes for errors, so the
shared HTTP client's retry logic handles transient failures without the
gnomAD-style body inspection.

A high combined score means STRING has multiple independent evidence
lines for the interaction. It does not confirm direct physical binding
or functional relevance in any particular tissue or condition.
Experimental validation is always required.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote

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
    Refusal,
    SchemaError,
)
from ..core.paths import sanitize_slug
from ..core.qps import qps_for_host

logger = logging.getLogger(__name__)

TOOL = "ppi"
ARTIFACT_CLASS = "genomics"

STRING_API = "https://string-db.org/api"

# Evidence channel names returned by STRING's network endpoint.
_EVIDENCE_CHANNELS = (
    "nscore",  # gene neighbourhood
    "fscore",  # gene fusion
    "pscore",  # phylogenetic co-occurrence
    "ascore",  # co-expression
    "escore",  # experimental
    "dscore",  # database (curated)
    "tscore",  # textmining
)

# Human-readable names for the evidence channels used in the artifact.
_CHANNEL_LABELS = {
    "nscore": "neighbourhood",
    "fscore": "fusion",
    "pscore": "cooccurrence",
    "ascore": "coexpression",
    "escore": "experimental",
    "dscore": "database",
    "tscore": "textmining",
}


def _resolve_string_id(gene: str, species: int) -> str:
    """Resolve a gene/protein name to a STRING identifier.

    Raises Refusal if the gene is not found in STRING.
    """
    url = (
        f"{STRING_API}/json/get_string_ids"
        f"?identifiers={quote(gene, safe='')}"
        f"&species={species}&limit=1"
    )
    data = http.get_json(url, qps=qps_for_host("string-db.org"), timeout=60.0)

    if not data or not isinstance(data, list) or len(data) == 0:
        raise Refusal(
            f"STRING has no record for {gene!r} (species {species})",
            remedy="check the gene/protein name at string-db.org",
        )
    string_id = data[0].get("stringId", "")
    if not string_id:
        raise Refusal(
            f"STRING returned a record for {gene!r} without a STRING identifier",
            remedy="check the gene/protein name at string-db.org",
        )
    return string_id


def _fetch_network(
    string_id: str, species: int, min_score: float
) -> tuple[bytes, list[dict[str, Any]]]:
    """Fetch the interaction network from STRING using a resolved STRING ID.

    Returns (verbatim response bytes, list of interaction rows).
    The required_score parameter is in STRING's 0-1000 scale.
    """
    required_score = int(min_score * 1000)
    url = (
        f"{STRING_API}/json/network"
        f"?identifiers={quote(string_id, safe='')}"
        f"&species={species}"
        f"&required_score={required_score}"
    )
    response = http.request(
        "GET",
        url,
        qps=qps_for_host("string-db.org"),
        timeout=60.0,
    )
    raw = response.content
    try:
        rows = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise SchemaError("STRING did not return JSON", detail=str(exc)) from exc

    if not isinstance(rows, list):
        raise SchemaError(
            "STRING returned an unexpected response shape",
            detail=f"expected a JSON array, got {type(rows).__name__}",
        )
    return raw, rows


def _extract_interactions(
    rows: list[dict[str, Any]], gene: str
) -> list[dict[str, Any]]:
    """Extract structured interactions from STRING network rows.

    Each row in STRING's network response represents an edge between two
    proteins. We extract the partner (the protein that is not the query
    gene), the combined score, and the individual evidence channel scores.
    """
    gene_upper = gene.upper()
    interactions: list[dict[str, Any]] = []
    seen_partners: set[str] = set()

    for row in rows:
        # STRING network rows have preferredName_A / preferredName_B.
        name_a = row.get("preferredName_A", "")
        name_b = row.get("preferredName_B", "")

        # Identify the partner (whichever is not our query gene).
        if name_a.upper() == gene_upper:
            partner = name_b
        elif name_b.upper() == gene_upper:
            partner = name_a
        else:
            # Defensive: for a single-gene query, STRING's network endpoint
            # should only return edges involving the query gene, so this
            # branch is unreachable in normal use.  If it fires, the API
            # returned an edge between two proteins neither of which is the
            # query — log it so unexpected behaviour is visible.
            logger.warning(
                "STRING returned an edge (%s — %s) where neither protein "
                "matches the query gene %r; this is unexpected for a "
                "single-gene query",
                name_a,
                name_b,
                gene,
            )
            partner = name_b if name_a.upper() < name_b.upper() else name_a

        if not partner or partner in seen_partners:
            continue
        seen_partners.add(partner)

        combined = row.get("score", 0.0)
        interaction: dict[str, Any] = {
            "partner": partner,
            "combined_score": combined,
        }
        # Add individual evidence channel scores.
        for channel_key, label in _CHANNEL_LABELS.items():
            score = row.get(channel_key)
            if score is not None:
                interaction[label] = score

        interactions.append(interaction)

    # Sort by combined score descending.
    interactions.sort(key=lambda x: x["combined_score"], reverse=True)
    return interactions


def _build_artifact(
    gene: str,
    species: int,
    min_score: float,
    interactions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the structured dde.ppi.v1 artifact."""
    top_partners = [i["partner"] for i in interactions[:5]]
    return {
        "schema": "dde.ppi.v1",
        "query": {
            "gene": gene.upper(),
            "species": species,
            "min_score": min_score,
        },
        "summary": {
            "n_interactions": len(interactions),
            "top_partners": top_partners,
        },
        "interactions": interactions,
    }


@click.group()
def ppi() -> None:
    """Protein-protein interaction lookup (STRING, free and unauthenticated)."""


@ppi.command("search")
@click.argument("gene")
@click.option(
    "--species",
    type=int,
    default=9606,
    show_default=True,
    help="NCBI taxonomy ID (default: 9606, Homo sapiens).",
)
@click.option(
    "--min-score",
    type=click.FloatRange(0.0, 1.0),
    default=0.4,
    show_default=True,
    help="Minimum combined interaction score (0-1, STRING medium confidence).",
)
@out_option
@output_options
@pass_state
def search_cmd(
    state: AppState,
    gene: str,
    species: int,
    min_score: float,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Search for protein-protein interactions for GENE via STRING."""
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)
    slug = sanitize_slug(gene.lower())

    # Step 1: resolve the gene name to a STRING ID.
    string_id = _resolve_string_id(gene, species)

    # Step 2: fetch the interaction network using the resolved STRING ID.
    raw, rows = _fetch_network(string_id, species, min_score)

    # Step 3: extract structured interactions.
    interactions = _extract_interactions(rows, gene)

    # Step 4: build the artifact.
    artifact = _build_artifact(gene, species, min_score, interactions)

    # Build the provenance sidecar.
    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint=STRING_API,
        parameters={
            "query_gene": gene,
            "resolved_gene": gene.upper(),
            "string_id": string_id,
            "species": species,
            "min_score": min_score,
        },
    )
    sidecar.note("source_db", "STRING")
    sidecar.note("n_interactions", len(interactions))

    # Relay: interaction scores are not functional evidence.
    sidecar.warn(
        "Protein-protein interactions reported by STRING are aggregated from "
        "multiple evidence channels including text mining, co-expression, and "
        "genomic context. A high interaction score does not confirm direct "
        "physical binding or functional relevance in the tissue or condition "
        "of interest. Experimental validation is required.",
        code="ppi.interaction_not_functional",
    )

    # Write verbatim response.
    verbatim_path = target_dir / f"{slug}.ppi-string.json"
    verbatim_path.write_bytes(raw)
    sidecar.add_output(verbatim_path)

    # Write structured artifact.
    artifact_path = target_dir / f"{slug}.ppi-string.artifact.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(artifact_path)

    # Write sidecar.
    meta_path = sidecar.write(target_dir / f"{slug}.ppi-string.meta.json")

    emit.data("gene", gene.upper())
    emit.data("string_id", string_id)
    emit.data("species", species)
    emit.data("n_interactions", len(interactions))
    emit.data("top_partners", artifact["summary"]["top_partners"])
    emit.path(verbatim_path, role="verbatim")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.flush()


@ppi.command("analyze")
@click.argument("gene")
@click.option(
    "--min-score",
    "score_threshold",
    type=click.FloatRange(0.0, 1.0),
    default=None,
    help="Override the minimum combined score threshold.",
)
@click.option(
    "--min-experimental",
    "experimental_threshold",
    type=click.FloatRange(0.0, 1.0),
    default=None,
    help="Override the minimum experimental evidence score threshold.",
)
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    gene: str,
    score_threshold: float | None,
    experimental_threshold: float | None,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Classify PPI interactions from a stored search. No network."""
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = sanitize_slug(gene.lower())
    artifact_path = source_dir / f"{slug}.ppi-string.artifact.json"
    if not artifact_path.is_file():
        raise ArtifactError(
            f"no PPI search artifact for {gene.upper()}",
            remedy=f"run `dde ppi search {gene}` first",
        )

    artifact = provenance.read_json(artifact_path, "PPI artifact")
    if artifact.get("schema") != "dde.ppi.v1":
        raise SchemaError(
            f"unexpected schema in {artifact_path.name}",
            detail=f"expected dde.ppi.v1, got {artifact.get('schema')!r}",
        )

    interactions = artifact.get("interactions", [])

    # Apply combined score threshold (STRING scores are 0-1).
    score_cutoff = score_threshold if score_threshold is not None else 0.7
    significant = [
        i for i in interactions if (i.get("combined_score") or 0) >= score_cutoff
    ]

    # Apply experimental evidence threshold when set.
    exp_cutoff = experimental_threshold if experimental_threshold is not None else 0.0
    if exp_cutoff > 0:
        significant = [
            i for i in significant if (i.get("experimental") or 0) >= exp_cutoff
        ]

    # Top partners from significant interactions.
    top_partners: list[str] = []
    seen: set[str] = set()
    for interaction in significant:
        partner = interaction.get("partner", "")
        if partner and partner not in seen:
            top_partners.append(partner)
            seen.add(partner)
        if len(top_partners) >= 10:
            break

    verdict = "interactions_found" if significant else "no_interactions"

    relays: list[dict[str, str]] = []

    def add_relay(code: str, message: str) -> None:
        if not any(r["code"] == code for r in relays):
            relays.append(provenance.relay(code, message))

    if significant:
        add_relay(
            "ppi.interaction_not_functional",
            f"{gene.upper()} has {len(significant)} interaction(s) above "
            f"the combined score threshold ({score_cutoff}); these are "
            "aggregated evidence scores from multiple channels including "
            "text mining, co-expression, and genomic context, not evidence "
            "of direct physical binding or functional relevance",
        )

    metrics = {
        "total_interactions": len(interactions),
        "significant_interactions": len(significant),
        "score_threshold": score_cutoff,
        "experimental_threshold": exp_cutoff,
        "top_partners": top_partners,
    }
    assessment = {
        "verdict": verdict,
        "gene": gene.upper(),
        "n_significant": len(significant),
        "top_partners": top_partners,
    }

    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.ppi-string.analysis.json",
        source=state.project().relative(artifact_path),
        threshold_set="ppi-string",
        thresholds_applied={
            "combined_score_cutoff": score_cutoff,
            "experimental_cutoff": exp_cutoff,
        },
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("metrics", metrics)
    emit.data("relays", relays)
    emit.line(
        f"{gene.upper()} -> {verdict.upper()} "
        f"({len(significant)}/{len(interactions)} above threshold)"
    )
    if top_partners:
        emit.line(f"top partners: {', '.join(top_partners[:5])}")
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()
