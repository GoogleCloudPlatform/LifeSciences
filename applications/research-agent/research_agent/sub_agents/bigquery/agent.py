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

"""BigQuery Agent for retrieving patent and article data."""

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from ...state_keys import StateKeys

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools import BaseTool, ToolContext
from google.adk.tools.bigquery import BigQueryToolset
from google.adk.tools.bigquery.config import BigQueryToolConfig, WriteMode
from google.genai import types

from ...config import config

logger = logging.getLogger(__name__)

# Load BigQuery instructions
_bigquery_instructions_path = Path(__file__).parent / "instructions.md"
BIGQUERY_INSTRUCTIONS = _bigquery_instructions_path.read_text()

# Get the project ID from environment - this is the billing project where queries run
project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
if not project_id:
    raise ValueError("GOOGLE_CLOUD_PROJECT environment variable must be set")

# Format instructions with the correct project ID
formatted_instructions = BIGQUERY_INSTRUCTIONS.format(project_id=project_id)

ADK_BUILTIN_BQ_EXECUTE_SQL_TOOL = "execute_sql"


def store_results_in_context(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
    tool_response: Dict,
) -> Optional[Dict]:
    """
    Store BigQuery results directly in state for downstream use.

    Storage strategy:
    - Detects data source (patents, pubmed, or generic) from SQL query
    - Results stored as: {source}_results (single key with all rows)
      - pubmed_results for PubMed/PMC articles
      - patents_results for patent data
      - bigquery_results for generic queries
    - Metadata stored as: {source}_metadata
    - Sets skip_summarization to bypass LLM processing of large data
    """
    if tool.name == ADK_BUILTIN_BQ_EXECUTE_SQL_TOOL:
        # Handle case where tool_response is a string (error message)
        if isinstance(tool_response, str):
            logger.error(f"BigQuery tool returned string response: {tool_response}")
            return None

        if tool_response.get("status") == "SUCCESS":
            rows = tool_response.get("rows", [])

            logger.info(f"BigQuery tool returned {len(rows)} rows in tool_response")

            # Detect data source from SQL query to use appropriate key prefix
            sql_query = args.get("query", "").lower()

            if "pmc_open_access" in sql_query or "articles" in sql_query:
                source_prefix = "pubmed"
            elif "patents" in sql_query or "patentsview" in sql_query or "patent_claims" in sql_query:
                source_prefix = "patents"
            else:
                source_prefix = "bigquery"  # Generic fallback

            logger.info(f"Detected data source: {source_prefix} from query")

            # Store results directly in state
            total_rows = len(rows)
            tool_context.state[f"{source_prefix}_results"] = rows

            # Store metadata
            job_id = tool_response.get("job_id")
            project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
            location = tool_response.get("location") or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

            # Build metadata dictionary, excluding null values
            metadata = {
                "source": source_prefix,
                "total_rows": total_rows,
                "sql_query": args.get("query", ""),
            }

            # Only include job_id if it exists
            if job_id:
                metadata["job_id"] = job_id

            # Only include console_link if both job_id and project_id exist
            if job_id and project_id:
                console_link = f"https://console.cloud.google.com/bigquery?project={project_id}&j=bq:{location}:{job_id}&page=queryresults"
                metadata["console_link"] = console_link

            tool_context.state[f"{source_prefix}_metadata"] = metadata

            # CRITICAL: Set skip_summarization to prevent LLM from processing large results
            # This ensures the Flash sub-agent doesn't see the full data in context
            tool_context.actions.skip_summarization = True

            logger.info(
                f"BigQuery results stored: {total_rows} rows in state['{source_prefix}_results'] "
                f"(skip_summarization=True)"
            )

    # IMPORTANT: Return None to let the original tool_response flow through
    # The skip_summarization flag will prevent LLM from seeing the full response
    return None


# Get the project ID from environment - this is the billing project where queries run
project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
if not project_id:
    raise ValueError("GOOGLE_CLOUD_PROJECT environment variable must be set")

# Configure BigQuery toolset with compute_project_id for running jobs
# This forces all queries to execute in the specified project
bigquery_tool_config = BigQueryToolConfig(
    write_mode=WriteMode.BLOCKED,
    application_name="research-agent",
    location=os.getenv("BIGQUERY_LOCATION", "US"),
    compute_project_id=project_id,
    max_query_result_rows=5000,  # Match our hard LIMIT 5000 from prompts
)

bigquery_toolset = BigQueryToolset(
    tool_filter=[ADK_BUILTIN_BQ_EXECUTE_SQL_TOOL],
    # Let ADK handle credentials with proper OAuth scopes
    bigquery_tool_config=bigquery_tool_config
)

bigquery_agent = LlmAgent(
    model=config.bigquery_model,
    name="bigquery_agent",
    instruction=formatted_instructions,
    tools=[bigquery_toolset],
    after_tool_callback=store_results_in_context,
    generate_content_config=types.GenerateContentConfig(
        temperature=0.01,
        tool_config=types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode.ANY,
            )
        )
    ),
)


# Wrapper function for calling bigquery_agent as a tool
async def query_bigquery_data(
    question: str,
    run_pubmed: bool = True,
    run_patents: bool = True,
    tool_context: ToolContext = None,
) -> dict:
    """
    Query patent and article data from BigQuery.

    This tool retrieves structured data from:
    - Google Patents Public Datasets (patents-public-data)
    - PubMed Central Open Access Articles (bigquery-public-data)

    Use this tool when you need to:
    - Find patents by topic, assignee, inventor, or technology area
    - Find scientific articles by topic, author, or publication
    - Retrieve patent claims, citations, or legal status
    - Get publication metadata and statistics

    Args:
        question: Natural language query for data retrieval
        run_pubmed: Whether to search PubMed/PMC articles (default: True)
        run_patents: Whether to search patents (default: True)
        tool_context: The tool context

    Returns:
        Dictionary with:
            - status (str): "success" or "error"
            - message (str): Human-readable status message
            - source (str): Data source name (e.g., "Patents", "PubMed/PMC")
            - total_rows (int): Number of rows returned
            - metadata (dict): Query metadata (SQL, console link, etc.)
        Full results are stored in state.
    """
    logger.info(f"Querying BigQuery: {question} (run_pubmed={run_pubmed}, run_patents={run_patents})")

    # Check if both are disabled
    if not run_pubmed and not run_patents:
        return {
            "status": "skipped",
            "message": "Skipped BigQuery - both PubMed and Patents disabled",
            "source": "BigQuery",
            "total_rows": 0,
        }

    # Modify question to guide agent on what to search
    modified_question = question
    if run_pubmed and not run_patents:
        modified_question = f"{question}\n\nIMPORTANT: Search ONLY PubMed/PMC articles, do NOT search patents."
    elif run_patents and not run_pubmed:
        modified_question = f"{question}\n\nIMPORTANT: Search ONLY patents, do NOT search PubMed/PMC articles."

    try:
        from google.adk.tools.agent_tool import AgentTool

        agent_tool = AgentTool(agent=bigquery_agent)
        result = await agent_tool.run_async(
            args={"request": modified_question}, tool_context=tool_context
        )

        logger.info(f"BigQuery agent returned: {type(result)}, value: {result}")

        # The BigQuery sub-agent stores results directly in state automatically
        # via its after_tool_callback (see store_results_in_context above)
        # The callback sets skip_summarization=True to prevent context overflow

        # Determine which source was queried based on the run flags passed to this function
        if run_pubmed and not run_patents:
            last_source = "pubmed"
        elif run_patents and not run_pubmed:
            last_source = "patents"
        else:
            # Both or neither - use generic
            last_source = "bigquery"

        metadata = None
        source_name = "BigQuery"

        if last_source == "pubmed":
            metadata = tool_context.state.get(StateKeys.PUBMED_METADATA)
            source_name = "PubMed/PMC"
        elif last_source == "patents":
            metadata = tool_context.state.get(StateKeys.PATENTS_METADATA)
            source_name = "Patents"
        else:
            metadata = tool_context.state.get(StateKeys.BIGQUERY_METADATA)

        logger.info(f"Last source: {last_source}, Metadata exists: {metadata is not None}, Source: {source_name}")

        if metadata:
            total_rows = metadata.get("total_rows", 0)
            sql_preview = metadata.get('sql_query', 'N/A')
            if len(sql_preview) > 200:
                sql_preview = sql_preview[:200] + "..."

            return {
                "status": "success",
                "message": f"✓ {source_name} query executed successfully.",
                "source": source_name,
                "total_rows": total_rows,
                "metadata": {
                    "sql_query": sql_preview,
                    "console_link": metadata.get('console_link', 'N/A')
                },
                "details": f"Results are stored in state and will be used for synthesis."
            }
        else:
            # Fallback if no metadata
            preview = str(result)[:300] if result else "No results"
            return {
                "status": "success",
                "message": "✓ BigQuery query completed.",
                "source": source_name,
                "preview": preview
            }
    except Exception as e:
        logger.error(f"Error querying BigQuery agent: {e}", exc_info=True)
        return {
            "status": "error",
            "error_message": f"An error occurred while querying the BigQuery database: {str(e)}",
            "error_type": type(e).__name__
        }
