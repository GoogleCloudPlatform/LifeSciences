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

"""AlphaGenome: regulatory variant scoring and genomic track prediction.

STATUS: the Vertex request *and* response schemas are verified against
live calls made 2026-08-18. What each command writes is what the model
returned, decoded; nothing here is inferred from the pip API's docs.

Verified facts the code depends on, each of which cost a 502 to learn:

  * `organism` is required. Omitting it returns 502, not a 400.
  * `strand` is required on the Interval. STRAND_UNSPECIFIED is rejected.
  * The interval length must be exactly one of VALID_WINDOWS. Anything
    else is refused, so `--window` snaps and says that it snapped.
  * `variantScorers` must be given explicitly. The default set produces a
    response large enough to trip a gateway limit, which also surfaces
    as a 502.
  * The response is NDJSON: line 1 metadata, lines 2+ base64 zstd tensor
    chunks. `score_variant` returns float32 [1, genes, tracks] in one
    chunk; `predict_interval` returns **bfloat16** [positions, tracks]
    across several. A decoder written against only the first of those
    silently mis-reads the second, so both are handled here.
  * Roughly half of a score_variant tensor is NaN, and that is
    structural rather than an error: a gene is scored only on tracks
    matching its own strand. Treating NaN as missing data — or calling
    `max()` over the raw array — is the mistake this module exists to
    prevent.

Two backends, one interface (--backend):
  vertex — GCP auth, no API key. score_variant, predict_interval.
  pip    — the `alphagenome` package against gdmscience.googleapis.com,
           needs ALPHAGENOME_API_KEY. The only path offering ISM.

Source: candidate-tools/alphagenome-toolkit notes.md, the schema
discovery reported by the ag-workshop agent, and the live calls above.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
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
from ..core.errors import (
    CredentialError,
    DependencyError,
    EndpointUnavailable,
    SchemaError,
    UsageError,
)
from ..core.output import Emitter
from ..core.paths import sanitize_slug
from ..core.thresholds import MANDATORY_ADVISORIES

TOOL = "alphagenome-vertex"
TOOL_PIP = "alphagenome-api"
ARTIFACT_CLASS = "genomics"

AG_PROJECT = os.environ.get("DDE_AG_PROJECT", "pharma-oss-factory")
AG_REGION = os.environ.get("DDE_AG_REGION", "us-central1")
AG_ENDPOINT_ID = os.environ.get(
    "DDE_AG_ENDPOINT_ID", "mg-endpoint-5713950e-c097-4470-81e5-fc3db9c9b32a"
)
# Dedicated endpoints are not reachable through the regional Vertex host;
# they have their own DNS name, and calling the regional one returns a
# 404 that reads like the endpoint does not exist.
AG_ENDPOINT_DNS = os.environ.get(
    "DDE_AG_ENDPOINT_DNS",
    f"{AG_ENDPOINT_ID}.us-central1-826100493373.prediction.vertexai.goog",
)

OUTPUT_TYPES = [
    "ATAC",
    "CAGE",
    "DNASE",
    "RNA_SEQ",
    "CHIP_HISTONE",
    "CHIP_TF",
    "SPLICE_SITES",
    "SPLICE_SITE_USAGE",
    "SPLICE_JUNCTIONS",
    "CONTACT_MAPS",
    "PROCAP",
]

# The magnitude bands in the alphagenome-variant-effect threshold set were
# read off RNA-seq. Applying them to another assay is an extrapolation,
# and the finding has to say so — see the band_modality_mismatch relay.
BAND_NATIVE_OUTPUT = "RNA_SEQ"

SCORERS = [
    "geneMask",
    "geneMaskActive",
    "geneMaskSplicing",
    "centerMask",
    "paQtl",
    "spliceJunction",
    "contactMap",
]

# Scorers whose proto message declares no fields at all. They are sent as
# a bare `{scorer: {}}`; adding `requestedOutput` is a hard 400:
#
#   Message type "...PolyadenylationScorer" has no field named
#   "requestedOutput". Available Fields(except extensions): "[]"
#
# Each answers for a single fixed modality, so there is nothing to
# request. They ignore --output-type rather than erroring on it, and the
# sidecar records that the flag was dropped.
FIELDLESS_SCORERS = frozenset({"paQtl", "spliceJunction", "contactMap"})

# geneMaskSplicing takes a requestedOutput but only accepts splicing
# modalities. Anything else — including RNA_SEQ, which every other
# gene-mask scorer accepts — comes back 502, indistinguishable from an
# outage. Constrained here so the failure is a local error message
# instead of a ten-minute retry budget spent on a rejected request.
SCORER_OUTPUT_TYPES = {
    "geneMaskSplicing": ("SPLICE_SITES", "SPLICE_SITE_USAGE"),
}

# Output types whose response has never come back under the gateway's
# size limit, at any window tested. They 502 — the same code a real
# outage produces — so without this an agent spends its whole retry
# budget waiting for a response that cannot fit. Not a hard block: the
# limit is the gateway's, not the model's, and a narrower window may
# yet succeed. The point is that the caller knows which failure to
# expect before it happens.
OVERSIZED_OUTPUT_TYPES = frozenset({"OUTPUT_TYPE_CHIP_HISTONE", "OUTPUT_TYPE_CHIP_TF"})

# The model accepts exactly these interval lengths. A request one base
# off is rejected, so the CLI snaps rather than passing the user's number
# through to a confusing server-side error.
VALID_WINDOWS = (16384, 131072, 524288, 1048576)

ORGANISMS = {
    "HOMO_SAPIENS": "ORGANISM_HOMO_SAPIENS",
    "MUS_MUSCULUS": "ORGANISM_MUS_MUSCULUS",
}

# Track strands that carry no orientation, and so are scored for a gene
# on either strand. STRAND_UNSTRANDED is a real value, not a synonym for
# unset: RNA_SEQ returns 271 positive, 271 negative and 125 unstranded
# tracks, and a gene is scored on 396 of them — its own 271 plus all 125.
# Treating only STRAND_UNSPECIFIED as unoriented made the tool report
# 1000 unexplained missing scores (8 genes x 125) for a mask that was in
# fact fully explained.
UNORIENTED_STRANDS = frozenset({"STRAND_UNSPECIFIED", "STRAND_UNSTRANDED"})

_INTERVAL_RE = re.compile(r"^(?P<chrom>[\w.]+):(?P<start>\d+)-(?P<end>\d+)$")
_COLD_RE = re.compile(r"not yet ready|scale-up|scaling", re.IGNORECASE)


@click.group()
def alphagenome() -> None:
    """AlphaGenome regulatory variant scoring against the Vertex endpoint."""


# ---------------------------------------------------------------------------
# Request construction
# ---------------------------------------------------------------------------


def _parse_interval(text: str) -> dict[str, Any]:
    match = _INTERVAL_RE.match(text.strip())
    if not match:
        raise UsageError(
            f"could not parse interval {text!r}",
            detail="expected the form chr1:50000-150000",
        )
    start = int(match.group("start"))
    end = int(match.group("end"))
    if end <= start:
        raise UsageError(f"interval end must exceed start: {text!r}")
    return {"chromosome": match.group("chrom"), "start": start, "end": end}


def _snap_window(requested: int) -> int:
    """Round to the nearest interval length the model accepts."""
    return min(VALID_WINDOWS, key=lambda valid: (abs(valid - requested), valid))


def _centred_interval(chrom: str, pos: int, window: int, strand: str) -> dict[str, Any]:
    """An interval of exactly `window` bases centred on `pos`.

    Length is preserved when clamping at the start of the chromosome,
    because the model rejects any other length — shifting the window is
    the only legal response to running off the left edge, and the caller
    is told when that happened via the returned `centred` flag.
    """
    start = pos - window // 2
    if start < 0:
        start = 0
    return {
        "chromosome": chrom,
        "start": start,
        "end": start + window,
        "strand": strand,
        # not part of the request; stripped before sending
        "centred": start == pos - window // 2,
    }


def _organism_enum(name: str) -> str:
    key = name.strip().upper().replace("-", "_").removeprefix("ORGANISM_")
    if key not in ORGANISMS:
        raise UsageError(
            f"unknown organism {name!r}",
            detail=f"known organisms: {', '.join(sorted(ORGANISMS))}",
        )
    return ORGANISMS[key]


def _output_type_enum(name: str) -> str:
    key = name.strip().upper().replace("-", "_").removeprefix("OUTPUT_TYPE_")
    if key not in OUTPUT_TYPES:
        raise UsageError(
            f"unknown output type {name!r}",
            detail=f"known types: {', '.join(OUTPUT_TYPES)}",
        )
    return f"OUTPUT_TYPE_{key}"


def _strand_enum(name: str) -> str:
    key = name.strip().upper().removeprefix("STRAND_")
    if key in ("+", "POSITIVE", "PLUS"):
        return "STRAND_POSITIVE"
    if key in ("-", "NEGATIVE", "MINUS"):
        return "STRAND_NEGATIVE"
    raise UsageError(
        f"unknown strand {name!r}",
        detail="the Interval requires STRAND_POSITIVE or STRAND_NEGATIVE; "
        "STRAND_UNSPECIFIED is rejected by the endpoint",
    )


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def _access_token() -> tuple[str, str]:
    """An OAuth token for the endpoint, and how it was obtained.

    Application Default Credentials are preferred so a service account
    works unattended. The gcloud CLI is the fallback because that is what
    an interactive agent session actually has. Which one was used goes
    into the sidecar: the two can carry different identities, and an
    artifact that does not record which one produced it cannot be
    attributed later.
    """
    try:
        import google.auth
        import google.auth.transport.requests

        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        creds.refresh(google.auth.transport.requests.Request())
        if creds.token:
            return creds.token, "application-default-credentials"
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError as e:
        raise CredentialError(
            "no Google credentials: neither ADC nor the gcloud CLI is available",
            remedy="run `gcloud auth application-default login`, or provide a "
            "service account via GOOGLE_APPLICATION_CREDENTIALS",
        ) from e
    except subprocess.TimeoutExpired as e:
        raise CredentialError(
            "`gcloud auth print-access-token` timed out after 60s"
        ) from e

    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        raise CredentialError(
            "could not obtain a Google access token",
            detail=result.stderr.strip()[:500] or "gcloud produced no token",
            remedy="run `gcloud auth login` and confirm the active project has "
            f"access to {AG_PROJECT}",
        )
    return token, "gcloud-cli"


def _rawpredict_url() -> str:
    return (
        f"https://{AG_ENDPOINT_DNS}/v1/projects/{AG_PROJECT}"
        f"/locations/{AG_REGION}/endpoints/{AG_ENDPOINT_ID}:rawPredict"
    )


def _call(
    request_type: str,
    data: dict,
    sidecar: provenance.Sidecar,
    deadline: float,
    busy_wait: float = 20.0,
    cold_wait: float = 30.0,
) -> bytes:
    """POST one request, retrying only transient failures.

    502 is retried but is *not* assumed transient any more: since the
    scale-to-zero fix it usually means the request itself was rejected by
    the backend — a missing organism, a bad interval length, or a
    response too large for the gateway. The exhaustion message says so,
    because "retry later" sent an earlier version of this tool into a
    ten-minute wait for an error that a corrected request would have
    avoided.
    """
    from ..core import http

    token, token_source = _access_token()
    sidecar.note("credential_source", token_source)

    body = json.dumps({"instances": [{"request_type": request_type, "data": data}]})
    url = _rawpredict_url()
    limit = time.monotonic() + deadline
    attempts = 0
    last_detail = ""
    exhausted = False

    while True:
        attempts += 1
        if exhausted or time.monotonic() > limit:
            raise EndpointUnavailable(
                f"AlphaGenome {request_type} exceeded the {deadline:.0f}s deadline "
                f"after {attempts - 1} attempt(s)",
                detail=last_detail or None,
                remedy=(
                    "a persistent 502 here is usually a rejected request rather "
                    "than a busy backend: check that organism and interval strand "
                    "are set, that the interval length is one of "
                    f"{VALID_WINDOWS}, and that variantScorers is explicit and "
                    "small. Run `dde doctor` to confirm endpoint health."
                ),
            )
        try:
            response = http.request(
                "POST",
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                data=body,
                qps=0,
                timeout=min(300.0, max(30.0, limit - time.monotonic())),
                max_attempts=1,
                expect_status=200,
            )
        except EndpointUnavailable as exc:
            # http.request folds the status into the message rather than
            # onto the exception, so cold-start detection reads it back
            # out of the text. Getting this wrong costs only a shorter
            # sleep, never a wrong answer.
            last_detail = f"{exc}: {getattr(exc, 'detail', '') or ''}".strip(": ")
            if _COLD_RE.search(last_detail) or " 503" in last_detail:
                sidecar.warn("Endpoint was scaling; cold-start delay incurred.")
                wait = cold_wait
            else:
                wait = busy_wait
            if time.monotonic() + wait > limit:
                exhausted = True
                continue  # let the deadline branch raise, with the detail attached
            time.sleep(wait)
            continue

        sidecar.note("attempts", attempts)
        return response.content


# ---------------------------------------------------------------------------
# Response decoding
# ---------------------------------------------------------------------------


def _require_numpy():
    try:
        import numpy
    except ImportError as e:
        raise DependencyError(
            "numpy is not installed; AlphaGenome responses cannot be decoded",
            detail="the endpoint returns packed binary tensors, so there is no "
            "numpy-free path to a number",
            remedy="install numpy into the tools environment",
        ) from e
    return numpy


def _decompress(chunk: dict[str, Any]) -> bytes:
    payload = base64.b64decode(chunk["data"])
    compression = chunk.get("compressionType", "COMPRESSION_TYPE_UNSPECIFIED")
    if compression in ("COMPRESSION_TYPE_UNSPECIFIED", "COMPRESSION_TYPE_NONE", ""):
        return payload
    if compression != "COMPRESSION_TYPE_ZSTD":
        raise SchemaError(
            f"unsupported tensor compression {compression!r}",
            detail="only COMPRESSION_TYPE_ZSTD and uncompressed chunks are handled",
            remedy="report the compression type so the decoder can be extended — "
            "do not hand-derive a verdict from the raw artifact",
        )
    try:
        import zstandard
    except ImportError as e:
        raise DependencyError(
            "zstandard is not installed; AlphaGenome tensor chunks cannot be read",
            remedy="install zstandard into the tools environment",
        ) from e
    return zstandard.ZstdDecompressor().decompressobj().decompress(payload)


def _to_array(raw: bytes, shape: list[int], data_type: str):
    """Reinterpret packed tensor bytes, including bfloat16.

    bfloat16 is float32 with the low 16 mantissa bits dropped, so it
    widens exactly: shift each 16-bit word up into the high half of a
    32-bit word and reinterpret. numpy has no bfloat16 dtype, and reading
    those bytes as float16 instead — the obvious near-miss — produces
    numbers that are wrong without being obviously wrong.
    """
    np = _require_numpy()
    if data_type == "DATA_TYPE_FLOAT32":
        array = np.frombuffer(raw, dtype="<f4")
    elif data_type == "DATA_TYPE_BFLOAT16":
        words = np.frombuffer(raw, dtype="<u2").astype(np.uint32) << 16
        array = words.view(np.float32)
    elif data_type == "DATA_TYPE_FLOAT16":
        array = np.frombuffer(raw, dtype="<f2").astype(np.float32)
    else:
        raise SchemaError(
            f"unsupported tensor dataType {data_type!r}",
            detail="handled: DATA_TYPE_FLOAT32, DATA_TYPE_BFLOAT16, DATA_TYPE_FLOAT16",
            remedy="report the dataType so the decoder can be extended",
        )

    expected = 1
    for dim in shape:
        expected *= dim
    if array.size != expected:
        raise SchemaError(
            f"tensor payload holds {array.size} values but the declared shape "
            f"{shape} needs {expected}",
            detail="a short payload usually means a chunk was dropped; the "
            "declared chunkCount and the number of NDJSON chunk lines disagree",
            remedy="re-run the phase-1 command; do not analyse a partial tensor",
        )
    return array.reshape(shape)


def _decode_response(raw: bytes) -> list[tuple[dict[str, Any], Any]]:
    """Split an NDJSON response into (header, tensor) blocks.

    One request can carry several scorers, and the response then
    interleaves: header, its chunks, next header, its chunks. Decoding
    only the first header and treating every remaining line as its chunks
    concatenates two different tensors into one buffer — which is why the
    declared-vs-seen chunk count below is checked per block rather than
    per response.
    """
    lines = [line for line in raw.split(b"\n") if line.strip()]
    if not lines:
        raise SchemaError("AlphaGenome returned an empty response body")

    records: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SchemaError(
                f"NDJSON line {index} is not valid JSON",
                detail=f"{exc}; first 300 bytes: {line[:300]!r}",
            ) from exc

    if "output" not in records[0]:
        raise SchemaError(
            "AlphaGenome response does not begin with an 'output' header",
            detail=f"first record keys: {sorted(records[0])}",
            remedy="report the actual response shape rather than deriving a "
            "verdict from it by hand",
        )

    blocks: list[tuple[dict[str, Any], Any]] = []
    header: dict[str, Any] | None = None
    chunks: list[dict[str, Any]] = []

    def flush() -> None:
        if header is None:
            return
        output = header["output"]
        if not isinstance(output, dict):
            raise SchemaError("response 'output' is not an object")
        container = None
        for key in ("variantData", "trackData"):
            if isinstance(output.get(key), dict):
                container = output[key]
                break
        if container is None:
            raise SchemaError(
                "response block carries neither variantData nor trackData",
                detail=f"output keys: {sorted(output)}",
            )
        values = container.get("values") or {}
        declared = int(values.get("chunkCount", 1))
        if len(chunks) != declared:
            raise SchemaError(
                f"response block declared {declared} tensor chunk(s) but "
                f"carried {len(chunks)}",
                detail="a truncated stream reshapes into a plausible-looking "
                "array of the wrong values, so this is refused rather than padded",
                remedy="re-run the phase-1 command",
            )
        payload = b"".join(_decompress(chunk) for chunk in chunks)
        shape = [int(dim) for dim in values.get("shape", [])]
        blocks.append((output, _to_array(payload, shape, values.get("dataType", ""))))

    for record in records:
        if "output" in record:
            flush()
            header, chunks = record, []
        elif isinstance(record.get("tensorChunk"), dict):
            chunks.append(record["tensorChunk"])
    flush()

    if not blocks:
        raise SchemaError("AlphaGenome response carried no decodable tensor blocks")
    return blocks


def _split_score_dims(array) -> tuple[Any, Any | None]:
    """Separate raw scores from quantile scores.

    The leading dimension is 1 or 2. When it is 2, index 1 holds a
    *signed* quantile in [-1, 1] whose sign tracks the raw score's — not
    a [0, 1] percentile. Verified on RNA_SEQ for chr17:7675088 C>T: sign
    agreement with the raw score is 100% across all eight genes and
    Spearman rho between the two runs 0.94-0.997.

    Which of the two you get depends on the **output type**, not on the
    scorer: geneMask returns [1, genes, tracks] for CAGE, ATAC, DNASE and
    PROCAP but [2, genes, tracks] for RNA_SEQ. So quantile availability
    has to be read off each response rather than predicted from the
    request.
    """
    if array.ndim != 3:
        raise SchemaError(
            f"expected a 3-dimensional variant tensor, got shape {list(array.shape)}"
        )
    leading = array.shape[0]
    if leading == 1:
        return array[0], None
    if leading == 2:
        return array[0], array[1]
    raise SchemaError(
        f"variant tensor has {leading} leading planes; only 1 (raw) and 2 "
        "(raw + quantile) are understood",
        remedy="report the shape so the decoder can be extended — do not "
        "assume plane 0 is the raw score",
    )


# ---------------------------------------------------------------------------
# Strand masking
# ---------------------------------------------------------------------------


def _strand_summary(
    matrix, quantiles, genes: list[dict], tracks: list[dict]
) -> dict[str, Any]:
    """Per-gene statistics over the tracks a gene was actually scored on.

    Half of a score_variant tensor is NaN by design: a gene is scored
    only on tracks that match its own strand. The finite mask is taken as
    ground truth — it is what the model returned — and the strand rule is
    then checked *against* it. If the two disagree the discrepancy is
    reported rather than reconciled, because a NaN the tool cannot
    explain is a missing score, and averaging over 546 tracks when 273
    hold values understates every magnitude by half.
    """
    np = _require_numpy()
    matrix = np.asarray(matrix)  # [genes, tracks]
    finite = np.isfinite(matrix)

    track_strands = [t.get("strand", "STRAND_UNSPECIFIED") for t in tracks]
    rows = []
    # Two opposite disagreements with the same-strand hypothesis, kept
    # apart because only one of them threatens a finding. A cell that is
    # scored where the rule predicted NaN just means the rule is too
    # narrow for this scorer — paQtl scores every track regardless of
    # strand — and costs nothing, because every count below is taken
    # from the finite mask rather than from the prediction. A cell that
    # is *missing* where the rule predicted a score is the dangerous
    # one: something dropped out for a reason the tool cannot name.
    missing_unexplained = 0
    scored_unexpectedly = 0

    for index, gene in enumerate(genes):
        gene_strand = gene.get("strand", "STRAND_UNSPECIFIED")
        expected = np.array(
            [
                strand == gene_strand or strand in UNORIENTED_STRANDS
                for strand in track_strands
            ]
        )
        observed = finite[index]
        missing_unexplained += int(np.count_nonzero(expected & ~observed))
        scored_unexpectedly += int(np.count_nonzero(~expected & observed))

        values = matrix[index][observed]
        quants = (
            np.asarray(quantiles)[index][observed] if quantiles is not None else None
        )
        scored_positions = np.flatnonzero(observed)

        if values.size:
            order = np.argsort(-np.abs(values))[:5]
            top = []
            for j in order:
                track = tracks[int(scored_positions[j])]
                entry = {
                    "track": track.get("name"),
                    "biosample": (track.get("biosample") or {}).get("name"),
                    "strand": track.get("strand"),
                    "raw_score": float(values[j]),
                }
                if quants is not None:
                    entry["quantile_score"] = float(quants[j])
                top.append(entry)
        else:
            top = []

        row = {
            "gene_id": gene.get("geneId"),
            "gene_name": gene.get("name"),
            "gene_type": gene.get("type"),
            "strand": gene_strand,
            "n_tracks_total": len(tracks),
            "n_tracks_scored": int(values.size),
            "max_abs_raw_score": float(np.max(np.abs(values))) if values.size else None,
            "mean_raw_score": float(np.mean(values)) if values.size else None,
            "top_tracks": top,
        }
        if quants is not None and quants.size:
            row["max_abs_quantile_score"] = float(np.max(np.abs(quants)))
            # The raw score at the most extreme quantile, not the largest
            # raw score. The guide's artifact rule is about that pairing:
            # an extreme quantile sitting on a negligible raw value.
            peak = int(np.argmax(np.abs(quants)))
            row["raw_at_max_abs_quantile"] = float(values[peak])
        rows.append(row)

    return {
        "genes": rows,
        "unexplained_missing_cells": missing_unexplained,
        "unexpectedly_scored_cells": scored_unexpectedly,
        "n_scored_total": int(np.count_nonzero(finite)),
        "n_cells_total": int(finite.size),
    }


def _require_api_key() -> str:
    key = os.environ.get("ALPHAGENOME_API_KEY")
    if not key:
        raise CredentialError(
            "ALPHAGENOME_API_KEY is not set; the pip backend is unavailable",
            detail="checked the ALPHAGENOME_API_KEY environment variable",
            remedy=(
                "provision an AlphaGenome API key into the agent environment, "
                "or use --backend vertex for the operations it supports"
            ),
        )
    return key


# ---------------------------------------------------------------------------
# Phase 1 — score a variant
# ---------------------------------------------------------------------------


@alphagenome.command("score-variant")
@click.option("--chrom", required=True, help="Chromosome, e.g. chr17.")
@click.option("--pos", required=True, type=int, help="1-based variant position.")
@click.option("--ref", required=True, help="Reference base(s).")
@click.option("--alt", required=True, help="Alternate base(s).")
@click.option(
    "--window",
    type=int,
    default=131072,
    help=f"Context width in bp, centred on the variant. Snapped to one of {VALID_WINDOWS}.",
)
@click.option(
    "--output-type",
    "output_types",
    multiple=True,
    default=("CAGE",),
    help=f"Modality to score. Repeatable. One of: {', '.join(OUTPUT_TYPES)}",
)
@click.option(
    "--scorer",
    type=click.Choice(SCORERS),
    default="geneMask",
    help="VariantScorer variant to apply to each output type.",
)
@click.option(
    "--strand", default="STRAND_POSITIVE", help="Interval strand (required by the API)."
)
@click.option("--organism", default="HOMO_SAPIENS", help="Organism enum.")
@click.option(
    "--backend",
    type=click.Choice(["vertex", "pip"]),
    default="vertex",
    help="vertex (GCP auth) or pip (needs ALPHAGENOME_API_KEY).",
)
@click.option("--deadline", type=float, default=600.0, help="Total time cap, seconds.")
@out_option
@output_options
@pass_state
def score_variant(
    state: AppState,
    chrom: str,
    pos: int,
    ref: str,
    alt: str,
    window: int,
    output_types: tuple[str, ...],
    scorer: str,
    strand: str,
    organism: str,
    backend: str,
    deadline: float,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Score one variant's regulatory effect. Writes decoded raw scores.

    Emits the response verbatim as `.response.ndjson` alongside the
    decoded `.scores.json`, so the decoding can be re-checked without
    another call to a model that is not guaranteed to be deterministic.
    """
    if backend == "pip":
        _require_api_key()
        raise DependencyError(
            "the pip backend for score-variant is not implemented yet",
            remedy="use --backend vertex",
        )

    project = state.project()
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)

    strand_enum = _strand_enum(strand)
    organism_enum = _organism_enum(organism)
    requested = [_output_type_enum(t) for t in output_types] or ["OUTPUT_TYPE_CAGE"]

    dropped_output_types: list[str] = []
    if scorer in FIELDLESS_SCORERS:
        # Only an *explicit* --output-type is worth warning about. The
        # default is the CLI's own, and telling the user their unstated
        # preference was ignored is noise that trains them to skim
        # warnings — which is how the load-bearing ones get missed.
        source = click.get_current_context().get_parameter_source("output_types")
        if source is not None and source.name != "DEFAULT":
            dropped_output_types = list(requested)
        requested = []
    elif scorer in SCORER_OUTPUT_TYPES:
        allowed = {_output_type_enum(t) for t in SCORER_OUTPUT_TYPES[scorer]}
        rejected = [t for t in requested if t not in allowed]
        if rejected:
            raise UsageError(
                f"{scorer} does not accept {', '.join(rejected)}",
                detail=(
                    "the endpoint answers an unsupported pairing with 502, which "
                    "is indistinguishable from an outage, so this is caught here"
                ),
                remedy=(
                    "use --output-type "
                    f"{' or '.join(SCORER_OUTPUT_TYPES[scorer])}, or pick "
                    "another scorer"
                ),
            )

    snapped = _snap_window(window)
    interval = _centred_interval(chrom, pos, snapped, strand_enum)
    centred = interval.pop("centred")

    stem = sanitize_slug(f"{chrom}-{pos}-{ref}-{alt}")
    request_data: dict[str, Any] = {
        "variant": {
            "chromosome": chrom,
            "position": pos,
            "referenceBases": ref,
            "alternateBases": alt,
        },
        "interval": interval,
        "organism": organism_enum,
        "variantScorers": (
            [{scorer: {}}]
            if scorer in FIELDLESS_SCORERS
            else [{scorer: {"requestedOutput": rt}} for rt in requested]
        ),
    }

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="score-variant",
        endpoint=_rawpredict_url(),
        parameters={
            "variant": f"{chrom}:{pos}{ref}>{alt}",
            "window_requested": window,
            "window_used": snapped,
            "interval": f"{chrom}:{interval['start']}-{interval['end']}",
            "interval_strand": strand_enum,
            "organism": organism_enum,
            "scorer": scorer,
            "output_types": requested,
            "backend": backend,
        },
    )
    oversized = [rt for rt in requested if rt in OVERSIZED_OUTPUT_TYPES]
    if oversized:
        # To stderr as well as the sidecar, and before the call rather
        # than after it. A sidecar warning about a wait is written when
        # the wait is already over; the caller needs this while there is
        # still a decision to make.
        click.echo(
            f"warning: {', '.join(rt.removeprefix('OUTPUT_TYPE_') for rt in oversized)} "
            "has never returned successfully — expect a 502 after the retry "
            "budget, and consider --deadline to shorten the wait.",
            err=True,
        )
        sidecar.warn(
            f"{', '.join(rt.removeprefix('OUTPUT_TYPE_') for rt in oversized)} has "
            "not returned successfully at any window tested; the response exceeds "
            "a gateway size limit and fails as a 502, which is the same code an "
            "outage produces. Treat a 502 here as the expected rejection, not as "
            "an endpoint fault."
        )
    if dropped_output_types:
        sidecar.warn(
            f"--output-type {', '.join(dropped_output_types)} was ignored: "
            f"{scorer} declares no requestable output and answers for one fixed "
            "modality. The result is not the modality that was asked for."
        )
    if snapped != window:
        sidecar.warn(
            f"Requested window {window} bp is not a legal interval length; used "
            f"{snapped} bp. The model accepts only {VALID_WINDOWS}."
        )
    if not centred:
        sidecar.warn(
            f"The {snapped} bp window would start before position 0, so it was "
            f"shifted to {interval['start']}-{interval['end']} and the variant is "
            "not at its centre."
        )
    if len(requested) > 2:
        sidecar.warn(
            f"{len(requested)} output types requested in one call. Large responses "
            "have been observed to fail at the gateway as a 502; if this call "
            "fails, split it."
        )
    sidecar.note(
        "interval_strand_note",
        "The Interval requires a strand and rejects STRAND_UNSPECIFIED. Its "
        "effect on the returned scores has not been characterised; gene-level "
        "strand handling is driven by geneMetadata, not by this field.",
    )

    raw = _call("score_variant", request_data, sidecar, deadline)

    response_path = target_dir / f"{stem}.response.ndjson"
    response_path.write_bytes(raw)
    sidecar.add_output(response_path)

    blocks = _decode_response(raw)
    # Fieldless scorers request no output types at all, so a count
    # mismatch against `requested` is expected rather than suspicious.
    if requested and len(blocks) != len(requested):
        sidecar.warn(
            f"requested {len(requested)} scorer(s) but the response carried "
            f"{len(blocks)} result block(s); blocks are labelled by their own "
            "reported output type, not by request order."
        )

    decoded: list[dict[str, Any]] = []
    variant_meta = None
    without_quantiles: list[str] = []

    for position, (output, array) in enumerate(blocks):
        container = output.get("variantData") or {}
        metadata = container.get("metadata") or {}
        genes = metadata.get("geneMetadata") or []
        tracks = metadata.get("trackMetadata") or []
        variant_meta = variant_meta or metadata.get("variant")

        matrix, quantiles = _split_score_dims(array)

        # contactMap scores the interval, not the genes in it: it returns
        # trackMetadata but no geneMetadata, and a single row. Rather than
        # invent a gene name for that row — which would put a fabricated
        # label on a real number — the row is named for the interval and
        # marked as not gene-resolved, so nothing downstream can attribute
        # a contact-map score to a gene.
        interval_level = not genes and matrix.shape[0] == 1
        if interval_level:
            genes = [
                {
                    "geneId": None,
                    "name": f"{chrom}:{interval['start']}-{interval['end']}",
                    "strand": "STRAND_UNSPECIFIED",
                    "interval_level": True,
                }
            ]

        if matrix.shape != (len(genes), len(tracks)):
            raise SchemaError(
                f"tensor plane {list(matrix.shape)} does not match "
                f"{len(genes)} gene(s) x {len(tracks)} track(s)",
                remedy="re-run; do not map scores onto names by position when "
                "the counts disagree",
            )

        summary = _strand_summary(matrix, quantiles, genes, tracks)
        if interval_level:
            sidecar.warn(
                f"{scorer} returned no gene metadata, so its score describes the "
                f"whole {chrom}:{interval['start']}-{interval['end']} interval. "
                "It cannot be attributed to any gene in that interval."
            )
        # The response does not label a variantData block with its output
        # type, so it is taken from the request by position. When those
        # counts disagree the warning above has already fired.
        if position < len(requested):
            label = requested[position]
        elif scorer in FIELDLESS_SCORERS:
            # These name no output type in either direction: not in the
            # request, because the field does not exist, and not in the
            # response. The scorer is the only honest label available,
            # and it is at least the thing that determines the modality.
            label = f"SCORER_{scorer}"
        else:
            label = "OUTPUT_TYPE_UNKNOWN"
        short = label.removeprefix("OUTPUT_TYPE_")
        if quantiles is None:
            without_quantiles.append(short)

        if summary["unexplained_missing_cells"]:
            sidecar.warn(
                f"{summary['unexplained_missing_cells']} of "
                f"{summary['n_cells_total']} {short} gene-track cells are "
                "unscored where the same-strand rule predicts a score, so some "
                "missing scores have no explanation.",
                code="alphagenome.unexplained_missing_scores",
            )
        if summary["unexpectedly_scored_cells"]:
            # Not a relay. The counts downstream come from the finite
            # mask, so a too-narrow strand rule cannot bias them; this
            # records that the rule does not describe this scorer.
            sidecar.warn(
                f"{summary['unexpectedly_scored_cells']} of "
                f"{summary['n_cells_total']} {short} gene-track cells are scored "
                "on the opposite strand, so the same-strand rule does not apply "
                f"to {scorer}. Counts are taken from the returned values, not "
                "from the rule, and are unaffected."
            )

        decoded.append(
            {
                "output_type": label,
                "scorer": scorer,
                "tensor_shape": list(array.shape),
                "quantile_scores_available": quantiles is not None,
                "genes": summary["genes"],
                "unexplained_missing_cells": summary["unexplained_missing_cells"],
                "unexpectedly_scored_cells": summary["unexpectedly_scored_cells"],
                "n_scored_cells": summary["n_scored_total"],
                "n_cells": summary["n_cells_total"],
                "track_metadata": tracks,
            }
        )

    non_native = [rt for rt in requested if rt != f"OUTPUT_TYPE_{BAND_NATIVE_OUTPUT}"]
    if non_native:
        sidecar.warn(
            "Magnitude bands are RNA-seq-derived; scores here come from "
            + ", ".join(rt.removeprefix("OUTPUT_TYPE_") for rt in non_native)
            + ".",
            code="alphagenome.band_modality_mismatch",
        )
    if without_quantiles:
        sidecar.warn(
            "No quantile scores were returned for "
            + ", ".join(without_quantiles)
            + "; the guide's significance rule cannot be applied to those.",
            code="alphagenome.no_quantile_scores",
        )
    sidecar.note("blocks", [b["output_type"] for b in decoded])
    sidecar.note("tensor_shapes", [b["tensor_shape"] for b in decoded])

    scores_path = target_dir / f"{stem}.scores.json"
    scores_path.write_text(
        json.dumps(
            {
                "variant": variant_meta,
                "interval": interval,
                "organism": organism_enum,
                "scorer": scorer,
                "output_types": requested,
                "blocks": decoded,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    sidecar.add_output(scores_path)

    request_path = target_dir / f"{stem}.request.json"
    request_path.write_text(json.dumps(request_data, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(request_path)

    meta = sidecar.write(target_dir / f"{stem}.meta.json")

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("variant", f"{chrom}:{pos}{ref}>{alt}")
    emit.data("blocks", [b["output_type"] for b in decoded])
    emit.data("warnings", sidecar.warnings)
    emit.path(project.relative(scores_path), "scores")
    emit.path(project.relative(response_path), "response")
    emit.path(project.relative(request_path), "request")
    emit.path(project.relative(meta), "sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# Phase 1 — predict tracks across an interval
# ---------------------------------------------------------------------------


@alphagenome.command("predict-interval")
@click.option("--interval", required=True, help="e.g. chr17:7609896-7740968.")
@click.option("--strand", default="STRAND_POSITIVE", help="Interval strand (required).")
@click.option("--organism", default="HOMO_SAPIENS")
@click.option(
    "--output-type",
    "output_types",
    multiple=True,
    default=("CAGE",),
    help=f"Requested output types. One of: {', '.join(OUTPUT_TYPES)}",
)
@click.option("--deadline", type=float, default=900.0)
@out_option
@output_options
@pass_state
def predict_interval(
    state: AppState,
    interval: str,
    strand: str,
    organism: str,
    output_types: tuple[str, ...],
    deadline: float,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Predict genomic tracks across an interval (no variant).

    The tensor is [positions x tracks] and runs to millions of values, so
    it is stored as .npz rather than JSON. Track metadata goes to a
    sibling JSON file, which is what a reader should open first.
    """
    project = state.project()
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)
    parsed = _parse_interval(interval)

    length = parsed["end"] - parsed["start"]
    if length not in VALID_WINDOWS:
        raise UsageError(
            f"interval {interval} is {length} bp; the model accepts only "
            f"{', '.join(str(w) for w in VALID_WINDOWS)}",
            detail="the endpoint rejects any other length",
            remedy=f"widen or narrow the interval to {_snap_window(length)} bp",
        )

    strand_enum = _strand_enum(strand)
    organism_enum = _organism_enum(organism)
    requested = [_output_type_enum(t) for t in output_types] or ["OUTPUT_TYPE_CAGE"]

    stem = f"{parsed['chromosome']}-{parsed['start']}-{parsed['end']}"
    request_data: dict[str, Any] = {
        "interval": {**parsed, "strand": strand_enum},
        "organism": organism_enum,
        "requestedOutputs": requested,
    }

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="predict-interval",
        endpoint=_rawpredict_url(),
        parameters={
            "interval": interval,
            "interval_length": length,
            "interval_strand": strand_enum,
            "organism": organism_enum,
            "requested_outputs": requested,
        },
    )

    raw = _call("predict_interval", request_data, sidecar, deadline)

    response_path = target_dir / f"{stem}.tracks.response.ndjson"
    response_path.write_bytes(raw)
    sidecar.add_output(response_path)

    blocks = _decode_response(raw)
    if len(blocks) != 1:
        raise SchemaError(
            f"predict-interval returned {len(blocks)} result blocks; this "
            "command stores one",
            remedy="request a single --output-type at a time",
        )
    output, array = blocks[0]
    container = output.get("trackData") or {}
    tracks = container.get("metadata") or []

    np = _require_numpy()
    npz_path = target_dir / f"{stem}.tracks.npz"
    np.savez_compressed(
        npz_path,
        values=array,
        track_names=np.array([t.get("name", "") for t in tracks], dtype=object),
    )
    sidecar.add_output(npz_path)

    sidecar.note("tensor_shape", list(array.shape))
    sidecar.note("output_type", output.get("outputType"))

    index_path = target_dir / f"{stem}.tracks.json"
    index_path.write_text(
        json.dumps(
            {
                "interval": {**parsed, "strand": strand_enum},
                "organism": organism_enum,
                "output_type": output.get("outputType"),
                "tensor_shape": list(array.shape),
                "values_file": npz_path.name,
                "track_metadata": tracks,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    sidecar.add_output(index_path)

    request_path = target_dir / f"{stem}.tracks.request.json"
    request_path.write_text(json.dumps(request_data, indent=2) + "\n", encoding="utf-8")
    sidecar.add_output(request_path)

    meta = sidecar.write(target_dir / f"{stem}.tracks.meta.json")

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("interval", interval)
    emit.data("shape", list(array.shape))
    emit.path(project.relative(index_path), "tracks")
    emit.path(project.relative(npz_path), "values")
    emit.path(project.relative(response_path), "response")
    emit.path(project.relative(meta), "sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# Phase 1 — ISM (no Vertex path)
# ---------------------------------------------------------------------------


@alphagenome.command()
@click.option(
    "--interval", required=True, help="ISM scan window, e.g. chr1:99900-100100."
)
@click.option("--organism", default="HOMO_SAPIENS")
@out_option
@output_options
@pass_state
def ism(
    state: AppState,
    interval: str,
    organism: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """In-silico mutagenesis scan for motif discovery. PIP BACKEND ONLY.

    The Vertex endpoint rejects `score_ism_variants` as an invalid
    request type, so this operation has no Vertex path. It requires
    ALPHAGENOME_API_KEY and the `alphagenome` package.
    """
    _parse_interval(interval)
    _require_api_key()
    try:
        import alphagenome  # noqa: F401
    except ImportError as e:
        raise DependencyError(
            "the `alphagenome` package is not installed; ISM is unavailable",
            detail="ISM has no Vertex endpoint equivalent — the endpoint rejects "
            "score_ism_variants as an invalid request_type",
            remedy="install alphagenome>=0.6.1 into the tools environment",
        ) from e
    raise DependencyError(
        "ISM support is not implemented yet",
        detail="the pip backend is unexercised: no ALPHAGENOME_API_KEY has been "
        "available to validate it against",
        remedy="provision an API key, then this command can be completed and tested",
    )


# ---------------------------------------------------------------------------
# Phase 2 — analysis
# ---------------------------------------------------------------------------


@alphagenome.command()
@click.argument("artifact")
@click.option(
    "--raw-score-moderate", type=float, default=None, help="Override threshold."
)
@click.option(
    "--quantile-significance", type=float, default=None, help="Override threshold."
)
@out_option
@output_options
@pass_state
def analyze(
    state: AppState,
    artifact: str,
    raw_score_moderate: float | None,
    quantile_significance: float | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Apply variant-effect thresholds to a stored score artifact.

    ARTIFACT is the `.scores.json` written by `score-variant`.

    Whether a significance call is possible depends on the output type,
    not on the scorer: RNA_SEQ comes back with a signed quantile plane
    and CAGE, ATAC, DNASE and PROCAP do not. So the verdict is qualified
    per block. Where quantiles exist the guide's artifact rule applies —
    an extreme quantile on a negligible raw score is a ranking against a
    flat background, not a molecular effect — and where they do not, the
    verdict says it is a magnitude reading rather than borrowing a word
    the guide reserves for a test that was never run.
    """
    project = state.project()
    path = resolve_artifact(state, artifact, "AlphaGenome scores")
    payload = provenance.read_json(path, "AlphaGenome scores")

    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise SchemaError(
            f"{path.name} is not an AlphaGenome score artifact",
            detail="expected a 'blocks' array as written by `dde alphagenome "
            f"score-variant`; top-level keys are {sorted(payload)[:12]}",
            remedy="pass the .scores.json produced by score-variant; artifacts "
            "written before the multi-block change carry 'genes' at the top "
            "level and must be regenerated",
        )

    thresholds = load_thresholds(
        state,
        "alphagenome-variant-effect",
        {
            "raw_score_moderate": raw_score_moderate,
            "quantile_significance": quantile_significance,
        },
    )
    no_effect = thresholds.get("raw_score_no_effect")
    subtle = thresholds.get("raw_score_subtle")
    moderate = thresholds.get("raw_score_moderate")
    quant_cut = thresholds.get("quantile_significance")
    artifact_ceiling = thresholds.get("artifact_raw_ceiling")

    def band(value: float) -> str:
        magnitude = abs(value)
        if magnitude < no_effect:
            return "no_effect"
        if magnitude < subtle:
            return "subtle"
        if magnitude < moderate:
            return "moderate"
        return "strong"

    order = ["no_effect", "subtle", "moderate", "strong"]
    advisories: list[str] = list(MANDATORY_ADVISORIES["alphagenome-variant-effect"])
    relays: list[dict[str, str]] = []

    block_results: list[dict[str, Any]] = []
    without_quantiles: list[str] = []
    total_artifacts = 0

    for block in blocks:
        label = str(block.get("output_type", "OUTPUT_TYPE_UNKNOWN")).removeprefix(
            "OUTPUT_TYPE_"
        )
        has_quantiles = bool(block.get("quantile_scores_available"))
        if not has_quantiles:
            without_quantiles.append(label)

        genes = block.get("genes") or []
        scored = [g for g in genes if g.get("max_abs_raw_score") is not None]
        ranked = sorted(scored, key=lambda g: abs(g["max_abs_raw_score"]), reverse=True)

        hits = []
        n_artifact = 0
        for gene in ranked:
            entry = {
                "gene_name": gene.get("gene_name"),
                "gene_id": gene.get("gene_id"),
                "strand": gene.get("strand"),
                "max_abs_raw_score": gene["max_abs_raw_score"],
                "mean_raw_score": gene.get("mean_raw_score"),
                "n_tracks_scored": gene.get("n_tracks_scored"),
                "n_tracks_total": gene.get("n_tracks_total"),
                "magnitude": band(gene["max_abs_raw_score"]),
                "top_track": (gene.get("top_tracks") or [{}])[0].get("track"),
            }
            if has_quantiles:
                peak_q = gene.get("max_abs_quantile_score")
                raw_at_peak = gene.get("raw_at_max_abs_quantile")
                entry["max_abs_quantile_score"] = peak_q
                entry["raw_at_max_abs_quantile"] = raw_at_peak
                # The guide's rule, verbatim: an extreme quantile paired
                # with a negligible raw score is a ranking against a flat
                # background, not a molecular effect.
                is_artifact = (
                    peak_q is not None
                    and raw_at_peak is not None
                    and abs(peak_q) > quant_cut
                    and abs(raw_at_peak) < artifact_ceiling
                )
                entry["statistical_artifact"] = bool(is_artifact)
                if is_artifact:
                    n_artifact += 1
            hits.append(entry)

        total_artifacts += n_artifact
        top = max((h["magnitude"] for h in hits), key=order.index, default=None)
        if top is None:
            block_verdict = "no_scores"
        elif top in ("no_effect", "subtle"):
            block_verdict = f"{top}_by_magnitude"
        elif has_quantiles:
            block_verdict = f"{top}_magnitude_confirmed"
        else:
            block_verdict = f"{top}_magnitude_unconfirmed"

        block_results.append(
            {
                "output_type": label,
                "verdict": block_verdict,
                "significance_test_applied": "quantile" if has_quantiles else "none",
                "quantile_scores_available": has_quantiles,
                "n_genes": len(genes),
                "n_genes_scored": len(scored),
                "n_statistical_artifacts": n_artifact,
                "genes": hits[:25],
            }
        )

        if n_artifact:
            advisories.insert(
                0,
                f"{label}: {n_artifact} gene(s) reach |quantile| above the "
                "significance threshold while their raw score at that quantile "
                "stays below the artifact ceiling. That pairing is the "
                "documented low-expression artifact, not a molecular effect.",
            )
            relays.append(
                provenance.relay(
                    "alphagenome.quantile_artifact",
                    f"{n_artifact} {label} gene(s) are high-quantile/low-raw "
                    "artifacts and must not be reported as effects.",
                )
            )

    if without_quantiles:
        # Only strip the quantile advisory when nothing in this artifact
        # has quantiles. With a mixed request the caution is live for the
        # blocks that do.
        if len(without_quantiles) == len(blocks):
            advisories = [a for a in advisories if "uantile" not in a]
        advisories.insert(
            0,
            "NO QUANTILE SCORES for "
            + ", ".join(without_quantiles)
            + ". The guide's significance test and its high-quantile/low-raw "
            "artifact rule were not applied to those blocks, because the "
            "response does not carry the input they need. Their verdicts "
            "reflect raw magnitude only and are not significance calls.",
        )
        relays.append(
            provenance.relay(
                "alphagenome.no_quantile_scores",
                "Verdicts for "
                + ", ".join(without_quantiles)
                + " rest on raw magnitude alone; no quantile test was performed.",
            )
        )

    meta_path = path.with_name(path.name.replace(".scores.json", ".meta.json"))
    if meta_path.is_file():
        fetch_meta = provenance.read_json(meta_path, "provenance sidecar")
        for item in fetch_meta.get("mandatory_relays", []) or []:
            if not any(r["code"] == item.get("code") for r in relays):
                relays.append(item)

    # The context window is not a performance knob. Measured on
    # chr17:7675088 C>T with a geneMask CAGE scorer, widening it from
    # 16384 to 524288 bp took the reported gene set from 1 gene to 54 and
    # moved TP53's own max |raw| from 0.02500 to 0.01209 by way of
    # 0.00760. Both the denominator and the number are properties of the
    # request, so a finding that names neither is not reproducible.
    interval = payload.get("interval") or {}
    window = None
    if interval.get("end") is not None and interval.get("start") is not None:
        window = int(interval["end"]) - int(interval["start"])
        advisories.insert(
            0,
            f"Gene set and scores are bounded by the {window} bp context window "
            f"({interval.get('chromosome')}:{interval.get('start')}-"
            f"{interval.get('end')}). A different window returns a different "
            "set of genes and different scores for the same variant; report the "
            "window alongside any gene list.",
        )

    scored_cells = sum(b.get("n_scored_cells") or 0 for b in blocks)
    total_cells = sum(b.get("n_cells") or 0 for b in blocks)
    unexplained = sum(b.get("unexplained_missing_cells") or 0 for b in blocks)
    if scored_cells and total_cells and scored_cells < total_cells:
        advisories.insert(
            0,
            f"{scored_cells} of {total_cells} gene-track cells carry a score; the "
            "remainder are unscored because a gene is scored only on tracks of "
            "its own strand. Quote the per-gene scored count, not the track total.",
        )

    # The overall verdict is the strongest block verdict, so a mixed
    # request cannot be summarised as confirmed on the strength of a
    # block that had no quantiles to confirm anything with.
    def rank(result: dict[str, Any]) -> tuple[int, int]:
        name = result["verdict"]
        base = next((i for i, o in enumerate(order) if name.startswith(o)), -1)
        return (base, 1 if "confirmed" in name and "unconfirmed" not in name else 0)

    strongest = max(block_results, key=rank)
    verdict = strongest["verdict"]

    # Claim-triggered relay for the confident, large-effect verdict.
    # A complete quantile set with a confirmed strong or moderate effect
    # used to produce zero relays — the most quotable result went out
    # with the least guidance. The wrong reading: a large predicted
    # regulatory effect stands in for pathogenicity or causality.
    #
    # Conditional on purpose: _magnitude_confirmed is the verdict that
    # invites the over-read. _unconfirmed already carries its own
    # uncertainty caveat via the no_quantile_scores relay, and no_scores
    # has nothing to over-read.
    if verdict.endswith("_magnitude_confirmed"):
        top_gene = (
            strongest["genes"][0]["gene_name"] if strongest["genes"] else "unknown"
        )
        relays.append(
            provenance.relay(
                "alphagenome.effect_is_not_pathogenicity",
                f"Confirmed {verdict.split('_magnitude')[0]} effect "
                f"(top gene {top_gene} in {strongest['output_type']}) "
                "is a predicted regulatory effect size, not a clinical "
                "interpretation and not evidence of causality for a phenotype.",
            )
        )

    metrics = {
        "n_blocks": len(block_results),
        "n_genes": sum(b["n_genes"] for b in block_results),
        "n_genes_scored": sum(b["n_genes_scored"] for b in block_results),
        "n_statistical_artifacts": total_artifacts,
        "max_abs_raw_score": max(
            (
                g["max_abs_raw_score"]
                for b in block_results
                for g in b["genes"]
                if g.get("max_abs_raw_score") is not None
            ),
            default=None,
        ),
        "top_gene": strongest["genes"][0]["gene_name"] if strongest["genes"] else None,
        "quantile_blocks": [
            b["output_type"] for b in block_results if b["quantile_scores_available"]
        ],
        "context_window_bp": window,
        "unexplained_missing_cells": unexplained,
        "scored_cells": scored_cells,
        "total_cells": total_cells,
    }
    assessment = {
        "verdict": verdict,
        "verdict_from": strongest["output_type"],
        "blocks": block_results,
        "advisories": advisories,
    }

    analysis_path = beside_or_out(
        state, path, path.name.replace(".scores.json", ".analysis.json"), out
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
    emit.line(f"{path.name}  [threshold_set {thresholds.tag}]")
    emit.line(f"Verdict: {verdict} (from {strongest['output_type']})")
    for result in block_results:
        emit.line(
            f"{result['output_type']}: {result['verdict']}, "
            f"{result['n_genes_scored']}/{result['n_genes']} genes scored, "
            f"significance {result['significance_test_applied']}"
            + (
                f", {result['n_statistical_artifacts']} artifact(s)"
                if result["n_statistical_artifacts"]
                else ""
            )
        )
        for hit in result["genes"][:2]:
            flag = " [ARTIFACT]" if hit.get("statistical_artifact") else ""
            emit.line(
                f"  {hit['gene_name']}: max |raw| {hit['max_abs_raw_score']:.4f} "
                f"over {hit['n_tracks_scored']} scored track(s) — "
                f"{hit['magnitude']}{flag}"
            )
    if without_quantiles:
        emit.line(
            "! No quantile scores for "
            + ", ".join(without_quantiles)
            + ": magnitude reading, not a significance call."
        )
    if total_artifacts:
        emit.line(
            f"! {total_artifacts} high-quantile/low-raw artifact(s): NOT molecular effects."
        )
    emit.path(project.relative(analysis_path), "analysis")
    emit.flush()


@alphagenome.command("analyze-ism")
@click.argument("artifact")
@out_option
@output_options
@pass_state
def analyze_ism(
    state: AppState, artifact: str, out: str | None, as_json: bool, quiet: bool
) -> None:
    """Identify motifs in a stored ISM scan.

    Refuses to run: the motif-calling cutoffs in the `alphagenome-ism`
    threshold set have no cited source. Requesting them raises rather
    than substituting a plausible number.

    `--out` is accepted and currently unused, because the command writes
    nothing. It is here so that the phase-2 contract holds by shape
    rather than by exemption: the day these cutoffs acquire a source,
    this becomes a command that writes a verdict, and the reviewer's
    ability to regenerate it elsewhere must not depend on whoever
    resolves the thresholds also remembering the flag. `dde doctor`
    checks the shape and would have to carry an exception for this
    command otherwise — an exception nobody would revisit.
    """
    thresholds = load_thresholds(state, "alphagenome-ism")
    unresolved = thresholds.unresolved()
    if unresolved:
        from ..core.errors import ThresholdError

        raise ThresholdError(
            "ISM analysis cannot produce a verdict: "
            f"{', '.join(unresolved)} have no defensible default",
            detail=f"threshold set {thresholds.tag}; provenance: {thresholds.provenance}",
            remedy=(
                "establish these cutoffs from a cited source and set them in "
                "<project>/.dde/thresholds.yaml under 'alphagenome-ism'"
            ),
        )
