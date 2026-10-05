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

"""Utilities for generating GCS V4 Signed URLs and compact artifact ZIP bundles."""

from __future__ import annotations

import concurrent.futures
import datetime
import json
import logging
import os
import re
import tempfile
import zipfile
from typing import Any

import google.auth
from google.auth import transport as auth_transport
from google.auth.transport import requests as auth_requests
from google.cloud import storage

logger = logging.getLogger(__name__)

SIGNED_URL_EXPIRATION_SECONDS = int(os.getenv("SIGNED_URL_EXPIRATION_SECONDS", "3600"))


def _sanitize_filename(name: str, fallback: str = "artifact") -> str:
    """Sanitize a filename for safe use in Content-Disposition headers and ZIP paths."""
    cleaned = re.sub(r"[^a-zA-Z0-9._-]", "_", str(name or fallback)).strip("._-")
    return cleaned or fallback


def parse_gcs_uri(gcs_uri: str) -> tuple[str, str]:
    """Split a gs://bucket/path URI into (bucket_name, blob_name)."""
    if not isinstance(gcs_uri, str) or not gcs_uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {gcs_uri!r}")
    parts = gcs_uri[5:].split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1] if len(parts) > 1 else ""
    if not bucket_name or not blob_name or ".." in blob_name.split("/"):
        raise ValueError(f"Invalid GCS URI path: {gcs_uri!r}")
    return bucket_name, blob_name


def prepare_signing_context(
    project_id: str | None = None,
    auth_Request: auth_transport.Request | None = None,
) -> tuple[storage.Client | None, Any | None]:
    """Initialize a storage.Client and refresh keyless ADC credentials once for batch URL signing."""
    try:
        credentials, default_project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        effective_project = project_id or default_project
        if not hasattr(credentials, "sign_bytes"):
            req = auth_Request or auth_requests.Request()
            if not getattr(credentials, "valid", False) or not getattr(credentials, "token", None):
                credentials.refresh(req)
        client = storage.Client(project=effective_project, credentials=credentials)
        return client, credentials
    except Exception as e:
        logger.warning(f"Could not initialize signing context: {e}")
        return None, None


def generate_signed_download_url(
    gcs_uri: str,
    download_filename: str | None = None,
    expiration_seconds: int = SIGNED_URL_EXPIRATION_SECONDS,
    project_id: str | None = None,
    content_type: str | None = None,
    auth_Request: auth_transport.Request | None = None,
    storage_client: storage.Client | None = None,
    credentials: Any | None = None,
) -> str | None:
    """Generate a time-limited GCS V4 Signed URL with Content-Disposition: attachment.

    Supports both local service account keys (sign_bytes) and keyless Cloud Run /
    Agent Runtime credentials via IAM signBlob (service_account_email + access_token).
    Accepts pre-initialized `storage_client` and `credentials` to avoid repeated
    metadata server round-trips during batch URL generation.
    """
    try:
        bucket_name, blob_name = parse_gcs_uri(gcs_uri)
        if storage_client is None or credentials is None:
            init_client, init_creds = prepare_signing_context(
                project_id=project_id,
                auth_Request=auth_Request,
            )
            storage_client = storage_client or init_client
            credentials = credentials or init_creds

        if storage_client is None:
            return None

        blob = storage_client.bucket(bucket_name).blob(blob_name)

        safe_filename = _sanitize_filename(download_filename or blob_name.rsplit("/", 1)[-1])
        disposition = f'attachment; filename="{safe_filename}"'

        kwargs: dict[str, Any] = {
            "version": "v4",
            "expiration": datetime.timedelta(seconds=expiration_seconds),
            "method": "GET",
            "response_disposition": disposition,
        }
        if content_type:
            kwargs["response_type"] = content_type

        # If credentials do not have a local private key (e.g. Compute Engine / Cloud Run ADC),
        # refresh token if needed and pass service_account_email + access_token to invoke IAM signBlob.
        if credentials is not None and not hasattr(credentials, "sign_bytes"):
            if not getattr(credentials, "valid", False) or not getattr(credentials, "token", None):
                req = auth_Request or auth_requests.Request()
                credentials.refresh(req)
            sa_email = getattr(credentials, "service_account_email", None)
            if sa_email and getattr(credentials, "token", None):
                kwargs["service_account_email"] = sa_email
                kwargs["access_token"] = credentials.token

        return blob.generate_signed_url(**kwargs)
    except Exception as e:
        logger.warning(f"Could not generate V4 signed URL for {gcs_uri}: {e}")
        return None


def build_expert_analysis_markdown(summary_data: dict[str, Any]) -> str:
    """Build a standalone Markdown report from summary.json."""
    job_id = summary_data.get("job_id", "unknown")
    model_type = summary_data.get("model_type", "unknown")
    analyzed_at = summary_data.get("analyzed_at", "")
    protein_info = summary_data.get("summary", {}).get("protein_info", {})
    job_meta = protein_info.get("job_metadata", {})
    display_name = job_meta.get("display_name") or job_id

    lines = [
        f"# FoldRun Analysis Report: {display_name}",
        "",
        f"- **Job ID**: `{job_id}`",
        f"- **Model**: `{model_type}`",
        f"- **Analyzed At**: `{analyzed_at}`",
    ]
    if protein_info.get("sequence_length"):
        lines.append(f"- **Sequence / Token Length**: `{protein_info['sequence_length']}`")
    if job_meta.get("duration_formatted"):
        lines.append(f"- **Pipeline Duration**: `{job_meta['duration_formatted']}`")

    best = summary_data.get("best_prediction") or {}
    if best:
        lines.extend(["", "## Top-Ranked Prediction Summary", ""])
        name = best.get("sample_name") or best.get("model_name") or "Rank 1"
        lines.append(f"- **Prediction**: `{name}`")
        if best.get("plddt_mean") is not None:
            lines.append(f"- **Mean pLDDT**: `{best['plddt_mean']:.2f}`")
        if best.get("ranking_score") is not None:
            lines.append(f"- **Ranking Score**: `{best['ranking_score']:.4f}`")
        if best.get("ranking_confidence") is not None:
            lines.append(f"- **Ranking Confidence**: `{best['ranking_confidence']:.4f}`")
        if best.get("ptm") is not None:
            lines.append(f"- **pTM**: `{best['ptm']:.4f}`")
        if best.get("iptm") is not None:
            lines.append(f"- **ipTM**: `{best['iptm']:.4f}`")
        if best.get("pae_mean") is not None:
            lines.append(f"- **Mean PAE**: `{best['pae_mean']:.2f} Å`")
        if best.get("quality_assessment"):
            lines.append(f"- **Quality Assessment**: `{best['quality_assessment']}`")

    expert = summary_data.get("expert_analysis") or {}
    expert_text = expert.get("analysis") if isinstance(expert, dict) else None
    if expert_text:
        lines.extend(["", "## Gemini Expert Analysis", "", str(expert_text).strip(), ""])

    return "\n".join(lines) + "\n"


def ensure_run_archive_in_gcs(
    analysis_path: str,
    summary_data: dict[str, Any],
    project_id: str | None = None,
) -> str | None:
    """Return the GCS URI of the job's compact ZIP archive, creating it on-demand if missing."""
    if not analysis_path.endswith("/"):
        analysis_path += "/"

    bundle_uri = summary_data.get("artifacts_bundle_uri") or (
        f"{analysis_path}artifacts_bundle.zip"
    )
    try:
        bucket_name, bundle_blob_name = parse_gcs_uri(bundle_uri)
        storage_client = storage.Client(project=project_id)
        bucket = storage_client.bucket(bucket_name)
        bundle_blob = bucket.blob(bundle_blob_name)
        if bundle_blob.exists():
            return bundle_uri

        logger.info(f"Archive bundle not yet present at {bundle_uri}; building on-demand...")
        download_tasks: list[tuple[str, str]] = []
        all_preds = list(summary_data.get("all_predictions_summary", []))
        all_preds.sort(key=lambda p: p.get("plddt_rank") or p.get("rank") or 999)

        for idx, pred in enumerate(all_preds):
            rank = int(pred.get("plddt_rank") or pred.get("rank") or (idx + 1))
            label = _sanitize_filename(
                pred.get("sample_name") or pred.get("model_name") or f"model_{rank}"
            )
            rank_prefix = f"rank_{rank:02d}_{label}"

            if pred.get("cif_uri"):
                download_tasks.append((f"structures/{rank_prefix}.cif", pred["cif_uri"]))
            elif pred.get("pdb_uri") or pred.get("uri"):
                raw_uri = str(pred.get("pdb_uri") or pred.get("uri"))
                if raw_uri.endswith("/raw_prediction.pkl"):
                    download_tasks.append(
                        (
                            f"structures/{rank_prefix}_unrelaxed.pdb",
                            raw_uri.replace("/raw_prediction.pkl", "/unrelaxed_protein.pdb"),
                        )
                    )
                    download_tasks.append(
                        (
                            f"structures/{rank_prefix}_relaxed.pdb",
                            raw_uri.replace("/raw_prediction.pkl", "/relaxed_protein.pdb"),
                        )
                    )
                elif raw_uri.endswith(".pdb"):
                    download_tasks.append((f"structures/{rank_prefix}.pdb", raw_uri))

            plots = pred.get("plots") or {}
            for plot_key, plot_uri in plots.items():
                if not plot_uri:
                    continue
                clean_key = _sanitize_filename(plot_key.replace("_plot", ""))
                download_tasks.append((f"plots/{rank_prefix}_{clean_key}.png", plot_uri))

        def _fetch_member(item: tuple[str, str]) -> tuple[str, bytes | None]:
            arc_path, uri = item
            try:
                b_name, bl_name = parse_gcs_uri(uri)
                if b_name != bucket_name:
                    return arc_path, None
                bl = bucket.blob(bl_name)
                if not bl.exists():
                    return arc_path, None
                return arc_path, bl.download_as_bytes()
            except Exception:
                return arc_path, None

        fetched_items: list[tuple[str, bytes]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for arc_path, data in pool.map(_fetch_member, download_tasks):
                if data is not None:
                    fetched_items.append((arc_path, data))

        protein_info = summary_data.get("summary", {}).get("protein_info", {})
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_zip:
            tmp_zip_path = tmp_zip.name

        try:
            with zipfile.ZipFile(tmp_zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                report_md = build_expert_analysis_markdown(summary_data)
                zf.writestr("report/expert_analysis.md", report_md.encode("utf-8"))

                fasta_seq = protein_info.get("fasta_sequence")
                if fasta_seq:
                    header = protein_info.get("fasta_header") or summary_data.get(
                        "job_id", "sequence"
                    )
                    zf.writestr(
                        "input/sequence.fasta",
                        f">{header}\n{fasta_seq}\n".encode(),
                    )

                if protein_info.get("input_query_json"):
                    zf.writestr(
                        "input/query.json",
                        json.dumps(protein_info["input_query_json"], indent=2).encode("utf-8"),
                    )

                if protein_info.get("input_query_yaml"):
                    yaml_val = protein_info["input_query_yaml"]
                    yaml_str = (
                        yaml_val if isinstance(yaml_val, str) else json.dumps(yaml_val, indent=2)
                    )
                    zf.writestr("input/query.yaml", yaml_str.encode("utf-8"))

                affinity = summary_data.get("summary", {}).get("affinity")
                if affinity:
                    zf.writestr(
                        "metrics/affinity.json",
                        json.dumps(affinity, indent=2).encode("utf-8"),
                    )

                for arc_path, content_bytes in fetched_items:
                    zf.writestr(arc_path, content_bytes)

                summary_copy = dict(summary_data)
                summary_copy["artifacts_bundle_uri"] = bundle_uri
                zf.writestr(
                    "metrics/summary.json",
                    json.dumps(summary_copy, indent=2).encode("utf-8"),
                )

            bundle_blob.upload_from_filename(tmp_zip_path, content_type="application/zip")
            return bundle_uri
        finally:
            if os.path.exists(tmp_zip_path):
                os.remove(tmp_zip_path)
    except Exception as e:
        logger.warning(f"Failed to ensure archive bundle at {bundle_uri}: {e}")
        return None


def build_agent_downloads_dict(
    job_id: str,
    analysis_path: str,
    summary_data: dict[str, Any],
    project_id: str | None = None,
    expiration_seconds: int = SIGNED_URL_EXPIRATION_SECONDS,
    ensure_bundle: bool = True,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Build a dictionary of V4 Signed URLs for the ZIP bundle, best structure, and plots."""
    if not analysis_path.endswith("/"):
        analysis_path += "/"

    protein_info = summary_data.get("summary", {}).get("protein_info", {})
    display_name = protein_info.get("job_metadata", {}).get("display_name") or job_id
    safe_job_label = _sanitize_filename(display_name, fallback=job_id)

    downloads: dict[str, Any] = {
        "expires_in_seconds": expiration_seconds,
        "expires_in_minutes": round(expiration_seconds / 60),
    }

    # Initialize GCS client and credentials once for the entire batch of signed URLs
    signing_client, signing_creds = prepare_signing_context(project_id=project_id)

    # 1. Full ZIP archive bundle
    bundle_uri = summary_data.get("artifacts_bundle_uri")
    if not bundle_uri and ensure_bundle:
        bundle_uri = ensure_run_archive_in_gcs(
            analysis_path=analysis_path,
            summary_data=summary_data,
            project_id=project_id,
        )
    elif bundle_uri and ensure_bundle:
        # Verify it exists or build on-demand
        bundle_uri = ensure_run_archive_in_gcs(
            analysis_path=analysis_path,
            summary_data=summary_data,
            project_id=project_id,
        )

    if bundle_uri:
        downloads["artifacts_bundle_uri"] = bundle_uri
        signed_zip = generate_signed_download_url(
            bundle_uri,
            download_filename=f"{safe_job_label}_artifacts.zip",
            expiration_seconds=expiration_seconds,
            project_id=project_id,
            content_type="application/zip",
            storage_client=signing_client,
            credentials=signing_creds,
        )
        if signed_zip:
            downloads["artifacts_bundle_signed_url"] = signed_zip

    # 2. Consolidated summary.json
    summary_uri = f"{analysis_path}summary.json"
    signed_summary = generate_signed_download_url(
        summary_uri,
        download_filename=f"{safe_job_label}_summary.json",
        expiration_seconds=expiration_seconds,
        project_id=project_id,
        content_type="application/json",
        storage_client=signing_client,
        credentials=signing_creds,
    )
    if signed_summary:
        downloads["summary_json_signed_url"] = signed_summary

    # 3. Best prediction structure & plots
    best = summary_data.get("best_prediction") or {}
    best_name = _sanitize_filename(best.get("sample_name") or best.get("model_name") or "rank_01")
    best_struct_uri = best.get("cif_uri")
    best_ext = "cif"
    if not best_struct_uri and (best.get("pdb_uri") or best.get("uri")):
        raw_uri = str(best.get("pdb_uri") or best.get("uri"))
        best_struct_uri = (
            raw_uri.replace("/raw_prediction.pkl", "/unrelaxed_protein.pdb")
            if raw_uri.endswith("/raw_prediction.pkl")
            else raw_uri
        )
        best_ext = "pdb"

    if best_struct_uri:
        downloads["best_structure_uri"] = best_struct_uri
        signed_best = generate_signed_download_url(
            best_struct_uri,
            download_filename=f"{safe_job_label}_rank_01_{best_name}.{best_ext}",
            expiration_seconds=expiration_seconds,
            project_id=project_id,
            storage_client=signing_client,
            credentials=signing_creds,
        )
        if signed_best:
            downloads["best_structure_signed_url"] = signed_best

    best_plots_signed: dict[str, str] = {}
    for plot_key, plot_uri in (best.get("plots") or {}).items():
        if not plot_uri:
            continue
        clean_key = _sanitize_filename(plot_key.replace("_plot", ""))
        signed_plot = generate_signed_download_url(
            plot_uri,
            download_filename=f"{safe_job_label}_rank_01_{best_name}_{clean_key}.png",
            expiration_seconds=expiration_seconds,
            project_id=project_id,
            content_type="image/png",
            storage_client=signing_client,
            credentials=signing_creds,
        )
        if signed_plot:
            best_plots_signed[plot_key] = signed_plot
    if best_plots_signed:
        downloads["best_plots_signed_urls"] = best_plots_signed

    # 4. Top predictions (up to 5)
    all_preds = list(summary_data.get("all_predictions_summary", []))
    all_preds.sort(key=lambda p: p.get("plddt_rank") or p.get("rank") or 999)
    top_downloads: list[dict[str, Any]] = []
    for idx, pred in enumerate(all_preds[:5]):
        rank = int(pred.get("plddt_rank") or pred.get("rank") or (idx + 1))
        pred_name = _sanitize_filename(
            pred.get("sample_name") or pred.get("model_name") or f"model_{rank}"
        )
        struct_uri = pred.get("cif_uri")
        ext = "cif"
        if not struct_uri and (pred.get("pdb_uri") or pred.get("uri")):
            r_uri = str(pred.get("pdb_uri") or pred.get("uri"))
            struct_uri = (
                r_uri.replace("/raw_prediction.pkl", "/unrelaxed_protein.pdb")
                if r_uri.endswith("/raw_prediction.pkl")
                else r_uri
            )
            ext = "pdb"

        entry: dict[str, Any] = {
            "rank": rank,
            "name": pred.get("sample_name") or pred.get("model_name") or pred_name,
        }
        if struct_uri:
            signed_s = generate_signed_download_url(
                struct_uri,
                download_filename=f"{safe_job_label}_rank_{rank:02d}_{pred_name}.{ext}",
                expiration_seconds=expiration_seconds,
                project_id=project_id,
                storage_client=signing_client,
                credentials=signing_creds,
            )
            if signed_s:
                entry["structure_signed_url"] = signed_s

        if include_raw:
            raw_uri = pred.get("confidences_uri") or pred.get("uri")
            if raw_uri:
                raw_ext = raw_uri.rsplit(".", 1)[-1] if "." in raw_uri else "dat"
                signed_raw = generate_signed_download_url(
                    raw_uri,
                    download_filename=f"{safe_job_label}_rank_{rank:02d}_{pred_name}_raw.{raw_ext}",
                    expiration_seconds=expiration_seconds,
                    project_id=project_id,
                    storage_client=signing_client,
                    credentials=signing_creds,
                )
                if signed_raw:
                    entry["raw_artifact_signed_url"] = signed_raw

        top_downloads.append(entry)

    if top_downloads:
        downloads["top_predictions"] = top_downloads

    return downloads


def _build_af3_archive_and_downloads(
    job_id: str,
    bucket_name: str,
    project_id: str | None = None,
    expiration_seconds: int = SIGNED_URL_EXPIRATION_SECONDS,
) -> dict[str, Any] | None:
    """Check if job_id corresponds to an AlphaFold 3 job under af3_predictions/<job_id>/ and build bundle."""
    safe_id = _sanitize_filename(job_id)
    prefix = f"af3_predictions/{safe_id}"
    storage_client = storage.Client(project=project_id)
    bucket = storage_client.bucket(bucket_name)

    blobs = list(bucket.list_blobs(prefix=f"{prefix}/"))
    if not blobs:
        return None

    bundle_blob_name = f"{prefix}/artifacts_bundle.zip"
    bundle_uri = f"gs://{bucket_name}/{bundle_blob_name}"
    bundle_blob = bucket.blob(bundle_blob_name)

    if not bundle_blob.exists():
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_zip:
            tmp_zip_path = tmp_zip.name
        try:
            with zipfile.ZipFile(tmp_zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for b in blobs:
                    rel_name = b.name[len(prefix) + 1 :]
                    if not rel_name or rel_name.endswith(".zip"):
                        continue
                    if rel_name.endswith(".cif"):
                        arc_name = f"structures/{rel_name}"
                    elif rel_name == "input.json":
                        arc_name = f"input/{rel_name}"
                    else:
                        arc_name = f"metrics/{rel_name}"
                    zf.writestr(arc_name, b.download_as_bytes())
            bundle_blob.upload_from_filename(tmp_zip_path, content_type="application/zip")
        finally:
            if os.path.exists(tmp_zip_path):
                os.remove(tmp_zip_path)

    signing_client, signing_creds = prepare_signing_context(project_id=project_id)

    downloads: dict[str, Any] = {
        "expires_in_seconds": expiration_seconds,
        "expires_in_minutes": round(expiration_seconds / 60),
        "artifacts_bundle_uri": bundle_uri,
    }
    signed_zip = generate_signed_download_url(
        bundle_uri,
        download_filename=f"{safe_id}_af3_artifacts.zip",
        expiration_seconds=expiration_seconds,
        project_id=project_id,
        content_type="application/zip",
        storage_client=signing_client,
        credentials=signing_creds,
    )
    if signed_zip:
        downloads["artifacts_bundle_signed_url"] = signed_zip

    for b in blobs:
        rel_name = b.name[len(prefix) + 1 :]
        uri = f"gs://{bucket_name}/{b.name}"
        if rel_name.endswith(".cif"):
            downloads["best_structure_uri"] = uri
            signed_cif = generate_signed_download_url(
                uri,
                download_filename=rel_name,
                expiration_seconds=expiration_seconds,
                project_id=project_id,
                storage_client=signing_client,
                credentials=signing_creds,
            )
            if signed_cif:
                downloads["best_structure_signed_url"] = signed_cif
        elif rel_name.endswith("summary_confidences.json"):
            signed_conf = generate_signed_download_url(
                uri,
                download_filename=rel_name,
                expiration_seconds=expiration_seconds,
                project_id=project_id,
                content_type="application/json",
                storage_client=signing_client,
                credentials=signing_creds,
            )
            if signed_conf:
                downloads["summary_json_signed_url"] = signed_conf

    return {
        "status": "success",
        "job_id": job_id,
        "model_type": "af3",
        "downloads": downloads,
    }


def download_job_artifacts_for_job(
    job_id: str,
    project_id: str,
    region: str,
    bucket_name: str | None = None,
    include_raw: bool = False,
    expiration_minutes: int = 60,
) -> dict[str, Any]:
    """Generate V4 Signed URLs for a job's ZIP archive bundle and key individual artifacts."""
    if not job_id or not re.match(r"^[a-zA-Z0-9_-]+$", str(job_id)):
        return {
            "status": "error",
            "job_id": job_id,
            "message": "Invalid job_id format. Only alphanumeric characters, hyphens, and underscores are allowed.",
        }

    expiration_seconds = max(60, min(int(expiration_minutes) * 60, 7 * 24 * 3600))

    # 1. Try resolving as a Vertex AI / Agent Platform PipelineJob (AF2, OF3, Boltz-2)
    from foldrun_app.core.vertex_utils import get_pipeline_job

    job = None
    try:
        job = get_pipeline_job(job_id, project_id, region)
    except Exception:
        job = None

    if job is not None:
        pipeline_root = None
        if hasattr(job, "runtime_config") and job.runtime_config:
            if (
                hasattr(job.runtime_config, "gcs_output_directory")
                and job.runtime_config.gcs_output_directory
            ):
                pipeline_root = job.runtime_config.gcs_output_directory

        if not pipeline_root:
            return {
                "status": "error",
                "job_id": job_id,
                "message": "Pipeline job does not have a gcs_output_directory.",
            }

        if not pipeline_root.endswith("/"):
            pipeline_root += "/"
        analysis_path = f"{pipeline_root}analysis/"
        summary_uri = f"{analysis_path}summary.json"

        try:
            b_name, s_blob_name = parse_gcs_uri(summary_uri)
            storage_client = storage.Client(project=project_id)
            s_blob = storage_client.bucket(b_name).blob(s_blob_name)
            if s_blob.exists():
                summary_data = json.loads(s_blob.download_as_text())
                downloads = build_agent_downloads_dict(
                    job_id=job_id,
                    analysis_path=analysis_path,
                    summary_data=summary_data,
                    project_id=project_id,
                    expiration_seconds=expiration_seconds,
                    ensure_bundle=True,
                    include_raw=include_raw,
                )
                return {
                    "status": "success",
                    "job_id": job_id,
                    "model_type": summary_data.get("model_type", "unknown"),
                    "downloads": downloads,
                }
        except Exception as e:
            logger.warning(f"Could not load summary.json for {job_id}: {e}")

        # Fallback for unanalyzed pipeline jobs: package structures directly from pipeline_root
        try:
            b_name, root_prefix = parse_gcs_uri(pipeline_root)
            storage_client = storage.Client(project=project_id)
            bucket = storage_client.bucket(b_name)
            blobs = list(bucket.list_blobs(prefix=root_prefix))
            struct_blobs = [
                b for b in blobs if b.name.endswith((".cif", ".pdb")) and "/analysis/" not in b.name
            ]
            if not struct_blobs:
                return {
                    "status": "not_ready",
                    "job_id": job_id,
                    "message": (
                        f"No structure predictions or analysis summary found yet for {job_id}. "
                        "Ensure the pipeline has succeeded and run analysis first."
                    ),
                }

            safe_id = _sanitize_filename(job.display_name or job_id, fallback=job_id)
            bundle_blob_name = f"{root_prefix}analysis/artifacts_bundle.zip"
            bundle_uri = f"gs://{b_name}/{bundle_blob_name}"
            bundle_blob = bucket.blob(bundle_blob_name)

            if not bundle_blob.exists():
                with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_zip:
                    tmp_zip_path = tmp_zip.name
                try:
                    with zipfile.ZipFile(
                        tmp_zip_path, mode="w", compression=zipfile.ZIP_DEFLATED
                    ) as zf:
                        for idx, sb in enumerate(struct_blobs, start=1):
                            fname = _sanitize_filename(sb.name.rsplit("/", 1)[-1])
                            zf.writestr(
                                f"structures/{idx:02d}_{fname}",
                                sb.download_as_bytes(),
                            )
                    bundle_blob.upload_from_filename(tmp_zip_path, content_type="application/zip")
                finally:
                    if os.path.exists(tmp_zip_path):
                        os.remove(tmp_zip_path)

            signing_client, signing_creds = prepare_signing_context(project_id=project_id)

            downloads: dict[str, Any] = {
                "expires_in_seconds": expiration_seconds,
                "expires_in_minutes": round(expiration_seconds / 60),
                "artifacts_bundle_uri": bundle_uri,
            }
            signed_zip = generate_signed_download_url(
                bundle_uri,
                download_filename=f"{safe_id}_artifacts.zip",
                expiration_seconds=expiration_seconds,
                project_id=project_id,
                content_type="application/zip",
                storage_client=signing_client,
                credentials=signing_creds,
            )
            if signed_zip:
                downloads["artifacts_bundle_signed_url"] = signed_zip

            first_uri = f"gs://{b_name}/{struct_blobs[0].name}"
            downloads["best_structure_uri"] = first_uri
            signed_first = generate_signed_download_url(
                first_uri,
                download_filename=struct_blobs[0].name.rsplit("/", 1)[-1],
                expiration_seconds=expiration_seconds,
                project_id=project_id,
                storage_client=signing_client,
                credentials=signing_creds,
            )
            if signed_first:
                downloads["best_structure_signed_url"] = signed_first

            return {
                "status": "success",
                "job_id": job_id,
                "model_type": (job.labels or {}).get("model_type", "unknown")
                if hasattr(job, "labels")
                else "unknown",
                "note": "Job has not been analyzed yet; bundle contains raw predicted structures.",
                "downloads": downloads,
            }
        except Exception as e:
            return {
                "status": "error",
                "job_id": job_id,
                "message": f"Failed to package artifacts for {job_id}: {e!s}",
            }

    # 2. Try resolving as an AlphaFold 3 job in af3_predictions/<job_id>/
    if bucket_name:
        af3_res = _build_af3_archive_and_downloads(
            job_id=job_id,
            bucket_name=bucket_name,
            project_id=project_id,
            expiration_seconds=expiration_seconds,
        )
        if af3_res is not None:
            return af3_res

    return {
        "status": "not_found",
        "job_id": job_id,
        "message": f"Could not find pipeline job or AF3 prediction artifacts for '{job_id}'.",
    }
