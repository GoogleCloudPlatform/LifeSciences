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

"""`dde gtex` — GTEx whole-blood median gene expression (RNA-seq, TPM).

This tool answers: *what is the median RNA expression level of this gene
in whole blood, as measured by the GTEx consortium?*  It does not predict,
rank, or classify — GTEx publishes no expression-level cutoff analogous
to HPA's nTPM >= 1, so the raw median TPM is reported without a
threshold verdict.

Two phases (docs/tool-design-guidance.md §3):

  fetch    one gene -> the GTEx whole-blood median expression record,
           written verbatim to Layer 0 with a provenance sidecar. No
           judgment, no cutoffs.
  analyze  reads that artifact from disk, reports the median TPM and
           the tissue caveat. Never touches the network.

Why this is separate from `expression`:

GTEx and HPA measure different things in different ways. HPA reports
consensus nTPM from its own pipeline across 50 tissues; GTEx reports
median TPM from bulk RNA-seq of 755 whole-blood samples drawn from
femoral/subclavian veins. The units are different (TPM vs nTPM), the
tissue definitions are different, and blending them into a single
"expression level" would manufacture a composite that neither source
published. Issue #30 decided: build GTEx as its own tool.

Design decision — whole-blood-vs-peripheral-blood disposition:

    GTEx "whole blood" is drawn from femoral/subclavian veins and measured
    by bulk RNA-seq. It is a partial proxy for peripheral blood protein
    expression, not an equivalent measurement. This is true of every GTEx
    whole-blood query without exception — it is a standing property of the
    data source, not a per-query finding.

    Per tool-design-guidance.md §5.1, an unconditional relay is usually a
    smell: a warning that fires on every invocation is a property wearing
    a relay costume. However, the alternative (a sidecar field) is not
    mechanically enumerable by `dde relays`, which means a reviewer
    has to know to look for it. The relay is chosen here because:

    1. `dde relays` enumerates it mechanically — a reviewer running
       the enumeration will see it, and a skill author writing an
       interpretation contract can name the code.
    2. The obligation on the finding is specific and actionable: "report
       this as GTEx whole-blood RNA-seq, not as peripheral blood
       expression". An unconditional relay that says nothing actionable
       is noise; this one changes how the claim is worded.
    3. The cost of the alternative (a sidecar field nobody checks) is
       higher than the cost of a relay that fires on every call (a line
       in the relay list that is always present). The relay list is short
       and this entry is distinctive.

    Option A (relay code) is therefore implemented.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

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
from ..core.errors import ArtifactError, Refusal, SchemaError
from ..core.paths import confine_path, sanitize_slug
from ..core.qps import qps_for_host

GTEX_BASE = "https://gtexportal.org"
GTEX_API = f"{GTEX_BASE}/api/v2"

# The dataset version recorded in provenance. GTEx v8 is the current
# release as of 2026-08-19.
GTEX_DATASET = "gtex_v8"

# Gene reference parameters verified against the live API.
GENCODE_VERSION = "v26"
GENOME_BUILD = "GRCh38/hg38"

# Whole blood tissue identifier in GTEx's vocabulary.
TISSUE_ID = "Whole_Blood"

ENSG_RE = re.compile(r"^ENSG\d{11}$")
VERSIONED_ENSG_RE = re.compile(r"^ENSG\d{11}(\.\d+)?$")


# ---------------------------------------------------------------------------
# Phase 1 helpers
# ---------------------------------------------------------------------------


def _resolve_gene(query: str) -> tuple[str, str, str]:
    """Resolve a symbol or Ensembl ID to (gencode_id, symbol, how).

    Identity resolution discipline: if the lookup returns 0 matches,
    refuse (exit 9). If it returns >1 match, refuse (exit 9). Never
    rank and pick — the same principle as expression.py's _resolve_gene.

    Returns the versioned gencodeId (e.g. ENSG00000141510.16), the gene
    symbol, and how the resolution was performed.
    """
    query = query.strip()

    url = f"{GTEX_API}/reference/gene?" + urlencode(
        {
            "geneId": query,
            "gencodeVersion": GENCODE_VERSION,
            "genomeBuild": GENOME_BUILD,
        }
    )

    payload = http.get_json(url, qps=qps_for_host("gtexportal.org"), timeout=90.0)
    if not isinstance(payload, dict) or "data" not in payload:
        raise SchemaError(
            f"GTEx gene lookup for {query!r} did not return the expected shape",
            detail=f"got {type(payload).__name__}: {str(payload)[:200]}",
        )

    data = payload["data"]
    if not isinstance(data, list):
        raise SchemaError(
            "GTEx gene lookup 'data' field is not a list",
            detail=f"got {type(data).__name__}",
        )

    # For Ensembl IDs, match on the base ID (before the version suffix).
    if ENSG_RE.match(query.upper()):
        base_id = query.upper()
        exact = [
            r
            for r in data
            if isinstance(r, dict) and (r.get("gencodeId", "").split(".")[0] == base_id)
        ]
        how = "ensembl_id"
    else:
        # Symbol search: exact match only.
        exact = [
            r
            for r in data
            if isinstance(r, dict)
            and (r.get("geneSymbol", "").upper() == query.upper())
        ]
        how = "symbol"

    if not exact:
        near = ", ".join(
            sorted({r.get("geneSymbol", "?") for r in data if isinstance(r, dict)})[:8]
        )
        raise Refusal(
            f"GTEx has no gene matching {query!r}",
            detail=f"lookup returned: {near or 'nothing'}",
            remedy=(
                "check the gene symbol or Ensembl ID; GTEx gene search is "
                "exact-match only (e.g. 'HBA1' works, 'HBA' does not)"
            ),
        )

    if len(exact) > 1:
        listed = ", ".join(
            f"{r.get('gencodeId', '?')} ({r.get('geneSymbol', '?')})" for r in exact
        )
        raise Refusal(
            f"the query {query!r} matches {len(exact)} GTEx genes",
            detail=listed,
            remedy=(
                "pass the specific Ensembl gene ID you mean; this tool will "
                "not choose between them"
            ),
        )

    match = exact[0]
    gencode_id = match.get("gencodeId", "")
    symbol = match.get("geneSymbol", query)

    if not gencode_id:
        raise SchemaError(
            f"GTEx gene record for {query!r} has no gencodeId",
            detail=f"record: {json.dumps(match)[:300]}",
        )

    return gencode_id, symbol, how


def _fetch_expression_bytes(gencode_id: str) -> bytes:
    """Fetch the whole-blood median expression record and keep exact bytes.

    The bytes are what gets hashed and what gets written. Round-tripping
    through json.loads/json.dumps would change the digest for formatting
    reasons and break the provenance anchor.
    """
    url = f"{GTEX_API}/expression/medianGeneExpression?" + urlencode(
        {
            "gencodeId": gencode_id,
            "datasetId": GTEX_DATASET,
            "tissueSiteDetailId": TISSUE_ID,
        }
    )

    response = http.request(
        "GET", url, qps=qps_for_host("gtexportal.org"), timeout=90.0
    )
    body = response.content
    try:
        json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise SchemaError(
            "GTEx expression response is not valid JSON",
            detail=str(exc),
        ) from exc
    return body


# ---------------------------------------------------------------------------
# Command group
# ---------------------------------------------------------------------------


@click.group()
def gtex() -> None:
    """GTEx whole-blood median gene expression (RNA-seq, TPM)."""


@gtex.command("fetch")
@click.argument("gene")
@out_option
@output_options
@pass_state
def fetch_cmd(
    state: AppState, gene: str, out: str | None, as_json: bool, quiet: bool
) -> None:
    """Fetch the GTEx whole-blood median expression for GENE.

    GENE is a gene symbol or an Ensembl gene ID. Symbols that match more
    than one GTEx gene are refused rather than resolved.
    """
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir("gtex", out)

    gencode_id, symbol, how = _resolve_gene(gene)
    gencode_id = sanitize_slug(gencode_id)

    sidecar = provenance.Sidecar(
        tool="gtex",
        subcommand="fetch",
        endpoint=GTEX_BASE,
        parameters={
            "query": gene,
            "resolved_gencode_id": gencode_id,
            "resolved_symbol": symbol,
            "resolved_by": how,
            "tissue": TISSUE_ID,
            "dataset": GTEX_DATASET,
        },
    )
    sidecar.note("source", "GTEx Portal (Genotype-Tissue Expression project)")
    sidecar.note("licence", "dbGaP (public summary data, no access restriction)")
    sidecar.note("gtex_dataset", GTEX_DATASET)
    sidecar.note("gencode_version", GENCODE_VERSION)
    sidecar.note("genome_build", GENOME_BUILD)

    expression_bytes = _fetch_expression_bytes(gencode_id)
    expression_data = json.loads(expression_bytes.decode("utf-8"))

    # The 200-with-empty-data pattern: GTEx returns {"data": []} for genes
    # with no whole-blood data. This is a real answer (the gene is not
    # expressed / not measured in whole blood), not an error.
    data_records = expression_data.get("data", [])
    if not data_records:
        sidecar.note("whole_blood_data", "none")
        sidecar.note(
            "finding",
            f"GTEx holds no whole-blood expression data for {symbol} "
            f"({gencode_id}) in dataset {GTEX_DATASET}",
        )
    else:
        record = data_records[0]
        median_tpm = record.get("median")
        unit = record.get("unit", "TPM")
        sidecar.note("whole_blood_data", "present")
        sidecar.note("median_tpm", median_tpm)
        sidecar.note("unit", unit)

    # Write Layer 0 artifacts.
    expression_path = target_dir / f"{gencode_id}.gtex.json"
    expression_path.write_bytes(expression_bytes)
    sidecar.add_output(expression_path)

    meta_path = sidecar.write(target_dir / f"{gencode_id}.meta.json")

    emit.data("gene", symbol)
    emit.data("gencode_id", gencode_id)
    emit.data("resolved_by", how)
    emit.data("dataset", GTEX_DATASET)
    emit.data("tissue", TISSUE_ID)
    emit.data("has_data", bool(data_records))
    if data_records:
        emit.data("median_tpm", data_records[0].get("median"))
    emit.path(expression_path, role="expression")
    emit.path(meta_path, role="sidecar")
    emit.flush()


@gtex.command("analyze")
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
    """Report GTEx whole-blood expression from a fetched record. No network."""
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir("gtex", from_dir)
    target_dir = state.project().artifact_dir("gtex", out)

    expression_path, gencode_id = _locate(state, gene, source_dir)
    gencode_id = sanitize_slug(gencode_id)
    expression_data = provenance.read_json(expression_path, "GTEx expression record")

    if not isinstance(expression_data, dict) or "data" not in expression_data:
        raise SchemaError(
            "GTEx expression record does not have the expected shape",
            detail=f"expected a dict with 'data' key, got {type(expression_data).__name__}",
        )

    data_records = expression_data.get("data", [])

    relays: list[dict[str, str]] = []

    # The whole-blood-vs-peripheral-blood relay fires on every query.
    # See the module docstring for the design decision and rationale.
    relays.append(
        provenance.relay(
            "gtex.whole_blood_is_not_peripheral_blood",
            "GTEx whole blood is drawn from femoral/subclavian veins and "
            "measured by bulk RNA-seq (755 samples, median TPM). Report "
            "this as 'GTEx whole-blood RNA-seq expression', not as "
            "'peripheral blood expression'. It is a partial proxy, not "
            "an equivalent measurement.",
        )
    )

    # Load the gtex-expression threshold set. All values are UNRESOLVED by
    # default (GTEx publishes no expression-level cutoffs), but program-level
    # overrides via .dde/thresholds.yaml can fill in site-specific values.
    thresholds = load_thresholds(state, "gtex-expression")
    unresolved = thresholds.unresolved()

    if not data_records:
        # No whole-blood data: a real finding, not an error.
        assessment: dict[str, Any] = {
            "whole_blood_expression": "no_data",
            "finding": (
                f"GTEx holds no whole-blood expression data for {gencode_id} "
                f"in dataset {GTEX_DATASET}"
            ),
        }
        metrics: dict[str, Any] = {
            "gencode_id": gencode_id,
            "dataset": GTEX_DATASET,
            "tissue": TISSUE_ID,
            "median_tpm": None,
        }
    else:
        record = data_records[0]
        median_tpm = record.get("median")
        unit = record.get("unit", "TPM")
        gene_symbol = record.get("geneSymbol", gencode_id)

        assessment = {
            "whole_blood_expression": "measured",
            "median_tpm": median_tpm,
            "unit": unit,
            "gene_symbol": gene_symbol,
        }

        # Apply classification if program overrides have resolved the
        # thresholds; otherwise report the raw value with an explanation.
        if not unresolved:
            # All thresholds resolved (via program overrides) — classify.
            expressed_tpm = thresholds.get("expressed_tpm")
            high_tpm = thresholds.get("high_expression_tpm")
            low_tpm = thresholds.get("low_expression_tpm")
            if median_tpm is not None:
                if median_tpm < expressed_tpm:
                    classification = "not_expressed"
                elif median_tpm < low_tpm:
                    classification = "low"
                elif median_tpm < high_tpm:
                    classification = "moderate"
                else:
                    classification = "high"
                assessment["classification"] = classification
        else:
            # Thresholds are UNRESOLVED — no classification is possible.
            # GTEx publishes no expression-level cutoffs analogous to HPA's
            # "detected at nTPM >= 1", and GTEx TPM is not comparable to
            # HPA nTPM (different normalization, different tissue sets).
            # The raw median is reported without a verdict.
            assessment["note"] = (
                "No expression-level classification is applied. "
                f"Threshold set {thresholds.tag} has unresolved values "
                f"({', '.join(unresolved)}): GTEx publishes no defensible "
                "cutoff for classifying expression levels in TPM, and HPA's "
                "nTPM cutoffs do not transfer (different normalization). "
                "Set program overrides in .dde/thresholds.yaml to enable "
                "classification."
            )

        metrics = {
            "gencode_id": gencode_id,
            "gene_symbol": gene_symbol,
            "dataset": GTEX_DATASET,
            "tissue": TISSUE_ID,
            "median_tpm": median_tpm,
            "unit": unit,
            "ontology_id": record.get("ontologyId"),
        }

    analysis_path = provenance.write_analysis(
        target_dir / f"{gencode_id}.analysis.json",
        source=state.project().relative(expression_path),
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
    emit.data("relays", relays)
    if data_records:
        record = data_records[0]
        emit.line(
            f"{record.get('geneSymbol', gencode_id)} ({gencode_id}) — "
            f"GTEx whole-blood median"
        )
        emit.line(f"median: {record.get('median')} {record.get('unit', 'TPM')}")
    else:
        emit.line(f"{gencode_id} — no GTEx whole-blood data")
    for r in relays:
        emit.line(f"relay {r['code']}: {r['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()


def _locate(state: AppState, gene: str, target_dir: Path) -> tuple[Path, str]:
    """Find the fetched GTEx artifact for GENE.

    Returns (expression_path, gencode_id). Handles gene symbols by
    searching sidecars, same pattern as expression.py's _locate.
    """
    # Versioned gencodeId first — its dot would trick Path.suffix.
    if VERSIONED_ENSG_RE.match(gene.upper()):
        gencode_id = gene
        expression_path = target_dir / f"{gencode_id}.gtex.json"
        if not expression_path.is_file():
            raise ArtifactError(
                f"GTEx expression file not found: {expression_path}",
                remedy=f"run `dde gtex fetch {gene}` first",
            )
        return expression_path, gencode_id

    candidate = Path(gene)
    if candidate.suffix or "/" in gene:
        expression_path = (
            candidate if candidate.is_absolute() else state.project().root / candidate
        )
        # Confine to project root to prevent path traversal.
        confined = confine_path(state.project().root, expression_path)
        if confined is None:
            raise ArtifactError(
                f"path {gene!r} escapes project root",
                remedy="provide a path within the project directory",
            )
        expression_path = confined
        if not expression_path.is_file():
            raise ArtifactError(f"GTEx expression file not found: {expression_path}")
        gencode_id = expression_path.name.replace(".gtex.json", "")
        return expression_path, gencode_id

    # Bare Ensembl ID without version: search for files matching the base.
    if ENSG_RE.match(gene.upper()):
        base_id = gene.upper()
        matches = sorted(target_dir.glob(f"{base_id}.*.gtex.json"))
        if not matches:
            raise ArtifactError(
                f"no fetched GTEx expression for {gene!r} under {target_dir}",
                remedy=f"run `dde gtex fetch {gene}` first",
            )
        if len(matches) > 1:
            raise Refusal(
                f"{gene!r} matches {len(matches)} fetched GTEx records",
                detail=", ".join(m.name for m in matches),
                remedy="pass the full versioned gencodeId you mean",
            )
        gencode_id = matches[0].name.replace(".gtex.json", "")
        return matches[0], gencode_id

    # Symbol: recover gencode_id from sidecars.
    matches = [
        meta
        for meta in sorted(target_dir.glob("ENSG*.meta.json"))
        if (json.loads(meta.read_text(encoding="utf-8")).get("parameters") or {})
        .get("resolved_symbol", "")
        .upper()
        == gene.upper()
    ]
    if not matches:
        raise ArtifactError(
            f"no fetched GTEx expression for {gene!r} under {target_dir}",
            remedy=f"run `dde gtex fetch {gene}` first",
        )
    if len(matches) > 1:
        raise Refusal(
            f"{gene!r} matches {len(matches)} fetched GTEx records",
            detail=", ".join(m.name for m in matches),
            remedy="pass the gencodeId you mean",
        )
    gencode_id = matches[0].name.replace(".meta.json", "")
    expression_path = target_dir / f"{gencode_id}.gtex.json"
    if not expression_path.is_file():
        raise ArtifactError(
            f"GTEx expression file not found: {expression_path}",
            remedy=f"run `dde gtex fetch {gene}` first",
        )
    return expression_path, gencode_id
