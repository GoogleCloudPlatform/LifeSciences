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

"""AlphaFold: AFDB lookup and AF3 prediction.

Two sources, one command group, because the interpretation contract is
shared — both produce a structure plus confidence metrics.

  fetch    — phase 1, AlphaFold DB. Downloads mmCIF + PAE + API record.
  analyze  — phase 2. pLDDT bands and PAE domain segmentation.
  predict  — phase 1, AlphaFold 3 on the Vertex dedicated endpoint.
  analyze-prediction — phase 2 for an AF3 result.

Blended from:
  * science-skills alphafold_database_fetch_and_analyze
    (fetch_structure.py, analyze_plddt.py, analyze_pae.py) — the AFDB
    path and the pLDDT/PAE algorithms.
  * candidate-tools/af3-toolkit af3_client.py — endpoint discovery by
    display name, two-tier 429 backoff, single-flight serialisation.

Changes made in the blend, per tool-design-guidance:
  * fetch no longer prints a report; it prints paths (§6).
  * thresholds moved out of module constants into the `alphafold` and
    `af3` threshold sets, program-overridable and cited by name (§7).
  * every artifact gets a .meta.json sidecar; analyses get
    .analysis.json (§5).
  * the isoform-substitution and fragment warnings, which upstream
    printed to stdout and lost, are now recorded in the sidecar so a
    reviewer can confirm they were relayed (§5).
  * fetch cross-checks the modelled length against UniProt's canonical
    length. AFDB answers a long-protein query with short isoform records
    and no indication that most of the protein is absent; without the
    cross-check both this tool and its predecessor reported a fragment's
    confidence as the protein's (§8, "never degrade silently").
"""

from __future__ import annotations

import itertools
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import click

from ..common import (
    AppState,
    beside_or_out,
    from_option,
    load_thresholds,
    out_option,
    output_options,
    pass_state,
    resolve_artifact,
)
from ..core import http, provenance
from ..core.errors import (
    ArtifactError,
    DependencyError,
    EndpointError,
    EndpointUnavailable,
    Refusal,
    SchemaError,
    UsageError,
)
from ..core.output import Emitter
from ..core.qps import qps_for_host

TOOL_AFDB = "alphafold-db"
TOOL_AF3 = "alphafold3-vertex"
ARTIFACT_CLASS = "structures"

AFDB_API = "https://alphafold.ebi.ac.uk/api/prediction"

UNIPROT_API = "https://rest.uniprot.org/uniprotkb"

# AFDB v4 models proteins up to this length. Structural fact about the
# database, not a judgement threshold.
FRAGMENT_LENGTH = 2700


def _canonical_length(accession: str) -> tuple[int | None, str | None]:
    """Canonical sequence length and gene symbol from UniProt.

    Needed because AFDB's coverage gap is invisible from its own
    response. For a long protein the API does not say "truncated" — it
    returns a set of *short isoform* records and nothing else. Asking
    for SYNE1 (Q8NF91, 8797 aa) yields six isoforms of which the longest
    is 1725 aa, with no field indicating that 80% of the protein is
    missing. Verified against the live API 2026-08-18.

    Without this second lookup the tool would report confidence metrics
    for a fragment as though they described the whole protein. Returns
    (None, None) on any failure: an unavailable cross-check degrades the
    warning to a weaker one, and is itself recorded, but never blocks
    the fetch.
    """
    try:
        record = http.get_json(
            f"{UNIPROT_API}/{accession}.json?fields=length,gene_primary",
            qps=qps_for_host("rest.uniprot.org"),
        )
    except Exception:
        return None, None
    length = record.get("sequence", {}).get("length")
    genes = record.get("genes") or []
    symbol = None
    if genes and isinstance(genes[0], dict):
        symbol = (genes[0].get("geneName") or {}).get("value")
    return (length if isinstance(length, int) else None), symbol


_UNIPROT_RE = re.compile(
    r"^[A-NR-Z][0-9][A-Z0-9]{3}[0-9]$|^[OPQ][0-9][A-Z0-9]{3}[0-9]$|^[A-Z0-9]{10}$"
)


@click.group()
def alphafold() -> None:
    """AlphaFold DB structures and AlphaFold 3 predictions."""


# ---------------------------------------------------------------------------
# Phase 1 — AlphaFold DB
# ---------------------------------------------------------------------------


# Verb aliases — see docs/tool-design-guidance.md and issue #92.
#
#   fetch  = retrieve the record for a known identifier (gene, CID, …)
#   search = query and get back a result set
#
# Both verbs are accepted as aliases for discoverability.  The primary
# verb for this group is ``fetch`` (AFDB resolves one UniProt accession
# to one structure); ``search`` is the alias.


@alphafold.command()
@click.argument("uniprot_id")
@out_option
@output_options
@pass_state
def fetch(
    state: AppState, uniprot_id: str, out: str | None, as_json: bool, quiet: bool
) -> None:
    """Fetch an AlphaFold DB structure by UniProt accession.

    Writes the mmCIF, the PAE matrix, the AFDB API record, and a
    provenance sidecar. Makes no judgement.
    """
    accession = uniprot_id.strip().upper()
    if not _UNIPROT_RE.match(accession):
        raise UsageError(
            f"{accession!r} does not look like a UniProt accession",
            detail="expected e.g. P04637, P00520, A0A1B0GX81",
            remedy="resolve the gene or protein name to a UniProt accession first",
        )

    project = state.project()
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)
    api_url = f"{AFDB_API}/{accession}"

    sidecar = provenance.Sidecar(
        tool=TOOL_AFDB,
        subcommand="fetch",
        endpoint=api_url,
        parameters={"uniprot": accession},
    )

    try:
        data = http.get_json(api_url, qps=qps_for_host("alphafold.ebi.ac.uk"))
    except EndpointError as exc:
        if "404" in (exc.detail or "") or "404" in exc.message:
            raise EndpointError(
                f"UniProt accession {accession} has no AlphaFold DB entry",
                remedy="check the accession, or use AF3 prediction for a sequence "
                "with no AFDB record",
            ) from exc
        raise

    if not isinstance(data, list) or not data:
        raise SchemaError(
            f"AFDB returned no prediction records for {accession}",
            detail=f"response type {type(data).__name__}",
        )

    # Prefer the canonical entry whose accession matches exactly; fall
    # back to the longest isoform, and record that substitution.
    entry = next((e for e in data if e.get("uniprotAccession") == accession), None)
    substituted = entry is None
    if substituted:
        entry = max(data, key=lambda e: e.get("sequenceEnd", 0) or 0)

    modelled = entry.get("sequenceEnd") or len(entry.get("uniprotSequence") or "")
    canonical, symbol = _canonical_length(accession)
    sidecar.note("canonical_length", canonical)
    sidecar.note("modelled_length", modelled)
    if symbol:
        sidecar.note("gene_symbol", symbol)

    if canonical is None:
        sidecar.warn(
            "Could not reach UniProt to confirm the canonical sequence length; "
            "the coverage of this model against the full-length protein is "
            "unverified.",
            code="afdb.coverage_unverified",
        )
    elif modelled and canonical > modelled:
        coverage = modelled / canonical
        sidecar.warn(
            f"Model covers {modelled} of {canonical} aa ({coverage:.0%}) of "
            f"canonical {symbol or accession}. AFDB returned no full-length "
            "entry. Confidence metrics below describe the modelled region "
            "ONLY and must not be relayed as properties of the whole protein.",
            code="afdb.partial_coverage",
        )

    if substituted:
        sidecar.warn(
            f"No canonical AFDB entry for {accession}; substituted isoform "
            f"{entry.get('uniprotAccession')} ({modelled} aa). This "
            "substitution must be relayed in any finding.",
            code="afdb.isoform_substituted",
        )

    if canonical and canonical > FRAGMENT_LENGTH:
        sidecar.warn(
            f"{accession} is {canonical} aa, above the {FRAGMENT_LENGTH} aa "
            "AFDB v4 modelling limit; no full-length prediction exists in the "
            "database for this protein at any accession.",
            code="afdb.above_modelling_limit",
        )

    entry_acc = entry.get("uniprotAccession", accession)
    stem = f"AF-{entry_acc}-F1"

    record_path = target_dir / f"{stem}.afdb.json"
    record_path.write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(record_path)

    outputs = {"afdb_record": record_path}

    cif_url = entry.get("cifUrl")
    if cif_url:
        cif_path = target_dir / f"{stem}.cif"
        cif_path.write_bytes(
            http.get_bytes(cif_url, qps=qps_for_host("alphafold.ebi.ac.uk"))
        )
        sidecar.add_output(cif_path)
        outputs["structure"] = cif_path
    else:
        sidecar.warn("AFDB record carries no cifUrl; no structure was downloaded.")

    pae_url = entry.get("paeDocUrl")
    if pae_url:
        pae_path = target_dir / f"{stem}.pae.json"
        pae_path.write_bytes(
            http.get_bytes(pae_url, qps=qps_for_host("alphafold.ebi.ac.uk"))
        )
        sidecar.add_output(pae_path)
        outputs["pae"] = pae_path
    else:
        sidecar.warn(
            "AFDB record carries no paeDocUrl; domain analysis is unavailable."
        )

    if "structure" not in outputs:
        raise EndpointError(
            f"no structure file could be downloaded for {accession}",
            detail="AFDB record present but cifUrl missing or unreachable",
        )

    sidecar.note("uniprot_requested", accession)
    sidecar.note("uniprot_returned", entry_acc)
    meta = sidecar.write(target_dir / f"{stem}.meta.json")

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("uniprot", entry_acc)
    emit.data("warnings", sidecar.warnings)
    for role, path in outputs.items():
        emit.path(project.relative(path), role)
    emit.path(project.relative(meta), "sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# Phase 2 — AFDB analysis
# ---------------------------------------------------------------------------


def _plddt_assessment(entry: dict, t) -> tuple[dict, dict]:
    """pLDDT band analysis. Algorithm from analyze_plddt.py."""
    frac_vlow = entry.get("fractionPlddtVeryLow", 0.0) or 0.0
    frac_low = entry.get("fractionPlddtLow", 0.0) or 0.0
    frac_conf = entry.get("fractionPlddtConfident", 0.0) or 0.0
    frac_vhigh = entry.get("fractionPlddtVeryHigh", 0.0) or 0.0
    confident_total = frac_conf + frac_vhigh

    conf_cut = t.get("plddt_confident")
    mod_cut = t.get("plddt_moderate")
    notable = t.get("disorder_notable")
    mixed = t.get("disorder_mixed")
    mostly = t.get("disorder_mostly")

    if confident_total >= conf_cut:
        if frac_vlow > notable:
            verdict = "confident-with-disorder"
            statement = "Mostly confidently predicted, with notable disordered regions."
        else:
            verdict = "confident"
            statement = "Confidently predicted and likely fully ordered."
    elif confident_total >= mod_cut:
        if frac_vlow >= mixed:
            verdict = "mixed"
            statement = (
                "A mixture of confidently predicted structured domains and "
                "significant intrinsically disordered regions."
            )
        else:
            verdict = "moderate"
            statement = (
                "Moderate prediction confidence; some regions may be flexible "
                "or poorly predicted."
            )
    else:
        if frac_vlow >= mostly:
            verdict = "highly-disordered"
            statement = (
                "Mostly poorly predicted; likely highly intrinsically disordered."
            )
        else:
            verdict = "low-confidence"
            statement = "Low prediction confidence overall."

    metrics = {
        "global_plddt": entry.get("globalMetricValue"),
        "fraction_very_low": frac_vlow,
        "fraction_low": frac_low,
        "fraction_confident": frac_conf,
        "fraction_very_high": frac_vhigh,
        "fraction_confident_total": round(confident_total, 4),
    }
    return metrics, {"verdict": verdict, "statement": statement}


def _find_sub_domains(pae, cutoff: float, min_size: int) -> list[list[int]]:
    """Algorithm carried over verbatim from analyze_pae.py."""
    n = len(pae)
    domains: list[list[int]] = []
    current: list[int] = []
    for i in range(n):
        if not current:
            current.append(i)
            continue
        window = min(20, len(current))
        recent = current[-window:]
        total = sum(pae[r][i] + pae[i][r] for r in recent)
        if total / (2.0 * window) < cutoff:
            current.append(i)
        else:
            if len(current) >= min_size:
                domains.append(current)
            current = [i]
    if len(current) >= min_size:
        domains.append(current)
    return [[c[0] + 1, c[-1] + 1] for c in domains]


def _merge_domains(
    bounds, pae, merge_cutoff: float, min_global: int
) -> list[list[int]]:
    if not bounds:
        return []
    if len(bounds) == 1:
        merged = [list(bounds[0])]
    else:
        merged = [list(bounds[0])]
        for nxt in bounds[1:]:
            prev_end = merged[-1][1] - 1
            curr_start = nxt[0] - 1
            lookback = max(merged[-1][0] - 1, prev_end - 30)
            lookfwd = min(nxt[1] - 1, curr_start + 30)
            total = 0.0
            pairs = 0
            for r1 in range(lookback, prev_end + 1):
                for r2 in range(curr_start, lookfwd + 1):
                    total += pae[r1][r2] + pae[r2][r1]
                    pairs += 2
            if pairs and (total / pairs) < merge_cutoff:
                merged[-1][1] = nxt[1]
            else:
                merged.append(list(nxt))
    return [d for d in merged if (d[1] - d[0] + 1) > min_global]


def _pae_assessment(pae_doc: Any, t) -> tuple[dict, dict]:
    if isinstance(pae_doc, list) and pae_doc:
        block = pae_doc[0]
    elif isinstance(pae_doc, dict):
        block = pae_doc
    else:
        raise SchemaError("PAE document has an unrecognised top-level shape")

    if "predicted_aligned_error" in block:
        pae = block["predicted_aligned_error"]
    elif "distance" in block:
        pae = block["distance"]
    else:
        raise SchemaError(
            "could not locate a PAE matrix in the PAE document",
            detail=f"keys present: {', '.join(sorted(block))}",
        )
    if not pae or not pae[0]:
        raise SchemaError("PAE matrix is empty")

    flat = list(itertools.chain.from_iterable(pae))
    confident_cut = t.get("pae_confident_pair")
    sub = _find_sub_domains(
        pae, t.get("pae_domain_cutoff"), t.get("pae_min_domain_size")
    )
    domains = _merge_domains(
        sub, pae, t.get("pae_merge_cutoff"), t.get("pae_min_global_domain")
    )

    if len(domains) == 1:
        verdict = "single-domain"
        statement = "A single well-folded, rigid composite domain."
    elif len(domains) > 1:
        verdict = "multi-domain"
        statement = (
            f"{len(domains)} independently positioned global domains separated "
            "by flexible joints."
        )
    else:
        verdict = "no-rigid-domain"
        statement = "No distinct rigidly-folded domain detected; likely disordered."

    metrics = {
        "matrix_size": [len(pae), len(pae[0])],
        "mean_pae": round(sum(flat) / len(flat), 3),
        "min_pae": round(min(flat), 3),
        "max_pae": round(max(flat), 3),
        "fraction_confident_pairs": round(
            sum(1 for p in flat if p < confident_cut) / len(flat), 4
        ),
    }
    assessment = {
        "verdict": verdict,
        "statement": statement,
        "ordered_domains": domains,
    }
    return metrics, assessment


@alphafold.command()
@click.argument("uniprot_or_path")
@click.option("--plddt-confident", type=float, default=None, help="Override threshold.")
@click.option("--plddt-moderate", type=float, default=None, help="Override threshold.")
@click.option(
    "--pae-domain-cutoff", type=float, default=None, help="Override threshold."
)
@from_option
@out_option
@output_options
@pass_state
def analyze(
    state: AppState,
    uniprot_or_path: str,
    plddt_confident: float | None,
    plddt_moderate: float | None,
    pae_domain_cutoff: float | None,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Analyze a fetched AFDB structure. Accepts a UniProt ID or a path.

    Reads the stored Layer 0 artifacts and applies the `alphafold`
    threshold set. Does not re-query AFDB.
    """
    project = state.project()
    source_dir = project.artifact_dir(ARTIFACT_CLASS, from_dir)
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)

    candidate = Path(uniprot_or_path)
    if candidate.suffix:
        record_path = candidate if candidate.is_absolute() else project.root / candidate
        stem = record_path.name.replace(".afdb.json", "")
    else:
        accession = uniprot_or_path.strip().upper()
        stem = f"AF-{accession}-F1"
        record_path = source_dir / f"{stem}.afdb.json"

    if not record_path.is_file() and not candidate.suffix:
        # AFDB may have served an isoform for the accession asked for, in
        # which case `fetch Q8NF91` wrote AF-Q8NF91-6-F1 and looking up
        # the bare accession finds nothing. Recover the round-trip, but
        # never by guessing: with more than one isoform on disk there is
        # no way to tell which the caller meant, and picking one would
        # attach the finding to a molecule they did not name — the exact
        # error the isoform_substituted relay exists to prevent.
        isoforms = sorted(source_dir.glob(f"AF-{accession}-*-F1.afdb.json"))
        if len(isoforms) == 1:
            record_path = isoforms[0]
            stem = record_path.name.replace(".afdb.json", "")
        elif len(isoforms) > 1:
            raise Refusal(
                f"{accession} has {len(isoforms)} isoform records fetched; the "
                "accession alone does not say which to analyse",
                detail=", ".join(p.name for p in isoforms),
                remedy="pass the path of the isoform you mean rather than the "
                "bare accession",
            )

    if not record_path.is_file():
        raise ArtifactError(
            f"no fetched AFDB record at {record_path}",
            remedy=f"run `dde alphafold fetch {uniprot_or_path}` first",
        )

    entry = provenance.read_json(record_path, "AFDB record")
    thresholds = load_thresholds(
        state,
        "alphafold",
        {
            "plddt_confident": plddt_confident,
            "plddt_moderate": plddt_moderate,
            "pae_domain_cutoff": pae_domain_cutoff,
        },
    )

    plddt_metrics, plddt_assessment = _plddt_assessment(entry, thresholds)

    advisories: list[str] = []
    pae_metrics: dict[str, Any] = {}
    pae_assessment: dict[str, Any] = {}
    pae_path = record_path.with_name(f"{stem}.pae.json")
    if pae_path.is_file():
        pae_doc = provenance.read_json(pae_path, "PAE document")
        pae_metrics, pae_assessment = _pae_assessment(pae_doc, thresholds)
    else:
        advisories.append(
            "No PAE artifact present; domain boundaries were not derived."
        )

    # Relay warnings recorded at fetch time so they reach the analysis.
    meta_path = record_path.with_name(f"{stem}.meta.json")
    fetch_warnings: list[str] = []
    fetch_relays: list[dict] = []
    coverage: float | None = None
    canonical_length = modelled_length = None
    if meta_path.is_file():
        meta = provenance.read_json(meta_path, "provenance sidecar")
        fetch_warnings = meta.get("warnings", []) or []
        fetch_relays = meta.get("mandatory_relays", []) or []
        canonical_length = meta.get("canonical_length")
        modelled_length = meta.get("modelled_length")
        if canonical_length and modelled_length:
            coverage = modelled_length / canonical_length

    # Scope the verdict to what was actually modelled. Read from the
    # sidecar's structured fields rather than by matching warning text.
    # Without this an incidentally well-modelled 20% of a protein reads
    # as "confidently predicted and likely fully ordered" — a statement
    # about a fragment presented as one about the protein.
    statement = plddt_assessment["statement"]
    if coverage is not None and coverage < 1.0:
        statement = (
            f"{statement.rstrip('.')} — but this describes only the modelled "
            f"{modelled_length} aa ({coverage:.0%} of the {canonical_length} aa "
            "canonical sequence), not the whole protein."
        )
        advisories.append(
            f"Partial coverage: {coverage:.0%} of the canonical sequence is "
            "modelled. Residue numbering below is local to the modelled "
            "region. Do not state a whole-protein conclusion from this."
        )

    if plddt_assessment["verdict"] == "highly-disordered":
        advisories.append(
            "Highly disordered: whole-protein downstream structural work "
            "(docking, structural search) is not supported by this prediction. "
            "Restrict any further analysis to ordered domain boundaries."
        )
    elif plddt_assessment["verdict"] == "mixed" and pae_assessment.get(
        "ordered_domains"
    ):
        advisories.append(
            "Mixed order/disorder: restrict downstream structural work to the "
            "listed ordered domains."
        )

    # Claim-triggered relays for high-confidence verdicts. A full-coverage,
    # high-pLDDT model used to produce zero relays — the run that went well
    # was the one with the most quotable number and the least guidance.
    # These guard the two well-known wrong readings of a confident fold.
    #
    # Conditional for the same reason fpocket.druggability_is_not_affinity
    # and gnomad.constraint_is_not_safety are: below the confidence cutoff
    # there is no accuracy or docking claim available to make, and a relay
    # that fired on every run would be quoted and ignored.
    plddt_verdict = plddt_assessment["verdict"]
    if plddt_verdict in ("confident", "confident-with-disorder"):
        fetch_relays.append(
            provenance.relay(
                "afdb.confidence_is_not_accuracy",
                f"pLDDT {plddt_metrics['global_plddt']} for {stem} is "
                "prediction self-confidence, not agreement with experiment "
                "and not confirmation of the biological conformation.",
            )
        )

    # The fully ordered, high-confidence verdict is the one that looks most
    # like a crystal structure and invites docking. "confident-with-disorder"
    # already carries disorder advisories that discourage whole-protein
    # structural work; the unqualified "confident" verdict is where the
    # docking over-read is unguarded.
    if plddt_verdict == "confident":
        fetch_relays.append(
            provenance.relay(
                "afdb.model_is_not_docking_ready",
                f"{stem} is a predicted fold, not an experimental structure; "
                "side-chain and loop conformations — what docking depends "
                "on — are the parts pLDDT is least informative about.",
            )
        )

    assessment = {
        "verdict": plddt_assessment["verdict"],
        "statement": statement,
        "domain_verdict": pae_assessment.get("verdict"),
        "ordered_domains": pae_assessment.get("ordered_domains", []),
        "advisories": advisories,
        "relayed_fetch_warnings": fetch_warnings,
    }
    metrics = {
        "plddt": plddt_metrics,
        "pae": pae_metrics,
        "coverage": {
            "canonical_length": canonical_length,
            "modelled_length": modelled_length,
            "fraction": round(coverage, 4) if coverage is not None else None,
        },
    }

    analysis_path = target_dir / f"{stem}.alphafold.analysis.json"
    provenance.write_analysis(
        analysis_path,
        source=project.relative(meta_path)
        if meta_path.is_file()
        else project.relative(record_path),
        threshold_set=thresholds.tag,
        thresholds_applied=thresholds.applied(),
        threshold_sources=thresholds.sources(),
        threshold_provenance=thresholds.provenance,
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=fetch_relays,
        suppress_warnings=as_json,
    )

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("assessment", assessment)
    emit.data("metrics", metrics)
    emit.data("mandatory_relays", fetch_relays)
    emit.data("threshold_set", thresholds.tag)
    emit.line(f"{stem}  [threshold_set {thresholds.tag}]")
    emit.line(
        f"pLDDT: global {plddt_metrics['global_plddt']}, "
        f"confident fraction {plddt_metrics['fraction_confident_total']:.2f} "
        f"-> {plddt_assessment['verdict']}"
    )
    if pae_metrics:
        domains = pae_assessment.get("ordered_domains", [])
        emit.line(
            f"PAE: mean {pae_metrics['mean_pae']} A, "
            f"{pae_metrics['fraction_confident_pairs']:.0%} confident pairs "
            f"-> {pae_assessment['verdict']}"
        )
        if domains:
            shown = ", ".join(f"{s}-{e}" for s, e in domains[:6])
            emit.line(f"Ordered domains ({len(domains)}): {shown}")
    for warning in fetch_warnings[:3]:
        emit.line(f"! {warning}")
    for advisory in advisories[:3]:
        emit.line(f"* {advisory}")
    emit.path(project.relative(analysis_path), "analysis")
    emit.flush()


# ---------------------------------------------------------------------------
# Phase 1 — AlphaFold 3 on Vertex
# ---------------------------------------------------------------------------

AF3_PROJECT = os.environ.get("DDE_AF3_PROJECT", "pharma-oss-factory")
AF3_REGION = os.environ.get("DDE_AF3_REGION", "us-central1")
AF3_DISPLAY_NAME = os.environ.get(
    "DDE_AF3_ENDPOINT_NAME", "AlphaFold 3 Dedicated Endpoint"
)

_COLD_START_RE = re.compile(r"model is not yet ready", re.IGNORECASE)
_BUSY_RE = re.compile(r"already working on a prediction", re.IGNORECASE)

_ALLOWED_CHAIN_KEYS = {"protein", "rna", "dna", "ligand"}

# ---------------------------------------------------------------------------
# AF3 input templates (Item 2)
# ---------------------------------------------------------------------------

_AF3_TEMPLATES: dict[str, dict] = {
    "default": {
        "dialect": "alphafold3",
        "version": 1,
        "name": "my_prediction",
        "modelSeeds": [42],
        "sequences": [
            {"protein": {"id": "A", "sequence": "REPLACE_WITH_SEQUENCE"}},
            {"ligand": {"id": "B", "smiles": "REPLACE_WITH_SMILES"}},
        ],
    },
    "complex": {
        "dialect": "alphafold3",
        "version": 1,
        "name": "my_complex",
        "modelSeeds": [42],
        "sequences": [
            {"protein": {"id": "A", "sequence": "REPLACE_WITH_SEQUENCE_A"}},
            {"protein": {"id": "B", "sequence": "REPLACE_WITH_SEQUENCE_B"}},
        ],
    },
    "ligand": {
        "dialect": "alphafold3",
        "version": 1,
        "name": "my_docking",
        "modelSeeds": [42],
        "sequences": [
            {"protein": {"id": "A", "sequence": "REPLACE_WITH_SEQUENCE"}},
            {"ligand": {"id": "B", "smiles": "REPLACE_WITH_SMILES"}},
        ],
    },
}


def _af3_template(template_type: str) -> dict:
    """Return an AF3 input JSON template by type.

    Accepted types: 'default', 'complex' (protein-protein),
    'ligand' (protein-ligand). Raises UsageError for unknown types.
    """
    template = _AF3_TEMPLATES.get(template_type)
    if template is None:
        raise UsageError(
            f"unknown template type {template_type!r}",
            detail=f"valid types: {', '.join(sorted(_AF3_TEMPLATES))}",
            remedy="use --template, --template complex, or --template ligand",
        )
    # Return a deep copy so callers cannot mutate the canonical templates.
    return json.loads(json.dumps(template))


# ---------------------------------------------------------------------------
# Per-residue pLDDT computation (Item 1 — SAFETY-RELEVANT)
# ---------------------------------------------------------------------------
#
# AF3 produces per-atom pLDDT values stored in the B-factor column of
# the CIF file.  Indexing these values as per-residue requires grouping
# atoms by chain and residue number and computing the arithmetic mean.
# The indexing scheme is documented in the output so no downstream
# consumer can mistake per-atom values for per-residue values.


def _parse_cif_plddt(
    cif_text: str,
) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """Parse per-atom B-factors from a CIF file, return per-residue mean pLDDT.

    AF3 stores pLDDT in the ``B_iso_or_equiv`` column.  Atoms are
    grouped by ``(auth_asym_id, auth_seq_id)`` — the user-facing chain
    and residue number.

    Returns:
        ``(per_residue_plddt, chain_mean_plddt)``

        ``per_residue_plddt`` maps chain → {residue_number → mean pLDDT}::

            {"A": {"1": 92.3, "2": 88.1}, "B": {"1": 45.2}}

        ``chain_mean_plddt`` maps chain → mean pLDDT over all residues.
    """
    lines = cif_text.splitlines()
    columns: list[str] = []
    data_start = 0
    in_atom_site = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("_atom_site."):
            in_atom_site = True
            columns.append(stripped.split(".")[1])
        elif in_atom_site:
            data_start = i
            break

    if not columns:
        raise SchemaError(
            "CIF file contains no _atom_site header block",
            detail="cannot extract per-atom pLDDT without atom site columns",
        )

    def _col(name: str, fallback: str | None = None) -> int | None:
        if name in columns:
            return columns.index(name)
        if fallback and fallback in columns:
            return columns.index(fallback)
        return None

    col_chain = _col("auth_asym_id", "label_asym_id")
    col_resnum = _col("auth_seq_id", "label_seq_id")
    col_bfactor = _col("B_iso_or_equiv")

    if col_chain is None or col_resnum is None or col_bfactor is None:
        missing = []
        if col_chain is None:
            missing.append("chain (auth_asym_id)")
        if col_resnum is None:
            missing.append("residue number (auth_seq_id)")
        if col_bfactor is None:
            missing.append("B-factor (B_iso_or_equiv)")
        raise SchemaError(
            f"CIF file missing required columns: {', '.join(missing)}",
            detail=f"available columns: {', '.join(columns)}",
        )

    # Accumulate per-atom pLDDT values grouped by (chain, resnum).
    atom_values: dict[tuple[str, str], list[float]] = {}

    for line in lines[data_start:]:
        if not line.startswith(("ATOM", "HETATM")):
            continue
        fields = line.split()
        try:
            chain = fields[col_chain]
            resnum = fields[col_resnum]
            bfactor = float(fields[col_bfactor])
        except (ValueError, IndexError):
            continue
        atom_values.setdefault((chain, resnum), []).append(bfactor)

    if not atom_values:
        raise SchemaError(
            "no atom records found in CIF file",
            detail="the CIF file appears to contain no ATOM/HETATM records",
        )

    # Compute per-residue means.
    per_residue: dict[str, dict[str, float]] = {}
    chain_accum: dict[str, list[float]] = {}

    for (chain, resnum), values in sorted(atom_values.items()):
        mean_val = round(sum(values) / len(values), 2)
        per_residue.setdefault(chain, {})[resnum] = mean_val
        chain_accum.setdefault(chain, []).append(mean_val)

    chain_means: dict[str, float] = {}
    for chain, residue_means in sorted(chain_accum.items()):
        chain_means[chain] = round(sum(residue_means) / len(residue_means), 2)

    return per_residue, chain_means


def _response_bytes(response: Any) -> bytes:
    """The response body, whatever transport object carried it.

    `raw_predict` returns a `requests.Response`, not the protobuf
    `HttpBody` the SDK docs describe, and the two expose the body under
    different names. Guessing wrong is expensive in a specific way: the
    prediction succeeds, the H100 time is spent, and the result is then
    thrown away by a TypeError — which is exactly what happened before
    this function existed.

    Raising on an unrecognised object is deliberate. The previous code
    fell through to the object itself, and the next line down stringified
    it into the saved artifact, so the preserved "raw response" would
    have read `<Response [200]>`. A crash costs one prediction; a
    plausible-looking artifact containing no data costs whatever is
    later concluded from it.
    """
    for attribute in ("content", "data", "_content"):
        body = getattr(response, attribute, None)
        if isinstance(body, (bytes, bytearray)):
            return bytes(body)
        if isinstance(body, str):
            return body.encode("utf-8")
    if isinstance(response, (bytes, bytearray)):
        return bytes(response)
    if isinstance(response, str):
        return response.encode("utf-8")
    raise SchemaError(
        f"cannot read a response body from {type(response).__name__}",
        detail=f"tried .content, .data, ._content; attributes: "
        f"{', '.join(sorted(a for a in dir(response) if not a.startswith('__')))[:300]}",
        remedy="the endpoint transport has changed shape; _response_bytes needs "
        "updating before any prediction can be saved",
    )


def _validate_af3_input(payload: dict) -> None:
    """Fail fast before spending H100 minutes. From af3_client.py."""
    if not isinstance(payload, dict):
        raise UsageError("AF3 input must be a JSON object")
    for key in ("name", "modelSeeds", "sequences", "dialect", "version"):
        if key not in payload:
            raise UsageError(f"AF3 input missing required field: {key}")
    if not isinstance(payload["modelSeeds"], list) or not payload["modelSeeds"]:
        raise UsageError("modelSeeds must be a non-empty list of integers")
    if payload["dialect"] != "alphafold3":
        raise UsageError(f"dialect must be 'alphafold3'; got {payload['dialect']!r}")
    if payload["version"] not in (1, 2, 3):
        raise UsageError(f"version must be 1, 2 or 3; got {payload['version']!r}")
    if not isinstance(payload["sequences"], list) or not payload["sequences"]:
        raise UsageError("sequences must be a non-empty list")
    seen: set[str] = set()
    for i, chain in enumerate(payload["sequences"]):
        if not isinstance(chain, dict) or len(chain) != 1:
            raise UsageError(f"sequences[{i}] must be a single-key object")
        kind = next(iter(chain))
        if kind not in _ALLOWED_CHAIN_KEYS:
            raise UsageError(
                f"sequences[{i}] type {kind!r} not one of "
                f"{', '.join(sorted(_ALLOWED_CHAIN_KEYS))}"
            )
        entry = chain[kind]
        if not isinstance(entry, dict) or "id" not in entry:
            raise UsageError(f"sequences[{i}].{kind}.id is required")
        if entry["id"] in seen:
            raise UsageError(f"duplicate chain id {entry['id']!r}")
        seen.add(entry["id"])


# ---------------------------------------------------------------------------
# Lock helpers — used by ``predict`` for single-flight serialisation.
# ---------------------------------------------------------------------------

_LOCK_POLL_INTERVAL = 0.5  # seconds between non-blocking lock attempts


def _resolve_lock_path() -> Path:
    """Return a user-private lock-file path for AF3 prediction serialisation.

    Respects ``DDE_AF3_LOCK`` when set explicitly; otherwise defaults to
    ``~/.cache/dde/af3.lock`` and creates the parent directory (mode 0o700)
    if it does not yet exist.
    """
    env = os.environ.get("DDE_AF3_LOCK", "")
    if env:
        return Path(env)
    lock_dir = Path.home() / ".cache" / "dde"
    lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return lock_dir / "af3.lock"


def _acquire_lock_bounded(fd: int, deadline_mono: float) -> None:
    """Acquire an exclusive flock on *fd*, raising if *deadline_mono* elapses.

    Uses non-blocking attempts with a short polling interval so that the
    ``--deadline`` timer is respected even while waiting for the lock.

    Raises :class:`EndpointUnavailable` on timeout.
    """
    import fcntl

    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return  # lock acquired
        except OSError:
            # EWOULDBLOCK / EAGAIN — lock is held by another process
            pass
        if time.monotonic() >= deadline_mono:
            raise EndpointUnavailable(
                "timed out waiting for the AF3 lock file — another "
                "prediction is likely running on this host",
                remedy="wait for the other prediction to finish, "
                "raise --deadline, or remove the stale lock file",
            )
        # Sleep for the poll interval, but not past the deadline.
        remaining = deadline_mono - time.monotonic()
        time.sleep(min(_LOCK_POLL_INTERVAL, max(remaining, 0)))


@alphafold.command()
@click.option(
    "--input",
    "input_file",
    required=False,
    default=None,
    help="AF3 input JSON (the config object, or an instances-wrapped one). "
    "Relative paths resolve against the project root.",
)
@click.option(
    "--template",
    "template_type",
    default=None,
    is_flag=False,
    flag_value="default",
    help="Print an AF3 input JSON template to stdout and exit. "
    "Values: default, complex (protein-protein), ligand (protein-ligand).",
)
@click.option("--deadline", type=float, default=1800.0, help="Total time cap, seconds.")
@click.option(
    "--cold-start-wait",
    type=float,
    default=25.0,
    help="Seconds between cold-start polls.",
)
@click.option(
    "--busy-wait", type=float, default=15.0, help="Seconds between busy polls."
)
@click.option(
    "--gateway-timeout-wait",
    type=float,
    default=60.0,
    help="Seconds to wait after a 504 gateway timeout before retrying.",
)
@out_option
@output_options
@pass_state
def predict(
    state: AppState,
    input_file: str | None,
    template_type: str | None,
    deadline: float,
    cold_start_wait: float,
    busy_wait: float,
    gateway_timeout_wait: float,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Run one AlphaFold 3 prediction on the Vertex dedicated endpoint.

    The endpoint is single-flight (max one replica) and returns 429 under
    concurrency; this command serialises callers on one machine with a
    file lock and applies two-tier backoff.

    Cross-container concurrency is NOT solved here — see the lease broker
    note in tool-design-guidance.md §8. Parallel specialists in separate
    containers can still collide.

    With ``--template``, prints a valid AF3 input JSON template to stdout
    and exits without running a prediction.
    """
    if template_type is not None:
        click.echo(json.dumps(_af3_template(template_type), indent=2))
        return

    if input_file is None:
        raise UsageError(
            "--input is required when not using --template",
            remedy="pass --input <path> to supply the AF3 input JSON, or "
            "--template to print a template",
        )

    try:
        from google.cloud import aiplatform
    except ImportError as e:
        raise DependencyError(
            "google-cloud-aiplatform is not installed; AF3 prediction is unavailable",
            remedy="install google-cloud-aiplatform into the tools environment, "
            "then re-run `dde doctor`",
        ) from e

    payload = provenance.read_json(Path(input_file), "AF3 input")
    if isinstance(payload, dict) and "instances" in payload:
        instances = payload["instances"]
        if not isinstance(instances, list) or len(instances) != 1:
            raise UsageError(
                "the AF3 endpoint accepts exactly one instance per request",
                detail=f"input carries {len(instances) if isinstance(instances, list) else '?'}",
            )
        payload = instances[0]
    _validate_af3_input(payload)

    project = state.project()
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)
    job = payload["name"]
    stem = f"AF3-{job}"

    aiplatform.init(project=AF3_PROJECT, location=AF3_REGION)
    matches = aiplatform.Endpoint.list(filter=f'display_name="{AF3_DISPLAY_NAME}"')
    if not matches:
        raise EndpointUnavailable(
            f"no Vertex endpoint named {AF3_DISPLAY_NAME!r} in "
            f"{AF3_PROJECT}/{AF3_REGION}",
            remedy="confirm the endpoint is deployed, or set DDE_AF3_ENDPOINT_NAME",
        )
    if len(matches) > 1:
        raise EndpointError(
            f"multiple endpoints match {AF3_DISPLAY_NAME!r}",
            detail=", ".join(e.resource_name for e in matches),
        )
    endpoint = matches[0]

    sidecar = provenance.Sidecar(
        tool=TOOL_AF3,
        subcommand="predict",
        endpoint=endpoint.resource_name,
        parameters={
            "job_name": job,
            "dialect": payload.get("dialect"),
            "version": payload.get("version"),
            "model_seeds": payload.get("modelSeeds"),
            "n_chains": len(payload["sequences"]),
        },
    )

    body = json.dumps({"instances": [payload]}).encode("utf-8")
    lock_path = _resolve_lock_path()
    started = time.time()
    limit = time.monotonic() + deadline
    attempts = 0

    import fcntl

    fd = os.open(
        str(lock_path),
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        _acquire_lock_bounded(fd, limit)
        while True:
            attempts += 1
            if time.monotonic() > limit:
                raise EndpointUnavailable(
                    f"AF3 prediction exceeded the {deadline:.0f}s deadline after "
                    f"{attempts - 1} attempt(s)",
                    remedy="raise --deadline, or retry when the endpoint is warm",
                )
            try:
                response = endpoint.raw_predict(
                    body=body, headers={"Content-Type": "application/json"}
                )
            except Exception as exc:
                if time.monotonic() + busy_wait > limit:
                    raise EndpointUnavailable(
                        "transport failure calling the AF3 endpoint",
                        detail=f"{type(exc).__name__}: {exc}",
                    ) from exc
                elapsed = round(time.time() - started)
                click.echo(
                    f"waiting for AF3 endpoint (attempt {attempts}, "
                    f"{elapsed}s elapsed, transport error — "
                    f"retrying in {busy_wait:.0f}s)...",
                    err=True,
                )
                time.sleep(busy_wait)
                continue

            status = getattr(response, "status_code", 200)
            data = _response_bytes(response)

            if status == 200:
                break

            try:
                detail = json.loads(data).get("detail", "")
                if isinstance(detail, list):
                    detail = json.dumps(detail)
            except Exception:
                detail = str(data)[:500]

            if status == 429:
                wait = cold_start_wait if _COLD_START_RE.search(detail) else busy_wait
                if _COLD_START_RE.search(detail):
                    sidecar.warn(
                        "Endpoint was scaling from zero; cold-start delay incurred."
                    )
                elif _BUSY_RE.search(detail):
                    sidecar.warn(
                        "Endpoint was busy with another prediction; call was queued."
                    )
                elapsed = round(time.time() - started)
                click.echo(
                    f"waiting for AF3 endpoint (attempt {attempts}, "
                    f"{elapsed}s elapsed, retrying in {wait:.0f}s)...",
                    err=True,
                )
                time.sleep(wait)
                continue

            if status == 504:
                sidecar.warn(
                    "Gateway timeout (504) — likely a cold-start timeout at the "
                    "gateway level; retrying on a now-warm endpoint."
                )
                elapsed = round(time.time() - started)
                click.echo(
                    f"waiting for AF3 endpoint (attempt {attempts}, "
                    f"{elapsed}s elapsed, 504 gateway timeout — "
                    f"retrying in {gateway_timeout_wait:.0f}s)...",
                    err=True,
                )
                time.sleep(gateway_timeout_wait)
                continue

            raise EndpointError(
                f"AF3 endpoint returned HTTP {status}", detail=detail[:500]
            )
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    parsed = json.loads(data)
    predictions = parsed.get("predictions")
    if not predictions:
        raise SchemaError(
            "AF3 response carried no predictions",
            detail=f"keys: {', '.join(sorted(parsed))}",
        )
    pred = predictions[0]

    raw_path = target_dir / f"{stem}.response.json"
    # Bytes by construction — _response_bytes raises rather than handing
    # back something that would stringify into a convincing-looking file.
    raw_path.write_bytes(data)
    sidecar.add_output(raw_path)

    request_path = target_dir / f"{stem}.request.json"
    request_path.write_text(
        json.dumps({"instances": [payload]}, indent=2), encoding="utf-8"
    )
    sidecar.add_output(request_path)

    outputs = {"response": raw_path, "request": request_path}

    if "structure_cif" in pred:
        cif_path = target_dir / f"{stem}.cif"
        cif_path.write_text(pred["structure_cif"], encoding="utf-8")
        sidecar.add_output(cif_path)
        outputs["structure"] = cif_path
    if "summary" in pred:
        summary_path = target_dir / f"{stem}.summary.json"
        summary_path.write_text(json.dumps(pred["summary"], indent=2), encoding="utf-8")
        sidecar.add_output(summary_path)
        outputs["summary"] = summary_path
    if "plddt" in pred:
        plddt_path = target_dir / f"{stem}.plddt.json"
        plddt_path.write_text(json.dumps(pred["plddt"]), encoding="utf-8")
        sidecar.add_output(plddt_path)
    if "pae" in pred:
        pae_path = target_dir / f"{stem}.pae.json"
        pae_path.write_text(json.dumps(pred["pae"]), encoding="utf-8")
        sidecar.add_output(pae_path)

    if len(payload.get("modelSeeds", [])) > 1:
        sidecar.warn(
            f"{len(payload['modelSeeds'])} model seeds requested but the Vertex "
            "container returns only the top-ranked structure; per-seed outputs "
            "are not available."
        )

    sidecar.note("attempts", attempts)
    sidecar.note("wall_time_s", round(time.time() - started, 1))
    meta = sidecar.write(target_dir / f"{stem}.meta.json")

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("job", job)
    emit.data("attempts", attempts)
    emit.data("warnings", sidecar.warnings)
    for role, path in outputs.items():
        emit.path(project.relative(path), role)
    emit.path(project.relative(meta), "sidecar")
    emit.flush()


@alphafold.command("analyze-prediction")
@click.argument("summary_path")
@out_option
@output_options
@pass_state
def analyze_prediction(
    state: AppState, summary_path: str, out: str | None, as_json: bool, quiet: bool
) -> None:
    """Analyze a stored AF3 prediction summary against the `af3` thresholds.

    SUMMARY_PATH is the `.summary.json` written by `predict`.
    """
    project = state.project()
    path = resolve_artifact(state, summary_path, "AF3 summary")
    summary = provenance.read_json(path, "AF3 summary")
    thresholds = load_thresholds(state, "af3")

    iptm = summary.get("iptm")
    ptm = summary.get("ptm")
    ranking = summary.get("ranking_score")
    disordered = summary.get("fraction_disordered")
    has_clash = summary.get("has_clash")

    advisories: list[str] = []
    if iptm is None:
        advisories.append(
            "ipTM is null — single-chain prediction; interface confidence is "
            "not defined and must not be reported."
        )
        verdict = "monomer"
        if ptm is not None:
            verdict = (
                "confident"
                if ptm >= thresholds.get("ptm_confident")
                else "low-confidence"
            )
    else:
        if iptm >= thresholds.get("iptm_confident"):
            verdict = "confident-interface"
        elif iptm >= thresholds.get("iptm_borderline"):
            verdict = "borderline-interface"
            advisories.append(
                "Interface confidence is borderline; treat the complex geometry "
                "as a hypothesis, not a result."
            )
        else:
            verdict = "low-confidence-interface"
            advisories.append(
                "Interface confidence is low; this complex should not be used "
                "for downstream structural work."
            )

    if has_clash:
        advisories.append("Prediction contains a steric clash (has_clash set).")
    if disordered is not None and disordered > thresholds.get(
        "fraction_disordered_notable"
    ):
        advisories.append(
            f"{disordered:.0%} of the model is disordered; restrict downstream "
            "work to ordered regions."
        )

    # `chain_ids` is deliberately not carried through. Despite the name
    # it holds one entry per *residue*, not per chain: a 75-residue
    # monomer returns 75 copies of "A". Anything reaching for it to count
    # chains gets the sequence length instead, and a monomer looks like a
    # 75-chain complex. `chain_ptm` is the per-chain array (length 1
    # here), and the request's own sequence count is recorded as
    # n_chains on the phase-1 sidecar.
    metrics = {
        "ptm": ptm,
        "iptm": iptm,
        "ranking_score": ranking,
        "fraction_disordered": disordered,
        "has_clash": has_clash,
        "chain_ptm": summary.get("chain_ptm"),
    }
    assessment = {"verdict": verdict, "advisories": advisories}

    analysis_path = beside_or_out(
        state, path, path.name.replace(".summary.json", ".alphafold.analysis.json"), out
    )
    provenance.write_analysis(
        analysis_path,
        source=project.relative(path),
        threshold_set=thresholds.tag,
        thresholds_applied=thresholds.applied(),
        threshold_sources=thresholds.sources(),
        threshold_provenance=thresholds.provenance,
        metrics=metrics,
        assessment=assessment,
        suppress_warnings=as_json,
    )

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("assessment", assessment)
    emit.data("metrics", metrics)
    emit.line(f"{path.name}  [threshold_set {thresholds.tag}]")
    emit.line(f"Verdict: {verdict}")
    emit.line(f"pTM {ptm}, ipTM {iptm}, ranking_score {ranking}")
    for advisory in advisories[:4]:
        emit.line(f"* {advisory}")
    emit.path(project.relative(analysis_path), "analysis")
    emit.flush()


@alphafold.command("analyze-plddt")
@click.argument("cif_path")
@out_option
@output_options
@pass_state
def analyze_plddt(
    state: AppState, cif_path: str, out: str | None, as_json: bool, quiet: bool
) -> None:
    """Compute per-residue pLDDT means from an AF3 prediction CIF file.

    SAFETY-RELEVANT: AF3 produces per-atom pLDDT values in the B-factor
    column of the CIF output. Indexing these directly as per-residue
    values yields plausible but wrong numbers (silent corruption).
    This command groups atoms by chain and residue number and computes
    the arithmetic mean, documenting the indexing scheme.

    CIF_PATH is the ``.cif`` file from ``predict``.
    """
    project = state.project()
    path = resolve_artifact(state, cif_path, "AF3 CIF structure")

    cif_text = path.read_text(encoding="utf-8", errors="replace")
    per_residue, chain_means = _parse_cif_plddt(cif_text)

    stem = path.stem
    # Strip common AF3 naming suffixes to derive a clean stem.
    for suffix in (".cif",):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]

    result = {
        "source": path.name,
        "per_residue_plddt": per_residue,
        "chain_mean_plddt": chain_means,
        "indexing": "per_residue_mean_of_per_atom_values",
        "note": (
            "Values are arithmetic means of per-atom pLDDT (B-factor) "
            "values grouped by (chain, residue_number). Do NOT index "
            "a flat per-atom array as per-residue — the number of atoms "
            "per residue varies, and the resulting values are wrong."
        ),
    }

    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)
    analysis_path = target_dir / f"{stem}.plddt_analysis.json"
    analysis_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("per_residue_plddt", per_residue)
    emit.data("chain_mean_plddt", chain_means)
    emit.data("indexing", result["indexing"])
    emit.line(f"{path.name}: {len(per_residue)} chain(s)")
    for chain, mean in chain_means.items():
        n_res = len(per_residue.get(chain, {}))
        emit.line(f"  chain {chain}: {n_res} residues, mean pLDDT {mean:.1f}")
    emit.path(project.relative(analysis_path), "plddt_analysis")
    emit.flush()


# Register ``search`` as an alias for ``fetch`` (issue #92).
alphafold.add_command(fetch, "search")
