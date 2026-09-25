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

"""Tests for MPO selectivity-ratio flattening (#135, Item 1).

Covers:
- Nested list-of-dicts records are correctly flattened into keyed metrics
- Identity field extraction picks the first string field
- Slug normalization produces lowercase alphanumeric + hyphens
- Flattened metrics appear correctly in MPO _extract_metrics output
"""

from __future__ import annotations

import unittest

from dde.commands.mpo import _extract_metrics, _flatten_list_of_dicts, _slugify


class TestSlugify(unittest.TestCase):
    """Slug normalization produces safe lowercase identifiers."""

    def test_lowercase(self):
        self.assertEqual(_slugify("KLK14"), "klk14")

    def test_spaces_become_hyphens(self):
        self.assertEqual(_slugify("off target name"), "off-target-name")

    def test_special_chars_removed(self):
        self.assertEqual(_slugify("CYP3A4/5"), "cyp3a4-5")

    def test_consecutive_hyphens_collapsed(self):
        self.assertEqual(_slugify("foo--bar"), "foo-bar")

    def test_leading_trailing_hyphens_stripped(self):
        self.assertEqual(_slugify("-foo-"), "foo")

    def test_empty_string_returns_unknown(self):
        self.assertEqual(_slugify(""), "unknown")

    def test_pure_special_chars_returns_unknown(self):
        self.assertEqual(_slugify("!!!"), "unknown")


class TestFlattenListOfDicts(unittest.TestCase):
    """List-of-dicts flattening produces correct keyed values."""

    def test_selectivity_ratios(self):
        """Standard selectivity ratio format is correctly flattened."""
        ratios = [
            {"off_target": "KLK14", "selectivity_ratio": 631.0},
            {"off_target": "thrombin", "selectivity_ratio": 3162.0},
            {"off_target": "hERG", "selectivity_ratio": 45.0},
        ]
        result = _flatten_list_of_dicts("selectivity", ratios)
        self.assertAlmostEqual(result["selectivity.klk14"], 631.0)
        self.assertAlmostEqual(result["selectivity.thrombin"], 3162.0)
        self.assertAlmostEqual(result["selectivity.herg"], 45.0)

    def test_first_string_field_as_identity(self):
        """Uses the first string field as identity when no field specified."""
        records = [
            {"name": "Alpha", "score": 10.0, "label": "extra"},
            {"name": "Beta", "score": 20.0, "label": "extra"},
        ]
        result = _flatten_list_of_dicts("metric", records)
        self.assertIn("metric.alpha", result)
        self.assertIn("metric.beta", result)
        self.assertAlmostEqual(result["metric.alpha"], 10.0)

    def test_first_numeric_field_as_value(self):
        """Uses the first numeric field as value when no field specified."""
        records = [
            {"target": "X", "potency": 5.5, "secondary": 100.0},
        ]
        result = _flatten_list_of_dicts("data", records)
        # First numeric field is potency, not secondary
        self.assertAlmostEqual(result["data.x"], 5.5)

    def test_configurable_identity_field(self):
        """Explicit identity_field overrides auto-detection."""
        records = [
            {"label": "ignored", "id": "UsedId", "value": 42.0},
        ]
        result = _flatten_list_of_dicts(
            "test",
            records,
            identity_field="id",
        )
        self.assertIn("test.usedid", result)
        self.assertAlmostEqual(result["test.usedid"], 42.0)

    def test_configurable_value_field(self):
        """Explicit value_field overrides auto-detection."""
        records = [
            {"name": "A", "first_num": 1.0, "target_num": 99.0},
        ]
        result = _flatten_list_of_dicts(
            "test",
            records,
            value_field="target_num",
        )
        self.assertAlmostEqual(result["test.a"], 99.0)

    def test_skips_non_dict_entries(self):
        """Non-dict entries in the list are silently skipped."""
        records = [
            {"name": "A", "value": 1.0},
            "not a dict",
            42,
            {"name": "B", "value": 2.0},
        ]
        result = _flatten_list_of_dicts("test", records)
        self.assertEqual(len(result), 2)
        self.assertIn("test.a", result)
        self.assertIn("test.b", result)

    def test_skips_records_without_string_identity(self):
        """Records with no string field are skipped."""
        records = [
            {"x": 1, "y": 2.0},
        ]
        result = _flatten_list_of_dicts("test", records)
        self.assertEqual(result, {})

    def test_skips_records_without_numeric_value(self):
        """Records with no numeric field are skipped."""
        records = [
            {"name": "A", "label": "text_only"},
        ]
        result = _flatten_list_of_dicts("test", records)
        self.assertEqual(result, {})

    def test_boolean_not_treated_as_numeric(self):
        """Boolean fields are not treated as numeric values."""
        records = [
            {"name": "A", "flag": True, "score": 5.0},
        ]
        result = _flatten_list_of_dicts("test", records)
        self.assertAlmostEqual(result["test.a"], 5.0)


class TestExtractMetricsWithLists(unittest.TestCase):
    """_extract_metrics correctly handles list-of-dict values."""

    def test_selectivity_ratios_in_metrics(self):
        """Selectivity ratios nested under metrics are flattened."""
        doc = {
            "metrics": {
                "pIC50": 7.5,
                "selectivity_ratios": [
                    {"off_target": "KLK14", "selectivity_ratio": 631.0},
                    {"off_target": "thrombin", "selectivity_ratio": 3162.0},
                ],
            },
        }
        result = _extract_metrics(doc)
        self.assertAlmostEqual(result["pIC50"], 7.5)
        self.assertAlmostEqual(
            result["selectivity_ratios.klk14"],
            631.0,
        )
        self.assertAlmostEqual(
            result["selectivity_ratios.thrombin"],
            3162.0,
        )

    def test_ratios_under_assessment(self):
        """List-of-dicts under assessment section are also flattened."""
        doc = {
            "assessment": {
                "ratios": [
                    {"off_target": "hERG", "selectivity_ratio": 45.0},
                ],
            },
        }
        result = _extract_metrics(doc)
        self.assertAlmostEqual(result["ratios.herg"], 45.0)

    def test_nested_list_under_dict(self):
        """List-of-dicts nested one level deeper inside a dict."""
        doc = {
            "metrics": {
                "selectivity": {
                    "panel_ratios": [
                        {"off_target": "CYP3A4", "selectivity_ratio": 100.0},
                    ],
                },
            },
        }
        result = _extract_metrics(doc)
        self.assertAlmostEqual(
            result["selectivity.panel_ratios.cyp3a4"],
            100.0,
        )

    def test_mixed_numeric_and_list_values(self):
        """Numeric values and list-of-dicts coexist correctly."""
        doc = {
            "metrics": {
                "pIC50": 7.5,
                "SA_score": 2.5,
                "off_target_data": [
                    {"target": "A", "ratio": 10.0},
                    {"target": "B", "ratio": 20.0},
                ],
            },
        }
        result = _extract_metrics(doc)
        self.assertAlmostEqual(result["pIC50"], 7.5)
        self.assertAlmostEqual(result["SA_score"], 2.5)
        self.assertIn("off_target_data.a", result)
        self.assertIn("off_target_data.b", result)

    def test_empty_list_produces_no_keys(self):
        """An empty list produces no additional flat keys."""
        doc = {
            "metrics": {
                "pIC50": 7.5,
                "empty_list": [],
            },
        }
        result = _extract_metrics(doc)
        self.assertEqual(result, {"pIC50": 7.5})


if __name__ == "__main__":
    unittest.main()
