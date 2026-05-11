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

"""Gene description tool for Biology Agent.

Single Responsibility: Get gene description from NCBI Gene database.
"""

from Bio import Entrez


class GetGeneDescriptionTool:
    """Gene description retrieval tool using NCBI Entrez."""

    def __init__(self, entrez_email: str = "txgemma@example.com"):
        """Initialize with NCBI Entrez email.

        Args:
            entrez_email: Email address for NCBI API (required by NCBI)
        """
        self.entrez_email = entrez_email
        Entrez.email = entrez_email

    def get_gene_description(self, gene_name: str, organism: str = "Homo sapiens") -> str:
        """Get gene description from NCBI Gene database.

        This tool searches the NCBI Gene database for a specific gene and returns
        detailed information including function, synonyms, location, and annotations.

        Args:
            gene_name: The gene symbol or name to search for (e.g., "TP53", "BRCA1", "EGFR").
                      Use standard nomenclature (uppercase for human genes).
            organism: The organism name (default: "Homo sapiens"). Examples:
                     - "Homo sapiens" (human)
                     - "Mus musculus" (mouse)
                     - "Rattus norvegicus" (rat)

        Returns:
            A formatted report containing:
            - Gene symbol and name
            - Gene ID and synonyms
            - Chromosomal location
            - Gene description and function
            - Error message if gene not found

        Examples:
            >>> get_gene_description("TP53")
            >>> get_gene_description("BRCA1", organism="Homo sapiens")
            >>> get_gene_description("tp53", organism="Mus musculus")
        """
        try:
            # Search for the gene
            search_term = f"{gene_name}[Gene Name] AND {organism}[Organism]"
            search_handle = Entrez.esearch(db="gene", term=search_term, retmax=1)
            search_results = Entrez.read(search_handle)
            search_handle.close()

            if not search_results["IdList"]:
                return (
                    f"ERROR: Gene '{gene_name}' not found in NCBI Gene database for {organism}.\n\n"
                    f"Suggestions:\n"
                    f"- Check gene symbol spelling\n"
                    f"- Try common synonyms or alternative names\n"
                    f"- Verify organism name is correct\n"
                    f"- Use standard nomenclature (e.g., 'TP53' not 'tp53' for human genes)"
                )

            gene_id = search_results["IdList"][0]

            # Fetch gene details
            fetch_handle = Entrez.efetch(db="gene", id=gene_id, retmode="xml")
            records = Entrez.read(fetch_handle)
            fetch_handle.close()

            if not records:
                return f"ERROR: Could not retrieve details for Gene ID {gene_id}"

            # Parse gene information
            gene_record = records[0]

            # Extract key information
            gene_symbol = gene_record.get("Entrezgene_gene", {}).get("Gene-ref", {}).get("Gene-ref_locus", "N/A")
            gene_desc = gene_record.get("Entrezgene_gene", {}).get("Gene-ref", {}).get("Gene-ref_desc", "N/A")

            # Get synonyms
            gene_ref = gene_record.get("Entrezgene_gene", {}).get("Gene-ref", {})
            synonyms = gene_ref.get("Gene-ref_syn", [])
            synonyms_str = ", ".join(synonyms[:5]) if synonyms else "None"

            # Get chromosome location
            location_info = gene_record.get("Entrezgene_location", [])
            chromosome = "N/A"
            if location_info:
                for loc in location_info:
                    if "Maps_display-str" in loc:
                        chromosome = loc["Maps_display-str"]
                        break

            # Get summary/description
            summary = gene_record.get("Entrezgene_summary", "No summary available")

            # Build formatted report
            report = f"""Gene Information for '{gene_name}':

**Identity**:
- Gene Symbol: {gene_symbol}
- Gene ID: {gene_id}
- Organism: {organism}
- Synonyms: {synonyms_str}

**Location**:
- Chromosome: {chromosome}

**Description**:
{gene_desc}

**Summary**:
{summary}

**Database**: NCBI Gene (Gene ID: {gene_id})
**URL**: https://www.ncbi.nlm.nih.gov/gene/{gene_id}
"""
            return report

        except Exception as e:
            return f"ERROR: Failed to retrieve gene information for '{gene_name}': {e!s}"
