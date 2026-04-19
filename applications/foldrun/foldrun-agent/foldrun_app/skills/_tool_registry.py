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

"""Singleton registry for all FoldRun tool instances (AF2, OF3, Boltz-2).

Provides eager or lazy initialization of Config + tool class instances.
"""

import logging

logger = logging.getLogger(__name__)

_agents = {}
_initialized = False


def ensure_initialized():
    """Eagerly initialize all tools.

    Call this at agent startup so the first user command doesn't
    pay the initialization cost. Safe to call multiple times.
    """
    global _initialized
    if not _initialized:
        _initialize_all_tools()
        _initialized = True
    logger.info(f"Tool registry ready — {len(_agents)} tools loaded")


def get_tool(tool_name: str):
    """Get or create a tool instance by name.

    Args:
        tool_name: Tool name as defined in alphafold_tools.json (e.g. 'af2_submit_monomer').

    Returns:
        Initialized tool instance.
    """
    global _initialized
    if not _initialized:
        _initialize_all_tools()
        _initialized = True
    return _agents[tool_name]


def _initialize_all_tools():
    """Initialize all tool instances (AF2 + OF3 + Boltz2)."""
    _initialize_af2_tools()
    _initialize_of3_tools()
    _initialize_boltz2_tools()


def _initialize_boltz2_tools():
    """Initialize Boltz2 tool instances."""
    try:
        from foldrun_app.models.boltz2.startup import get_config, get_tool_configs
        from foldrun_app.models.boltz2.tools import (
            BOLTZ2GetAnalysisResultsTool,
            BOLTZ2JobAnalysisTool,
            BOLTZ2OpenViewerTool,
            BOLTZ2SubmitPredictionTool,
        )

        config = get_config()
        boltz2_tools = get_tool_configs()

        tool_classes = {
            "BOLTZ2SubmitPredictionTool": BOLTZ2SubmitPredictionTool,
            "BOLTZ2JobAnalysisTool": BOLTZ2JobAnalysisTool,
            "BOLTZ2GetAnalysisResultsTool": BOLTZ2GetAnalysisResultsTool,
            "BOLTZ2OpenViewerTool": BOLTZ2OpenViewerTool,
        }

        for tool_config in boltz2_tools:
            tool_type = tool_config["type"]
            tool_name = tool_config["name"]
            tool_class = tool_classes.get(tool_type)

            if tool_class:
                _agents[tool_name] = tool_class(tool_config=tool_config, config=config)
                logger.info(f"Initialized tool: {tool_name}")
            else:
                logger.warning(f"Unknown Boltz2 tool type: {tool_type}")

    except Exception as e:
        logger.warning(f"Boltz2 tools not initialized (missing BOLTZ2_COMPONENTS_IMAGE?): {e}")


def _initialize_af2_tools():
    """Initialize AF2 tool instances."""
    from foldrun_app.models.af2.startup import get_config, get_tool_configs
    from foldrun_app.models.af2.tools import (
        AF2AnalysisTool,
        AF2AnalyzeJobDeepTool,
        AF2BatchSubmitTool,
        AF2CheckGPUQuotaTool,
        AF2CleanupGCSFilesTool,
        AF2DeleteJobTool,
        AF2FindOrphanedGCSFilesTool,
        AF2GetJobDetailsTool,
        AF2GetResultsTool,
        AF2JobStatusTool,
        AF2ListJobsTool,
        AF2OpenViewerTool,
        AF2SubmitMonomerTool,
        AF2SubmitMultimerTool,
        AF2VisualizationTool,
        AlphaFoldDBGetAnnotations,
        AlphaFoldDBGetPrediction,
        AlphaFoldDBGetSummary,
    )
    from foldrun_app.models.af2.tools.analyze_job import AF2JobAnalysisTool
    from foldrun_app.models.af2.tools.get_analysis_results import AF2GetAnalysisResultsTool

    config = get_config()

    # Load tool configurations (cached — shared with startup.py)
    af2_tools = get_tool_configs()

    tool_classes = {
        "AF2SubmitMonomerTool": AF2SubmitMonomerTool,
        "AF2SubmitMultimerTool": AF2SubmitMultimerTool,
        "AF2BatchSubmitTool": AF2BatchSubmitTool,
        "AF2JobStatusTool": AF2JobStatusTool,
        "AF2ListJobsTool": AF2ListJobsTool,
        "AF2GetResultsTool": AF2GetResultsTool,
        "AF2GetJobDetailsTool": AF2GetJobDetailsTool,
        "AF2DeleteJobTool": AF2DeleteJobTool,
        "AF2CleanupGCSFilesTool": AF2CleanupGCSFilesTool,
        "AF2FindOrphanedGCSFilesTool": AF2FindOrphanedGCSFilesTool,
        "AF2CheckGPUQuotaTool": AF2CheckGPUQuotaTool,
        "AF2AnalysisTool": AF2AnalysisTool,
        "AF2VisualizationTool": AF2VisualizationTool,
        "AF2GetAnalysisResultsTool": AF2GetAnalysisResultsTool,
        "AF2JobAnalysisTool": AF2JobAnalysisTool,
        "AF2OpenViewerTool": AF2OpenViewerTool,
        "AlphaFoldDBGetPrediction": AlphaFoldDBGetPrediction,
        "AlphaFoldDBGetSummary": AlphaFoldDBGetSummary,
        "AlphaFoldDBGetAnnotations": AlphaFoldDBGetAnnotations,
    }

    for tool_config in af2_tools:
        tool_type = tool_config["type"]
        tool_name = tool_config["name"]
        tool_class = tool_classes.get(tool_type)

        if tool_class:
            _agents[tool_name] = tool_class(tool_config=tool_config, config=config)
            logger.info(f"Initialized tool: {tool_name}")
        else:
            logger.warning(f"Unknown tool type: {tool_type}")

    # Also register the deep analysis tool (not in JSON config, dynamically created)
    if "af2_analyze_job_deep" not in _agents:
        deep_config = {
            "name": "af2_analyze_job_deep",
            "type": "AF2AnalyzeJobDeepTool",
            "description": "Performs comprehensive analysis of any AlphaFold job regardless of state.",
        }
        _agents["af2_analyze_job_deep"] = AF2AnalyzeJobDeepTool(
            tool_config=deep_config, config=config
        )
        logger.info("Initialized tool: af2_analyze_job_deep")


def _initialize_of3_tools():
    """Initialize OF3 tool instances."""
    try:
        from foldrun_app.models.of3.startup import get_config, get_tool_configs
        from foldrun_app.models.of3.tools import (
            OF3GetAnalysisResultsTool,
            OF3JobAnalysisTool,
            OF3OpenViewerTool,
            OF3SubmitPredictionTool,
        )

        config = get_config()
        of3_tools = get_tool_configs()

        tool_classes = {
            "OF3SubmitPredictionTool": OF3SubmitPredictionTool,
            "OF3JobAnalysisTool": OF3JobAnalysisTool,
            "OF3GetAnalysisResultsTool": OF3GetAnalysisResultsTool,
            "OF3OpenViewerTool": OF3OpenViewerTool,
        }

        for tool_config in of3_tools:
            tool_type = tool_config["type"]
            tool_name = tool_config["name"]
            tool_class = tool_classes.get(tool_type)

            if tool_class:
                _agents[tool_name] = tool_class(tool_config=tool_config, config=config)
                logger.info(f"Initialized tool: {tool_name}")
            else:
                logger.warning(f"Unknown OF3 tool type: {tool_type}")

    except Exception as e:
        logger.warning(f"OF3 tools not initialized (missing OPENFOLD3_COMPONENTS_IMAGE?): {e}")
