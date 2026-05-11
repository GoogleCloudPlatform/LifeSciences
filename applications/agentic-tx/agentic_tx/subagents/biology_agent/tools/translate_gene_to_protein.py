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

"""Gene to protein translation tool for Biology Agent.

Single Responsibility: Get protein sequence(s) for a given gene.
"""

from Bio import Entrez, SeqIO


class TranslateGeneToProteinTool:
    """Gene to protein translation tool using NCBI Entrez."""

    def __init__(self, entrez_email: str = "txgemma@example.com"):
        """Initialize with NCBI Entrez email.

        Args:
            entrez_email: Email address for NCBI API (required by NCBI)
        """
        self.entrez_email = entrez_email
        Entrez.email = entrez_email

    def translate_gene_to_protein(
        self, gene_name: str, organism: str = "Homo sapiens", return_full_sequence: bool = False
    ) -> str:
        """Get protein sequence(s) for a given gene.

        This tool retrieves the protein products encoded by a gene from NCBI databases.
        It can return either truncated sequences (for display) or full sequences (for analysis).

        Args:
            gene_name: The gene symbol or name (e.g., "TP53", "BRCA1", "EGFR").
            organism: The organism name (default: "Homo sapiens").
            return_full_sequence: If True, returns complete protein sequences.
                                 If False (default), returns truncated sequences for display.

        Returns:
            A formatted report containing:
            - Gene symbol and ID
            - Protein product name(s)
            - Protein accession number(s)
            - Sequence length
            - Protein sequence (full or truncated)
            - Error message if not found

        Examples:
            >>> translate_gene_to_protein("TP53")
            >>> translate_gene_to_protein("BRCA1", return_full_sequence=True)
            >>> translate_gene_to_protein("tp53", organism="Mus musculus")
        """
        try:
            # First, get the gene ID
            search_term = f"{gene_name}[Gene Name] AND {organism}[Organism]"
            search_handle = Entrez.esearch(db="gene", term=search_term, retmax=1)
            search_results = Entrez.read(search_handle)
            search_handle.close()

            if not search_results["IdList"]:
                return (
                    f"ERROR: Gene '{gene_name}' not found in NCBI Gene database for {organism}.\n\n"
                    f"Please check the gene name and organism."
                )

            gene_id = search_results["IdList"][0]

            # Search for protein sequences associated with this gene
            protein_search = f"{gene_name}[Gene Name] AND {organism}[Organism]"
            protein_handle = Entrez.esearch(db="protein", term=protein_search, retmax=5)
            protein_results = Entrez.read(protein_handle)
            protein_handle.close()

            if not protein_results["IdList"]:
                return (
                    f"No protein sequences found for gene '{gene_name}' in {organism}.\n\n"
                    f"This gene may not have protein products in the database."
                )

            # Fetch protein sequences
            protein_ids = protein_results["IdList"][:3]  # Get top 3 protein products
            fetch_handle = Entrez.efetch(db="protein", id=protein_ids, rettype="fasta", retmode="text")
            fasta_data = fetch_handle.read()
            fetch_handle.close()

            # Parse FASTA sequences
            from io import StringIO

            sequences = list(SeqIO.parse(StringIO(fasta_data), "fasta"))

            if not sequences:
                return f"ERROR: Could not parse protein sequences for gene '{gene_name}'"

            # Build formatted report
            report = f"""Protein Products for Gene '{gene_name}':

**Gene Information**:
- Gene ID: {gene_id}
- Organism: {organism}
- Number of Protein Products: {len(sequences)}

"""

            for i, seq_record in enumerate(sequences, 1):
                accession = seq_record.id.split("|")[0] if "|" in seq_record.id else seq_record.id
                description = seq_record.description
                sequence_length = len(seq_record.seq)

                # Truncate sequence unless full sequence requested
                if return_full_sequence:
                    sequence_display = str(seq_record.seq)
                else:
                    # Show first 50 and last 50 amino acids
                    if sequence_length > 100:
                        sequence_display = f"{seq_record.seq[:50]!s}...{seq_record.seq[-50:]!s}"
                    else:
                        sequence_display = str(seq_record.seq)

                report += f"""**Protein Product #{i}**:
- Accession: {accession}
- Description: {description}
- Length: {sequence_length} amino acids
- Sequence:
{sequence_display}

"""

            report += """**Database**: NCBI Protein
**Note**: Use return_full_sequence=True to get complete sequences for analysis.
"""

            return report

        except Exception as e:
            return f"ERROR: Failed to retrieve protein sequences for '{gene_name}': {e!s}"
