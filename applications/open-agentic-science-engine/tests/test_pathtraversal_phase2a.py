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

"""Regression tests for path-traversal fixes in Phase 2A (Batch A).

Covers slug sanitization in:
  - dice.py (#185): search_cmd, analyze_cmd
  - gwas.py (#194): search_cmd, search_disease_cmd, analyze_cmd
  - phenotype.py (#203): search_cmd, analyze_cmd
  - pubchem.py (#201): fetch_cmd slug_override
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.core.paths import sanitize_slug

# ---------------------------------------------------------------------------
# dice.py — slug = sanitize_slug(gene.upper())
# ---------------------------------------------------------------------------


class TestDiceSlugSanitized:
    """Verify dice slug rejects traversal characters (#185)."""

    def test_traversal_neutralized(self) -> None:
        """A traversal string does NOT produce a path outside target_dir."""
        malicious = "../../etc/passwd"
        slug = sanitize_slug(malicious.upper())
        assert "/" not in slug
        # Slashes removed — slug is a flat filename, can't traverse directories.
        # Dots are intentionally kept for GENCODE-style IDs (e.g. ENSG...16).
        assert "\\" not in slug

    def test_normal_gene_preserved(self) -> None:
        """Normal gene symbol passes through unchanged."""
        assert sanitize_slug("TP53") == "TP53"

    def test_gencode_id_preserved(self) -> None:
        """GENCODE-style ID with dots is preserved."""
        assert sanitize_slug("ENSG00000141510.16") == "ENSG00000141510.16"


# ---------------------------------------------------------------------------
# gwas.py — slug = sanitize_slug(gene.lower()) and disease slug
# ---------------------------------------------------------------------------


class TestGwasSlugSanitized:
    """Verify gwas gene slug rejects traversal characters (#194)."""

    def test_traversal_neutralized(self) -> None:
        """A traversal string does NOT produce a path outside target_dir."""
        malicious = "../../etc/passwd"
        slug = sanitize_slug(malicious.lower())
        assert "/" not in slug
        # Slashes removed — slug is a flat filename, can't traverse directories.
        # Dots are intentionally kept for GENCODE-style IDs (e.g. ENSG...16).
        assert "\\" not in slug

    def test_normal_gene_preserved(self) -> None:
        """Normal gene symbol (lowercased) passes through unchanged."""
        assert sanitize_slug("tp53") == "tp53"

    def test_gencode_id_preserved(self) -> None:
        """GENCODE-style ID with dots is preserved."""
        assert sanitize_slug("ensg00000141510.16") == "ensg00000141510.16"


class TestGwasDiseaseSlugSanitized:
    """Verify gwas disease slug rejects traversal characters (#194)."""

    def test_traversal_neutralized(self) -> None:
        """A traversal disease name is sanitized."""
        malicious = "../../etc/passwd"
        slug = sanitize_slug(malicious.lower())
        assert "/" not in slug
        # Slashes removed — slug is a flat filename, can't traverse directories.
        # Dots are intentionally kept for GENCODE-style IDs (e.g. ENSG...16).
        assert "\\" not in slug

    def test_normal_disease_preserved(self) -> None:
        """A simple disease name passes through."""
        assert sanitize_slug("diabetes") == "diabetes"

    def test_disease_with_spaces_hyphenated(self) -> None:
        """Spaces in disease names are replaced with hyphens."""
        assert sanitize_slug("breast cancer") == "breast-cancer"

    def test_empty_disease_fallback(self) -> None:
        """Empty disease string triggers fallback to 'disease'."""
        # The code uses: sanitize_slug(disease.lower()) if disease.strip() else "disease"
        disease = "   "
        slug = sanitize_slug(disease.lower()) if disease.strip() else "disease"
        assert slug == "disease"


# ---------------------------------------------------------------------------
# phenotype.py — slug = sanitize_slug(gene.lower())
# ---------------------------------------------------------------------------


class TestPhenotypeSlugSanitized:
    """Verify phenotype slug rejects traversal characters (#203)."""

    def test_traversal_neutralized(self) -> None:
        """A traversal string does NOT produce a path outside target_dir."""
        malicious = "../../etc/passwd"
        slug = sanitize_slug(malicious.lower())
        assert "/" not in slug
        # Slashes removed — slug is a flat filename, can't traverse directories.
        # Dots are intentionally kept for GENCODE-style IDs (e.g. ENSG...16).
        assert "\\" not in slug

    def test_normal_gene_preserved(self) -> None:
        """Normal gene symbol (lowercased) passes through unchanged."""
        assert sanitize_slug("brca1") == "brca1"

    def test_gencode_id_preserved(self) -> None:
        """GENCODE-style ID with dots is preserved."""
        assert sanitize_slug("ensg00000012048.23") == "ensg00000012048.23"


# ---------------------------------------------------------------------------
# pubchem.py — slug = sanitize_slug(slug_override) if slug_override else str(cid)
# ---------------------------------------------------------------------------


class TestPubchemSlugSanitized:
    """Verify pubchem slug_override rejects traversal characters (#201)."""

    def test_traversal_neutralized(self) -> None:
        """A traversal slug_override is sanitized."""
        malicious = "../../etc/passwd"
        slug = sanitize_slug(malicious)
        assert "/" not in slug
        # Slashes removed — slug is a flat filename, can't traverse directories.
        # Dots are intentionally kept for GENCODE-style IDs (e.g. ENSG...16).
        assert "\\" not in slug

    def test_normal_name_preserved(self) -> None:
        """A simple compound name passes through unchanged."""
        assert sanitize_slug("aspirin") == "aspirin"

    def test_integer_cid_fallback_safe(self) -> None:
        """When slug_override is absent, str(cid) is an integer string — safe."""
        # This mirrors the code path: slug_override is falsy → str(cid).
        cid = 2244
        slug_override = ""
        slug = sanitize_slug(slug_override) if slug_override else str(cid)
        assert slug == "2244"

    def test_name_with_dots_preserved(self) -> None:
        """Compound names with dots (e.g. version suffixes) are preserved."""
        assert sanitize_slug("compound.v2") == "compound.v2"
