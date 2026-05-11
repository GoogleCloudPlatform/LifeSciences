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

"""Molecular properties tool for Chemistry Agent.

Single Responsibility: Get molecular properties from PubChem.
"""

from textwrap import dedent

import pubchempy as pcp

from rdkit import Chem
from rdkit.Chem import Crippen, Descriptors


class GetMolecularPropertiesTool:
    """Molecular properties calculation tool using PubChem and RDKit."""

    def get_molecular_properties(self, smiles: str) -> str:
        """Get molecular properties for a compound from its SMILES string.

        This tool calculates and retrieves molecular properties including:
        - Basic properties: Molecular weight, formula, charge
        - Lipophilicity: LogP (partition coefficient)
        - Polarity: TPSA (topological polar surface area)
        - Hydrogen bonding: H-bond donors and acceptors
        - Flexibility: Rotatable bonds
        - Complexity: Ring count, aromatic rings

        Args:
            smiles: SMILES string representation of the compound.

        Returns:
            A formatted report containing:
            - Molecular formula and weight
            - Lipophilicity (LogP)
            - Polarity (TPSA)
            - H-bond donors and acceptors
            - Rotatable bonds and ring counts
            - Additional descriptors
            - Error message if calculation fails

        Examples:
            >>> get_molecular_properties("CC(=O)OC1=CC=CC=C1C(=O)O")  # Aspirin
            >>> get_molecular_properties("CC(C)Cc1ccc(cc1)C(C)C(O)=O")  # Ibuprofen
            >>> get_molecular_properties("CN1C=NC2=C1C(=O)N(C(=O)N2C)C")  # Caffeine
        """
        try:
            # Parse SMILES with RDKit
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                return f"ERROR: Invalid SMILES string: '{smiles}'. Please provide a valid SMILES representation."

            # Calculate RDKit descriptors
            mw = Descriptors.MolWt(mol)
            logp = Crippen.MolLogP(mol)
            tpsa = Descriptors.TPSA(mol)
            hbd = Descriptors.NumHDonors(mol)
            hba = Descriptors.NumHAcceptors(mol)
            rotatable_bonds = Descriptors.NumRotatableBonds(mol)
            aromatic_rings = Descriptors.NumAromaticRings(mol)
            rings = Descriptors.RingCount(mol)
            heavy_atoms = Descriptors.HeavyAtomCount(mol)

            # Get molecular formula
            formula = Chem.rdMolDescriptors.CalcMolFormula(mol)

            # Try to get additional info from PubChem (optional, may fail)
            pubchem_info = ""
            try:
                compounds = pcp.get_compounds(smiles, "smiles")
                if compounds:
                    compound = compounds[0]
                    pubchem_info = (
                        f"\n**PubChem Data**:\n- CID: {compound.cid}\n- IUPAC Name: {compound.iupac_name or 'N/A'}"
                    )
            except:
                pass  # PubChem lookup is optional

            # Build formatted report
            report = dedent(f"""\
                Molecular Properties for: {smiles}

                **Basic Properties**:
                - Molecular Formula: {formula}
                - Molecular Weight: {mw:.2f} g/mol
                - Heavy Atom Count: {heavy_atoms}

                **Lipophilicity**:
                - LogP: {logp:.2f} (partition coefficient, lipophilicity measure)

                **Polarity**:
                - TPSA: {tpsa:.2f} Ų (topological polar surface area)

                **Hydrogen Bonding**:
                - H-bond Donors: {hbd}
                - H-bond Acceptors: {hba}

                **Structural Features**:
                - Rotatable Bonds: {rotatable_bonds}
                - Ring Count: {rings}
                - Aromatic Rings: {aromatic_rings}
                {pubchem_info}

                **Interpretation**:
                - LogP {logp:.2f}: {"Good lipophilicity" if -0.4 <= logp <= 5.6 else "Outside typical drug range"}
                - TPSA {tpsa:.2f} Ų: {"Good permeability" if tpsa <= 140 else "May have poor permeability"}
                - Rotatable Bonds {rotatable_bonds}: {"Flexible" if rotatable_bonds > 5 else "Rigid"} structure
                """)
            return report

        except Exception as e:
            return f"ERROR: Failed to calculate molecular properties: {e!s}"
