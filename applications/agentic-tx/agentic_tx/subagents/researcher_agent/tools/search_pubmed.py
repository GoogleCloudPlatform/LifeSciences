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

"""PubMed search tool for Researcher Agent.

Single Responsibility: Provide PubMed search functionality for the agent.
"""

from Bio import Entrez, Medline


class SearchPubmedTool:
    """PubMed search tool wrapper."""

    def __init__(self, max_results: int = 3):
        """Initialize with configuration.

        Args:
            max_results: Maximum number of PubMed results to return (default: 3)
        """
        self.max_results = max_results

    def search_pubmed(self, query: str, max_results: int | None = None) -> str:
        """Search PubMed for biomedical literature based on the provided query.

        This tool searches the PubMed database for scientific articles related to your query.
        It returns article titles, abstracts, authors, and publication information.

        Args:
            query: The search query to use on PubMed. Use specific scientific terminology
                   for best results (e.g., gene names, compound names, diseases).
            max_results: Maximum number of results to return. If not specified, uses
                        the default configured for this agent.

        Returns:
            A formatted report containing:
            - Article metadata (PMID, title, authors, journal, date)
            - Abstract excerpts
            - Error message if search fails

        Examples:
            >>> search_pubmed("aspirin cardiovascular protection", max_results=3)
            >>> search_pubmed("TP53 tumor suppressor mutations")
            >>> search_pubmed("metformin kidney development toxicity")
        """
        # Use provided max_results or fall back to instance default
        max_res = max_results if max_results is not None else self.max_results

        try:
            # Set email for NCBI's tracking purposes
            Entrez.email = "txgemma@example.com"

            # Search PubMed
            handle = Entrez.esearch(db="pubmed", sort="relevance", term=query, retmax=max_res)
            record = Entrez.read(handle)
            pmids = record.get("IdList", [])
            handle.close()

            if not pmids:
                return f"No PubMed articles found for '{query}'. Please try a different search term."

            # Fetch article details
            fetch_handle = Entrez.efetch(db="pubmed", id=",".join(pmids), rettype="medline", retmode="text")
            records = list(Medline.parse(fetch_handle))
            fetch_handle.close()

            # Format results
            articles = []
            for rec in records:
                article = {
                    "pmid": rec.get("PMID", "N/A"),
                    "title": rec.get("TI", "No title available"),
                    "abstract": rec.get("AB", "No abstract available"),
                    "journal": rec.get("JT", "No journal info"),
                    "pub_date": rec.get("DP", "No date info"),
                    "authors": rec.get("AU", []),
                }
                articles.append(article)

            # Format readable summary
            summary = []
            for i, article in enumerate(articles, 1):
                authors_str = ", ".join(article["authors"][:3])
                if len(article["authors"]) > 3:
                    authors_str += " et al."

                summary.append(
                    f"Article #{i}:\n"
                    f"PMID: {article['pmid']}\n"
                    f"Title: {article['title']}\n"
                    f"Authors: {authors_str}\n"
                    f"Journal: {article['journal']}\n"
                    f"Publication Date: {article['pub_date']}\n"
                    f"Abstract: {article['abstract'][:300]}...\n"
                )

            return f"Found {len(articles)} articles for '{query}':\n\n" + "\n\n".join(summary)

        except Exception as e:
            return f"Error searching PubMed: {e!s}"
