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
from datetime import datetime

import google.auth
import google.auth.transport.requests
from flask import Flask, abort, jsonify, render_template, request
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
_quota_project = PROJECT_ID if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") else None
_creds, _ = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"],
    quota_project_id=_quota_project,
)
storage_client = storage.Client(project=PROJECT_ID, credentials=_creds)


def parse_gcs_uri(uri):
    """Parse gs://bucket/path URI into bucket and path components"""
    if not uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {uri}")

    uri = uri[5:]  # Remove 'gs://'
    parts = uri.split("/", 1)

    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def load_gcs_file(uri, as_json=False):
    """Load a file from GCS"""
    try:
        bucket_name, blob_path = parse_gcs_uri(uri)
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
    return load_gcs_file(pdb_uri)


def get_analysis_summary(job_id=None, summary_uri=None):
    """Get enhanced analysis summary from GCS.

    When given a job_id, searches for the analysis summary by extracting the
    date from the pipeline job name and scanning matching pipeline_runs/ dirs.
    """
    if summary_uri:
        return load_gcs_file(summary_uri, as_json=True)

    if not job_id:
        raise ValueError("Either job_id or summary_uri must be provided")

    # Extract date portion from job_id
    # AF2: alphafold-inference-pipeline-20260215153755
    # OF3: openfold3-inference-pipeline-20260215153755
    import re

    match = re.search(r"(\d{8})\d{6}$", job_id)
    if not match:
        raise ValueError(f"Cannot extract date from job_id: {job_id}")

    date_prefix = match.group(1)  # YYYYMMDD

    # Search GCS for pipeline_runs/YYYYMMDD_*/analysis/summary.json
    bucket = storage_client.bucket(BUCKET_NAME)
    prefix = f"pipeline_runs/{date_prefix}"

    # Simpler approach: list all blobs matching the pattern
    summary_blobs = list(
        bucket.list_blobs(prefix=prefix, match_glob="**/analysis/summary.json")
    )

    if not summary_blobs:
        # Fallback: try listing all blobs under prefix and filter
        all_blobs = list(bucket.list_blobs(prefix=prefix))
        summary_blobs = [
            b for b in all_blobs if b.name.endswith("/analysis/summary.json")
        ]

    if not summary_blobs:
        raise FileNotFoundError(
            f"No analysis summary found for job {job_id}. "
            f"Searched gs://{BUCKET_NAME}/{prefix}*/analysis/summary.json"
        )

    # If multiple summaries found for the same date, pick the closest timestamp
    if len(summary_blobs) == 1:
        uri = f"gs://{BUCKET_NAME}/{summary_blobs[0].name}"
        return load_gcs_file(uri, as_json=True)

    # Multiple runs on the same date — match by full timestamp
    full_ts = re.search(r"(\d{14})$", job_id)
    if full_ts:
        ts = full_ts.group(1)  # YYYYMMDDHHMMSS
        target = f"{ts[:8]}_{ts[8:]}"  # YYYYMMDD_HHMMSS
        for blob in summary_blobs:
            if target in blob.name:
                uri = f"gs://{BUCKET_NAME}/{blob.name}"
                return load_gcs_file(uri, as_json=True)
        # Try +/- 1 second for timestamp drift
        for offset in [-1, 1, -2, 2]:
            adjusted = str(int(ts) + offset)
            adjusted_dir = f"{adjusted[:8]}_{adjusted[8:]}"
            for blob in summary_blobs:
                if adjusted_dir in blob.name:
                    uri = f"gs://{BUCKET_NAME}/{blob.name}"
                    return load_gcs_file(uri, as_json=True)

    # Fallback: use the most recent summary
    summary_blobs.sort(key=lambda b: b.name, reverse=True)
    uri = f"gs://{BUCKET_NAME}/{summary_blobs[0].name}"
    return load_gcs_file(uri, as_json=True)


def _get_authed_session():
    """Return an AuthorizedSession using application default credentials."""
    quota_project = PROJECT_ID if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") else None
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
            predictions.append({"uri": uri, "model_name": parent, "ranking_confidence": 0})
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
            sample_name = name.split("/")[-1].replace("_confidences_aggregated.json", "")
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
            cif_filename = filename.replace("confidence_", "", 1).replace(".json", ".cif")
            cif_name = "/".join(parts_list[:-1] + [cif_filename])
            pde_filename = filename.replace("confidence_", "pde_", 1).replace(".json", ".npz")
            pde_name = "/".join(parts_list[:-1] + [pde_filename])
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


@app.route("/")
def index():
    """Landing page - redirects to combined viewer if job_id provided"""
    job_id = request.args.get("job_id")

    if job_id:
        # Redirect to combined viewer
        from flask import redirect, url_for

        return redirect(url_for("combined_viewer", job_id=job_id))

    return render_template("index.html")


@app.route("/job/<job_id>")
def job_viewer(job_id):
    """Short URL for viewing a job"""
    from flask import redirect, url_for

    return redirect(url_for("combined_viewer", job_id=job_id))



@app.route("/combined")
def combined_viewer():
    """Combined structure + analysis viewer"""
    job_id = request.args.get("job_id")
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

    # Derive GCS console link from summary_uri or pdb_uri
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

    return render_template(
        "combined.html",
        job_id=job_id,
        pdb_uri=pdb_uri,
        summary_uri=summary_uri,
        model_name=model_name,
        project_id=PROJECT_ID,
        region=REGION,
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
        content = load_gcs_file(cif_uri)
        return content, 200, {"Content-Type": "text/plain"}
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
        bucket_name, blob_path = parse_gcs_uri(uri)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)

        # Download image content
        image_bytes = blob.download_as_bytes()

        # Determine content type based on file extension
        content_type = "image/png"
        if blob_path.endswith(".jpg") or blob_path.endswith(".jpeg"):
            content_type = "image/jpeg"

        return image_bytes, 200, {"Content-Type": content_type}
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
            gcs_output_dir = (
                pj.get("runtimeConfig", {}).get("gcsOutputDirectory", "")
            )
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

        return jsonify({
            "jobs": jobs,
            "next_page_token": data.get("nextPageToken", ""),
        })

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
        return jsonify({
            "quality_assessment": qm.get("quality_assessment", ""),
            "best_model_plddt": qm.get("best_model_plddt"),
            "best_model_pae": qm.get("best_model_pae"),
        })
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
    model_type = data.get("model_type", "alphafold2")

    if not job_id:
        return jsonify({"error": "job_id is required"}), 400

    try:
        authed = _get_authed_session()

        # Fetch the full pipeline job to get gcsOutputDirectory
        pj_url = (
            f"https://{REGION}-aiplatform.googleapis.com/v1"
            f"/projects/{PROJECT_ID}/locations/{REGION}/pipelineJobs/{job_id}"
        )
        pj_resp = authed.get(pj_url, timeout=15)
        pj_resp.raise_for_status()
        pj = pj_resp.json()

        gcs_output_dir = (
            pj.get("runtimeConfig", {}).get("gcsOutputDirectory", "").rstrip("/") + "/"
        )
        if not gcs_output_dir or gcs_output_dir == "/":
            return jsonify({"error": "Could not determine gcs_output_directory for job"}), 400

        analysis_path = f"{gcs_output_dir}analysis/"

        # Discover predictions based on model type
        if model_type == "openfold3":
            raw_predictions = _discover_of3_predictions(gcs_output_dir)
            cr_job_name = os.environ.get("OF3_ANALYSIS_JOB_NAME", "of3-analysis-job")
        elif model_type == "boltz2":
            raw_predictions = _discover_boltz2_predictions(gcs_output_dir)
            cr_job_name = os.environ.get("BOLTZ2_ANALYSIS_JOB_NAME", "boltz2-analysis-job")
        else:
            raw_predictions = _discover_af2_predictions(gcs_output_dir)
            cr_job_name = os.environ.get("AF2_ANALYSIS_JOB_NAME", "af2-analysis-job")

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

        # Write task_config.json and analysis_metadata.json to GCS
        tc_bucket_name, tc_blob_path = parse_gcs_uri(f"{analysis_path}task_config.json")
        tc_bucket = storage_client.bucket(tc_bucket_name)

        tc_bucket.blob(tc_blob_path).upload_from_string(
            json.dumps(task_config, indent=2), content_type="application/json"
        )
        tc_bucket.blob(tc_blob_path.replace("task_config.json", "analysis_metadata.json")).upload_from_string(
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

        # Trigger the Cloud Run analysis job via REST API
        cr_job_path = f"projects/{PROJECT_ID}/locations/{REGION}/jobs/{cr_job_name}"
        cr_url = f"https://{REGION}-run.googleapis.com/v2/{cr_job_path}:run"
        cr_body = {
            "overrides": {
                "taskCount": len(raw_predictions),
                "timeout": "600s",
                "containerOverrides": [
                    {"env": [{"name": "ANALYSIS_PATH", "value": analysis_path}]}
                ],
            }
        }
        cr_resp = authed.post(cr_url, json=cr_body, timeout=30)
        cr_resp.raise_for_status()

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


@app.route("/health")
def health():
    """Health check endpoint for Cloud Run"""
    return jsonify({"status": "healthy", "service": "foldrun-viewer"}), 200


if __name__ == "__main__":
    # For local development
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
