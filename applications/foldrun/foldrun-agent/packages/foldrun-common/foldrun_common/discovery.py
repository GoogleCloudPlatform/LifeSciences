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

"""Shared GCS prediction sample discovery and task_config builders across Agent and Viewer."""

from __future__ import annotations

import logging
import re
from typing import Any

from .gcs import JOB_ID_PATTERN, validate_gcs_uri
from .models import (
    AF2PredictionTask,
    AF3PredictionTask,
    AnalysisTaskConfig,
    Boltz2PredictionTask,
    OF3PredictionTask,
)

logger = logging.getLogger(__name__)


def _parse_pipeline_root(
    pipeline_root: str,
    *,
    expected_bucket: str | None = None,
    allowed_prefixes: tuple[str, ...] | None = None,
) -> tuple[str, str]:
    bucket_name, prefix = validate_gcs_uri(
        pipeline_root,
        expected_bucket=expected_bucket,
        allowed_prefixes=allowed_prefixes,
    )
    if not prefix.endswith("/"):
        prefix += "/"
    return bucket_name, prefix


def discover_af2_predictions_in_gcs(
    pipeline_root: str,
    storage_client: Any,
    *,
    expected_bucket: str | None = None,
    allowed_prefixes: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Scan GCS for AlphaFold2 ``raw_prediction.pkl`` artifacts under ``pipeline_root``."""
    bucket_name, prefix = _parse_pipeline_root(
        pipeline_root,
        expected_bucket=expected_bucket,
        allowed_prefixes=allowed_prefixes,
    )
    bucket = storage_client.bucket(bucket_name)
    predictions: list[dict[str, Any]] = []
    for blob in bucket.list_blobs(prefix=prefix):
        if blob.name.endswith("/raw_prediction.pkl"):
            uri = f"gs://{bucket_name}/{blob.name}"
            parts = blob.name.split("/")
            parent = parts[-2] if len(parts) >= 2 else "model"
            predictions.append(
                {
                    "uri": uri,
                    "model_name": parent,
                    "ranking_confidence": 0,
                }
            )
    return predictions


def discover_of3_predictions(
    pipeline_root: str,
    storage_client: Any,
    *,
    expected_bucket: str | None = None,
    allowed_prefixes: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Scan GCS for OpenFold3 ``*_confidences_aggregated.json`` sample outputs."""
    bucket_name, prefix = _parse_pipeline_root(
        pipeline_root,
        expected_bucket=expected_bucket,
        allowed_prefixes=allowed_prefixes,
    )
    bucket = storage_client.bucket(bucket_name)
    samples: list[dict[str, Any]] = []
    seen: set[str] = set()

    for blob in bucket.list_blobs(prefix=prefix):
        name = blob.name
        if name.endswith("_confidences_aggregated.json") and name not in seen:
            seen.add(name)
            base = name.removesuffix("_confidences_aggregated.json")
            sample_name = name.rsplit("/", 1)[-1].removesuffix("_confidences_aggregated.json")
            samples.append(
                {
                    "sample_name": sample_name,
                    "cif_uri": f"gs://{bucket_name}/{base}_model.cif",
                    "confidences_uri": f"gs://{bucket_name}/{base}_confidences.json",
                    "aggregated_uri": f"gs://{bucket_name}/{name}",
                }
            )

    logger.info(f"Discovered {len(samples)} OF3 samples")
    return samples


def discover_boltz2_predictions(
    pipeline_root: str,
    storage_client: Any,
    *,
    expected_bucket: str | None = None,
    allowed_prefixes: tuple[str, ...] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Scan GCS for Boltz-2 ``confidence_*.json`` samples and optional ``affinity_*.json``."""
    bucket_name, prefix = _parse_pipeline_root(
        pipeline_root,
        expected_bucket=expected_bucket,
        allowed_prefixes=allowed_prefixes,
    )
    bucket = storage_client.bucket(bucket_name)
    samples: list[dict[str, Any]] = []
    seen: set[str] = set()
    affinity_uri: str | None = None

    for blob in bucket.list_blobs(prefix=prefix):
        name = blob.name
        if "/predictions/" not in name or not name.endswith(".json"):
            continue
        parts_list = name.split("/")
        filename = parts_list[-1]

        if "/confidence_" in name and name not in seen:
            seen.add(name)
            cif_filename = filename.replace("confidence_", "", 1).replace(".json", ".cif")
            cif_name = "/".join([*parts_list[:-1], cif_filename])
            pde_filename = filename.replace("confidence_", "pde_", 1).replace(".json", ".npz")
            pde_name = "/".join([*parts_list[:-1], pde_filename])
            sample_name = filename.replace("confidence_", "", 1).removesuffix(".json")
            samples.append(
                {
                    "sample_name": sample_name,
                    "cif_uri": f"gs://{bucket_name}/{cif_name}",
                    "confidences_uri": "",
                    "aggregated_uri": f"gs://{bucket_name}/{name}",
                    "pde_uri": f"gs://{bucket_name}/{pde_name}",
                }
            )
        elif filename.startswith("affinity_") and affinity_uri is None:
            affinity_uri = f"gs://{bucket_name}/{name}"
            logger.info(f"Found affinity results: {affinity_uri}")

    logger.info(f"Discovered {len(samples)} BOLTZ2 samples")
    return samples, affinity_uri


def discover_af3_predictions(
    job_prefix_or_uri: str,
    storage_client: Any,
    *,
    default_bucket: str | None = None,
    expected_bucket: str | None = None,
    allowed_prefixes: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Scan GCS for AlphaFold 3 ``seed-*_sample-*`` outputs (``.cif[.zst]`` & ``.json[.zst]``)."""
    if job_prefix_or_uri.startswith("gs://"):
        bucket_name, parsed_prefix = validate_gcs_uri(
            job_prefix_or_uri,
            expected_bucket=expected_bucket,
            allowed_prefixes=allowed_prefixes,
        )
        base_prefix = f"{parsed_prefix.strip('/')}/"
    else:
        if not default_bucket:
            raise ValueError("default_bucket is required when job_prefix_or_uri is relative")
        bucket_name = default_bucket
        base_prefix = f"{job_prefix_or_uri.strip('/')}/"
        if allowed_prefixes and not any(
            base_prefix.startswith(prefix) for prefix in allowed_prefixes
        ):
            raise ValueError(
                f"Invalid GCS path: '{base_prefix}' must start with an allowed prefix {allowed_prefixes}"
            )

    bucket = storage_client.bucket(bucket_name)
    sample_blobs: dict[str, dict[str, str | None]] = {}
    top_cif: str | None = None
    top_sum: str | None = None
    top_conf: str | None = None

    for b in bucket.list_blobs(prefix=base_prefix):
        bname = b.name
        if "/analysis/" in bname:
            continue
        m_seed = re.search(r"/(seed-\d+_sample-\d+)/", bname)
        if m_seed:
            s_key = m_seed.group(1)
            s_entry = sample_blobs.setdefault(s_key, {})
            if bname.endswith(("_model.cif", "/model.cif", "_model.cif.zst", "/model.cif.zst")):
                s_entry["cif_uri"] = f"gs://{bucket_name}/{bname}"
            elif bname.endswith(
                (
                    "_summary_confidences.json",
                    "/summary_confidences.json",
                    "_summary_confidences.json.zst",
                    "/summary_confidences.json.zst",
                )
            ):
                s_entry["summary_uri"] = f"gs://{bucket_name}/{bname}"
            elif bname.endswith(
                (
                    "_confidences.json",
                    "/confidences.json",
                    "_confidences.json.zst",
                    "/confidences.json.zst",
                )
            ):
                s_entry["confidences_uri"] = f"gs://{bucket_name}/{bname}"
            continue

        if bname.endswith(("_model.cif", "_model.cif.zst")):
            top_cif = f"gs://{bucket_name}/{bname}"
        elif bname.endswith(("_summary_confidences.json", "_summary_confidences.json.zst")):
            top_sum = f"gs://{bucket_name}/{bname}"
        elif bname.endswith(("_confidences.json", "_confidences.json.zst")):
            top_conf = f"gs://{bucket_name}/{bname}"

    predictions: list[dict[str, Any]] = [
        {
            "sample_name": k.replace("-", "_"),
            "cif_uri": v["cif_uri"],
            "summary_uri": v["summary_uri"],
            "confidences_uri": v.get("confidences_uri"),
        }
        for k, v in sorted(sample_blobs.items())
        if v.get("cif_uri") and v.get("summary_uri")
    ]
    if not predictions and top_cif and top_sum:
        predictions = [
            {
                "sample_name": "seed_1_sample_0",
                "cif_uri": top_cif,
                "summary_uri": top_sum,
                "confidences_uri": top_conf,
            }
        ]
    return predictions


def resolve_boltz2_query_yaml_uri(
    *,
    bucket_name: str,
    parameter_values: dict[str, Any] | None = None,
    labels: dict[str, Any] | None = None,
    display_name: str | None = None,
    strict_label_validation: bool = False,
) -> str | None:
    """Resolve and validate the Boltz-2 input YAML URI under ``gs://<bucket>/boltz2_queries/``."""
    expected_prefix = f"gs://{bucket_name}/boltz2_queries/"
    param_vals = parameter_values or {}
    raw_query_uri = param_vals.get("query_json_path")
    if isinstance(raw_query_uri, str) and raw_query_uri.startswith(expected_prefix):
        rel = raw_query_uri[len(expected_prefix) :]
        segments = [seg for seg in rel.split("/") if seg]
        if (
            segments
            and ".." not in segments
            and "." not in segments
            and rel.lower().endswith((".yaml", ".yml"))
        ):
            return raw_query_uri

    lbls = labels or {}
    query_name = (
        lbls.get("query_name") or (display_name if not strict_label_validation else "") or ""
    )
    if not query_name or not isinstance(query_name, str):
        return None

    if "/" in query_name or ".." in query_name or not JOB_ID_PATTERN.fullmatch(query_name):
        if strict_label_validation:
            raise ValueError(
                f"Path traversal detected or invalid characters in query_name label: {query_name!r}"
            )
        return None

    return f"{expected_prefix}{query_name}.yaml"


def build_analysis_task_config(
    *,
    model_type: str,
    job_id: str,
    analysis_path: str,
    raw_predictions: list[dict[str, Any]],
    affinity_uri: str | None = None,
    query_yaml_uri: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> AnalysisTaskConfig:
    """Construct a validated ``AnalysisTaskConfig`` for AF2, OF3, Boltz-2, or AF3."""
    if model_type == "alphafold2":
        predictions_cfg = [
            AF2PredictionTask(
                index=i,
                uri=p["uri"],
                model_name=p["model_name"],
                ranking_confidence=float(p.get("ranking_confidence", 0.0)),
                output_uri=f"{analysis_path}prediction_{i}_analysis.json" if analysis_path else "",
            ).model_dump()
            for i, p in enumerate(raw_predictions)
        ]
    elif model_type == "openfold3":
        predictions_cfg = [
            OF3PredictionTask(
                index=i,
                sample_name=p["sample_name"],
                cif_uri=p["cif_uri"],
                confidences_uri=p.get("confidences_uri", ""),
                aggregated_uri=p["aggregated_uri"],
                output_uri=f"{analysis_path}prediction_{i}_analysis.json",
            ).model_dump()
            for i, p in enumerate(raw_predictions)
        ]
    elif model_type == "boltz2":
        predictions_cfg = [
            Boltz2PredictionTask(
                index=i,
                sample_name=p["sample_name"],
                cif_uri=p["cif_uri"],
                confidences_uri=p.get("confidences_uri", ""),
                aggregated_uri=p["aggregated_uri"],
                pde_uri=p.get("pde_uri", ""),
                output_uri=f"{analysis_path}prediction_{i}_analysis.json",
            ).model_dump()
            for i, p in enumerate(raw_predictions)
        ]
    elif model_type == "alphafold3":
        predictions_cfg = [
            AF3PredictionTask(
                index=i,
                sample_name=p["sample_name"],
                cif_uri=p["cif_uri"],
                summary_uri=p["summary_uri"],
                confidences_uri=p.get("confidences_uri"),
                output_uri=f"{analysis_path}prediction_{i}_analysis.json",
            ).model_dump()
            for i, p in enumerate(raw_predictions)
        ]
    else:
        raise ValueError(f"Unsupported model_type for task_config: {model_type}")

    payload: dict[str, Any] = {
        "model_type": model_type,
        "job_id": job_id,
        "analysis_path": analysis_path,
        "task_config_uri": f"{analysis_path}task_config.json" if analysis_path else None,
        "predictions": predictions_cfg,
    }
    if affinity_uri is not None or model_type == "boltz2":
        payload["affinity_uri"] = affinity_uri
    if query_yaml_uri is not None or model_type == "boltz2":
        payload["query_yaml_uri"] = query_yaml_uri
    if extra_fields:
        payload.update(extra_fields)

    return AnalysisTaskConfig(**payload)
