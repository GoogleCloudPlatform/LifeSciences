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
    "matplotlib": MagicMock(),
    "matplotlib.pyplot": MagicMock(),
    "google.cloud.storage": MagicMock(),
    "google.cloud.aiplatform_v1": MagicMock(),
    "google.genai": MagicMock(),
    "google.genai.types": MagicMock(),
}
for name, stub in _stubs.items():
    sys.modules.setdefault(name, stub)

from foldrun_analysis import af2_analyzer


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
