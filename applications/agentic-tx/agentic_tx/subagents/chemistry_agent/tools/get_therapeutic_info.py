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

"""Therapeutic information tool for Chemistry Agent.

Single Responsibility: Get therapeutic information from ChEMBL and other databases.
"""

import requests

from rdkit import Chem


class GetTherapeuticInfoTool:
    """Therapeutic information retrieval tool using ChEMBL API."""

    def get_therapeutic_info(self, smiles: str) -> str:
        """Get therapeutic information for a compound from ChEMBL.

        This tool retrieves therapeutic data including:
        - Mechanisms of action
        - Drug indications
        - ATC (Anatomical Therapeutic Chemical) classifications
        - Development phase
        - Known targets

        Args:
            smiles: SMILES string representation of the compound.

        Returns:
            A formatted report containing:
            - ChEMBL ID
            - Mechanisms of action
            - Therapeutic indications
            - ATC classifications
            - Development status
            - Known targets
            - Error message if no data found or lookup fails

        Examples:
            >>> get_therapeutic_info("CC(=O)OC1=CC=CC=C1C(=O)O")  # Aspirin
            >>> get_therapeutic_info("CC(C)Cc1ccc(cc1)C(C)C(O)=O")  # Ibuprofen
        """
        try:
            # Validate SMILES
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                return f"ERROR: Invalid SMILES string: '{smiles}'. Please provide a valid SMILES representation."

            # Convert to canonical SMILES for ChEMBL lookup
            canonical_smiles = Chem.MolToSmiles(mol)

            # Try to find the compound in ChEMBL by structure similarity
            chembl_api_url = "https://www.ebi.ac.uk/chembl/api/data"

            # Search by SMILES
            search_url = (
                f"{chembl_api_url}/molecule.json?molecule_structures__canonical_smiles__flexmatch={canonical_smiles}"
            )

            response = requests.get(search_url, timeout=10)

            if response.status_code != 200:
                return f"ERROR: ChEMBL API request failed with status {response.status_code}."

            data = response.json()

            if not data.get("molecules") or len(data["molecules"]) == 0:
                return f"No therapeutic information found in ChEMBL for SMILES: {smiles}\n\nThis compound may not be a known drug or may not be in the ChEMBL database."

            # Get the first (best match) molecule
            molecule = data["molecules"][0]
            chembl_id = molecule.get("molecule_chembl_id", "N/A")
            pref_name = molecule.get("pref_name", "N/A")
            max_phase = molecule.get("max_phase", "N/A")

            # Get mechanisms of action
            mechanisms_url = f"{chembl_api_url}/mechanism.json?molecule_chembl_id={chembl_id}"
            mechanisms_response = requests.get(mechanisms_url, timeout=10)
            mechanisms = []

            if mechanisms_response.status_code == 200:
                mech_data = mechanisms_response.json()
                if mech_data.get("mechanisms"):
                    for mech in mech_data["mechanisms"][:3]:  # Top 3 mechanisms
                        action = mech.get("mechanism_of_action", "N/A")
                        target = mech.get("target_chembl_id", "N/A")
                        mechanisms.append(f"- {action} (Target: {target})")

            mechanisms_str = "\n".join(mechanisms) if mechanisms else "- No mechanism data available"

            # Get indications
            indications_url = f"{chembl_api_url}/drug_indication.json?molecule_chembl_id={chembl_id}"
            indications_response = requests.get(indications_url, timeout=10)
            indications = []

            if indications_response.status_code == 200:
                ind_data = indications_response.json()
                if ind_data.get("drug_indications"):
                    for ind in ind_data["drug_indications"][:5]:  # Top 5 indications
                        indication = ind.get("indication", "N/A")
                        max_phase_ind = ind.get("max_phase_for_ind", "N/A")
                        indications.append(f"- {indication} (Phase {max_phase_ind})")

            indications_str = "\n".join(indications) if indications else "- No indication data available"

            # Get ATC classifications
            atc_codes = molecule.get("atc_classifications", [])
            atc_str = "\n".join([f"- {atc}" for atc in atc_codes]) if atc_codes else "- No ATC classification available"

            # Phase interpretation
            phase_interpretation = {
                "0": "Preclinical",
                "1": "Phase I Clinical Trial",
                "2": "Phase II Clinical Trial",
                "3": "Phase III Clinical Trial",
                "4": "Approved Drug",
            }
            phase_text = phase_interpretation.get(str(max_phase), "Unknown")

            # Build report
            report = f"""Therapeutic Information for: {smiles}

**Identity**:
- ChEMBL ID: {chembl_id}
- Preferred Name: {pref_name}
- Development Status: {phase_text} (Max Phase: {max_phase})

**Mechanisms of Action**:
{mechanisms_str}

**Therapeutic Indications**:
{indications_str}

**ATC Classifications**:
{atc_str}

**Database**: ChEMBL (https://www.ebi.ac.uk/chembl/compound_report_card/{chembl_id}/)
"""
            return report

        except requests.exceptions.RequestException as e:
            return f"ERROR: Network error accessing ChEMBL API: {e!s}"
        except Exception as e:
            return f"ERROR: Failed to retrieve therapeutic information: {e!s}"
