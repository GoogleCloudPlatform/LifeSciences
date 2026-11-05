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

"""Unit tests for shared KFP core hierarchy, startup GPU caching, and polymorphic dispatch."""

import json
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

import foldrun_app.core.base_tool as base_tool_mod
from foldrun_app.core.config import CoreConfig
from foldrun_app.core.dispatch import (
    dispatch_analyze_job_parallel,
    dispatch_get_analysis_results,
    dispatch_open_structure_viewer,
    infer_model_type_from_job_id,
    normalize_model_type,
    resolve_job_model_type,
)
from foldrun_app.models.af2.config import Config as AF2Config
from foldrun_app.models.af2.startup import _auto_detect_gpus as af2_auto_detect
from foldrun_app.models.boltz2.config import BOLTZ2Config
from foldrun_app.models.boltz2.startup import _auto_detect_gpus as boltz2_auto_detect
from foldrun_app.models.boltz2.tools.get_analysis_results import BOLTZ2GetAnalysisResultsTool
from foldrun_app.models.boltz2.tools.open_viewer import BOLTZ2OpenViewerTool
from foldrun_app.models.of3.config import OF3Config
from foldrun_app.models.of3.startup import _auto_detect_gpus as of3_auto_detect
from foldrun_app.models.of3.tools.get_analysis_results import OF3GetAnalysisResultsTool
from foldrun_app.models.of3.tools.open_viewer import OF3OpenViewerTool


@pytest.fixture
def isolated_env():
    env = {
        "GCP_PROJECT_ID": "test-project",
        "GCP_REGION": "us-central1",
        "GCS_BUCKET_NAME": "test-bucket",
        "FILESTORE_ID": "test-nfs",
        "ALPHAFOLD_COMPONENTS_IMAGE": "us-central1-docker.pkg.dev/test-project/foldrun-repo/af2:latest",
        "OPENFOLD3_COMPONENTS_IMAGE": "us-central1-docker.pkg.dev/test-project/foldrun-repo/of3:latest",
        "BOLTZ2_COMPONENTS_IMAGE": "us-central1-docker.pkg.dev/test-project/foldrun-repo/boltz2:latest",
        "FOLDRUN_VIEWER_URL": "https://viewer.example.com/",
    }
    with patch.dict(os.environ, env, clear=False):
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            CoreConfig.clear_gpu_detection_cache()
            base_tool_mod._shared_storage_client = None
            base_tool_mod._vertex_initialized = False
            yield
            CoreConfig.clear_gpu_detection_cache()
            base_tool_mod._shared_storage_client = None
            base_tool_mod._vertex_initialized = False


class TestStartupGpuDetectionCache:
    """Verify session-cached GCE GPU quota detection across AF2, OF3, and Boltz-2."""

    def test_detect_supported_gpus_cached_across_models(self, isolated_env):
        af2_cfg = AF2Config()
        of3_cfg = OF3Config()
        boltz2_cfg = BOLTZ2Config()

        with patch(
            "foldrun_app.core.hardware.detect_supported_gpus",
            return_value=["L4", "A100", "A100_80GB", "H100"],
        ) as mock_detect:
            af2_auto_detect(af2_cfg)
            of3_auto_detect(of3_cfg)
            boltz2_auto_detect(boltz2_cfg)

            assert mock_detect.call_count == 1
            assert af2_cfg.supported_gpus == ["L4", "A100", "A100_80GB", "H100"]
            assert of3_cfg.supported_gpus == ["L4", "A100", "A100_80GB"]
            assert boltz2_cfg.supported_gpus == ["L4", "A100", "A100_80GB"]

            # Clearing cache triggers a fresh quota lookup
            CoreConfig.clear_gpu_detection_cache()
            af2_auto_detect(af2_cfg)
            assert mock_detect.call_count == 2


class TestSharedKFPPostSubmissionTools:
    """Verify OF3 and Boltz-2 inherit terminal error detection and viewer URL handling."""

    @staticmethod
    def _fake_job(job_id: str = "job-123") -> MagicMock:
        job = MagicMock()
        job.name = f"projects/test-project/locations/us-central1/pipelineJobs/{job_id}"
        job.runtime_config.gcs_output_directory = f"gs://test-bucket/pipeline_runs/{job_id}"
        return job

    @pytest.mark.parametrize(
        "tool_cls,cfg_cls,expected_noun",
        [
            (OF3GetAnalysisResultsTool, OF3Config, "samples"),
            (BOLTZ2GetAnalysisResultsTool, BOLTZ2Config, "samples"),
        ],
    )
    def test_error_json_returns_terminal_failed_status(
        self, isolated_env, tool_cls, cfg_cls, expected_noun
    ):
        tool = tool_cls({"name": "get_analysis_results"}, cfg_cls())
        job = self._fake_job("sample-job-1")

        def fake_blob(blob_name: str) -> MagicMock:
            blob = MagicMock()
            if blob_name.endswith("summary.json"):
                blob.download_as_text.side_effect = FileNotFoundError("no summary")
            elif blob_name.endswith("analysis_metadata.json"):
                blob.download_as_text.return_value = json.dumps(
                    {
                        "job_id": "sample-job-1",
                        "total_predictions": 5,
                        "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    }
                )
            elif blob_name.endswith("error.json"):
                blob.download_as_text.return_value = json.dumps(
                    {"error": "Missing CIF coordinates", "details": "Task 0 crashed"}
                )
            return blob

        mock_bucket = MagicMock()
        mock_bucket.blob.side_effect = fake_blob
        mock_storage_client = MagicMock()
        mock_storage_client.bucket.return_value = mock_bucket

        with patch("google.cloud.storage.Client", return_value=mock_storage_client):
            res = tool.run({"job_id": "sample-job-1", "_pipeline_job": job})

        assert res["status"] == "failed"
        assert res["error"] == "Missing CIF coordinates"
        assert res["error_details"] == "Task 0 crashed"
        assert tool.item_noun == expected_noun

    @pytest.mark.parametrize(
        "tool_cls,cfg_cls",
        [
            (OF3GetAnalysisResultsTool, OF3Config),
            (BOLTZ2GetAnalysisResultsTool, BOLTZ2Config),
        ],
    )
    def test_stale_zero_completed_returns_likely_failed(self, isolated_env, tool_cls, cfg_cls):
        tool = tool_cls({"name": "get_analysis_results"}, cfg_cls())
        job = self._fake_job("stale-job-1")
        ten_min_ago = (datetime.now(UTC) - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")

        def fake_blob(blob_name: str) -> MagicMock:
            blob = MagicMock()
            if blob_name.endswith("summary.json") or blob_name.endswith("error.json"):
                blob.download_as_text.side_effect = FileNotFoundError("missing")
            elif blob_name.endswith("analysis_metadata.json"):
                blob.download_as_text.return_value = json.dumps(
                    {
                        "job_id": "stale-job-1",
                        "total_predictions": 5,
                        "started_at": ten_min_ago,
                    }
                )
            return blob

        mock_bucket = MagicMock()
        mock_bucket.blob.side_effect = fake_blob
        mock_bucket.list_blobs.return_value = []
        mock_storage_client = MagicMock()
        mock_storage_client.bucket.return_value = mock_bucket

        with patch("google.cloud.storage.Client", return_value=mock_storage_client):
            res = tool.run({"job_id": "stale-job-1", "_pipeline_job": job})

        assert res["status"] == "likely_failed"
        assert "0/5 samples analyzed" in res["message"]

    @pytest.mark.parametrize(
        "tool_cls,cfg_cls",
        [
            (OF3OpenViewerTool, OF3Config),
            (BOLTZ2OpenViewerTool, BOLTZ2Config),
        ],
    )
    def test_kfp_open_viewer_strips_trailing_slash_and_extracts_short_id(
        self, isolated_env, tool_cls, cfg_cls
    ):
        tool = tool_cls({"name": "open_viewer"}, cfg_cls())
        full_resource = "projects/test-project/locations/us-central1/pipelineJobs/run-abc-123"

        with patch("foldrun_app.core.kfp_tools.webbrowser.open") as mock_open:
            res = tool.run({"job_id": full_resource, "open_browser": True})

        assert res["job_id"] == "run-abc-123"
        assert res["viewer_url"] == "https://viewer.example.com/job/run-abc-123"
        assert res["browser_opened"] is True
        mock_open.assert_called_once_with("https://viewer.example.com/job/run-abc-123")


class TestPolymorphicDispatch:
    """Verify label-first, prefix-fallback, and AF3 GCS-fallback dispatch."""

    def test_normalize_and_infer_helpers(self):
        assert normalize_model_type("OF3") == "openfold3"
        assert normalize_model_type("boltz-2") == "boltz2"
        assert normalize_model_type("AlphaFold3") == "alphafold3"
        assert normalize_model_type("af2") == "alphafold2"
        assert normalize_model_type("unknown") is None

        assert infer_model_type_from_job_id("openfold3-inference-pipeline-20260401") == "openfold3"
        assert infer_model_type_from_job_id("boltz2-inference-pipeline-20260401") == "boltz2"
        assert (
            infer_model_type_from_job_id("alphafold3-inference-pipeline-20260401") == "alphafold3"
        )
        assert (
            infer_model_type_from_job_id("alphafold2-inference-pipeline-20260401") == "alphafold2"
        )
        assert infer_model_type_from_job_id("custom-protein-run-42") is None

    @pytest.mark.parametrize(
        "label_value,expected_analyze_key,expected_results_key,expected_viewer_key",
        [
            ("alphafold2", "af2_analyze_parallel", "af2_get_analysis_results", "af2_open_viewer"),
            ("openfold3", "of3_analyze_parallel", "of3_get_analysis_results", "of3_open_viewer"),
            (
                "boltz2",
                "boltz2_analyze_parallel",
                "boltz2_get_analysis_results",
                "boltz2_open_viewer",
            ),
            ("alphafold3", "af3_get_results", "af3_get_results", "af3_open_viewer"),
        ],
    )
    def test_dispatch_routes_by_pipeline_job_model_type_label(
        self,
        isolated_env,
        label_value,
        expected_analyze_key,
        expected_results_key,
        expected_viewer_key,
    ):
        fake_job = MagicMock()
        fake_job.labels = {"model_type": label_value}
        fake_job.display_name = "custom-unprefixed-run"
        fake_job.name = "projects/test-project/locations/us-central1/pipelineJobs/987654321"

        dispatched_calls: list[tuple[str, dict]] = []

        def fake_get_tool(key: str):
            t = MagicMock()
            t.run.side_effect = lambda args: (
                dispatched_calls.append((key, args)) or {"status": "ok", "backend": key}
            )
            return t

        with patch("foldrun_app.core.dispatch.get_pipeline_job", return_value=fake_job) as mock_gpj:
            res_an = dispatch_analyze_job_parallel(
                "987654321", top_n=5, overwrite=True, get_tool_fn=fake_get_tool
            )
            res_get = dispatch_get_analysis_results(
                "987654321", wait=True, timeout=15, poll_interval=3, get_tool_fn=fake_get_tool
            )
            res_view = dispatch_open_structure_viewer(
                "987654321", open_browser=False, get_tool_fn=fake_get_tool
            )

        assert mock_gpj.call_count == 3
        assert res_an["backend"] == expected_analyze_key
        assert res_get["backend"] == expected_results_key
        assert res_view["backend"] == expected_viewer_key

        if label_value != "alphafold3":
            assert dispatched_calls[0][1]["_pipeline_job"] is fake_job
            assert dispatched_calls[1][1]["_pipeline_job"] is fake_job

    def test_single_pipeline_job_rpc_end_to_end_with_kfp_backend(self, isolated_env):
        """Dispatching to a KFP backend passes _pipeline_job so get_pipeline_job runs only once."""
        fake_job = MagicMock()
        fake_job.labels = {"model_type": "openfold3"}
        fake_job.display_name = "of3-complex"
        fake_job.name = "projects/test-project/locations/us-central1/pipelineJobs/of3-single-rpc"
        fake_job.runtime_config.gcs_output_directory = (
            "gs://test-bucket/pipeline_runs/of3-single-rpc"
        )

        summary_blob = MagicMock()
        summary_blob.download_as_text.return_value = json.dumps(
            {"best_prediction": {"sample_ranking_score": 0.92}}
        )
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = summary_blob
        mock_storage_client = MagicMock()
        mock_storage_client.bucket.return_value = mock_bucket

        of3_results_tool = OF3GetAnalysisResultsTool(
            {"name": "of3_get_analysis_results"}, OF3Config()
        )

        with (
            patch(
                "foldrun_app.core.dispatch.get_pipeline_job", return_value=fake_job
            ) as mock_disp_gpj,
            patch("foldrun_app.core.kfp_tools.get_pipeline_job") as mock_backend_gpj,
            patch("google.cloud.storage.Client", return_value=mock_storage_client),
            patch(
                "foldrun_app.core.kfp_tools.build_agent_downloads_dict",
                return_value={},
            ),
        ):
            res = dispatch_get_analysis_results(
                "of3-single-rpc",
                get_tool_fn=lambda _k: of3_results_tool,
            )

        assert res["status"] == "complete"
        assert mock_disp_gpj.call_count == 1
        mock_backend_gpj.assert_not_called()

    def test_synchronous_af3_gcs_prefix_fallback(self, isolated_env, monkeypatch):
        """Unprefixed synchronous AF3 job_name resolves to alphafold3 via af3_predictions/<job_id>/."""
        monkeypatch.setenv(
            "AF3_VERTEX_ENDPOINT", "projects/test-project/locations/us-central1/endpoints/12345"
        )

        mock_bucket = MagicMock()
        mock_bucket.list_blobs.return_value = [
            MagicMock(name="af3_predictions/kras_sync_run/summary.json")
        ]
        mock_storage_client = MagicMock()
        mock_storage_client.bucket.return_value = mock_bucket

        with (
            patch(
                "foldrun_app.core.dispatch.get_pipeline_job",
                side_effect=ValueError("PipelineJob not found"),
            ),
            patch("foldrun_app.core.dispatch.storage.Client", return_value=mock_storage_client),
        ):
            model_type, job = resolve_job_model_type("kras_sync_run", allow_remote_lookup=True)

        assert model_type == "alphafold3"
        assert job is None
        mock_bucket.list_blobs.assert_called_once_with(
            prefix="af3_predictions/kras_sync_run/", max_results=1
        )
