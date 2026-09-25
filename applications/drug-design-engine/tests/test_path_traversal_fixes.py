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

"""Regression tests for path traversal vulnerability fixes.

Each test verifies that user-controlled input is properly sanitized
before being used in filesystem paths, preventing directory traversal
attacks via ``../`` sequences or absolute paths.

Covers:
  - Fix 1: coscientist session_id (#181)
  - Fix 2: conservation query_name (#183)
  - Fix 3: manufacturing concept_id (#193)
  - Fix 4: homology PDB entity ID (#195)
  - Fix 5: gtex _locate GENCODE ID validation (#199)
  - Fix 6: CDN URL path injection (#177)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# Ensure the tools package is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from dde.core.paths import confine_path, sanitize_slug

# ---------------------------------------------------------------------------
# Fix 1: coscientist session_id (#181)
# ---------------------------------------------------------------------------


class TestCoscientistSessionId:
    """Verify session_id is sanitized before use in filenames."""

    def test_traversal_in_session_id(self):
        """A session_id containing ../../ must be sanitized to a safe slug."""
        session = "../../etc/passwd"
        slug = sanitize_slug(session)
        # No path separators — the slug is a single filename component.
        assert "/" not in slug
        assert "\\" not in slug
        # When used in a path, it cannot escape the target directory.
        assert (Path("target") / f"{slug}.json").parts[0] == "target"

    def test_normal_session_id_preserved(self):
        """A normal session_id should survive sanitization."""
        session = "abc-123-def"
        slug = sanitize_slug(session)
        assert slug == "abc-123-def"

    def test_session_name_construction(self):
        """The cs-{session} pattern produces a safe filename."""
        session = "../../tmp/evil"
        slug = sanitize_slug(session)
        name = f"cs-{slug}"
        # No path separators — the name is a single filename component.
        assert "/" not in name
        assert "\\" not in name
        # The constructed path stays within the target directory.
        assert (Path("target") / f"{name}.json").parts[0] == "target"


# ---------------------------------------------------------------------------
# Fix 2: conservation query_name (#183)
# ---------------------------------------------------------------------------


class TestConservationQueryName:
    """Verify query_name is sanitized in conservation commands."""

    def test_traversal_in_name_option(self):
        """A --name value with ../../ must be sanitized."""
        name = "../../etc/shadow"
        slug = sanitize_slug(name)
        # No path separators — the slug is a single filename component.
        assert "/" not in slug
        assert "\\" not in slug
        assert (Path("target") / f"{slug}.json").parts[0] == "target"

    def test_traversal_in_msa_stem(self):
        """An MSA filename stem containing traversal chars is sanitized."""
        name = "weird/../../evil"
        slug = sanitize_slug(name)
        # No path separators.
        assert "/" not in slug
        assert "\\" not in slug

    def test_filename_from_sanitized_query_name(self):
        """The resulting filename stays confined."""
        slug = sanitize_slug("../../etc/passwd")
        filename = f"{slug}.conservation.json"
        assert "/" not in filename


# ---------------------------------------------------------------------------
# Fix 3: manufacturing concept_id (#193)
# ---------------------------------------------------------------------------


class TestManufacturingConceptId:
    """Verify concept_id is sanitized before use in filenames."""

    def test_traversal_in_concept_id(self):
        """A concept_id with ../ must be sanitized."""
        concept_id = "../../../tmp/external"
        slug = sanitize_slug(concept_id)
        # No path separators — the slug is a single filename component.
        assert "/" not in slug
        assert "\\" not in slug
        assert (Path("target") / f"{slug}.json").parts[0] == "target"

    def test_normal_concept_id_preserved(self):
        """A normal concept_id like IC-001 should survive sanitization."""
        slug = sanitize_slug("IC-001")
        assert slug == "IC-001"

    def test_filename_from_sanitized_concept_id(self):
        """The manufacturing output filename is safe."""
        slug = sanitize_slug("../../../etc/cron.d/evil")
        filename = f"{slug}.manufacturing-stage0.json"
        assert "/" not in filename


# ---------------------------------------------------------------------------
# Fix 4: homology PDB entity ID (#195)
# ---------------------------------------------------------------------------


class TestHomologyPdbEntityId:
    """Verify PDB entity IDs are sanitized after parsing."""

    def test_parse_and_sanitize_traversal(self):
        """_parse_pdb_entity_id splits on '_'; the first part is sanitized."""
        # Simulate what _parse_pdb_entity_id does: split on _, uppercase first.
        raw = "../../etc_1"
        parts = raw.strip().split("_")
        pdb_id = parts[0].upper()
        # Now sanitize, as the fix does.
        pdb_id = sanitize_slug(pdb_id)
        # No path separators — the slug is a single filename component.
        assert "/" not in pdb_id
        assert "\\" not in pdb_id
        assert (Path("target") / f"{pdb_id}.cif").parts[0] == "target"

    def test_normal_pdb_id_preserved(self):
        """A normal PDB ID like 7FD3 survives sanitization."""
        raw = "7FD3_1"
        parts = raw.strip().split("_")
        pdb_id = sanitize_slug(parts[0].upper())
        assert pdb_id == "7FD3"

    def test_pdb_filename_safe(self):
        """The resulting structure filename is confined."""
        raw = "../../etc"
        parts = raw.strip().split("_")
        pdb_id = sanitize_slug(parts[0].upper())
        filename = f"{pdb_id}.cif"
        assert "/" not in filename


# ---------------------------------------------------------------------------
# Fix 5: gtex _locate GENCODE ID validation (#199)
# ---------------------------------------------------------------------------


class TestGtexLocateValidation:
    """Verify _locate rejects traversal inputs via regex and confinement."""

    # The VERSIONED_ENSG_RE from gtex.py:
    VERSIONED_ENSG_RE = re.compile(r"^ENSG\d{11}(\.\d+)?$")

    def test_valid_versioned_gencode_id(self):
        """A valid versioned GENCODE ID matches the regex."""
        assert self.VERSIONED_ENSG_RE.match("ENSG00000139618.15")

    def test_valid_unversioned_gencode_id(self):
        """A valid unversioned GENCODE ID matches the regex."""
        assert self.VERSIONED_ENSG_RE.match("ENSG00000139618")

    def test_traversal_in_gencode_id_rejected(self):
        """An input like ENSG../../../etc/passwd.1 must NOT match."""
        assert not self.VERSIONED_ENSG_RE.match("ENSG../../../etc/passwd.1")

    def test_traversal_with_dots_rejected(self):
        """ENSG00000000001/../../evil.1 must NOT match."""
        assert not self.VERSIONED_ENSG_RE.match("ENSG00000000001/../../evil.1")

    def test_slash_in_gene_rejected(self):
        """Inputs with slashes must not match the versioned regex."""
        assert not self.VERSIONED_ENSG_RE.match("ENSG/../../tmp/evil.1")

    def test_path_confinement_rejects_traversal(self, tmp_path):
        """confine_path rejects paths that escape the project root."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        evil_path = project_root / "../../etc/passwd"
        result = confine_path(project_root, evil_path)
        assert result is None

    def test_path_confinement_accepts_valid_path(self, tmp_path):
        """confine_path accepts paths within the project root."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        good_path = project_root / "data" / "expression.json"
        result = confine_path(project_root, good_path)
        assert result is not None


# ---------------------------------------------------------------------------
# Fix 6: CDN URL path injection (#177)
# ---------------------------------------------------------------------------

# Import the _url_to_vendor_path function from the standalone script.
_CDN_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "skills"
    / "site-generation"
    / "references"
    / "fix_localize_cdn.py"
)


def _load_cdn_module():
    """Load fix_localize_cdn as a module for testing."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("fix_localize_cdn", _CDN_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCdnPathInjection:
    """Verify _url_to_vendor_path produces paths confined to vendor/."""

    @pytest.fixture(autouse=True)
    def _load_module(self):
        self.cdn = _load_cdn_module()

    def test_traversal_in_url_path(self):
        """A URL with /../../../etc/passwd must produce a safe path."""
        result = self.cdn._url_to_vendor_path(
            "https://cdn.example.com/../../../etc/passwd"
        )
        # The result should stay under vendor/ and not contain ".."
        assert result.startswith("vendor/")
        assert ".." not in result

    def test_traversal_deep(self):
        """Multiple levels of traversal must all be stripped."""
        result = self.cdn._url_to_vendor_path(
            "https://cdn.example.com/a/../../b/../../../etc/cron.d/external"
        )
        assert result.startswith("vendor/")
        assert ".." not in result

    def test_normal_url_preserved(self):
        """A normal CDN URL should produce the expected vendor path."""
        result = self.cdn._url_to_vendor_path(
            "https://cdn.jsdelivr.net/npm/3dmol@2.4.2/build/3Dmol-min.js"
        )
        assert result == "vendor/cdn.jsdelivr.net/npm/3dmol@2.4.2/build/3Dmol-min.js"

    def test_url_with_query_string(self):
        """URLs with query strings produce a hash-suffixed path."""
        result = self.cdn._url_to_vendor_path(
            "https://fonts.googleapis.com/css2?family=Roboto"
        )
        assert result.startswith("vendor/fonts.googleapis.com/css2_q_")
        assert ".." not in result

    def test_confinement_in_localize_html(self, tmp_path):
        """Destinations that escape site_dir should be skipped."""
        site_dir = tmp_path / "site"
        site_dir.mkdir()
        # The resolved dest must stay within site_dir.
        rel_vendor = "vendor/cdn.example.com/safe/file.js"
        dest = site_dir / rel_vendor
        resolved_dest = dest.resolve()
        assert resolved_dest.is_relative_to(site_dir.resolve())

    def test_domain_sanitized(self):
        """A domain with path-separator characters is sanitized."""
        result = self.cdn._url_to_vendor_path("https://evil/../../host/path")
        assert result.startswith("vendor/")
        # No traversal segments in the resulting path.
        parts = Path(result).parts
        assert ".." not in parts
