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

"""
FoldRun Structure Viewer
Cloud Run web application for viewing AlphaFold2, AlphaFold 3, OpenFold3, and Boltz-2 predictions
"""

import json
import logging
import os
import re
import urllib.parse
from datetime import UTC, datetime, timedelta

import google.auth
import google.auth.exceptions
import google.auth.transport.requests
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from google.auth.compute_engine import credentials as compute_engine_credentials
from google.cloud import storage

from foldrun_common import (
    JOB_ID_PATTERN,
    MAX_DECOMPRESSED_ZST_BYTES,
    AnalysisMetadata,
    build_analysis_task_config,
    discover_af2_predictions_in_gcs,
    discover_af3_predictions,
    discover_boltz2_predictions,
    discover_of3_predictions,
    ensure_run_archive_in_gcs,
    resolve_boltz2_query_yaml_uri,
    validate_gcs_uri,
)
from foldrun_common import (
    SUPPORTED_MODEL_TYPES as _SUPPORTED_MODEL_TYPES,
)
from foldrun_common import (
    build_expert_analysis_markdown as _common_build_expert_analysis_markdown,
)
from foldrun_common import (
    cleanup_stale_analysis_blobs as _common_cleanup_stale_analysis_blobs,
)
from foldrun_common import (
    decompress_zst_bytes as _common_decompress_zst_bytes,
)
from foldrun_common import (
    infer_model_type_from_display_name as _infer_model_type_from_display_name,
)
from foldrun_common import (
    normalize_model_type as _normalize_model_type,
)
from foldrun_common import (
    sanitize_filename as _common_sanitize_filename,
)
from foldrun_common import (
    sanitize_json_floats as _sanitize_data,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration
PROJECT_ID = os.environ["PROJECT_ID"]
BUCKET_NAME = os.environ["BUCKET_NAME"]
REGION = os.environ.get("REGION", "us-central1")

# Initialize GCS client. When running locally via Docker with user ADC
# (GOOGLE_APPLICATION_CREDENTIALS set), we must specify quota_project_id so
# GCS requests aren't rejected for missing billing project. On Cloud Run the
# service account already has implicit quota project — setting it explicitly
# causes a serviceusage.services.use permission error.
_quota_project = (
    PROJECT_ID if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") else None
)
_creds, _ = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"],
    quota_project_id=_quota_project,
)
storage_client = storage.Client(project=PROJECT_ID, credentials=_creds)


ALLOWED_GCS_PREFIXES = ("pipeline_runs/", "af3_predictions/")
SIGNED_URL_EXPIRATION_SECONDS = int(
    os.environ.get("SIGNED_URL_EXPIRATION_SECONDS", "3600")
)
_NONFINITE_TOKEN_RE = re.compile(r"^[+-]?(?:nan|inf|infinity)$", re.IGNORECASE)


def parse_gcs_uri(uri, allowed_extensions=None):
    """Parse and validate gs://bucket/path URI into bucket and path components."""
    return validate_gcs_uri(
        uri,
        expected_bucket=BUCKET_NAME,
        allowed_prefixes=ALLOWED_GCS_PREFIXES,
        allowed_extensions=allowed_extensions,
    )


_sanitize_json_floats = _sanitize_data


def _decompress_zst_bytes(
    raw_bytes: bytes, max_bytes: int = MAX_DECOMPRESSED_ZST_BYTES
) -> bytes:
    """Decompress zstandard-compressed bytes with a strict size limit."""
    return _common_decompress_zst_bytes(raw_bytes, max_bytes=max_bytes)


def _sanitize_structure_bfactors(content: str, fmt: str | None = None) -> str:
    """Replace non-finite B-factor tokens in ATOM/HETATM records with 0.00 for 3Dmol.js.

    Never fabricates high-confidence pLDDT values (such as 80.0/82.0) and avoids
    touching chemical component IDs (e.g., sialic acid 'NAN').
    """
    out_lines: list[str] = []
    in_atom_site_loop = False
    atom_site_cols: list[str] = []
    b_col_idx: int | None = None

    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        if fmt != "pdb":
            if stripped == "loop_":
                in_atom_site_loop = False
                atom_site_cols = []
                b_col_idx = None
                out_lines.append(line)
                continue

            if stripped.startswith("_atom_site."):
                in_atom_site_loop = True
                atom_site_cols.append(stripped)
                if stripped == "_atom_site.B_iso_or_equiv":
                    b_col_idx = len(atom_site_cols) - 1
                out_lines.append(line)
                continue

            if (
                in_atom_site_loop
                and stripped
                and not stripped.startswith(("ATOM ", "HETATM ", "ATOM\t", "HETATM\t"))
            ):
                if stripped.startswith(("_", "#")):
                    in_atom_site_loop = False
                    atom_site_cols = []
                    b_col_idx = None
                out_lines.append(line)
                continue

        if stripped.startswith(("ATOM ", "HETATM ", "ATOM\t", "HETATM\t")):
            line_ending = (
                "\r\n"
                if line.endswith("\r\n")
                else ("\n" if line.endswith("\n") else "")
            )
            body = line[: len(line) - len(line_ending)] if line_ending else line

            # Headered mmCIF _atom_site loop with known B_iso_or_equiv column
            if fmt != "pdb" and in_atom_site_loop and b_col_idx is not None:
                tokens = body.split()
                if b_col_idx < len(tokens) and _NONFINITE_TOKEN_RE.match(
                    tokens[b_col_idx]
                ):
                    tokens[b_col_idx] = "0.00"
                    out_lines.append(" ".join(tokens) + line_ending)
                    continue
                out_lines.append(line)
                continue

            tokens = body.split()
            is_pdb_line = fmt == "pdb" or (
                fmt is None
                and not in_atom_site_loop
                and body[:6] in ("ATOM  ", "HETATM")
                and "." not in tokens[4:6]
                and "?" not in tokens[4:6]
            )
            # Fixed-width PDB ATOM/HETATM record (columns 61-66 are tempFactor)
            if is_pdb_line:
                if len(body) > 60:
                    bfac_slice = body[60:66].strip()
                    if bfac_slice and _NONFINITE_TOKEN_RE.match(bfac_slice):
                        out_lines.append(
                            body[:60] + f"{0.0:6.2f}" + body[66:] + line_ending
                        )
                        continue
                out_lines.append(line)
                continue

            # Headerless mmCIF record: B-factor is always at token index >= 10 (after x, y, z, occupancy)
            changed = False
            for idx in range(10, len(tokens)):
                tok = tokens[idx]
                if tok != "NAN" and _NONFINITE_TOKEN_RE.match(tok):
                    tokens[idx] = "0.00"
                    changed = True
                elif tok == "NAN" and idx in (10, 14):
                    tokens[idx] = "0.00"
                    changed = True
            if changed:
                out_lines.append(" ".join(tokens) + line_ending)
                continue

        out_lines.append(line)

    return "".join(out_lines)


def load_gcs_file(uri, as_json=False, allowed_extensions=None):
    """Load a file from GCS (transparently decompressing .zst objects)."""
    try:
        bucket_name, blob_path = parse_gcs_uri(
            uri, allowed_extensions=allowed_extensions
        )
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)

        content = blob.download_as_bytes()
        if blob_path.lower().endswith(".zst"):
            content = _decompress_zst_bytes(content)

        if as_json:
            parsed = json.loads(
                content.decode("utf-8"),
                parse_constant=lambda _: None,
            )
            return _sanitize_data(parsed)

        return content.decode("utf-8")

    except Exception as e:
        logger.error(f"Error loading {uri}: {e}")
        raise


def get_pdb_content(pdb_uri):
    """Get PDB file content from GCS"""
    content = load_gcs_file(pdb_uri, allowed_extensions=(".pdb", ".pdb.zst"))
    return _sanitize_structure_bfactors(content, fmt="pdb")


def _resolve_summary_and_uri(job_id=None, summary_uri=None):
    """Resolve and load analysis summary JSON together with its canonical gs:// URI."""
    if summary_uri:
        parse_gcs_uri(summary_uri, allowed_extensions=(".json", ".json.zst"))
        return (
            load_gcs_file(
                summary_uri, as_json=True, allowed_extensions=(".json", ".json.zst")
            ),
            summary_uri,
        )

    if not job_id:
        raise ValueError("Either job_id or summary_uri must be provided")

    if not JOB_ID_PATTERN.fullmatch(str(job_id)):
        raise ValueError(f"Invalid job_id format: {job_id}")

    bucket = storage_client.bucket(BUCKET_NAME)

    # 1. Check AlphaFold 3 predictions directory first
    af3_summary_path = f"af3_predictions/{job_id}/analysis/summary.json"
    if bucket.blob(af3_summary_path).exists():
        uri = f"gs://{BUCKET_NAME}/{af3_summary_path}"
        return load_gcs_file(uri, as_json=True), uri

    # 2. Exact lookup via Vertex AI PipelineJob runtimeConfig.gcsOutputDirectory
    try:
        safe_job_id = urllib.parse.quote(str(job_id), safe="")
        authed = _get_authed_session()
        pj_url = (
            f"https://{REGION}-aiplatform.googleapis.com/v1"
            f"/projects/{PROJECT_ID}/locations/{REGION}/pipelineJobs/{safe_job_id}"
        )
        pj_resp = authed.get(pj_url, timeout=10)
        if pj_resp.ok:
            pj = pj_resp.json()
            gcs_output_dir = (
                pj.get("runtimeConfig", {}).get("gcsOutputDirectory", "").rstrip("/")
            )
            if gcs_output_dir.startswith(f"gs://{BUCKET_NAME}/"):
                exact_uri = f"{gcs_output_dir}/analysis/summary.json"
                _, exact_blob_path = parse_gcs_uri(exact_uri)
                if not bucket.blob(exact_blob_path).exists():
                    raise FileNotFoundError(
                        f"Analysis summary not ready yet for pipeline job {job_id} at {exact_uri}"
                    )
                return load_gcs_file(exact_uri, as_json=True), exact_uri
    except FileNotFoundError:
        raise
    except Exception as e:
        logger.debug(
            f"Direct PipelineJob lookup for {job_id} fell back to GCS scan: {e}"
        )

    # 3. Fallback: extract date portion if present, or scan pipeline_runs/
    match = re.search(r"(\d{8})\d{6}$", job_id)
    date_prefix = match.group(1) if match else ""
    prefix = f"pipeline_runs/{date_prefix}" if date_prefix else "pipeline_runs/"

    summary_blobs = list(
        bucket.list_blobs(prefix=prefix, match_glob="**/analysis/summary.json")
    )

    broad_scan = False
    if not summary_blobs and date_prefix:
        summary_blobs = list(
            bucket.list_blobs(
                prefix="pipeline_runs/", match_glob="**/analysis/summary.json"
            )
        )
        broad_scan = True

    if not summary_blobs:
        all_blobs = list(bucket.list_blobs(prefix=prefix))
        summary_blobs = [
            b for b in all_blobs if b.name.endswith("/analysis/summary.json")
        ]
        if summary_blobs:
            broad_scan = False

    if not summary_blobs and date_prefix:
        all_blobs = list(bucket.list_blobs(prefix="pipeline_runs/"))
        summary_blobs = [
            b for b in all_blobs if b.name.endswith("/analysis/summary.json")
        ]
        broad_scan = True

    if not summary_blobs:
        raise FileNotFoundError(
            f"No analysis summary found for job {job_id}. "
            f"Searched gs://{BUCKET_NAME}/{af3_summary_path} and gs://{BUCKET_NAME}/pipeline_runs/**/analysis/summary.json"
        )

    if len(summary_blobs) == 1 and date_prefix and not broad_scan:
        uri = f"gs://{BUCKET_NAME}/{summary_blobs[0].name}"
        return load_gcs_file(uri, as_json=True), uri

    # Check if any summary.json matches job_id in its blob path
    for blob in summary_blobs:
        if job_id in blob.name:
            uri = f"gs://{BUCKET_NAME}/{blob.name}"
            return load_gcs_file(uri, as_json=True), uri

    # Check if any summary.json matches job_id in its JSON payload
    for blob in summary_blobs:
        try:
            uri = f"gs://{BUCKET_NAME}/{blob.name}"
            data = load_gcs_file(uri, as_json=True)
            if isinstance(data, dict) and data.get("job_id") == job_id:
                return data, uri
        except Exception:
            continue

    full_ts = re.search(r"(\d{14})$", job_id)
    if full_ts:
        ts = full_ts.group(1)  # YYYYMMDDHHMMSS
        target = f"{ts[:8]}_{ts[8:]}"  # YYYYMMDD_HHMMSS
        for blob in summary_blobs:
            if target in blob.name:
                uri = f"gs://{BUCKET_NAME}/{blob.name}"
                return load_gcs_file(uri, as_json=True), uri
        for offset in [-1, 1, -2, 2]:
            adjusted = str(int(ts) + offset)
            adjusted_dir = f"{adjusted[:8]}_{adjusted[8:]}"
            for blob in summary_blobs:
                if adjusted_dir in blob.name:
                    uri = f"gs://{BUCKET_NAME}/{blob.name}"
                    return load_gcs_file(uri, as_json=True), uri

    if not date_prefix or broad_scan:
        raise FileNotFoundError(
            f"No analysis summary matching custom job_id '{job_id}' found under pipeline_runs/"
        )

    summary_blobs.sort(key=lambda b: b.name, reverse=True)
    uri = f"gs://{BUCKET_NAME}/{summary_blobs[0].name}"
    return load_gcs_file(uri, as_json=True), uri


def get_analysis_summary(job_id=None, summary_uri=None):
    """Get enhanced analysis summary from GCS.

    When given a job_id, first checks af3_predictions/ and Vertex AI PipelineJob
    runtimeConfig.gcsOutputDirectory, then falls back to scanning pipeline_runs/.
    """
    summary, _ = _resolve_summary_and_uri(job_id=job_id, summary_uri=summary_uri)
    return summary


def _get_authed_session():
    """Return an AuthorizedSession using application default credentials."""
    quota_project = (
        PROJECT_ID if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") else None
    )
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
        quota_project_id=quota_project,
    )
    return google.auth.transport.requests.AuthorizedSession(creds)


def _scan_analysis_state():
    """Scan GCS for analysis state files in a single listing pass.

    Returns (complete, running):
        complete: set of run directory keys (e.g. '20260504224020' or custom slug) where summary.json exists
        running:  set of run directory keys where analysis_metadata.json or task_config.json exists but summary.json does not
    """
    complete = set()
    has_metadata = set()
    try:
        bucket = storage_client.bucket(BUCKET_NAME)
        blobs = bucket.list_blobs(prefix="pipeline_runs/")
        for blob in blobs:
            name = blob.name
            if "/analysis/" not in name:
                continue
            parts = name.split("/")
            if len(parts) < 3 or parts[0] != "pipeline_runs":
                continue
            run_dir = parts[1]
            m = re.match(r"^(\d{8})_(\d{6})$", run_dir)
            key = (m.group(1) + m.group(2)) if m else run_dir
            if name.endswith("/analysis/summary.json"):
                complete.add(key)
            elif name.endswith("/analysis/analysis_metadata.json") or name.endswith(
                "/analysis/task_config.json"
            ):
                has_metadata.add(key)
    except Exception as e:
        logger.warning(f"Could not scan analysis state: {e}")
    running = has_metadata - complete
    return complete, running


def _discover_af2_predictions(pipeline_root: str) -> list:
    """Scan GCS for AF2 raw_prediction.pkl files under pipeline_root."""
    return discover_af2_predictions_in_gcs(
        pipeline_root,
        storage_client,
        expected_bucket=BUCKET_NAME,
        allowed_prefixes=ALLOWED_GCS_PREFIXES,
    )


def _discover_of3_predictions(pipeline_root: str) -> list:
    """Scan GCS for OF3 *_confidences_aggregated.json files under pipeline_root."""
    return discover_of3_predictions(
        pipeline_root,
        storage_client,
        expected_bucket=BUCKET_NAME,
        allowed_prefixes=ALLOWED_GCS_PREFIXES,
    )


def _discover_boltz2_predictions_and_affinity(
    pipeline_root: str,
) -> tuple[list, str | None]:
    """Scan GCS for Boltz-2 confidence_*.json and optional affinity_*.json files."""
    return discover_boltz2_predictions(
        pipeline_root,
        storage_client,
        expected_bucket=BUCKET_NAME,
        allowed_prefixes=ALLOWED_GCS_PREFIXES,
    )


def _discover_boltz2_predictions(pipeline_root: str) -> list:
    """Scan GCS for Boltz-2 confidence_*.json files under pipeline_root."""
    predictions, _ = _discover_boltz2_predictions_and_affinity(pipeline_root)
    return predictions


def _resolve_boltz2_query_yaml_uri(pj: dict) -> str | None:
    """Resolve and validate the Boltz-2 input YAML URI within BUCKET_NAME."""
    return resolve_boltz2_query_yaml_uri(
        bucket_name=BUCKET_NAME,
        parameter_values=(pj.get("runtimeConfig") or {}).get("parameterValues"),
        labels=pj.get("labels"),
        display_name=pj.get("displayName"),
    )


def _cleanup_stale_analysis_blobs(bucket, analysis_prefix: str) -> int:
    """Delete stale per-prediction and consolidated blobs prior to re-analysis."""
    return _common_cleanup_stale_analysis_blobs(bucket, analysis_prefix)


def _validate_job_id(job_id: str) -> str:
    """Validate job_id against strict allowlist pattern or abort with 400."""
    if not isinstance(job_id, str) or not JOB_ID_PATTERN.fullmatch(job_id):
        abort(400, description="Invalid job_id format")
    return job_id


def _redirect_to_combined_viewer(job_id: str):
    """Return a validated local redirect to the combined viewer for a safe job_id."""
    safe_job_id = _validate_job_id(job_id)
    target_url = url_for("combined_viewer", job_id=safe_job_id)
    parsed_url = urllib.parse.urlparse(target_url)
    if parsed_url.scheme or parsed_url.netloc or not target_url.startswith("/"):
        abort(400, description="Invalid redirect target")
    return redirect(target_url)


@app.route("/")
def index():
    """Landing page - redirects to combined viewer if job_id provided"""
    job_id = request.args.get("job_id")

    if job_id:
        return _redirect_to_combined_viewer(job_id)

    return render_template("index.html")


@app.route("/job/<job_id>")
def job_viewer(job_id):
    """Short URL for viewing a job"""
    return _redirect_to_combined_viewer(job_id)


@app.route("/combined")
def combined_viewer():
    """Combined structure + analysis viewer"""
    job_id = request.args.get("job_id")
    if job_id:
        job_id = _validate_job_id(job_id)
    pdb_uri = request.args.get("pdb_uri")
    summary_uri = request.args.get("summary_uri")
    model_name = request.args.get("model", "Best Model")

    # pdb_uri is optional if job_id or summary_uri is provided
    # (the viewer will load predictions from summary.json)
    if not pdb_uri and not job_id and not summary_uri:
        abort(
            400,
            description="Either pdb_uri, job_id, or summary_uri parameter is required",
        )

    # Derive GCS console link from summary_uri, pdb_uri, or af3_ job_id
    gcs_console_url = None
    gcs_ref = summary_uri or pdb_uri
    if gcs_ref and gcs_ref.startswith("gs://"):
        # Strip gs:// and go up to the pipeline root (parent of analysis/ or predict/)
        gcs_path = gcs_ref[5:]  # bucket/path/to/analysis/summary.json
        # Remove trailing filename segments to get the pipeline run directory
        for suffix in ("/analysis/", "/predict/"):
            idx = gcs_path.find(suffix)
            if idx != -1:
                gcs_path = gcs_path[: idx + 1]
                break
        gcs_console_url = f"https://console.cloud.google.com/storage/browser/{gcs_path}?project={PROJECT_ID}"
    elif job_id and job_id.startswith("af3_"):
        gcs_console_url = (
            f"https://console.cloud.google.com/storage/browser/"
            f"{BUCKET_NAME}/af3_predictions/{job_id}?project={PROJECT_ID}"
        )

    return render_template(
        "combined.html",
        job_id=job_id,
        pdb_uri=pdb_uri,
        summary_uri=summary_uri,
        model_name=model_name,
        project_id=PROJECT_ID,
        region=REGION,
        bucket_name=BUCKET_NAME,
        gcs_console_url=gcs_console_url,
    )


@app.route("/api/pdb")
def get_pdb():
    """API endpoint to fetch PDB content"""
    pdb_uri = request.args.get("uri")

    if not pdb_uri:
        return jsonify({"error": "uri parameter is required"}), 400

    try:
        content = get_pdb_content(pdb_uri)
        return content, 200, {"Content-Type": "text/plain"}
    except ValueError as e:
        logger.warning(f"Invalid PDB URI: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error fetching PDB: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/cif")
def get_cif():
    """API endpoint to fetch CIF content (supports .cif and .cif.zst)"""
    cif_uri = request.args.get("uri")

    if not cif_uri:
        return jsonify({"error": "uri parameter is required"}), 400

    try:
        content = load_gcs_file(cif_uri, allowed_extensions=(".cif", ".cif.zst"))
        content = _sanitize_structure_bfactors(content, fmt="cif")
        return content, 200, {"Content-Type": "text/plain"}
    except ValueError as e:
        logger.warning(f"Invalid CIF URI: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error fetching CIF: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/analysis")
def get_analysis():
    """API endpoint to fetch analysis summary"""
    job_id = request.args.get("job_id")
    summary_uri = request.args.get("summary_uri")

    if not job_id and not summary_uri:
        return jsonify({"error": "Either job_id or summary_uri is required"}), 400

    try:
        summary = get_analysis_summary(job_id=job_id, summary_uri=summary_uri)
        return jsonify(summary)
    except ValueError as e:
        logger.warning(f"Invalid analysis request: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error fetching analysis: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/image")
def get_image():
    """API endpoint to fetch image from GCS"""
    uri = request.args.get("uri")

    if not uri:
        return jsonify({"error": "uri parameter is required"}), 400

    try:
        bucket_name, blob_path = parse_gcs_uri(
            uri, allowed_extensions=(".png", ".jpg", ".jpeg")
        )
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)

        # Download image content
        image_bytes = blob.download_as_bytes()

        # Determine content type based on file extension
        content_type = "image/png"
        if blob_path.endswith(".jpg") or blob_path.endswith(".jpeg"):
            content_type = "image/jpeg"

        return image_bytes, 200, {"Content-Type": content_type}
    except ValueError as e:
        logger.warning(f"Invalid image URI: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error fetching image from {uri}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/jobs")
def list_jobs():
    """List recent FoldRun pipeline jobs from Agent Platform, sorted by recency.

    Supports pagination via ?page_token=<token>. Returns 20 jobs per page plus
    a next_page_token if more results exist.
    """
    try:
        authed = _get_authed_session()
        page_token = request.args.get("page_token", "")

        url = (
            f"https://{REGION}-aiplatform.googleapis.com/v1"
            f"/projects/{PROJECT_ID}/locations/{REGION}/pipelineJobs"
        )
        params = {
            "filter": "labels.submitted_by=foldrun-agent",
            "orderBy": "createTime desc",
            "pageSize": "20",
        }
        if page_token:
            params["pageToken"] = page_token

        resp = authed.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        raw_pipeline_jobs = list(data.get("pipelineJobs", []))
        seen_job_ids = {
            pj.get("name", "").split("/")[-1]
            for pj in raw_pipeline_jobs
            if pj.get("name")
        }

        # Best-effort secondary query for legacy / AF3 jobs labeled managed_by=foldrun
        if not page_token:
            try:
                legacy_params = {
                    "filter": "labels.managed_by=foldrun",
                    "orderBy": "createTime desc",
                    "pageSize": "20",
                }
                legacy_resp = authed.get(url, params=legacy_params, timeout=10)
                if legacy_resp.ok:
                    legacy_data = legacy_resp.json()
                    for lpj in legacy_data.get("pipelineJobs", []):
                        lid = lpj.get("name", "").split("/")[-1]
                        if lid and lid not in seen_job_ids:
                            seen_job_ids.add(lid)
                            raw_pipeline_jobs.append(lpj)
                    raw_pipeline_jobs.sort(
                        key=lambda item: item.get("createTime", ""), reverse=True
                    )
            except Exception as legacy_err:
                logger.debug(
                    f"Secondary managed_by=foldrun query skipped: {legacy_err}"
                )

        jobs = []
        for pj in raw_pipeline_jobs:
            labels = pj.get("labels", {})
            resource_name = pj.get("name", "")
            job_id = resource_name.split("/")[-1]
            if (
                labels.get("job_type") == "af3_idle_drain"
                or labels.get("model_type") == "af3_system"
                or job_id.startswith("alphafold3-idle-drain-")
            ):
                continue
            # Extract exact GCS timestamp or custom slug from gcsOutputDirectory (e.g.
            # "gs://bucket/pipeline_runs/20260504_224020" → "20260504224020").
            gcs_output_dir = pj.get("runtimeConfig", {}).get("gcsOutputDirectory", "")
            gcs_ts = None
            m = re.search(r"pipeline_runs/(\d{8})_(\d{6})", gcs_output_dir)
            if m:
                gcs_ts = m.group(1) + m.group(2)
            else:
                m_dir = re.search(r"pipeline_runs/([^/]+)", gcs_output_dir)
                if m_dir:
                    gcs_ts = m_dir.group(1)
            disp_name = pj.get("displayName", job_id)
            raw_mt = labels.get("model_type") or _infer_model_type_from_display_name(
                disp_name
            )
            norm_mt = _normalize_model_type(raw_mt) or "alphafold2"
            pipeline_url = (
                f"https://console.cloud.google.com/vertex-ai/pipelines/locations/"
                f"{REGION}/runs/{job_id}?project={PROJECT_ID}"
            )
            job_entry = {
                "job_id": job_id,
                "display_name": disp_name,
                "model_type": norm_mt,
                "state": pj.get("state", "PIPELINE_STATE_UNSPECIFIED"),
                "create_time": pj.get("createTime", ""),
                "has_analysis": False,
                "analysis_running": False,
                "pipeline_url": pipeline_url,
                "_gcs_ts": gcs_ts,
            }
            if job_entry["model_type"] == "alphafold3":
                q_name = labels.get("query_name") or disp_name
                job_entry["logs_url"] = (
                    f"https://console.cloud.google.com/logs/query;query="
                    f"resource.type%3D%22aiplatform.googleapis.com%2FEndpoint%22%20%22{q_name}%22"
                    f";duration=P30D?project={PROJECT_ID}"
                )
            jobs.append(job_entry)

        # Single GCS scan to determine analysis state per job
        complete_ts, running_ts = _scan_analysis_state()
        for job in jobs:
            ts = job.pop("_gcs_ts", None)
            if ts:
                job["has_analysis"] = ts in complete_ts
                job["analysis_running"] = ts in running_ts and not job["has_analysis"]

        # Include AlphaFold 3 Endpoint prediction jobs stored under af3_predictions/
        if not page_token:
            try:
                bucket = storage_client.bucket(BUCKET_NAME)
                af3_blobs = list(bucket.list_blobs(prefix="af3_predictions/"))
                af3_by_job: dict[str, dict] = {}
                for b in af3_blobs:
                    parts = b.name.split("/")
                    if len(parts) < 3:
                        continue
                    jid = parts[1]
                    if (
                        not jid
                        or jid.startswith(".")
                        or not JOB_ID_PATTERN.fullmatch(jid)
                    ):
                        continue
                    entry = af3_by_job.setdefault(
                        jid,
                        {
                            "job_id": jid,
                            "display_name": jid,
                            "model_type": "alphafold3",
                            "state": "PIPELINE_STATE_SUCCEEDED",
                            "create_time": "",
                            "has_analysis": False,
                            "analysis_running": False,
                            "logs_url": (
                                f"https://console.cloud.google.com/logs/query;query="
                                f"resource.type%3D%22aiplatform.googleapis.com%2FEndpoint%22%20%22{jid}%22"
                                f";duration=P30D?project={PROJECT_ID}"
                            ),
                        },
                    )
                    if b.time_created:
                        iso_ts = b.time_created.isoformat().replace("+00:00", "Z")
                        if not entry["create_time"] or iso_ts > entry["create_time"]:
                            entry["create_time"] = iso_ts
                    if b.name.endswith("/analysis/summary.json"):
                        entry["has_analysis"] = True
                        entry["analysis_running"] = False
                        if "msa_free" in jid:
                            entry["display_name"] = f"{jid} (Zero-MSA / --msa-free)"
                        elif "full_msa" in jid:
                            entry["display_name"] = (
                                f"{jid} (Full 630 GB MSA + Templates)"
                            )
                    elif (
                        b.name.endswith("/analysis/analysis_metadata.json")
                        and not entry["has_analysis"]
                    ):
                        entry["analysis_running"] = True
                succeeded_kfp_names = set()
                for j in jobs:
                    if (
                        j.get("model_type") == "alphafold3"
                        and j.get("state") == "PIPELINE_STATE_SUCCEEDED"
                    ):
                        dn = j.get("display_name", "")
                        succeeded_kfp_names.add(j["job_id"])
                        if dn:
                            succeeded_kfp_names.add(dn)
                        if dn in af3_by_job:
                            if af3_by_job[dn].get("has_analysis"):
                                j["has_analysis"] = True
                                j["analysis_running"] = False
                            elif af3_by_job[dn].get("analysis_running"):
                                j["analysis_running"] = True
                for jid, af3_job in af3_by_job.items():
                    if jid not in succeeded_kfp_names and (
                        af3_job["has_analysis"] or af3_job["analysis_running"]
                    ):
                        jobs.append(af3_job)
                jobs.sort(key=lambda x: str(x.get("create_time", "")), reverse=True)
            except Exception as af3_exc:
                logger.warning(
                    f"Could not scan af3_predictions/ for job list: {af3_exc}"
                )

        return jsonify(
            {
                "jobs": jobs,
                "next_page_token": data.get("nextPageToken", ""),
            }
        )

    except google.auth.exceptions.DefaultCredentialsError:
        logger.warning("No credentials available for /api/jobs")
        return jsonify(
            {
                "jobs": [],
                "error": "No credentials found. Ensure GOOGLE_APPLICATION_CREDENTIALS is set.",
            }
        )
    except Exception as e:
        logger.error(f"Error listing jobs: {e}")
        return jsonify({"jobs": [], "error": str(e)}), 500


@app.route("/api/quality/<job_id>")
def job_quality(job_id):
    """Return quality assessment for a single analyzed job (lazy-loaded by the UI).

    Reads quality_metrics from the job's analysis/summary.json. Returns 404 if
    no summary exists yet. Called per-row after the job list renders so it never
    blocks the initial page load.
    """
    try:
        summary = get_analysis_summary(job_id=job_id)
        qm = summary.get("summary", {}).get("quality_metrics", {})
        qa = qm.get("quality_assessment", "") or summary.get("best_prediction", {}).get(
            "quality_assessment", ""
        )
        if not isinstance(qa, str):
            qa = ""
        return jsonify(
            {
                "quality_assessment": qa,
                "best_model_plddt": qm.get("best_model_plddt")
                or qm.get("best_plddt")
                or summary.get("best_prediction", {}).get("plddt_mean"),
                "best_model_pae": qm.get("best_model_pae"),
            }
        )
    except Exception:
        return jsonify({"quality_assessment": ""}), 404


def _discover_af3_predictions(job_prefix: str) -> list:
    """Scan GCS for AF3 seed-*_sample-* outputs (.cif/.cif.zst and .json/.json.zst)."""
    return discover_af3_predictions(
        job_prefix,
        storage_client,
        default_bucket=BUCKET_NAME,
        expected_bucket=BUCKET_NAME,
        allowed_prefixes=ALLOWED_GCS_PREFIXES,
    )


@app.route("/api/analyze", methods=["POST"])
def trigger_analysis():
    """Trigger analysis for a succeeded prediction job that has no analysis yet.

    Discovers prediction outputs in GCS, writes task_config.json, and kicks off
    the appropriate Cloud Run analysis job.
    """
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    explicit_model_type = data.get("model_type")

    if not job_id:
        return jsonify({"error": "job_id is required"}), 400

    if not JOB_ID_PATTERN.fullmatch(str(job_id)):
        return jsonify({"error": "Invalid job_id format"}), 400

    if explicit_model_type is not None and str(explicit_model_type).strip() != "":
        normalized_explicit = _normalize_model_type(explicit_model_type)
        if normalized_explicit not in _SUPPORTED_MODEL_TYPES:
            return jsonify(
                {
                    "error": (
                        f"Unsupported model_type '{explicit_model_type}'. "
                        f"Must be one of {sorted(_SUPPORTED_MODEL_TYPES)}"
                    )
                }
            ), 400

    safe_job_id = urllib.parse.quote(str(job_id), safe="")

    try:
        authed = _get_authed_session()

        # Fetch the full pipeline job to get gcsOutputDirectory
        pj_url = (
            f"https://{REGION}-aiplatform.googleapis.com/v1"
            f"/projects/{PROJECT_ID}/locations/{REGION}/pipelineJobs/{safe_job_id}"
        )
        pj_resp = authed.get(pj_url, timeout=15)
        prelim_model_type = _normalize_model_type(explicit_model_type) or "alphafold2"
        if pj_resp.status_code == 404 and prelim_model_type == "alphafold3":
            pj = {"displayName": str(job_id)}
            gcs_output_dir = f"gs://{BUCKET_NAME}/af3_predictions/{job_id}/"
        else:
            pj_resp.raise_for_status()
            pj = pj_resp.json()
            gcs_output_dir = (
                pj.get("runtimeConfig", {}).get("gcsOutputDirectory", "").rstrip("/")
                + "/"
            )
            if not gcs_output_dir or gcs_output_dir == "/":
                return jsonify(
                    {"error": "Could not determine gcs_output_directory for job"}
                ), 400

        raw_model_type = (
            explicit_model_type
            or pj.get("labels", {}).get("model_type")
            or _infer_model_type_from_display_name(pj.get("displayName", ""))
            or "alphafold2"
        )
        model_type = _normalize_model_type(raw_model_type)
        if model_type not in _SUPPORTED_MODEL_TYPES:
            return jsonify({"error": f"Unsupported model_type: {raw_model_type}"}), 400

        analysis_path = f"{gcs_output_dir}analysis/"
        af3_extra_cfg: dict = {}
        boltz2_extra_cfg: dict = {}

        # Discover predictions based on model type
        if model_type == "alphafold3":
            param_vals = pj.get("runtimeConfig", {}).get("parameterValues", {}) or {}
            labels = pj.get("labels", {}) or {}
            af3_job_name = (
                param_vals.get("job_name")
                or labels.get("query_name")
                or pj.get("displayName")
                or str(job_id)
            )
            af3_prefix = f"af3_predictions/{af3_job_name}"
            raw_predictions = _discover_af3_predictions(af3_prefix)
            if raw_predictions and any(
                "/af3_predictions/" in p.get("cif_uri", "") for p in raw_predictions
            ):
                analysis_path = f"gs://{BUCKET_NAME}/{af3_prefix}/analysis/"
            else:
                raw_predictions = _discover_af3_predictions(gcs_output_dir)
                analysis_path = f"{gcs_output_dir}analysis/"
            af3_extra_cfg = {
                "job_id": af3_job_name,
                "pipeline_job_id": str(job_id),
                "project_id": PROJECT_ID,
                "region": REGION,
                "bucket_name": BUCKET_NAME,
                "job_prefix": af3_prefix,
                "pipeline_root_prefix": gcs_output_dir.replace(
                    f"gs://{BUCKET_NAME}/", ""
                ).strip("/"),
                "model_type": "alphafold3",
            }
        elif model_type == "openfold3":
            raw_predictions = _discover_of3_predictions(gcs_output_dir)
        elif model_type == "boltz2":
            raw_predictions, affinity_uri = _discover_boltz2_predictions_and_affinity(
                gcs_output_dir
            )
            boltz2_extra_cfg = {
                "affinity_uri": affinity_uri,
                "query_yaml_uri": _resolve_boltz2_query_yaml_uri(pj),
            }
        elif model_type == "boltzgen":
            raw_predictions = _discover_boltzgen_predictions(gcs_output_dir)
        elif model_type in ("rfd4_proteina", "rfd4-proteina"):
            raw_predictions = _discover_rfd4_proteina_predictions(gcs_output_dir)
        elif model_type == "bindcraft":
            raw_predictions = _discover_bindcraft_predictions(gcs_output_dir)
        else:
            raw_predictions = _discover_af2_predictions(gcs_output_dir)

        cr_job_name = os.environ.get("ANALYSIS_JOB_NAME", "foldrun-analysis-job")

        if not raw_predictions:
            return jsonify({"error": "No prediction outputs found for this job"}), 404

        task_config = build_analysis_task_config(
            model_type=model_type,
            job_id=str(job_id),
            analysis_path=analysis_path,
            raw_predictions=raw_predictions,
            affinity_uri=boltz2_extra_cfg.get("affinity_uri"),
            query_yaml_uri=boltz2_extra_cfg.get("query_yaml_uri"),
            extra_fields=af3_extra_cfg or None,
        ).model_dump()

        # Clear any stale analysis artifacts before writing task_config.json so
        # sibling tasks do not prematurely consolidate against old outputs.
        tc_bucket_name, tc_blob_path = parse_gcs_uri(f"{analysis_path}task_config.json")
        tc_bucket = storage_client.bucket(tc_bucket_name)
        _, analysis_prefix = parse_gcs_uri(analysis_path)
        _cleanup_stale_analysis_blobs(tc_bucket, analysis_prefix)

        tc_bucket.blob(tc_blob_path).upload_from_string(
            json.dumps(task_config, indent=2), content_type="application/json"
        )

        # Trigger the Cloud Run analysis job via REST API (capped at 25 parallel tasks)
        task_count = max(1, min(len(raw_predictions), 25))
        cr_job_path = f"projects/{PROJECT_ID}/locations/{REGION}/jobs/{cr_job_name}"
        cr_url = f"https://{REGION}-run.googleapis.com/v2/{cr_job_path}:run"
        cr_body = {
            "overrides": {
                "taskCount": task_count,
                "timeout": "600s",
                "containerOverrides": [
                    {
                        "env": [
                            {"name": "ANALYSIS_PATH", "value": analysis_path},
                            {"name": "MODEL_TYPE", "value": model_type},
                            {"name": "GCS_BUCKET", "value": BUCKET_NAME},
                            {"name": "GOOGLE_CLOUD_PROJECT", "value": PROJECT_ID},
                            {"name": "PIPELINE_JOB_LOCATION", "value": REGION},
                        ]
                    }
                ],
            }
        }
        cr_resp = authed.post(cr_url, json=cr_body, timeout=30)
        cr_resp.raise_for_status()

        # Write analysis_metadata.json only after Cloud Run Job trigger succeeds
        # so a failed API call never leaves the job stuck in "Analyzing…" state
        metadata_payload = AnalysisMetadata(
            job_id=str(job_id),
            total_predictions=len(raw_predictions),
            started_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            status="running",
            model_type=model_type,
            execution_method="cloud_run_job",
            triggered_by="foldrun-viewer",
        ).model_dump(exclude_none=True)
        tc_bucket.blob(
            tc_blob_path.replace("task_config.json", "analysis_metadata.json")
        ).upload_from_string(
            json.dumps(metadata_payload, indent=2),
            content_type="application/json",
        )

        logger.info(
            f"Triggered analysis for {job_id}: {task_count} tasks ({len(raw_predictions)} predictions), job={cr_job_name}"
        )
        return jsonify(
            {
                "status": "started",
                "job_id": job_id,
                "model_type": model_type,
                "total_predictions": len(raw_predictions),
                "task_count": task_count,
                "analysis_path": analysis_path,
            }
        )

    except Exception as e:
        logger.error(f"Error triggering analysis for {job_id}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def _sanitize_filename(value: str) -> str:
    """Sanitize a string for safe use in Content-Disposition filenames."""
    return _common_sanitize_filename(value, fallback="foldrun")


def _build_expert_analysis_markdown(summary: dict) -> str:
    """Format standalone Markdown report from summary.json."""
    return _common_build_expert_analysis_markdown(summary)


def _ensure_bundle_in_gcs(summary: dict, summary_uri: str) -> str:
    """Ensure compact artifacts_bundle.zip exists in GCS for the given summary, building on-demand if missing."""
    analysis_path = summary_uri.rsplit("/", 1)[0] + "/"
    bundle_uri = ensure_run_archive_in_gcs(
        analysis_path=analysis_path,
        summary_data=summary,
        storage_client=storage_client,
        expected_bucket=BUCKET_NAME,
        allowed_prefixes=ALLOWED_GCS_PREFIXES,
        raise_on_error=True,
    )
    if not bundle_uri:
        raise ValueError("Could not resolve or build artifacts_bundle.zip")
    return bundle_uri


def _generate_signed_url_or_none(
    gcs_uri: str,
    download_filename: str,
    response_type: str | None = None,
) -> str | None:
    """Attempt to generate a 60-minute V4 Signed URL; return None if local user ADC cannot sign."""
    try:
        bucket_name, blob_path = parse_gcs_uri(gcs_uri)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)

        safe_filename = _sanitize_filename(download_filename)
        disposition = f'attachment; filename="{safe_filename}"'
        expiration = timedelta(seconds=SIGNED_URL_EXPIRATION_SECONDS)

        if not _creds.valid:
            _creds.refresh(google.auth.transport.requests.Request())

        service_account_email = getattr(_creds, "service_account_email", None)
        token = getattr(_creds, "token", None)

        kwargs = {
            "version": "v4",
            "expiration": expiration,
            "method": "GET",
            "response_disposition": disposition,
        }
        if response_type:
            kwargs["response_type"] = response_type

        if hasattr(_creds, "sign_bytes") and not isinstance(
            _creds, compute_engine_credentials.Credentials
        ):
            signed_url = blob.generate_signed_url(credentials=_creds, **kwargs)
        elif service_account_email and token:
            signed_url = blob.generate_signed_url(
                service_account_email=service_account_email,
                access_token=token,
                **kwargs,
            )
        else:
            return None

        parsed = urllib.parse.urlparse(signed_url)
        if parsed.scheme == "https" and parsed.netloc == "storage.googleapis.com":
            return signed_url
        return None
    except Exception as exc:
        logger.info(
            f"V4 signed URL unavailable for {gcs_uri} (falling back to direct stream): {exc}"
        )
        return None


def _serve_gcs_download(
    gcs_uri: str,
    download_filename: str,
    response_type: str,
    allowed_extensions=None,
):
    """Redirect to a 60-minute V4 Signed URL (on Cloud Run) or stream directly from GCS (local dev)."""
    bucket_name, blob_path = parse_gcs_uri(
        gcs_uri, allowed_extensions=allowed_extensions
    )
    safe_filename = _sanitize_filename(download_filename)
    is_zst_to_plain = blob_path.lower().endswith(
        ".zst"
    ) and not safe_filename.lower().endswith(".zst")

    signed_filename = f"{safe_filename}.zst" if is_zst_to_plain else safe_filename
    signed_response_type = "application/zstd" if is_zst_to_plain else response_type

    signed_url = _generate_signed_url_or_none(
        gcs_uri=gcs_uri,
        download_filename=signed_filename,
        response_type=signed_response_type,
    )

    if request.args.get("format") == "json":
        return jsonify(
            {
                "gcs_uri": gcs_uri,
                "signed_url": signed_url,
                "filename": signed_filename,
                "expires_in_seconds": SIGNED_URL_EXPIRATION_SECONDS,
            }
        )

    if signed_url and not is_zst_to_plain:
        parsed = urllib.parse.urlparse(signed_url)
        if parsed.scheme == "https" and parsed.netloc == "storage.googleapis.com":
            return redirect(signed_url, code=302)

    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    if not blob.exists():
        return jsonify({"error": f"Artifact not found: {blob_path}"}), 404

    data = blob.download_as_bytes()
    if is_zst_to_plain:
        data = _decompress_zst_bytes(data)
    return Response(
        data,
        status=200,
        mimetype=response_type,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_filename}"',
            "Cache-Control": "private, max-age=300",
        },
    )


@app.route("/api/download/bundle")
def download_bundle():
    """Download the compact .zip archive of job artifacts (via V4 Signed URL or direct stream)."""
    job_id = request.args.get("job_id")
    summary_uri = request.args.get("summary_uri")

    if not job_id and not summary_uri:
        return jsonify({"error": "Either job_id or summary_uri is required"}), 400

    try:
        summary, resolved_summary_uri = _resolve_summary_and_uri(
            job_id=job_id, summary_uri=summary_uri
        )
        bundle_uri = _ensure_bundle_in_gcs(summary, resolved_summary_uri)
        effective_job_id = summary.get("job_id") or job_id or "foldrun_job"
        download_filename = f"{_sanitize_filename(effective_job_id)}_artifacts.zip"
        return _serve_gcs_download(
            gcs_uri=bundle_uri,
            download_filename=download_filename,
            response_type="application/zip",
            allowed_extensions=(".zip",),
        )
    except ValueError as e:
        logger.warning(f"Invalid bundle download request: {e}")
        return jsonify({"error": str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.error(f"Error serving bundle download: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/download/file")
def download_file():
    """Download an individual job artifact (structure, report, summary, affinity, or plot)."""
    job_id = request.args.get("job_id")
    summary_uri = request.args.get("summary_uri")
    artifact_type = request.args.get("type", "structure")
    rank_param = request.args.get("rank", "1")

    if not job_id and not summary_uri:
        return jsonify({"error": "Either job_id or summary_uri is required"}), 400

    try:
        rank = max(1, int(rank_param))
    except ValueError:
        return jsonify({"error": "rank must be an integer"}), 400

    try:
        summary, resolved_summary_uri = _resolve_summary_and_uri(
            job_id=job_id, summary_uri=summary_uri
        )
        effective_job_id = _sanitize_filename(
            summary.get("job_id") or job_id or "foldrun_job"
        )
        analysis_path = resolved_summary_uri.rsplit("/", 1)[0] + "/"

        if artifact_type == "report":
            report_md = _build_expert_analysis_markdown(summary)
            filename = f"{effective_job_id}_expert_analysis.md"
            return Response(
                report_md.encode("utf-8"),
                status=200,
                mimetype="text/markdown; charset=utf-8",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                },
            )

        if artifact_type == "summary":
            return _serve_gcs_download(
                gcs_uri=resolved_summary_uri,
                download_filename=f"{effective_job_id}_summary.json",
                response_type="application/json",
                allowed_extensions=(".json",),
            )

        if artifact_type == "affinity":
            affinity_uri = f"{analysis_path}affinity.json"
            bucket_name, aff_blob_path = parse_gcs_uri(
                affinity_uri, allowed_extensions=(".json",)
            )
            if storage_client.bucket(bucket_name).blob(aff_blob_path).exists():
                return _serve_gcs_download(
                    gcs_uri=affinity_uri,
                    download_filename=f"{effective_job_id}_affinity.json",
                    response_type="application/json",
                    allowed_extensions=(".json",),
                )
            affinity_data = summary.get("summary", {}).get("affinity")
            if affinity_data:
                return Response(
                    json.dumps(affinity_data, indent=2).encode("utf-8"),
                    status=200,
                    mimetype="application/json",
                    headers={
                        "Content-Disposition": f'attachment; filename="{effective_job_id}_affinity.json"',
                    },
                )
            return jsonify({"error": "No affinity data found for this job"}), 404

        predictions = summary.get("all_predictions_summary") or summary.get(
            "top_predictions", []
        )
        selected_pred = None
        for p in predictions:
            if p.get("rank") == rank:
                selected_pred = p
                break
        if not selected_pred:
            selected_pred = summary.get("best_prediction") or (
                predictions[0] if predictions else None
            )
        if not selected_pred:
            return jsonify({"error": "No predictions found in summary"}), 404

        pred_rank = selected_pred.get("rank", rank)
        sample_label = _sanitize_filename(
            selected_pred.get("sample_name")
            or selected_pred.get("model_name")
            or f"rank_{pred_rank}"
        )

        if artifact_type == "structure":
            struct_uri = (
                selected_pred.get("cif_uri")
                or selected_pred.get("pdb_uri")
                or selected_pred.get("uri")
                or ""
            )
            if struct_uri.endswith("/raw_prediction.pkl"):
                struct_uri = struct_uri.replace(
                    "/raw_prediction.pkl", "/unrelaxed_protein.pdb"
                )
            if not struct_uri:
                return jsonify({"error": "No structure URI found for prediction"}), 404
            struct_lower = struct_uri.lower()
            ext = (
                ".cif"
                if struct_lower.endswith(".cif") or struct_lower.endswith(".cif.zst")
                else ".pdb"
            )
            content_type = "chemical/x-mmcif" if ext == ".cif" else "chemical/x-pdb"
            filename = f"{effective_job_id}_rank_{pred_rank:02d}_{sample_label}{ext}"
            return _serve_gcs_download(
                gcs_uri=struct_uri,
                download_filename=filename,
                response_type=content_type,
                allowed_extensions=(".pdb", ".cif", ".cif.zst", ".pdb.zst"),
            )

        if artifact_type in (
            "plddt_plot",
            "pae_plot",
            "pde_plot",
            "iptm_matrix_plot",
            "ranking_comparison",
            "plddt_distribution",
        ):
            plot_uri = (selected_pred.get("plots") or {}).get(artifact_type) or (
                summary.get("plots") or {}
            ).get(artifact_type)
            if not plot_uri:
                return jsonify(
                    {"error": f"Plot '{artifact_type}' not found for rank {pred_rank}"}
                ), 404
            filename = (
                f"{effective_job_id}_rank_{pred_rank:02d}_{artifact_type}.png"
                if artifact_type
                not in ("iptm_matrix_plot", "ranking_comparison", "plddt_distribution")
                else f"{effective_job_id}_{artifact_type}.png"
            )
            return _serve_gcs_download(
                gcs_uri=plot_uri,
                download_filename=filename,
                response_type="image/png",
                allowed_extensions=(".png", ".jpg", ".jpeg"),
            )

        return jsonify({"error": f"Unsupported artifact type: {artifact_type}"}), 400

    except ValueError as e:
        logger.warning(f"Invalid file download request: {e}")
        return jsonify({"error": str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.error(f"Error serving file download: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    """Health check endpoint for Cloud Run"""
    return jsonify({"status": "healthy", "service": "foldrun-viewer"}), 200


_BINDER_MODEL_ALIASES = {
    "boltzgen": "boltzgen",
    "rfd4-proteina": "rfd4_proteina",
    "rfd4_proteina": "rfd4_proteina",
    "rfd4": "rfd4_proteina",
    "proteina": "rfd4_proteina",
    "bindcraft": "bindcraft",
}


def _discover_binder_predictions(pipeline_root: str) -> list:
    """Scan GCS for generative binder design confidence_design_*.json files under pipeline_root."""
    bucket_name, prefix = parse_gcs_uri(pipeline_root)
    if not prefix.endswith("/"):
        prefix += "/"
    bucket = storage_client.bucket(bucket_name)
    predictions = []
    seen = set()
    for blob in bucket.list_blobs(prefix=prefix):
        name = blob.name
        if not name.endswith(".json") or (
            "/predictions/" not in name and "confidence_design" not in name
        ):
            continue
        parts_list = name.split("/")
        filename = parts_list[-1]
        if "confidence_design_" in filename and name not in seen:
            seen.add(name)
            cif_filename = filename.replace("confidence_", "", 1).replace(
                ".json", ".cif"
            )
            cif_name = "/".join([*parts_list[:-1], cif_filename])
            sample_name = filename.replace("confidence_", "", 1).replace(".json", "")
            predictions.append(
                {
                    "sample_name": sample_name,
                    "cif_uri": f"gs://{bucket_name}/{cif_name}",
                    "aggregated_uri": f"gs://{bucket_name}/{name}",
                }
            )
    return predictions


def _discover_bindcraft_predictions(pipeline_root: str) -> list:
    """Discover BindCraft design outputs (confidence_design_*.json or Accepted/*.cif|*.pdb)."""
    predictions = _discover_binder_predictions(pipeline_root)
    if predictions:
        return predictions

    bucket_name, prefix = parse_gcs_uri(pipeline_root)
    if not prefix.endswith("/"):
        prefix += "/"
    bucket = storage_client.bucket(bucket_name)
    all_blobs = list(bucket.list_blobs(prefix=prefix))
    stats_csv = next(
        (
            b.name
            for b in all_blobs
            if b.name.endswith("/final_design_stats.csv")
            or b.name.endswith("final_design_stats.csv")
        ),
        "",
    )
    for blob in sorted(all_blobs, key=lambda x: x.name):
        name = blob.name
        if "/Accepted/" in name and (name.endswith(".cif") or name.endswith(".pdb")):
            sample_name = name.split("/")[-1].rsplit(".", 1)[0]
            predictions.append(
                {
                    "sample_name": sample_name,
                    "cif_uri": f"gs://{bucket_name}/{name}",
                    "aggregated_uri": (
                        f"gs://{bucket_name}/{stats_csv}" if stats_csv else ""
                    ),
                }
            )
    return predictions


_discover_boltzgen_predictions = _discover_binder_predictions
_discover_rfd4_proteina_predictions = _discover_binder_predictions


if __name__ == "__main__":
    # For local development
    port = int(os.environ.get("PORT", 8080))
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
