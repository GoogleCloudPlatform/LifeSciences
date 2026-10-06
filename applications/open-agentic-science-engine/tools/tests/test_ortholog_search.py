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

"""Tests for ``dde homology orthologs`` — ortholog/paralog search.

Covers:
1. UniProt ortholog query construction
2. Gene-to-accession resolution (mock)
3. FASTA output format
4. Organism filter
5. UniProt accession input path
6. Relay code registration
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

# Patch optional dependencies before importing the module under test.
_mock_requests = MagicMock()
_mock_yaml = MagicMock()
_module_patches = patch.dict(
    "sys.modules",
    {"requests": _mock_requests, "yaml": _mock_yaml},
)
_module_patches.start()

from dde.commands.homology import (  # noqa: E402
    _UNIPROT_RE,
    _format_fasta,
    _resolve_gene_to_accession,
    _search_orthologs,
)
from dde.core.errors import UsageError  # noqa: E402

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

SAMPLE_ORTHOLOGS = [
    {
        "accession": "P04637",
        "gene_names": ["TP53"],
        "organism": "Homo sapiens",
        "sequence": "MEEPQSDPSVEPPLSQETFSD",
        "length": 21,
    },
    {
        "accession": "Q00366",
        "gene_names": ["TP53", "Trp53"],
        "organism": "Mus musculus",
        "sequence": "MTAMEESQSDISLELPLSQET",
        "length": 21,
    },
    {
        "accession": "O09185",
        "gene_names": ["TP53"],
        "organism": "Rattus norvegicus",
        "sequence": "MEDSQSDMSIELPLSQETFSD",
        "length": 21,
    },
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestUniProtAccessionRegex(unittest.TestCase):
    """_UNIPROT_RE correctly identifies accession vs gene symbol."""

    def test_standard_accession(self):
        self.assertIsNotNone(_UNIPROT_RE.match("P04637"))

    def test_ten_char_accession(self):
        self.assertIsNotNone(_UNIPROT_RE.match("A0A1B0GX81"))

    def test_gene_symbol_not_matched(self):
        self.assertIsNone(_UNIPROT_RE.match("TP53"))

    def test_lowercase_not_matched(self):
        self.assertIsNone(_UNIPROT_RE.match("p04637"))


class TestResolveGeneToAccession(unittest.TestCase):
    """_resolve_gene_to_accession resolves a gene symbol via UniProt search."""

    @patch("dde.commands.homology.http.get_json")
    def test_resolves_gene(self, mock_get_json):
        mock_get_json.return_value = {"results": [{"primaryAccession": "P04637"}]}
        result = _resolve_gene_to_accession("TP53")
        self.assertEqual(result, "P04637")

        # Verify the URL includes gene_exact and reviewed filter
        call_url = mock_get_json.call_args[0][0]
        self.assertIn("gene_exact:TP53", call_url)
        self.assertIn("reviewed:true", call_url)

    @patch("dde.commands.homology.http.get_json")
    def test_raises_on_no_results(self, mock_get_json):
        mock_get_json.return_value = {"results": []}
        with self.assertRaises(UsageError) as ctx:
            _resolve_gene_to_accession("FAKEGENE123")
        self.assertIn("FAKEGENE123", str(ctx.exception))


class TestSearchOrthologs(unittest.TestCase):
    """_search_orthologs queries UniProt for orthologous sequences."""

    @patch("dde.commands.homology.http.get_json")
    def test_basic_search(self, mock_get_json):
        mock_get_json.return_value = {
            "results": [
                {
                    "primaryAccession": "P04637",
                    "genes": [{"geneName": {"value": "TP53"}}],
                    "organism": {"scientificName": "Homo sapiens"},
                    "sequence": {"value": "MEEPQSD", "length": 7},
                },
                {
                    "primaryAccession": "Q00366",
                    "genes": [{"geneName": {"value": "Trp53"}}],
                    "organism": {"scientificName": "Mus musculus"},
                    "sequence": {"value": "MTAMEES", "length": 7},
                },
            ]
        }

        gene, orthologs = _search_orthologs(
            "TP53", max_orthologs=20, organism_filter=None
        )
        self.assertEqual(gene, "TP53")
        self.assertEqual(len(orthologs), 2)
        self.assertEqual(orthologs[0]["accession"], "P04637")
        self.assertEqual(orthologs[1]["organism"], "Mus musculus")

    @patch("dde.commands.homology.http.get_json")
    def test_organism_filter_in_query(self, mock_get_json):
        mock_get_json.return_value = {"results": []}
        _search_orthologs("TP53", max_orthologs=10, organism_filter="Mammalia")

        call_url = mock_get_json.call_args[0][0]
        self.assertIn("organism_name:Mammalia", call_url)

    @patch("dde.commands.homology.http.get_json")
    def test_max_orthologs_in_query(self, mock_get_json):
        mock_get_json.return_value = {"results": []}
        _search_orthologs("TP53", max_orthologs=15, organism_filter=None)

        call_url = mock_get_json.call_args[0][0]
        self.assertIn("size=15", call_url)

    @patch("dde.commands.homology.http.get_json")
    def test_empty_results(self, mock_get_json):
        mock_get_json.return_value = {"results": []}
        _gene, orthologs = _search_orthologs(
            "UNKNOWN", max_orthologs=20, organism_filter=None
        )
        self.assertEqual(orthologs, [])

    @patch("dde.commands.homology.http.get_json")
    def test_missing_fields_handled(self, mock_get_json):
        """Entries with missing optional fields don't crash."""
        mock_get_json.return_value = {
            "results": [
                {
                    "primaryAccession": "X12345",
                    "genes": [],
                    "organism": {},
                    "sequence": {},
                }
            ]
        }
        _gene, orthologs = _search_orthologs(
            "GENE1", max_orthologs=20, organism_filter=None
        )
        self.assertEqual(len(orthologs), 1)
        self.assertEqual(orthologs[0]["accession"], "X12345")
        self.assertEqual(orthologs[0]["gene_names"], [])
        self.assertEqual(orthologs[0]["organism"], "")


class TestFormatFasta(unittest.TestCase):
    """_format_fasta produces correct FASTA text."""

    def test_basic_format(self):
        fasta = _format_fasta(SAMPLE_ORTHOLOGS)
        lines = fasta.strip().split("\n")

        # 3 entries, each with header + 1 sequence line = 6 lines
        self.assertEqual(len(lines), 6)

        # First header
        self.assertTrue(lines[0].startswith(">P04637"))
        self.assertIn("TP53", lines[0])
        self.assertIn("OS=Homo sapiens", lines[0])

        # First sequence
        self.assertEqual(lines[1], "MEEPQSDPSVEPPLSQETFSD")

    def test_sequence_wrapping(self):
        """Long sequences are wrapped at 70 characters."""
        long_orthologs = [
            {
                "accession": "P00000",
                "gene_names": ["TEST"],
                "organism": "Test organism",
                "sequence": "A" * 150,
                "length": 150,
            }
        ]
        fasta = _format_fasta(long_orthologs)
        lines = fasta.strip().split("\n")

        # Header + 3 sequence lines (70 + 70 + 10)
        self.assertEqual(len(lines), 4)
        self.assertEqual(len(lines[1]), 70)
        self.assertEqual(len(lines[2]), 70)
        self.assertEqual(len(lines[3]), 10)

    def test_empty_list(self):
        fasta = _format_fasta([])
        self.assertEqual(fasta, "")

    def test_multiple_gene_names_uses_first(self):
        """Only the first gene name appears in the FASTA header."""
        fasta = _format_fasta(SAMPLE_ORTHOLOGS)
        lines = fasta.strip().split("\n")
        # Second entry has gene_names ["TP53", "Trp53"] — header uses first
        self.assertIn(">Q00366 TP53", lines[2])

    def test_organism_filter_in_fasta(self):
        """Organism name appears in the FASTA header."""
        fasta = _format_fasta(SAMPLE_ORTHOLOGS)
        self.assertIn("OS=Mus musculus", fasta)
        self.assertIn("OS=Rattus norvegicus", fasta)


if __name__ == "__main__":
    unittest.main()
