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

"""Community detection on the proximity graph."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import networkx as nx

from prox._io import atomic_write_json


def _louvain_partition(G: nx.Graph) -> dict[str, int]:
    """Try Louvain community detection.  Returns node→community mapping."""
    # python-louvain exposes itself as ``community``
    import community as community_louvain  # type: ignore[import-untyped]

    return community_louvain.best_partition(G)


def _connected_components_partition(G: nx.Graph) -> dict[str, int]:
    """Fallback: assign each connected component a community label."""
    partition: dict[str, int] = {}
    for idx, comp in enumerate(nx.connected_components(G)):
        for node in comp:
            partition[node] = idx
    return partition


def detect_communities(G: nx.Graph) -> tuple[dict[str, int], str]:
    """Run community detection, returning (partition, algorithm_name).

    Prefers Louvain; falls back to connected-components if
    ``python-louvain`` is not installed.
    """
    try:
        return _louvain_partition(G), "louvain"
    except ImportError:
        return _connected_components_partition(G), "connected_components"


def graph_json_to_nx(graph_data: dict[str, Any]) -> nx.Graph:
    """Convert the adjacency-list ``graph.json`` structure to a networkx Graph."""
    G = nx.Graph()
    nodes = graph_data.get("nodes", {})
    for node_id in nodes:
        G.add_node(node_id)
    for node_id, info in nodes.items():
        for edge in info.get("edges", []):
            # Only add if not already present (undirected)
            if not G.has_edge(node_id, edge["target"]):
                G.add_edge(node_id, edge["target"], weight=edge["similarity"])
    return G


def format_cluster_label(index: int) -> str:
    """Format a zero-based cluster index as ``C-NN``."""
    return f"C-{index + 1:02d}"


def run_clusters(run_dir: Path) -> None:
    """End-to-end clustering: load graph → detect communities → save."""
    prox_dir = run_dir / "proximity"
    graph_path = prox_dir / "graph.json"

    with open(graph_path) as fh:
        graph_data: dict[str, Any] = json.load(fh)

    G = graph_json_to_nx(graph_data)
    partition, algorithm = detect_communities(G)

    # Build mapping from raw community index → sorted C-NN label
    raw_labels = sorted(set(partition.values()))
    label_map = {raw: format_cluster_label(i) for i, raw in enumerate(raw_labels)}

    # Assign cluster labels to graph.json nodes
    for node_id, info in graph_data["nodes"].items():
        raw = partition.get(node_id)
        info["cluster"] = label_map[raw] if raw is not None else None

    # Write updated graph.json
    atomic_write_json(graph_path, graph_data)

    # Build clusters.json
    clusters: dict[str, list[str]] = {}
    for node_id, raw in sorted(partition.items()):
        label = label_map[raw]
        clusters.setdefault(label, []).append(node_id)
    # Sort members within each cluster
    for members in clusters.values():
        members.sort()
    # Sort clusters by label for stable output
    clusters = dict(sorted(clusters.items()))

    clusters_data = {
        "clusters": clusters,
        "algorithm": algorithm,
        "num_clusters": len(clusters),
    }

    atomic_write_json(prox_dir / "clusters.json", clusters_data)
