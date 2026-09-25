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

"""Tests for ClinVar variant type stratification (issue #97).

Asserts:
1. _classify_variant_type correctly identifies gene-specific vs locus-overlapping.
2. _analyze_clinvar stratifies pathogenic counts.
3. Verdict uses gene-specific count as primary safety signal.
4. clinvar.cnv_not_gene_specific relay fires when CNVs dominate.
5. Relay does NOT fire when gene-specific variants dominate.
6. Relay code is registered in provenance.RELAY_CODES.
7. Backward compatibility: classification works without obj_type.
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

from dde.commands.gwas import _analyze_clinvar, _classify_variant_type  # noqa: E402
from dde.core.provenance import RELAY_CODES  # noqa: E402


def _make_variant(
    classification: str = "Pathogenic",
    obj_type: str = "",
    variant_title: str = "",
    review_status: str = "criteria provided, single submitter",
    variant_id: str = "12345",
    disease_name: str = "Some condition",
) -> dict:
    """Build a minimal ClinVar association record for testing."""
    return {
        "source_db": "clinvar",
        "variant_id": variant_id,
        "variant_title": variant_title,
        "obj_type": obj_type,
        "disease_name": disease_name,
        "classification": classification,
        "review_status": review_status,
        "trait_xrefs": [],
    }


class TestClassifyVariantType(unittest.TestCase):
    """_classify_variant_type identifies gene-specific vs locus-overlapping."""

    # --- obj_type-based classification ---

    def test_snv_is_gene_specific(self):
        v = _make_variant(obj_type="single nucleotide variant")
        self.assertEqual(_classify_variant_type(v), "gene_specific")

    def test_indel_is_gene_specific(self):
        v = _make_variant(obj_type="Indel")
        self.assertEqual(_classify_variant_type(v), "gene_specific")

    def test_deletion_is_locus_overlapping(self):
        v = _make_variant(obj_type="Deletion")
        self.assertEqual(_classify_variant_type(v), "locus_overlapping")

    def test_duplication_is_locus_overlapping(self):
        v = _make_variant(obj_type="Duplication")
        self.assertEqual(_classify_variant_type(v), "locus_overlapping")

    def test_copy_number_gain_is_locus_overlapping(self):
        v = _make_variant(obj_type="copy number gain")
        self.assertEqual(_classify_variant_type(v), "locus_overlapping")

    def test_copy_number_loss_is_locus_overlapping(self):
        v = _make_variant(obj_type="copy number loss")
        self.assertEqual(_classify_variant_type(v), "locus_overlapping")

    def test_structural_variant_is_locus_overlapping(self):
        v = _make_variant(obj_type="structural variant")
        self.assertEqual(_classify_variant_type(v), "locus_overlapping")

    def test_variation_is_gene_specific(self):
        """Generic 'Variation' type without locus-overlapping keywords."""
        v = _make_variant(obj_type="Variation")
        self.assertEqual(_classify_variant_type(v), "gene_specific")

    def test_empty_obj_type_defaults_gene_specific(self):
        v = _make_variant(obj_type="", variant_title="NM_054030.3(MRGPRX2):c.839G>A")
        self.assertEqual(_classify_variant_type(v), "gene_specific")

    # --- variant_title fallback (no obj_type) ---

    def test_title_with_transcript_is_gene_specific(self):
        """Coding variant with transcript notation — gene-specific."""
        v = _make_variant(
            obj_type="",
            variant_title="NM_054030.3(MRGPRX2):c.839G>A (p.Arg280Gln)",
        )
        self.assertEqual(_classify_variant_type(v), "gene_specific")

    def test_title_with_chromosomal_range_is_locus_overlapping(self):
        """Chromosomal-range title — locus-overlapping."""
        v = _make_variant(
            obj_type="",
            variant_title="GRCh38/hg38 11q12.1-12.2(chr11:57542043-59876252)x1",
        )
        self.assertEqual(_classify_variant_type(v), "locus_overlapping")

    def test_title_with_xp_band_is_locus_overlapping(self):
        """X-chromosome cytogenetic band — locus-overlapping."""
        v = _make_variant(
            obj_type="",
            variant_title="GRCh38/hg38 Xp22.31-22.2(chrX:1234-5678)",
        )
        self.assertEqual(_classify_variant_type(v), "locus_overlapping")

    def test_title_with_chr_range_is_locus_overlapping(self):
        v = _make_variant(
            obj_type="",
            variant_title="chr2:148000000-149000000",
        )
        self.assertEqual(_classify_variant_type(v), "locus_overlapping")


class TestAnalyzeClinvarStratification(unittest.TestCase):
    """_analyze_clinvar stratifies pathogenic variants by type."""

    def _collect_relays(self):
        """Return (relay_list, add_relay_callback)."""
        relays: list[dict] = []

        def add_relay(code, message):
            if not any(r["code"] == code for r in relays):
                relays.append({"code": code, "message": message})

        return relays, add_relay

    def test_all_gene_specific(self):
        """All pathogenic variants are gene-specific — verdict is pathogenic_variants_found."""
        variants = [
            _make_variant(
                classification="Pathogenic",
                obj_type="single nucleotide variant",
                variant_id=str(i),
            )
            for i in range(3)
        ]
        relays, add_relay = self._collect_relays()
        significant, metrics, assessment = _analyze_clinvar(
            "BRCA1", variants, add_relay
        )

        self.assertEqual(len(significant), 3)
        self.assertEqual(metrics["pathogenic_total"], 3)
        self.assertEqual(metrics["pathogenic_gene_specific"], 3)
        self.assertEqual(metrics["pathogenic_locus_overlapping"], 0)
        self.assertEqual(assessment["verdict"], "pathogenic_variants_found")
        self.assertEqual(assessment["pathogenic_gene_specific"], 3)
        self.assertEqual(assessment["pathogenic_locus_overlapping"], 0)

        # CNV relay should NOT fire.
        relay_codes = [r["code"] for r in relays]
        self.assertNotIn("clinvar.cnv_not_gene_specific", relay_codes)

    def test_all_locus_overlapping(self):
        """All pathogenic variants are CNVs — verdict is no_pathogenic_variants.

        This is the MRGPRX2 scenario from issue #97. 14 pathogenic variants,
        all CNVs, zero gene-specific. The old code would say
        'pathogenic_variants_found'; the new code says 'no_pathogenic_variants'
        because none are gene-specific.
        """
        variants = [
            _make_variant(
                classification="Pathogenic",
                obj_type="Deletion",
                variant_id=str(i),
                variant_title=f"GRCh38/hg38 11q12.1(chr11:{i}00000-{i}99999)x1",
            )
            for i in range(14)
        ]
        relays, add_relay = self._collect_relays()
        significant, metrics, assessment = _analyze_clinvar(
            "MRGPRX2", variants, add_relay
        )

        self.assertEqual(len(significant), 14)
        self.assertEqual(metrics["pathogenic_total"], 14)
        self.assertEqual(metrics["pathogenic_gene_specific"], 0)
        self.assertEqual(metrics["pathogenic_locus_overlapping"], 14)
        # Verdict is no_pathogenic_variants because gene-specific count is 0.
        self.assertEqual(assessment["verdict"], "no_pathogenic_variants")

        # CNV relay MUST fire.
        relay_codes = [r["code"] for r in relays]
        self.assertIn("clinvar.cnv_not_gene_specific", relay_codes)

    def test_mixed_cnv_dominated(self):
        """Mixed variants where CNVs dominate — CNV relay fires."""
        variants = [
            _make_variant(
                classification="Pathogenic",
                obj_type="single nucleotide variant",
                variant_id="1",
            ),
            _make_variant(
                classification="Pathogenic",
                obj_type="Deletion",
                variant_id="2",
            ),
            _make_variant(
                classification="Pathogenic",
                obj_type="Duplication",
                variant_id="3",
            ),
            _make_variant(
                classification="Pathogenic",
                obj_type="copy number loss",
                variant_id="4",
            ),
        ]
        relays, add_relay = self._collect_relays()
        _significant, metrics, assessment = _analyze_clinvar(
            "GENE1", variants, add_relay
        )

        self.assertEqual(metrics["pathogenic_total"], 4)
        self.assertEqual(metrics["pathogenic_gene_specific"], 1)
        self.assertEqual(metrics["pathogenic_locus_overlapping"], 3)
        # Verdict is pathogenic_variants_found because gene-specific count > 0.
        self.assertEqual(assessment["verdict"], "pathogenic_variants_found")

        # CNV relay fires because locus_overlapping > gene_specific.
        relay_codes = [r["code"] for r in relays]
        self.assertIn("clinvar.cnv_not_gene_specific", relay_codes)

    def test_mixed_gene_specific_dominated(self):
        """Mixed variants where gene-specific dominates — CNV relay does NOT fire."""
        variants = [
            _make_variant(
                classification="Pathogenic",
                obj_type="single nucleotide variant",
                variant_id="1",
            ),
            _make_variant(
                classification="Pathogenic",
                obj_type="single nucleotide variant",
                variant_id="2",
            ),
            _make_variant(
                classification="Pathogenic",
                obj_type="single nucleotide variant",
                variant_id="3",
            ),
            _make_variant(
                classification="Pathogenic",
                obj_type="Deletion",
                variant_id="4",
            ),
        ]
        relays, add_relay = self._collect_relays()
        _significant, metrics, _assessment = _analyze_clinvar(
            "GENE2", variants, add_relay
        )

        self.assertEqual(metrics["pathogenic_gene_specific"], 3)
        self.assertEqual(metrics["pathogenic_locus_overlapping"], 1)

        # CNV relay does NOT fire — gene-specific dominates.
        relay_codes = [r["code"] for r in relays]
        self.assertNotIn("clinvar.cnv_not_gene_specific", relay_codes)

    def test_no_pathogenic_variants(self):
        """No pathogenic variants at all."""
        variants = [
            _make_variant(
                classification="Benign",
                obj_type="single nucleotide variant",
                variant_id="1",
            ),
            _make_variant(
                classification="Uncertain significance",
                obj_type="Deletion",
                variant_id="2",
            ),
        ]
        relays, add_relay = self._collect_relays()
        significant, metrics, assessment = _analyze_clinvar(
            "GENE3", variants, add_relay
        )

        self.assertEqual(len(significant), 0)
        self.assertEqual(metrics["pathogenic_total"], 0)
        self.assertEqual(metrics["pathogenic_gene_specific"], 0)
        self.assertEqual(metrics["pathogenic_locus_overlapping"], 0)
        self.assertEqual(assessment["verdict"], "no_pathogenic_variants")

        # No relays should fire about CNVs or pathogenicity.
        relay_codes = [r["code"] for r in relays]
        self.assertNotIn("clinvar.cnv_not_gene_specific", relay_codes)
        self.assertNotIn("clinvar.classification_is_curated", relay_codes)

    def test_backward_compat_no_obj_type(self):
        """Stratification works when obj_type is absent (old artifacts)."""
        variants = [
            _make_variant(
                classification="Pathogenic",
                obj_type="",
                variant_title="NM_054030.3(GENE):c.839G>A (p.Arg280Gln)",
                variant_id="1",
            ),
            _make_variant(
                classification="Pathogenic",
                obj_type="",
                variant_title="GRCh38/hg38 11q12.1(chr11:57542043-59876252)x1",
                variant_id="2",
            ),
        ]
        _relays, add_relay = self._collect_relays()
        _significant, metrics, _assessment = _analyze_clinvar(
            "GENE", variants, add_relay
        )

        self.assertEqual(metrics["pathogenic_gene_specific"], 1)
        self.assertEqual(metrics["pathogenic_locus_overlapping"], 1)


class TestRelayCodeRegistration(unittest.TestCase):
    """clinvar.cnv_not_gene_specific relay code is registered."""

    def test_cnv_relay_registered(self):
        self.assertIn("clinvar.cnv_not_gene_specific", RELAY_CODES)

    def test_cnv_relay_text_mentions_cnv(self):
        text = RELAY_CODES["clinvar.cnv_not_gene_specific"]
        self.assertIn("CNV", text)
        self.assertIn("gene-specific", text)


if __name__ == "__main__":
    unittest.main()
