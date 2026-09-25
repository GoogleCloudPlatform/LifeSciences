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

"""Shared test helpers and fixtures for prox tests."""

from __future__ import annotations


def make_graph_data(edges: list[tuple[str, str, float]]) -> dict:
    """Build a minimal graph.json-style dict from edge tuples.

    Each edge is ``(source_id, target_id, similarity)``.  Both directions
    are added automatically (undirected graph).
    """
    node_ids: set[str] = set()
    for a, b, _ in edges:
        node_ids.update([a, b])

    nodes: dict = {nid: {"cluster": None, "edges": []} for nid in sorted(node_ids)}
    for a, b, sim in edges:
        nodes[a]["edges"].append({"target": b, "similarity": sim})
        nodes[b]["edges"].append({"target": a, "similarity": sim})

    return {"threshold": 0.3, "nodes": nodes}
