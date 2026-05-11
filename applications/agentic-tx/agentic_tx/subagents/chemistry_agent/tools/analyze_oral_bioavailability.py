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

"""Oral bioavailability analysis tool for Chemistry Agent.

Single Responsibility: Analyze oral bioavailability using Lipinski rules and other criteria.
"""

from rdkit import Chem
from rdkit.Chem import Crippen, Descriptors


class AnalyzeOralBioavailabilityTool:
    """Oral bioavailability analysis tool using Lipinski's Rule of Five."""

    def analyze_oral_bioavailability(self, smiles: str) -> str:
        """Analyze oral bioavailability using Lipinski's Rule of Five and related criteria.

        This tool evaluates a compound's drug-likeness and predicted oral bioavailability
        based on:
        - Lipinski's Rule of Five (MW, LogP, HBD, HBA)
        - Veber's rules (rotatable bonds, TPSA)
        - Additional ADME-relevant properties

        Lipinski's Rule of Five states that poor absorption or permeation is more likely when:
        - Molecular weight > 500 Da
        - LogP > 5
        - H-bond donors > 5
        - H-bond acceptors > 10

        Args:
            smiles: SMILES string representation of the compound.

        Returns:
            A formatted report containing:
            - Lipinski rule compliance (pass/fail for each rule)
            - Veber criteria (rotatable bonds, TPSA)
            - Overall bioavailability assessment
            - Recommendations
            - Error message if calculation fails

        Examples:
            >>> analyze_oral_bioavailability("CC(=O)OC1=CC=CC=C1C(=O)O")  # Aspirin
            >>> analyze_oral_bioavailability(
            ...     "CC(C)Cc1ccc(cc1)C(C)C(O)=O"
            ... )  # Ibuprofen
        """
        try:
            # Parse SMILES
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                return f"ERROR: Invalid SMILES string: '{smiles}'. Please provide a valid SMILES representation."

            # Calculate descriptors
            mw = Descriptors.MolWt(mol)
            logp = Crippen.MolLogP(mol)
            hbd = Descriptors.NumHDonors(mol)
            hba = Descriptors.NumHAcceptors(mol)
            rotatable_bonds = Descriptors.NumRotatableBonds(mol)
            tpsa = Descriptors.TPSA(mol)

            # Lipinski's Rule of Five checks
            mw_pass = mw <= 500
            logp_pass = logp <= 5
            hbd_pass = hbd <= 5
            hba_pass = hba <= 10

            # Veber's rules
            rotatable_pass = rotatable_bonds <= 10
            tpsa_pass = tpsa <= 140

            # Count violations
            lipinski_violations = sum([not mw_pass, not logp_pass, not hbd_pass, not hba_pass])
            veber_violations = sum([not rotatable_pass, not tpsa_pass])

            # Overall assessment
            if lipinski_violations == 0 and veber_violations == 0:
                overall = "EXCELLENT - Likely good oral bioavailability"
                color = "✓✓✓"
            elif lipinski_violations <= 1 and veber_violations <= 1:
                overall = "GOOD - Acceptable for oral drugs"
                color = "✓✓"
            elif lipinski_violations <= 2:
                overall = "MODERATE - May have bioavailability issues"
                color = "✓"
            else:
                overall = "POOR - Likely poor oral bioavailability"
                color = "✗"

            # Build report
            report = f"""Oral Bioavailability Analysis for: {smiles}

**Lipinski's Rule of Five**:
- Molecular Weight ≤ 500: {mw:.2f} Da {"✓ PASS" if mw_pass else "✗ FAIL"}
- LogP ≤ 5: {logp:.2f} {"✓ PASS" if logp_pass else "✗ FAIL"}
- H-bond Donors ≤ 5: {hbd} {"✓ PASS" if hbd_pass else "✗ FAIL"}
- H-bond Acceptors ≤ 10: {hba} {"✓ PASS" if hba_pass else "✗ FAIL"}

Lipinski Violations: {lipinski_violations}/4

**Veber's Criteria** (flexibility and polarity):
- Rotatable Bonds ≤ 10: {rotatable_bonds} {"✓ PASS" if rotatable_pass else "✗ FAIL"}
- TPSA ≤ 140 Ų: {tpsa:.2f} Ų {"✓ PASS" if tpsa_pass else "✗ FAIL"}

Veber Violations: {veber_violations}/2

**Overall Assessment**: {color} {overall}

**Interpretation**:
"""

            # Add specific recommendations
            recommendations = []

            if not mw_pass:
                recommendations.append(
                    "- Molecular weight is too high. Consider removing or replacing heavy substituents."
                )
            if not logp_pass:
                recommendations.append("- LogP is too high (too lipophilic). Consider adding polar groups.")
            if not hbd_pass:
                recommendations.append(
                    "- Too many H-bond donors. Consider protecting or removing hydroxyl/amine groups."
                )
            if not hba_pass:
                recommendations.append(
                    "- Too many H-bond acceptors. Consider reducing carbonyl, ether, or nitrogen groups."
                )
            if not rotatable_pass:
                recommendations.append(
                    "- Too many rotatable bonds. Structure is too flexible. Consider adding ring constraints."
                )
            if not tpsa_pass:
                recommendations.append("- TPSA is too high. Compound may have poor membrane permeability.")

            if recommendations:
                report += "\n".join(recommendations)
            else:
                report += "- Compound shows excellent drug-like properties for oral administration.\n"
                report += "- All Lipinski and Veber criteria are satisfied.\n"
                report += "- Predicted good oral bioavailability and membrane permeability."

            # Add note about exceptions
            report += """

**Note**: Lipinski's Rule of Five is a guideline, not an absolute rule. Some successful
oral drugs violate one or more criteria (e.g., natural products, peptides). Violations
should be considered in context with other ADME properties and therapeutic class.
"""

            return report

        except Exception as e:
            return f"ERROR: Failed to analyze oral bioavailability: {e!s}"
