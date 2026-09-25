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

"""Tests for prox.embed — TF-IDF embedding of hypothesis text."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import scipy.sparse

from prox.embed import embed_tfidf, extract_text, load_hypotheses, run_embed

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a temporary run directory with hypothesis fixtures."""
    hyp_dir = tmp_path / "hypotheses"
    hyp_dir.mkdir()
    for src in sorted(FIXTURES.glob("H-*.json")):
        shutil.copy(src, hyp_dir / src.name)
    return tmp_path


@pytest.fixture()
def single_hyp_dir(tmp_path: Path) -> Path:
    """Run directory with exactly one hypothesis."""
    hyp_dir = tmp_path / "hypotheses"
    hyp_dir.mkdir()
    shutil.copy(FIXTURES / "H-0001.json", hyp_dir / "H-0001.json")
    return tmp_path


@pytest.fixture()
def empty_dir(tmp_path: Path) -> Path:
    """Run directory with no hypotheses/ subdirectory."""
    return tmp_path


# --- load_hypotheses ---


def test_load_hypotheses_returns_all(run_dir: Path) -> None:
    hyps = load_hypotheses(run_dir)
    assert len(hyps) == 5
    ids = [h["id"] for h in hyps]
    assert ids == ["H-0001", "H-0002", "H-0003", "H-0004", "H-0005"]


def test_load_hypotheses_empty(empty_dir: Path) -> None:
    assert load_hypotheses(empty_dir) == []


def test_load_hypotheses_single(single_hyp_dir: Path) -> None:
    hyps = load_hypotheses(single_hyp_dir)
    assert len(hyps) == 1
    assert hyps[0]["id"] == "H-0001"


# --- extract_text ---


def test_extract_text_concatenates_fields() -> None:
    hyp = {"title": "Alpha", "statement": "Beta claim", "mechanism": "Gamma pathway"}
    text = extract_text(hyp)
    assert "Alpha" in text
    assert "Beta claim" in text
    assert "Gamma pathway" in text


def test_extract_text_missing_fields() -> None:
    hyp: dict = {"title": "Only title"}
    text = extract_text(hyp)
    assert text == "Only title"


# --- embed_tfidf ---


def test_embed_tfidf_shape() -> None:
    texts = ["alpha beta", "gamma delta", "alpha gamma"]
    matrix = embed_tfidf(texts)
    assert matrix.shape[0] == 3
    assert matrix.shape[1] > 0


def test_embed_tfidf_empty() -> None:
    matrix = embed_tfidf([])
    assert matrix.shape == (0, 0)


def test_embed_tfidf_single() -> None:
    matrix = embed_tfidf(["hello world"])
    assert matrix.shape[0] == 1


def test_embed_tfidf_identical_texts() -> None:
    texts = ["identical text here", "identical text here"]
    matrix = embed_tfidf(texts)
    # Identical texts should produce identical vectors
    sim = (matrix[0].toarray() * matrix[1].toarray()).sum()
    assert sim == pytest.approx(1.0, abs=1e-6)


# --- run_embed (integration) ---


def test_run_embed_creates_files(run_dir: Path) -> None:
    run_embed(run_dir, backend="tfidf")
    prox = run_dir / "proximity"
    assert (prox / "embeddings.npz").exists()
    assert (prox / "embedding-ids.json").exists()

    ids = json.loads((prox / "embedding-ids.json").read_text())
    assert len(ids) == 5

    mat = scipy.sparse.load_npz(prox / "embeddings.npz")
    assert mat.shape[0] == 5


def test_run_embed_sbert_raises() -> None:
    with pytest.raises(NotImplementedError, match="sbert"):
        run_embed(Path("/nonexistent"), backend="sbert")


def test_run_embed_no_hypotheses(empty_dir: Path) -> None:
    with pytest.raises(SystemExit):
        run_embed(empty_dir)


def test_run_embed_single(single_hyp_dir: Path) -> None:
    run_embed(single_hyp_dir, backend="tfidf")
    prox = single_hyp_dir / "proximity"
    mat = scipy.sparse.load_npz(prox / "embeddings.npz")
    assert mat.shape[0] == 1


def test_load_hypotheses_skips_malformed(tmp_path: Path) -> None:
    """Malformed JSON files should be skipped, not crash the pipeline."""
    hyp_dir = tmp_path / "hypotheses"
    hyp_dir.mkdir()
    # One good file
    shutil.copy(FIXTURES / "H-0001.json", hyp_dir / "H-0001.json")
    # One malformed file
    (hyp_dir / "H-9999.json").write_text("{bad json!!!}")

    hyps = load_hypotheses(tmp_path)
    assert len(hyps) == 1
    assert hyps[0]["id"] == "H-0001"
