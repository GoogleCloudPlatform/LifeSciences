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

"""Tests for prox.cluster — community detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prox.cluster import (
    detect_communities,
    format_cluster_label,
    graph_json_to_nx,
    run_clusters,
)

from .conftest import make_graph_data

# --- format_cluster_label ---


def test_format_cluster_label() -> None:
    assert format_cluster_label(0) == "C-01"
    assert format_cluster_label(9) == "C-10"


# --- graph_json_to_nx ---


def test_graph_to_nx_correct_edges() -> None:
    data = make_graph_data([("H-0001", "H-0002", 0.5)])
    G = graph_json_to_nx(data)
    assert G.number_of_nodes() == 2
    assert G.number_of_edges() == 1
    assert G["H-0001"]["H-0002"]["weight"] == 0.5


def test_graph_to_nx_no_duplicate_edges() -> None:
    data = make_graph_data([("H-0001", "H-0002", 0.5)])
    G = graph_json_to_nx(data)
    # Undirected graph: edge count should be 1 even though both sides list it
    assert G.number_of_edges() == 1


def test_graph_to_nx_isolated_node() -> None:
    data = {"threshold": 0.3, "nodes": {"H-0001": {"cluster": None, "edges": []}}}
    G = graph_json_to_nx(data)
    assert G.number_of_nodes() == 1
    assert G.number_of_edges() == 0


# --- detect_communities ---


def test_detect_communities_connected_components() -> None:
    """Two disconnected components should yield two communities."""
    import networkx as nx

    G = nx.Graph()
    G.add_edge("H-0001", "H-0002", weight=0.8)
    G.add_edge("H-0003", "H-0004", weight=0.9)

    partition, _algo = detect_communities(G)
    # Should have exactly 2 communities
    assert len(set(partition.values())) == 2
    # Nodes in same component get same label
    assert partition["H-0001"] == partition["H-0002"]
    assert partition["H-0003"] == partition["H-0004"]
    assert partition["H-0001"] != partition["H-0003"]


def test_detect_communities_single_node() -> None:
    import networkx as nx

    G = nx.Graph()
    G.add_node("H-0001")
    partition, _algo = detect_communities(G)
    assert len(partition) == 1
    assert "H-0001" in partition


def test_detect_communities_empty_graph() -> None:
    import networkx as nx

    G = nx.Graph()
    partition, _algo = detect_communities(G)
    assert partition == {}


# --- run_clusters (integration) ---


@pytest.fixture()
def graph_dir(tmp_path: Path) -> Path:
    """Create a run directory with a pre-built graph.json."""
    prox = tmp_path / "proximity"
    prox.mkdir()
    data = make_graph_data(
        [
            ("H-0001", "H-0002", 0.8),
            ("H-0003", "H-0004", 0.9),
        ]
    )
    with open(prox / "graph.json", "w") as fh:
        json.dump(data, fh)
    return tmp_path


def test_run_clusters_updates_graph(graph_dir: Path) -> None:
    run_clusters(graph_dir)
    prox = graph_dir / "proximity"

    graph = json.loads((prox / "graph.json").read_text())
    for info in graph["nodes"].values():
        assert info["cluster"] is not None
        assert info["cluster"].startswith("C-")


def test_run_clusters_writes_clusters_json(graph_dir: Path) -> None:
    run_clusters(graph_dir)
    prox = graph_dir / "proximity"

    clusters = json.loads((prox / "clusters.json").read_text())
    assert "clusters" in clusters
    assert "algorithm" in clusters
    assert "num_clusters" in clusters
    assert clusters["num_clusters"] >= 1

    # Every hypothesis should appear in exactly one cluster
    all_members = []
    for members in clusters["clusters"].values():
        all_members.extend(members)
    assert sorted(all_members) == ["H-0001", "H-0002", "H-0003", "H-0004"]


def test_run_clusters_every_hyp_in_one_cluster(graph_dir: Path) -> None:
    run_clusters(graph_dir)
    prox = graph_dir / "proximity"

    graph = json.loads((prox / "graph.json").read_text())
    clusters_seen: dict[str, str] = {}
    for node_id, info in graph["nodes"].items():
        assert info["cluster"] is not None
        clusters_seen[node_id] = info["cluster"]

    # Each node belongs to exactly one cluster
    assert len(clusters_seen) == 4


def test_run_clusters_labels_format(graph_dir: Path) -> None:
    run_clusters(graph_dir)
    prox = graph_dir / "proximity"

    clusters = json.loads((prox / "clusters.json").read_text())
    import re

    for label in clusters["clusters"]:
        assert re.match(r"^C-\d{2}$", label), f"Bad label: {label}"
