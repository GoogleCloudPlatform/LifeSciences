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
    """Construct and optionally open the FoldRun 3D Mol* structure viewer URL for any completed job.

    Args:
        job_id: Pipeline job ID or AlphaFold 3 job name.
        pdb_uri: Optional GCS URI to PDB file (AF2 only).
        summary_uri: Optional GCS URI to summary.json (AF2 only).
        model_name: Display name (default "Best Model").
        open_browser: Open in browser automatically (default True).
    """
    return dispatch_open_structure_viewer(
        job_id,
        pdb_uri=pdb_uri,
        summary_uri=summary_uri,
        model_name=model_name,
        open_browser=open_browser,
        get_tool_fn=get_tool,
    )
