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

"""`dde dice` — DICE immune cell subtype expression (bulk RNA-seq, TPM).

This tool answers: *which immune cell subtypes express this gene, and at
what level?*  DICE (Database of Immune Cell Expression, dice-database.org)
provides bulk RNA-seq TPM values across 15 immune cell types, with
individual donor replicates per cell type.

Two phases (docs/tool-design-guidance.md S3):

  search   one gene -> the DICE per-gene CSV, parsed into a structured
           artifact with mean TPM per cell type. Written verbatim to
           Layer 0 with a provenance sidecar.
  analyze  reads that artifact from disk, reports per-cell-type
           expression. Never touches the network.

Why this is separate from `expression`:

DICE measures expression in sorted/stimulated immune cells (in vitro),
not in bulk tissue (HPA) or whole blood (GTEx). The cell type vocabulary
is different (TH1, TH2, TH17, TFH, Tregs vs HPA's single "T-cells"),
the units differ from HPA nTPM, and the experimental conditions (in-vitro
stimulated/sorted cells) are not comparable to in-vivo tissue. Mixing them
would manufacture a composite that neither source published.

Design decision -- in-vitro relay:

    DICE expression data is derived from in-vitro stimulated or sorted
    immune cells. This is a standing property of the data source, not a
    per-query finding, and fires on every search that returns data.
    The relay is chosen (over a sidecar note) for the same reason as
    gtex.whole_blood_is_not_peripheral_blood: a reviewer running
    `dde relays` will see it, and a skill author writing an
    interpretation contract can name the code.

Error handling:

    DICE returns HTTP 500 with an HTML body for unknown genes. The shared
    HTTP client retries 500s, which is correct for transient failures but
    not for DICE's permanent "gene not found" response. This module
    tolerates 500 and inspects the Content-Type to distinguish the two:
    a 500 with text/html is a lookup refusal, not a server error.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any
from urllib.parse import quote

import click

from ..common import (
    AppState,
    emitter,
    from_option,
    load_thresholds,
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

TOOL = "dice"
ARTIFACT_CLASS = "genomics"

DICE_API = "https://dice-database.org/downloads/genes/expression"

# Cell type to immunological category mapping. Derived from the DICE
# database documentation and verified against the IFIH1 CSV response
# on 2026-08-21. The names here are the column headers in the CSV.
CELL_TYPE_CATEGORIES: dict[str, str] = {
    "TH1": "T cell",
    "TH2": "T cell",
    "TH17": "T cell",
    "TFH": "T cell",
    "TH1/17": "T cell",
    "NAIVE CD4": "T cell",
    "MEMORY TREG": "T cell",
    "NAIVE TREG": "T cell",
    "NAIVE CD4 ACTIVATED": "T cell",
    "NAIVE CD8": "T cell",
    "NAIVE CD8 ACTIVATED": "T cell",
    "NAIVE B": "B cell",
    "CLASSICAL MONOCYTE": "Monocyte",
    "NON-CLASSICAL MONOCYTE": "Monocyte",
    "NK": "NK cell",
}


def _fetch_dice(gene: str) -> tuple[bytes, str]:
    """GET the DICE per-gene expression CSV.

    Returns (raw response bytes, resolved Content-Type).

    DICE returns HTTP 500 with Content-Type text/html for unknown genes.
    The shared HTTP client retries 500s by default, which wastes retries
    on a permanent refusal. We tolerate 500 and inspect the response
    ourselves.
    """
    url = f"{DICE_API}/{quote(gene, safe='')}"

    response = http.request(
        "GET",
        url,
        qps=qps_for_host("dice-database.org"),
        timeout=60.0,
        tolerate_status=(500,),
    )

    content_type = response.headers.get("Content-Type", "")

    if response.status_code == 500 or "text/html" in content_type.lower():
        raise Refusal(
            f"DICE has no expression data for {gene!r}",
            detail=(
                f"HTTP {response.status_code}, Content-Type: {content_type}; "
                "DICE returns HTTP 500 with an HTML error page for unknown genes"
            ),
            remedy=(
                "check the gene symbol; DICE accepts HGNC gene symbols "
                "(e.g. IFIH1, TP53, BRCA1)"
            ),
        )

    return response.content, content_type


def _parse_csv(raw: bytes, gene: str) -> list[dict[str, Any]]:
    """Parse the DICE CSV into per-cell-type expression records.

    The CSV format (verified against IFIH1 on 2026-08-21):
    - Row 1: description string (single cell, ignored)
    - Rows 2+: cell type name in first column, followed by individual
      donor replicate TPM values.

    Returns a list of dicts, one per cell type, with mean TPM computed
    across all donor replicates.
    """
    text = raw.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))

    rows = list(reader)
    if len(rows) < 2:
        raise SchemaError(
            f"DICE CSV for {gene!r} has fewer than 2 rows",
            detail=f"got {len(rows)} row(s)",
        )

    # Skip description row (row 0).
    expression: list[dict[str, Any]] = []
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue

        cell_type = row[0].strip()

        # Parse individual donor replicate TPM values.
        tpm_values: list[float] = []
        for val in row[1:]:
            val = val.strip()
            if not val:
                continue
            try:
                tpm_values.append(float(val))
            except ValueError:
                continue

        if not tpm_values:
            continue

        mean_tpm = sum(tpm_values) / len(tpm_values)
        cell_type_upper = cell_type.upper()
        category = CELL_TYPE_CATEGORIES.get(cell_type_upper, "Other")

        expression.append(
            {
                "cell_type": cell_type,
                "tpm": round(mean_tpm, 4),
                "n_samples": len(tpm_values),
                "category": category,
            }
        )

    if not expression:
        raise SchemaError(
            f"DICE CSV for {gene!r} contains no parseable expression data",
            detail="no cell type rows with valid TPM values found",
        )

    return expression


def _build_artifact(gene: str, expression: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the structured dde.dice.v1 artifact."""
    # Find the top cell type by mean TPM.
    sorted_expr = sorted(expression, key=lambda e: e["tpm"], reverse=True)
    top = sorted_expr[0]

    return {
        "schema": "dde.dice.v1",
        "query": {"gene": gene.upper()},
        "summary": {
            "n_cell_types": len(expression),
            "max_tpm": top["tpm"],
            "top_cell_type": top["cell_type"],
        },
        "expression": expression,
    }


# ---------------------------------------------------------------------------
# Command group
# ---------------------------------------------------------------------------


@click.group()
def dice() -> None:
    """DICE immune cell subtype expression (bulk RNA-seq, TPM)."""


# Verb aliases — see docs/tool-design-guidance.md and issue #92.
#
#   fetch  = retrieve the record for a known identifier (gene, CID, …)
#   search = query and get back a result set
#
# Both verbs are accepted as aliases for discoverability.  The primary
# verb for this group is ``search`` (DICE returns expression across cell
# types for a gene query); ``fetch`` is the alias.


@dice.command("search")
@click.argument("gene")
@out_option
@output_options
@pass_state
def search_cmd(
    state: AppState,
    gene: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Search DICE for immune cell expression of GENE.

    GENE is a gene symbol (e.g. IFIH1, TP53). DICE returns bulk RNA-seq
    TPM values across 15 immune cell subtypes including TH1, TH2, TH17,
    TFH, Tregs, CD8 subtypes, monocyte subtypes, B cells, and NK cells.
    """
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)
    slug = sanitize_slug(gene.upper())

    raw, _content_type = _fetch_dice(gene)
    expression = _parse_csv(raw, gene)
    artifact = _build_artifact(gene, expression)

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint=DICE_API,
        parameters={
            "query_gene": gene,
            "resolved_gene": slug,
        },
    )
    sidecar.note(
        "source",
        "DICE (Database of Immune Cell Expression, La Jolla Institute for Immunology)",
    )
    sidecar.note("licence", "CC BY 4.0 (summary data, publicly accessible)")
    sidecar.note("data_type", "bulk RNA-seq TPM, individual donor replicates")
    sidecar.note("n_cell_types", artifact["summary"]["n_cell_types"])
    sidecar.note("top_cell_type", artifact["summary"]["top_cell_type"])
    sidecar.note("max_tpm", artifact["summary"]["max_tpm"])

    sidecar.warn(
        "DICE expression data is derived from in-vitro stimulated or sorted "
        "immune cells. Expression levels may differ from in-vivo tissue "
        "microenvironments. Cell isolation and culture conditions can alter "
        "gene expression profiles.",
        code="dice.in_vitro_not_in_vivo",
    )

    # Write verbatim CSV response.
    verbatim_path = target_dir / f"{slug}.dice-expression.csv"
    verbatim_path.write_bytes(raw)
    sidecar.add_output(verbatim_path)

    # Write structured artifact.
    artifact_path = target_dir / f"{slug}.dice-expression.artifact.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(artifact_path)

    # Write sidecar.
    meta_path = sidecar.write(target_dir / f"{slug}.dice-expression.meta.json")

    emit.data("gene", slug)
    emit.data("n_cell_types", artifact["summary"]["n_cell_types"])
    emit.data("max_tpm", artifact["summary"]["max_tpm"])
    emit.data("top_cell_type", artifact["summary"]["top_cell_type"])
    emit.path(verbatim_path, role="verbatim")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.flush()


@dice.command("analyze")
@click.argument("gene")
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    gene: str,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Summarise DICE immune cell expression from a stored search. No network."""
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = sanitize_slug(gene.upper())
    artifact_path = source_dir / f"{slug}.dice-expression.artifact.json"
    if not artifact_path.is_file():
        raise ArtifactError(
            f"no DICE expression artifact for {slug}",
            remedy=f"run `dde dice search {gene}` first",
        )

    artifact = provenance.read_json(artifact_path, "DICE expression artifact")
    if artifact.get("schema") != "dde.dice.v1":
        raise SchemaError(
            f"unexpected schema in {artifact_path.name}",
            detail=f"expected dde.dice.v1, got {artifact.get('schema')!r}",
        )

    expression = artifact.get("expression", [])

    thresholds = load_thresholds(state, "dice-expression")
    unresolved = thresholds.unresolved()

    relays: list[dict[str, str]] = []

    def add_relay(code: str, message: str) -> None:
        if not any(r["code"] == code for r in relays):
            relays.append(provenance.relay(code, message))

    if expression:
        add_relay(
            "dice.in_vitro_not_in_vivo",
            "DICE expression data is derived from in-vitro stimulated or sorted "
            "immune cells. Expression levels may differ from in-vivo tissue "
            "microenvironments. Cell isolation and culture conditions can alter "
            "gene expression profiles.",
        )

    # Build per-category summaries.
    categories: dict[str, list[dict[str, Any]]] = {}
    for entry in expression:
        cat = entry.get("category", "Other")
        categories.setdefault(cat, []).append(entry)

    category_summaries = []
    for cat, entries in sorted(categories.items()):
        tpm_values = [e["tpm"] for e in entries]
        category_summaries.append(
            {
                "category": cat,
                "n_cell_types": len(entries),
                "max_tpm": max(tpm_values),
                "mean_tpm": round(sum(tpm_values) / len(tpm_values), 4),
            }
        )

    sorted_expr = sorted(expression, key=lambda e: e["tpm"], reverse=True)
    top_cell_types = [
        {"cell_type": e["cell_type"], "tpm": e["tpm"], "category": e["category"]}
        for e in sorted_expr[:5]
    ]

    verdict = "expression_found" if expression else "no_expression"

    metrics = {
        "n_cell_types": len(expression),
        "max_tpm": sorted_expr[0]["tpm"] if sorted_expr else None,
        "top_cell_type": sorted_expr[0]["cell_type"] if sorted_expr else None,
        "expression_by_cell_type": [
            {"cell_type": e["cell_type"], "tpm": e["tpm"], "category": e["category"]}
            for e in sorted_expr
        ],
        "category_summaries": category_summaries,
    }
    assessment = {
        "verdict": verdict,
        "gene": slug,
        "n_cell_types": len(expression),
        "top_cell_types": top_cell_types,
    }

    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.dice-expression.analysis.json",
        source=state.project().relative(artifact_path),
        threshold_set=thresholds.tag,
        thresholds_applied=thresholds.applied(),
        metrics=metrics,
        assessment=assessment,
        threshold_sources=thresholds.sources(),
        threshold_provenance=thresholds.provenance,
        unresolved=unresolved,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("metrics", metrics)
    emit.data("relays", relays)
    emit.line(f"{slug} -> {verdict.upper()} ({len(expression)} cell type(s) from DICE)")
    if top_cell_types:
        top_lines = [
            f"  {t['cell_type']} ({t['category']}): {t['tpm']} TPM"
            for t in top_cell_types
        ]
        emit.line("top cell types:")
        for line in top_lines:
            emit.line(line)
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()


# Register ``fetch`` as an alias for ``search`` (issue #92).
dice.add_command(search_cmd, "fetch")
