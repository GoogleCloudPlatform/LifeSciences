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

"""Tests for AlphaFold2 analysis utilities: calculate_pae_stats, get_quality_assessment."""

import sys
from unittest.mock import MagicMock

# Stub heavy imports BEFORE loading the module
_stubs = {
    "google.cloud.storage": MagicMock(),
    "google.cloud.aiplatform_v1": MagicMock(),
    "google.genai": MagicMock(),
    "google.genai.types": MagicMock(),
    "matplotlib": MagicMock(),
    "matplotlib.pyplot": MagicMock(),
    "seaborn": MagicMock(),
}
for name, stub in _stubs.items():
    sys.modules.setdefault(name, stub)

from foldrun_analysis import af2_analyzer  # noqa: E402


class TestAF2Analysis:
    """Tests for AF2 specific post-processing functions."""

    def test_calculate_pae_stats_present(self):
        """PAE statistics computed correctly when error matrix is present."""
        import numpy as np

        raw_prediction = {
            "predicted_aligned_error": np.array([[2.0, 4.0], [6.0, 8.0]]),
            "max_predicted_aligned_error": 31.0,
        }
        stats = af2_analyzer.calculate_pae_stats(raw_prediction)
        assert stats is not None
        assert stats["mean"] == 5.0
        assert stats["median"] == 5.0
        assert stats["min"] == 2.0
        assert stats["max"] == 8.0
        assert stats["max_predicted"] == 31.0

    def test_calculate_pae_stats_missing(self):
        """Returns None gracefully when PAE is missing from prediction dict."""
        raw_prediction = {"plddt": [85.0, 90.0]}
        stats = af2_analyzer.calculate_pae_stats(raw_prediction)
        assert stats is None

    def test_quality_assessment_thresholds(self):
        """get_quality_assessment maps pLDDT score ranges accurately."""
        assert af2_analyzer.get_quality_assessment(95.0) == "very_high_confidence"
        assert af2_analyzer.get_quality_assessment(90.0) == "very_high_confidence"
        assert af2_analyzer.get_quality_assessment(85.0) == "high_confidence"
        assert af2_analyzer.get_quality_assessment(70.0) == "high_confidence"
        assert af2_analyzer.get_quality_assessment(65.0) == "low_confidence"
        assert af2_analyzer.get_quality_assessment(50.0) == "low_confidence"
        assert af2_analyzer.get_quality_assessment(45.0) == "very_low_confidence"

    def test_load_raw_prediction_valid_numpy_dict(self, tmp_path):
        """Legitimate AlphaFold2 prediction dict with NumPy arrays and scalars deserializes cleanly."""
        import pickle

        import numpy as np

        valid_payload = {
            "plddt": np.array([91.5, 88.0, 74.2], dtype=np.float32),
            "predicted_aligned_error": np.array(
                [[0.5, 2.1, 4.3], [2.0, 0.4, 3.1], [4.1, 3.0, 0.6]],
                dtype=np.float64,
            ),
            "max_predicted_aligned_error": np.float64(31.75),
            "ranking_confidence": 0.92,
        }
        pkl_file = tmp_path / "valid_prediction.pkl"
        with open(pkl_file, "wb") as f:
            pickle.dump(valid_payload, f)

        loaded = af2_analyzer.load_raw_prediction(str(pkl_file))
        assert isinstance(loaded, dict)
        assert np.allclose(loaded["plddt"], valid_payload["plddt"])
        assert np.allclose(
            loaded["predicted_aligned_error"], valid_payload["predicted_aligned_error"]
        )
        assert float(loaded["max_predicted_aligned_error"]) == 31.75

    def test_load_raw_prediction_blocks_rce_payload(self, tmp_path):
        """Malicious pickle payloads attempting arbitrary code execution via __reduce__ are blocked."""
        import os
        import pickle

        import pytest

        marker = tmp_path / "rce_marker.txt"

        class ExploitPayload:
            def __reduce__(self):
                return (os.system, (f"touch {marker}",))

        malicious_file = tmp_path / "malicious.pkl"
        with open(malicious_file, "wb") as f:
            pickle.dump({"plddt": ExploitPayload()}, f)

        with pytest.raises(pickle.UnpicklingError, match="Forbidden global"):
            af2_analyzer.load_raw_prediction(str(malicious_file))

        assert not marker.exists()

    def test_load_raw_prediction_valid_jax_array_dict(self, tmp_path):
        """Legitimate AlphaFold2 prediction dict containing JAX arrays deserializes cleanly."""
        import pickle

        import jax.numpy as jnp
        import numpy as np

        valid_payload = {
            "plddt": jnp.array([91.5, 88.0, 74.2], dtype=jnp.float32),
            "predicted_aligned_error": jnp.array(
                [[0.5, 2.1, 4.3], [2.0, 0.4, 3.1], [4.1, 3.0, 0.6]],
                dtype=jnp.float32,
            ),
            "bfloat16_logits": jnp.array([1.25, -0.5], dtype=jnp.bfloat16),
            "max_predicted_aligned_error": np.float64(31.75),
            "ranking_confidence": 0.92,
        }
        pkl_file = tmp_path / "valid_jax_prediction.pkl"
        with open(pkl_file, "wb") as f:
            pickle.dump(valid_payload, f)

        loaded = af2_analyzer.load_raw_prediction(str(pkl_file))
        assert isinstance(loaded, dict)
        assert isinstance(loaded["plddt"], np.ndarray)
        assert np.allclose(loaded["plddt"], valid_payload["plddt"])
        assert np.allclose(
            loaded["predicted_aligned_error"],
            valid_payload["predicted_aligned_error"],
        )
        assert float(loaded["max_predicted_aligned_error"]) == 31.75

    def test_load_raw_prediction_blocks_forged_jax_reconstructor(self, tmp_path):
        """Forged constructor passed to _safe_reconstruct_jax_array is rejected."""
        import pickle

        import pytest

        with pytest.raises(
            pickle.UnpicklingError,
            match="Forbidden constructor in JAX array reconstruction",
        ):
            af2_analyzer._safe_reconstruct_jax_array(dict, (), {}, {})

    def test_load_raw_prediction_blocks_object_dtype_array(self, tmp_path):
        """Object-dtype NumPy arrays inside prediction dicts are rejected."""
        import pickle

        import numpy as np
        import pytest

        pkl_file = tmp_path / "obj_dtype.pkl"
        with open(pkl_file, "wb") as f:
            pickle.dump({"plddt": np.array([{"a": 1}], dtype=object)}, f)

        with pytest.raises(
            pickle.UnpicklingError,
            match="Object-dtype NumPy arrays are forbidden",
        ):
            af2_analyzer.load_raw_prediction(str(pkl_file))

    def test_validate_task_gcs_uri_enforces_bucket_and_blocks_traversal(self):
        """_validate_task_gcs_uri enforces bucket allowlist and rejects traversal."""
        import pytest

        af2_analyzer._validate_task_gcs_uri(
            "gs://valid-bucket/pipeline_runs/job1/raw_prediction.pkl",
            expected_bucket="valid-bucket",
        )

        with pytest.raises(ValueError, match="Unauthorized GCS bucket"):
            af2_analyzer._validate_task_gcs_uri(
                "gs://attacker-bucket/exploit.pkl",
                expected_bucket="valid-bucket",
            )

        with pytest.raises(ValueError, match="Path traversal detected"):
            af2_analyzer._validate_task_gcs_uri(
                "gs://valid-bucket/pipeline_runs/../exploit.pkl",
                expected_bucket="valid-bucket",
            )
