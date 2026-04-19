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

"""Visualization tool wrappers for ADK FunctionTool."""

from typing import Optional

from foldrun_app.skills._tool_registry import get_tool


def open_of3_structure_viewer(
    job_id: str,
    open_browser: bool = True,
) -> dict:
    """
    Open the FoldRun structure viewer for OpenFold3 predictions.

    Displays interactive 3D CIF structure with pLDDT confidence coloring,
    ranking scores, ipTM matrix, and analysis plots.

    Args:
        job_id: OF3 pipeline job ID (e.g., 'openfold3-inference-pipeline-20260215153755')
        open_browser: Automatically open in browser (default: True)

    Returns:
        Viewer URL and status information
    """
    return get_tool("of3_open_viewer").run(
        {
            "job_id": job_id,
            "open_browser": open_browser,
        }
    )


def open_boltz2_structure_viewer(
    job_id: str,
    open_browser: bool = True,
) -> dict:
    """
    Open the FoldRun structure viewer for Boltz-2 predictions.

    Displays interactive 3D CIF structure with pLDDT confidence coloring,
    ranking scores, ipTM matrix, and analysis plots.

    Args:
        job_id: Boltz-2 pipeline job ID
        open_browser: Automatically open in browser (default: True)

    Returns:
        Viewer URL and status information
    """
    return get_tool("boltz2_open_viewer").run(
        {
            "job_id": job_id,
            "open_browser": open_browser,
        }
    )


def open_structure_viewer(
    job_id: str,
    pdb_uri: Optional[str] = None,
    summary_uri: Optional[str] = None,
    model_name: str = "Best Model",
    open_browser: bool = True,
) -> dict:
    """
    Open the FoldRun structure viewer in a web browser.

    Displays interactive 3D protein structure with pLDDT confidence coloring,
    quality metrics, analysis plots, and download links. Works with completed
    prediction jobs.

    Args:
        job_id: Pipeline job ID (e.g., 'alphafold-inference-pipeline-20250421110959')
        pdb_uri: GCS URI to PDB file (optional, defaults to ranked_0.pdb from job)
        summary_uri: GCS URI to analysis summary.json (optional, auto-constructed from job_id)
        model_name: Display name for the model (default: 'Best Model')
        open_browser: Automatically open in browser (default: True)

    Returns:
        Viewer URL and status information
    """
    return get_tool("af2_open_viewer").run(
        {
            "job_id": job_id,
            "pdb_uri": pdb_uri,
            "summary_uri": summary_uri,
            "model_name": model_name,
            "open_browser": open_browser,
        }
    )
