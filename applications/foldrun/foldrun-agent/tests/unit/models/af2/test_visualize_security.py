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

"""Security unit tests for AF2 visualization tool and pickle deserialization."""

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

from foldrun_app.models.af2.tools.visualize import AF2VisualizationTool  # noqa: E402
from foldrun_app.models.af2.utils.viz_utils import (  # noqa: E402
    generate_plddt_colored_pdb,
    load_raw_prediction,
)


class _ExploitPayload:
    """Malicious pickle payload that records code execution during unpickling."""

    executed = False

    def __reduce__(self):
        return (os.system, ("echo pwned >/dev/null",))


def _make_mock_tool(bucket_name: str = "authorized-bucket") -> AF2VisualizationTool:
    mock_config = MagicMock()
    mock_config.project_id = "test-project"
    mock_config.region = "us-central1"
    mock_config.bucket_name = bucket_name
    mock_config.nfs_mount_point = "/mnt/nfs/foldrun"
    with patch("foldrun_app.core.base_tool._ensure_clients"):
        tool = AF2VisualizationTool(tool_config={"name": "visualize"}, config=mock_config)
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


def _write_sample_pdb(path: str) -> None:
    lines = [
        "ATOM      1  N   ALA A   1      11.104   6.134  -6.504  1.00  0.00           N  ",
        "ATOM      2  CA  GLY A   2      12.104   7.134  -5.504  1.00  0.00           C  ",
    ]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


class TestAF2VisualizationSecurity(unittest.TestCase):
    """Tests covering Findings 2.1, 2.2, 4.22, and 4.23."""

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
        """Finding 2.1: AF2VisualizationTool.run() must reject GCS URIs outside authorized bucket."""
        tool = _make_mock_tool(bucket_name="authorized-bucket")
        with self.assertRaises(ValueError):
            tool.run(
                {
                    "pdb_path": "gs://authorized-bucket/structure.pdb",
                    "raw_prediction_path": "gs://attacker-bucket/malicious.pkl",
                }
            )

    def test_finding_2_1_rejects_unsafe_local_prediction_path(self):
        """Finding 2.1: AF2VisualizationTool.run() must reject unsafe local paths."""
        tool = _make_mock_tool()
        with self.assertRaises(ValueError):
            tool.run(
                {
                    "pdb_path": "/etc/hosts",
                    "raw_prediction_path": "/etc/passwd",
                }
            )

    def test_finding_4_22_uses_unpredictable_tempfiles(self):
        """Finding 4.22: GCS downloads must not use predictable /tmp/structure.pdb or /tmp/raw_prediction.pkl."""
        tool = _make_mock_tool(bucket_name="authorized-bucket")
        downloaded_paths = []

        def fake_download(gcs_path: str, local_path: str) -> str:
            downloaded_paths.append(local_path)
            if gcs_path.endswith(".pdb"):
                _write_sample_pdb(local_path)
            elif gcs_path.endswith(".pkl"):
                _write_valid_prediction(local_path)
            return local_path

        tool._download_from_gcs = fake_download
        with tempfile.TemporaryDirectory() as out_dir:
            out_file = os.path.join(out_dir, "out.pdb")
            result = tool.run(
                {
                    "pdb_path": "gs://authorized-bucket/test.pdb",
                    "raw_prediction_path": "gs://authorized-bucket/test.pkl",
                    "output_path": out_file,
                }
            )
            self.assertEqual(result["status"], "success")
            self.assertTrue(os.path.exists(out_file))

        predictable_pdb = os.path.join(tempfile.gettempdir(), "structure.pdb")
        predictable_pkl = os.path.join(tempfile.gettempdir(), "raw_prediction.pkl")
        self.assertNotIn(predictable_pdb, downloaded_paths)
        self.assertNotIn(predictable_pkl, downloaded_paths)

    def test_finding_4_23_blocks_arbitrary_output_path_overwrite(self):
        """Finding 4.23: Unsanitized output_path traversal outside allowed dirs must be rejected."""
        tool = _make_mock_tool()
        with tempfile.TemporaryDirectory() as tmpdir:
            pdb_path = os.path.join(tmpdir, "input.pdb")
            pkl_path = os.path.join(tmpdir, "pred.pkl")
            _write_sample_pdb(pdb_path)
            _write_valid_prediction(pkl_path)

            with self.assertRaises(ValueError):
                tool.run(
                    {
                        "pdb_path": pdb_path,
                        "raw_prediction_path": pkl_path,
                        "output_path": "/etc/cron.d/evil_overwrite.pdb",
                    }
                )

            with self.assertRaises(ValueError):
                generate_plddt_colored_pdb(
                    pdb_path=pdb_path,
                    raw_prediction_path=pkl_path,
                    output_path="/tmp/../../etc/evil.pdb",
                )


if __name__ == "__main__":
    unittest.main()
