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

"""Security regression tests for AF2AnalysisTool (Findings 3.1/4.13, 4.14, 4.15)."""

import os
import pickle
import tempfile
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from foldrun_app.models.af2.tools.analyze import AF2AnalysisTool


class _MaliciousPayload:
    """Pickle gadget that attempts arbitrary code execution via __reduce__."""

    def __reduce__(self):
        return (os.system, ("echo pwned",))


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.project_id = "test-project"
    config.region = "us-central1"
    config.bucket_name = "authorized-pipeline-bucket"
    config.databases_bucket_name = "authorized-databases-bucket"
    config.nfs_mount_point = "/mnt/nfs/foldrun"
    return config


@pytest.fixture
def analysis_tool(mock_config):
    with patch("foldrun_app.core.base_tool._ensure_clients"):
        tool = AF2AnalysisTool(
            tool_config={"name": "af2_analyze", "description": "Analyze AF2 predictions"},
            config=mock_config,
        )
        tool.storage_client = MagicMock()
        return tool


class TestAF2AnalyzeSecurity:
    """Tests covering CWE-502 (Finding 3.1/4.13), CWE-377 (Finding 4.14), CWE-22 (Finding 4.15)."""

    def test_finding_4_13_rejects_unauthorized_gcs_bucket(self, analysis_tool):
        """Finding 3.1 / 4.13: GCS URIs outside configured buckets must be rejected."""
        with pytest.raises(ValueError, match="Unauthorized GCS bucket"):
            analysis_tool.run(
                {"raw_prediction_path": "gs://attacker-untrusted-bucket/malicious.pkl"}
            )

    def test_finding_4_13_blocks_malicious_pickle_deserialization(self, analysis_tool):
        """Finding 3.1 / 4.13: Malicious pickle gadgets (__reduce__ -> os.system) must be blocked."""
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as tmp_file:
            pickle.dump(_MaliciousPayload(), tmp_file)
            malicious_pkl_path = tmp_file.name

        try:
            with pytest.raises(pickle.UnpicklingError, match="forbidden"):
                analysis_tool.run({"raw_prediction_path": malicious_pkl_path})
        finally:
            if os.path.exists(malicious_pkl_path):
                os.remove(malicious_pkl_path)

    def test_finding_4_13_allows_valid_numpy_prediction_pickle(self, analysis_tool):
        """Finding 3.1 / 4.13: Legitimate AF2 prediction dicts with numpy arrays still load."""
        valid_prediction = {
            "plddt": np.array([92.5, 94.0, 91.0, 89.5], dtype=np.float32),
            "predicted_aligned_error": np.array([[0.5, 1.2], [1.1, 0.4]], dtype=np.float32),
            "max_predicted_aligned_error": 31.75,
        }
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as tmp_file:
            pickle.dump(valid_prediction, tmp_file)
            valid_pkl_path = tmp_file.name

        try:
            result = analysis_tool.run({"raw_prediction_path": valid_pkl_path})
            assert result["quality_assessment"] == "very_high_confidence"
            assert result["has_pae"] is True
        finally:
            if os.path.exists(valid_pkl_path):
                os.remove(valid_pkl_path)

    def test_finding_4_14_no_predictable_temp_paths_used(self, analysis_tool):
        """Finding 4.14: GCS downloads must not use predictable /tmp/raw_prediction*.pkl paths."""
        valid_prediction = {
            "plddt": np.array([85.0, 88.0], dtype=np.float32),
        }
        downloaded_paths = []

        def fake_download(gcs_path, local_path):
            downloaded_paths.append(local_path)
            with open(local_path, "wb") as f:
                pickle.dump(valid_prediction, f)
            return local_path

        analysis_tool._download_from_gcs = fake_download
        analysis_tool.run(
            {"raw_prediction_path": "gs://authorized-pipeline-bucket/job1/raw_prediction.pkl"}
        )

        assert len(downloaded_paths) == 1
        used_path = downloaded_paths[0]
        predictable_1 = os.path.join(tempfile.gettempdir(), "raw_prediction.pkl")
        predictable_2 = os.path.join(tempfile.gettempdir(), "raw_prediction_temp.pkl")
        assert used_path != predictable_1
        assert used_path != predictable_2
        # Temporary file must be cleaned up after run()
        assert not os.path.exists(used_path)

    def test_finding_4_15_blocks_local_path_traversal(self, analysis_tool):
        """Finding 4.15: Local path traversal outside authorized directories must be rejected."""
        with pytest.raises(ValueError, match="Unauthorized local file path"):
            analysis_tool.run({"raw_prediction_path": "/etc/passwd"})

        with pytest.raises(ValueError, match="Unauthorized local file path"):
            analysis_tool.run(
                {"raw_prediction_path": os.path.join(tempfile.gettempdir(), "../../etc/passwd")}
            )

    def test_fails_closed_when_allowed_buckets_unconfigured(self, analysis_tool):
        """_validate_gcs_uri must fail closed when no buckets are configured."""
        analysis_tool.config.bucket_name = None
        analysis_tool.config.databases_bucket_name = None

        with pytest.raises(ValueError, match="No authorized GCS buckets configured"):
            analysis_tool.run({"raw_prediction_path": "gs://attacker-bucket/exploit.pkl"})

    def test_get_gcs_file_size_validates_bucket(self, analysis_tool):
        """_get_gcs_file_size must validate bucket authorization before querying GCS."""
        with pytest.raises(ValueError, match="Unauthorized GCS bucket"):
            analysis_tool._get_gcs_file_size("gs://attacker-bucket/exploit.pkl")

        with pytest.raises(ValueError, match="Path traversal detected"):
            analysis_tool._get_gcs_file_size(
                "gs://authorized-pipeline-bucket/job1/../../exploit.pkl"
            )

    def test_rejects_non_dict_and_object_dtype_pickles(self, analysis_tool):
        """Non-dict top-level payloads and object-dtype NumPy arrays must be rejected."""
        # 1. Top-level non-dict (e.g. int/list)
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as tmp_file:
            pickle.dump([1, 2, 3], tmp_file)
            non_dict_path = tmp_file.name

        try:
            with pytest.raises(
                pickle.UnpicklingError, match="Expected prediction payload to be a dict"
            ):
                analysis_tool.run({"raw_prediction_path": non_dict_path})
        finally:
            if os.path.exists(non_dict_path):
                os.remove(non_dict_path)

        # 2. Dict containing an object-dtype NumPy array
        obj_array_payload = {
            "plddt": np.array([{"nested": "object"}, 90.0], dtype=object),
        }
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as tmp_file:
            pickle.dump(obj_array_payload, tmp_file)
            obj_array_path = tmp_file.name

        try:
            with pytest.raises(
                pickle.UnpicklingError, match="Object-dtype NumPy arrays are forbidden"
            ):
                analysis_tool.run({"raw_prediction_path": obj_array_path})
        finally:
            if os.path.exists(obj_array_path):
                os.remove(obj_array_path)

    def test_skill_entrypoint_blocks_attacker_gcs_pickle(self, analysis_tool):
        """End-to-end taint chain from analyze_prediction_quality blocks unauthorized GCS URI."""
        from foldrun_app.skills.results_analysis.tools import analyze_prediction_quality

        with patch(
            "foldrun_app.skills.results_analysis.tools.get_tool",
            return_value=analysis_tool,
        ):
            with pytest.raises(ValueError, match="Unauthorized GCS bucket"):
                analyze_prediction_quality(raw_prediction_path="gs://attacker-bucket/exploit.pkl")
