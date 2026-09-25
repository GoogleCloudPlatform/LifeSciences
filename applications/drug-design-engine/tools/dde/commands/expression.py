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

"""`dde expression` — measured human RNA expression from the Human Protein Atlas.

This tool answers one question and refuses the neighbouring ones: *has
somebody measured how much of this transcript is present in this tissue?*
It does not predict, it does not rank targets, and it does not decide
whether an expression level is therapeutically interesting.

Two phases (docs/tool-design-guidance.md §3):

  fetch    one gene -> the HPA per-gene annotation record plus the full
           50-tissue consensus nTPM profile, written verbatim to Layer 0
           with a provenance sidecar. No judgment, no cutoffs.
  analyze  reads those artifacts from disk, applies the named
           `expression` threshold set, emits a per-tissue verdict.
           Never touches the network.

Three properties of the HPA API drive most of the code below, and each
one silently manufactures a wrong answer if it is not handled:

1. **`search_download.php` is a search, not a lookup.** `search=WEE1`
   returns two genes — WEE1 (ENSG00000166483) and WEE1B/WEE2
   (ENSG00000214102) — and a caller taking the first row gets whichever
   HPA ranked highest. We resolve symbols separately, demand exactly one
   exact match, and then query by Ensembl ID, which does return one row.

2. **Invalid tissue columns vanish silently.** `t_RNA_not_a_tissue`
   produces no error and no column; the response is simply narrower.
   Eight plausible names (`skin`, `stomach`, `endometrium`,
   `hypophysis`, `medulla_oblongata`, `pons`, `white_matter`,
   `thalamus`) drop this way. A tool that did not check would report
   "not measured in skin" for a gene HPA measures at 55.1 nTPM in
   `skin 1`. Every requested column is verified present in the response.

3. **The per-gene JSON carries only *specific* nTPM values.** For a
   tissue-enriched gene it lists the enriched tissues and nothing else;
   for a low-specificity gene the field is `null`. Absence from that
   dict is not absence of expression, so the full profile is fetched
   separately rather than inferred from the record.

4. **Single-cell columns return null, not zero.** Unlike the tissue
   profile (which always returns a numeric string for all 50 columns),
   the single-cell profile returns `null` for cell types where a gene
   has no measured expression. The column is present — proving the
   column code was accepted — but carries no value. This is recorded
   as 0.0 nCPM, not treated as an error.

HPA exposes no release version through the API — not in the body, not in
the headers. We scrape it from `/about/download` as a convenience label
and anchor provenance on the SHA-256 of the exact response payload
instead, which the sidecar records for every output. If the scrape
fails we still write the artifact and relay
`expression.release_version_unknown`: the measurement is real and
nothing is invented, and `analyze` re-runs from the stored bytes, so
what is lost is comparability across releases rather than
reproducibility.
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
from ..core.errors import ArtifactError, Refusal, SchemaError, UsageError
from ..core.gene import resolve_gene
from ..core.paths import confine_path, sanitize_slug
from ..core.qps import qps_for_host

HPA_BASE = "https://www.proteinatlas.org"
SEARCH_URL = f"{HPA_BASE}/api/search_download.php"
DOWNLOAD_PAGE = f"{HPA_BASE}/about/download"

ENSG_RE = re.compile(r"^ENSG\d{11}$")

# The 50 consensus tissues verified to return a column on 2026-08-18
# against HPA 25.1. This list is deliberately hardcoded rather than
# discovered: HPA gives no endpoint that enumerates valid column codes,
# and an unvalidated name is indistinguishable from a tissue in which
# the gene is not expressed. When HPA adds a tissue this list goes
# stale, which surfaces as a missing tissue rather than as a wrong
# number — the safe direction to be wrong in.
#
# Names are HPA's own response labels; the column code is the name with
# spaces replaced by underscores and `t_RNA_` prepended.
TISSUES: tuple[str, ...] = (
    "adipose tissue",
    "adrenal gland",
    "amygdala",
    "appendix",
    "basal ganglia",
    "bone marrow",
    "breast",
    "cerebellum",
    "cerebral cortex",
    "cervix",
    "choroid plexus",
    "colon",
    "duodenum",
    "endometrium 1",
    "epididymis",
    "esophagus",
    "fallopian tube",
    "gallbladder",
    "heart muscle",
    "hippocampal formation",
    "hypothalamus",
    "kidney",
    "liver",
    "lung",
    "lymph node",
    "midbrain",
    "ovary",
    "pancreas",
    "parathyroid gland",
    "pituitary gland",
    "placenta",
    "prostate",
    "rectum",
    "retina",
    "salivary gland",
    "seminal vesicle",
    "skeletal muscle",
    "skin 1",
    "small intestine",
    "smooth muscle",
    "spinal cord",
    "spleen",
    "stomach 1",
    "testis",
    "thymus",
    "thyroid gland",
    "tongue",
    "tonsil",
    "urinary bladder",
    "vagina",
)

# Names a specialist will plausibly type that HPA does not accept. These
# resolve `--tissue` arguments only; they never enter a column request.
# Every one of these was observed to be silently dropped by the API.
TISSUE_ALIASES: dict[str, str] = {
    "skin": "skin 1",
    "stomach": "stomach 1",
    "endometrium": "endometrium 1",
    "hypophysis": "pituitary gland",
    "pituitary": "pituitary gland",
    "heart": "heart muscle",
    "brain cortex": "cerebral cortex",
    "cortex": "cerebral cortex",
    "muscle": "skeletal muscle",
    "adipose": "adipose tissue",
    "fat": "adipose tissue",
    "thyroid": "thyroid gland",
    "adrenal": "adrenal gland",
    "salivary": "salivary gland",
    "bladder": "urinary bladder",
}

# The 154 single-cell types verified to return a column on 2026-08-19
# against HPA 25.1. Same rationale as TISSUES: hardcoded rather than
# discovered, because an unrecognised column code is silently dropped.
# Names are HPA's own response labels; the column code is the name with
# spaces replaced by underscores and `sc_RNA_` prepended.
#
# Note: HPA preserves case exactly as listed (cDC, monocytes, pDCs are
# lowercase-initial), and two names contain non-ASCII or punctuation:
# "Müller glia" (ü) and "Pituicytes/FSCs" (/).
CELL_TYPES: tuple[str, ...] = (
    "Adipocytes",
    "Adrenal cortex cells",
    "Adrenal medulla cells",
    "Alveolar cells type 1",
    "Alveolar cells type 2",
    "Astrocytes",
    "B-cells",
    "Basal keratinocytes",
    "Basal prostatic cells",
    "Bergmann glia",
    "Brain excitatory neurons",
    "Brain inhibitory neurons",
    "Breast hormone-responsive cells",
    "Breast lactating cells",
    "Breast myoepithelial cells",
    "Breast secretory cells",
    "Cardiomyocytes",
    "cDC",
    "Cholangiocytes",
    "Choroid plexus epithelial cells",
    "Colonocytes",
    "Cone photoreceptor cells",
    "Conjunctival goblet cells",
    "Corticotrophs",
    "Cytotrophoblasts",
    "Decidual stromal cells",
    "Differentiating spermatogonia",
    "Distal convoluted tubule cells",
    "Early primary spermatocytes",
    "Early spermatids",
    "Endometrial ciliated cells",
    "Endometrial glandular cells",
    "Endometrial luminal cells",
    "Endometrial secretory cells",
    "Endometrial stromal cells",
    "Enteric stem cells",
    "Enteric transient amplifying cells",
    "Enterocytes",
    "Ependymal cells",
    "Epicardial cells",
    "Epididymal basal cells",
    "Epididymal clear cells",
    "Epididymal efferent duct absorptive cells",
    "Epididymal efferent duct ciliated cells",
    "Epididymal principal cells",
    "Erythrocyte progenitors",
    "Erythrocytes",
    "Esophageal apical cells",
    "Esophageal basal cells",
    "Esophageal suprabasal cells",
    "Extravillous trophoblasts",
    "Fallopian secretory cells",
    "Fallopian tube ciliated cells",
    "Fibro-adipogenic progenitors",
    "Fibroblasts",
    "Foveolar cells",
    "Gastric chief cells",
    "Gastric progenitor cells",
    "Goblet cells",
    "Gonadotrophs",
    "Granulosa cells",
    "Hematopoietic stem cells",
    "Hepatic stellate cells",
    "Hepatocytes",
    "Hofbauer cells",
    "Innate lymphoid cells",
    "Kupffer cells",
    "Lacrimal acinar cells",
    "Lactotrophs",
    "Late primary spermatocytes",
    "Late spermatids",
    "Leydig cells",
    "Loop of henle epithelial cells",
    "Lymphatic endothelial cells",
    "Macrophages",
    "Mast cells",
    "Medullary thymic epithelial cells",
    "Megakaryocyte progenitors",
    "Megakaryocyte-Erythroid progenitors",
    "Megakaryocytes",
    "Melanocytes",
    "Mesothelial cells",
    "Microglia",
    "Migrating cytotrophoblasts",
    "Monocyte progenitors",
    "monocytes",
    "Mucous neck cells",
    "Müller glia",
    "Myonuclei",
    "Myosatellite cells",
    "Neuroendocrine cells",
    "Neutrophil progenitors",
    "Neutrophils",
    "NK-cells",
    "Ocular epithelial cells",
    "Oligodendrocyte progenitor cells",
    "Oligodendrocytes",
    "Oocytes",
    "Other brain neurons",
    "Ovarian stromal cells",
    "Pancreatic acinar cells",
    "Pancreatic duct cells",
    "Pancreatic islet cells",
    "Paneth cells",
    "Papillary tip epithelial cells",
    "Parietal cells",
    "pDCs",
    "Pericytes",
    "Peritubular myoid cells",
    "Pituicytes/FSCs",
    "Pituitary stem cells",
    "Plasma cells",
    "Platelets",
    "Podocytes",
    "Prostatic club cells",
    "Prostatic glandular cells",
    "Prostatic hillock cells",
    "Proximal tubule cells",
    "Renal collecting duct intercalated cells",
    "Renal collecting duct principal cells",
    "Renal connecting tubule cells",
    "Respiratory basal cells",
    "Respiratory ciliated cells",
    "Respiratory deuterosomal cells",
    "Respiratory ionocytes",
    "Respiratory secretory cells",
    "Retinal amacrine cells",
    "Retinal bipolar cells",
    "Retinal ganglion cells",
    "Retinal horizontal cells",
    "Retinal pigment epithelial cells",
    "Rod photoreceptor cells",
    "Salivary acinar cells",
    "Salivary basal cells",
    "Salivary duct cells",
    "Salivary ionocytes",
    "Salivary myoepithelial cells",
    "Schwann cells",
    "Sertoli cells",
    "Smooth muscle cells",
    "Somatotrophs",
    "Submucosal glandular cells",
    "Suprabasal keratinocytes",
    "Syncytiotrophoblasts",
    "T-cells",
    "Thymic myoid cells",
    "Thymocytes",
    "Thyrotrophs",
    "Transitional alveolar cells",
    "Tuft cells",
    "Undifferentiated spermatogonia",
    "Urothelial cells",
    "Vascular endothelial cells",
    "Vascular smooth muscle cells",
)

TISSUE_COLUMN_RE = re.compile(r"^Tissue RNA - (.+) \[nTPM\]$")
CELL_TYPE_COLUMN_RE = re.compile(r"^Single Cell Type RNA - (.+) \[nCPM\]$")

# `Human Protein Atlas version 25.1`. Anchored on the full product name
# because the page also says "version 16 of the Human Protein Atlas" in
# a historical note and "version 109" about Ensembl.
VERSION_RE = re.compile(r"Human Protein Atlas version (\d+(?:\.\d+)*)")

# Field in the per-gene record that is non-null exactly when HPA holds
# single-cell RNA data for the gene.
SINGLE_CELL_FIELD = "RNA single cell type specificity"

# Distribution field from the per-gene record, used in the single-cell
# analysis metrics.
SC_DISTRIBUTION_FIELD = "RNA single cell type distribution"


def _column_code(tissue: str) -> str:
    return "t_RNA_" + tissue.replace(" ", "_")


def _normalise_tissue(name: str) -> str:
    """Map a user-supplied tissue name onto an HPA label, or fail."""
    key = " ".join(name.strip().lower().split())
    if key in {t.lower() for t in TISSUES}:
        return next(t for t in TISSUES if t.lower() == key)
    if key in TISSUE_ALIASES:
        return TISSUE_ALIASES[key]
    close = sorted(t for t in TISSUES if key in t.lower() or t.lower() in key)
    raise UsageError(
        f"{name!r} is not an HPA consensus tissue",
        detail=(f"did you mean: {', '.join(close)}" if close else None),
        remedy="run `dde expression tissues` for the 50 measured tissues",
    )


# ---------------------------------------------------------------------------
# Phase 1 helpers
# ---------------------------------------------------------------------------


def _hpa_release() -> tuple[str | None, str | None]:
    """Scrape the HPA release label. Returns (version, failure_reason)."""
    try:
        response = http.request(
            "GET", DOWNLOAD_PAGE, qps=qps_for_host("www.proteinatlas.org"), timeout=45.0
        )
    except Exception as exc:  # any transport or status failure
        return None, f"{type(exc).__name__}: {exc}"
    match = VERSION_RE.search(response.text or "")
    if not match:
        return None, f"no 'Human Protein Atlas version N' string at {DOWNLOAD_PAGE}"
    return match.group(1), None


def _resolve_gene(query: str) -> tuple[str, str, str]:
    """Resolve a symbol or Ensembl ID to (ensembl, symbol, how).

    A symbol search that matches more than one gene is a hard failure.
    HPA ranks its hits and we do not know its ranking rule; picking the
    top one for `WEE1` silently chooses between WEE1 and WEE2.
    """
    query = query.strip()
    if ENSG_RE.match(query.upper()):
        ensembl = query.upper()
        rows = _search(ensembl, ["g", "eg"])
        exact = [r for r in rows if r.get("Ensembl") == ensembl]
        if not exact:
            raise Refusal(
                f"HPA has no record for {ensembl}",
                remedy="check the Ensembl gene ID, or search by symbol instead",
            )
        return ensembl, exact[0].get("Gene") or ensembl, "ensembl_id"

    rows = _search(query, ["g", "eg"])
    exact = [r for r in rows if (r.get("Gene") or "").upper() == query.upper()]
    # These are refusals, not endpoint errors. The endpoint worked; the
    # tool is declining to guess. Retrying the same symbol will always
    # be declined, which is what exit 9 tells the caller and what exit 6
    # ("retry later") would have told them wrongly.
    if not exact:
        near = ", ".join(sorted({r.get("Gene", "?") for r in rows})[:8])
        raise Refusal(
            f"no HPA gene has the exact symbol {query!r}",
            detail=f"search returned: {near or 'nothing'}",
            remedy="pass the Ensembl gene ID (ENSG...) instead",
        )
    if len(exact) > 1:
        listed = ", ".join(f"{r['Ensembl']} ({r.get('Gene')})" for r in exact)
        raise Refusal(
            f"the symbol {query!r} matches {len(exact)} HPA genes",
            detail=listed,
            remedy=(
                "pass the Ensembl gene ID you mean; this tool will not choose "
                "between them, because the wrong choice produces a clean-looking "
                "profile for the wrong gene"
            ),
        )
    return exact[0]["Ensembl"], exact[0]["Gene"], "symbol"


def _search(term: str, columns: list[str]) -> list[dict[str, Any]]:
    url = f"{SEARCH_URL}?" + urlencode(
        {
            "search": term,
            "format": "json",
            "columns": ",".join(columns),
            "compress": "no",
        }
    )
    payload = http.get_json(url, qps=qps_for_host("www.proteinatlas.org"), timeout=90.0)
    if not isinstance(payload, list):
        raise SchemaError(
            f"HPA search for {term!r} did not return a list",
            detail=f"got {type(payload).__name__}",
        )
    return [r for r in payload if isinstance(r, dict)]


def _fetch_bytes(url: str, what: str) -> bytes:
    """Fetch a payload and keep the exact bytes.

    The bytes are what gets hashed and what gets written. Round-tripping
    through `json.loads`/`json.dumps` would change the digest for
    formatting reasons and break the provenance anchor.
    """
    response = http.request(
        "GET", url, qps=qps_for_host("www.proteinatlas.org"), timeout=90.0
    )
    body = response.content
    try:
        json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise SchemaError(f"{what} is not valid JSON", detail=str(exc)) from exc
    return body


def _tissue_row(
    payload: Any, ensembl: str, label: str = "tissue profile"
) -> dict[str, Any]:
    if not isinstance(payload, list):
        raise SchemaError(f"HPA {label} did not return a list")
    rows = [r for r in payload if isinstance(r, dict) and r.get("Ensembl") == ensembl]
    if len(rows) != 1:
        raise SchemaError(
            f"HPA {label} returned {len(rows)} rows for {ensembl}, expected 1",
            detail="the query was by Ensembl ID, which should be unique",
        )
    return rows[0]


def _check_columns(row: dict[str, Any]) -> dict[str, float]:
    """Parse the tissue columns, failing if any requested one is missing.

    This is the guard for HPA's silent-drop behaviour. A dropped column
    is not a gap in the data — it is a name the API did not recognise,
    and treating it as "not measured" is how a tool invents a
    not-expressed verdict.
    """
    present: dict[str, float] = {}
    for key, value in row.items():
        match = TISSUE_COLUMN_RE.match(key)
        if not match:
            continue
        label = match.group(1)
        try:
            present[label] = float(value)
        except (TypeError, ValueError) as e:
            raise SchemaError(
                f"tissue column {label!r} carried a non-numeric nTPM: {value!r}"
            ) from e
    missing = [t for t in TISSUES if t not in present]
    if missing:
        raise SchemaError(
            f"HPA returned no column for {len(missing)} requested tissue(s)",
            detail=", ".join(missing),
            remedy=(
                "HPA drops unrecognised column codes without an error, so this "
                "means the hardcoded tissue list in commands/expression.py no "
                "longer matches the current release. Re-validate it rather than "
                "reading the gap as absent expression."
            ),
        )
    return present


def _sc_column_code(cell_type: str) -> str:
    return "sc_RNA_" + cell_type.replace(" ", "_")


def _check_sc_columns(row: dict[str, Any]) -> dict[str, float]:
    """Parse the single-cell columns, failing if any requested one is missing.

    Same guard as `_check_columns` but for the single-cell column set.

    Unlike the tissue API which always returns numeric strings for all 50
    columns, the single-cell API returns `null` for some cell types on
    some genes (observed on HPA 25.1). A null column is present in the
    response — which proves the column code was accepted — but carries no
    measurement. We record it as 0.0 rather than failing, because the
    absence of expression is the datum, not an error.
    """
    present: dict[str, float] = {}
    for key, value in row.items():
        match = CELL_TYPE_COLUMN_RE.match(key)
        if not match:
            continue
        label = match.group(1)
        if value is None:
            present[label] = 0.0
            continue
        try:
            present[label] = float(value)
        except (TypeError, ValueError) as e:
            raise SchemaError(
                f"cell type column {label!r} carried a non-numeric nCPM: {value!r}"
            ) from e
    missing = [ct for ct in CELL_TYPES if ct not in present]
    if missing:
        raise SchemaError(
            f"HPA returned no column for {len(missing)} requested cell type(s)",
            detail=", ".join(missing),
            remedy=(
                "HPA drops unrecognised column codes without an error, so this "
                "means the hardcoded cell type list in commands/expression.py no "
                "longer matches the current release. Re-validate it rather than "
                "reading the gap as absent expression."
            ),
        )
    return present


def _normalise_cell_type(name: str) -> str:
    """Map a user-supplied cell type name onto an HPA label, or fail."""
    key = " ".join(name.strip().split())
    # Exact match (case-insensitive)
    for ct in CELL_TYPES:
        if ct.lower() == key.lower():
            return ct
    # Substring match for suggestions
    close = sorted(
        ct
        for ct in CELL_TYPES
        if key.lower() in ct.lower() or ct.lower() in key.lower()
    )
    raise UsageError(
        f"{name!r} is not an HPA single-cell type",
        detail=(f"did you mean: {', '.join(close)}" if close else None),
        remedy="run `dde expression cell-types` for the 154 measured cell types",
    )


# ---------------------------------------------------------------------------
# Command group
# ---------------------------------------------------------------------------


@click.group()
def expression() -> None:
    """Measured human RNA expression (Human Protein Atlas, CC BY 4.0)."""


@expression.command("tissues")
def tissues_cmd() -> None:
    """List the HPA consensus tissues this tool measures. No network."""
    click.echo(f"{len(TISSUES)} consensus tissues (HPA nTPM):")
    for tissue in TISSUES:
        click.echo(f"  {tissue}")
    click.echo("\naccepted aliases:")
    for alias, target in sorted(TISSUE_ALIASES.items()):
        click.echo(f"  {alias} -> {target}")


@expression.command("cell-types")
def cell_types_cmd() -> None:
    """List the HPA single-cell types this tool measures. No network."""
    click.echo(f"{len(CELL_TYPES)} single-cell types (HPA nCPM):")
    for cell_type in CELL_TYPES:
        click.echo(f"  {cell_type}")


# Verb aliases — see docs/tool-design-guidance.md and issue #92.
#
#   fetch  = retrieve the record for a known identifier (gene, CID, …)
#   search = query and get back a result set
#
# Both verbs are accepted as aliases for discoverability.  The primary
# verb for this group is ``fetch`` (HPA resolves one gene to one
# expression profile); ``search`` is the alias.


@expression.command("fetch")
@click.argument("gene")
@out_option
@output_options
@pass_state
def fetch_cmd(
    state: AppState, gene: str, out: str | None, as_json: bool, quiet: bool
) -> None:
    """Fetch the HPA record and full tissue profile for GENE.

    GENE is a gene symbol or an Ensembl gene ID. Symbols that match more
    than one HPA gene are refused rather than resolved.
    """
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir("expression", out)

    # ── HGNC gene symbol resolution (#147) ────────────────────────────
    gene_res = resolve_gene(gene)
    if not gene_res.resolved:
        suggestions = ", ".join(gene_res.suggestions) if gene_res.suggestions else ""
        hint = f" Did you mean: {suggestions}?" if suggestions else ""
        provenance.relay(
            "gene.unresolved_symbol",
            f"Gene symbol {gene!r} could not be resolved via HGNC. "
            f"No query was attempted. This is a lookup failure, not "
            f"evidence of gene absence.{hint}",
        )
        raise Refusal(
            f"could not resolve {gene!r} to a known gene via HGNC",
            detail=f"suggestions: {suggestions}"
            if suggestions
            else "no near matches found",
            remedy="check the gene symbol or pass an Ensembl gene ID (ENSG...)",
        )
    echo = gene_res.echo_line()
    if echo:
        emit.line(echo)
    # Use Ensembl ID from HGNC when available for more reliable HPA lookup;
    # fall back to canonical symbol, then original input.
    query_gene = gene_res.ensembl_id or gene_res.canonical_symbol or gene

    release, release_failure = _hpa_release()
    try:
        ensembl, symbol, how = _resolve_gene(query_gene)
    except Refusal as e:
        canonical = gene_res.canonical_symbol or gene
        provenance.relay(
            "expression.no_data_found",
            f"Gene {canonical} resolved successfully via HGNC "
            f"({gene_res.source}) but no expression data found in HPA. "
            f"This is a data gap, not evidence of non-expression.",
        )
        raise Refusal(
            f"gene {canonical!r} resolved via HGNC but HPA has no record",
            detail=(
                f"HGNC resolved {gene!r} to {canonical} (source: {gene_res.source})"
            ),
            remedy=(
                "HPA may not index this gene. This is a data gap, not "
                "evidence of non-expression."
            ),
        ) from e

    sidecar = provenance.Sidecar(
        tool="expression",
        subcommand="fetch",
        endpoint=HPA_BASE,
        parameters={
            "query": gene,
            "resolved_ensembl": ensembl,
            "resolved_symbol": symbol,
            "resolved_by": how,
            "tissues_requested": len(TISSUES),
            "gene_resolution": gene_res.to_dict(),
        },
    )
    sidecar.note("source", "Human Protein Atlas")
    sidecar.note("licence", "CC BY 4.0")
    sidecar.note("hpa_release", release)

    if release is None:
        sidecar.warn(
            "the HPA release version could not be determined; the artifact is "
            f"identified by payload SHA-256 only ({release_failure})",
            code="expression.release_version_unknown",
        )

    record_bytes = _fetch_bytes(f"{HPA_BASE}/{ensembl}.json", "HPA per-gene record")
    record = json.loads(record_bytes.decode("utf-8"))
    if isinstance(record, list):  # defensive: the endpoint has returned both shapes
        record = record[0] if record else {}
    if not isinstance(record, dict):
        raise SchemaError(f"HPA per-gene record for {ensembl} is not an object")

    columns = ["g", "gs", "eg"] + [_column_code(t) for t in TISSUES]
    profile_url = f"{SEARCH_URL}?" + urlencode(
        {
            "search": ensembl,
            "format": "json",
            "columns": ",".join(columns),
            "compress": "no",
        }
    )
    profile_bytes = _fetch_bytes(profile_url, "HPA tissue profile")
    row = _tissue_row(json.loads(profile_bytes.decode("utf-8")), ensembl)
    measured = _check_columns(row)  # raises on a silently dropped column
    sidecar.note("tissues_returned", len(measured))

    record_path = target_dir / f"{ensembl}.hpa.json"
    profile_path = target_dir / f"{ensembl}.tissue.json"
    record_path.write_bytes(record_bytes)
    profile_path.write_bytes(profile_bytes)
    sidecar.add_output(record_path)
    sidecar.add_output(profile_path)

    meta_path = sidecar.write(target_dir / f"{ensembl}.meta.json")

    emit.data("gene", symbol)
    emit.data("ensembl", ensembl)
    emit.data("resolved_by", how)
    emit.data("hpa_release", release)
    emit.data("tissues", len(measured))
    emit.path(record_path, role="record")
    emit.path(profile_path, role="tissue_profile")
    emit.path(meta_path, role="sidecar")
    emit.flush()


@expression.command("analyze")
@click.argument("gene")
@click.option(
    "--tissue",
    "query_tissues",
    multiple=True,
    help="Tissue to answer for explicitly. Repeatable. Omit for a whole-profile "
    "summary. Unknown names fail rather than resolving to nothing.",
)
@click.option(
    "--expressed-ntpm",
    type=float,
    default=None,
    help="Override the expressed_ntpm threshold for this invocation.",
)
@click.option(
    "--enriched-fold",
    type=float,
    default=None,
    help="Override the enriched_fold threshold for this invocation.",
)
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    gene: str,
    query_tissues: tuple[str, ...],
    expressed_ntpm: float | None,
    enriched_fold: float | None,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Apply expression thresholds to a fetched profile. No network."""
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir("expression", from_dir)
    target_dir = state.project().artifact_dir("expression", out)

    profile_path, record_path, ensembl = _locate(state, gene, source_dir)
    row = _tissue_row(provenance.read_json(profile_path, "tissue profile"), ensembl)
    measured = _check_columns(row)
    record = provenance.read_json(record_path, "HPA per-gene record")
    if isinstance(record, list):
        record = record[0] if record else {}

    thresholds = load_thresholds(
        state,
        "expression",
        {"expressed_ntpm": expressed_ntpm, "enriched_fold": enriched_fold},
    )
    cutoff = thresholds.get("expressed_ntpm")
    fold = thresholds.get("enriched_fold")

    wanted = [_normalise_tissue(t) for t in query_tissues] or list(TISSUES)
    verdicts = {
        tissue: ("expressed" if measured[tissue] >= cutoff else "not_detected")
        for tissue in wanted
    }

    ranked = sorted(measured.items(), key=lambda kv: kv[1], reverse=True)
    top_tissue, top_value = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    others_mean = (
        (sum(v for _, v in ranked[1:]) / (len(ranked) - 1)) if len(ranked) > 1 else 0.0
    )

    if top_value < cutoff:
        computed = "not detected"
    elif runner_up > 0 and top_value >= fold * runner_up:
        computed = "tissue enriched"
    elif others_mean > 0 and top_value >= fold * others_mean:
        computed = "tissue enhanced"
    else:
        computed = "low tissue specificity"

    curated = record.get("RNA tissue specificity")
    detected_in = sum(1 for v in measured.values() if v >= cutoff)

    relays: list[dict[str, str]] = []

    def add_relay(code: str, message: str) -> None:
        if not any(r["code"] == code for r in relays):
            relays.append(provenance.relay(code, message))

    if any(v == "not_detected" for v in verdicts.values()):
        negatives = [t for t, v in verdicts.items() if v == "not_detected"]
        add_relay(
            "expression.absent_is_not_evidence",
            f"{len(negatives)} not_detected verdict(s) rest on one consensus "
            f"bulk RNA dataset (HPA): {', '.join(negatives[:6])}"
            + (" …" if len(negatives) > 6 else ""),
        )

    if record.get(SINGLE_CELL_FIELD):
        add_relay(
            "expression.tissue_resolution_only",
            f"HPA holds single-cell RNA data for this gene "
            f"({SINGLE_CELL_FIELD}: {record[SINGLE_CELL_FIELD]!r}); this verdict "
            "is a whole-tissue average. Run `dde expression fetch-single-cell` "
            "and `dde expression analyze-single-cell` for cell-type resolution",
        )
    else:
        add_relay(
            "expression.single_cell_unavailable",
            "HPA holds no single-cell RNA data for this gene, so the bulk "
            "tissue consensus is the best resolution obtainable here",
        )

    warnings: list[str] = []
    if curated and computed != str(curated).lower():
        warnings.append(
            f"recomputed specificity {computed!r} disagrees with HPA's curated "
            f"{str(curated).lower()!r}; HPA's classifier uses the full "
            "expression matrix, this one uses the 50 consensus tissues"
        )

    unresolved = thresholds.unresolved()
    if "low_confidence_ntpm" in unresolved:
        warnings.append(
            "no near-threshold confidence band is applied: HPA publishes no "
            "such cutoff and low_confidence_ntpm is UNRESOLVED, so a value of "
            f"{cutoff} nTPM is reported as expressed with no margin"
        )

    assessment = {
        "verdict_by_tissue": verdicts,
        "specificity_computed": computed,
        "specificity_curated_by_hpa": curated,
        "highest_tissue": top_tissue,
        "highest_ntpm": top_value,
        "tissues_detected": detected_in,
        "tissues_measured": len(measured),
        "warnings": warnings,
    }
    metrics = {
        "ensembl": ensembl,
        "symbol": record.get("Gene"),
        "ntpm": measured,
        "runner_up_ntpm": runner_up,
        "mean_other_ntpm": round(others_mean, 4),
        "hpa_distribution": record.get("RNA tissue distribution"),
    }

    analysis_path = provenance.write_analysis(
        target_dir / f"{ensembl}.analysis.json",
        source=state.project().relative(profile_path),
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
    emit.line(f"{record.get('Gene') or ensembl} ({ensembl}) — HPA consensus RNA")
    emit.line(
        f"specificity: {computed} (HPA curated: {curated or 'n/a'}); "
        f"detected in {detected_in}/{len(measured)} tissues at ≥{cutoff} nTPM"
    )
    emit.line(f"highest: {top_tissue} {top_value} nTPM")
    if query_tissues:
        for tissue in wanted:
            emit.line(f"  {tissue}: {measured[tissue]} nTPM -> {verdicts[tissue]}")
    for warning in warnings:
        emit.line(f"warning: {warning}")
    for record_relay in relays:
        emit.line(f"relay {record_relay['code']}: {record_relay['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()


def _locate(state: AppState, gene: str, target_dir: Path) -> tuple[Path, Path, str]:
    """Find the fetched artifacts for GENE, which may be a path or an ID."""
    candidate = Path(gene)
    if candidate.suffix or "/" in gene:
        profile_path = (
            candidate if candidate.is_absolute() else state.project().root / candidate
        )
        confined = confine_path(state.project().root, profile_path)
        if confined is None:
            raise ArtifactError(
                f"tissue profile path escapes project root: {profile_path}"
            )
        profile_path = confined
        if not profile_path.is_file():
            raise ArtifactError(f"tissue profile not found: {profile_path}")
        ensembl = sanitize_slug(profile_path.name.split(".")[0])
        return profile_path, profile_path.with_name(f"{ensembl}.hpa.json"), ensembl

    if ENSG_RE.match(gene.upper()):
        ensembl = sanitize_slug(gene.upper())
    else:
        # A symbol was fetched under its Ensembl ID; recover it from the
        # sidecars rather than by asking the network, which `analyze`
        # must never do.
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
                f"no fetched expression profile for {gene!r} under {target_dir}",
                remedy=f"run `dde expression fetch {gene}` first",
            )
        if len(matches) > 1:
            raise Refusal(
                f"{gene!r} matches {len(matches)} fetched profiles",
                detail=", ".join(m.name for m in matches),
                remedy="pass the Ensembl gene ID you mean",
            )
        ensembl = sanitize_slug(matches[0].name.split(".")[0])

    profile_path = target_dir / f"{ensembl}.tissue.json"
    if not profile_path.is_file():
        raise ArtifactError(
            f"tissue profile not found: {profile_path}",
            remedy=f"run `dde expression fetch {ensembl}` first",
        )
    return profile_path, target_dir / f"{ensembl}.hpa.json", ensembl


def _locate_single_cell(
    state: AppState, gene: str, target_dir: Path
) -> tuple[Path, Path, str]:
    """Find the fetched single-cell artifacts for GENE."""
    candidate = Path(gene)
    if candidate.suffix or "/" in gene:
        profile_path = (
            candidate if candidate.is_absolute() else state.project().root / candidate
        )
        confined = confine_path(state.project().root, profile_path)
        if confined is None:
            raise ArtifactError(
                f"single-cell profile path escapes project root: {profile_path}"
            )
        profile_path = confined
        if not profile_path.is_file():
            raise ArtifactError(f"single-cell profile not found: {profile_path}")
        ensembl = sanitize_slug(profile_path.name.split(".")[0])
        return profile_path, profile_path.with_name(f"{ensembl}.hpa.json"), ensembl

    if ENSG_RE.match(gene.upper()):
        ensembl = sanitize_slug(gene.upper())
    else:
        matches = [
            meta
            for meta in sorted(target_dir.glob("ENSG*.sc-meta.json"))
            if (json.loads(meta.read_text(encoding="utf-8")).get("parameters") or {})
            .get("resolved_symbol", "")
            .upper()
            == gene.upper()
        ]
        if not matches:
            raise ArtifactError(
                f"no fetched single-cell profile for {gene!r} under {target_dir}",
                remedy=f"run `dde expression fetch-single-cell {gene}` first",
            )
        if len(matches) > 1:
            raise Refusal(
                f"{gene!r} matches {len(matches)} fetched profiles",
                detail=", ".join(m.name for m in matches),
                remedy="pass the Ensembl gene ID you mean",
            )
        ensembl = sanitize_slug(matches[0].name.split(".")[0])

    profile_path = target_dir / f"{ensembl}.single-cell.json"
    if not profile_path.is_file():
        raise ArtifactError(
            f"single-cell profile not found: {profile_path}",
            remedy=f"run `dde expression fetch-single-cell {ensembl}` first",
        )
    return profile_path, target_dir / f"{ensembl}.hpa.json", ensembl


# ---------------------------------------------------------------------------
# Single-cell commands
# ---------------------------------------------------------------------------


@expression.command("fetch-single-cell")
@click.argument("gene")
@out_option
@output_options
@pass_state
def fetch_single_cell_cmd(
    state: AppState, gene: str, out: str | None, as_json: bool, quiet: bool
) -> None:
    """Fetch the HPA single-cell RNA profile for GENE.

    GENE is a gene symbol or an Ensembl gene ID. Fetches per-cell-type
    nCPM values across 154 cell types, plus the per-gene annotation
    record. Symbols that match more than one HPA gene are refused.
    """
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir("expression", out)

    # ── HGNC gene symbol resolution (#147) ────────────────────────────
    gene_res = resolve_gene(gene)
    if not gene_res.resolved:
        suggestions = ", ".join(gene_res.suggestions) if gene_res.suggestions else ""
        hint = f" Did you mean: {suggestions}?" if suggestions else ""
        provenance.relay(
            "gene.unresolved_symbol",
            f"Gene symbol {gene!r} could not be resolved via HGNC. "
            f"No query was attempted. This is a lookup failure, not "
            f"evidence of gene absence.{hint}",
        )
        raise Refusal(
            f"could not resolve {gene!r} to a known gene via HGNC",
            detail=f"suggestions: {suggestions}"
            if suggestions
            else "no near matches found",
            remedy="check the gene symbol or pass an Ensembl gene ID (ENSG...)",
        )
    echo = gene_res.echo_line()
    if echo:
        emit.line(echo)
    query_gene = gene_res.ensembl_id or gene_res.canonical_symbol or gene

    release, release_failure = _hpa_release()
    try:
        ensembl, symbol, how = _resolve_gene(query_gene)
    except Refusal as e:
        canonical = gene_res.canonical_symbol or gene
        provenance.relay(
            "expression.no_data_found",
            f"Gene {canonical} resolved successfully via HGNC "
            f"({gene_res.source}) but no single-cell expression data "
            f"found in HPA. This is a data gap, not evidence of "
            f"non-expression.",
        )
        raise Refusal(
            f"gene {canonical!r} resolved via HGNC but HPA has no record",
            detail=(
                f"HGNC resolved {gene!r} to {canonical} (source: {gene_res.source})"
            ),
            remedy=(
                "HPA may not index this gene. This is a data gap, not "
                "evidence of non-expression."
            ),
        ) from e

    sidecar = provenance.Sidecar(
        tool="expression",
        subcommand="fetch-single-cell",
        endpoint=HPA_BASE,
        parameters={
            "query": gene,
            "resolved_ensembl": ensembl,
            "resolved_symbol": symbol,
            "resolved_by": how,
            "cell_types_requested": len(CELL_TYPES),
            "gene_resolution": gene_res.to_dict(),
        },
    )
    sidecar.note("source", "Human Protein Atlas")
    sidecar.note("licence", "CC BY 4.0")
    sidecar.note("hpa_release", release)

    if release is None:
        sidecar.warn(
            "the HPA release version could not be determined; the artifact is "
            f"identified by payload SHA-256 only ({release_failure})",
            code="expression.release_version_unknown",
        )

    # Per-gene record (same endpoint as tissue fetch)
    record_bytes = _fetch_bytes(f"{HPA_BASE}/{ensembl}.json", "HPA per-gene record")
    record = json.loads(record_bytes.decode("utf-8"))
    if isinstance(record, list):
        record = record[0] if record else {}
    if not isinstance(record, dict):
        raise SchemaError(f"HPA per-gene record for {ensembl} is not an object")

    # Single-cell profile via search_download
    columns = ["g", "gs", "eg"] + [_sc_column_code(ct) for ct in CELL_TYPES]
    profile_url = f"{SEARCH_URL}?" + urlencode(
        {
            "search": ensembl,
            "format": "json",
            "columns": ",".join(columns),
            "compress": "no",
        }
    )
    profile_bytes = _fetch_bytes(profile_url, "HPA single-cell profile")
    row = _tissue_row(
        json.loads(profile_bytes.decode("utf-8")), ensembl, "single-cell profile"
    )
    measured = _check_sc_columns(row)
    sidecar.note("cell_types_returned", len(measured))

    # Check whether HPA actually holds single-cell data for this gene
    sc_specificity = record.get(SINGLE_CELL_FIELD)
    if not sc_specificity:
        sidecar.warn(
            f"HPA reports no single-cell RNA data for this gene "
            f"({SINGLE_CELL_FIELD} is {sc_specificity!r}); the profile was "
            "fetched but all values may be zero"
        )

    record_path = target_dir / f"{ensembl}.hpa.json"
    profile_path = target_dir / f"{ensembl}.single-cell.json"
    record_path.write_bytes(record_bytes)
    profile_path.write_bytes(profile_bytes)
    sidecar.add_output(record_path)
    sidecar.add_output(profile_path)

    meta_path = sidecar.write(target_dir / f"{ensembl}.sc-meta.json")

    emit.data("gene", symbol)
    emit.data("ensembl", ensembl)
    emit.data("resolved_by", how)
    emit.data("hpa_release", release)
    emit.data("cell_types", len(measured))
    emit.data("single_cell_specificity", sc_specificity)
    emit.path(record_path, role="record")
    emit.path(profile_path, role="single_cell_profile")
    emit.path(meta_path, role="sidecar")
    emit.flush()


@expression.command("analyze-single-cell")
@click.argument("gene")
@click.option(
    "--cell-type",
    "query_cell_types",
    multiple=True,
    help="Cell type to answer for explicitly. Repeatable. Omit for a whole-profile "
    "summary. Unknown names fail rather than resolving to nothing.",
)
@click.option(
    "--expressed-ncpm",
    type=float,
    default=None,
    help="Override the expressed_ncpm threshold for this invocation.",
)
@click.option(
    "--enriched-fold",
    type=float,
    default=None,
    help="Override the enriched_fold threshold for this invocation.",
)
@from_option
@out_option
@output_options
@pass_state
def analyze_single_cell_cmd(
    state: AppState,
    gene: str,
    query_cell_types: tuple[str, ...],
    expressed_ncpm: float | None,
    enriched_fold: float | None,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Apply expression thresholds to a fetched single-cell profile. No network."""
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir("expression", from_dir)
    target_dir = state.project().artifact_dir("expression", out)

    profile_path, record_path, ensembl = _locate_single_cell(state, gene, source_dir)
    row = _tissue_row(
        provenance.read_json(profile_path, "single-cell profile"),
        ensembl,
        "single-cell profile",
    )
    measured = _check_sc_columns(row)
    record = provenance.read_json(record_path, "HPA per-gene record")
    if isinstance(record, list):
        record = record[0] if record else {}

    thresholds = load_thresholds(
        state,
        "expression-single-cell",
        {"expressed_ncpm": expressed_ncpm, "enriched_fold": enriched_fold},
    )
    cutoff = thresholds.get("expressed_ncpm")
    fold = thresholds.get("enriched_fold")

    wanted = [_normalise_cell_type(ct) for ct in query_cell_types] or list(CELL_TYPES)

    # HPA single-cell detection uses strict inequality (nCPM > 1), unlike
    # tissue (nTPM >= 1). This matches HPA's published definition.
    verdicts = {
        cell_type: ("expressed" if measured[cell_type] > cutoff else "not_detected")
        for cell_type in wanted
    }

    ranked = sorted(measured.items(), key=lambda kv: kv[1], reverse=True)
    top_cell_type, top_value = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    others_mean = (
        (sum(v for _, v in ranked[1:]) / (len(ranked) - 1)) if len(ranked) > 1 else 0.0
    )

    if top_value <= cutoff:
        computed = "not detected"
    elif runner_up > 0 and top_value >= fold * runner_up:
        computed = "cell type enriched"
    elif others_mean > 0 and top_value >= fold * others_mean:
        computed = "cell type enhanced"
    else:
        computed = "low cell type specificity"

    curated = record.get(SINGLE_CELL_FIELD)
    detected_in = sum(1 for v in measured.values() if v > cutoff)

    relays: list[dict[str, str]] = []

    def add_relay(code: str, message: str) -> None:
        if not any(r["code"] == code for r in relays):
            relays.append(provenance.relay(code, message))

    if any(v == "not_detected" for v in verdicts.values()):
        negatives = [ct for ct, v in verdicts.items() if v == "not_detected"]
        add_relay(
            "expression.absent_is_not_evidence",
            f"{len(negatives)} not_detected verdict(s) rest on one single-cell "
            f"RNA dataset (HPA): {', '.join(negatives[:6])}"
            + (" …" if len(negatives) > 6 else ""),
        )

    warnings: list[str] = []
    if curated and computed != str(curated).lower():
        warnings.append(
            f"recomputed specificity {computed!r} disagrees with HPA's curated "
            f"{str(curated).lower()!r}; HPA's classifier uses additional "
            "grouping and the full cell type matrix, this one uses the 154 "
            "individual cell types"
        )

    unresolved = thresholds.unresolved()
    if "low_confidence_ncpm" in unresolved:
        warnings.append(
            "no near-threshold confidence band is applied: HPA publishes no "
            "such cutoff and low_confidence_ncpm is UNRESOLVED, so a value of "
            f"{cutoff} nCPM is reported as expressed with no margin"
        )

    assessment = {
        "verdict_by_cell_type": verdicts,
        "specificity_computed": computed,
        "specificity_curated_by_hpa": curated,
        "highest_cell_type": top_cell_type,
        "highest_ncpm": top_value,
        "cell_types_detected": detected_in,
        "cell_types_measured": len(measured),
        "warnings": warnings,
    }
    metrics = {
        "ensembl": ensembl,
        "symbol": record.get("Gene"),
        "ncpm": measured,
        "runner_up_ncpm": runner_up,
        "mean_other_ncpm": round(others_mean, 4),
        "hpa_distribution": record.get(SC_DISTRIBUTION_FIELD),
    }

    analysis_path = provenance.write_analysis(
        target_dir / f"{ensembl}.sc-analysis.json",
        source=state.project().relative(profile_path),
        threshold_set=thresholds.tag,
        thresholds_applied=thresholds.applied(),
        metrics=metrics,
        assessment=assessment,
        threshold_sources=thresholds.sources(),
        threshold_provenance=thresholds.provenance,
        unresolved=thresholds.unresolved(),
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("relays", relays)
    emit.line(f"{record.get('Gene') or ensembl} ({ensembl}) — HPA single-cell RNA")
    emit.line(
        f"specificity: {computed} (HPA curated: {curated or 'n/a'}); "
        f"detected in {detected_in}/{len(measured)} cell types at >{cutoff} nCPM"
    )
    emit.line(f"highest: {top_cell_type} {top_value} nCPM")
    if query_cell_types:
        for cell_type in wanted:
            emit.line(
                f"  {cell_type}: {measured[cell_type]} nCPM -> {verdicts[cell_type]}"
            )
    for warning in warnings:
        emit.line(f"warning: {warning}")
    for record_relay in relays:
        emit.line(f"relay {record_relay['code']}: {record_relay['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()


# Register ``search`` as an alias for ``fetch`` (issue #92).
expression.add_command(fetch_cmd, "search")
