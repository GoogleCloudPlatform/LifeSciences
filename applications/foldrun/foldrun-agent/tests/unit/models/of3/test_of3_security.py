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

"""Security tests for OF3SubmitPredictionTool (Findings 3.6/4.37 and 4.38)."""

import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_env():
    env = {
        "GCP_PROJECT_ID": "test-project",
        "GCP_REGION": "us-central1",
        "GCS_BUCKET_NAME": "test-bucket",
        "FILESTORE_ID": "test-nfs",
        "FILESTORE_IP": "10.1.0.2",
        "FILESTORE_NETWORK": "projects/123/global/networks/test-net",
        "OPENFOLD3_COMPONENTS_IMAGE": "of3-image:stable",
        "NFS_SERVER": "10.1.0.2",
        "NFS_PATH": "/datasets",
        "NFS_MOUNT_POINT": "/mnt/nfs/foldrun",
        "NETWORK": "projects/123/global/networks/test-net",
    }
    with patch.dict(os.environ, env, clear=False):
        yield


def _make_tool():
    from foldrun_app.models.of3.tools.submit_prediction import OF3SubmitPredictionTool

    mock_storage = MagicMock()
    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_blob.exists.return_value = True
    mock_bucket.blob.return_value = mock_blob
    mock_storage.bucket.return_value = mock_bucket

    with patch("foldrun_app.core.base_tool._ensure_clients"):
        tool = OF3SubmitPredictionTool(tool_config={"name": "of3_submit_prediction"})
    tool.storage_client = mock_storage
    return tool


class TestArbitraryLocalFileReadRemediation:
    """Finding 3.6 / 4.37: Restrict local file reads via os.path.realpath containment."""

    def test_rejects_local_file_when_no_allowed_dir_configured(self, tmp_path):
        """Local file reads are rejected when FOLDRUN_ALLOWED_INPUT_DIR is unset."""
        secret_file = tmp_path / "secret.fasta"
        secret_file.write_text(">seq1\nMKTAYIAKQRQISFVKSHFSRQ\n")

        tool = _make_tool()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FOLDRUN_ALLOWED_INPUT_DIR", None)
            with pytest.raises(ValueError, match="authorized"):
                tool.run({"input": str(secret_file), "job_name": "test_job"})

    def test_rejects_local_file_outside_allowed_dir(self, tmp_path):
        """Local file reads outside FOLDRUN_ALLOWED_INPUT_DIR are rejected."""
        allowed_dir = tmp_path / "allowed"
        allowed_dir.mkdir()
        outside_file = tmp_path / "outside.fasta"
        outside_file.write_text(">seq1\nMKTAYIAKQRQISFVKSHFSRQ\n")

        tool = _make_tool()
        with patch.dict(os.environ, {"FOLDRUN_ALLOWED_INPUT_DIR": str(allowed_dir)}):
            with pytest.raises(ValueError, match="outside the authorized directory"):
                tool.run({"input": str(outside_file), "job_name": "test_job"})

    def test_rejects_symlink_escaping_allowed_dir(self, tmp_path):
        """Symlinks inside allowed_dir pointing outside are rejected via realpath check."""
        allowed_dir = tmp_path / "allowed"
        allowed_dir.mkdir()
        outside_file = tmp_path / "secret_target.fasta"
        outside_file.write_text(">seq1\nMKTAYIAKQRQISFVKSHFSRQ\n")
        symlink_path = allowed_dir / "escape.fasta"
        symlink_path.symlink_to(outside_file)

        tool = _make_tool()
        with patch.dict(os.environ, {"FOLDRUN_ALLOWED_INPUT_DIR": str(allowed_dir)}):
            with pytest.raises(ValueError, match="outside the authorized directory"):
                tool.run({"input": str(symlink_path), "job_name": "test_job"})

    def test_allows_local_file_inside_allowed_dir(self, tmp_path):
        """Valid local files inside FOLDRUN_ALLOWED_INPUT_DIR are accepted."""
        allowed_dir = tmp_path / "allowed"
        allowed_dir.mkdir()
        valid_file = allowed_dir / "valid.fasta"
        valid_file.write_text(">seq1\nMKTAYIAKQRQISFVKSHFSRQ\n")

        tool = _make_tool()
        mock_job = MagicMock()
        mock_job.resource_name = "projects/test-project/locations/us-central1/pipelineJobs/run-1"

        with (
            patch.dict(os.environ, {"FOLDRUN_ALLOWED_INPUT_DIR": str(allowed_dir)}),
            patch.object(tool, "_upload_to_gcs"),
            patch(
                "foldrun_app.models.of3.tools.submit_prediction.vertex_ai.PipelineJob",
                return_value=mock_job,
            ),
        ):
            result = tool.run({"input": str(valid_file), "job_name": "test_job"})
            assert result["status"] == "submitted"


class TestCompileEnvRaceConditionRemediation:
    """Finding 4.38: Protect concurrent compilation/environment setup with a lock."""

    def test_concurrent_submissions_do_not_contaminate_compile_env(self):
        """Concurrent run() calls serialize environment setup + compilation via lock."""
        observed_envs_at_compile: dict[str, dict[str, str]] = {}
        errors: list[Exception] = []

        def _fake_compile(self_compiler, pipeline_func, package_path):
            # Record the environment variables observed at compile time
            job_name = (
                os.path.basename(package_path).replace("of3_pipeline_", "").replace(".json", "")
            )
            observed_envs_at_compile[job_name] = {
                "PREDICT_ACCELERATOR_TYPE": os.environ.get("PREDICT_ACCELERATOR_TYPE", ""),
                "PREDICT_MACHINE_TYPE": os.environ.get("PREDICT_MACHINE_TYPE", ""),
            }
            with open(package_path, "w") as f:
                f.write("{}")

        tool1 = _make_tool()
        tool2 = _make_tool()
        orig_setup1 = tool1._setup_compile_env
        orig_setup2 = tool2._setup_compile_env

        def _slow_setup1(*args, **kwargs):
            orig_setup1(*args, **kwargs)
            time.sleep(0.08)

        def _slow_setup2(*args, **kwargs):
            orig_setup2(*args, **kwargs)
            time.sleep(0.08)

        tool1._setup_compile_env = _slow_setup1
        tool2._setup_compile_env = _slow_setup2
        tool1._upload_to_gcs = MagicMock()
        tool2._upload_to_gcs = MagicMock()

        def _make_mock_job(**kwargs):
            job = MagicMock()
            job.resource_name = (
                f"projects/test/locations/us-central1/pipelineJobs/{kwargs['display_name']}"
            )
            return job

        def _run_submission(tool, job_name: str, gpu_type: str):
            try:
                tool.run(
                    {
                        "input": ">seq1\nMKTAYIAKQRQISFVKSHFSRQ\n",
                        "job_name": job_name,
                        "gpu_type": gpu_type,
                    }
                )
            except Exception as exc:
                errors.append(exc)

        with (
            patch("kfp.compiler.Compiler.compile", new=_fake_compile),
            patch(
                "foldrun_app.models.of3.tools.submit_prediction.vertex_ai.PipelineJob",
                side_effect=_make_mock_job,
            ),
        ):
            t1 = threading.Thread(target=_run_submission, args=(tool1, "job_a100", "A100"))
            t2 = threading.Thread(
                target=_run_submission, args=(tool2, "job_a100_80gb", "A100_80GB")
            )
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        assert not errors, f"Unexpected errors during concurrent execution: {errors}"
        assert (
            observed_envs_at_compile["job_a100"]["PREDICT_ACCELERATOR_TYPE"] == "NVIDIA_TESLA_A100"
        )
        assert observed_envs_at_compile["job_a100"]["PREDICT_MACHINE_TYPE"] == "a2-highgpu-1g"
        assert (
            observed_envs_at_compile["job_a100_80gb"]["PREDICT_ACCELERATOR_TYPE"]
            == "NVIDIA_A100_80GB"
        )
        assert observed_envs_at_compile["job_a100_80gb"]["PREDICT_MACHINE_TYPE"] == "a2-ultragpu-1g"
