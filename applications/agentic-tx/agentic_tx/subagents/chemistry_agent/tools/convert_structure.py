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

"""Structure conversion tool for Chemistry Agent.

Single Responsibility: Convert between chemical structure representations.
"""

from rdkit import Chem


class ConvertStructureTool:
    """Chemical structure conversion tool using RDKit."""

    def convert_structure(self, input_structure: str, input_format: str, output_format: str) -> str:
        """Convert between chemical structure representations.

        This tool converts chemical structures between different formats including
        SMILES, InChI, InChIKey, and Mol formats using RDKit.

        Args:
            input_structure: The chemical structure string to convert.
            input_format: Format of the input structure. Options:
                         - "SMILES": Simplified Molecular Input Line Entry System
                         - "InChI": IUPAC International Chemical Identifier
                         - "InChIKey": Hashed InChI (27-character string)
                         - "Mol": MDL Molfile format
            output_format: Desired output format. Options: "SMILES", "InChI", "InChIKey", "Mol"

        Returns:
            The converted structure string, or an error message if conversion fails.

        Examples:
            >>> convert_structure("CC(=O)OC1=CC=CC=C1C(=O)O", "SMILES", "InChI")
            >>> convert_structure(
            ...     "InChI=1S/C9H8O4/c1-6(10)13-8-5-3-2-4-7(8)9(11)12/h2-5H,1H3,(H,11,12)",
            ...     "InChI",
            ...     "SMILES",
            ... )
            >>> convert_structure("CC(=O)OC1=CC=CC=C1C(=O)O", "SMILES", "InChIKey")

        Note:
            - InChIKey cannot be used as input format (it's a hash, not reversible)
            - Mol format conversions may lose some stereochemistry information
        """
        input_format = input_format.upper()
        output_format = output_format.upper()

        # Validate formats
        valid_formats = {"SMILES", "INCHI", "INCHIKEY", "MOL"}
        if input_format not in valid_formats:
            return f"ERROR: Invalid input format '{input_format}'. Must be one of: {', '.join(valid_formats)}"
        if output_format not in valid_formats:
            return f"ERROR: Invalid output format '{output_format}'. Must be one of: {', '.join(valid_formats)}"

        # InChIKey cannot be used as input (it's a hash)
        if input_format == "INCHIKEY":
            return "ERROR: InChIKey cannot be used as input format. It is a hash and cannot be converted back to other formats."

        # If input and output are the same, just return the input
        if input_format == output_format:
            return input_structure

        try:
            # Step 1: Convert input to RDKit Mol object
            mol = None

            if input_format == "SMILES":
                mol = Chem.MolFromSmiles(input_structure)
            elif input_format == "INCHI":
                mol = Chem.MolFromInchi(input_structure)
            elif input_format == "MOL":
                mol = Chem.MolFromMolBlock(input_structure)

            if mol is None:
                return f"ERROR: Failed to parse {input_format} structure. Please check the input structure is valid."

            # Step 2: Convert Mol to output format
            result = None

            if output_format == "SMILES":
                result = Chem.MolToSmiles(mol)
            elif output_format == "INCHI":
                result = Chem.MolToInchi(mol)
            elif output_format == "INCHIKEY":
                result = Chem.MolToInchiKey(mol)
            elif output_format == "MOL":
                result = Chem.MolToMolBlock(mol)

            if result is None:
                return f"ERROR: Failed to convert to {output_format} format."

            return f"Converted {input_format} to {output_format}:\n\n{result}"

        except Exception as e:
            return f"ERROR: Structure conversion failed: {e!s}"
