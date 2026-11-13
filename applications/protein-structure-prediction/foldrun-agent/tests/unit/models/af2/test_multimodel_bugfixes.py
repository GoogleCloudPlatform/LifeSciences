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

"""Regression tests for multi-model job management, input formatting, and analysis cleanup."""

import os
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _mock_env():
    env = {
        "GCP_PROJECT_ID": "test-project",
        "GCP_REGION": "us-central1",
        "GCS_BUCKET_NAME": "test-bucket",
        "FILESTORE_ID": "test-nfs",
        "ALPHAFOLD_COMPONENTS_IMAGE": "test-af2:latest",
        "OPENFOLD3_COMPONENTS_IMAGE": "test-of3:latest",
        "BOLTZ2_COMPONENTS_IMAGE": "test-boltz2:latest",
        "FOLDRUN_VIEWER_URL": "https://viewer.example.com",
    }
    with patch.dict(os.environ, env, clear=False):
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            yield


def _make_job(
    job_id: str,
    display_name: str,
    labels: dict[str, str],
    params: dict[str, object] | None = None,
    gcs_output_directory: str = "gs://test-bucket/pipeline_runs/20260301_120000",
) -> MagicMock:
    job = MagicMock()
    job.name = f"projects/test-project/locations/us-central1/pipelineJobs/{job_id}"
    job.display_name = display_name
    job.labels = labels
    job.state.name = "PIPELINE_STATE_SUCCEEDED"
    job.create_time = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    job.start_time = datetime(2026, 3, 1, 12, 1, tzinfo=UTC)
    job.end_time = datetime(2026, 3, 1, 12, 10, tzinfo=UTC)
    job.error = None
    job.job_detail = None
    job.runtime_config.parameter_values = params or {}
    job.runtime_config.gcs_output_directory = gcs_output_directory
    return job


class TestListJobsMultiModelSupport:
    """Verify list_jobs supports query_name, num_tokens, and model-specific experiment labels."""

    def test_filters_and_labels_of3_boltz2_and_af3_jobs(self):
        from foldrun_app.core.config import CoreConfig
        from foldrun_app.core.tools.list_jobs import ListJobsTool

        tool = ListJobsTool(tool_config={"name": "list_jobs"}, config=CoreConfig())
        jobs = [
            _make_job(
                "af2-1",
                "af2-ubiquitin",
                {
                    "model_type": "alphafold2",
                    "job_type": "monomer",
                    "seq_name": "ubiquitin",
                    "seq_len": "76",
                },
            ),
            _make_job(
                "of3-1",
                "of3-kinase",
                {
                    "model_type": "openfold3",
                    "job_type": "complex",
                    "query_name": "kinase_ligand",
                    "num_tokens": "420",
                },
            ),
            _make_job(
                "boltz-1",
                "boltz-inhibitor",
                {
                    "model_type": "boltz2",
                    "job_type": "complex",
                    "query_name": "kinase_inhibitor",
                    "num_tokens": "510",
                },
            ),
            _make_job(
                "af3-1",
                "af3-dimer",
                {
                    "model_type": "af3",
                    "job_type": "multimer",
                    "query_name": "egfr_dimer",
                    "num_tokens": "1200",
                },
            ),
        ]

        def fake_status(job):
            return {
                "state": "PIPELINE_STATE_SUCCEEDED",
                "labels": job.labels,
                "pipeline_spec": "spec",
                "create_time": "2026-03-01T12:00:00Z",
                "start_time": "2026-03-01T12:01:00Z",
                "end_time": "2026-03-01T12:10:00Z",
                "duration_seconds": 540.0,
            }

        with (
            patch(
                "foldrun_app.core.tools.list_jobs.list_pipeline_jobs",
                return_value=jobs,
            ),
            patch(
                "foldrun_app.core.tools.list_jobs.get_job_status",
                side_effect=fake_status,
            ),
        ):
            filtered = tool.run(
                {
                    "seq_name": "kinase",
                    "min_seq_length": 400,
                    "max_seq_length": 600,
                    "check_analysis": False,
                }
            )

        returned_ids = [j["job_id"] for j in filtered["jobs"]]
        assert returned_ids == ["of3-1", "boltz-1"]
        by_id = {j["job_id"]: j for j in filtered["jobs"]}
        assert by_id["of3-1"]["experiment"] == "OpenFold3 Complex"
        assert by_id["of3-1"]["sequence_name"] == "kinase_ligand"
        assert by_id["boltz-1"]["experiment"] == "Boltz-2 Complex"
        assert by_id["boltz-1"]["sequence_name"] == "kinase_inhibitor"


class TestGetJobDetailsMultiModelInputs:
    """Verify get_job_details distinguishes Boltz-2 YAML, OF3 JSON, AF3 JSON, and AF2 FASTA."""

    @pytest.mark.parametrize(
        ("labels", "params", "raw_text", "expected_header", "expected_fmt", "expected_model"),
        [
            (
                {"model_type": "boltz2", "query_name": "cdk2_inh", "num_tokens": "312"},
                {"query_json_path": "gs://test-bucket/boltz2_queries/cdk2_inh.yaml"},
                "version: 1\nsequences:\n  - protein:\n      id: A\n      sequence: MKT\n",
                "Boltz-2 query YAML",
                "yaml",
                "boltz2",
            ),
            (
                {"model_type": "openfold3", "query_name": "of3_q1", "num_tokens": "200"},
                {"query_json_path": "gs://test-bucket/of3_queries/of3_q1.json"},
                '{"queries": {"of3_q1": {}}}',
                "OF3 query JSON",
                "json",
                "openfold3",
            ),
            (
                {"model_type": "af3", "query_name": "af3_q1", "num_tokens": "650"},
                {"query_json_path": "gs://test-bucket/af3_predictions/af3_q1/input.json"},
                '{"dialect": "alphafold3", "version": 1}',
                "AF3 query JSON",
                "json",
                "alphafold3",
            ),
        ],
    )
    def test_non_af2_query_formats_not_mislabeled_as_of3(
        self, labels, params, raw_text, expected_header, expected_fmt, expected_model
    ):
        from foldrun_app.core.config import CoreConfig
        from foldrun_app.core.tools.get_job_details import GetJobDetailsTool

        tool = GetJobDetailsTool(tool_config={"name": "get_job_details"}, config=CoreConfig())
        job = _make_job("job-1", "test-job", labels=labels, params=params)

        mock_storage = MagicMock()
        mock_storage.bucket.return_value.blob.return_value.download_as_text.return_value = raw_text

        with (
            patch(
                "foldrun_app.core.tools.get_job_details.get_pipeline_job",
                return_value=job,
            ),
            patch(
                "foldrun_app.core.tools.get_job_details.storage.Client",
                return_value=mock_storage,
            ),
        ):
            details = tool.run({"job_id": "job-1"})

        assert details["input"]["header"] == expected_header
        assert details["input"]["format"] == expected_fmt
        assert details["input"]["content"] == raw_text
        assert details["parameters"]["model_type"] == expected_model
        assert details["resubmission_ready"]["suggested_params"]["input"] == raw_text
        assert details["resubmission_ready"]["suggested_params"]["model_type"] == expected_model


class TestAnalyzeJobStaleBlobCleanup:
    """Verify re-analysis deletes stale per-prediction and summary blobs before launching tasks."""

    def _make_stale_blobs(self, prefix: str):
        names = [
            f"{prefix}prediction_0_analysis.json",
            f"{prefix}analysis_1.json",
            f"{prefix}summary.json",
            f"{prefix}artifacts_bundle.zip",
            f"{prefix}error.json",
            f"{prefix}analysis_metadata.json",
            f"{prefix}plddt_plot.png",
        ]
        blobs = []
        for n in names:
            b = MagicMock()
            b.name = n
            blobs.append(b)
        return blobs

    def test_af2_analyze_job_cleans_stale_blobs_on_overwrite(self):
        from foldrun_app.models.af2.config import Config
        from foldrun_app.models.af2.tools.analyze_job import AF2JobAnalysisTool

        tool = AF2JobAnalysisTool(tool_config={"name": "analyze_job_parallel"}, config=Config())
        job = _make_job("af2-job", "af2-job", labels={"model_type": "alphafold2"})
        prefix = "pipeline_runs/20260301_120000/analysis/"
        blobs = self._make_stale_blobs(prefix)

        mock_bucket = MagicMock()
        mock_bucket.list_blobs.return_value = blobs
        mock_bucket.blob.return_value.exists.return_value = False
        mock_storage = MagicMock()
        mock_storage.bucket.return_value = mock_bucket

        mock_jobs_client = MagicMock()
        mock_jobs_client.return_value.run_job.return_value.metadata.name = "projects/test-project/locations/us-central1/jobs/foldrun-analysis-job/executions/exec-1"

        with (
            patch(
                "foldrun_app.core.kfp_tools.get_pipeline_job",
                return_value=job,
            ),
            patch(
                "foldrun_app.models.af2.tools.analyze_job.get_task_details",
                return_value={
                    "predictions": [
                        {
                            "uri": "gs://test-bucket/pipeline_runs/20260301_120000/predict/raw_prediction.pkl",
                            "model_name": "model_1",
                            "ranking_confidence": 0.91,
                        }
                    ]
                },
            ),
            patch(
                "foldrun_app.core.kfp_tools.storage.Client",
                return_value=mock_storage,
            ),
            patch(
                "foldrun_app.core.kfp_tools.run_v2.JobsClient",
                mock_jobs_client,
            ),
        ):
            res = tool.run({"job_id": "af2-job", "overwrite": True})

        assert res["status"] == "started"
        assert res["execution_uid"] == "exec-1"
        deleted_names = {b.name.rsplit("/", 1)[-1] for b in blobs if b.delete.called}
        assert deleted_names == {
            "prediction_0_analysis.json",
            "analysis_1.json",
            "summary.json",
            "artifacts_bundle.zip",
            "error.json",
        }

    def test_of3_and_boltz2_cleanup_stale_analysis_blobs_helper(self):
        from foldrun_app.models.boltz2.config import BOLTZ2Config
        from foldrun_app.models.boltz2.tools.analyze_job import BOLTZ2JobAnalysisTool
        from foldrun_app.models.of3.config import OF3Config
        from foldrun_app.models.of3.tools.analyze_job import OF3JobAnalysisTool

        prefix = "pipeline_runs/20260301_120000/analysis/"
        for tool_cls, cfg_cls in [
            (OF3JobAnalysisTool, OF3Config),
            (BOLTZ2JobAnalysisTool, BOLTZ2Config),
        ]:
            tool = tool_cls(tool_config={"name": "analyze"}, config=cfg_cls())
            blobs = self._make_stale_blobs(prefix)
            mock_storage = MagicMock()
            mock_storage.bucket.return_value.list_blobs.return_value = blobs
            with patch("foldrun_app.core.kfp_tools.storage.Client", return_value=mock_storage):
                deleted = tool._cleanup_stale_analysis_blobs(f"gs://test-bucket/{prefix}")
            assert deleted == 5
