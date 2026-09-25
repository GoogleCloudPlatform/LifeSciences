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

"""TF-IDF embedding of hypothesis text fields."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import scipy.sparse
from sklearn.feature_extraction.text import TfidfVectorizer

from prox._io import atomic_write_json, atomic_write_npz

log = logging.getLogger(__name__)


def load_hypotheses(run_dir: Path) -> list[dict[str, Any]]:
    """Load all H-*.json hypothesis files from *run_dir*/hypotheses/.

    Malformed files are logged and skipped rather than aborting the pipeline.
    """
    hyp_dir = run_dir / "hypotheses"
    if not hyp_dir.is_dir():
        return []

    hypotheses: list[dict[str, Any]] = []
    for path in sorted(hyp_dir.glob("H-*.json")):
        # Skip symlinks to prevent symlink exploitation (issue #307).
        if path.is_symlink():
            log.warning("Skipping symlink hypothesis file %s", path.name)
            continue
        try:
            with open(path) as fh:
                hypotheses.append(json.load(fh))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Skipping malformed hypothesis file %s: %s", path.name, exc)
    return hypotheses


def extract_text(hyp: dict[str, Any]) -> str:
    """Concatenate title, statement, and mechanism into a single text string."""
    parts = [
        hyp.get("title", ""),
        hyp.get("statement", ""),
        hyp.get("mechanism", ""),
    ]
    return " ".join(p for p in parts if p)


def embed_tfidf(
    texts: list[str],
) -> scipy.sparse.csr_matrix:
    """Vectorise *texts* with TF-IDF and return a sparse matrix.

    Returns a CSR matrix of shape (n_docs, n_features).
    """
    if not texts:
        return scipy.sparse.csr_matrix((0, 0))

    vectorizer = TfidfVectorizer()
    return vectorizer.fit_transform(texts)


def run_embed(run_dir: Path, backend: str = "tfidf") -> None:
    """End-to-end embed pipeline: load → extract → vectorise → save."""
    if backend != "tfidf":
        raise NotImplementedError(
            f"Backend '{backend}' is not implemented. "
            "Currently only 'tfidf' is supported. "
            "Sentence-transformer support (sbert) is planned for a future release."
        )

    hypotheses = load_hypotheses(run_dir)
    if not hypotheses:
        raise SystemExit("No hypothesis files found in " + str(run_dir / "hypotheses"))

    ids = [h["id"] for h in hypotheses]
    texts = [extract_text(h) for h in hypotheses]

    matrix = embed_tfidf(texts)

    prox_dir = run_dir / "proximity"
    prox_dir.mkdir(parents=True, exist_ok=True)

    atomic_write_npz(prox_dir / "embeddings.npz", matrix)
    atomic_write_json(prox_dir / "embedding-ids.json", ids)
