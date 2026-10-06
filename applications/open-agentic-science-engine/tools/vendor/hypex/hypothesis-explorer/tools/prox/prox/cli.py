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

"""CLI entry-point for the prox tool."""

from __future__ import annotations

from pathlib import Path

import click

from prox.cluster import run_clusters
from prox.dupes import run_dupes
from prox.embed import run_embed
from prox.graph import run_graph


@click.group()
def main() -> None:
    """prox — proximity / similarity tool for Hypothesis-Explorer."""


@main.command()
@click.option(
    "--run-dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to the run directory containing hypotheses/.",
)
@click.option(
    "--backend",
    default="tfidf",
    type=click.Choice(["tfidf", "sbert"]),
    help="Embedding backend (default: tfidf).",
)
def embed(run_dir: Path, backend: str) -> None:
    """Vectorise hypothesis texts and write embeddings."""
    run_embed(run_dir, backend=backend)
    click.echo(f"Embeddings written to {run_dir / 'proximity'}")


@main.command()
@click.option(
    "--run-dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to the run directory.",
)
@click.option(
    "--threshold",
    default=0.3,
    type=float,
    help="Minimum cosine similarity to create an edge (default: 0.3).",
)
def graph(run_dir: Path, threshold: float) -> None:
    """Build a cosine-similarity adjacency graph from embeddings."""
    run_graph(run_dir, threshold=threshold)
    click.echo(f"Graph written to {run_dir / 'proximity' / 'graph.json'}")


@main.command()
@click.option(
    "--run-dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to the run directory.",
)
def clusters(run_dir: Path) -> None:
    """Detect communities and assign cluster labels."""
    run_clusters(run_dir)
    click.echo(f"Clusters written to {run_dir / 'proximity' / 'clusters.json'}")


@main.command()
@click.option(
    "--run-dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to the run directory.",
)
@click.option(
    "--threshold",
    default=0.80,
    type=float,
    help="Minimum similarity to flag as near-duplicate (default: 0.80).",
)
def dupes(run_dir: Path, threshold: float) -> None:
    """Report near-duplicate hypothesis pairs."""
    output = run_dupes(run_dir, threshold=threshold)
    click.echo(output)
