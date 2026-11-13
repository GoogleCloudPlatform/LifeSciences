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

from foldrun_common import (
    MAX_DECOMPRESSED_ZST_BYTES,
)
from foldrun_common import (
    build_expert_analysis_markdown as _common_build_expert_analysis_markdown,
)
from foldrun_common import (
    ensure_run_archive_in_gcs as _common_ensure_run_archive_in_gcs,
)
from foldrun_common import (
    maybe_decompress_zst as _common_maybe_decompress_zst,
)
from foldrun_common import (
    sanitize_filename as _common_sanitize_filename,
)
from foldrun_common import (
    validate_gcs_uri as _common_validate_gcs_uri,
)

logger = logging.getLogger(__name__)

SIGNED_URL_EXPIRATION_SECONDS = int(os.getenv("SIGNED_URL_EXPIRATION_SECONDS", "3600"))
MAX_ZST_DECOMPRESSED_BYTES = MAX_DECOMPRESSED_ZST_BYTES


def _maybe_decompress_zst(arc_path: str, raw_bytes: bytes) -> tuple[str, bytes]:
    """Decompress .zst bytes into plain text when zstandard is available; otherwise preserve .zst extension."""
    return _common_maybe_decompress_zst(arc_path, raw_bytes, max_bytes=MAX_ZST_DECOMPRESSED_BYTES)


def _sanitize_filename(name: str, fallback: str = "artifact") -> str:
    """Sanitize a filename for safe use in Content-Disposition headers and ZIP paths."""
    return _common_sanitize_filename(name, fallback=fallback)


def parse_gcs_uri(gcs_uri: str) -> tuple[str, str]:
    """Split a gs://bucket/path URI into (bucket_name, blob_name)."""
    return _common_validate_gcs_uri(gcs_uri)


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
    return _common_build_expert_analysis_markdown(summary_data)


def ensure_run_archive_in_gcs(
    analysis_path: str,
    summary_data: dict[str, Any],
    project_id: str | None = None,
) -> str | None:
    """Return the GCS URI of the job's compact ZIP archive, creating it on-demand if missing."""
    client = storage.Client(project=project_id)
    return _common_ensure_run_archive_in_gcs(
        analysis_path=analysis_path,
        summary_data=summary_data,
        project_id=project_id,
        storage_client=client,
    )


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
    best_ext = (
        "cif.zst"
        if best_struct_uri and str(best_struct_uri).lower().endswith(".cif.zst")
        else "cif"
    )
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
        ext = "cif.zst" if struct_uri and str(struct_uri).lower().endswith(".cif.zst") else "cif"
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
                    if rel_name.endswith(".cif") or rel_name.endswith(".cif.zst"):
                        arc_name, content_bytes = _maybe_decompress_zst(
                            f"structures/{rel_name}", b.download_as_bytes()
                        )
                        zf.writestr(arc_name, content_bytes)
                        continue
                    elif rel_name == "input.json":
                        arc_name = f"input/{rel_name}"
                        zf.writestr(arc_name, b.download_as_bytes())
                    else:
                        arc_name, content_bytes = _maybe_decompress_zst(
                            f"metrics/{rel_name}", b.download_as_bytes()
                        )
                        zf.writestr(arc_name, content_bytes)
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

    cif_blobs = [b for b in blobs if b.name[len(prefix) + 1 :].endswith((".cif", ".cif.zst"))]
    root_cifs = [b for b in cif_blobs if "/" not in b.name[len(prefix) + 1 :]]
    best_cif_blob = root_cifs[0] if root_cifs else (cif_blobs[0] if cif_blobs else None)
    if best_cif_blob is not None:
        rel_name = best_cif_blob.name[len(prefix) + 1 :]
        uri = f"gs://{bucket_name}/{best_cif_blob.name}"
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

    conf_blobs = [
        b
        for b in blobs
        if b.name[len(prefix) + 1 :].endswith(
            ("summary_confidences.json", "summary_confidences.json.zst")
        )
    ]
    root_confs = [b for b in conf_blobs if "/" not in b.name[len(prefix) + 1 :]]
    best_conf_blob = root_confs[0] if root_confs else (conf_blobs[0] if conf_blobs else None)
    if best_conf_blob is not None:
        rel_name = best_conf_blob.name[len(prefix) + 1 :]
        uri = f"gs://{bucket_name}/{best_conf_blob.name}"
        content_type = "application/zstd" if rel_name.endswith(".zst") else "application/json"
        signed_conf = generate_signed_download_url(
            uri,
            download_filename=rel_name,
            expiration_seconds=expiration_seconds,
            project_id=project_id,
            content_type=content_type,
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
    from foldrun_app.core.vertex_utils import extract_short_job_id, get_pipeline_job

    target_job_id = str(job_id) if job_id is not None else ""
    job = None
    try:
        job = get_pipeline_job(target_job_id, project_id, region)
    except ValueError as e:
        return {
            "status": "error",
            "job_id": job_id,
            "message": f"Invalid job_id format: {e}",
        }
    except Exception:
        job = None

    if job is not None:
        job_res_name = getattr(job, "name", None)
        job_id = (
            job_res_name.rsplit("/", 1)[-1]
            if isinstance(job_res_name, str) and job_res_name
            else extract_short_job_id(target_job_id, project_id, region)
        )
    elif isinstance(job_id, str) and job_id.startswith("projects/"):
        try:
            job_id = extract_short_job_id(job_id, project_id, region)
        except ValueError as e:
            return {
                "status": "error",
                "job_id": job_id,
                "message": f"Invalid job_id format: {e}",
            }

    if not job_id or not re.match(r"^[a-zA-Z0-9_-]+$", str(job_id)):
        return {
            "status": "error",
            "job_id": job_id,
            "message": "Invalid job_id format. Only alphanumeric characters, hyphens, and underscores are allowed.",
        }

    expiration_seconds = max(60, min(int(expiration_minutes) * 60, 7 * 24 * 3600))

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
                b
                for b in blobs
                if b.name.endswith((".cif", ".pdb", ".cif.zst", ".pdb.zst"))
                and "/analysis/" not in b.name
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
                            arc_name, content_bytes = _maybe_decompress_zst(
                                f"structures/{idx:02d}_{fname}",
                                sb.download_as_bytes(),
                            )
                            zf.writestr(arc_name, content_bytes)
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
