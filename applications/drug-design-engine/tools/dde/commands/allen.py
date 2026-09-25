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

"""`dde allen` — Allen Brain Map API query for gene expression and donor data.

Queries the Allen Brain Map public API for gene expression datasets and
donor metadata from the Aging/Dementia/TBI study.  The Allen Brain Atlas
hosts comprehensive brain region expression data across human and mouse
atlases.

Two phases:

  search        queries the Allen Brain Map API for gene expression
                datasets (SectionDataSets) matching a gene symbol;
                writes structured results to Layer 0 with a sidecar.
  donors        queries the Aging/Dementia/TBI donor detail endpoint
                for donor metadata filtered by dementia status, diagnosis,
                or Braak stage; writes structured results to Layer 0.
  analyze       reads stored search results and summarises product/atlas
                distribution, experiment counts, and organisms.
                No network.
  analyze-donors reads stored donor results and summarises dementia/control
                breakdown, diagnosis distribution, Braak stage distribution,
                APOE4 status, and demographics.  No network.

The Allen Brain Atlas covers brain region expression only.  Expression in
peripheral tissues (skin, DRG, etc.) is not represented in this dataset.
"""

from __future__ import annotations

import json
import re
from collections import Counter
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
from ..core.errors import ArtifactError, Refusal, SchemaError
from ..core.qps import qps_for_host

TOOL = "allen"
ARTIFACT_CLASS = "transcriptomics"

ALLEN_API = "https://api.brain-map.org/api/v2"


def _slugify(query: str) -> str:
    """Derive a filesystem-safe slug from a search query string."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", query).strip("-").lower()
    return slug[:80] or "allen-search"


def _fetch_gene(gene: str, organism: str | None) -> tuple[bytes, dict[str, Any]]:
    """Fetch gene info and expression datasets from the Allen Brain Map API.

    Returns (verbatim response bytes, structured artifact dict).
    """
    # Step 1: look up the gene by acronym.
    gene_encoded = quote(gene, safe="")
    gene_url = (
        f"{ALLEN_API}/data/Gene/query.json"
        f"?criteria=[acronym$eq'{gene_encoded}']&include=organism"
    )
    gene_data = http.get_json(
        gene_url, qps=qps_for_host("api.brain-map.org"), timeout=120.0
    )

    if not isinstance(gene_data, dict):
        raise SchemaError(
            "Allen Gene endpoint did not return a JSON object",
            detail=f"got {type(gene_data).__name__}",
        )

    gene_results = gene_data.get("msg", [])
    if not gene_results:
        raise Refusal(
            f"no gene found for symbol {gene!r} in the Allen Brain Map",
            remedy="check the gene symbol and try again",
        )

    # Filter by organism if specified.
    if organism:
        organism_lower = organism.lower()
        filtered = []
        for g in gene_results:
            org = g.get("organism", {})
            org_name = org.get("name", "") if isinstance(org, dict) else ""
            if organism_lower in org_name.lower():
                filtered.append(g)
        if filtered:
            gene_results = filtered

    # Step 2: get SectionDataSets containing this gene.
    dataset_url = (
        f"{ALLEN_API}/data/SectionDataSet/query.json"
        f"?criteria=[genes.acronym$eq'{gene_encoded}']"
        f"&include=genes,products&num_rows=50"
    )
    dataset_data = http.get_json(
        dataset_url, qps=qps_for_host("api.brain-map.org"), timeout=120.0
    )

    if not isinstance(dataset_data, dict):
        raise SchemaError(
            "Allen SectionDataSet endpoint did not return a JSON object",
            detail=f"got {type(dataset_data).__name__}",
        )

    datasets = dataset_data.get("msg", [])

    # Build verbatim response from both API calls.
    verbatim = {
        "gene_query": gene_data,
        "dataset_query": dataset_data,
    }
    raw = json.dumps(verbatim, indent=2).encode("utf-8")

    # Extract structured records.
    gene_records = []
    for g in gene_results:
        org = g.get("organism", {})
        gene_records.append(
            {
                "gene_id": g.get("id"),
                "acronym": g.get("acronym", ""),
                "name": g.get("name", ""),
                "organism_id": org.get("id") if isinstance(org, dict) else None,
                "organism_name": org.get("name", "") if isinstance(org, dict) else "",
            }
        )

    dataset_records = []
    for ds in datasets:
        products = ds.get("products", [])
        product_names = []
        for p in products:
            if isinstance(p, dict):
                pname = p.get("abbreviation") or p.get("name", "")
                if pname:
                    product_names.append(pname)

        genes_in_ds = ds.get("genes", [])
        gene_symbols = []
        for gn in genes_in_ds:
            if isinstance(gn, dict):
                sym = gn.get("acronym", "")
                if sym:
                    gene_symbols.append(sym)

        dataset_records.append(
            {
                "dataset_id": ds.get("id"),
                "specimen_id": ds.get("specimen_id"),
                "plane_of_section_id": ds.get("plane_of_section_id"),
                "products": product_names,
                "genes": gene_symbols,
                "failed": ds.get("failed", False),
            }
        )

    artifact: dict[str, Any] = {
        "schema": "dde.allen-search.v1",
        "query": {
            "gene": gene,
            "organism": organism or "",
        },
        "summary": {
            "n_gene_matches": len(gene_records),
            "n_datasets": len(dataset_records),
        },
        "genes": gene_records,
        "datasets": dataset_records,
    }

    return raw, artifact


def _fetch_donors(
    disease: str | None,
    dementia: bool,
    min_braak: int | None,
) -> tuple[bytes, dict[str, Any]]:
    """Fetch donor metadata from the Allen Aging/Dementia/TBI dataset.

    Returns (verbatim response bytes, structured artifact dict).
    """
    # Build criteria filters.
    criteria_parts: list[str] = []
    if dementia:
        criteria_parts.append("[act_demented$eqtrue]")
    if disease:
        disease_encoded = quote(disease, safe="")
        criteria_parts.append(f"[dsm_iv_clinical_diagnosis$il'*{disease_encoded}*']")

    criteria = "".join(criteria_parts) if criteria_parts else ""

    url = (
        f"{ALLEN_API}/data/ApiTbiDonorDetail/query.json?criteria={criteria}&num_rows=50"
    )
    raw_data = http.get_json(url, qps=qps_for_host("api.brain-map.org"), timeout=120.0)

    if not isinstance(raw_data, dict):
        raise SchemaError(
            "Allen ApiTbiDonorDetail endpoint did not return a JSON object",
            detail=f"got {type(raw_data).__name__}",
        )

    donors = raw_data.get("msg", [])

    # Filter by Braak stage if specified.
    if min_braak is not None:
        filtered = []
        for d in donors:
            braak = d.get("braak")
            if braak is not None:
                try:
                    if int(braak) >= min_braak:
                        filtered.append(d)
                except (ValueError, TypeError):
                    # Non-numeric Braak values — include them unfiltered.
                    filtered.append(d)
            # Donors without Braak data are excluded when filtering.
        donors = filtered

    raw = json.dumps(raw_data, indent=2).encode("utf-8")

    # Extract structured donor records.
    donor_records = []
    for d in donors:
        donor_records.append(
            {
                "donor_id": d.get("donor_id") or d.get("id"),
                "name": d.get("name", ""),
                "age": d.get("age_at_death") or d.get("age"),
                "sex": d.get("sex", ""),
                "race": d.get("race", ""),
                "act_demented": d.get("act_demented"),
                "dsm_iv_clinical_diagnosis": d.get("dsm_iv_clinical_diagnosis", ""),
                "nincds_arda_diagnosis": d.get("nincds_arda_diagnosis", ""),
                "braak": d.get("braak"),
                "cerad": d.get("cerad"),
                "apoe4_status": d.get("apo_e4_allele") or d.get("apoe4_status"),
                "education_years": d.get("education_years"),
            }
        )

    artifact: dict[str, Any] = {
        "schema": "dde.allen-donors.v1",
        "query": {
            "disease": disease or "",
            "dementia": dementia,
            "min_braak": min_braak,
        },
        "summary": {
            "n_donors": len(donor_records),
        },
        "donors": donor_records,
    }

    return raw, artifact


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def allen() -> None:
    """Allen Brain Map gene expression and donor data queries."""


# Verb aliases — see docs/tool-design-guidance.md and issue #92.
#
#   fetch  = retrieve the record for a known identifier (gene, CID, …)
#   search = query and get back a result set
#
# Both verbs are accepted as aliases for discoverability.  The primary
# verb for this group is ``search`` (Allen returns datasets matching a
# gene query); ``fetch`` is the alias.


@allen.command("search")
@click.argument("query")
@click.option(
    "--organism",
    default=None,
    type=click.Choice(["human", "mouse"], case_sensitive=False),
    help="Filter gene matches by organism (human or mouse).",
)
@out_option
@output_options
@pass_state
def search_cmd(
    state: AppState,
    query: str,
    organism: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Search the Allen Brain Map for gene expression datasets.

    QUERY is a gene symbol (e.g. PVALB, GAD1).  Looks up the gene in the
    Allen API and retrieves SectionDataSets containing that gene, along
    with the products/atlases each dataset belongs to.
    """
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = _slugify(query)

    raw, artifact = _fetch_gene(query, organism)

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="search",
        endpoint=f"{ALLEN_API}/data/SectionDataSet/query.json",
        parameters={
            "gene": query,
            "organism": organism or "",
        },
    )
    sidecar.note("source", "Allen Brain Map (brain-map.org)")
    sidecar.note("n_gene_matches", artifact["summary"]["n_gene_matches"])
    sidecar.note("n_datasets", artifact["summary"]["n_datasets"])

    # Write verbatim response.
    verbatim_path = target_dir / f"{slug}.allen.json"
    verbatim_path.write_bytes(raw)
    sidecar.add_output(verbatim_path)

    # Write structured artifact.
    artifact_path = target_dir / f"{slug}.allen.artifact.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(artifact_path)

    # Write sidecar.
    meta_path = sidecar.write(target_dir / f"{slug}.allen.meta.json")

    emit.data("query", query)
    emit.data("n_gene_matches", artifact["summary"]["n_gene_matches"])
    emit.data("n_datasets", artifact["summary"]["n_datasets"])
    emit.path(verbatim_path, role="verbatim")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.line(f"Allen Brain Map search: {query!r}")
    emit.line(
        f"  {artifact['summary']['n_gene_matches']} gene match(es), "
        f"{artifact['summary']['n_datasets']} dataset(s)"
    )
    if artifact["genes"]:
        for g in artifact["genes"][:5]:
            emit.line(
                f"  {g['acronym']} ({g['organism_name']}): gene_id={g['gene_id']}"
            )
        if len(artifact["genes"]) > 5:
            emit.line(f"  ... {len(artifact['genes']) - 5} more in the artifact")
    if artifact["datasets"]:
        for ds in artifact["datasets"][:5]:
            products_str = ", ".join(ds["products"]) if ds["products"] else "(none)"
            emit.line(f"  dataset {ds['dataset_id']}: {products_str}")
        if len(artifact["datasets"]) > 5:
            emit.line(f"  ... {len(artifact['datasets']) - 5} more in the artifact")
    emit.flush()


@allen.command("donors")
@click.option(
    "--disease",
    default=None,
    help="Filter donors by DSM-IV diagnosis (substring match).",
)
@click.option(
    "--dementia",
    is_flag=True,
    default=False,
    help="Filter to donors with act_demented=true.",
)
@click.option(
    "--min-braak",
    default=None,
    type=int,
    help="Filter to donors with Braak stage >= N.",
)
@out_option
@output_options
@pass_state
def donors_cmd(
    state: AppState,
    disease: str | None,
    dementia: bool,
    min_braak: int | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Query donor metadata from the Allen Aging/Dementia/TBI dataset.

    Returns donor demographics, dementia status, DSM-IV diagnosis,
    Braak stage, and APOE4 status.  Use --disease, --dementia, and
    --min-braak to filter results.
    """
    if not disease and not dementia and min_braak is None:
        raise Refusal(
            "no donor filter criteria provided",
            remedy="provide at least one of --disease, --dementia, or --min-braak",
        )

    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug_source = disease or ("dementia" if dementia else "allen-donors")
    slug = _slugify(slug_source)

    raw, artifact = _fetch_donors(disease, dementia, min_braak)

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="donors",
        endpoint=f"{ALLEN_API}/data/ApiTbiDonorDetail/query.json",
        parameters={
            "disease": disease or "",
            "dementia": dementia,
            "min_braak": min_braak,
        },
    )
    sidecar.note("source", "Allen Brain Map Aging/Dementia/TBI (brain-map.org)")
    sidecar.note("n_donors", artifact["summary"]["n_donors"])

    # Write verbatim response.
    verbatim_path = target_dir / f"{slug}.allen-donors.json"
    verbatim_path.write_bytes(raw)
    sidecar.add_output(verbatim_path)

    # Write structured artifact.
    artifact_path = target_dir / f"{slug}.allen-donors.artifact.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(artifact_path)

    # Write sidecar.
    meta_path = sidecar.write(target_dir / f"{slug}.allen-donors.meta.json")

    emit.data("n_donors", artifact["summary"]["n_donors"])
    emit.path(verbatim_path, role="verbatim")
    emit.path(artifact_path, role="artifact")
    emit.path(meta_path, role="sidecar")
    emit.line("Allen Brain Map donors query")
    emit.line(f"  {artifact['summary']['n_donors']} donor(s) found")
    if artifact["donors"]:
        for d in artifact["donors"][:5]:
            diag = d.get("dsm_iv_clinical_diagnosis") or "(none)"
            emit.line(
                f"  donor {d['donor_id']}: {d.get('sex', '?')}, "
                f"age={d.get('age', '?')}, diagnosis={diag}"
            )
        if len(artifact["donors"]) > 5:
            emit.line(f"  ... {len(artifact['donors']) - 5} more in the artifact")
    emit.flush()


@allen.command("analyze")
@click.argument("query")
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    query: str,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Summarise stored Allen Brain Map gene search results. No network.

    Reads what ``search`` wrote and produces a summary: product/atlas
    distribution, experiment counts, and organism breakdown.
    """
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug = _slugify(query)

    artifact_path = source_dir / f"{slug}.allen.artifact.json"
    if not artifact_path.is_file():
        raise ArtifactError(
            f"no Allen Brain Map search artifact for {query!r} under {source_dir}",
            remedy=f"run `dde allen search {query!r}` first",
        )

    artifact = provenance.read_json(artifact_path, "Allen Brain Map search artifact")
    if artifact.get("schema") != "dde.allen-search.v1":
        raise SchemaError(
            f"unexpected schema in {artifact_path.name}",
            detail=f"expected dde.allen-search.v1, got {artifact.get('schema')!r}",
        )

    genes = artifact.get("genes", [])
    datasets = artifact.get("datasets", [])
    query_meta = artifact.get("query", {})

    # Compute distributions.
    product_counts: Counter[str] = Counter()
    organism_counts: Counter[str] = Counter()
    n_failed = 0

    for g in genes:
        org_name = g.get("organism_name", "")
        if org_name:
            organism_counts[org_name] += 1

    for ds in datasets:
        for p in ds.get("products", []):
            product_counts[p] += 1
        if ds.get("failed"):
            n_failed += 1

    top_products = product_counts.most_common(15)

    relays: list[dict[str, str]] = []
    # Mandatory relay: Allen Brain Atlas covers brain regions only.
    # Must always be present regardless of whether datasets were returned,
    # to prevent false negative inferences about peripheral tissue expression.
    relays.append(
        provenance.relay(
            "allen.brain_region_expression_only",
            f"Allen Brain Atlas expression data covers brain regions only. "
            f"Expression in peripheral tissues (skin, DRG, etc.) is not "
            f"represented. {len(datasets)} dataset(s) returned for "
            f"{query_meta.get('gene', query)!r} are all brain-region scoped.",
        )
    )

    assessment: dict[str, Any] = {
        "outcome": "datasets_found" if datasets else "no_datasets",
        "query_gene": query_meta.get("gene", ""),
        "query_organism": query_meta.get("organism", ""),
        "n_gene_matches": len(genes),
        "n_datasets": len(datasets),
        "n_failed_datasets": n_failed,
        "product_distribution": [{"product": p, "count": c} for p, c in top_products],
        "organism_breakdown": dict(organism_counts),
    }

    metrics: dict[str, Any] = {
        "n_gene_matches": len(genes),
        "n_datasets": len(datasets),
        "n_failed_datasets": n_failed,
        "n_unique_products": len(product_counts),
        "n_unique_organisms": len(organism_counts),
    }

    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.allen.analysis.json",
        source=state.project().relative(artifact_path),
        threshold_set="allen-search",
        thresholds_applied={},
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("relays", relays)
    emit.line(f"Allen Brain Map analysis: {query!r}")
    emit.line(f"  Outcome: {assessment['outcome']}")
    emit.line(f"  {len(genes)} gene match(es), {len(datasets)} dataset(s)")
    if n_failed:
        emit.line(f"  {n_failed} failed dataset(s)")
    if top_products:
        emit.line("  Products/atlases:")
        for p, c in top_products[:5]:
            emit.line(f"    {p}: {c}")
    if organism_counts:
        emit.line(f"  Organisms: {dict(organism_counts)}")
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()


@allen.command("analyze-donors")
@click.option(
    "--disease",
    default=None,
    help="Disease filter used in the original donors query (for slug matching).",
)
@click.option(
    "--dementia",
    is_flag=True,
    default=False,
    help="Dementia filter used in the original donors query (for slug matching).",
)
@from_option
@out_option
@output_options
@pass_state
def analyze_donors_cmd(
    state: AppState,
    disease: str | None,
    dementia: bool,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Summarise stored Allen Brain Map donor data. No network.

    Reads what ``donors`` wrote and produces a summary: dementia/control
    breakdown, diagnosis distribution, Braak stage distribution, APOE4
    status, and age/sex demographics.
    """
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = state.project().artifact_dir(ARTIFACT_CLASS, out)

    slug_source = disease or ("dementia" if dementia else "allen-donors")
    slug = _slugify(slug_source)

    artifact_path = source_dir / f"{slug}.allen-donors.artifact.json"
    if not artifact_path.is_file():
        raise ArtifactError(
            f"no Allen Brain Map donors artifact for {slug_source!r} under {source_dir}",
            remedy="run `dde allen donors` with the appropriate filters first",
        )

    artifact = provenance.read_json(artifact_path, "Allen Brain Map donors artifact")
    if artifact.get("schema") != "dde.allen-donors.v1":
        raise SchemaError(
            f"unexpected schema in {artifact_path.name}",
            detail=f"expected dde.allen-donors.v1, got {artifact.get('schema')!r}",
        )

    donors = artifact.get("donors", [])
    query_meta = artifact.get("query", {})

    # Compute distributions.
    diagnosis_counts: Counter[str] = Counter()
    braak_counts: Counter[str] = Counter()
    sex_counts: Counter[str] = Counter()
    apoe4_counts: Counter[str] = Counter()
    n_demented = 0
    n_control = 0
    ages: list[float] = []

    for d in donors:
        # Dementia breakdown.
        act_demented = d.get("act_demented")
        if act_demented is True or act_demented == "true" or act_demented == "True":
            n_demented += 1
        elif (
            act_demented is False or act_demented == "false" or act_demented == "False"
        ):
            n_control += 1

        # Diagnosis distribution.
        diag = d.get("dsm_iv_clinical_diagnosis", "")
        if diag:
            diagnosis_counts[diag] += 1

        # Braak stage distribution.
        braak = d.get("braak")
        if braak is not None:
            braak_counts[str(braak)] += 1

        # Sex distribution.
        sex = d.get("sex", "")
        if sex:
            sex_counts[sex] += 1

        # APOE4 status.
        apoe4 = d.get("apoe4_status")
        if apoe4 is not None:
            apoe4_counts[str(apoe4)] += 1

        # Age.
        age = d.get("age")
        if age is not None:
            try:
                ages.append(float(age))
            except (ValueError, TypeError):
                pass

    top_diagnoses = diagnosis_counts.most_common(15)
    braak_distribution = dict(sorted(braak_counts.items()))

    age_stats: dict[str, Any] = {}
    if ages:
        age_stats = {
            "min": min(ages),
            "max": max(ages),
            "mean": round(sum(ages) / len(ages), 1),
            "n": len(ages),
        }

    relays: list[dict[str, str]] = []

    assessment: dict[str, Any] = {
        "outcome": "donors_found" if donors else "no_donors",
        "query_disease": query_meta.get("disease", ""),
        "query_dementia": query_meta.get("dementia", False),
        "query_min_braak": query_meta.get("min_braak"),
        "n_donors": len(donors),
        "n_demented": n_demented,
        "n_control": n_control,
        "diagnosis_distribution": [
            {"diagnosis": d, "count": c} for d, c in top_diagnoses
        ],
        "braak_distribution": braak_distribution,
        "sex_distribution": dict(sex_counts),
        "apoe4_distribution": dict(apoe4_counts),
        "age_stats": age_stats,
    }

    metrics: dict[str, Any] = {
        "n_donors": len(donors),
        "n_demented": n_demented,
        "n_control": n_control,
        "n_unique_diagnoses": len(diagnosis_counts),
        "n_unique_braak_stages": len(braak_counts),
    }

    analysis_path = provenance.write_analysis(
        target_dir / f"{slug}.allen-donors.analysis.json",
        source=state.project().relative(artifact_path),
        threshold_set="allen-donors",
        thresholds_applied={},
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit.data("assessment", assessment)
    emit.data("relays", relays)
    emit.line(f"Allen Brain Map donor analysis: {slug_source!r}")
    emit.line(f"  Outcome: {assessment['outcome']}")
    emit.line(f"  {len(donors)} donor(s)")
    emit.line(f"  Demented: {n_demented}, Control: {n_control}")
    if top_diagnoses:
        emit.line("  Diagnoses:")
        for d, c in top_diagnoses[:5]:
            emit.line(f"    {d}: {c}")
    if braak_distribution:
        emit.line(f"  Braak stages: {braak_distribution}")
    if sex_counts:
        emit.line(f"  Sex: {dict(sex_counts)}")
    if apoe4_counts:
        emit.line(f"  APOE4: {dict(apoe4_counts)}")
    if age_stats:
        emit.line(
            f"  Age: min={age_stats['min']}, max={age_stats['max']}, "
            f"mean={age_stats['mean']} (n={age_stats['n']})"
        )
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()


# Register ``fetch`` as an alias for ``search`` (issue #92).
allen.add_command(search_cmd, "fetch")
