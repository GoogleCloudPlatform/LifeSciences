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

"""Regression tests for symlink exploitation fixes in skills/ and vendor/.

Covers:
  - fix_add_themes.py: rglob HTML symlink following (#246)
  - postbuild-template.py: rglob HTML symlink following in all fix functions (#247)
  - embed.py: glob hypothesis file symlink following (#307)
  - validate.go: DirEntry symlink following (#306) — conceptual Python test

Run with:
    cd applications/DDE
    python -m pytest tests/test_symlink_vendor.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure the tools and skills packages are importable.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "skills" / "site-generation" / "references"))

import importlib

from fix_add_themes import fix_add_themes

# postbuild-template.py has a hyphen in the filename, so we use importlib.
_postbuild_spec = importlib.util.spec_from_file_location(
    "postbuild_template",
    str(
        REPO_ROOT
        / "skills"
        / "site-generation"
        / "references"
        / "postbuild-template.py"
    ),
)
_postbuild_mod = importlib.util.module_from_spec(_postbuild_spec)
_postbuild_spec.loader.exec_module(_postbuild_mod)
fix_tables = _postbuild_mod.fix_tables
fix_duplicate_h1 = _postbuild_mod.fix_duplicate_h1
fix_md_links = _postbuild_mod.fix_md_links


# ---------------------------------------------------------------------------
# Fix #246: fix_add_themes.py — rglob HTML symlink following
# ---------------------------------------------------------------------------


class TestFixAddThemesSymlink:
    """Verify fix_add_themes skips symlinked HTML files."""

    def _make_site(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        """Create a site dir with a real HTML file and a symlinked one."""
        site_dir = tmp_path / "site"
        site_dir.mkdir()

        # Real HTML file that should be processed.
        real_html = site_dir / "index.html"
        real_html.write_text(
            "<html><head></head><body><nav></nav>Hello</body></html>",
            encoding="utf-8",
        )

        # Outside target — must not be modified.
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "evil.html"
        target.write_text(
            "<html><head></head><body>Secret</body></html>",
            encoding="utf-8",
        )

        # Symlink inside site dir pointing outside.
        link = site_dir / "evil.html"
        link.symlink_to(target)

        return site_dir, target, real_html

    def test_symlink_html_not_modified(self, tmp_path: Path) -> None:
        """fix_add_themes must skip symlinked HTML files."""
        site_dir, target, _real = self._make_site(tmp_path)
        original_content = target.read_text()

        fix_add_themes(site_dir)

        # The symlink target must be untouched.
        assert target.read_text() == original_content

    def test_real_html_still_processed(self, tmp_path: Path) -> None:
        """fix_add_themes must still process real (non-symlink) HTML files."""
        site_dir, _target, real = self._make_site(tmp_path)

        fix_add_themes(site_dir)

        # The real file should have the theme marker injected.
        text = real.read_text(encoding="utf-8")
        assert "<!-- dde-theme-system -->" in text


# ---------------------------------------------------------------------------
# Fix #247: postbuild-template.py — rglob HTML symlink following
# ---------------------------------------------------------------------------


class TestPostbuildTemplateSymlink:
    """Verify postbuild fix functions skip symlinked HTML files."""

    def _make_site_with_symlink(
        self, tmp_path: Path, content: str
    ) -> tuple[Path, Path]:
        """Create a site dir with a symlinked HTML file containing *content*."""
        site_dir = tmp_path / "site"
        site_dir.mkdir()

        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "evil.html"
        target.write_text(content, encoding="utf-8")

        link = site_dir / "evil.html"
        link.symlink_to(target)

        return site_dir, target

    def test_fix_tables_skips_symlink(self, tmp_path: Path) -> None:
        """fix_tables must skip symlinked HTML files."""
        content = "<html><body>| col1 | col2 |\n| a | b |</body></html>"
        site_dir, target = self._make_site_with_symlink(tmp_path, content)
        original = target.read_text()

        fix_tables(site_dir)

        assert target.read_text() == original

    def test_fix_duplicate_h1_skips_symlink(self, tmp_path: Path) -> None:
        """fix_duplicate_h1 must skip symlinked HTML files."""
        content = "<html><body><h1>Title</h1><h1>Title</h1></body></html>"
        site_dir, target = self._make_site_with_symlink(tmp_path, content)
        original = target.read_text()

        fix_duplicate_h1(site_dir)

        assert target.read_text() == original

    def test_fix_md_links_skips_symlink(self, tmp_path: Path) -> None:
        """fix_md_links must skip symlinked HTML files."""
        content = '<html><body><a href="page.md">link</a></body></html>'
        site_dir, target = self._make_site_with_symlink(tmp_path, content)
        original = target.read_text()

        fix_md_links(site_dir)

        assert target.read_text() == original


# ---------------------------------------------------------------------------
# Fix #307: embed.py — glob hypothesis file symlink following
# ---------------------------------------------------------------------------


def _import_embed_load_hypotheses():
    """Import load_hypotheses from embed.py, mocking unavailable dependencies."""
    from types import ModuleType
    from unittest import mock

    # Mock scipy and sklearn which may not be installed in the test environment.
    mock_scipy = ModuleType("scipy")
    mock_scipy.sparse = ModuleType("scipy.sparse")
    mock_sklearn = ModuleType("sklearn")
    mock_sklearn.feature_extraction = ModuleType("sklearn.feature_extraction")
    mock_sklearn.feature_extraction.text = ModuleType("sklearn.feature_extraction.text")
    mock_sklearn.feature_extraction.text.TfidfVectorizer = mock.MagicMock()

    # Mock prox._io which the module imports at top level.
    mock_prox = ModuleType("prox")
    mock_prox._io = ModuleType("prox._io")
    mock_prox._io.atomic_write_json = mock.MagicMock()
    mock_prox._io.atomic_write_npz = mock.MagicMock()

    embed_path = (
        REPO_ROOT
        / "tools"
        / "vendor"
        / "hypex"
        / "hypothesis-explorer"
        / "tools"
        / "prox"
        / "prox"
        / "embed.py"
    )
    with mock.patch.dict(
        sys.modules,
        {
            "scipy": mock_scipy,
            "scipy.sparse": mock_scipy.sparse,
            "sklearn": mock_sklearn,
            "sklearn.feature_extraction": mock_sklearn.feature_extraction,
            "sklearn.feature_extraction.text": mock_sklearn.feature_extraction.text,
            "prox._io": mock_prox._io,
        },
    ):
        spec = importlib.util.spec_from_file_location("prox.embed", str(embed_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod.load_hypotheses


class TestEmbedLoadHypothesesSymlink:
    """Verify load_hypotheses skips symlinked hypothesis files."""

    def test_symlink_hypothesis_skipped(self, tmp_path: Path) -> None:
        """load_hypotheses must skip symlinked H-*.json files."""
        load_hypotheses = _import_embed_load_hypotheses()

        # Create run directory structure.
        hyp_dir = tmp_path / "hypotheses"
        hyp_dir.mkdir()

        # Real hypothesis file.
        real_hyp = {
            "id": "H-001",
            "title": "Real Hypothesis",
            "statement": "A real statement",
            "mechanism": "A real mechanism",
        }
        (hyp_dir / "H-001.json").write_text(json.dumps(real_hyp))

        # Outside target — a malicious hypothesis.
        outside = tmp_path / "outside"
        outside.mkdir()
        evil_hyp = {
            "id": "H-EVIL",
            "title": "Evil Hypothesis",
            "statement": "Injected",
            "mechanism": "Malicious",
        }
        target = outside / "H-EVIL.json"
        target.write_text(json.dumps(evil_hyp))

        # Symlink inside hypotheses dir.
        link = hyp_dir / "H-EVIL.json"
        link.symlink_to(target)

        hypotheses = load_hypotheses(tmp_path)

        # Only the real hypothesis should be loaded.
        ids = [h["id"] for h in hypotheses]
        assert "H-001" in ids
        assert "H-EVIL" not in ids

    def test_all_real_files_still_loaded(self, tmp_path: Path) -> None:
        """load_hypotheses must still load all non-symlink files."""
        load_hypotheses = _import_embed_load_hypotheses()

        hyp_dir = tmp_path / "hypotheses"
        hyp_dir.mkdir()

        for i in range(3):
            hyp = {
                "id": f"H-{i:03d}",
                "title": f"Hypothesis {i}",
                "statement": f"Statement {i}",
                "mechanism": f"Mechanism {i}",
            }
            (hyp_dir / f"H-{i:03d}.json").write_text(json.dumps(hyp))

        hypotheses = load_hypotheses(tmp_path)
        assert len(hypotheses) == 3


# ---------------------------------------------------------------------------
# Fix #306: validate.go — DirEntry symlink check (conceptual Python test)
# ---------------------------------------------------------------------------


class TestGoDirEntrySymlinkConcept:
    """Conceptual test verifying the DirEntry symlink filtering pattern.

    The actual fix is in Go code. This Python test verifies the same
    pattern works with Python's os.scandir (which returns DirEntry
    objects similar to Go's fs.DirEntry).
    """

    def test_scandir_skips_symlinks(self, tmp_path: Path) -> None:
        """os.scandir DirEntry.is_symlink() correctly identifies symlinks."""
        import os

        # Create a regular JSON file.
        (tmp_path / "H-001.json").write_text("{}")

        # Create a symlink to an outside file.
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "H-EVIL.json"
        target.write_text("{}")
        (tmp_path / "H-EVIL.json").symlink_to(target)

        # Scan and filter, mirroring the Go fix pattern.
        results = []
        for entry in os.scandir(tmp_path):
            if entry.is_symlink():
                continue
            if not entry.is_dir() and entry.name.endswith(".json"):
                results.append(entry.name)

        assert "H-001.json" in results
        assert "H-EVIL.json" not in results
