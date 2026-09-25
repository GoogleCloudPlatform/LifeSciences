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

"""Build a thresholded cosine-similarity adjacency graph from embeddings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import scipy.sparse
from sklearn.metrics.pairwise import cosine_similarity

from prox._io import atomic_write_json


def load_embeddings(run_dir: Path) -> tuple[scipy.sparse.csr_matrix, list[str]]:
    """Load the embedding matrix and hypothesis IDs from *run_dir*/proximity/."""
    prox_dir = run_dir / "proximity"

    matrix = scipy.sparse.load_npz(prox_dir / "embeddings.npz")

    with open(prox_dir / "embedding-ids.json") as fh:
        ids: list[str] = json.load(fh)

    return matrix, ids


def build_graph(
    matrix: scipy.sparse.csr_matrix,
    ids: list[str],
    threshold: float = 0.3,
) -> dict[str, Any]:
    """Compute pairwise cosine similarity and return an adjacency-list graph.

    Only edges whose similarity ≥ *threshold* are included.
    Self-edges are excluded.
    """
    if matrix.shape[0] == 0:
        return {"threshold": threshold, "nodes": {}}

    sim_matrix = cosine_similarity(matrix)

    nodes: dict[str, Any] = {}
    for i, src_id in enumerate(ids):
        edges: list[dict[str, Any]] = []
        for j, tgt_id in enumerate(ids):
            if i == j:
                continue
            sim = float(sim_matrix[i, j])
            if sim >= threshold:
                edges.append({"target": tgt_id, "similarity": round(sim, 6)})
        # Sort edges by descending similarity for stable output
        edges.sort(key=lambda e: (-e["similarity"], e["target"]))
        nodes[src_id] = {"cluster": None, "edges": edges}

    return {"threshold": threshold, "nodes": nodes}


def run_graph(run_dir: Path, threshold: float = 0.3) -> None:
    """End-to-end graph pipeline: load embeddings → build graph → save."""
    matrix, ids = load_embeddings(run_dir)
    graph = build_graph(matrix, ids, threshold=threshold)

    prox_dir = run_dir / "proximity"
    prox_dir.mkdir(parents=True, exist_ok=True)

    atomic_write_json(prox_dir / "graph.json", graph)
