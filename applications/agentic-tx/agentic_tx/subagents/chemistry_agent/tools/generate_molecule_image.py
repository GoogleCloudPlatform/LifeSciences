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

"""Molecule visualization tool for Chemistry Agent.

Single Responsibility: Generate 2D molecular structure images from SMILES.
"""

import io

from typing import Any
from uuid import uuid4

from rdkit import Chem
from rdkit.Chem import Draw
from rdkit.Chem.Draw import rdMolDraw2D

from google.adk.tools.tool_context import ToolContext
from google.genai import types


class GenerateMoleculeImageTool:
    """Molecule image generation tool using RDKit."""

    async def generate_molecule_image(
        self,
        smiles: str,
        tool_context: ToolContext,
        width: int = 800,
        height: int = 800,
        highlight_atoms: list[int] | None = None,
    ) -> dict[str, Any]:
        """Generate a 2D image of a molecule from its SMILES string.

        This tool creates a 2D molecular structure diagram that can be used for
        visualization and analysis. The image is saved as an artifact and can be
        referenced in subsequent operations.

        Args:
            smiles: SMILES string representation of the compound.
            tool_context: The tool context for artifact management.
            width: Width of the generated image in pixels (default: 800).
            height: Height of the generated image in pixels (default: 800).
            highlight_atoms: Optional list of atom indices to highlight in the image.
                           Atom indices start at 0. Example: [0, 1, 2] highlights the first three atoms.

        Returns:
            dict[str, Any]: A dictionary containing either:
                - 'filename': The artifact filename where the image was saved
                - 'artifact_id': The ID of the saved artifact
                - 'error': Error message if generation failed

        Examples:
            >>> await generate_molecule_image(
            ...     "CC(=O)OC1=CC=CC=C1C(=O)O", tool_context
            ... )  # Aspirin
            {'filename': 'molecule_1234567890.png'}

            >>> await generate_molecule_image(
            ...     "CC(C)Cc1ccc(cc1)C(C)C(O)=O", tool_context
            ... )  # Ibuprofen
            {'filename': 'molecule_1234567891.png'}

            >>> await generate_molecule_image(
            ...     "CN1C=NC2=C1C(=O)N(C(=O)N2C)C",
            ...     tool_context,
            ...     highlight_atoms=[0, 1, 2],
            ... )
            {'filename': 'molecule_1234567892.png'}

        Note:
            - The generated image is in PNG format
            - Invalid SMILES strings will return an error
            - Larger molecules may benefit from larger width/height values
        """
        try:
            # Parse SMILES with RDKit
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                return {
                    "error": f"Invalid SMILES string: '{smiles}'. Please provide a valid SMILES representation.",
                }

            # Configure drawing options using modern MolDrawOptions
            # Note: MolToImage automatically generates 2D coordinates if needed
            draw_options = rdMolDraw2D.MolDrawOptions()
            draw_options.bondLineWidth = 2

            # Generate the molecule image
            if highlight_atoms:
                img = Draw.MolToImage(mol, size=(width, height), highlightAtoms=highlight_atoms, options=draw_options)
            else:
                img = Draw.MolToImage(mol, size=(width, height), options=draw_options)

            # Convert PIL Image to PNG bytes
            img_bytes = io.BytesIO()
            img.save(img_bytes, format="PNG")
            img_data = img_bytes.getvalue()

            # Create a Part with inline_data (following the reference pattern)
            part = types.Part(inline_data=types.Blob(mime_type="image/png", data=img_data))

            # Save the artifact using the tool context
            filename = f"molecule_{uuid4()}.png"
            artifact_id = await tool_context.save_artifact(filename, part)

            return {"filename": filename, "artifact_id": artifact_id}

        except (ValueError, AttributeError, OSError) as e:
            return {"error": f"Failed to generate molecule image: {e!s}"}

    async def generate_molecule_grid(
        self,
        smiles_list: list[str],
        tool_context: ToolContext,
        legends: list[str] | None = None,
        mols_per_row: int = 2,
        width: int = 800,
    ) -> dict[str, Any]:
        """Generate a grid image showing multiple molecules side-by-side.

        This tool is useful for comparing multiple compounds visually. Each molecule
        is displayed in its own cell in a grid layout, optionally with labels.
        The grid automatically adjusts dimensions to preserve aspect ratio.

        Args:
            smiles_list: List of SMILES strings for the compounds to display.
            tool_context: The tool context for artifact management.
            legends: Optional list of labels for each molecule (e.g., compound names).
                    Must match length of smiles_list if provided.
            mols_per_row: Number of molecules to display per row (default: 2).
            width: Total width of the grid image in pixels (default: 1200).

        Returns:
            dict[str, Any]: A dictionary containing either:
                - 'filename': The artifact filename where the grid was saved
                - 'artifact_id': The ID of the saved artifact
                - 'error': Error message if generation failed

        Examples:
            >>> await generate_molecule_grid(
            ...     ["CC(=O)OC1=CC=CC=C1C(=O)O", "CC(C)Cc1ccc(cc1)C(C)C(O)=O"],
            ...     tool_context,
            ...     legends=["Aspirin", "Ibuprofen"],
            ... )
            {'filename': 'molecule_grid_1234567890.png'}

        Note:
            - Any invalid SMILES in the list will be displayed as an empty cell
            - Grid dimensions and height adjust automatically to preserve aspect ratio
            - Each molecule cell is square (equal width and height)
        """
        try:
            if not smiles_list:
                return {"error": "smiles_list cannot be empty"}

            # Parse all SMILES strings
            mols = []
            for smiles in smiles_list:
                mol = Chem.MolFromSmiles(smiles)
                mols.append(mol)  # None will be handled by RDKit as empty cell

            # Validate legends if provided
            if legends and len(legends) != len(smiles_list):
                return {
                    "error": f"legends length ({len(legends)}) must match smiles_list length ({len(smiles_list)})",
                }

            # Calculate sub-image size (square cells)
            # Grid rows are calculated automatically by RDKit based on number of molecules
            sub_img_width = width // mols_per_row
            sub_img_height = sub_img_width  # Keep square aspect ratio

            # Generate grid image
            img = Draw.MolsToGridImage(
                mols,
                molsPerRow=mols_per_row,
                subImgSize=(sub_img_width, sub_img_height),
                legends=legends,
            )

            # Convert PIL Image to PNG bytes
            img_bytes = io.BytesIO()
            img.save(img_bytes, format="PNG")
            img_data = img_bytes.getvalue()

            # Create a Part with inline_data
            part = types.Part(inline_data=types.Blob(mime_type="image/png", data=img_data))

            # Save the artifact
            filename = f"molecule_grid_{uuid4()}.png"
            artifact_id = await tool_context.save_artifact(filename, part)

            return {"filename": filename, "artifact_id": artifact_id}

        except (ValueError, AttributeError, OSError) as e:
            return {"error": f"Failed to generate molecule grid: {e!s}"}
