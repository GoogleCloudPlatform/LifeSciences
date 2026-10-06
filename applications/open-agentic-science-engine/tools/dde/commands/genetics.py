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

"""`dde genetics` — gnomAD constraint: is human loss of function tolerated?

Measured from observed human population data, not predicted. That is the
whole reason this tool is in the pilot: the tournament's safety arguments
were reaching for the question *what happens to people who lack this
gene?*, and gnomAD answers it by counting who exists.

Two phases:

  fetch    one gene -> the gnomAD GraphQL constraint record, written
           verbatim to Layer 0 with a sidecar.
  analyze  applies `gnomad-constraint@1.0` and returns lof_intolerant /
           lof_tolerant / indeterminate. No network.

Three things about the API shape a caller has to get right:

**gnomAD answers a failed query with HTTP 200.** `gene_symbol:"NOTAGENE"`
returns `{"errors":[{"message":"Gene not found"}],"data":{"gene":null}}`
with a 200 status. A client that checks only the status code writes an
artifact containing a null gene and reads it later as missing data. The
`errors` key is checked on every response.

**The same 200 also carries transient errors, and telling the two apart
is the whole job.** `{"errors":[{"message":"Service overloaded"}]}`
arrives with an identical status and an identical shape to "Gene not
found". The first cut of this file treated every `errors` entry as
permanent and duly reported that TP53 and KRAS do not exist — two genes
that plainly do — after five back-to-back queries at 1 qps. Hence
`TRANSIENT_ERRORS`, the retry loop, and `GNOMAD_QPS` well below 1.

The asymmetry is what makes this worth the retry budget. Misreading a
permanent error as transient wastes retries and then fails loudly.
Misreading a throttle as a permanent "not found" writes a clean artifact
saying the gene has no constraint data, and that is a fabricated
negative finding: a failure to answer is never an answer of no. See
tool-design-guidance §8, "HTTP status does not classify retryability, in
either direction".

**Symbol lookup is a lookup here, not a search** — gnomAD resolves one
symbol to one gene or to nothing — but the resolved `gene_id` is
recorded in the sidecar regardless, per tool-design-guidance §8.

The thresholds are gnomAD's own published recommendations rather than
convention. That matters more than usual here: the widely-quoted LOEUF
cutoff of 0.35 is the gnomAD **v2** figure, and the current
documentation states that o/e values are higher in v4 and that "any
LOEUF thresholds used on v2 will not give an equivalent number of genes
when applied to v4". The documented v4 recommendation is 0.45. Carrying
0.35 forward would silently reclassify genes in the 0.35-0.45 range.
"""

from __future__ import annotations

import json
import time
from typing import Any

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
    EndpointUnavailable,
    Refusal,
    SchemaError,
)
from ..core.gene import resolve_gene
from ..core.paths import sanitize_slug
from ..core.qps import qps_for_host

GNOMAD_API = "https://gnomad.broadinstitute.org/api"

# gnomAD reports *everything* through the GraphQL `errors` array with an
# HTTP 200, including its own rate limiting. "Gene not found" and
# "Service overloaded" arrive in the same envelope with the same status,
# and treating them alike is how a transient throttle becomes a
# permanent "this gene does not exist" in a finding. These substrings
# mark the transient half; anything else is taken as a real answer.
TRANSIENT_ERRORS = (
    "service overloaded",
    "rate limit",
    "too many requests",
    "timed out",
    "timeout",
    "try again",
)
MAX_GNOMAD_ATTEMPTS = 5

# Field names verified by GraphQL introspection of GnomadConstraint on
# 2026-08-18. `pNull`/`pRec` do not exist on this type despite appearing
# in the constraint literature; asking for them fails the whole query.
CONSTRAINT_FIELDS = (
    "pLI oe_lof oe_lof_lower oe_lof_upper oe_lof_percentile "
    "oe_mis oe_mis_lower oe_mis_upper oe_syn "
    "exp_lof obs_lof exp_mis obs_mis exp_syn obs_syn "
    "lof_z mis_z syn_z flags"
)

QUERY = """
{
  gene(gene_symbol: "%s", reference_genome: GRCh38) {
    gene_id
    gene_version
    symbol
    name
    chrom
    start
    stop
    gnomad_constraint { %s }
  }
}
"""


def _query_gnomad(symbol: str) -> tuple[bytes, dict[str, Any]]:
    """POST the constraint query. Returns (verbatim bytes, parsed payload).

    Retries in here rather than in `http.request`, because the condition
    to retry on is inside a 200 body and the shared client only sees
    status codes.
    """
    body = json.dumps({"query": QUERY % (symbol.upper(), CONSTRAINT_FIELDS)})
    messages = ""

    for attempt in range(1, MAX_GNOMAD_ATTEMPTS + 1):
        response = http.request(
            "POST",
            GNOMAD_API,
            qps=qps_for_host("gnomad.broadinstitute.org"),
            timeout=60.0,
            headers={"Content-Type": "application/json"},
            data=body.encode("utf-8"),
        )
        raw = response.content
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise SchemaError("gnomAD did not return JSON", detail=str(exc)) from exc

        if not payload.get("errors"):
            return raw, payload

        messages = "; ".join(
            str(e.get("message", e)) for e in payload["errors"] if isinstance(e, dict)
        )
        lowered = messages.lower()
        if not any(marker in lowered for marker in TRANSIENT_ERRORS):
            # A refusal, not an endpoint failure: gnomAD answered, and
            # the answer is that it will not serve this query. Exit 9
            # says "change the input"; the exit 6 this used to raise
            # said "the endpoint is at fault", which for `Gene not
            # found` sends the caller to retry a query that can only
            # ever be declined.
            raise Refusal(
                f"gnomAD declined the query for {symbol!r}",
                detail=messages,
                remedy=(
                    "check the gene symbol; gnomAD returns HTTP 200 with an "
                    "errors key rather than a 4xx, so this is a real negative "
                    "answer and not an outage"
                ),
            )
        if attempt < MAX_GNOMAD_ATTEMPTS:
            time.sleep(2.0**attempt)

    raise EndpointUnavailable(
        f"gnomAD stayed overloaded for {symbol!r} across "
        f"{MAX_GNOMAD_ATTEMPTS} attempts",
        detail=messages,
        remedy=(
            "this is throttling, not a missing gene — retry later rather than "
            "recording the gene as having no constraint data"
        ),
    )


def _extract(payload: Any, symbol: str) -> tuple[dict[str, Any], dict[str, Any]]:
    gene = (payload or {}).get("data", {}).get("gene")
    if not isinstance(gene, dict):
        raise Refusal(
            f"gnomAD has no gene record for {symbol!r}",
            remedy="check the symbol against gnomad.broadinstitute.org",
        )
    constraint = gene.get("gnomad_constraint")
    if not isinstance(constraint, dict):
        raise Refusal(
            f"gnomAD has no constraint record for {gene.get('symbol') or symbol}",
            detail=(
                "gnomAD computes constraint only on MANE Select transcripts that "
                "pass its outlier filters; absence is a property of the gene, not "
                "a fetch failure"
            ),
            remedy="report the gene as having no published constraint, and do not "
            "substitute a related gene's value",
        )
    return gene, constraint


@click.group()
def genetics() -> None:
    """Human population genetics evidence (gnomAD, free and unauthenticated)."""


# Verb aliases — see docs/tool-design-guidance.md and issue #92.
#
#   fetch  = retrieve the record for a known identifier (gene, CID, …)
#   search = query and get back a result set
#
# Both verbs are accepted as aliases for discoverability.  The primary
# verb for this group is ``fetch`` (gnomAD resolves one symbol to one
# gene); ``search`` is the alias.


@genetics.command("fetch")
@click.argument("symbol")
@out_option
@output_options
@pass_state
def fetch_cmd(
    state: AppState, symbol: str, out: str | None, as_json: bool, quiet: bool
) -> None:
    """Fetch the gnomAD constraint record for gene SYMBOL."""
    emit = emitter(as_json, quiet)
    target_dir = state.project().artifact_dir("genomics", out)

    # ── HGNC gene symbol resolution (#147) ────────────────────────────
    gene_res = resolve_gene(symbol)
    if not gene_res.resolved:
        suggestions = ", ".join(gene_res.suggestions) if gene_res.suggestions else ""
        hint = f" Did you mean: {suggestions}?" if suggestions else ""
        provenance.relay(
            "gene.unresolved_symbol",
            f"Gene symbol {symbol!r} could not be resolved via HGNC. "
            f"No query was attempted. This is a lookup failure, not "
            f"evidence of gene absence.{hint}",
        )
        raise Refusal(
            f"could not resolve {symbol!r} to a known gene via HGNC",
            detail=f"suggestions: {suggestions}"
            if suggestions
            else "no near matches found",
            remedy="check the gene symbol or pass an Ensembl gene ID (ENSG...)",
        )
    echo = gene_res.echo_line()
    if echo:
        emit.line(echo)
    # gnomAD uses gene symbols; use canonical from HGNC when available
    query_symbol = gene_res.canonical_symbol or symbol

    try:
        raw, payload = _query_gnomad(query_symbol)
        gene, constraint = _extract(payload, query_symbol)
    except Refusal as e:
        canonical = gene_res.canonical_symbol or symbol
        provenance.relay(
            "genetics.no_data_found",
            f"Gene {canonical} resolved successfully via HGNC "
            f"({gene_res.source}) but gnomAD returned no data. "
            f"This is a data gap, not evidence that the gene has no "
            f"constraint data.",
        )
        raise Refusal(
            f"gene {canonical!r} resolved via HGNC but gnomAD has no record",
            detail=(
                f"HGNC resolved {symbol!r} to {canonical} (source: {gene_res.source})"
            ),
            remedy=(
                "gnomAD may not have constraint data for this gene. "
                "This is a data gap, not evidence that the gene has "
                "no constraint data."
            ),
        ) from e
    resolved = gene.get("symbol") or query_symbol.upper()

    sidecar = provenance.Sidecar(
        tool="genetics",
        subcommand="fetch",
        endpoint=GNOMAD_API,
        parameters={
            "query_symbol": symbol,
            "resolved_symbol": resolved,
            "resolved_gene_id": gene.get("gene_id"),
            "reference_genome": "GRCh38",
            "gene_resolution": gene_res.to_dict(),
        },
    )
    sidecar.note("source", "gnomAD (Broad Institute)")
    sidecar.note("constraint_flags", constraint.get("flags"))

    path = target_dir / f"{resolved}.gnomad-constraint.json"
    path.write_bytes(raw)
    sidecar.add_output(path)
    meta_path = sidecar.write(target_dir / f"{resolved}.gnomad-constraint.meta.json")

    emit.data("symbol", resolved)
    emit.data("gene_id", gene.get("gene_id"))
    emit.path(path, role="constraint")
    emit.path(meta_path, role="sidecar")
    emit.flush()


@genetics.command("analyze")
@click.argument("symbol")
@click.option(
    "--pli",
    "pli_override",
    type=float,
    default=None,
    help="Override lof_intolerant_pli for this invocation.",
)
@click.option(
    "--loeuf",
    "loeuf_override",
    type=float,
    default=None,
    help="Override loeuf_constrained for this invocation.",
)
@from_option
@out_option
@output_options
@pass_state
def analyze_cmd(
    state: AppState,
    symbol: str,
    pli_override: float | None,
    loeuf_override: float | None,
    from_dir: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Classify LoF tolerance from a fetched constraint record. No network."""
    emit = emitter(as_json, quiet)
    source_dir = state.project().artifact_dir("genomics", from_dir)
    target_dir = state.project().artifact_dir("genomics", out)

    resolved = sanitize_slug(symbol.upper())
    path = source_dir / f"{resolved}.gnomad-constraint.json"
    if not path.is_file():
        raise ArtifactError(
            f"no fetched constraint record for {resolved}",
            remedy=f"run `dde genetics fetch {symbol}` first",
        )
    gene, constraint = _extract(provenance.read_json(path, "gnomAD record"), resolved)

    thresholds = load_thresholds(
        state,
        "gnomad-constraint",
        {"lof_intolerant_pli": pli_override, "loeuf_constrained": loeuf_override},
    )
    pli_cut = thresholds.get("lof_intolerant_pli")
    pli_floor = thresholds.get("pli_indeterminate_floor")
    loeuf_cut = thresholds.get("loeuf_constrained")

    pli = constraint.get("pLI")
    loeuf = constraint.get("oe_lof_upper")

    warnings: list[str] = []
    relays: list[dict[str, str]] = []

    def add_relay(code: str, message: str) -> None:
        if not any(r["code"] == code for r in relays):
            relays.append(provenance.relay(code, message))

    if pli is None or loeuf is None:
        # Missing pLI/LOEUF is an expected gnomAD state (no_exp_lof flag),
        # not a schema violation.  Return indeterminate with whatever IS
        # present so the provenance chain stays intact.
        verdict = "indeterminate"
        by_pli = None
        by_loeuf = None

        add_relay(
            "gnomad.constraint_not_estimable",
            f"gnomAD constraint record for {resolved} lacks "
            f"pLI={pli!r} oe_lof_upper={loeuf!r}; loss-of-function "
            "intolerance could not be assessed",
        )
    else:
        by_pli = (
            "intolerant"
            if pli >= pli_cut
            else ("tolerant" if pli < pli_floor else "indeterminate")
        )
        by_loeuf = "intolerant" if loeuf < loeuf_cut else "tolerant"

        if by_pli == "intolerant" or by_loeuf == "intolerant":
            verdict = "lof_intolerant"
        elif by_pli == "tolerant":
            verdict = "lof_tolerant"
        else:
            verdict = "indeterminate"

        # gnomAD's own documented unreliability criterion, quoted: "Intermediate
        # pLI scores (0.1-0.9) are typically an indication that the gene was too
        # small to be confidently categorized."
        if pli_floor <= pli < pli_cut:
            add_relay(
                "gnomad.constraint_unreliable",
                f"pLI {pli:.3f} lies in the intermediate band [{pli_floor}, {pli_cut}), "
                "which gnomAD documents as an indication that the gene was too small "
                "to be confidently categorised",
            )
        if by_pli != "indeterminate" and by_pli != by_loeuf:
            add_relay(
                "gnomad.constraint_unreliable",
                f"pLI says {by_pli} ({pli:.3f} vs {pli_cut}) and LOEUF says {by_loeuf} "
                f"({loeuf:.3f} vs {loeuf_cut}); the two metrics disagree, so this gene "
                "sits at the boundary rather than in either class",
            )

    if constraint.get("flags"):
        add_relay(
            "gnomad.constraint_unreliable",
            f"gnomAD flagged this transcript: {constraint['flags']}",
        )

    # Conditional on purpose. On a LoF-tolerant gene there is no
    # toxicology inference to guard against, and a relay that fired on
    # every gene would be quoted and ignored.
    if verdict == "lof_intolerant":
        add_relay(
            "gnomad.constraint_is_not_safety",
            f"{resolved} is LoF-intolerant in human populations; this describes "
            "complete loss from conception, not partial reversible adult "
            "pharmacological inhibition, and is not a toxicology prediction",
        )

    if "loeuf_unreliable_min_expected_lof" in thresholds.unresolved():
        warnings.append(
            "no expected-LoF floor is applied: gnomAD publishes no such cutoff, "
            f"so read exp_lof={constraint.get('exp_lof')} and the 90% CI "
            f"[{constraint.get('oe_lof_lower')}, {loeuf}] directly"
        )

    assessment: dict[str, Any] = {
        "verdict": verdict,
        "symbol": resolved,
        "gene_id": gene.get("gene_id"),
        "warnings": warnings,
    }
    if by_pli is not None:
        assessment["verdict_by_pli"] = by_pli
    if by_loeuf is not None:
        assessment["verdict_by_loeuf"] = by_loeuf
    if pli is None or loeuf is None:
        assessment["reason"] = "constraint_not_estimable"

    metrics: dict[str, Any] = {
        "pLI": pli,
        "loeuf": loeuf,
        "oe_lof": constraint.get("oe_lof"),
        "oe_lof_ci90": [constraint.get("oe_lof_lower"), constraint.get("oe_lof_upper")],
        "oe_lof_percentile": constraint.get("oe_lof_percentile"),
        "oe_mis": constraint.get("oe_mis"),
        "exp_lof": constraint.get("exp_lof"),
        "obs_lof": constraint.get("obs_lof"),
        "flags": constraint.get("flags"),
    }

    analysis_path = provenance.write_analysis(
        target_dir / f"{resolved}.gnomad-constraint.analysis.json",
        source=state.project().relative(path),
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
    emit.data("metrics", metrics)
    emit.data("relays", relays)
    emit.line(f"{resolved} ({gene.get('gene_id')}) -> {verdict.upper()}")
    if pli is not None and loeuf is not None:
        emit.line(
            f"pLI {pli:.4g} (>= {pli_cut} intolerant) | LOEUF {loeuf:.4g} "
            f"(< {loeuf_cut} constrained) | obs/exp LoF "
            f"{constraint.get('obs_lof')}/{constraint.get('exp_lof'):.1f}"
        )
    else:
        emit.line(
            f"pLI={pli!r} LOEUF={loeuf!r} — constraint not estimable "
            f"(reason: no_exp_lof or insufficient data)"
        )
    for warning in warnings:
        emit.line(f"warning: {warning}")
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()


# Register ``search`` as an alias for ``fetch`` (issue #92).
genetics.add_command(fetch_cmd, "search")
