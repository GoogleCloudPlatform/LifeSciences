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

"""Tests for AlphaFold 3 tool classes."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from google.api_core.exceptions import DeadlineExceeded

import foldrun_app.core.base_tool as base_tool_mod
from foldrun_app.models.af3.config import AF3Config
from foldrun_app.models.af3.tools.check_endpoint import AF3CheckEndpointTool
from foldrun_app.models.af3.tools.get_results import AF3GetResultsTool
from foldrun_app.models.af3.tools.open_viewer import AF3OpenViewerTool
from foldrun_app.models.af3.tools.submit_prediction import AF3SubmitPredictionTool


@pytest.fixture
def mock_af3_env():
    env = {
        "GCP_PROJECT_ID": "test-project",
        "GCP_REGION": "us-central1",
        "GCS_BUCKET_NAME": "test-bucket",
        "AF3_ENDPOINT": "projects/test-project/locations/us-central1/endpoints/12345",
        "FOLDRUN_VIEWER_URL": "https://viewer.example.com",
    }
    with patch.dict(os.environ, env, clear=False):
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            base_tool_mod._shared_storage_client = None
            base_tool_mod._vertex_initialized = False
            yield
            base_tool_mod._shared_storage_client = None
            base_tool_mod._vertex_initialized = False


class TestAF3Tools:
    """Unit tests for AF3 tools."""

    def test_submit_prediction_success(self, mock_af3_env):
        config = AF3Config()

        mock_storage = MagicMock()
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_storage.bucket.return_value = mock_bucket
        mock_bucket.blob.return_value = mock_blob

        tool = AF3SubmitPredictionTool(
            tool_config={"name": "af3_submit_prediction"},
            config=config,
        )
        tool.storage_client = mock_storage

        mock_endpoint = MagicMock()
        mock_endpoint.resource_name = "projects/test-project/locations/us-central1/endpoints/12345"
        mock_endpoint.predict.return_value = MagicMock(
            predictions=[
                {
                    "cif": "data_test\n_entry.id test\n",
                    "summary_confidences": {
                        "ptm": 0.85,
                        "iptm": 0.80,
                        "ranking_score": 0.81,
                        "mean_plddt": 88.5,
                    },
                }
            ]
        )

        with patch.object(tool, "get_endpoint", return_value=mock_endpoint):
            result = tool.run(
                {
                    "input": "MKTIIALSYIFCLVFA",
                    "job_name": "af3_unit_test",
                    "msa_free": True,
                }
            )

        assert result["status"] == "succeeded"
        assert result["job_id"] == "af3_unit_test"
        assert result["mode"] == "msa-free"
        assert result["metrics"]["ranking_score"] == 0.81
        assert result["metrics"]["mean_plddt"] == 88.5
        assert "af3_unit_test_model.cif" in result["cif_uri"]
        assert "viewer.example.com/job/af3_unit_test?model=af3" in result["viewer_url"]
        assert mock_blob.upload_from_string.called

        # Assert predict call arguments and timeout
        mock_endpoint.predict.assert_called_once()
        call_kwargs = mock_endpoint.predict.call_args.kwargs
        assert call_kwargs.get("timeout") == config.timeout_seconds
        assert call_kwargs.get("parameters") == {
            "run_data_pipeline": False,
            "num_diffusion_samples": 5,
        }
        call_instances = mock_endpoint.predict.call_args.kwargs.get("instances")
        assert call_instances is not None
        assert "msaFree" not in call_instances[0]
        assert call_instances[0]["dialect"] == "alphafold3"
        assert call_instances[0]["sequences"][0]["protein"]["unpairedMsa"] == ""
        assert call_instances[0]["sequences"][0]["protein"]["pairedMsa"] == ""
        assert call_instances[0]["sequences"][0]["protein"]["templates"] == []

    def test_submit_prediction_full_msa_and_model_garden_response(self, mock_af3_env):
        config = AF3Config()

        mock_storage = MagicMock()
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_storage.bucket.return_value = mock_bucket
        mock_bucket.blob.return_value = mock_blob

        tool = AF3SubmitPredictionTool(
            tool_config={"name": "af3_submit_prediction"},
            config=config,
        )
        tool.storage_client = mock_storage

        mock_endpoint = MagicMock()
        mock_endpoint.resource_name = "projects/test-project/locations/us-central1/endpoints/12345"
        mock_endpoint.predict.return_value = MagicMock(
            predictions=[
                {
                    "structure_cif": (
                        "data_test\n"
                        "ATOM 1 N N . MET A 1 1 ? 0.0 0.0 0.0 1.00 92.40 ? 1 MET A N 1\n"
                        "ATOM 2 CA C . MET A 1 1 ? 1.0 0.0 0.0 1.00 92.40 ? 1 MET A CA 1\n"
                    ),
                    "plddt": [92.0, 92.8],
                    "pae": [[0.5, 1.5], [1.5, 0.5]],
                    "summary": {
                        "ptm": 0.85,
                        "ranking_score": 0.85,
                        "fraction_disordered": 0.0,
                        "has_clash": 0.0,
                    },
                }
            ]
        )

        with patch.object(tool, "get_endpoint", return_value=mock_endpoint):
            result = tool.run(
                {
                    "input": "MKTIIALSYIFCLVFA",
                    "job_name": "af3_full_msa_test",
                    "msa_free": False,
                }
            )

        assert result["status"] == "succeeded"
        assert result["mode"] == "standard"
        assert result["metrics"]["ranking_score"] == 0.85
        assert result["metrics"]["ptm"] == 0.85
        assert result["metrics"]["mean_plddt"] == 92.4
        assert result["metrics"]["mean_pae"] == 1.0
        assert result["summary_uri"].endswith("af3_full_msa_test/analysis/summary.json")

        call_kwargs = mock_endpoint.predict.call_args.kwargs
        assert call_kwargs.get("parameters") == {
            "run_data_pipeline": True,
            "num_diffusion_samples": 5,
        }
        call_instances = call_kwargs.get("instances")
        assert "msaFree" not in call_instances[0]
        assert "unpairedMsa" not in call_instances[0]["sequences"][0]["protein"]

    def test_submit_prediction_missing_input(self, mock_af3_env):
        config = AF3Config()
        tool = AF3SubmitPredictionTool(
            tool_config={"name": "af3_submit_prediction"},
            config=config,
        )
        result = tool.run({})
        assert result["status"] == "error"
        assert "Missing required argument 'input'" in result["message"]

    def test_submit_prediction_deadline_exceeded(self, mock_af3_env):
        config = AF3Config()
        tool = AF3SubmitPredictionTool(
            tool_config={"name": "af3_submit_prediction"},
            config=config,
        )
        mock_endpoint = MagicMock()
        mock_endpoint.predict.side_effect = DeadlineExceeded("Deadline exceeded")

        with patch.object(tool, "get_endpoint", return_value=mock_endpoint):
            result = tool.run({"input": "MKTIIALSYIFCLVFA", "job_name": "timeout_job"})

        assert result["status"] == "error"
        assert "exceeded timeout budget" in result["message"]

    def test_submit_prediction_empty_cif(self, mock_af3_env):
        config = AF3Config()
        tool = AF3SubmitPredictionTool(
            tool_config={"name": "af3_submit_prediction"},
            config=config,
        )
        mock_endpoint = MagicMock()
        mock_endpoint.predict.return_value = MagicMock(
            predictions=[{"cif": "", "summary_confidences": {}}]
        )

        with patch.object(tool, "get_endpoint", return_value=mock_endpoint):
            result = tool.run({"input": "MKTIIALSYIFCLVFA", "job_name": "empty_cif_job"})

        assert result["status"] == "error"
        assert "no structure coordinates" in result["message"]

    def test_check_endpoint(self, mock_af3_env):
        config = AF3Config()
        tool = AF3CheckEndpointTool(tool_config={"name": "af3_check_endpoint"}, config=config)

        mock_endpoint = MagicMock()
        mock_endpoint.resource_name = "projects/test-project/locations/us-central1/endpoints/12345"
        mock_endpoint.display_name = "AlphaFold3-Production-Endpoint"

        machine_spec = MagicMock()
        machine_spec.machine_type = "g2-standard-16"
        acc_enum = MagicMock()
        acc_enum.name = "NVIDIA_L4"
        machine_spec.accelerator_type = acc_enum
        machine_spec.accelerator_count = 1

        dedicated_resources = MagicMock()
        dedicated_resources.machine_spec = machine_spec

        deployed_model = MagicMock()
        deployed_model.id = "dep-1"
        deployed_model.model = "projects/test-project/models/af3-v1"
        deployed_model.display_name = "af3-model-v1"
        deployed_model.dedicated_resources = dedicated_resources

        mock_endpoint.deployed_models = [deployed_model]

        with patch.object(tool, "get_endpoint", return_value=mock_endpoint):
            result = tool.run()

        assert result["status"] == "ready"
        assert result["display_name"] == "AlphaFold3-Production-Endpoint"
        assert result["deployed_models_count"] == 1
        assert result["msa_free_supported"] is True
        assert result["deployed_models"][0]["machine_type"] == "g2-standard-16"
        assert result["deployed_models"][0]["accelerator_type"] == "NVIDIA_L4"
        assert result["deployed_models"][0]["accelerator_count"] == 1

    def test_get_results_success(self, mock_af3_env):
        config = AF3Config()
        tool = AF3GetResultsTool(tool_config={"name": "af3_get_results"}, config=config)

        mock_storage = MagicMock()
        mock_bucket = MagicMock()
        mock_conf_blob = MagicMock()
        mock_cif_blob = MagicMock()

        mock_conf_blob.exists.return_value = True
        mock_conf_blob.download_as_text.return_value = json.dumps(
            {
                "ranking_score": 0.85,
                "ptm": 0.88,
                "iptm": 0.82,
                "mean_plddt": 91.2,
                "fraction_disordered": 0.05,
            }
        )
        mock_cif_blob.exists.return_value = True

        def get_blob(path):
            if path.endswith("summary_confidences.json"):
                return mock_conf_blob
            return mock_cif_blob

        mock_bucket.blob.side_effect = get_blob
        mock_storage.bucket.return_value = mock_bucket
        tool.storage_client = mock_storage

        result = tool.run({"job_id": "test_job_123"})
        assert result["status"] == "succeeded"
        assert result["metrics"]["ranking_score"] == 0.85
        assert result["metrics"]["mean_plddt"] == 91.2
        assert "High confidence" in result["quality_assessment"]

    def test_get_results_not_found(self, mock_af3_env):
        config = AF3Config()
        tool = AF3GetResultsTool(tool_config={"name": "af3_get_results"}, config=config)

        mock_storage = MagicMock()
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_blob.exists.return_value = False
        mock_bucket.blob.return_value = mock_blob
        mock_storage.bucket.return_value = mock_bucket
        tool.storage_client = mock_storage

        result = tool.run({"job_id": "missing_job"})
        assert result["status"] == "not_found"

    def test_open_viewer_success(self, mock_af3_env):
        config = AF3Config()
        tool = AF3OpenViewerTool(tool_config={"name": "af3_open_viewer"}, config=config)

        with patch("webbrowser.open") as mock_open:
            result = tool.run({"job_id": "job_xyz", "open_browser": True})
            assert result["status"] == "succeeded"
            assert result["job_id"] == "job_xyz"
            assert result["viewer_url"] == "https://viewer.example.com/job/job_xyz?model=af3"
            assert result["browser_opened"] is True
            mock_open.assert_called_once_with("https://viewer.example.com/job/job_xyz?model=af3")

    def test_open_viewer_missing_job_id(self, mock_af3_env):
        config = AF3Config()
        tool = AF3OpenViewerTool(tool_config={"name": "af3_open_viewer"}, config=config)
        result = tool.run({})
        assert result["status"] == "error"
        assert "Missing required argument 'job_id'" in result["message"]

    def test_submit_prediction_path_traversal_denied(self, mock_af3_env):
        config = AF3Config()
        tool = AF3SubmitPredictionTool(
            tool_config={"name": "af3_submit_prediction"},
            config=config,
        )
        result = tool.run({"input": "../../../../etc/passwd"})
        assert result["status"] == "error"
        assert (
            "Access denied" in result["message"] or "escapes allowed workspace" in result["message"]
        )

    def test_submit_prediction_malformed_gcs_uri(self, mock_af3_env):
        config = AF3Config()
        tool = AF3SubmitPredictionTool(
            tool_config={"name": "af3_submit_prediction"},
            config=config,
        )
        result = tool.run({"input": "gs://onlybucket"})
        assert result["status"] == "error"
        assert "Invalid GCS URI" in result["message"]

    def test_submit_prediction_custom_output_gcs_uri(self, mock_af3_env):
        config = AF3Config()
        mock_storage = MagicMock()
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_storage.bucket.return_value = mock_bucket
        mock_bucket.blob.return_value = mock_blob

        tool = AF3SubmitPredictionTool(
            tool_config={"name": "af3_submit_prediction"},
            config=config,
        )
        tool.storage_client = mock_storage

        mock_endpoint = MagicMock()
        mock_endpoint.resource_name = "projects/test-project/locations/us-central1/endpoints/12345"
        mock_endpoint.predict.return_value = MagicMock(
            predictions=[
                {
                    "cif": "data_test\n_entry.id test\n",
                    "summary_confidences": {"ptm": 0.85, "iptm": 0.80},
                }
            ]
        )

        # Unpinned bucket without AF3_ALLOWED_BUCKETS is rejected
        with patch.object(tool, "get_endpoint", return_value=mock_endpoint):
            denied = tool.run(
                {
                    "input": "MKTIIALSYIFCLVFA",
                    "job_name": "custom_out_test",
                    "output_gcs_uri": "gs://unauthorized-bucket/experiments/run1",
                }
            )
        assert denied["status"] == "error"
        assert "Access denied" in denied["message"]

        # Allowed bucket via AF3_ALLOWED_BUCKETS succeeds
        with (
            patch.dict(os.environ, {"AF3_ALLOWED_BUCKETS": "custom-archive-bucket"}),
            patch.object(tool, "get_endpoint", return_value=mock_endpoint),
        ):
            result = tool.run(
                {
                    "input": "MKTIIALSYIFCLVFA",
                    "job_name": "custom_out_test",
                    "output_gcs_uri": "gs://custom-archive-bucket/experiments/run1",
                }
            )

        assert result["status"] == "succeeded"
        assert (
            "gs://custom-archive-bucket/experiments/run1/custom_out_test"
            in result["gcs_output_dir"]
        )
        mock_storage.bucket.assert_called_with("custom-archive-bucket")

    def test_submit_prediction_job_name_traversal_denied(self, mock_af3_env):
        config = AF3Config()
        tool = AF3SubmitPredictionTool(
            tool_config={"name": "af3_submit_prediction"},
            config=config,
        )
        result = tool.run(
            {
                "input": "MKTIIALSYIFCLVFA",
                "job_name": "../../pipeline_runs/victim_job",
            }
        )
        assert result["status"] == "error"
        assert "Invalid job_name" in result["message"]

    def test_multimer_ranking_score_zero_iptm(self):
        from foldrun_app.models.af3.utils.metrics import compute_ranking_score

        # Multimer where interface failed (iptm == 0.0)
        score = compute_ranking_score({"ptm": 0.80, "iptm": 0.0})
        assert score == pytest.approx(0.16)

        # Explicit multimer flag
        score_flag = compute_ranking_score({"ptm": 0.80, "iptm": 0.0}, is_multimer=True)
        assert score_flag == pytest.approx(0.16)

        # Monomer (no iptm)
        score_monomer = compute_ranking_score({"ptm": 0.85})
        assert score_monomer == pytest.approx(0.85)

        # Monomer with None iptm
        score_monomer_none = compute_ranking_score({"ptm": 0.85, "iptm": None})
        assert score_monomer_none == pytest.approx(0.85)

    def test_check_endpoint_list_models_sdk_compat(self, mock_af3_env):
        config = AF3Config()
        tool = AF3CheckEndpointTool(tool_config={"name": "af3_check_endpoint"}, config=config)

        class FakeSdkEndpoint:
            resource_name = (
                "projects/test-project/locations/us-central1/endpoints/mg-endpoint-12345"
            )
            display_name = "AlphaFold 3 Dedicated Endpoint"

            def list_models(self):
                dm = MagicMock()
                dm.id = "3746287354239778816"
                dm.model = "projects/test-project/locations/us-central1/models/af3-h100"
                dm.display_name = "alphafold3-v3_0_4-h100"
                dm.dedicated_resources.machine_spec.machine_type = "a3-highgpu-1g"
                dm.dedicated_resources.machine_spec.accelerator_type = "NVIDIA_H100_80GB"
                dm.dedicated_resources.machine_spec.accelerator_count = 1
                dm.dedicated_resources.min_replica_count = 1
                dm.dedicated_resources.max_replica_count = 1
                return [dm]

        with (
            patch.object(tool, "get_endpoint", return_value=FakeSdkEndpoint()),
            patch.object(tool, "get_active_deploy_operations", return_value=[]),
        ):
            result = tool.run({})

        assert result["status"] == "ready"
        assert result["deployed_models_count"] == 1
        assert "/locations/us-central1/endpoints/mg-endpoint-12345" in result["console_url"]

    def test_submit_prediction_kfp_async(self, mock_af3_env):
        config = AF3Config()
        tool = AF3SubmitPredictionTool(
            tool_config={"name": "af3_submit_prediction"},
            config=config,
        )
        mock_endpoint = MagicMock()
        mock_endpoint.resource_name = (
            "projects/test-project/locations/us-central1/endpoints/mg-endpoint-12345"
        )
        mock_endpoint.deployed_models = [MagicMock(id="dep-1")]

        mock_pjob = MagicMock()
        mock_pjob.resource_name = (
            "projects/test-project/locations/us-central1/pipelineJobs/"
            "alphafold3-inference-pipeline-20261006070000"
        )
        mock_pjob.state.name = "PIPELINE_STATE_PENDING"

        with (
            patch.object(tool, "get_endpoint", return_value=mock_endpoint),
            patch("google.cloud.aiplatform.PipelineJob", return_value=mock_pjob) as mock_pjob_cls,
        ):
            result = tool.run(
                {
                    "input": "MKTIIALSYIFCLVFA",
                    "job_name": "kras_af3_msa",
                    "msa_free": False,
                    "sync": False,
                }
            )

        assert result["status"] == "submitted"
        assert result["query_name"] == "kras_af3_msa"
        assert result["job_name"] == "kras_af3_msa"
        assert result["job_id"].startswith("alphafold3-inference-pipeline-")
        assert result["mode"] == "standard"
        assert "/vertex-ai/pipelines/locations/us-central1/runs/" in result["console_url"]
        assert f"viewer.example.com/job/{result['job_id']}" in result["viewer_url"]
        mock_pjob_cls.assert_called_once()
        mock_pjob.submit.assert_called_once()

    def test_submit_batch_predictions(self, mock_af3_env):
        from foldrun_app.models.af3.tools.submit_batch import AF3BatchSubmitTool

        config = AF3Config()
        batch_tool = AF3BatchSubmitTool(
            tool_config={"name": "af3_submit_batch"},
            config=config,
        )
        mock_endpoint = MagicMock()
        mock_endpoint.resource_name = (
            "projects/test-project/locations/us-central1/endpoints/mg-endpoint-12345"
        )
        mock_endpoint.deployed_models = [MagicMock(id="dep-1")]

        mock_pjob = MagicMock()
        mock_pjob.state.name = "PIPELINE_STATE_PENDING"

        with (
            patch(
                "foldrun_app.models.af3.tools.submit_prediction.AF3SubmitPredictionTool.get_endpoint",
                return_value=mock_endpoint,
            ),
            patch("google.cloud.aiplatform.PipelineJob", return_value=mock_pjob),
        ):
            res = batch_tool.run(
                {
                    "batch_config": [
                        {
                            "input": "MKTIIALSYIFCLVFA",
                            "job_name": "btk_af3_msa",
                            "msa_free": False,
                        },
                        {
                            "input": "ACDEFGHIKLMNPQRSTVWY",
                            "job_name": "jak2_af3_msa",
                            "msa_free": False,
                        },
                    ]
                }
            )

        assert res["status"] == "submitted"
        assert res["total"] == 2
        assert res["succeeded"] == 2
        assert res["failed"] == 0
        # Ensure unique KFP job IDs even when submitted in the same second
        job_ids = [j["job_id"] for j in res["submitted_jobs"]]
        assert len(set(job_ids)) == 2

    def test_resolve_af3_hardware_plan_with_reservations_and_quotas(self, mock_af3_env):
        from foldrun_app.core.hardware import resolve_af3_hardware_plan

        fake_reservations = [
            {
                "name": "h100-reserved-pool",
                "full_resource_name": (
                    "projects/test-project/zones/us-central1-a/reservations/h100-reserved-pool"
                ),
                "zone": "us-central1-a",
                "specific_reservation_required": True,
                "machine_type": "a3-highgpu-1g",
                "accelerator_types": ["nvidia-h100-80gb"],
                "total_count": 2,
                "in_use_count": 0,
                "available_count": 2,
            }
        ]

        with patch(
            "foldrun_app.core.hardware.check_gpu_reservations",
            return_value=fake_reservations,
        ):
            plan = resolve_af3_hardware_plan(
                project_id="test-project",
                region="us-central1",
                requested_machine_type="a3-highgpu-1g",
                requested_accelerator_type="NVIDIA_H100_80GB",
                requested_accelerator_count=1,
                min_replicas=1,
                auto_fallback=True,
            )

        assert plan["primary"]["machine_type"] == "a3-highgpu-1g"
        assert plan["primary"]["reservation_affinity_type"] == "SPECIFIC_RESERVATION"
        assert (
            "projects/test-project/zones/us-central1-a/reservations/h100-reserved-pool"
            in plan["primary"]["reservation_affinity_values"]
        )
        assert plan["candidates"][1]["machine_type"] == "a2-ultragpu-1g"

    def test_submit_prediction_kfp_no_deployed_model_returns_rerun_infra_error(self, mock_af3_env):
        config = AF3Config()
        tool = AF3SubmitPredictionTool(
            tool_config={"name": "af3_submit_prediction"},
            config=config,
        )
        mock_endpoint = MagicMock()
        mock_endpoint.resource_name = (
            "projects/test-project/locations/us-central1/endpoints/mg-endpoint-12345"
        )
        mock_endpoint.deployed_models = []
        mock_endpoint.list_models.return_value = []

        with (
            patch.object(tool, "get_endpoint", return_value=mock_endpoint),
            patch.object(tool, "get_active_deploy_operations", return_value=[]),
            patch("google.cloud.aiplatform.PipelineJob") as mock_pjob_cls,
        ):
            result = tool.run(
                {
                    "input": "MKTIIALSYIFCLVFA",
                    "job_name": "cold_start_af3",
                    "msa_free": False,
                    "sync": False,
                }
            )

        assert result["status"] == "error"
        assert "--steps infra --enable-af3" in result["message"]
        mock_pjob_cls.assert_not_called()

    def test_af3_4_stage_kfp_pipeline_compilation(self, tmp_path):
        from kfp import compiler

        from foldrun_app.models.af3.pipeline import create_af3_inference_pipeline

        pipeline_func = create_af3_inference_pipeline()
        out_file = tmp_path / "af3_4stage_pipeline.json"
        compiler.Compiler().compile(pipeline_func=pipeline_func, package_path=str(out_file))

        spec = json.loads(out_file.read_text(encoding="utf-8"))
        tasks = spec["root"]["dag"]["tasks"]
        display_names = {
            t_name: t_info.get("taskInfo", {}).get("name", "") for t_name, t_info in tasks.items()
        }
        assert len(tasks) == 4
        assert "1. Provision & Queue AF3 Endpoint" in display_names.values()
        assert "2. Run AF3 Inference (H100 Endpoint)" in display_names.values()
        assert "3. Report AF3 Endpoint Available" in display_names.values()
        assert "4. Process AF3 Results & Expert Analysis" in display_names.values()

    def test_af3_batch_queue_status_and_scale_up_recommendation(self, mock_af3_env):
        from foldrun_app.models.af3.tools.submit_batch import AF3BatchSubmitTool

        config = AF3Config()
        tool = AF3BatchSubmitTool(tool_config={}, config=config)
        tool.storage_client = MagicMock()

        mock_endpoint = MagicMock()
        mock_endpoint.resource_name = (
            "projects/test-project/locations/us-central1/endpoints/mg-endpoint-12345"
        )
        mock_dm = MagicMock()
        mock_dm.dedicated_resources.min_replica_count = 1
        mock_endpoint.deployed_models = [mock_dm]

        mock_pjob = MagicMock()
        mock_pjob.resource_name = (
            "projects/test-project/locations/us-central1/pipelineJobs/af3-batch-item"
        )

        with (
            patch.object(tool, "get_endpoint", return_value=mock_endpoint),
            patch(
                "foldrun_app.models.af3.tools.submit_prediction.AF3SubmitPredictionTool.get_endpoint",
                return_value=mock_endpoint,
            ),
            patch("google.cloud.aiplatform.PipelineJob", return_value=mock_pjob),
        ):
            result = tool.run(
                {
                    "batch_config": [
                        {"input": "MKTIIALSYIFCLVFA", "job_name": "batch_job_1"},
                        {"input": "MKTIIALSYIFCLVFA", "job_name": "batch_job_2"},
                        {"input": "MKTIIALSYIFCLVFA", "job_name": "batch_job_3"},
                        {"input": "MKTIIALSYIFCLVFA", "job_name": "batch_job_4"},
                    ]
                }
            )

        assert result["status"] == "submitted"
        assert result["succeeded"] == 4
        assert result["queue_status"] is not None
        assert result["queue_status"]["total_active_and_queued"] == 4
        assert result["scale_up_recommendation"] is not None
        assert result["scale_up_recommendation"]["recommended"] is True
        assert result["scale_up_recommendation"]["recommended_replicas"] == 2
        assert "af3_max_replica_count = 2" in result["message"]

    def test_af3_auto_defaults_model_seeds_monomer_vs_multimer(self, mock_af3_env):
        config = AF3Config()
        tool = AF3SubmitPredictionTool(
            tool_config={"name": "af3_submit_prediction"},
            config=config,
        )
        mock_endpoint = MagicMock()
        mock_endpoint.resource_name = (
            "projects/test-project/locations/us-central1/endpoints/mg-endpoint-12345"
        )
        mock_dm = MagicMock()
        mock_dm.dedicated_resources.min_replica_count = 1
        mock_endpoint.deployed_models = [mock_dm]

        mock_pjob = MagicMock()
        mock_pjob.resource_name = (
            "projects/test-project/locations/us-central1/pipelineJobs/af3-seeds-test"
        )
        mock_pjob.state.name = "PIPELINE_STATE_PENDING"

        with (
            patch.object(tool, "get_endpoint", return_value=mock_endpoint),
            patch("google.cloud.aiplatform.PipelineJob", return_value=mock_pjob),
        ):
            # 1. Monomer (1 chain) -> modelSeeds=[1] (5 diffusion samples)
            res_monomer = tool.run(
                {
                    "input": ">A\nMKTIIALSYIFCLVFA",
                    "job_name": "monomer_test",
                    "msa_free": False,
                    "sync": False,
                }
            )
            assert res_monomer["model_seeds"] == [1]
            assert res_monomer["diffusion_samples_expected"] == 5

            # 2. Multimer / Complex (>1 chain, Full MSA) -> modelSeeds=[1, 2, 3, 4, 5] (25 diffusion samples)
            res_multimer = tool.run(
                {
                    "input": ">ChainA\nMKTIIALSYIFCLVFA\n>ChainB\nGIVEQCCTSICSLYQLENYCN",
                    "job_name": "multimer_test",
                    "msa_free": False,
                    "sync": False,
                }
            )
            assert res_multimer["model_seeds"] == [1, 2, 3, 4, 5]
            assert res_multimer["diffusion_samples_expected"] == 25

            # 3. Multimer with msa_free=True -> modelSeeds=[1] (5 diffusion samples)
            res_msafree = tool.run(
                {
                    "input": ">ChainA\nMKTIIALSYIFCLVFA\n>ChainB\nGIVEQCCTSICSLYQLENYCN",
                    "job_name": "multimer_msafree_test",
                    "msa_free": True,
                    "sync": False,
                }
            )
            assert res_msafree["model_seeds"] == [1]
            assert res_msafree["diffusion_samples_expected"] == 5

            # 4. Multimer where caller explicitly passes model_seeds=[1] -> NEVER silently overwritten; preserves [1] + emits Ensemble Advisory
            res_multimer_explicit_one = tool.run(
                {
                    "input": ">ChainA\nMKTIIALSYIFCLVFA\n>ChainB\nGIVEQCCTSICSLYQLENYCN",
                    "job_name": "multimer_explicit_one_test",
                    "model_seeds": [1],
                    "msa_free": False,
                    "sync": False,
                }
            )
            assert res_multimer_explicit_one["model_seeds"] == [1]
            assert res_multimer_explicit_one["diffusion_samples_expected"] == 5
            assert any("Ensemble Advisory" in w for w in res_multimer_explicit_one["warnings"])

            # 5. Multimer AF3 JSON with 1 seed ("modelSeeds": [42]) -> NEVER silently overwritten; preserves [42] + emits Ensemble Advisory
            res_multimer_json_one = tool.run(
                {
                    "input": json.dumps(
                        {
                            "name": "json_one_seed",
                            "modelSeeds": [42],
                            "sequences": [
                                {"protein": {"id": "A", "sequence": "MKTIIALSYIFCLVFA"}},
                                {"ligand": {"id": "B", "ccdCodes": ["ATP"]}},
                            ],
                            "dialect": "alphafold3",
                            "version": 1,
                        }
                    ),
                    "job_name": "multimer_json_one_test",
                    "msa_free": False,
                    "sync": False,
                }
            )
            assert res_multimer_json_one["model_seeds"] == [42]
            assert res_multimer_json_one["diffusion_samples_expected"] == 5
            assert any("Ensemble Advisory" in w for w in res_multimer_json_one["warnings"])

    def test_get_results_resolves_pipeline_job_id_to_job_name(self, mock_af3_env):
        config = AF3Config()
        tool = AF3GetResultsTool(tool_config={"name": "af3_get_results"}, config=config)

        mock_storage = MagicMock()
        mock_bucket = MagicMock()
        mock_missing_blob = MagicMock()
        mock_missing_blob.exists.return_value = False

        mock_conf_blob = MagicMock()
        mock_conf_blob.exists.return_value = True
        mock_conf_blob.download_as_text.return_value = json.dumps(
            {"ranking_score": 0.91, "ptm": 0.90, "iptm": 0.92, "mean_plddt": 93.4}
        )
        mock_cif_blob = MagicMock()
        mock_cif_blob.exists.return_value = True

        def get_blob(path):
            if "af3_ubiquitin_run/" in path:
                return (
                    mock_conf_blob if path.endswith("summary_confidences.json") else mock_cif_blob
                )
            return mock_missing_blob

        mock_bucket.blob.side_effect = get_blob
        mock_storage.bucket.return_value = mock_bucket
        tool.storage_client = mock_storage

        fake_pjob = MagicMock()
        fake_pjob.display_name = "af3_ubiquitin_run"

        with patch("google.cloud.aiplatform.PipelineJob.get", return_value=fake_pjob):
            res = tool.run({"job_id": "alphafold3-inference-pipeline-20261010120000"})

        assert res["status"] == "succeeded"
        assert res["job_id"] == "af3_ubiquitin_run"
        assert (
            res["resolved_from_pipeline_job_id"] == "alphafold3-inference-pipeline-20261010120000"
        )
        assert res["metrics"]["ranking_score"] == 0.91

    def test_get_model_prefers_tagless_serving_container_image_uri(self, mock_af3_env):
        config = AF3Config()
        tool = AF3CheckEndpointTool(tool_config={"name": "af3_check_endpoint"}, config=config)

        tagged_model = MagicMock()
        tagged_model.display_name = "alphafold3"
        tagged_model.resource_name = "projects/test-project/locations/us-central1/models/111"
        tagged_model.container_spec = MagicMock(
            image_uri="us-docker.pkg.dev/vertex-ai-restricted/alphafold3/alphafold3-inference:latest"
        )

        tagless_model = MagicMock()
        tagless_model.display_name = "alphafold3"
        tagless_model.resource_name = "projects/test-project/locations/us-central1/models/222"
        tagless_model.container_spec = MagicMock(
            image_uri="us-docker.pkg.dev/vertex-ai-restricted/alphafold3/alphafold3-inference"
        )

        with patch(
            "google.cloud.aiplatform.Model.list", return_value=[tagged_model, tagless_model]
        ):
            selected = tool.get_model("auto")
            assert selected is tagless_model

    def test_setup_af3_endpoint_script_deploy_and_idempotent_reuse(self):
        import sys
        from pathlib import Path

        scripts_dir = Path(__file__).resolve().parents[4] / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        import setup_af3_endpoint as setup_mod

        mock_ep = MagicMock()
        mock_ep.display_name = "af3-dedicated-ep"
        mock_ep.resource_name = "projects/269454588150/locations/us-central1/endpoints/777"
        mock_ep.name = "777"

        mock_open_model = MagicMock()
        mock_open_model._publisher_model_name = "publishers/google/models/alphafold3@v3_0_4"
        mock_lro = MagicMock()
        mock_lro.operation.name = "projects/269454588150/locations/us-central1/operations/999"
        mock_lro.result.return_value = MagicMock(
            endpoint="projects/269454588150/locations/us-central1/endpoints/777"
        )
        mock_open_model._model_garden_client.deploy.return_value = mock_lro
        mock_open_model.deploy.return_value = mock_ep

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_session.post.return_value = mock_resp

        # 1. Fresh project (default DWS=True): deploys via DWS FlexStart + sets 3,600s timeout
        with (
            patch("vertexai.init"),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.aiplatform.Endpoint.list", return_value=[]),
            patch("google.cloud.aiplatform.Endpoint", return_value=mock_ep),
            patch("vertexai.model_garden.OpenModel", return_value=mock_open_model),
            patch("google.auth.default", return_value=(MagicMock(), "269454588150")),
            patch.object(setup_mod, "AuthorizedSession", return_value=mock_session),
        ):
            res1 = setup_mod.setup_af3_endpoint(
                project_id="269454588150",
                location="us-central1",
            )

        assert res1["created_new"] is True
        assert res1["endpoint_id"] == "777"
        assert res1["provisioning_mode"] == "DWS_FLEX_START"
        mock_open_model._model_garden_client.deploy.assert_called_once()
        sent_req = mock_open_model._model_garden_client.deploy.call_args.args[0]
        assert (
            int(
                sent_req.deploy_config.dedicated_resources.flex_start.max_runtime_duration.total_seconds()
            )
            == 561600
        )

        mock_session.post.assert_called_once_with(
            "https://us-central1-aiplatform.googleapis.com/v1/projects/269454588150/locations/us-central1/endpoints/777:update",
            json={
                "endpoint": {
                    "name": "projects/269454588150/locations/us-central1/endpoints/777",
                    "clientConnectionConfig": {"inferenceTimeout": {"seconds": 3600}},
                }
            },
            timeout=60,
        )

        # 2. Re-run: existing endpoint found -> skips OpenModel.deploy, updates 3,600s timeout
        mock_open_model._model_garden_client.deploy.reset_mock()
        mock_open_model.deploy.reset_mock()
        mock_session.post.reset_mock()
        with (
            patch("vertexai.init"),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.aiplatform.Endpoint.list", return_value=[mock_ep]),
            patch("vertexai.model_garden.OpenModel", return_value=mock_open_model),
            patch("google.auth.default", return_value=(MagicMock(), "269454588150")),
            patch.object(setup_mod, "AuthorizedSession", return_value=mock_session),
        ):
            res2 = setup_mod.setup_af3_endpoint(
                project_id="269454588150",
                location="us-central1",
            )

        assert res2["created_new"] is False
        assert res2["endpoint_id"] == "777"
        mock_open_model._model_garden_client.deploy.assert_not_called()
        mock_open_model.deploy.assert_not_called()
        mock_session.post.assert_called_once()

    def test_check_endpoint_stage_explanations_and_3tier_strategy(self, mock_af3_env):
        """check_af3_endpoint explains PENDING_NODE_ALLOCATION, ADDING_NODES_TO_CLUSTER, and ACTIVE stages."""
        tool = AF3CheckEndpointTool({"name": "check_af3_endpoint"}, AF3Config())
        mock_ep = MagicMock()
        mock_ep.deployed_models = []
        mock_ep.resource_name = "projects/test-proj/locations/us-central1/endpoints/12345"
        mock_ep.display_name = "af3-ep"
        mock_ep.gca_resource.deployed_models = []

        # 1. PENDING_NODE_ALLOCATION
        with (
            patch.object(tool, "get_endpoint", return_value=mock_ep),
            patch.object(
                tool,
                "get_active_deploy_operations",
                return_value=[
                    {
                        "operation_name": "ops/1",
                        "deployment_stage": "PENDING_NODE_ALLOCATION",
                        "create_time": "2026-10-10T18:00:00Z",
                    }
                ],
            ),
        ):
            res_pending = tool.run()
        assert res_pending["status"] == "deploying"
        assert res_pending["deployment_stage"] == "PENDING_NODE_ALLOCATION"
        assert "DWS queueing" in res_pending["deployment_stage_explanation"]
        assert (
            "Custom model serving preemptible Nvidia H100 80GB GPUs per region"
            in res_pending["deployment_stage_explanation"]
        )
        assert len(res_pending["hardware_readiness"]["hardware_allocation_strategy"]) == 3

        # 2. ADDING_NODES_TO_CLUSTER
        with (
            patch.object(tool, "get_endpoint", return_value=mock_ep),
            patch.object(
                tool,
                "get_active_deploy_operations",
                return_value=[
                    {
                        "operation_name": "ops/1",
                        "deployment_stage": "ADDING_NODES_TO_CLUSTER",
                        "create_time": "2026-10-10T18:05:00Z",
                    }
                ],
            ),
        ):
            res_adding = tool.run()
        assert res_adding["status"] == "deploying"
        assert res_adding["deployment_stage"] == "ADDING_NODES_TO_CLUSTER"
        assert "Capacity has been secured!" in res_adding["deployment_stage_explanation"]
        assert "Do NOT cancel or resubmit" in res_adding["deployment_stage_explanation"]

    def test_submit_af3_endpoint_prediction_forwards_num_model_seeds_and_samples(
        self, mock_af3_env
    ):
        """submit_af3_endpoint_prediction wrapper forwards num_model_seeds and num_diffusion_samples."""
        from foldrun_app.skills.job_submission.tools import submit_af3_endpoint_prediction

        fake_tool = MagicMock()
        fake_tool.run.return_value = {"status": "submitted", "diffusion_samples_expected": 25}
        with patch(
            "foldrun_app.skills.job_submission.tools.get_tool",
            return_value=fake_tool,
        ):
            res = submit_af3_endpoint_prediction(
                input=">ChainA\nMKTIIALSYIFCLVFA",
                job_name="monomer_25_samples",
                num_model_seeds=5,
                num_diffusion_samples=5,
            )
        assert res["diffusion_samples_expected"] == 25
        called_args = fake_tool.run.call_args.args[0]
        assert called_args["num_model_seeds"] == 5
        assert called_args["num_diffusion_samples"] == 5

    def test_process_af3_results_task_queues_cloud_run_job_and_finishes_immediately(
        self, mock_af3_env
    ):
        """KFP Stage 4 writes task_config.json, queues foldrun-analysis-job (25 tasks), and exits immediately."""
        from foldrun_app.models.af3.pipeline import process_af3_results_task

        mock_bucket = MagicMock()
        uploaded_blobs = {}

        def make_blob(path):
            b = MagicMock()
            b.name = path
            if path.endswith("query.json"):
                b.download_as_text.return_value = json.dumps(
                    {
                        "name": "test_25",
                        "modelSeeds": [1, 2, 3, 4, 5],
                        "sequences": [{"protein": {"id": "A", "sequence": "MKTIIALSYIFCLVFA"}}],
                    }
                )

            def save_str(content, content_type=None):
                uploaded_blobs[path] = content

            b.upload_from_string.side_effect = save_str
            return b

        mock_bucket.blob.side_effect = make_blob

        raw_blobs = []
        for s in range(1, 6):
            for sample_idx in range(5):
                prefix = f"af3_predictions/test_25/af3_raw/seed-{s}_sample-{sample_idx}"
                cif_b = MagicMock()
                cif_b.name = f"{prefix}/model.cif"
                cif_b.download_as_text.return_value = (
                    "ATOM 1 N N . MET A 1 1 ? 1.0 2.0 3.0 1.00 91.5 1 MET A 1\n"
                )
                sum_b = MagicMock()
                sum_b.name = f"{prefix}/summary_confidences.json"
                sum_b.download_as_text.return_value = json.dumps(
                    {"ranking_score": 0.91, "ptm": 0.89, "mean_plddt": 91.5}
                )
                conf_b = MagicMock()
                conf_b.name = f"{prefix}/confidences.json"
                raw_blobs.extend([cif_b, sum_b, conf_b])

        def list_blobs_side_effect(prefix=""):
            if "af3_raw" in prefix:
                return raw_blobs
            return []

        mock_bucket.list_blobs.side_effect = list_blobs_side_effect
        mock_storage_client = MagicMock()
        mock_storage_client.bucket.return_value = mock_bucket

        mock_session = MagicMock()
        mock_cr_resp = MagicMock()
        mock_cr_resp.status_code = 200
        mock_session.post.return_value = mock_cr_resp

        with (
            patch("google.cloud.storage.Client", return_value=mock_storage_client),
            patch("google.auth.default", return_value=(MagicMock(), "test-project")),
            patch("google.auth.transport.requests.AuthorizedSession", return_value=mock_session),
            patch("time.sleep"),
        ):
            out = process_af3_results_task.python_func(
                project_id="test-project",
                region="us-central1",
                endpoint_location="us-central1",
                endpoint_id="projects/test-project/locations/us-central1/endpoints/123",
                bucket_name="test-bucket",
                job_name="test_25",
                pipeline_job_id="alphafold3-inference-pipeline-20261010190031",
                query_json_path="gs://test-bucket/af3_predictions/test_25/query.json",
                pipeline_root="gs://test-bucket/pipeline_runs/20261010_190031",
                job_prefix="af3_predictions/test_25",
                raw_output_gcs_uri="gs://test-bucket/af3_predictions/test_25/af3_raw",
                start_iso="2026-10-10T19:00:00Z",
                end_iso="2026-10-10T19:05:00Z",
                queue_wait_seconds=1.5,
                inference_seconds=300.0,
                endpoint_status="ACTIVE",
                msa_free=False,
            )

        assert out.ranking_score == pytest.approx(0.91)
        assert out.mean_plddt == pytest.approx(91.5)
        mock_session.post.assert_called_once()
        cr_call = mock_session.post.call_args
        assert "jobs/foldrun-analysis-job:run" in cr_call.args[0]
        assert cr_call.kwargs["json"]["overrides"]["taskCount"] == 25
        assert "af3_predictions/test_25/analysis/task_config.json" in uploaded_blobs
        assert "af3_predictions/test_25/analysis/analysis_metadata.json" in uploaded_blobs
        assert "pipeline_runs/20261010_190031/analysis/analysis_metadata.json" in uploaded_blobs

    def test_run_af3_inference_task_retries_404_unimplemented_on_wakeup(self, mock_af3_env):
        """KFP Stage 2 retries transient HTTP 404 UNIMPLEMENTED during Dedicated Endpoint GFE route propagation."""
        from foldrun_app.models.af3.pipeline import run_af3_inference_task

        mock_bucket = MagicMock()
        mock_bucket.list_blobs.return_value = []

        def make_blob(path):
            b = MagicMock()
            b.name = path
            b.exists.return_value = False
            if path.endswith("input.json"):
                b.download_as_text.return_value = json.dumps(
                    {
                        "name": "test_404_retry",
                        "modelSeeds": [1],
                        "sequences": [{"protein": {"id": "A", "sequence": "MKTIIALSYIFCLVFA"}}],
                    }
                )
            return b

        mock_bucket.blob.side_effect = make_blob
        mock_storage_client = MagicMock()
        mock_storage_client.bucket.return_value = mock_bucket

        mock_pred_resp = MagicMock()
        mock_pred_resp.predictions = [
            {
                "structure_cif": "ATOM 1 N N . MET A 1 1 ? 1.0 2.0 3.0 1.00 91.5 1 MET A 1\n",
                "summary_confidences": {"ranking_score": 0.88, "ptm": 0.88},
                "plddt": [91.5],
                "pae": [[0.5]],
            }
        ]

        mock_ep = MagicMock()
        mock_ep.resource_name = "projects/test-project/locations/us-central1/endpoints/123"
        mock_ep.predict.side_effect = [
            ValueError(
                'Failed to make prediction request. Status code:404, response: {"error":{"code":404,"message":"The server could not process your request because the specified resource was not found.","status":"UNIMPLEMENTED"}}'
            ),
            ValueError("Failed to make prediction request. Status code:503, response: unavailable"),
            mock_pred_resp,
        ]

        with (
            patch("google.cloud.storage.Client", return_value=mock_storage_client),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.aiplatform.Endpoint", return_value=mock_ep),
            patch("time.sleep"),
        ):
            out = run_af3_inference_task.python_func(
                project_id="test-project",
                region="us-central1",
                endpoint_location="us-central1",
                endpoint_id="projects/test-project/locations/us-central1/endpoints/123",
                bucket_name="test-bucket",
                job_name="test_404_retry",
                pipeline_job_id="alphafold3-inference-pipeline-20261010230519",
                query_json_path="gs://test-bucket/af3_predictions/test_404_retry/input.json",
                job_prefix="af3_predictions/test_404_retry",
                slot_blob_path="af3_predictions/.locks/123_slot_0.json",
                slot_index=0,
                active_replicas=1,
                queue_wait_seconds=10.0,
                msa_free=True,
                num_diffusion_samples=5,
                timeout_seconds=1800,
            )

        assert out.slot_released is True
        assert mock_ep.predict.call_count == 3

    def test_run_af3_inference_task_retries_400_not_ready_for_prediction_yet(self, mock_af3_env):
        """KFP Stage 2 retries HTTP 400 FailedPrecondition ('not ready for prediction yet') during DWS/Scale-to-Zero wakeup."""
        from google.api_core.exceptions import FailedPrecondition

        from foldrun_app.models.af3.pipeline import run_af3_inference_task

        mock_bucket = MagicMock()
        mock_bucket.list_blobs.return_value = []

        def make_blob(path):
            b = MagicMock()
            b.name = path
            b.exists.return_value = False
            if path.endswith("input.json"):
                b.download_as_text.return_value = json.dumps(
                    {
                        "name": "test_400_not_ready",
                        "modelSeeds": [1],
                        "sequences": [{"protein": {"id": "A", "sequence": "MKTIIALSYIFCLVFA"}}],
                    }
                )
            return b

        mock_bucket.blob.side_effect = make_blob
        mock_storage_client = MagicMock()
        mock_storage_client.bucket.return_value = mock_bucket

        mock_pred_resp = MagicMock()
        mock_pred_resp.predictions = [
            {
                "structure_cif": "ATOM 1 N N . MET A 1 1 ? 1.0 2.0 3.0 1.00 91.5 1 MET A 1\n",
                "summary_confidences": {"ranking_score": 0.90, "ptm": 0.90},
                "plddt": [91.5],
                "pae": [[0.5]],
            }
        ]

        mock_ep = MagicMock()
        mock_ep.resource_name = "projects/test-project/locations/us-central1/endpoints/123"
        mock_ep.predict.side_effect = [
            FailedPrecondition(
                'The model "google-alphafold3-v3_0_4-1791651264" is not ready for prediction yet.'
            ),
            RuntimeError("429 Endpoint is undergoing scale-up from zero"),
            mock_pred_resp,
        ]

        with (
            patch("google.cloud.storage.Client", return_value=mock_storage_client),
            patch("google.cloud.aiplatform.init"),
            patch("google.cloud.aiplatform.Endpoint", return_value=mock_ep),
            patch("time.sleep"),
        ):
            out = run_af3_inference_task.python_func(
                project_id="test-project",
                region="us-central1",
                endpoint_location="us-central1",
                endpoint_id="projects/test-project/locations/us-central1/endpoints/123",
                bucket_name="test-bucket",
                job_name="test_400_not_ready",
                pipeline_job_id="alphafold3-inference-pipeline-20261014232500",
                query_json_path="gs://test-bucket/af3_predictions/test_400_not_ready/input.json",
                job_prefix="af3_predictions/test_400_not_ready",
                slot_blob_path="af3_predictions/.locks/123_slot_0.json",
                slot_index=0,
                active_replicas=1,
                queue_wait_seconds=5.0,
                msa_free=True,
                num_diffusion_samples=5,
                timeout_seconds=1800,
            )

        assert out.slot_released is True
        assert mock_ep.predict.call_count == 3

    def test_capacity_pool_dws_default_and_any_reservation_config(self, mock_af3_env):
        """AF3Config and setup_af3_endpoint default to DWS and support ANY_RESERVATION config option."""
        import sys
        from pathlib import Path

        scripts_dir = Path(__file__).resolve().parents[4] / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        import setup_af3_endpoint as setup_mod

        # Default is DWS
        cfg_default = AF3Config()
        assert cfg_default.capacity_pool == "DWS"
        assert cfg_default.use_dws is True
        assert setup_mod.resolve_capacity_pool_config() == (True, False, "ANY_RESERVATION")

        # ANY_RESERVATION via capacity_pool argument or env
        assert setup_mod.resolve_capacity_pool_config(capacity_pool="ANY_RESERVATION") == (
            False,
            False,
            "ANY_RESERVATION",
        )
        parsed = setup_mod.parse_args(
            ["--project-id", "test-proj", "--capacity-pool", "ANY_RESERVATION"]
        )
        assert parsed.capacity_pool == "ANY_RESERVATION"

        with patch.dict(os.environ, {"AF3_CAPACITY_POOL": "ANY_RESERVATION"}):
            cfg_any = AF3Config()
            assert cfg_any.capacity_pool == "ANY_RESERVATION"
            assert cfg_any.use_dws is False
            assert cfg_any.reservation_affinity_type == "ANY_RESERVATION"

        # Verify whole-day timedelta (e.g. 7 days = 604800s where .seconds == 0) is recognized as DWS_FLEX_START
        from datetime import timedelta

        assert setup_mod._duration_seconds(timedelta(days=7)) == 604800
        assert setup_mod._duration_seconds(timedelta(days=0)) == 0

        dm_7d = MagicMock()
        dm_7d.dedicated_resources.min_replica_count = 0
        dm_7d.dedicated_resources.max_replica_count = 1
        dm_7d.dedicated_resources.initial_replica_count = 1
        dm_7d.dedicated_resources.flex_start.max_runtime_duration = timedelta(days=7)
        dm_7d.dedicated_resources.spot = False
        rep_7d = AF3CheckEndpointTool.extract_replica_counts(dm_7d)
        assert rep_7d["provisioning_mode"] == "DWS_FLEX_START"

    def test_scale_to_zero_defaults_and_check_endpoint(self, mock_af3_env):
        """AF3Config defaults to scale-to-zero and check_af3_endpoint reports it."""
        config = AF3Config()
        assert config.min_replica_count == 0
        assert config.max_replica_count == 1
        assert config.initial_replica_count == 1
        assert config.min_scaleup_period_seconds == 300
        assert config.idle_scaledown_period_seconds == 1200

        check_tool = AF3CheckEndpointTool(tool_config={"name": "af3_check_endpoint"}, config=config)
        dedicated = MagicMock()
        mock_endpoint = MagicMock()
        mock_endpoint.resource_name = "projects/test-project/locations/us-central1/endpoints/12345"
        mock_endpoint.display_name = "af3-stz-ep"
        mock_endpoint.deployed_models = [MagicMock(id="dep-stz", dedicated_resources=dedicated)]

        dedicated.min_replica_count = 0
        dedicated.max_replica_count = 1
        dedicated.initial_replica_count = 1
        with patch.object(check_tool, "get_endpoint", return_value=mock_endpoint):
            check_res = check_tool.run()

        assert check_res["status"] == "ready"
        assert check_res["deployed_models"][0]["min_replica_count"] == 0
        assert check_res["deployed_models"][0]["scale_to_zero_enabled"] is True
        assert "$0.00/hr when scaled to 0" in check_res["idle_cost"]

    def test_setup_af3_endpoint_reconcile_and_bucket_iam(self, mock_af3_env):
        """setup_af3_endpoint mutates replicas on existing endpoint and grants tenant SA bucket IAM."""
        import importlib.util
        from pathlib import Path

        script_path = Path(__file__).resolve().parents[4] / "scripts" / "setup_af3_endpoint.py"
        spec = importlib.util.spec_from_file_location("setup_af3_endpoint", script_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # 1. Verify ensure_endpoint_bucket_iam extracts tenant SA from 403 error and grants objectUser
        mock_ep = MagicMock()
        mock_ep.__class__.__module__ = "google.cloud.aiplatform.matching_engine"
        mock_ep.predict.side_effect = RuntimeError(
            "403 custom-online-prediction@w5b8aada84bff4c76p-tp.iam.gserviceaccount.com "
            "does not have storage.objects.create access"
        )
        mock_bucket = MagicMock()
        mock_policy = MagicMock()
        mock_policy.bindings = []
        mock_bucket.get_iam_policy.return_value = mock_policy
        mock_storage_client = MagicMock()
        mock_storage_client.bucket.return_value = mock_bucket

        with (
            patch.object(mod.aiplatform, "Endpoint", return_value=mock_ep),
            patch.object(mod, "_get_credentials", return_value=MagicMock()),
            patch("google.cloud.storage.Client", return_value=mock_storage_client),
        ):
            sa = mod.ensure_endpoint_bucket_iam(
                project_id="test-project",
                location="us-central1",
                endpoint_resource_name="projects/test-project/locations/us-central1/endpoints/123",
                bucket_name="test-project-foldrun-data",
            )

        assert sa == "custom-online-prediction@w5b8aada84bff4c76p-tp.iam.gserviceaccount.com"
        _, predict_kwargs = mock_ep.predict.call_args
        assert (
            predict_kwargs["parameters"]["output_dir"]
            == "gs://test-project-foldrun-data/af3_predictions/.iam_probe"
        )
        mock_bucket.set_iam_policy.assert_called_once_with(mock_policy)
        assert any(
            b.get("role") == "roles/storage.objectUser"
            and "serviceAccount:custom-online-prediction@w5b8aada84bff4c76p-tp.iam.gserviceaccount.com"
            in b.get("members", set())
            for b in mock_policy.bindings
        )

    def test_all_instructions_pass_adk_inject_session_state(self):
        import asyncio
        from pathlib import Path

        from google.adk.utils.instructions_utils import inject_session_state

        app_dir = Path(__file__).resolve().parents[4] / "foldrun_app"
        ctx = MagicMock()
        ctx.state = {}

        async def _check():
            for p in sorted(app_dir.rglob("*")):
                if p.suffix in (".py", ".md") and ("instruction" in p.name or "SKILL" in p.name):
                    text = p.read_text()
                    rendered = await inject_session_state(text, ctx)
                    assert len(rendered) > 0

        asyncio.run(_check())
