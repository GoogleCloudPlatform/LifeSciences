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

from foldrun_app.core.dispatch import dispatch_open_structure_viewer
from foldrun_app.skills._tool_registry import get_tool


def open_structure_viewer(
    job_id: str,
    pdb_uri: str | None = None,
    summary_uri: str | None = None,
    model_name: str = "Best Model",
    open_browser: bool = True,
) -> dict:
    """
    Open the FoldRun 3D structure viewer for any completed prediction job.

    Automatically detects the job's model backend (AlphaFold2, OpenFold3, Boltz-2,
    or AlphaFold 3) and constructs the interactive Mol* viewer URL displaying
    3D coordinates (PDB / mmCIF) with pLDDT confidence coloring, polymers, ligands,
    quality metrics, and analysis plots.

    Args:
        job_id: Pipeline job ID or AlphaFold 3 job name
        pdb_uri: GCS URI to PDB file (optional, AF2 only)
        summary_uri: GCS URI to analysis summary.json (optional, AF2 only)
        model_name: Display name for the model (default: 'Best Model')
        open_browser: Automatically open in browser (default: True)

    Returns:
        Viewer URL and status information
    """
    return dispatch_open_structure_viewer(
        job_id,
        pdb_uri=pdb_uri,
        summary_uri=summary_uri,
        model_name=model_name,
        open_browser=open_browser,
        get_tool_fn=get_tool,
    )
