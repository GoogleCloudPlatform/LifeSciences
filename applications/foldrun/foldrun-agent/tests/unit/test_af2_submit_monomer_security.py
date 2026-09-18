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

"""Security tests for AF2SubmitMonomerTool (Findings 3.3/4.24, 4.25, 4.26)."""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from foldrun_app.models.af2.tools.submit_monomer import AF2SubmitMonomerTool


def _make_mock_tool(databases_bucket_name: str = "my-db-bucket") -> AF2SubmitMonomerTool:
    tool = AF2SubmitMonomerTool.__new__(AF2SubmitMonomerTool)
    config = MagicMock()
    config.bucket_name = "my-work-bucket"
    config.databases_bucket_name = databases_bucket_name
    config.filestore_ip = "10.0.0.2"
    config.filestore_network = "projects/test-proj/global/networks/default"
    config.project_id = "test-proj"
    config.region = "us-central1"
    config.pipelines_sa_email = "sa@test-proj.iam.gserviceaccount.com"
    config.base_image = "gcr.io/test/af2:latest"
    config.nfs_share = "/datasets"
    config.nfs_mount_point = "/mnt/nfs/foldrun"
    config.parallelism = 5
    config.dws_max_wait_hours = 168
    config.supported_gpus = ["L4", "A100", "A100_80GB"]
    tool.config = config
    tool.storage_client = MagicMock()
    tool._upload_to_gcs = MagicMock()
    tool._clean_label = lambda s: s
    tool.gcs_console_url = lambda u: f"https://console.cloud.google.com/storage/{u}"
    return tool


class TestAF2SubmitMonomerSecurity(unittest.TestCase):
    """Security regression tests for AF2SubmitMonomerTool."""

    @patch("foldrun_app.models.af2.tools.submit_monomer.vertex_ai.PipelineJob")
    def test_finding_4_24_rejects_arbitrary_local_file_path_outside_tempdir(
        self, mock_pipeline_job
    ):
        """Finding 3.3 / 4.24: Arbitrary local file path outside authorized temp dir must be rejected."""
        tool = _make_mock_tool()
        # Create a file outside tempfile.gettempdir() (in CWD)
        local_secret_file = os.path.abspath("test_outside_temp_secret.fasta")
        with open(local_secret_file, "w") as f:
            f.write(">secret_header\nACDEFGHIKLMNPQRSTVWY\n")
        try:
            with self.assertRaises(ValueError):
                tool.run({"sequence": local_secret_file, "job_name": "test_job"})
            tool._upload_to_gcs.assert_not_called()
        finally:
            if os.path.exists(local_secret_file):
                os.remove(local_secret_file)

    @patch("foldrun_app.models.af2.tools.submit_monomer.vertex_ai.PipelineJob")
    def test_finding_4_24_validates_fasta_content_from_authorized_temp_file(
        self, mock_pipeline_job
    ):
        """Finding 3.3 / 4.24: Even within tempdir, invalid FASTA / non-AA content must be rejected."""
        tool = _make_mock_tool()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".fasta", delete=False) as tf:
            tf.write(">fake_header\nroot:x:0:0:root:/root:/bin/bash\n")
            temp_path = tf.name
        try:
            with self.assertRaises(Exception):  # noqa: B017
                tool.run({"sequence": temp_path, "job_name": "test_invalid_fasta"})
            tool._upload_to_gcs.assert_not_called()
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    @patch("foldrun_app.models.af2.tools.submit_monomer.vertex_ai.PipelineJob")
    def test_finding_4_25_unpredictable_temp_file_and_sanitized_job_name(self, mock_pipeline_job):
        """Finding 4.25: Pipeline compilation must not use predictable /tmp/af2_pipeline_{job_name}.json and must sanitize job_name."""
        tool = _make_mock_tool()
        captured_package_paths = []

        def fake_compile(pipeline_func, package_path):
            captured_package_paths.append(package_path)
            with open(package_path, "w") as f:
                f.write("{}")

        mock_compiler_cls = MagicMock()
        mock_compiler_cls.return_value.compile.side_effect = fake_compile
        mock_pipeline_job.return_value.resource_name = (
            "projects/test-proj/locations/us-central1/pipelineJobs/job-123"
        )

        with patch.dict(
            sys.modules,
            {
                "kfp": MagicMock(compiler=MagicMock(Compiler=mock_compiler_cls)),
                "kfp.compiler": MagicMock(Compiler=mock_compiler_cls),
                "foldrun_app.models.af2.utils.pipeline_utils": MagicMock(
                    load_vertex_pipeline=MagicMock(return_value=MagicMock())
                ),
            },
        ):
            res = tool.run(
                {
                    "sequence": ">seq1\nACDEFGHIKLMNPQRSTVWY\n",
                    "job_name": "../evil/job name",
                }
            )

        self.assertEqual(len(captured_package_paths), 1)
        compiled_path = captured_package_paths[0]
        predictable = os.path.join(tempfile.gettempdir(), f"af2_pipeline_{res['job_name']}.json")
        self.assertNotEqual(compiled_path, predictable)
        self.assertNotIn("/", res["job_name"])
        self.assertNotIn("..", res["job_name"])

    @patch("foldrun_app.models.af2.tools.submit_monomer.vertex_ai.PipelineJob")
    def test_finding_4_26_model_params_gcs_location_from_config_not_mutated_environ(
        self, mock_pipeline_job
    ):
        """Finding 4.26: model_params_gcs_location must come directly from self.config even if os.environ is mutated concurrently."""
        tool = _make_mock_tool(databases_bucket_name="expected-db-bucket")

        def fake_compile_contaminating_env(pipeline_func, package_path):
            os.environ["MODEL_PARAMS_GCS_LOCATION"] = (
                "gs://contaminated-other-request-bucket/alphafold2"
            )
            with open(package_path, "w") as f:
                f.write("{}")

        mock_compiler_cls = MagicMock()
        mock_compiler_cls.return_value.compile.side_effect = fake_compile_contaminating_env
        mock_pipeline_job.return_value.resource_name = (
            "projects/test-proj/locations/us-central1/pipelineJobs/job-456"
        )

        with patch.dict(
            sys.modules,
            {
                "kfp": MagicMock(compiler=MagicMock(Compiler=mock_compiler_cls)),
                "kfp.compiler": MagicMock(Compiler=mock_compiler_cls),
                "foldrun_app.models.af2.utils.pipeline_utils": MagicMock(
                    load_vertex_pipeline=MagicMock(return_value=MagicMock())
                ),
            },
        ):
            tool.run(
                {
                    "sequence": ">seq1\nACDEFGHIKLMNPQRSTVWY\n",
                    "job_name": "clean_job",
                }
            )

        _, kwargs = mock_pipeline_job.call_args
        self.assertEqual(
            kwargs["parameter_values"]["model_params_gcs_location"],
            "gs://expected-db-bucket/alphafold2",
        )


if __name__ == "__main__":
    unittest.main()
