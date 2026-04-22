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

# Initialize GCS client
storage_client = storage.Client(project=PROJECT_ID)


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
    """List recent FoldRun pipeline jobs from Vertex AI, sorted by recency.

    Works both in Cloud Run (identity token) and locally via Docker (ADC mount).
    Returns an empty list with an error message if credentials are unavailable.
    """
    try:
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        authed = google.auth.transport.requests.AuthorizedSession(creds)

        url = (
            f"https://{REGION}-aiplatform.googleapis.com/v1"
            f"/projects/{PROJECT_ID}/locations/{REGION}/pipelineJobs"
        )
        resp = authed.get(
            url,
            params={
                "filter": "labels.submitted_by=foldrun-agent",
                "orderBy": "createTime desc",
                "pageSize": "50",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        jobs = []
        for pj in data.get("pipelineJobs", []):
            labels = pj.get("labels", {})
            resource_name = pj.get("name", "")
            job_id = resource_name.split("/")[-1]
            jobs.append(
                {
                    "job_id": job_id,
                    "display_name": pj.get("displayName", job_id),
                    "model_type": labels.get("model_type", "alphafold2"),
                    "state": pj.get("state", "PIPELINE_STATE_UNSPECIFIED"),
                    "create_time": pj.get("createTime", ""),
                }
            )

        return jsonify({"jobs": jobs})

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


@app.route("/health")
def health():
    """Health check endpoint for Cloud Run"""
    return jsonify({"status": "healthy", "service": "foldrun-viewer"}), 200


if __name__ == "__main__":
    # For local development
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
