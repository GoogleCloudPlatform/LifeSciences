---
name: database-queries
description: Queries pre-computed protein structures, confidence summaries, and missense variant annotations from the public EBI AlphaFold Database by UniProt accession. Use before running expensive de novo predictions on known proteins or when the user asks about existing structures or mutations.
metadata:
  adk_additional_tools:
    - query_alphafold_db_prediction
    - query_alphafold_db_summary
    - query_alphafold_db_annotations
---

# Database Queries

- **Check Existing Structures (`query_alphafold_db_summary`)**: Query by UniProt ID (e.g., `P69905`) to check whether a pre-computed structure already exists in EBI AlphaFold DB before submitting an expensive de novo prediction job.
- **Fetch Full Prediction Metadata (`query_alphafold_db_prediction`)**: Retrieve full structure URLs (PDB/mmCIF/PAE) and confidence metadata for a UniProt accession.
- **Query Variant Annotations (`query_alphafold_db_annotations`)**: Inspect missense mutation and functional residue annotations mapped onto the AlphaFold DB structure.
