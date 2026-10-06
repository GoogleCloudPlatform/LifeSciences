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
Cloud Run web application for viewing AlphaFold2, OpenFold3, and Boltz-2 predictions
"""

import json
import logging
import os
import re
import tempfile
import urllib.parse
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import google.auth
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
JOB_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
SIGNED_URL_EXPIRATION_SECONDS = int(
    os.environ.get("SIGNED_URL_EXPIRATION_SECONDS", "3600")
)


def parse_gcs_uri(uri, allowed_extensions=None):
    """Parse and validate gs://bucket/path URI into bucket and path components."""
    if not uri or not uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {uri}")

    uri_body = uri[5:]  # Remove 'gs://'
    parts = uri_body.split("/", 1)
    bucket_name = parts[0]
    blob_path = parts[1] if len(parts) > 1 else ""

    if bucket_name != BUCKET_NAME:
        raise ValueError(
            f"Access denied: bucket '{bucket_name}' does not match configured BUCKET_NAME"
        )

    if ".." in blob_path.split("/") or ".." in blob_path or blob_path.startswith("/"):
        raise ValueError(f"Invalid GCS path: path traversal detected in '{blob_path}'")

    if not blob_path or not any(
        blob_path.startswith(prefix) for prefix in ALLOWED_GCS_PREFIXES
    ):
        raise ValueError(
            f"Invalid GCS path: '{blob_path}' must start with an allowed prefix {ALLOWED_GCS_PREFIXES}"
        )

    if allowed_extensions and not blob_path.lower().endswith(allowed_extensions):
        raise ValueError(
            f"Invalid GCS path: '{blob_path}' must end with one of {allowed_extensions}"
        )

    return bucket_name, blob_path


def load_gcs_file(uri, as_json=False, allowed_extensions=None):
    """Load a file from GCS"""
    try:
        bucket_name, blob_path = parse_gcs_uri(
            uri, allowed_extensions=allowed_extensions
        )
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)

        content = blob.download_as_bytes()

        if as_json:
            return json.loads(content.decode("utf-8"))

        return content.decode("utf-8")

    except Exception as e:
        logger.error(f"Error loading {uri}: {e}")
        raise


def get_pdb_content(pdb_uri):
    """Get PDB file content from GCS"""
    return load_gcs_file(pdb_uri, allowed_extensions=(".pdb",))


def _resolve_summary_and_uri(job_id=None, summary_uri=None):
    """Resolve and load analysis summary JSON together with its canonical gs:// URI."""
    if summary_uri:
        parse_gcs_uri(summary_uri, allowed_extensions=(".json",))
        return (
            load_gcs_file(summary_uri, as_json=True, allowed_extensions=(".json",)),
            summary_uri,
        )

    if not job_id:
        raise ValueError("Either job_id or summary_uri must be provided")

    if not JOB_ID_PATTERN.fullmatch(str(job_id)):
        raise ValueError(f"Invalid job_id format: {job_id}")

    bucket = storage_client.bucket(BUCKET_NAME)

    # Check AlphaFold 3 predictions directory first
    af3_summary_path = f"af3_predictions/{job_id}/analysis/summary.json"
    if bucket.blob(af3_summary_path).exists():
        uri = f"gs://{BUCKET_NAME}/{af3_summary_path}"
        return load_gcs_file(uri, as_json=True), uri

    match = re.search(r"(\d{8})\d{6}$", job_id)
    if not match:
        raise ValueError(f"Cannot extract date from job_id: {job_id}")

    date_prefix = match.group(1)  # YYYYMMDD
    prefix = f"pipeline_runs/{date_prefix}"

    summary_blobs = list(
        bucket.list_blobs(prefix=prefix, match_glob="**/analysis/summary.json")
    )

    if not summary_blobs:
        all_blobs = list(bucket.list_blobs(prefix=prefix))
        summary_blobs = [
            b for b in all_blobs if b.name.endswith("/analysis/summary.json")
        ]

    if not summary_blobs:
        raise FileNotFoundError(
            f"No analysis summary found for job {job_id}. "
            f"Searched gs://{BUCKET_NAME}/{af3_summary_path} and gs://{BUCKET_NAME}/{prefix}*/analysis/summary.json"
        )

    if len(summary_blobs) == 1:
        uri = f"gs://{BUCKET_NAME}/{summary_blobs[0].name}"
        return load_gcs_file(uri, as_json=True), uri

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

    summary_blobs.sort(key=lambda b: b.name, reverse=True)
    uri = f"gs://{BUCKET_NAME}/{summary_blobs[0].name}"
    return load_gcs_file(uri, as_json=True), uri


def get_analysis_summary(job_id=None, summary_uri=None):
    """Get enhanced analysis summary from GCS.

    When given a job_id, searches for the analysis summary by extracting the
    date from the pipeline job name and scanning matching pipeline_runs/ dirs.
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
        complete: set of 14-digit timestamps (YYYYMMDDHHMMSS) where summary.json exists
        running:  set of timestamps where analysis_metadata.json exists but summary.json does not
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
            m = re.search(r"pipeline_runs/(\d{8})_(\d{6})/", name)
            if not m:
                continue
            ts = m.group(1) + m.group(2)
            if name.endswith("/analysis/summary.json"):
                complete.add(ts)
            elif name.endswith("/analysis/analysis_metadata.json"):
                has_metadata.add(ts)
    except Exception as e:
        logger.warning(f"Could not scan analysis state: {e}")
    running = has_metadata - complete
    return complete, running


def _discover_af2_predictions(pipeline_root: str) -> list:
    """Scan GCS for AF2 raw_prediction.pkl files under pipeline_root."""
    bucket_name, prefix = parse_gcs_uri(pipeline_root)
    if not prefix.endswith("/"):
        prefix += "/"
    bucket = storage_client.bucket(bucket_name)
    predictions = []
    for blob in bucket.list_blobs(prefix=prefix):
        if blob.name.endswith("/raw_prediction.pkl"):
            uri = f"gs://{bucket_name}/{blob.name}"
            # Best-effort model name from parent directory
            parent = blob.name.split("/")[-2]
            predictions.append(
                {"uri": uri, "model_name": parent, "ranking_confidence": 0}
            )
    return predictions


def _discover_of3_predictions(pipeline_root: str) -> list:
    """Scan GCS for OF3 *_confidences_aggregated.json files under pipeline_root."""
    bucket_name, prefix = parse_gcs_uri(pipeline_root)
    if not prefix.endswith("/"):
        prefix += "/"
    bucket = storage_client.bucket(bucket_name)
    predictions = []
    seen = set()
    for blob in bucket.list_blobs(prefix=prefix):
        name = blob.name
        if name.endswith("_confidences_aggregated.json") and name not in seen:
            seen.add(name)
            base = name.replace("_confidences_aggregated.json", "")
            sample_name = name.split("/")[-1].replace(
                "_confidences_aggregated.json", ""
            )
            predictions.append(
                {
                    "cif_uri": f"gs://{bucket_name}/{base}_model.cif",
                    "confidences_uri": f"gs://{bucket_name}/{base}_confidences.json",
                    "aggregated_uri": f"gs://{bucket_name}/{name}",
                    "sample_name": sample_name,
                }
            )
    return predictions


def _discover_boltz2_predictions(pipeline_root: str) -> list:
    """Scan GCS for Boltz-2 confidence_*.json files under pipeline_root."""
    bucket_name, prefix = parse_gcs_uri(pipeline_root)
    if not prefix.endswith("/"):
        prefix += "/"
    bucket = storage_client.bucket(bucket_name)
    predictions = []
    seen = set()
    for blob in bucket.list_blobs(prefix=prefix):
        name = blob.name
        if "/predictions/" not in name or not name.endswith(".json"):
            continue
        parts_list = name.split("/")
        filename = parts_list[-1]
        if "/confidence_" in name and name not in seen:
            seen.add(name)
            cif_filename = filename.replace("confidence_", "", 1).replace(
                ".json", ".cif"
            )
            cif_name = "/".join([*parts_list[:-1], cif_filename])
            pde_filename = filename.replace("confidence_", "pde_", 1).replace(
                ".json", ".npz"
            )
            pde_name = "/".join([*parts_list[:-1], pde_filename])
            sample_name = filename.replace("confidence_", "", 1).replace(".json", "")
            predictions.append(
                {
                    "sample_name": sample_name,
                    "cif_uri": f"gs://{bucket_name}/{cif_name}",
                    "confidences_uri": "",
                    "aggregated_uri": f"gs://{bucket_name}/{name}",
                    "pde_uri": f"gs://{bucket_name}/{pde_name}",
                }
            )
    return predictions


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
    """API endpoint to fetch CIF content (for OF3 and Boltz-2 predictions)"""
    cif_uri = request.args.get("uri")

    if not cif_uri:
        return jsonify({"error": "uri parameter is required"}), 400

    try:
        content = load_gcs_file(cif_uri, allowed_extensions=(".cif",))
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

        jobs = []
        for pj in data.get("pipelineJobs", []):
            labels = pj.get("labels", {})
            resource_name = pj.get("name", "")
            job_id = resource_name.split("/")[-1]
            # Extract exact GCS timestamp from gcsOutputDirectory (e.g.
            # "gs://bucket/pipeline_runs/20260504_224020" → "20260504224020").
            # This is more reliable than parsing the job ID timestamp, which can
            # differ by 1-2 seconds from the directory name — causing false
            # positives when multiple jobs are submitted within seconds of each other.
            gcs_output_dir = pj.get("runtimeConfig", {}).get("gcsOutputDirectory", "")
            gcs_ts = None
            m = re.search(r"pipeline_runs/(\d{8})_(\d{6})", gcs_output_dir)
            if m:
                gcs_ts = m.group(1) + m.group(2)
            jobs.append(
                {
                    "job_id": job_id,
                    "display_name": pj.get("displayName", job_id),
                    "model_type": labels.get("model_type", "alphafold2"),
                    "state": pj.get("state", "PIPELINE_STATE_UNSPECIFIED"),
                    "create_time": pj.get("createTime", ""),
                    "has_analysis": False,
                    "analysis_running": False,
                    "_gcs_ts": gcs_ts,
                }
            )

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
                    if not jid or not JOB_ID_PATTERN.fullmatch(jid):
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
                        },
                    )
                    if b.time_created:
                        iso_ts = b.time_created.isoformat().replace("+00:00", "Z")
                        if not entry["create_time"] or iso_ts > entry["create_time"]:
                            entry["create_time"] = iso_ts
                    if b.name.endswith("/analysis/summary.json"):
                        entry["has_analysis"] = True
                        if "msa_free" in jid:
                            entry["display_name"] = f"{jid} (Zero-MSA / --msa-free)"
                        elif "full_msa" in jid:
                            entry["display_name"] = (
                                f"{jid} (Full 630 GB MSA + Templates)"
                            )
                seen_ids = {j["job_id"] for j in jobs}
                for jid, af3_job in af3_by_job.items():
                    if jid not in seen_ids and af3_job["has_analysis"]:
                        jobs.append(af3_job)
                jobs.sort(key=lambda x: x.get("create_time", ""), reverse=True)
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
        # quality_metrics is nested under summary["summary"], not at the root
        qm = summary.get("summary", {}).get("quality_metrics", {})
        return jsonify(
            {
                "quality_assessment": qm.get("quality_assessment", ""),
                "best_model_plddt": qm.get("best_model_plddt"),
                "best_model_pae": qm.get("best_model_pae"),
            }
        )
    except Exception:
        return jsonify({"quality_assessment": ""}), 404


@app.route("/api/analyze", methods=["POST"])
def trigger_analysis():
    """Trigger analysis for a succeeded prediction job that has no analysis yet.

    Discovers prediction outputs in GCS, writes task_config.json, and kicks off
    the appropriate Cloud Run analysis job.
    """
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    model_type = str(data.get("model_type", "alphafold2")).strip().lower()

    if not job_id:
        return jsonify({"error": "job_id is required"}), 400

    if not JOB_ID_PATTERN.fullmatch(str(job_id)):
        return jsonify({"error": "Invalid job_id format"}), 400

    allowed_model_types = {"alphafold2", "openfold3", "boltz2"}
    if model_type not in allowed_model_types:
        return jsonify(
            {
                "error": (
                    f"Invalid model_type '{model_type}'. "
                    f"Must be one of {sorted(allowed_model_types)}"
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
        pj_resp.raise_for_status()
        pj = pj_resp.json()

        gcs_output_dir = (
            pj.get("runtimeConfig", {}).get("gcsOutputDirectory", "").rstrip("/") + "/"
        )
        if not gcs_output_dir or gcs_output_dir == "/":
            return jsonify(
                {"error": "Could not determine gcs_output_directory for job"}
            ), 400

        analysis_path = f"{gcs_output_dir}analysis/"

        # Discover predictions based on model type
        if model_type == "openfold3":
            raw_predictions = _discover_of3_predictions(gcs_output_dir)
        elif model_type == "boltz2":
            raw_predictions = _discover_boltz2_predictions(gcs_output_dir)
        else:
            raw_predictions = _discover_af2_predictions(gcs_output_dir)

        cr_job_name = os.environ.get("ANALYSIS_JOB_NAME", "foldrun-analysis-job")

        if not raw_predictions:
            return jsonify({"error": "No prediction outputs found for this job"}), 404

        # Build task_config.json in the same format the Cloud Run job expects
        if model_type == "alphafold2":
            predictions_cfg = [
                {
                    "index": i,
                    "uri": p["uri"],
                    "model_name": p["model_name"],
                    "ranking_confidence": p["ranking_confidence"],
                    "output_uri": f"{analysis_path}prediction_{i}_analysis.json",
                }
                for i, p in enumerate(raw_predictions)
            ]
        elif model_type == "openfold3":
            predictions_cfg = [
                {
                    "index": i,
                    "cif_uri": p["cif_uri"],
                    "confidences_uri": p["confidences_uri"],
                    "aggregated_uri": p["aggregated_uri"],
                    "sample_name": p["sample_name"],
                    "output_uri": f"{analysis_path}prediction_{i}_analysis.json",
                }
                for i, p in enumerate(raw_predictions)
            ]
        else:  # boltz2
            predictions_cfg = [
                {
                    "index": i,
                    "sample_name": p["sample_name"],
                    "cif_uri": p["cif_uri"],
                    "confidences_uri": p.get("confidences_uri", ""),
                    "aggregated_uri": p["aggregated_uri"],
                    "pde_uri": p.get("pde_uri", ""),
                    "output_uri": f"{analysis_path}prediction_{i}_analysis.json",
                }
                for i, p in enumerate(raw_predictions)
            ]

        task_config = {
            "job_id": job_id,
            "analysis_path": analysis_path,
            "task_config_uri": f"{analysis_path}task_config.json",
            "predictions": predictions_cfg,
        }

        # Write task_config.json to GCS before triggering the job (tasks read it on startup)
        tc_bucket_name, tc_blob_path = parse_gcs_uri(f"{analysis_path}task_config.json")
        tc_bucket = storage_client.bucket(tc_bucket_name)

        tc_bucket.blob(tc_blob_path).upload_from_string(
            json.dumps(task_config, indent=2), content_type="application/json"
        )

        # Trigger the Cloud Run analysis job via REST API
        cr_job_path = f"projects/{PROJECT_ID}/locations/{REGION}/jobs/{cr_job_name}"
        cr_url = f"https://{REGION}-run.googleapis.com/v2/{cr_job_path}:run"
        cr_body = {
            "overrides": {
                "taskCount": len(raw_predictions),
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
        tc_bucket.blob(
            tc_blob_path.replace("task_config.json", "analysis_metadata.json")
        ).upload_from_string(
            json.dumps(
                {
                    "job_id": job_id,
                    "total_predictions": len(raw_predictions),
                    "started_at": datetime.utcnow().isoformat() + "Z",
                    "status": "running",
                    "model_type": model_type,
                    "execution_method": "cloud_run_job",
                    "triggered_by": "foldrun-viewer",
                },
                indent=2,
            ),
            content_type="application/json",
        )

        logger.info(
            f"Triggered analysis for {job_id}: {len(raw_predictions)} tasks, job={cr_job_name}"
        )
        return jsonify(
            {
                "status": "started",
                "job_id": job_id,
                "model_type": model_type,
                "total_predictions": len(raw_predictions),
                "analysis_path": analysis_path,
            }
        )

    except Exception as e:
        logger.error(f"Error triggering analysis for {job_id}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def _sanitize_filename(value: str) -> str:
    """Sanitize a string for safe use in Content-Disposition filenames."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip())
    return cleaned.strip("_") or "foldrun"


def _build_expert_analysis_markdown(summary: dict) -> str:
    """Format standalone Markdown report from summary.json."""
    job_id = summary.get("job_id", "unknown")
    model_type = summary.get("model_type", "alphafold2")
    expert = summary.get("expert_analysis") or {}
    analysis_text = (expert.get("analysis") or "").strip()
    qm = summary.get("summary", {}).get("quality_metrics", {})

    lines = [
        "# FoldRun Structure & Analysis Report",
        "",
        f"- **Job ID**: `{job_id}`",
        f"- **Model Type**: `{model_type}`",
        f"- **Generated At**: `{summary.get('analyzed_at', '')}`",
    ]
    if qm:
        lines.extend(
            [
                "",
                "## Summary Metrics",
                "",
                f"- **Quality Assessment**: `{qm.get('quality_assessment', 'N/A')}`",
                f"- **Best Model**: `{qm.get('best_model', 'N/A')}`",
                f"- **Best Mean pLDDT**: `{qm.get('best_model_plddt', 'N/A')}`",
            ]
        )
        if qm.get("best_ranking_score") is not None:
            lines.append(f"- **Best Ranking Score**: `{qm.get('best_ranking_score')}`")
        if qm.get("best_ptm") is not None:
            lines.append(f"- **Best pTM**: `{qm.get('best_ptm')}`")
        if qm.get("best_iptm") is not None:
            lines.append(f"- **Best ipTM**: `{qm.get('best_iptm')}`")

    lines.extend(["", "## Expert Analysis", ""])
    if analysis_text:
        lines.append(analysis_text)
    else:
        lines.append("_No Gemini expert analysis text available for this job._")
    lines.append("")
    return "\n".join(lines)


def _ensure_bundle_in_gcs(summary: dict, summary_uri: str) -> str:
    """Ensure compact artifacts_bundle.zip exists in GCS for the given summary, building on-demand if missing."""
    analysis_path = summary_uri.rsplit("/", 1)[0] + "/"
    bundle_uri = (
        summary.get("artifacts_bundle_uri") or f"{analysis_path}artifacts_bundle.zip"
    )
    bucket_name, bundle_blob_path = parse_gcs_uri(
        bundle_uri, allowed_extensions=(".zip",)
    )
    bucket = storage_client.bucket(bucket_name)
    bundle_blob = bucket.blob(bundle_blob_path)
    if bundle_blob.exists():
        return bundle_uri

    job_id = summary.get("job_id", "foldrun_job")
    model_type = summary.get("model_type", "alphafold2")
    predictions = summary.get("all_predictions_summary") or summary.get(
        "top_predictions", []
    )
    protein_info = summary.get("summary", {}).get("protein_info", {})

    with tempfile.TemporaryDirectory(prefix="foldrun_viewer_zip_") as tmp_dir:
        zip_path = Path(tmp_dir) / "artifacts_bundle.zip"
        seen_arcnames: set[str] = set()

        with zipfile.ZipFile(
            zip_path, mode="w", compression=zipfile.ZIP_DEFLATED
        ) as zf:
            zf.writestr(
                "metrics/summary.json",
                json.dumps(summary, indent=2, default=str),
            )
            seen_arcnames.add("metrics/summary.json")

            zf.writestr(
                "report/expert_analysis.md",
                _build_expert_analysis_markdown(summary),
            )
            seen_arcnames.add("report/expert_analysis.md")

            affinity = summary.get("summary", {}).get("affinity")
            if affinity:
                zf.writestr(
                    "metrics/affinity.json",
                    json.dumps(affinity, indent=2, default=str),
                )
                seen_arcnames.add("metrics/affinity.json")

            if protein_info.get("fasta_sequence"):
                header = protein_info.get("fasta_header") or job_id
                zf.writestr(
                    "input/input.fasta",
                    f">{header}\n{protein_info['fasta_sequence']}\n",
                )
                seen_arcnames.add("input/input.fasta")
            elif protein_info.get("input_query_yaml"):
                yaml_val = protein_info["input_query_yaml"]
                yaml_str = (
                    yaml_val
                    if isinstance(yaml_val, str)
                    else json.dumps(yaml_val, indent=2)
                )
                zf.writestr("input/query.yaml", yaml_str)
                seen_arcnames.add("input/query.yaml")
            elif protein_info.get("input_query_json"):
                json_val = protein_info["input_query_json"]
                json_str = (
                    json.dumps(json_val, indent=2)
                    if isinstance(json_val, (dict, list))
                    else str(json_val)
                )
                zf.writestr("input/query.json", json_str)
                seen_arcnames.add("input/query.json")

            for idx, pred in enumerate(predictions, 1):
                rank = pred.get("rank", idx)
                sample_label = _sanitize_filename(
                    pred.get("sample_name") or pred.get("model_name") or f"model_{idx}"
                )
                struct_uri = (
                    pred.get("cif_uri") or pred.get("pdb_uri") or pred.get("uri") or ""
                )
                if struct_uri.endswith("/raw_prediction.pkl"):
                    struct_uri = struct_uri.replace(
                        "/raw_prediction.pkl", "/unrelaxed_protein.pdb"
                    )
                if struct_uri and struct_uri.startswith("gs://"):
                    try:
                        _, s_blob_path = parse_gcs_uri(
                            struct_uri, allowed_extensions=(".pdb", ".cif")
                        )
                        s_blob = bucket.blob(s_blob_path)
                        if s_blob.exists():
                            ext = ".cif" if s_blob_path.endswith(".cif") else ".pdb"
                            arc = f"structures/rank_{rank:02d}_{sample_label}{ext}"
                            if arc not in seen_arcnames:
                                zf.writestr(arc, s_blob.download_as_bytes())
                                seen_arcnames.add(arc)
                    except Exception as exc:
                        logger.warning(f"Skipping structure {struct_uri} in zip: {exc}")

                plots = pred.get("plots") or {}
                for plot_key, plot_uri in plots.items():
                    if not plot_uri or not plot_uri.startswith("gs://"):
                        continue
                    try:
                        _, p_blob_path = parse_gcs_uri(
                            plot_uri, allowed_extensions=(".png", ".jpg", ".jpeg")
                        )
                        p_blob = bucket.blob(p_blob_path)
                        if p_blob.exists():
                            arc = (
                                f"plots/rank_{rank:02d}_{_sanitize_filename(plot_key)}.png"
                                if plot_key != "iptm_matrix_plot"
                                else "plots/iptm_matrix.png"
                            )
                            if arc not in seen_arcnames:
                                zf.writestr(arc, p_blob.download_as_bytes())
                                seen_arcnames.add(arc)
                    except Exception as exc:
                        logger.warning(f"Skipping plot {plot_uri} in zip: {exc}")

            readme_lines = [
                "# FoldRun Prediction Artifacts Bundle",
                "",
                f"- **Job ID**: `{job_id}`",
                f"- **Model Type**: `{model_type}`",
                f"- **Files Included**: `{len(seen_arcnames) + 1}`",
                "",
                "Generated by FoldRun Viewer.",
                "",
            ]
            zf.writestr("README.md", "\n".join(readme_lines))

        bundle_blob.upload_from_filename(str(zip_path), content_type="application/zip")

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

    signed_url = _generate_signed_url_or_none(
        gcs_uri=gcs_uri,
        download_filename=safe_filename,
        response_type=response_type,
    )

    if request.args.get("format") == "json":
        return jsonify(
            {
                "gcs_uri": gcs_uri,
                "signed_url": signed_url,
                "filename": safe_filename,
                "expires_in_seconds": SIGNED_URL_EXPIRATION_SECONDS,
            }
        )

    if signed_url:
        parsed = urllib.parse.urlparse(signed_url)
        if parsed.scheme == "https" and parsed.netloc == "storage.googleapis.com":
            return redirect(signed_url, code=302)

    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    if not blob.exists():
        return jsonify({"error": f"Artifact not found: {blob_path}"}), 404

    data = blob.download_as_bytes()
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
            ext = ".cif" if struct_uri.endswith(".cif") else ".pdb"
            content_type = "chemical/x-mmcif" if ext == ".cif" else "chemical/x-pdb"
            filename = f"{effective_job_id}_rank_{pred_rank:02d}_{sample_label}{ext}"
            return _serve_gcs_download(
                gcs_uri=struct_uri,
                download_filename=filename,
                response_type=content_type,
                allowed_extensions=(".pdb", ".cif"),
            )

        if artifact_type in (
            "plddt_plot",
            "pae_plot",
            "pde_plot",
            "iptm_matrix_plot",
        ):
            plot_uri = (selected_pred.get("plots") or {}).get(artifact_type)
            if not plot_uri:
                return jsonify(
                    {"error": f"Plot '{artifact_type}' not found for rank {pred_rank}"}
                ), 404
            filename = (
                f"{effective_job_id}_rank_{pred_rank:02d}_{artifact_type}.png"
                if artifact_type != "iptm_matrix_plot"
                else f"{effective_job_id}_iptm_matrix.png"
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


if __name__ == "__main__":
    # For local development
    port = int(os.environ.get("PORT", 8080))
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
