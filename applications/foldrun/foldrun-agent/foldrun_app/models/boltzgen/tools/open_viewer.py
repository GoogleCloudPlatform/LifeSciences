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

"""BOLTZGEN Open Viewer Tool - Opens the 3D structure viewer for BOLTZGEN design results."""

import logging
import re
import urllib.parse
import webbrowser
from typing import Any

from ..base import BOLTZGENTool

logger = logging.getLogger(__name__)

_JOB_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
_VERTEX_RESOURCE_PATTERN = re.compile(
    r"^projects/[a-zA-Z0-9_-]+/locations/[a-zA-Z0-9_-]+/pipelineJobs/([a-zA-Z0-9_-]+)$"
)


class BOLTZGENOpenViewerTool(BOLTZGENTool):
    """Opens the FoldRun structure viewer for completed BOLTZGEN design jobs."""

    def __init__(self, tool_config: dict[str, Any], config: Any):
        super().__init__(tool_config, config)
        self.viewer_base_url = config.viewer_url

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Open the viewer for a BOLTZGEN job.

        Args:
            arguments: Tool arguments containing job_id and optional parameters
        """
        if (
            not self.viewer_base_url
            or not isinstance(self.viewer_base_url, str)
            or not (
                self.viewer_base_url.startswith("http://")
                or self.viewer_base_url.startswith("https://")
            )
        ):
            raise ValueError(
                "Invalid viewer_base_url: must be set and start with http:// or https://"
            )

        job_id = arguments.get("job_id")
        if not job_id:
            raise ValueError("job_id is required")

        if isinstance(job_id, str):
            m = _VERTEX_RESOURCE_PATTERN.match(job_id)
            if m:
                job_id = m.group(1)

        if not isinstance(job_id, str) or not _JOB_ID_PATTERN.match(job_id):
            raise ValueError(f"Invalid job_id '{job_id}': must match ^[a-zA-Z0-9_-]+$")

        open_browser = arguments.get("open_browser", False)

        encoded_job_id = urllib.parse.quote(job_id, safe="")
        viewer_url = f"{self.viewer_base_url.rstrip('/')}/job/{encoded_job_id}"

        browser_opened = False
        if open_browser:
            try:
                webbrowser.open(viewer_url)
                browser_opened = True
                logger.info(f"Opened viewer in browser for BOLTZGEN job {job_id}")
            except Exception as e:
                logger.warning(f"Failed to open browser: {e}")

        return {
            "job_id": job_id,
            "viewer_url": viewer_url,
            "browser_opened": browser_opened,
            "message": f"Structure viewer URL generated: {viewer_url}",
        }
