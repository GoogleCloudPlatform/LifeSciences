# Copyright 2026 Google LLC
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

"""Tool for visualizing AlphaFold2 structures."""

import logging
import os
import tempfile
from typing import Any, Dict

from ..base import AF2Tool
from ..utils.viz_utils import generate_plddt_colored_pdb

logger = logging.getLogger(__name__)


class AF2VisualizationTool(AF2Tool):
    """Tool for generating 3D visualizations of predicted structures."""

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate structure visualization.

        Args:
            arguments: {
                'pdb_path': Path to PDB file (local or GCS),
                'raw_prediction_path': Path to raw prediction pickle (optional),
                'output_path': Output path for visualization (optional),
                'output_format': 'pdb_colored', 'html', or 'both' (default: 'pdb_colored')
            }

        Returns:
            Visualization results
        """
        pdb_path = arguments.get("pdb_path")
        raw_prediction_path = arguments.get("raw_prediction_path")
        output_path = arguments.get("output_path")
        output_format = arguments.get("output_format", "pdb_colored")

        if not pdb_path:
            raise ValueError("pdb_path is required")

        # Download PDB if from GCS
        local_pdb = pdb_path
        if pdb_path.startswith("gs://"):
            local_pdb = os.path.join(tempfile.gettempdir(), "structure.pdb")
            self._download_from_gcs(pdb_path, local_pdb)

        # Generate pLDDT-colored PDB
        if output_format in ["pdb_colored", "both"]:
            if not raw_prediction_path:
                return {
                    "status": "error",
                    "message": "raw_prediction_path is required for pLDDT coloring",
                }

            # Download raw prediction if from GCS
            local_raw = raw_prediction_path
            if raw_prediction_path.startswith("gs://"):
                local_raw = os.path.join(tempfile.gettempdir(), "raw_prediction.pkl")
                self._download_from_gcs(raw_prediction_path, local_raw)

            # Generate colored PDB
            colored_pdb_path = generate_plddt_colored_pdb(
                pdb_path=local_pdb, raw_prediction_path=local_raw, output_path=output_path
            )

            result = {
                "status": "success",
                "colored_pdb_path": colored_pdb_path,
                "message": "Generated pLDDT-colored PDB file",
            }

            # Cleanup temporary files
            if pdb_path.startswith("gs://"):
                os.remove(local_pdb)
            if raw_prediction_path.startswith("gs://"):
                os.remove(local_raw)

            return result

        # For HTML output (would require py3Dmol)
        if output_format in ["html", "both"]:
            # TODO: Implement HTML visualization with py3Dmol
            return {
                "status": "not_implemented",
                "message": "HTML visualization not yet implemented",
            }

        return {"status": "error", "message": f"Unknown output_format: {output_format}"}
