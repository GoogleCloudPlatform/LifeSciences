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

"""Unit tests for the shared foldrun-common package."""

from __future__ import annotations

import io
import zipfile
from unittest.mock import MagicMock

import pytest
import zstandard as zstd

from foldrun_common import (
    AnalysisSummary,
    AnalysisTaskConfig,
    SampleAnalysisResult,
    build_analysis_task_config,
    build_and_upload_run_archive,
    build_expert_analysis_markdown,
    cleanup_stale_analysis_blobs,
    decompress_zst_bytes,
    discover_af2_predictions_in_gcs,
    discover_af3_predictions,
    discover_boltz2_predictions,
    discover_of3_predictions,
    ensure_run_archive_in_gcs,
    infer_model_type_from_display_name,
    normalize_model_type,
    resolve_boltz2_query_yaml_uri,
    sanitize_json_floats,
    validate_gcs_uri,
)


def _mock_blob(name: str) -> MagicMock:
    b = MagicMock()
    b.name = name
    return b


class TestModelsAndNormalization:
    """Tests for Pydantic v2 schemas and model_type helpers."""

    def test_model_type_normalization_and_inference(self) -> None:
        assert normalize_model_type("af2") == "alphafold2"
        assert normalize_model_type("OF3") == "openfold3"
        assert normalize_model_type("boltz-2") == "boltz2"
        assert normalize_model_type("af3") == "alphafold3"
        assert normalize_model_type(None) == ""

        assert infer_model_type_from_display_name("openfold3-complex-run") == "openfold3"
        assert infer_model_type_from_display_name("boltz2-kinase-01") == "boltz2"
        assert infer_model_type_from_display_name("af3-dimer") == "alphafold3"
        assert infer_model_type_from_display_name("monomer-20261002") == "alphafold2"

    def test_pydantic_schemas_preserve_extra_fields(self) -> None:
        sample = SampleAnalysisResult(
            prediction_index=0,
            sample_name="seed_1_sample_0",
            plddt_mean=91.4,
            ranking_score=0.92,
            custom_extra_metric=42,
        )
        dumped = sample.model_dump()
        assert dumped["sample_name"] == "seed_1_sample_0"
        assert dumped["custom_extra_metric"] == 42

        summary = AnalysisSummary(
            job_id="job-123",
            model_type="openfold3",
            analyzed_at="2026-10-02T12:00:00Z",
            total_predictions=1,
            best_prediction=dumped,
            legacy_field="preserved",
        )
        assert summary.model_dump()["legacy_field"] == "preserved"


class TestGcsSecurityAndZstd:
    """Tests for GCS URI validation, zstd bounds, NaN scrubbing, and stale blob cleanup."""

    def test_validate_gcs_uri_enforces_bucket_prefix_and_traversal(self) -> None:
        bucket, path = validate_gcs_uri(
            "gs://my-bucket/pipeline_runs/run_1/summary.json",
            expected_bucket="my-bucket",
            allowed_prefixes=("pipeline_runs/",),
            allowed_extensions=(".json",),
        )
        assert bucket == "my-bucket"
        assert path == "pipeline_runs/run_1/summary.json"

        with pytest.raises(ValueError, match="Unauthorized GCS bucket"):
            validate_gcs_uri("gs://evil/pipeline_runs/a.json", expected_bucket="my-bucket")

        with pytest.raises(ValueError, match="Path traversal"):
            validate_gcs_uri("gs://my-bucket/pipeline_runs/../secret.json")

        with pytest.raises(ValueError, match="allowed prefix"):
            validate_gcs_uri(
                "gs://my-bucket/other/summary.json",
                allowed_prefixes=("pipeline_runs/",),
            )

    def test_sanitize_json_floats_and_zst_roundtrip(self) -> None:
        cleaned = sanitize_json_floats({"nan": float("nan"), "inf": float("inf"), "ok": 0.95})
        assert cleaned == {"nan": None, "inf": None, "ok": 0.95}

        compressed = zstd.ZstdCompressor().compress(b'{"hello": "world"}')
        assert decompress_zst_bytes(compressed) == b'{"hello": "world"}'

        oversize = zstd.ZstdCompressor().compress(b"X" * 2048)
        with pytest.raises(ValueError, match="exceeds safety limit"):
            decompress_zst_bytes(oversize, max_bytes=512)

    def test_cleanup_stale_analysis_blobs(self) -> None:
        stale_1 = _mock_blob("pipeline_runs/r1/analysis/prediction_0_analysis.json")
        stale_2 = _mock_blob("pipeline_runs/r1/analysis/summary.json")
        keep = _mock_blob("pipeline_runs/r1/analysis/analysis_metadata.json")
        mock_bucket = MagicMock()
        mock_bucket.list_blobs.return_value = [stale_1, stale_2, keep]

        deleted = cleanup_stale_analysis_blobs(mock_bucket, "pipeline_runs/r1/analysis")
        assert deleted == 2
        stale_1.delete.assert_called_once()
        stale_2.delete.assert_called_once()
        keep.delete.assert_not_called()


class TestDiscoveryAndTaskConfig:
    """Tests for AF2, OF3, Boltz-2, and AF3 GCS sample discovery."""

    def test_discover_all_four_models_and_build_task_configs(self) -> None:
        mock_client = MagicMock()
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        # AF2
        mock_bucket.list_blobs.return_value = [
            _mock_blob("pipeline_runs/r1/predict/model_1_pred_0/raw_prediction.pkl")
        ]
        af2_preds = discover_af2_predictions_in_gcs("gs://bkt/pipeline_runs/r1", mock_client)
        assert len(af2_preds) == 1
        af2_cfg = build_analysis_task_config(
            model_type="alphafold2",
            job_id="j1",
            analysis_path="gs://bkt/pipeline_runs/r1/analysis/",
            raw_predictions=af2_preds,
        )
        assert isinstance(af2_cfg, AnalysisTaskConfig)
        assert af2_cfg.predictions[0]["model_name"] == "model_1_pred_0"

        # OF3
        mock_bucket.list_blobs.return_value = [
            _mock_blob("pipeline_runs/r2/seed_1/s0_confidences_aggregated.json")
        ]
        of3_preds = discover_of3_predictions("gs://bkt/pipeline_runs/r2", mock_client)
        assert len(of3_preds) == 1
        assert of3_preds[0]["cif_uri"] == "gs://bkt/pipeline_runs/r2/seed_1/s0_model.cif"

        # Boltz-2
        mock_bucket.list_blobs.return_value = [
            _mock_blob("pipeline_runs/r3/predictions/q/confidence_q_model_0.json"),
            _mock_blob("pipeline_runs/r3/predictions/q/affinity_q.json"),
        ]
        boltz_preds, aff_uri = discover_boltz2_predictions("gs://bkt/pipeline_runs/r3", mock_client)
        assert len(boltz_preds) == 1
        assert aff_uri == "gs://bkt/pipeline_runs/r3/predictions/q/affinity_q.json"
        query_uri = resolve_boltz2_query_yaml_uri(
            bucket_name="bkt", labels={"query_name": "kinase_01"}
        )
        assert query_uri == "gs://bkt/boltz2_queries/kinase_01.yaml"

        # AF3
        mock_bucket.list_blobs.return_value = [
            _mock_blob("af3_predictions/job_af3/seed-1_sample-0/model.cif.zst"),
            _mock_blob("af3_predictions/job_af3/seed-1_sample-0/summary_confidences.json.zst"),
        ]
        af3_preds = discover_af3_predictions(
            "af3_predictions/job_af3", mock_client, default_bucket="bkt"
        )
        assert len(af3_preds) == 1
        assert af3_preds[0]["sample_name"] == "seed_1_sample_0"


class TestReportAndArchive:
    """Tests for standalone Markdown report and compact ZIP bundle creation."""

    def test_report_and_compact_zip_include_of3_input_and_boltz_affinity(self) -> None:
        cif_zst = zstd.ZstdCompressor().compress(b"data_mock\nATOM 1 C CA\n")
        uploaded: dict[str, bytes] = {}

        def fake_blob(blob_path: str) -> MagicMock:
            b = MagicMock()
            b.name = blob_path
            if blob_path.endswith("artifacts_bundle.zip"):
                b.exists.return_value = False

                def _upload_file(filename: str, content_type: str | None = None) -> None:
                    with open(filename, "rb") as f:
                        uploaded[blob_path] = f.read()

                b.upload_from_filename.side_effect = _upload_file
            elif blob_path.endswith(".cif.zst"):
                b.exists.return_value = True
                b.download_as_bytes.return_value = cif_zst
            elif blob_path.endswith(".png"):
                b.exists.return_value = True
                b.download_as_bytes.return_value = b"\x89PNG\r\n\x1a\n"
            else:
                b.exists.return_value = False
            return b

        mock_bucket = MagicMock()
        mock_bucket.blob.side_effect = fake_blob
        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        summary = {
            "job_id": "boltz2-run-01",
            "model_type": "boltz2",
            "analyzed_at": "2026-10-02T12:00:00Z",
            "affinity": {"ic50_nm": 12.4, "binding_classification": "strong"},
            "summary": {
                "protein_info": {
                    "input_query": {"queries": {"target": {}}},
                    "input_query_yaml": "version: 1\nsequences: []\n",
                },
                "quality_metrics": {
                    "quality_assessment": "very_high_confidence",
                    "best_model": "model_0",
                    "best_model_plddt": 92.8,
                    "best_ranking_score": 0.94,
                },
            },
            "all_predictions_summary": [
                {
                    "rank": 1,
                    "sample_name": "model_0",
                    "cif_uri": "gs://test-bucket/pipeline_runs/r1/model_0.cif.zst",
                    "plots": {
                        "plddt_plot": "gs://test-bucket/pipeline_runs/r1/analysis/plddt_0.png"
                    },
                }
            ],
            "expert_analysis": {
                "status": "success",
                "analysis": "Strong binding pose in ATP pocket.",
            },
        }

        md = build_expert_analysis_markdown(summary)
        assert "# FoldRun Analysis Report: boltz2-run-01" in md
        assert "92.80" in md
        assert "Strong binding pose in ATP pocket." in md

        uri = ensure_run_archive_in_gcs(
            "gs://test-bucket/pipeline_runs/r1/analysis/",
            summary,
            storage_client=mock_client,
            expected_bucket="test-bucket",
        )
        assert uri == "gs://test-bucket/pipeline_runs/r1/analysis/artifacts_bundle.zip"

        zip_bytes = uploaded["pipeline_runs/r1/analysis/artifacts_bundle.zip"]
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = set(zf.namelist())
            assert "README.md" in names
            assert "report/expert_analysis.md" in names
            assert "metrics/summary.json" in names
            assert "metrics/affinity.json" in names
            assert "input/query.json" in names
            assert "input/query.yaml" in names
            assert "structures/rank_01_model_0.cif" in names
            assert zf.read("structures/rank_01_model_0.cif") == b"data_mock\nATOM 1 C CA\n"
            assert "plots/rank_01_model_0_plddt.png" in names

        # Also exercise build_and_upload_run_archive with custom upload_fn
        custom_uploaded: dict[str, bytes] = {}
        meta = build_and_upload_run_archive(
            analysis_path="gs://test-bucket/pipeline_runs/r1/analysis/",
            bucket_name="test-bucket",
            summary_data=summary,
            storage_client=mock_client,
            upload_fn=lambda path, dest: custom_uploaded.setdefault(dest, open(path, "rb").read()),
        )
        assert meta["artifacts_bundle_file_count"] == 8
        assert uri in custom_uploaded
