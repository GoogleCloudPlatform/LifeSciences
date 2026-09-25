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

"""Tests for path traversal fixes — Round 1.

Verifies that the seven path-traversal vulnerabilities fixed in Round 1
are properly mitigated by sanitize_slug and confine_path.  Each class
covers one issue with at least two tests: a traversal-rejection test and
a normal-input regression test.

Issues covered:
  #252 — genetics.py: gene symbol in filename
  #249 — alphagenome.py: variant parameters in filename
  #258 — docking.py: receptor_id from JSON in path
  #265 — env.py: version token in diff path
  #268 — expression.py: gene input in path construction
  #260 — homology.py: query_accession and query_gene in filename
  #273 — litref.py: citation as path and slug
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
# Issue #252 — genetics.py: gene symbol in filename
# ---------------------------------------------------------------------------


class TestGeneticsSymbolSanitization:
    """Verify that gene symbols are sanitized before use in filenames."""

    def test_traversal_in_gene_symbol_is_neutralized(self) -> None:
        """A gene symbol like '../../etc/passwd' cannot traverse directories."""
        slug = sanitize_slug("../../etc/passwd".upper())
        # Slashes are stripped — the result is a flat filename, not a path
        assert "/" not in slug
        assert "\\" not in slug
        # The slug is safe as a filename component: no directory separators
        path = Path("/safe/dir") / f"{slug}.json"
        assert path.parent == Path("/safe/dir")

    def test_normal_gene_symbol_preserved(self) -> None:
        """Normal gene symbols like 'TP53' pass through sanitize_slug."""
        slug = sanitize_slug("TP53")
        assert slug == "TP53"


# ---------------------------------------------------------------------------
# Issue #249 — alphagenome.py: variant parameters in filename
# ---------------------------------------------------------------------------


class TestAlphagenomeVariantStemSanitization:
    """Verify that variant stem components are sanitized."""

    def test_traversal_in_variant_params_is_neutralized(self) -> None:
        """Variant parameters with path traversal chars are neutralized."""
        chrom = "../../etc"
        pos = "passwd"
        ref = "A"
        alt = "T"
        stem = sanitize_slug(f"{chrom}-{pos}-{ref}-{alt}")
        # Slashes removed — result cannot create subdirectories
        assert "/" not in stem
        path = Path("/safe/dir") / f"{stem}.response.ndjson"
        assert path.parent == Path("/safe/dir")

    def test_normal_variant_stem_preserved(self) -> None:
        """Normal variant components produce a clean stem."""
        stem = sanitize_slug("chr1-12345-A-G")
        assert stem == "chr1-12345-A-G"


# ---------------------------------------------------------------------------
# Issue #258 — docking.py: receptor_id from JSON in path
# ---------------------------------------------------------------------------


class TestDockingReceptorIdSanitization:
    """Verify that receptor_id from JSON is sanitized before path use."""

    def test_traversal_in_receptor_id_is_neutralized(self) -> None:
        """A receptor_id with traversal chars cannot escape the directory."""
        receptor_id = "../../etc/shadow"
        slug = sanitize_slug(receptor_id)
        assert "/" not in slug
        assert "\\" not in slug

    def test_normal_receptor_id_preserved(self) -> None:
        """Normal receptor IDs like '6LU7' pass through cleanly."""
        slug = sanitize_slug("6LU7")
        assert slug == "6LU7"

    def test_confine_path_rejects_traversal(self, tmp_path: Path) -> None:
        """confine_path rejects a constructed path that escapes from_dir."""
        result = confine_path(tmp_path, Path("../../etc/passwd.prepare.meta.json"))
        assert result is None


# ---------------------------------------------------------------------------
# Issue #265 — env.py: version token in diff path
# ---------------------------------------------------------------------------


class TestEnvVersionTokenSanitization:
    """Verify that version tokens are sanitized before use in archived()."""

    def test_traversal_in_version_token_is_neutralized(self) -> None:
        """A version token with path traversal is neutralized."""
        token = "../../etc/passwd"
        slug = sanitize_slug(token)
        assert "/" not in slug
        assert "\\" not in slug

    def test_normal_version_token_preserved(self) -> None:
        """A sha256-prefix version token passes through cleanly."""
        # The colon in sha256:abc123 is replaced, which is the expected
        # behaviour — envstamp.archived() already does .replace(":", "-").
        slug = sanitize_slug("sha256-abc123def456")
        assert slug == "sha256-abc123def456"

    def test_sha256_colon_token_sanitized_consistently(self) -> None:
        """A sha256:… token has its colon replaced consistently."""
        slug = sanitize_slug("sha256:abc123")
        # Colon becomes hyphen, consecutive hyphens collapse
        assert ":" not in slug
        assert "/" not in slug


# ---------------------------------------------------------------------------
# Issue #268 — expression.py: gene input in path construction
# ---------------------------------------------------------------------------


class TestExpressionGeneSanitization:
    """Verify that gene identifiers are sanitized in expression lookups."""

    def test_traversal_in_gene_path_confined(self, tmp_path: Path) -> None:
        """A gene input that looks like a path is confined to project root."""
        # Simulates expression._locate receiving a path-like gene
        malicious = Path("../../etc/passwd.tissue.json")
        result = confine_path(tmp_path, malicious)
        assert result is None

    def test_normal_ensembl_id_preserved(self) -> None:
        """Normal Ensembl IDs pass through sanitize_slug unchanged."""
        slug = sanitize_slug("ENSG00000141510")
        assert slug == "ENSG00000141510"

    def test_ensembl_id_with_traversal_neutralized(self) -> None:
        """An Ensembl-like ID with traversal chars is neutralized."""
        slug = sanitize_slug("ENSG00000141510/../../etc")
        assert "/" not in slug


# ---------------------------------------------------------------------------
# Issue #260 — homology.py: query_accession and query_gene in filename
# ---------------------------------------------------------------------------


class TestHomologyInputSanitization:
    """Verify that accession and gene inputs are sanitized for filenames."""

    def test_traversal_in_accession_neutralized(self) -> None:
        """Path traversal in query_accession is neutralized."""
        accession = "../../etc/shadow"
        slug = sanitize_slug(accession)
        assert "/" not in slug
        assert "\\" not in slug

    def test_traversal_in_gene_neutralized(self) -> None:
        """Path traversal in query_gene is neutralized."""
        gene = "../../../tmp/evil"
        slug = sanitize_slug(gene)
        assert "/" not in slug

    def test_normal_accession_and_gene_produce_safe_stem(self) -> None:
        """Normal accession + gene produce a valid filename stem."""
        stem = f"ORTHOLOGS-{sanitize_slug('P04637')}-{sanitize_slug('TP53')}"
        assert stem == "ORTHOLOGS-P04637-TP53"
        assert "/" not in stem


# ---------------------------------------------------------------------------
# Issue #273 — litref.py: citation as path and slug
# ---------------------------------------------------------------------------


class TestLitrefCitationSanitization:
    """Verify that citations are sanitized before use in path construction."""

    def test_traversal_in_citation_path_confined(self, tmp_path: Path) -> None:
        """A .meta.json citation with traversal is rejected by confine_path."""
        malicious = Path("../../etc/passwd.meta.json")
        result = confine_path(tmp_path, malicious)
        assert result is None

    def test_traversal_in_slug_neutralized(self) -> None:
        """A DOI-like citation with path chars is sanitized in _slug output."""
        # Simulate _slug("doi", "10.1056/../../etc/passwd")
        raw_slug = "doi-10.1056/../../etc/passwd"
        slug = sanitize_slug(raw_slug)
        assert "/" not in slug
        assert "\\" not in slug

    def test_normal_doi_slug_preserved(self) -> None:
        """A normal DOI slug passes through sanitize_slug cleanly."""
        # _slug would produce something like "doi-10.1056-nejmoa1505270"
        raw = "doi-10.1056-nejmoa1505270"
        slug = sanitize_slug(raw)
        assert slug == "doi-10.1056-nejmoa1505270"

    def test_normal_nct_slug_preserved(self) -> None:
        """A normal NCT slug passes through sanitize_slug cleanly."""
        raw = "nct-NCT01942135"
        slug = sanitize_slug(raw)
        assert slug == "nct-NCT01942135"
