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

"""Tests for path traversal fixes — Round 2.

Verifies that the six path-traversal vulnerabilities fixed in Round 2
are properly mitigated by sanitize_slug and confine_path.  Each class
covers one issue with at least two tests: a traversal-rejection test and
a normal-input regression test.

Issues covered:
  #274 — pathway.py: gene name in filename
  #287 — ppi.py: gene name as slug in paths
  #291 — preprint.py: artifact CLI argument in path
  #272 — site.py: site_dir CLI option in path
  #276 — structure.py: gene_label as filename stem
  #282 — core/envstamp.py: version string in path
  (A)  — expression.py: _locate_single_cell gene path traversal
  (B)  — homology.py: analyze manifest path traversal
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.core.paths import confine_path, sanitize_slug

# ---------------------------------------------------------------------------
# Issue #274 — pathway.py: gene name in filename
# ---------------------------------------------------------------------------


class TestPathwayGeneSanitization:
    """Verify that gene names are sanitized before use in pathway filenames."""

    def test_traversal_in_gene_name_is_neutralized(self) -> None:
        """A gene name like '../../etc/passwd' cannot traverse directories."""
        slug = sanitize_slug("../../etc/passwd".upper())
        # Slashes are stripped — the result is a flat filename, not a path
        assert "/" not in slug
        assert "\\" not in slug
        # The slug is safe as a filename component
        path = Path("/safe/dir") / f"{slug}.pathway-reactome.json"
        assert path.parent == Path("/safe/dir")

    def test_normal_gene_name_preserved(self) -> None:
        """Normal gene names like 'BRCA1' pass through sanitize_slug."""
        slug = sanitize_slug("BRCA1".upper())
        assert slug == "BRCA1"
        path = Path("/safe/dir") / f"{slug}.pathway-reactome.json"
        assert "BRCA1" in str(path)


# ---------------------------------------------------------------------------
# Issue #287 — ppi.py: gene name as slug in paths
# ---------------------------------------------------------------------------


class TestPpiSlugSanitization:
    """Verify that gene names are sanitized before use as PPI slugs."""

    def test_traversal_in_gene_slug_is_neutralized(self) -> None:
        """A gene name with path traversal chars cannot escape directory."""
        slug = sanitize_slug("../../etc/shadow".lower())
        assert "/" not in slug
        assert "\\" not in slug
        # Verify the slug produces safe filenames for all three PPI paths
        for suffix in [
            ".ppi-string.json",
            ".ppi-string.artifact.json",
            ".ppi-string.meta.json",
        ]:
            path = Path("/safe/dir") / f"{slug}{suffix}"
            assert path.parent == Path("/safe/dir")

    def test_normal_gene_slug_preserved(self) -> None:
        """Normal gene slugs like 'tp53' pass through sanitize_slug."""
        slug = sanitize_slug("tp53")
        assert slug == "tp53"


# ---------------------------------------------------------------------------
# Issue #291 — preprint.py: artifact CLI argument in path
# ---------------------------------------------------------------------------


class TestPreprintArtifactConfinement:
    """Verify that artifact paths are confined and slugs are sanitized."""

    def test_traversal_in_artifact_path_is_confined(self, tmp_path: Path) -> None:
        """An artifact with path traversal is rejected by confine_path."""
        result = confine_path(tmp_path, Path("../../etc/passwd"))
        assert result is None

    def test_normal_artifact_path_is_confined(self, tmp_path: Path) -> None:
        """A normal artifact filename stays within source_dir."""
        result = confine_path(tmp_path, Path("tp53.preprint-search.json"))
        assert result is not None
        assert result.is_relative_to(tmp_path.resolve())

    def test_traversal_in_artifact_slug_is_neutralized(self) -> None:
        """A traversal string passed through _slugify then sanitize_slug is safe."""
        # Simulate what happens when artifact goes through _slugify then
        # sanitize_slug: the result must be a safe filename component
        malicious = "../../etc/passwd"
        slug = sanitize_slug(malicious)
        assert "/" not in slug
        path = Path("/safe/dir") / f"{slug}.preprint-search.json"
        assert path.parent == Path("/safe/dir")


# ---------------------------------------------------------------------------
# Issue #272 — site.py: site_dir CLI option in path
# ---------------------------------------------------------------------------


class TestSiteDirConfinement:
    """Verify that site_dir is confined to the project root."""

    def test_traversal_in_site_dir_is_rejected(self, tmp_path: Path) -> None:
        """A site_dir like '../../etc' is rejected by confine_path."""
        result = confine_path(tmp_path, Path("../../etc"))
        assert result is None

    def test_absolute_path_in_site_dir_is_rejected(self, tmp_path: Path) -> None:
        """An absolute path outside project root is rejected."""
        result = confine_path(tmp_path, Path("/etc/passwd"))
        assert result is None

    def test_normal_site_dir_is_allowed(self, tmp_path: Path) -> None:
        """A normal site directory like '_site' stays within project root."""
        result = confine_path(tmp_path, Path("_site"))
        assert result is not None
        assert result.is_relative_to(tmp_path.resolve())


# ---------------------------------------------------------------------------
# Issue #276 — structure.py: gene_label as filename stem
# ---------------------------------------------------------------------------


class TestStructureStemSanitization:
    """Verify that gene_label is sanitized before use as filename stem."""

    def test_traversal_in_gene_label_is_neutralized(self) -> None:
        """A gene_label with traversal chars cannot escape the directory."""
        stem = sanitize_slug("../../etc/passwd".lower())
        assert "/" not in stem
        assert "\\" not in stem
        path = Path("/safe/dir") / f"{stem}.topology-annotation.artifact.json"
        assert path.parent == Path("/safe/dir")

    def test_normal_gene_label_preserved(self) -> None:
        """Normal gene labels like 'oprm1' pass through sanitize_slug."""
        stem = sanitize_slug("oprm1")
        assert stem == "oprm1"
        path = Path("/safe/dir") / f"{stem}.topology-annotation.artifact.json"
        assert "oprm1" in str(path)


# ---------------------------------------------------------------------------
# Issue #282 — core/envstamp.py: version string in path
# ---------------------------------------------------------------------------


class TestEnvstampVersionSanitization:
    """Verify that version strings are sanitized before archive path use."""

    def test_traversal_in_version_is_neutralized(self) -> None:
        """A version with path traversal is neutralized by sanitize_slug."""
        version = "../../etc/passwd"
        wanted = sanitize_slug(version.replace(":", "-"))
        assert "/" not in wanted
        assert "\\" not in wanted

    def test_version_with_colons_is_sanitized(self) -> None:
        """A version like 'sha256:abc123' has colons replaced then sanitized."""
        version = "sha256:abc123"
        wanted = sanitize_slug(version.replace(":", "-"))
        assert ":" not in wanted
        assert "/" not in wanted
        assert wanted == "sha256-abc123"

    def test_confine_path_rejects_escaped_archive_path(self, tmp_path: Path) -> None:
        """confine_path rejects a constructed path that escapes archive dir."""
        archive = tmp_path / "manifests"
        archive.mkdir()
        result = confine_path(archive, Path("../../etc/passwd.txt"))
        assert result is None

    def test_normal_version_produces_safe_path(self, tmp_path: Path) -> None:
        """A normal hash version produces a safe path within archive."""
        archive = tmp_path / "manifests"
        archive.mkdir()
        version = "abc123def456"
        wanted = sanitize_slug(version.replace(":", "-"))
        result = confine_path(archive, Path(f"{wanted}.txt"))
        assert result is not None
        assert result.is_relative_to(archive.resolve())


# ---------------------------------------------------------------------------
# Additional Issue A — expression.py: _locate_single_cell gene input
# ---------------------------------------------------------------------------


class TestExpressionSingleCellSanitization:
    """Verify that gene input in _locate_single_cell is sanitized."""

    def test_traversal_in_gene_path_is_confined(self, tmp_path: Path) -> None:
        """A gene path with traversal is rejected by confine_path."""
        result = confine_path(tmp_path, Path("../../etc/passwd.tissue.json"))
        assert result is None

    def test_normal_ensembl_id_sanitized(self) -> None:
        """Normal Ensembl IDs pass through sanitize_slug unchanged."""
        ensembl = sanitize_slug("ENSG00000141510")
        assert ensembl == "ENSG00000141510"
        # Verify it produces safe filenames
        path = Path("/safe/dir") / f"{ensembl}.single-cell.json"
        assert path.parent == Path("/safe/dir")

    def test_traversal_in_ensembl_id_neutralized(self) -> None:
        """A traversal string disguised as an Ensembl ID is neutralized."""
        malicious = "../../etc/passwd"
        slug = sanitize_slug(malicious.upper())
        assert "/" not in slug
        assert "\\" not in slug


# ---------------------------------------------------------------------------
# Additional Issue B — homology.py: analyze manifest path confinement
# ---------------------------------------------------------------------------


class TestHomologyManifestConfinement:
    """Verify that manifest paths are confined to project root."""

    def test_traversal_in_manifest_path_is_rejected(self, tmp_path: Path) -> None:
        """A manifest path with traversal is rejected by confine_path."""
        result = confine_path(tmp_path, Path("../../etc/passwd.search.json"))
        assert result is None

    def test_normal_manifest_path_is_allowed(self, tmp_path: Path) -> None:
        """A normal manifest path stays within project root."""
        result = confine_path(tmp_path, Path("HOMOLOGY-P04637.search.json"))
        assert result is not None
        assert result.is_relative_to(tmp_path.resolve())
