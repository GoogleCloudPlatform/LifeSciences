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

"""Compound lookup tool for Chemistry Agent.

Single Responsibility: Search for compounds by name and retrieve basic identifiers.
"""

import pubchempy as pcp


class LookupCompoundTool:
    """Compound lookup tool using PubChem."""

    def lookup_compound(self, compound_name: str, database: str = "pubchem") -> str:
        """Search for a compound by name and get basic identifiers.

        This tool searches chemical databases for compounds by name and returns
        basic structural identifiers including SMILES, InChI, molecular formula, etc.

        Args:
            compound_name: The name of the compound to search for (e.g., "aspirin",
                          "ibuprofen", "caffeine"). Can be common name, IUPAC name,
                          or trade name.
            database: Database to search. Currently supports "pubchem" (default).
                     ChemSpider support is planned but not yet implemented.

        Returns:
            A formatted report containing:
            - Compound name and synonyms
            - SMILES (canonical and isomeric)
            - InChI and InChIKey
            - Molecular formula and weight
            - PubChem CID
            - Error message if compound not found

        Examples:
            >>> lookup_compound("aspirin")
            >>> lookup_compound("ibuprofen", database="pubchem")
            >>> lookup_compound("acetylsalicylic acid")
        """
        if database != "pubchem":
            return f"ERROR: Database '{database}' not supported. Currently only 'pubchem' is available."

        try:
            # Search PubChem by name
            compounds = pcp.get_compounds(compound_name, "name")

            if not compounds:
                return f"ERROR: No compound found for '{compound_name}' in PubChem. Please check the compound name spelling."

            # Get the first (most relevant) result
            compound = compounds[0]

            # Build formatted response
            synonyms = compound.synonyms[:5] if compound.synonyms else []
            synonyms_str = ", ".join(synonyms) if synonyms else "N/A"

            report = f"""Compound Information for '{compound_name}':

**Identity**:
- PubChem CID: {compound.cid}
- IUPAC Name: {compound.iupac_name or "N/A"}
- Synonyms: {synonyms_str}

**Structure**:
- Canonical SMILES: {compound.connectivity_smiles or "N/A"}
- Isomeric SMILES: {compound.smiles or "N/A"}
- InChI: {compound.inchi or "N/A"}
- InChIKey: {compound.inchikey or "N/A"}

**Basic Properties**:
- Molecular Formula: {compound.molecular_formula or "N/A"}
- Molecular Weight: {compound.molecular_weight or "N/A"} g/mol
- Charge: {compound.charge or 0}

**Database**: PubChem (CID: {compound.cid})
"""
            return report

        except Exception as e:
            return f"ERROR: Failed to lookup compound '{compound_name}': {e!s}"
