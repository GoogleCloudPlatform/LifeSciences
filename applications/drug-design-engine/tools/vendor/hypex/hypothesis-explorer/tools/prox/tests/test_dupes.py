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

"""Tests for prox.dupes — near-duplicate detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prox.dupes import find_dupes, run_dupes

from .conftest import make_graph_data

# --- find_dupes ---


def test_find_dupes_above_threshold() -> None:
    data = make_graph_data([("H-0001", "H-0002", 0.90)])
    dupes = find_dupes(data, threshold=0.80)
    assert len(dupes) == 1
    assert dupes[0]["a"] == "H-0001"
    assert dupes[0]["b"] == "H-0002"
    assert dupes[0]["similarity"] == pytest.approx(0.90)


def test_find_dupes_below_threshold() -> None:
    data = make_graph_data([("H-0001", "H-0002", 0.50)])
    dupes = find_dupes(data, threshold=0.80)
    assert len(dupes) == 0


def test_find_dupes_boundary_exact() -> None:
    """Pair at exactly the threshold should be included."""
    data = make_graph_data([("H-0001", "H-0002", 0.80)])
    dupes = find_dupes(data, threshold=0.80)
    assert len(dupes) == 1


def test_find_dupes_no_duplicate_pairs() -> None:
    """Each pair should appear only once even though graph has both directions."""
    data = make_graph_data([("H-0001", "H-0002", 0.90)])
    dupes = find_dupes(data, threshold=0.80)
    assert len(dupes) == 1


def test_find_dupes_multiple_pairs() -> None:
    data = make_graph_data(
        [
            ("H-0001", "H-0002", 0.95),
            ("H-0003", "H-0004", 0.85),
            ("H-0001", "H-0003", 0.40),
        ]
    )
    dupes = find_dupes(data, threshold=0.80)
    assert len(dupes) == 2
    # Sorted by descending similarity
    assert dupes[0]["similarity"] > dupes[1]["similarity"]


def test_find_dupes_empty_graph() -> None:
    data = {"threshold": 0.3, "nodes": {}}
    dupes = find_dupes(data, threshold=0.80)
    assert dupes == []


def test_find_dupes_pair_ordering() -> None:
    """The 'a' field should be the lexicographically smaller ID."""
    data = make_graph_data([("H-0005", "H-0002", 0.90)])
    dupes = find_dupes(data, threshold=0.80)
    assert dupes[0]["a"] == "H-0002"
    assert dupes[0]["b"] == "H-0005"


# --- run_dupes (integration) ---


@pytest.fixture()
def dupes_dir(tmp_path: Path) -> Path:
    prox = tmp_path / "proximity"
    prox.mkdir()
    data = make_graph_data(
        [
            ("H-0001", "H-0002", 0.92),
            ("H-0003", "H-0004", 0.50),
        ]
    )
    with open(prox / "graph.json", "w") as fh:
        json.dump(data, fh)
    return tmp_path


def test_run_dupes_returns_json(dupes_dir: Path) -> None:
    output = run_dupes(dupes_dir, threshold=0.80)
    result = json.loads(output)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["a"] == "H-0001"


def test_run_dupes_high_threshold_no_results(dupes_dir: Path) -> None:
    output = run_dupes(dupes_dir, threshold=0.99)
    result = json.loads(output)
    assert result == []
