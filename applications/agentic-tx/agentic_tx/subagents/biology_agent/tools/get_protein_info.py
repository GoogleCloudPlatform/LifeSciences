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

"""Protein information tool for Biology Agent.

Single Responsibility: Get protein information from NCBI Protein database.
"""

from io import StringIO

from Bio import Entrez, SeqIO


class GetProteinInfoTool:
    """Protein information retrieval tool using NCBI Entrez."""

    def __init__(self, entrez_email: str = "txgemma@example.com"):
        """Initialize with NCBI Entrez email.

        Args:
            entrez_email: Email address for NCBI API (required by NCBI)
        """
        self.entrez_email = entrez_email
        Entrez.email = entrez_email

    def get_protein_info(self, protein_identifier: str, organism: str = "") -> str:
        """Get protein information from NCBI Protein database.

        This tool retrieves detailed information about a protein including its sequence,
        function, organism, and database identifiers.

        Args:
            protein_identifier: Protein name, accession number, or ID. Examples:
                               - Accession: "NP_000546" (RefSeq)
                               - Name: "p53 protein"
                               - UniProt: "P04637"
            organism: Optional organism filter to narrow search (e.g., "Homo sapiens").
                     If empty, searches across all organisms.

        Returns:
            A formatted report containing:
            - Protein accession and description
            - Organism
            - Sequence length
            - Amino acid sequence (truncated)
            - Associated gene
            - Database links
            - Error message if not found

        Examples:
            >>> get_protein_info("NP_000546")
            >>> get_protein_info("p53", organism="Homo sapiens")
            >>> get_protein_info("P04637")
        """
        try:
            # Build search query
            if organism:
                search_term = f"{protein_identifier} AND {organism}[Organism]"
            else:
                search_term = protein_identifier

            # Search protein database
            search_handle = Entrez.esearch(db="protein", term=search_term, retmax=1)
            search_results = Entrez.read(search_handle)
            search_handle.close()

            if not search_results["IdList"]:
                return (
                    f"ERROR: Protein '{protein_identifier}' not found in NCBI Protein database"
                    f"{' for ' + organism if organism else ''}.\n\n"
                    f"Suggestions:\n"
                    f"- Check protein accession or name spelling\n"
                    f"- Try alternative names or synonyms\n"
                    f"- Verify organism name if specified\n"
                    f"- Use RefSeq accessions (e.g., NP_xxxxxx) for best results"
                )

            protein_id = search_results["IdList"][0]

            # Fetch protein details in GenBank format
            gb_handle = Entrez.efetch(db="protein", id=protein_id, rettype="gb", retmode="text")
            gb_record = SeqIO.read(StringIO(gb_handle.read()), "genbank")
            gb_handle.close()

            # Extract information
            accession = gb_record.id
            description = gb_record.description
            organism_name = gb_record.annotations.get("organism", "N/A")
            sequence_length = len(gb_record.seq)
            sequence = str(gb_record.seq)

            # Extract gene name if available
            gene_name = "N/A"
            for feature in gb_record.features:
                if feature.type == "CDS" or feature.type == "gene":
                    if "gene" in feature.qualifiers:
                        gene_name = feature.qualifiers["gene"][0]
                        break

            # Get coded_by information (genomic location)
            coded_by = "N/A"
            for feature in gb_record.features:
                if feature.type == "CDS" and "coded_by" in feature.qualifiers:
                    coded_by = feature.qualifiers["coded_by"][0]
                    break

            # Truncate sequence for display
            if sequence_length > 100:
                sequence_display = f"{sequence[:50]}...{sequence[-50:]}"
                truncated_note = f"\n(Showing first 50 and last 50 of {sequence_length} amino acids)"
            else:
                sequence_display = sequence
                truncated_note = ""

            # Build formatted report
            report = f"""Protein Information for '{protein_identifier}':

**Identity**:
- Accession: {accession}
- Description: {description}
- Organism: {organism_name}
- Associated Gene: {gene_name}

**Sequence**:
- Length: {sequence_length} amino acids
- Sequence:
{sequence_display}{truncated_note}

**Genomic Information**:
- Coded By: {coded_by}

**Database**: NCBI Protein (ID: {protein_id})
**URL**: https://www.ncbi.nlm.nih.gov/protein/{accession}
"""

            return report

        except Exception as e:
            return f"ERROR: Failed to retrieve protein information for '{protein_identifier}': {e!s}"
