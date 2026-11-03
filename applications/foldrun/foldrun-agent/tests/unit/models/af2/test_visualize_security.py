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

"""Security unit tests for AF2 analysis tool and restricted pickle deserialization."""

import os
import pickle
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

# Mock Google Cloud dependencies if not installed in test environment
for mod_name in [
    "google",
    "google.cloud",
    "google.cloud.aiplatform",
    "google.cloud.storage",
    "google.cloud.filestore_v1",
    "google.cloud.resourcemanager_v3",
    "dotenv",
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

from foldrun_app.models.af2.tools.analyze import (  # noqa: E402
    AF2AnalysisTool,
    load_raw_prediction,
)


class _ExploitPayload:
    """Malicious pickle payload that records code execution during unpickling."""

    executed = False

    def __reduce__(self):
        return (os.system, ("echo pwned >/dev/null",))


def _make_mock_tool(bucket_name: str = "authorized-bucket") -> AF2AnalysisTool:
    mock_config = MagicMock()
    mock_config.project_id = "test-project"
    mock_config.region = "us-central1"
    mock_config.bucket_name = bucket_name
    mock_config.nfs_mount_point = "/mnt/nfs/foldrun"
    with patch("foldrun_app.core.base_tool._ensure_clients"):
        tool = AF2AnalysisTool(tool_config={"name": "analyze"}, config=mock_config)
    return tool


def _write_valid_prediction(path: str) -> None:
    prediction = {
        "plddt": np.array([92.5, 81.0], dtype=np.float32),
        "structure_module": {
            "final_atom_mask": np.ones((2, 1), dtype=np.float32),
        },
    }
    with open(path, "wb") as f:
        pickle.dump(prediction, f)


class TestAF2AnalysisPickleAndPathSecurity(unittest.TestCase):
    """Tests covering Findings 2.1, 2.2, and 4.22 in AF2AnalysisTool."""

    def test_finding_2_2_blocks_malicious_pickle_rce(self):
        """Finding 2.2: load_raw_prediction must block arbitrary code execution payloads."""
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as tf:
            pkl_path = tf.name
            pickle.dump(_ExploitPayload(), tf)

        try:
            with self.assertRaises((pickle.UnpicklingError, ValueError)):
                load_raw_prediction(pkl_path)
        finally:
            if os.path.exists(pkl_path):
                os.remove(pkl_path)

    def test_finding_2_2_allows_valid_numpy_prediction(self):
        """Finding 2.2: load_raw_prediction must allow valid NumPy prediction dictionaries."""
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as tf:
            pkl_path = tf.name
        try:
            _write_valid_prediction(pkl_path)
            loaded = load_raw_prediction(pkl_path)
            self.assertIn("plddt", loaded)
            self.assertTrue(np.allclose(loaded["plddt"], np.array([92.5, 81.0], dtype=np.float32)))
        finally:
            if os.path.exists(pkl_path):
                os.remove(pkl_path)

    def test_finding_2_1_rejects_unauthorized_gcs_bucket(self):
        """Finding 2.1: AF2AnalysisTool.run() must reject GCS URIs outside authorized bucket."""
        tool = _make_mock_tool(bucket_name="authorized-bucket")
        with self.assertRaises(ValueError):
            tool.run(
                {
                    "raw_prediction_path": "gs://attacker-bucket/malicious.pkl",
                }
            )

    def test_finding_2_1_rejects_unsafe_local_prediction_path(self):
        """Finding 2.1: AF2AnalysisTool.run() must reject unsafe local paths."""
        tool = _make_mock_tool()
        with self.assertRaises(ValueError):
            tool.run(
                {
                    "raw_prediction_path": "/etc/passwd",
                }
            )

    def test_finding_4_22_uses_unpredictable_tempfiles(self):
        """Finding 4.22: GCS downloads must use an unpredictable TemporaryDirectory."""
        tool = _make_mock_tool(bucket_name="authorized-bucket")
        downloaded_paths = []

        def fake_download(gcs_path: str, local_path: str) -> str:
            downloaded_paths.append(local_path)
            _write_valid_prediction(local_path)
            return local_path

        tool._download_from_gcs = fake_download
        result = tool.run({"raw_prediction_path": "gs://authorized-bucket/test.pkl"})
        self.assertEqual(result["quality_assessment"], "high_confidence")

        predictable_pkl = os.path.join(tempfile.gettempdir(), "raw_prediction.pkl")
        self.assertNotIn(predictable_pkl, downloaded_paths)


if __name__ == "__main__":
    unittest.main()
