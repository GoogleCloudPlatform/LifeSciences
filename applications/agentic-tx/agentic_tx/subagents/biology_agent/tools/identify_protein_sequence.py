# Copyright 2025 Google LLC
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

"""Protein sequence identification tool for Biology Agent.

Single Responsibility: Identify unknown protein sequences using BLASTP.
"""

from Bio import Entrez
from Bio.Blast import NCBIWWW, NCBIXML


class IdentifyProteinSequenceTool:
    """Protein sequence identification tool using NCBI BLASTP."""

    def __init__(self, entrez_email: str = "txgemma@example.com"):
        """Initialize with NCBI Entrez email.

        Args:
            entrez_email: Email address for NCBI API (required by NCBI)
        """
        self.entrez_email = entrez_email
        Entrez.email = entrez_email

    def identify_protein_sequence(self, sequence: str, database: str = "nr", max_hits: int = 5) -> str:
        """Identify an unknown protein sequence using BLASTP.

        This tool performs a BLAST (Basic Local Alignment Search Tool) search to identify
        similar protein sequences in NCBI databases. It's useful for:
        - Identifying unknown protein sequences
        - Finding homologous proteins across species
        - Determining protein function from sequence

        Args:
            sequence: Amino acid sequence in single-letter code (e.g., "MTEYKLVVVGAGGVGKSALTIQLI...").
                     Minimum recommended length: 30 amino acids.
            database: NCBI BLAST database to search. Options:
                     - "nr" (default): Non-redundant protein sequences
                     - "refseq_protein": Reference proteins (curated, higher quality)
            max_hits: Maximum number of BLAST hits to return (default: 5, max: 20).

        Returns:
            A formatted report containing:
            - Top BLAST hits with protein names and organisms
            - E-values (statistical significance)
            - Identity percentages
            - Accession numbers
            - Error message if BLAST fails

        Examples:
            >>> identify_protein_sequence(
            ...     "MTEYKLVVVGAGGVGKSALTIQLIQNHFVDEYDPTIEDSYRKQVVIDGETCLLDILDTAGQEEYSAMRDQYMRTGEGFLCVFAINNTKSFEDIHQYREQIKRVKDSDDVPMVLVGNKCDLAARTVESRQAQDLARSYGIPYIETSAKTRQGVEDAFYTLVREIRQHKLRKLNPPDESGPGCMSCKCVLS"
            ... )
            >>> identify_protein_sequence(
            ...     "MKTIIALSYIFCLVFA", database="refseq_protein", max_hits=3
            ... )

        Note:
            - BLAST searches can take 30-60 seconds to complete
            - Very short sequences (<30 aa) may not return meaningful results
            - E-value < 1e-10 indicates high confidence match
        """
        try:
            # Validate sequence
            sequence = sequence.strip().upper()
            valid_aa = set("ACDEFGHIKLMNPQRSTVWY")

            # Remove whitespace and newlines
            sequence = "".join(sequence.split())

            # Check if sequence contains valid amino acids
            invalid_chars = set(sequence) - valid_aa
            if invalid_chars:
                return (
                    f"ERROR: Invalid amino acid sequence. Found invalid characters: {invalid_chars}\n\n"
                    f"Please provide a sequence using single-letter amino acid codes (A-Z, excluding B, J, O, U, X, Z)."
                )

            if len(sequence) < 20:
                return (
                    f"ERROR: Sequence too short ({len(sequence)} amino acids).\n\n"
                    f"BLAST works best with sequences of at least 30 amino acids. "
                    f"Short sequences may not return meaningful results."
                )

            # Validate database
            valid_databases = {"nr", "refseq_protein"}
            if database not in valid_databases:
                return f"ERROR: Invalid database '{database}'. Must be one of: {', '.join(valid_databases)}"

            # Validate max_hits
            if max_hits < 1 or max_hits > 20:
                return "ERROR: max_hits must be between 1 and 20"

            # Run BLASTP search
            print(f"Running BLASTP search for sequence ({len(sequence)} amino acids)...")
            print("This may take 30-60 seconds...")

            result_handle = NCBIWWW.qblast(
                program="blastp", database=database, sequence=sequence, hitlist_size=max_hits
            )

            # Parse BLAST results
            blast_records = NCBIXML.parse(result_handle)
            blast_record = next(blast_records)
            result_handle.close()

            if not blast_record.alignments:
                return (
                    f"No BLAST hits found for the given sequence in database '{database}'.\n\n"
                    f"Possible reasons:\n"
                    f"- Sequence may be synthetic or engineered\n"
                    f"- Sequence may contain errors\n"
                    f"- Sequence may be from an organism not well-represented in the database"
                )

            # Build formatted report
            report = f"""BLASTP Results for Protein Sequence:

**Query Information**:
- Sequence Length: {len(sequence)} amino acids
- Database: {database}
- Number of Hits: {len(blast_record.alignments)}

**Top BLAST Hits**:

"""

            for i, alignment in enumerate(blast_record.alignments[:max_hits], 1):
                # Get the best HSP (high-scoring pair) for this alignment
                hsp = alignment.hsps[0]

                # Extract information
                accession = alignment.accession
                title = alignment.title
                e_value = hsp.expect
                identity_count = hsp.identities
                alignment_length = hsp.align_length
                identity_percent = (identity_count / alignment_length) * 100

                # Interpret E-value
                if e_value < 1e-50:
                    confidence = "VERY HIGH"
                elif e_value < 1e-10:
                    confidence = "HIGH"
                elif e_value < 1e-5:
                    confidence = "MODERATE"
                else:
                    confidence = "LOW"

                report += f"""Hit #{i}:
- Accession: {accession}
- Description: {title}
- E-value: {e_value:.2e} (Confidence: {confidence})
- Identity: {identity_count}/{alignment_length} ({identity_percent:.1f}%)
- Alignment Length: {alignment_length} amino acids

"""

            report += f"""**Interpretation**:
- E-value: Lower is better. E < 1e-10 indicates high confidence homology.
- Identity %: Higher is better. >90% = very similar, 30-90% = homologous, <30% = distant relationship.

**Database**: NCBI BLAST ({database})
"""

            return report

        except Exception as e:
            return f"ERROR: BLAST search failed: {e!s}\n\nPlease check your sequence and try again."
