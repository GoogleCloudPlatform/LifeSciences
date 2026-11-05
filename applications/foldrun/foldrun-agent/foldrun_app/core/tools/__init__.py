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

"""Model-agnostic platform tools shared across all FoldRun structure prediction backends."""

from foldrun_app.core.base_tool import BaseTool

from .alphafold_db_tools import (
    AlphaFoldDBGetAnnotations,
    AlphaFoldDBGetPrediction,
    AlphaFoldDBGetSummary,
)
from .analyze_job_deep import AnalyzeJobDeepTool
from .check_gpu_quota import CheckGPUQuotaTool
from .cleanup_gcs_files import CleanupGCSFilesTool
from .delete_job import DeleteJobTool
from .find_orphaned_gcs_files import FindOrphanedGCSFilesTool
from .get_job_details import GetJobDetailsTool
from .job_status import JobStatusTool
from .list_jobs import ListJobsTool

PLATFORM_TOOL_CLASSES: dict[str, type[BaseTool]] = {
    "check_job_status": JobStatusTool,
    "list_jobs": ListJobsTool,
    "get_job_details": GetJobDetailsTool,
    "delete_job": DeleteJobTool,
    "check_gpu_quota": CheckGPUQuotaTool,
    "cleanup_gcs_files": CleanupGCSFilesTool,
    "find_orphaned_files": FindOrphanedGCSFilesTool,
    "analyze_job_deep": AnalyzeJobDeepTool,
    "alphafold_db_get_prediction": AlphaFoldDBGetPrediction,
    "alphafold_db_get_summary": AlphaFoldDBGetSummary,
    "alphafold_db_get_annotations": AlphaFoldDBGetAnnotations,
}

__all__ = [
    "PLATFORM_TOOL_CLASSES",
    "AlphaFoldDBGetAnnotations",
    "AlphaFoldDBGetPrediction",
    "AlphaFoldDBGetSummary",
    "AnalyzeJobDeepTool",
    "CheckGPUQuotaTool",
    "CleanupGCSFilesTool",
    "DeleteJobTool",
    "FindOrphanedGCSFilesTool",
    "GetJobDetailsTool",
    "JobStatusTool",
    "ListJobsTool",
]
