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

"""Unit tests for ESMFold2 tools, KFP pipeline compilation, and cost estimation."""

from unittest.mock import MagicMock, patch

import pytest
import yaml
from kfp import compiler

from foldrun_app.core.tools.cleanup_gcs_files import CleanupGCSFilesTool
from foldrun_app.core.tools.get_job_details import GetJobDetailsTool
from foldrun_app.core.tools.list_jobs import ListJobsTool
from foldrun_app.models.esmfold2.base import ESMFOLD2Tool
from foldrun_app.models.esmfold2.config import ESMFOLD2Config
from foldrun_app.models.esmfold2.tools.analyze_job import ESMFOLD2JobAnalysisTool
from foldrun_app.models.esmfold2.tools.get_analysis_results import ESMFOLD2GetAnalysisResultsTool
from foldrun_app.models.esmfold2.tools.submit_prediction import ESMFOLD2SubmitPredictionTool
from foldrun_app.models.esmfold2.utils.pipeline_utils import load_vertex_pipeline
from foldrun_app.skills.cost_estimation.tools import estimate_job_cost
from foldrun_app.skills.job_submission import submit_esmfold2_prediction

UBIQUITIN_FASTA = (
    ">ubiquitin\nMQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
)


class TestEsmfold2HardwareAndPipelineCompilation:
    """Tests for GPU auto-recommendation and KFP pipeline compilation (use_msa=False vs True)."""

    def test_recommend_gpu_tiers(self):
        # Fast variant (default): <=1000 -> L4, <=2500 -> A100, >2500 -> A100_80GB
        assert ESMFOLD2Tool._recommend_gpu(76, "fast") == "L4"
        assert ESMFOLD2Tool._recommend_gpu(1000, "fast") == "L4"
        assert ESMFOLD2Tool._recommend_gpu(1001, "fast") == "A100"
        assert ESMFOLD2Tool._recommend_gpu(2500, "fast") == "A100"
        assert ESMFOLD2Tool._recommend_gpu(2501, "fast") == "A100_80GB"

        # Full variant: <=700 -> L4, <=2000 -> A100, >2000 -> A100_80GB
        assert ESMFOLD2Tool._recommend_gpu(700, "full") == "L4"
        assert ESMFOLD2Tool._recommend_gpu(701, "full") == "A100"
        assert ESMFOLD2Tool._recommend_gpu(2000, "full") == "A100"
        assert ESMFOLD2Tool._recommend_gpu(2001, "full") == "A100_80GB"

    def test_compile_kfp_pipeline_zero_msa_vs_with_msa(self, mock_env_vars, tmp_path):
        # 1. ESMFold2-Fast (use_msa=False): no MSA component in compiled YAML
        fast_pipeline_fn = load_vertex_pipeline(enable_flex_start=True, use_msa=False)
        fast_yaml_path = tmp_path / "esmfold2_fast.yaml"
        compiler.Compiler().compile(
            pipeline_func=fast_pipeline_fn, package_path=str(fast_yaml_path)
        )
        fast_spec = yaml.safe_load(fast_yaml_path.read_text(encoding="utf-8"))
        fast_execs = set(fast_spec["deploymentSpec"]["executors"].keys())
        assert any("configure-seeds" in k for k in fast_execs)
        assert any("predict-esmfold2" in k for k in fast_execs)
        assert not any("msa-pipeline-esmfold2" in k for k in fast_execs)

        # 2. Full ESMFold2 (use_msa=True): includes msa_pipeline_esmfold2 component
        full_pipeline_fn = load_vertex_pipeline(enable_flex_start=True, use_msa=True)
        full_yaml_path = tmp_path / "esmfold2_full.yaml"
        compiler.Compiler().compile(
            pipeline_func=full_pipeline_fn, package_path=str(full_yaml_path)
        )
        full_spec = yaml.safe_load(full_yaml_path.read_text(encoding="utf-8"))
        full_execs = set(full_spec["deploymentSpec"]["executors"].keys())
        assert any("configure-seeds" in k for k in full_execs)
        assert any("msa-pipeline-esmfold2" in k for k in full_execs)
        assert any("predict-esmfold2" in k for k in full_execs)


class TestEsmfold2SubmitPrediction:
    """Tests for ESMFOLD2SubmitPredictionTool and submit_esmfold2_prediction skill wrapper."""

    def test_submit_esmfold2_fast_vs_full(self, mock_env_vars):
        cfg = ESMFOLD2Config()
        tool = ESMFOLD2SubmitPredictionTool({"name": "esmfold2_submit_prediction"}, cfg)

        mock_pj = MagicMock()
        mock_pj.resource_name = "projects/test-project/locations/us-central1/pipelineJobs/esm-123"
        mock_pj.state.name = "PIPELINE_STATE_PENDING"

        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = MagicMock()
        mock_storage = MagicMock()
        mock_storage.bucket.return_value = mock_bucket

        with (
            patch.object(
                tool,
                "_ensure_filestore_config",
                return_value=("10.0.0.2", "projects/p/global/networks/default"),
            ),
            patch("google.cloud.storage.Client", return_value=mock_storage),
            patch("google.cloud.aiplatform.PipelineJob", return_value=mock_pj) as mock_pj_cls,
        ):
            # Fast variant (default): forces use_msa=False and uses esmfold2_fast_cache_path
            res_fast = tool.run({"input": UBIQUITIN_FASTA, "job_name": "ubq_fast"})
            assert res_fast["status"] == "submitted"
            assert res_fast["model_variant"] == "fast"
            assert res_fast["use_msa"] is False
            assert res_fast["input_info"]["model_variant"] == "fast"
            assert res_fast["input_info"]["use_msa"] is False
            assert "none" in res_fast["hardware"]["msa_pipeline"]
            assert "NVIDIA_L4" in res_fast["hardware"]["prediction"]

            fast_params = mock_pj_cls.call_args.kwargs["parameter_values"]
            assert fast_params["nfs_cache_path"] == f"{cfg.nfs_mount_point}/{cfg.fast_cache_path}"

            # Full variant: defaults use_msa=True and uses full_cache_path
            res_full = tool.run(
                {"input": UBIQUITIN_FASTA, "job_name": "ubq_full", "model_variant": "full"}
            )
            assert res_full["model_variant"] == "full"
            assert res_full["use_msa"] is True
            assert res_full["input_info"]["model_variant"] == "full"
            assert res_full["input_info"]["use_msa"] is True
            assert "c2-standard-16" in res_full["hardware"]["msa_pipeline"]

            full_params = mock_pj_cls.call_args.kwargs["parameter_values"]
            assert full_params["nfs_cache_path"] == f"{cfg.nfs_mount_point}/{cfg.full_cache_path}"

    def test_skill_wrapper_forwards_to_esmfold2_submit_prediction(self):
        mock_backend = MagicMock()
        mock_backend.run.return_value = {"status": "submitted", "job_id": "esm-456"}
        with patch(
            "foldrun_app.skills.job_submission.tools.get_tool", return_value=mock_backend
        ) as mock_get:
            res = submit_esmfold2_prediction(
                input=UBIQUITIN_FASTA,
                job_name="ubq-wrapper",
                model_variant="fast",
                num_model_seeds=2,
            )
            mock_get.assert_called_once_with("esmfold2_submit_prediction")
            forwarded = mock_backend.run.call_args[0][0]
            assert forwarded["job_name"] == "ubq-wrapper"
            assert forwarded["model_variant"] == "fast"
            assert forwarded["num_model_seeds"] == 2
            assert res["job_id"] == "esm-456"

    def test_cost_estimation_esmfold2_fast_vs_full(self):
        fast_est = estimate_job_cost(job_type="esmfold2_fast", sequence_length=300)
        assert fast_est["job_type"] == "esmfold2_fast"
        assert fast_est["gpu_type"] == "L4"
        assert len(fast_est["dws_flex_start"]["phases"]) == 1
        assert fast_est["dws_flex_start"]["phases"][0]["phase"] == "Predict"

        full_est = estimate_job_cost(job_type="esmfold2", sequence_length=300)
        assert full_est["job_type"] == "esmfold2"
        assert len(full_est["dws_flex_start"]["phases"]) == 2
        assert full_est["dws_flex_start"]["phases"][0]["phase"] == "MSA / Data Pipeline"
        assert full_est["dws_flex_start"]["phases"][1]["phase"] == "Predict"


class TestEsmfold2AnalysisAndCoreTools:
    """Tests covering Gerrit review edge cases in analysis, viewer URL, and core tools."""

    def test_get_analysis_results_builds_viewer_url_without_pkl_uri(
        self, mock_env_vars, monkeypatch
    ):
        monkeypatch.setenv("FOLDRUN_VIEWER_URL", "https://viewer.example.com/")
        cfg = ESMFOLD2Config()
        tool = ESMFOLD2GetAnalysisResultsTool({"name": "esmfold2_get_analysis_results"}, cfg)
        url = tool._build_viewer_url(
            job_id="esmfold2-inference-pipeline-20261109113348",
            summary_uri="gs://test-bucket/pipeline_runs/123/analysis/summary.json",
            summary={
                "job_type": "esmfold2_fast",
                "best_prediction": {
                    "cif_uri": "gs://test-bucket/pipeline_runs/123/seed_1_sample_0_model.cif",
                    "confidences_uri": "gs://test-bucket/pipeline_runs/123/seed_1_sample_0_confidences.json",
                },
            },
        )
        assert url == "https://viewer.example.com/job/esmfold2-inference-pipeline-20261109113348"

    def test_analyze_job_resolves_case_sensitive_query_json_from_parameter_values(
        self, mock_env_vars
    ):
        cfg = ESMFOLD2Config()
        bucket = cfg.bucket_name
        tool = ESMFOLD2JobAnalysisTool({"name": "esmfold2_job_analysis_parallel"}, cfg)

        exact_uri = f"gs://{bucket}/esmfold2_queries/UpperCase.Job_Name_Exceeding_63_Characters_Limit_0123456789ABCDEF.json"
        mock_job = MagicMock()
        mock_job.labels = {
            "query_name": "uppercase_job_name_exceeding_63_characters_limit_0123456789abcd"
        }
        mock_job.runtime_config.gcs_output_directory = f"gs://{bucket}/pipeline_runs/20261109_1200"
        mock_job.runtime_config.parameter_values = {"query_json_path": exact_uri}

        sample = {
            "sample_name": "seed_1_sample_0",
            "seed": 1,
            "sample": 0,
            "cif_uri": f"gs://{bucket}/pipeline_runs/20261109_1200/seed_1_sample_0_model.cif",
            "confidences_uri": f"gs://{bucket}/pipeline_runs/20261109_1200/seed_1_sample_0_confidences.json",
            "pae_uri": f"gs://{bucket}/pipeline_runs/20261109_1200/seed_1_sample_0_pae.npz",
        }
        with patch.object(tool, "_discover_esmfold2_samples", return_value=[sample]):
            res = tool._discover_and_build_task_config(
                job=mock_job,
                job_id="esm-123",
                analysis_path=f"gs://{bucket}/pipeline_runs/20261109_1200/analysis/",
                arguments={},
            )
        assert isinstance(res, tuple)
        task_cfg, count, _ = res
        assert count == 1
        assert task_cfg["query_json_uri"] == exact_uri

        # Also verify path-traversal in labels still raises ValueError under strict_label_validation
        bad_job = MagicMock()
        bad_job.labels = {"query_name": "../secret"}
        bad_job.runtime_config.gcs_output_directory = f"gs://{bucket}/pipeline_runs/20261109_1200"
        bad_job.runtime_config.parameter_values = {"query_json_path": exact_uri}
        with (
            patch.object(tool, "_discover_esmfold2_samples", return_value=[sample]),
            pytest.raises(ValueError, match="Path traversal"),
        ):
            tool._discover_and_build_task_config(
                job=bad_job,
                job_id="esm-123",
                analysis_path=f"gs://{bucket}/pipeline_runs/20261109_1200/analysis/",
                arguments={},
            )

    def test_core_job_and_storage_tools_classify_esmfold2_correctly(self, mock_env_vars):
        cfg = ESMFOLD2Config()
        bucket = cfg.bucket_name

        # 1. GetJobDetailsTool does NOT misclassify ESMFold2 query_json_path as openfold3
        details_tool = GetJobDetailsTool({"name": "get_job_details"}, cfg)
        mock_job = MagicMock()
        mock_job.name = "projects/p/locations/us-central1/pipelineJobs/esm-1"
        mock_job.display_name = "esmfold2-ubq"
        mock_job.state.name = "PIPELINE_STATE_SUCCEEDED"
        mock_job.labels = {
            "model_type": "esmfold2",
            "model_variant": "fast",
            "job_type": "monomer",
            "query_name": "esmfold2-ubq",
            "num_tokens": "76",
            "gpu_type": "l4",
        }
        mock_job.create_time = None
        mock_job.start_time = None
        mock_job.end_time = None
        mock_job.job_detail = None
        mock_job.error = None
        mock_job.runtime_config.gcs_output_directory = f"gs://{bucket}/pipeline_runs/20261109_1200"
        mock_job.runtime_config.parameter_values = {
            "query_json_path": f"gs://{bucket}/esmfold2_queries/esmfold2-ubq.json",
            "num_model_seeds": 1,
            "num_diffusion_samples": 1,
        }

        mock_blob = MagicMock()
        mock_blob.download_as_text.return_value = '{"name": "ubq", "sequences": []}'
        mock_storage_client = MagicMock()
        mock_storage_client.bucket.return_value.blob.return_value = mock_blob

        with (
            patch("foldrun_app.core.tools.get_job_details.get_pipeline_job", return_value=mock_job),
            patch(
                "foldrun_app.core.tools.get_job_details.storage.Client",
                return_value=mock_storage_client,
            ),
        ):
            details = details_tool.run({"job_id": "esm-1"})
        assert details["parameters"]["model_type"] == "esmfold2"
        assert details["resubmission_ready"]["suggested_params"]["model_type"] == "esmfold2"
        assert details["input"]["header"] == "ESMFold2 query JSON"

        # 2. CleanupGCSFilesTool accepts esmfold2_queries/ path in dry-run search mode
        cleanup_tool = CleanupGCSFilesTool({"name": "cleanup_gcs_files"}, cfg)
        mock_blob.exists.return_value = True
        mock_blob.size = 512
        with patch(
            "foldrun_app.core.tools.cleanup_gcs_files.storage.Client",
            return_value=mock_storage_client,
        ):
            cleanup_res = cleanup_tool.run(
                {
                    "gcs_paths": [f"gs://{bucket}/esmfold2_queries/esmfold2-ubq.json"],
                    "search_only": True,
                }
            )
        assert cleanup_res["files_found"] == 1

        # 3. ListJobsTool maps esmfold2 + model_variant to ESMFold2-Fast / ESMFold2
        with patch("google.cloud.storage.Client", return_value=mock_storage_client):
            list_tool = ListJobsTool({"name": "list_jobs"}, cfg)
        with (
            patch("foldrun_app.core.tools.list_jobs.list_pipeline_jobs", return_value=[mock_job]),
            patch(
                "foldrun_app.core.tools.list_jobs.get_job_status",
                return_value={
                    "state": "PIPELINE_STATE_SUCCEEDED",
                    "create_time": "2026-11-09T12:00:00Z",
                    "pipeline_spec": "esmfold2-inference-pipeline",
                    "labels": dict(mock_job.labels),
                },
            ),
            patch.object(list_tool, "_check_analysis_exists_for_job", return_value=True),
            patch.object(list_tool, "_format_state", return_value="Succeeded"),
        ):
            listed = list_tool.run({"check_analysis": True})
        assert listed["jobs"][0]["experiment"] == "ESMFold2-Fast Monomer"
