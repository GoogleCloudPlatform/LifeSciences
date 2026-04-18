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

"""BOLTZ2 Open Viewer Tool - Opens the 3D structure viewer for BOLTZ2 prediction results."""

import logging
import webbrowser
from typing import Any, Dict

from ..base import BOLTZ2Tool

logger = logging.getLogger(__name__)


class BOLTZ2OpenViewerTool(BOLTZ2Tool):
    """Opens the FoldRun structure viewer for completed BOLTZ2 predictions."""

    def __init__(self, tool_config: Dict[str, Any], config: Any):
        super().__init__(tool_config, config)
        self.viewer_base_url = config.viewer_url

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Open the viewer for an BOLTZ2 job.

        Args:
            arguments: Tool arguments containing job_id and optional parameters
        """
        job_id = arguments.get("job_id")
        if not job_id:
            raise ValueError("job_id is required")

        open_browser = arguments.get("open_browser", True)

        # Simple URL: viewer resolves all GCS paths server-side from job_id
        viewer_url = f"{self.viewer_base_url}/job/{job_id}"

        browser_opened = False
        if open_browser:
            try:
                webbrowser.open(viewer_url)
                browser_opened = True
                logger.info(f"Opened viewer in browser for BOLTZ2 job {job_id}")
            except Exception as e:
                logger.warning(f"Failed to open browser: {e}")

        return {
            "job_id": job_id,
            "viewer_url": viewer_url,
            "browser_opened": browser_opened,
            "message": (
                "Viewer opened in your default browser"
                if browser_opened
                else "Copy the URL to view in your browser"
            ),
        }
