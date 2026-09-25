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

"""Tests for _flatten_entry() and normalize_deliverables() (#201).

Covers:
  - _flatten_entry with plain string input
  - _flatten_entry with dict containing preferred key
  - _flatten_entry with dict falling back to "name"
  - _flatten_entry with dict falling back to "path"
  - _flatten_entry with empty dict (last-resort str({}))
  - _flatten_entry with dict containing no recognized keys but a string value
  - normalize_deliverables with mixed list in layer_0_classes
  - normalize_deliverables with mixed list in layer_1
  - normalize_deliverables layer_0 -> layer_0_classes rename
  - normalize_deliverables non-list layer_0_classes left unchanged
  - normalize_deliverables plain string lists pass through unchanged
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.core.controlstore import _flatten_entry, normalize_deliverables  # noqa: E402

# ---------------------------------------------------------------------------
# _flatten_entry tests
# ---------------------------------------------------------------------------


def test_flatten_entry_plain_string():
    """Plain string input is returned unchanged."""
    assert _flatten_entry("docking", key="class") == "docking"


def test_flatten_entry_dict_preferred_key():
    """Dict with the preferred key returns its value."""
    entry = {"class": "docking", "description": "test"}
    assert _flatten_entry(entry, key="class") == "docking"


def test_flatten_entry_dict_fallback_name():
    """Dict without the preferred key falls back to 'name'."""
    entry = {"name": "compounds"}
    assert _flatten_entry(entry, key="class") == "compounds"


def test_flatten_entry_dict_fallback_path():
    """Dict with 'path' key works as a fallback."""
    entry = {"path": "/data/results.csv"}
    assert _flatten_entry(entry, key="class") == "/data/results.csv"


def test_flatten_entry_empty_dict():
    """Empty dict returns str({}) as last resort."""
    assert _flatten_entry({}, key="class") == str({})


def test_flatten_entry_dict_no_recognized_keys_string_value():
    """Dict with no recognized keys but a string value uses the first string value."""
    entry = {"label": "my-compound", "count": 42}
    assert _flatten_entry(entry, key="class") == "my-compound"


def test_flatten_entry_preferred_key_path():
    """Dict with 'path' as the preferred key returns its value directly."""
    entry = {"path": "/output/report.json", "name": "report"}
    assert _flatten_entry(entry, key="path") == "/output/report.json"


# ---------------------------------------------------------------------------
# normalize_deliverables tests
# ---------------------------------------------------------------------------


def test_normalize_mixed_layer_0_classes():
    """Mixed list in layer_0_classes (strings + dicts) flattens and is sortable."""
    deliverables = {
        "layer_0_classes": [
            "homology",
            {"class": "docking", "description": "molecular docking"},
            "pharmacokinetics",
            {"name": "toxicity"},
        ],
    }
    result = normalize_deliverables(deliverables)
    flat = result["layer_0_classes"]
    assert flat == ["homology", "docking", "pharmacokinetics", "toxicity"]
    # Must be sortable (all strings)
    assert sorted(flat) == ["docking", "homology", "pharmacokinetics", "toxicity"]


def test_normalize_mixed_layer_1():
    """Mixed list in layer_1 (strings + dicts) flattens and is sortable."""
    deliverables = {
        "layer_0_classes": ["docking"],
        "layer_1": [
            "/data/results.csv",
            {"path": "/data/scores.json", "format": "json"},
            {"name": "summary"},
        ],
    }
    result = normalize_deliverables(deliverables)
    flat = result["layer_1"]
    assert flat == ["/data/results.csv", "/data/scores.json", "summary"]
    assert sorted(flat) == ["/data/results.csv", "/data/scores.json", "summary"]


def test_normalize_layer_0_rename():
    """layer_0 -> layer_0_classes rename still works."""
    deliverables = {"layer_0": ["docking", "homology"]}
    result = normalize_deliverables(deliverables)
    assert "layer_0" not in result
    assert result["layer_0_classes"] == ["docking", "homology"]


def test_normalize_layer_0_classes_wins_over_layer_0():
    """When both layer_0 and layer_0_classes are present, layer_0_classes wins."""
    deliverables = {
        "layer_0": ["old"],
        "layer_0_classes": ["canonical"],
    }
    result = normalize_deliverables(deliverables)
    assert "layer_0" not in result
    assert result["layer_0_classes"] == ["canonical"]


def test_normalize_non_list_layer_0_classes_unchanged():
    """Non-list layer_0_classes values are left unchanged (guard works)."""
    deliverables = {"layer_0_classes": "single-string"}
    result = normalize_deliverables(deliverables)
    assert result["layer_0_classes"] == "single-string"


def test_normalize_plain_string_lists_unchanged():
    """Plain string lists pass through unchanged."""
    deliverables = {
        "layer_0_classes": ["alpha", "beta", "gamma"],
        "layer_1": ["/a.csv", "/b.csv"],
    }
    result = normalize_deliverables(deliverables)
    assert result["layer_0_classes"] == ["alpha", "beta", "gamma"]
    assert result["layer_1"] == ["/a.csv", "/b.csv"]


def test_normalize_returns_new_dict():
    """normalize_deliverables returns a new dict, not a mutated input."""
    original = {"layer_0_classes": ["docking"]}
    result = normalize_deliverables(original)
    assert result is not original
    assert result == original


# ---------------------------------------------------------------------------
# Direct-run support
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
