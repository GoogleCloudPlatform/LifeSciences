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

"""Tests for prox.graph — cosine-similarity adjacency graph."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import scipy.sparse

from prox.graph import build_graph, run_graph


def _write_embeddings(
    run_dir: Path, matrix: scipy.sparse.csr_matrix, ids: list[str]
) -> None:
    """Helper: persist embeddings so graph commands can read them."""
    prox = run_dir / "proximity"
    prox.mkdir(parents=True, exist_ok=True)
    scipy.sparse.save_npz(prox / "embeddings.npz", matrix)
    with open(prox / "embedding-ids.json", "w") as fh:
        json.dump(ids, fh)


# --- build_graph ---


def test_build_graph_identical_vectors() -> None:
    """Two identical vectors should have similarity 1.0 and be connected."""
    mat = scipy.sparse.csr_matrix(np.array([[1, 0, 1], [1, 0, 1]], dtype=float))
    graph = build_graph(mat, ["H-0001", "H-0002"], threshold=0.3)

    assert len(graph["nodes"]) == 2
    edges_a = graph["nodes"]["H-0001"]["edges"]
    assert len(edges_a) == 1
    assert edges_a[0]["target"] == "H-0002"
    assert edges_a[0]["similarity"] == pytest.approx(1.0, abs=1e-4)


def test_build_graph_orthogonal_vectors() -> None:
    """Orthogonal vectors should have similarity 0 and no edge."""
    mat = scipy.sparse.csr_matrix(np.array([[1, 0], [0, 1]], dtype=float))
    graph = build_graph(mat, ["H-0001", "H-0002"], threshold=0.3)

    edges_a = graph["nodes"]["H-0001"]["edges"]
    assert len(edges_a) == 0


def test_build_graph_threshold_boundary() -> None:
    """Edge is included when similarity == threshold but excluded below."""
    # Construct vectors with known cosine similarity
    v1 = np.array([1.0, 0.0])
    v2 = np.array([0.6, 0.8])  # cosine with v1 = 0.6
    mat = scipy.sparse.csr_matrix(np.vstack([v1, v2]))

    # Threshold at 0.6 — should include
    graph = build_graph(mat, ["H-0001", "H-0002"], threshold=0.6)
    assert len(graph["nodes"]["H-0001"]["edges"]) == 1

    # Threshold at 0.61 — should exclude
    graph = build_graph(mat, ["H-0001", "H-0002"], threshold=0.61)
    assert len(graph["nodes"]["H-0001"]["edges"]) == 0


def test_build_graph_empty() -> None:
    """Empty embedding matrix produces a graph with no nodes."""
    mat = scipy.sparse.csr_matrix((0, 0))
    graph = build_graph(mat, [], threshold=0.3)
    assert graph["nodes"] == {}


def test_build_graph_single_node() -> None:
    """A single hypothesis should have a node with no edges."""
    mat = scipy.sparse.csr_matrix(np.array([[1, 2, 3]], dtype=float))
    graph = build_graph(mat, ["H-0001"], threshold=0.3)
    assert len(graph["nodes"]) == 1
    assert graph["nodes"]["H-0001"]["edges"] == []


def test_build_graph_no_self_edges() -> None:
    """Nodes should not have edges to themselves."""
    mat = scipy.sparse.csr_matrix(np.array([[1, 0], [1, 0]], dtype=float))
    graph = build_graph(mat, ["H-0001", "H-0002"], threshold=0.0)
    for node_id, info in graph["nodes"].items():
        targets = [e["target"] for e in info["edges"]]
        assert node_id not in targets


def test_build_graph_cluster_field_null() -> None:
    """All cluster fields should be null before clustering."""
    mat = scipy.sparse.csr_matrix(np.array([[1, 0], [0, 1]], dtype=float))
    graph = build_graph(mat, ["H-0001", "H-0002"], threshold=0.0)
    for info in graph["nodes"].values():
        assert info["cluster"] is None


def test_build_graph_threshold_stored() -> None:
    mat = scipy.sparse.csr_matrix(np.array([[1, 0]], dtype=float))
    graph = build_graph(mat, ["H-0001"], threshold=0.42)
    assert graph["threshold"] == 0.42


# --- run_graph (integration) ---


def test_run_graph_writes_file(tmp_path: Path) -> None:
    mat = scipy.sparse.csr_matrix(
        np.array([[1, 0, 1], [1, 0, 1], [0, 1, 0]], dtype=float)
    )
    _write_embeddings(tmp_path, mat, ["H-0001", "H-0002", "H-0003"])

    run_graph(tmp_path, threshold=0.3)

    graph_path = tmp_path / "proximity" / "graph.json"
    assert graph_path.exists()

    graph = json.loads(graph_path.read_text())
    assert "threshold" in graph
    assert "nodes" in graph
    assert len(graph["nodes"]) == 3
