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
from foldrun_app.models.af3.tools.deploy_endpoint import AF3DeployEndpointTool
from foldrun_app.models.af3.tools.get_results import AF3GetResultsTool
from foldrun_app.models.af3.tools.open_viewer import AF3OpenViewerTool
from foldrun_app.models.af3.tools.submit_prediction import AF3SubmitPredictionTool
from foldrun_app.models.af3.tools.undeploy_endpoint import AF3UndeployEndpointTool


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

    def test_deploy_endpoint_async(self, mock_af3_env):
        config = AF3Config()
        tool = AF3DeployEndpointTool(tool_config={"name": "af3_deploy_endpoint"}, config=config)

        mock_endpoint = MagicMock()
        mock_endpoint.resource_name = "projects/test-project/locations/us-central1/endpoints/12345"
        mock_endpoint.deployed_models = []

        mock_model = MagicMock()
        mock_model.resource_name = "projects/test-project/locations/us-central1/models/af3-model"

        with (
            patch.object(tool, "get_endpoint", return_value=mock_endpoint),
            patch.object(tool, "get_model", return_value=mock_model),
        ):
            result = tool.run({"sync": False})

        assert result["status"] == "deploying"
        assert "5-8 minutes" in result["estimated_wait_minutes"]
        mock_endpoint.deploy.assert_called_once()
        assert mock_endpoint.deploy.call_args.kwargs.get("sync") is False

    def test_deploy_endpoint_already_deployed(self, mock_af3_env):
        config = AF3Config()
        tool = AF3DeployEndpointTool(tool_config={"name": "af3_deploy_endpoint"}, config=config)

        dedicated = MagicMock()
        dedicated.min_replica_count = 1
        dedicated.max_replica_count = 1
        mock_endpoint = MagicMock()
        mock_endpoint.resource_name = "projects/test-project/locations/us-central1/endpoints/12345"
        mock_endpoint.deployed_models = [MagicMock(id="dep-1", dedicated_resources=dedicated)]

        with patch.object(tool, "get_endpoint", return_value=mock_endpoint):
            result = tool.run()

        assert result["status"] == "already_deployed"
        assert result["min_replica_count"] == 1
        assert result["max_replica_count"] == 1
        assert mock_endpoint.deploy.called is False

    def test_deploy_endpoint_scale_replicas_in_place(self, mock_af3_env):
        config = AF3Config()
        tool = AF3DeployEndpointTool(tool_config={"name": "af3_deploy_endpoint"}, config=config)

        dedicated = MagicMock()
        dedicated.min_replica_count = 1
        dedicated.max_replica_count = 1
        mock_endpoint = MagicMock()
        mock_endpoint.resource_name = "projects/test-project/locations/us-central1/endpoints/12345"
        mock_endpoint.deployed_models = [MagicMock(id="dep-1", dedicated_resources=dedicated)]

        mock_client = MagicMock()
        with (
            patch.object(tool, "get_endpoint", return_value=mock_endpoint),
            patch(
                "google.cloud.aiplatform_v1.EndpointServiceClient",
                return_value=mock_client,
            ),
        ):
            result = tool.run({"min_replica_count": 3, "max_replica_count": 4, "sync": True})

        assert result["status"] == "scaled"
        assert result["previous_min_replica_count"] == 1
        assert result["min_replica_count"] == 3
        assert result["max_replica_count"] == 4
        assert "$33.18/hr" in result["idle_cost"]
        mock_client.mutate_deployed_model.assert_called_once()
        assert mock_endpoint.deploy.called is False

    def test_undeploy_endpoint_all(self, mock_af3_env):
        config = AF3Config()
        tool = AF3UndeployEndpointTool(tool_config={"name": "af3_undeploy_endpoint"}, config=config)

        mock_endpoint = MagicMock()
        mock_endpoint.resource_name = "projects/test-project/locations/us-central1/endpoints/12345"
        mock_endpoint.deployed_models = [MagicMock(id="dep-1")]

        with patch.object(tool, "get_endpoint", return_value=mock_endpoint):
            result = tool.run({"sync": True})

        assert result["status"] == "succeeded"
        assert result["idle_cost"] == "$0.00/hr"
        mock_endpoint.undeploy_all.assert_called_once_with(sync=True)

    def test_undeploy_endpoint_specific_model(self, mock_af3_env):
        config = AF3Config()
        tool = AF3UndeployEndpointTool(tool_config={"name": "af3_undeploy_endpoint"}, config=config)

        mock_endpoint = MagicMock()
        mock_endpoint.resource_name = "projects/test-project/locations/us-central1/endpoints/12345"
        mock_endpoint.deployed_models = [MagicMock(id="dep-1")]

        with patch.object(tool, "get_endpoint", return_value=mock_endpoint):
            result = tool.run({"deployed_model_id": "dep-1", "sync": True})

        assert result["status"] == "succeeded"
        mock_endpoint.undeploy.assert_called_once_with(deployed_model_id="dep-1", sync=True)

    def test_undeploy_endpoint_already_undeployed(self, mock_af3_env):
        config = AF3Config()
        tool = AF3UndeployEndpointTool(tool_config={"name": "af3_undeploy_endpoint"}, config=config)

        mock_endpoint = MagicMock()
        mock_endpoint.resource_name = "projects/test-project/locations/us-central1/endpoints/12345"
        mock_endpoint.deployed_models = []

        with patch.object(tool, "get_endpoint", return_value=mock_endpoint):
            result = tool.run()

        assert result["status"] == "already_undeployed"
        assert result["idle_cost"] == "$0.00/hr"
        assert mock_endpoint.undeploy.called is False
        assert mock_endpoint.undeploy_all.called is False

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

    def test_deploy_endpoint_replica_cap_enforced(self, mock_af3_env):
        config = AF3Config()
        tool = AF3DeployEndpointTool(tool_config={"name": "af3_deploy_endpoint"}, config=config)
        result = tool.run({"min_replica_count": 10, "max_replica_count": 20})
        assert result["status"] == "error"
        assert "Invalid replica count" in result["message"]

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

    def test_deploy_endpoint_skips_when_active_operation_in_progress(self, mock_af3_env):
        config = AF3Config()
        tool = AF3DeployEndpointTool(tool_config={"name": "af3_deploy_endpoint"}, config=config)
        mock_endpoint = MagicMock()
        mock_endpoint.resource_name = (
            "projects/test-project/locations/us-central1/endpoints/mg-endpoint-12345"
        )
        mock_endpoint.deployed_models = []

        active_ops = [
            {
                "operation_name": "projects/test-project/locations/us-central1/endpoints/mg-endpoint-12345/operations/999",
                "deployment_stage": "GETTING_CONTAINER_IMAGE",
                "create_time": "2026-10-06T04:52:00Z",
            }
        ]
        with (
            patch.object(tool, "get_endpoint", return_value=mock_endpoint),
            patch.object(tool, "get_active_deploy_operations", return_value=active_ops),
        ):
            result = tool.run({"sync": False})

        assert result["status"] == "deploying"
        assert "Skipping duplicate deployment" in result["message"]
        mock_endpoint.deploy.assert_not_called()

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


