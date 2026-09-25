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

"""`dde cellxgene` — CZ CELLxGENE Discover single-cell dataset search.

Queries the CZ CELLxGENE Discover public API for collections and datasets
matching tissue, cell type, organism, or disease criteria.  CELLxGENE hosts
33M+ cells across 436+ datasets — this tool searches dataset *metadata*,
not expression values.

Two phases:

  search   queries the collections endpoint and filters by tissue,
           cell type, organism, or disease; writes structured results
           to Layer 0 with a sidecar.
  analyze  reads stored search results and summarises tissue and cell
           type distributions, organism breakdown, and total cell count.
           No network.

The search returns collection and dataset metadata — tissue annotations,
cell type annotations, assay types, and H5AD download links.  It does NOT
return gene expression values.  A dataset listed as containing a tissue
does not confirm that a specific gene is expressed there; confirming
expression requires downloading the H5AD file or using the Census API.
"""

from __future__ import annotations

import json
import re
from collections import Counter
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
from ..core.errors import ArtifactError, Refusal, SchemaError
from ..core.qps import qps_for_host

TOOL = "cellxgene"
ARTIFACT_CLASS = "single-cell"

CELLXGENE_API = "https://api.cellxgene.cziscience.com/curation/v1"


def _slugify(query: str) -> str:
    """Derive a filesystem-safe slug from a search query string."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", query).strip("-").lower()
    return slug[:80] or "cellxgene-search"


def _matches(labels: list[dict[str, Any]], term: str) -> bool:
    """Check if any label dict's 'label' field contains term (case-insensitive)."""
    term_lower = term.lower()
    for item in labels:
        label = item.get("label", "")
        if isinstance(label, str) and term_lower in label.lower():
            return True
    return False


def _filter_datasets(
    datasets: list[dict[str, Any]],
    tissue: str | None,
    cell_type: str | None,
    organism: str | None,
    disease: str | None,
) -> list[dict[str, Any]]:
    """Filter datasets by metadata fields (case-insensitive substring match)."""
    result = []
    for ds in datasets:
        if organism and not _matches(ds.get("organism", []), organism):
            continue
        if tissue and not _matches(ds.get("tissue", []), tissue):
            continue
        if cell_type and not _matches(ds.get("cell_type", []), cell_type):
            continue
        if disease and not _matches(ds.get("disease", []), disease):
            continue
        result.append(ds)
    return result


def _extract_dataset_record(ds: dict[str, Any]) -> dict[str, Any]:
    """Extract a structured record from a raw dataset object."""
    tissues = [t.get("label", "") for t in ds.get("tissue", []) if t.get("label")]
    cell_types = [c.get("label", "") for c in ds.get("cell_type", []) if c.get("label")]
    organisms = [o.get("label", "") for o in ds.get("organism", []) if o.get("label")]
    diseases = [d.get("label", "") for d in ds.get("disease", []) if d.get("label")]
    assays = [a.get("label", "") for a in ds.get("assay", []) if a.get("label")]

    # Extract H5AD download URLs from assets.
    h5ad_urls: list[str] = []
    for asset in ds.get("assets", []):
        url = asset.get("url", "")
        filetype = asset.get("filetype", "")
        if filetype == "H5AD" or (url and url.endswith(".h5ad")):
            h5ad_urls.append(url)

    return {
        "dataset_id": ds.get("dataset_id", ""),
        "cell_count": ds.get("cell_count", 0),
        "tissues": tissues,
        "cell_types": cell_types,
        "organisms": organisms,
        "diseases": diseases,
        "assays": assays,
        "h5ad_urls": h5ad_urls,
    }


def _fetch_collections(
    query: str | None,
    tissue: str | None,
    cell_type: str | None,
    organism: str | None,
    disease: str | None,
) -> tuple[bytes, dict[str, Any]]:
    """Fetch and filter CELLxGENE collections.

    Returns (verbatim response bytes, structured artifact dict).
    """
    url = f"{CELLXGENE_API}/collections"
    raw_data = http.get_json(
        url, qps=qps_for_host("api.cellxgene.cziscience.com"), timeout=120.0
    )

    if not isinstance(raw_data, list):
        raise SchemaError(
            "CELLxGENE collections endpoint did not return a JSON array",
            detail=f"got {type(raw_data).__name__}",
        )

    raw = json.dumps(raw_data, indent=2).encode("utf-8")

    # Filter collections: match query against name/description, then
    # filter datasets by tissue/cell_type/organism/disease.
    matched_collections: list[dict[str, Any]] = []
    query_lower = query.lower() if query else None

    for collection in raw_data:
        name = collection.get("name", "")
        description = collection.get("description", "")

        # Free-text query filter on collection name and description.
        if query_lower:
            name_lower = (name or "").lower()
            desc_lower = (description or "").lower()
            if query_lower not in name_lower and query_lower not in desc_lower:
                continue

        datasets = collection.get("datasets", [])
        filtered = _filter_datasets(datasets, tissue, cell_type, organism, disease)

        if not filtered and (tissue or cell_type or organism or disease):
            # Collection matched text but no datasets pass filters.
            continue

        dataset_records = (
            [_extract_dataset_record(ds) for ds in filtered]
            if filtered
            else [_extract_dataset_record(ds) for ds in datasets]
        )

        if dataset_records:
            matched_collections.append(
                {
                    "collection_id": collection.get("collection_id", ""),
                    "name": name,
                    "description": (description or "")[:500],
                    "doi": collection.get("doi", ""),
                    "n_datasets": len(dataset_records),
                    "datasets": dataset_records,
                }
            )

    artifact: dict[str, Any] = {
        "schema": "dde.cellxgene-search.v1",
        "query": {
            "text": query or "",
            "tissue": tissue or "",
            "cell_type": cell_type or "",
            "organism": organism or "Homo sapiens",
            "disease": disease or "",
        },
        "summary": {
            "n_collections": len(matched_collections),
            "n_datasets": sum(c["n_datasets"] for c in matched_collections),
            "total_cell_count": sum(
                ds.get("cell_count", 0)
                for c in matched_collections
                for ds in c["datasets"]
            ),
        },
        "collections": matched_collections,
    }

    return raw, artifact


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def cellxgene() -> None:
    """CZ CELLxGENE Discover single-cell dataset search."""


@cellxgene.command("search")
@click.argument("query", required=False, default=None)
@click.option(
    "--tissue",
    default=None,
    help="Filter datasets by tissue label (case-insensitive substring).",
)
@click.option(
    "--cell-type",
    default=None,
    help="Filter datasets by cell type label (case-insensitive substring).",
)
@click.option(
    "--organism",
    default="Homo sapiens",
    help="Filter datasets by organism (default: 'Homo sapiens').",
)
@click.option(
    "--disease",
    default=None,
    help="Filter datasets by disease label (case-insensitive substring).",
)
@out_option
@output_options
@pass_state
def search_cmd(
    state: AppState,
    query: str | None,
    tissue: str | None,
    cell_type: str | None,
    organism: str | None,
    disease: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Search CELLxGENE Discover for single-cell datasets.

    QUERY is optional free-text search across collection names and
    descriptions.  Use --tissue, --cell-type, --organism, and --disease
    to filter datasets by metadata fields.

    At least one of QUERY or a filter option must be provided.
    """
    if not query and not tissue and not cell_type and not disease:
        raise Refusal(
            "no search criteria provided",
            remedy="provide a QUERY argument and/or filter options "
            "(--tissue, --cell-type, --organism, --disease)",
        )

    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    # Build slug from the most specific criterion.
    slug_source = query or tissue or cell_type or disease or "cellxgene"
    slug = _slugify(slug_source)

    raw, artifact = _fetch_collections(query, tissue, cell_type, organism, disease)

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint=f"{CELLXGENE_API}/collections",
        parameters={
            "query": query or "",
            "tissue": tissue or "",
            "cell_type": cell_type or "",
            "organism": organism or "Homo sapiens",
            "disease": disease or "",
        },
    )
    sidecar.note("source", "CZ CELLxGENE Discover (cellxgene.cziscience.com)")
    sidecar.note("n_collections", artifact["summary"]["n_collections"])
    sidecar.note("n_datasets", artifact["summary"]["n_datasets"])

    # Write verbatim response.
    verbatim_path = target_dir / f"{slug}.cellxgene.json"
    verbatim_path.write_bytes(raw)
    sidecar.add_output(verbatim_path)

    # Write structured artifact.
    artifact_path = target_dir / f"{slug}.cellxgene.artifact.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(artifact_path)

    # Write sidecar.
    meta_path = sidecar.write(target_dir / f"{slug}.cellxgene.meta.json")

    emit.data("query", query or "")
    emit.data("n_collections", artifact["summary"]["n_collections"])
    emit.data("n_datasets", artifact["summary"]["n_datasets"])
    emit.data("total_cell_count", artifact["summary"]["total_cell_count"])
    emit.path(verbatim_path, role="verbatim")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.line(f"CELLxGENE search: {slug_source!r}")
    emit.line(
        f"  {artifact['summary']['n_collections']} collections, "
        f"{artifact['summary']['n_datasets']} datasets, "
        f"{artifact['summary']['total_cell_count']:,} cells"
    )
    if artifact["collections"]:
        for c in artifact["collections"][:5]:
            emit.line(f"  {c['name'][:70]} ({c['n_datasets']} datasets)")
        if len(artifact["collections"]) > 5:
            emit.line(f"  ... {len(artifact['collections']) - 5} more in the artifact")
    emit.flush()


@cellxgene.command("analyze")
@click.argument("query", required=False, default=None)
@click.option(
    "--tissue",
    default=None,
    help="Tissue filter used in the original search (for slug matching).",
)
@click.option(
    "--cell-type",
    default=None,
    help="Cell type filter used in the original search (for slug matching).",
)
@click.option(
    "--disease",
    default=None,
    help="Disease filter used in the original search (for slug matching).",
)
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    query: str | None,
    tissue: str | None,
    cell_type: str | None,
    disease: str | None,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Summarise stored CELLxGENE search results. No network.

    Reads what `search` wrote and produces a summary: total datasets,
    tissue distribution, cell type distribution, organism breakdown,
    and total cell count.
    """
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug_source = query or tissue or cell_type or disease or "cellxgene"
    slug = _slugify(slug_source)

    artifact_path = source_dir / f"{slug}.cellxgene.artifact.json"
    if not artifact_path.is_file():
        raise ArtifactError(
            f"no CELLxGENE search artifact for {slug_source!r} under {source_dir}",
            remedy=f"run `dde cellxgene search {slug_source!r}` first",
        )

    artifact = provenance.read_json(artifact_path, "CELLxGENE search artifact")
    if artifact.get("schema") != "dde.cellxgene-search.v1":
        raise SchemaError(
            f"unexpected schema in {artifact_path.name}",
            detail=f"expected dde.cellxgene-search.v1, got {artifact.get('schema')!r}",
        )

    collections = artifact.get("collections", [])
    query_meta = artifact.get("query", {})

    # Compute distributions across all datasets.
    tissue_counts: Counter[str] = Counter()
    cell_type_counts: Counter[str] = Counter()
    organism_counts: Counter[str] = Counter()
    disease_counts: Counter[str] = Counter()
    assay_counts: Counter[str] = Counter()
    total_datasets = 0
    total_cell_count = 0

    for collection in collections:
        for ds in collection.get("datasets", []):
            total_datasets += 1
            total_cell_count += ds.get("cell_count", 0)
            for t in ds.get("tissues", []):
                tissue_counts[t] += 1
            for ct in ds.get("cell_types", []):
                cell_type_counts[ct] += 1
            for org in ds.get("organisms", []):
                organism_counts[org] += 1
            for dis in ds.get("diseases", []):
                disease_counts[dis] += 1
            for assay in ds.get("assays", []):
                assay_counts[assay] += 1

    top_tissues = tissue_counts.most_common(15)
    top_cell_types = cell_type_counts.most_common(15)
    top_diseases = disease_counts.most_common(10)
    top_assays = assay_counts.most_common(10)

    relays: list[dict[str, str]] = []
    relays.append(
        provenance.relay(
            "cellxgene.search_is_metadata_only",
            f"CELLxGENE search returned metadata for {total_datasets} "
            f"dataset(s) across {len(collections)} collection(s); these "
            "are dataset annotations (tissue, cell type, disease), not "
            "gene expression values. A dataset containing the queried "
            "tissue does not confirm expression of any specific gene. "
            "Confirming expression requires downloading the H5AD file "
            "or using the Census API.",
        )
    )

    assessment = {
        "outcome": "datasets_found" if total_datasets else "no_datasets",
        "query_text": query_meta.get("text", ""),
        "query_tissue": query_meta.get("tissue", ""),
        "query_cell_type": query_meta.get("cell_type", ""),
        "query_organism": query_meta.get("organism", ""),
        "query_disease": query_meta.get("disease", ""),
        "n_collections": len(collections),
        "n_datasets": total_datasets,
        "total_cell_count": total_cell_count,
        "tissue_distribution": [{"tissue": t, "count": c} for t, c in top_tissues],
        "cell_type_distribution": [
            {"cell_type": ct, "count": c} for ct, c in top_cell_types
        ],
        "organism_breakdown": dict(organism_counts),
        "disease_distribution": [{"disease": d, "count": c} for d, c in top_diseases],
        "assay_distribution": [{"assay": a, "count": c} for a, c in top_assays],
    }

    metrics = {
        "n_collections": len(collections),
        "n_datasets": total_datasets,
        "total_cell_count": total_cell_count,
        "n_unique_tissues": len(tissue_counts),
        "n_unique_cell_types": len(cell_type_counts),
        "n_unique_organisms": len(organism_counts),
        "n_unique_diseases": len(disease_counts),
    }

    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.cellxgene.analysis.json",
        source=state.project().relative(artifact_path),
        threshold_set="cellxgene-search",
        thresholds_applied={},
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("relays", relays)
    emit.line(f"CELLxGENE analysis: {slug_source!r}")
    emit.line(f"  Outcome: {assessment['outcome']}")
    emit.line(
        f"  {len(collections)} collections, {total_datasets} datasets, "
        f"{total_cell_count:,} total cells"
    )
    if top_tissues:
        emit.line("  Top tissues:")
        for t, c in top_tissues[:5]:
            emit.line(f"    {t}: {c}")
    if top_cell_types:
        emit.line("  Top cell types:")
        for ct, c in top_cell_types[:5]:
            emit.line(f"    {ct}: {c}")
    if organism_counts:
        emit.line(f"  Organisms: {dict(organism_counts)}")
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()
