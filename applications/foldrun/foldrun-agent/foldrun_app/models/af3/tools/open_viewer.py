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

"""AF3 Open Viewer Tool - Opens the 3D structure viewer for AlphaFold 3 prediction results."""

import logging
import webbrowser
from typing import Any

from ..base import AF3Tool
from ..config import AF3Config

logger = logging.getLogger(__name__)


class AF3OpenViewerTool(AF3Tool):
    """Opens the FoldRun structure viewer for completed AlphaFold 3 predictions."""

    def __init__(self, tool_config: dict[str, Any], config: AF3Config | None = None):
        super().__init__(tool_config, config or AF3Config())

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Open the viewer for an AF3 job.

        Args:
            arguments: Tool arguments containing job_id and optional parameters

        Returns:
            Dictionary with viewer status, URL, and browser state.
        """
        job_id = arguments.get("job_id")
        if not job_id:
            return {"status": "error", "message": "Missing required argument 'job_id'."}

        open_browser = arguments.get("open_browser", True)
        viewer_base_url = self.config.viewer_url

        # Viewer URL with model=af3 to load mmCIF and render all-atom representation (ligands/nucleic acids)
        viewer_url = (
            f"{viewer_base_url}/job/{job_id}?model=af3"
            if viewer_base_url
            else self.gcs_console_url(self.get_job_gcs_prefix(job_id))
        )

        browser_opened = False
        if open_browser and viewer_base_url:
            try:
                webbrowser.open(viewer_url)
                browser_opened = True
                logger.info(f"Opened viewer in browser for AF3 job {job_id}")
            except Exception as e:
                logger.warning(f"Failed to open browser: {e}")

        return {
            "status": "succeeded",
            "job_id": job_id,
            "viewer_url": viewer_url,
            "browser_opened": browser_opened,
            "message": (
                "Viewer opened in your default browser"
                if browser_opened
                else "Copy the URL to view in your browser"
            ),
        }
