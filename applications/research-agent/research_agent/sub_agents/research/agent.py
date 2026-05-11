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

"""Research agents for web-based information gathering."""

import datetime
import logging
import re
import aiohttp
from collections.abc import AsyncGenerator
from typing import Literal

from google.adk.agents import BaseAgent, LlmAgent, SequentialAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.planners import BuiltInPlanner
from google.adk.tools import google_search, ToolContext
from google.genai import types as genai_types
from pydantic import BaseModel, Field

from ...config import config
from ...state_keys import StateKeys

logger = logging.getLogger(__name__)


async def _resolve_redirect_url(url: str) -> str:
    """Resolves Vertex AI grounding redirect URLs to their actual destination."""
    if not url:
        return url
        
    # Check if it's a known redirect URL pattern
    if "vertexaisearch.cloud.google.com" in url or "grounding-api-redirect" in url:
        try:
            # Use HEAD request to follow redirect without downloading body
            # Add User-Agent to avoid being blocked
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            # Increase timeout to 3s for reliability
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.head(url, allow_redirects=True, timeout=3.0) as response:
                    if response.status == 200:
                        final_url = str(response.url)
                        # Only return if it actually resolved to something different/cleaner
                        if "vertexaisearch" not in final_url and "grounding-api-redirect" not in final_url:
                            logger.info(f"Resolved URL: {url[:30]}... -> {final_url}")
                            return final_url
        except Exception as e:
            logger.warning(f"Failed to resolve URL {url}: {e}")
    
    return url


# --- Callbacks ---
async def collect_research_sources_callback(callback_context: CallbackContext) -> None:
    """Collects web research sources and their supported claims."""
    from ...state_keys import StateKeys
    import json

    session = callback_context._invocation_context.session
    url_to_short_id = callback_context.state.get(StateKeys.WEB_RESEARCH_URL_TO_ID, {})
    sources = callback_context.state.get(StateKeys.WEB_RESEARCH_SOURCES, {})
    id_counter = len(url_to_short_id) + 1

    # First pass: Collect clean URLs from google_search tool responses
    # Map title/domain -> clean_url to fix Vertex AI redirect links later
    clean_url_map = {}
    
    for event in session.events:
        # Check for tool/function responses
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.function_response:
                    resp = part.function_response
                    if resp.name == "google_search":
                        try:
                            # Response is usually a JSON string in 'content' or 'response' field
                            # Adjust based on exact ADK tool output structure
                            response_data = resp.response
                            if isinstance(response_data, dict) and "result" in response_data:
                                # Parse the JSON string result
                                results = json.loads(response_data["result"])
                                if isinstance(results, list):
                                    for item in results:
                                        if isinstance(item, dict):
                                            link = item.get("link") or item.get("url")
                                            title = item.get("title")
                                            source = item.get("source")
                                            
                                            if link:
                                                if title:
                                                    clean_url_map[title.lower()] = link
                                                if source:
                                                    clean_url_map[source.lower()] = link
                        except Exception as e:
                            logger.warning(f"Failed to parse google_search response: {e}")

    # Second pass: Process grounding metadata
    for event in session.events:
        if not (event.grounding_metadata and event.grounding_metadata.grounding_chunks):
            continue
        chunks_info = {}
        for idx, chunk in enumerate(event.grounding_metadata.grounding_chunks):
            if not chunk.web:
                continue
            
            raw_url = chunk.web.uri
            title = (
                chunk.web.title
                if chunk.web.title != chunk.web.domain
                else chunk.web.domain
            )
            
            # Try to find clean URL from our map
            final_url = raw_url
            if "vertexaisearch" in raw_url or "grounding-api-redirect" in raw_url:
                # Try matching by title
                if title and title.lower() in clean_url_map:
                    final_url = clean_url_map[title.lower()]
                    logger.info(f"Fixed URL for '{title}': {raw_url[:20]}... -> {final_url}")
                else:
                    # Fallback: Try to resolve the redirect URL directly
                    resolved_url = await _resolve_redirect_url(raw_url)
                    if resolved_url != raw_url:
                        final_url = resolved_url
            
            if final_url not in url_to_short_id:
                short_id = f"src-{id_counter}"
                url_to_short_id[final_url] = short_id
                sources[short_id] = {
                    "short_id": short_id,
                    "title": title,
                    "url": final_url,
                    "domain": chunk.web.domain,
                    "supported_claims": [],
                }
                id_counter += 1
            chunks_info[idx] = url_to_short_id[final_url]
            
        if event.grounding_metadata.grounding_supports:
            for support in event.grounding_metadata.grounding_supports:
                confidence_scores = support.confidence_scores or []
                chunk_indices = support.grounding_chunk_indices or []
                for i, chunk_idx in enumerate(chunk_indices):
                    if chunk_idx in chunks_info:
                        short_id = chunks_info[chunk_idx]
                        confidence = (
                            confidence_scores[i] if i < len(confidence_scores) else 0.5
                        )
                        text_segment = support.segment.text if support.segment else ""
                        sources[short_id]["supported_claims"].append(
                            {
                                "text_segment": text_segment,
                                "confidence": confidence,
                            }
                        )
    callback_context.state[StateKeys.WEB_RESEARCH_URL_TO_ID] = url_to_short_id
    callback_context.state[StateKeys.WEB_RESEARCH_SOURCES] = sources


def citation_replacement_callback(
    callback_context: CallbackContext,
) -> genai_types.Content:
    """Replaces citation tags with Markdown-formatted links and appends Web Sources section."""
    from ...state_keys import StateKeys

    final_report = callback_context.state.get(StateKeys.WEB_RESEARCH_FINAL_CITED_REPORT, "")
    sources = callback_context.state.get(StateKeys.WEB_RESEARCH_SOURCES, {})

    used_sources = set()

    def tag_replacer(match: re.Match) -> str:
        short_id = match.group(1)
        if not (source_info := sources.get(short_id)):
            logging.warning(f"Invalid citation tag found and removed: {match.group(0)}")
            return ""
        used_sources.add(short_id)
        display_text = source_info.get("title", source_info.get("domain", short_id))
        return f" [{display_text}]({source_info['url']})"

    processed_report = re.sub(
        r'<cite\s+source\s*=\s*["\']?\s*(src-\d+)\s*["\']?\s*/>',
        tag_replacer,
        final_report,
    )
    processed_report = re.sub(r"\s+([.,;:])", r"\1", processed_report)

    # Append Web Sources section if not already present (to avoid duplication if model wrote it)
    if used_sources and "### Web Sources" not in processed_report:
        processed_report += "\n\n### Web Sources\n"
        # Sort by short_id to keep order consistent
        for short_id in sorted(used_sources, key=lambda x: int(x.split('-')[1]) if '-' in x and x.split('-')[1].isdigit() else 0):
            source = sources[short_id]
            title = source.get("title", source.get("domain", "Source"))
            url = source.get("url", "#")
            processed_report += f"- [{title}]({url})\n"

    # Store the processed report with citations directly in WEB_RESEARCH_RESULTS
    callback_context.state[StateKeys.WEB_RESEARCH_RESULTS] = processed_report
    return genai_types.Content(parts=[genai_types.Part(text=processed_report)])


# --- AGENT DEFINITIONS ---
web_researcher = LlmAgent(
    model=config.worker_model,  # Use Flash for web searches
    name="web_researcher",
    description="Performs web research to supplement BigQuery data.",
    planner=BuiltInPlanner(
        thinking_config=genai_types.ThinkingConfig(include_thoughts=True)
    ),
    instruction=f"""
    You are a web research specialist that complements structured data from BigQuery
    with additional context from web sources.

    Your task:
    1. Review the research plan from '{StateKeys.WEB_RESEARCH_PLAN}' state
    2. Review any BigQuery data from 'bigquery_query_result' state (if available)
    3. Identify gaps that need web research (trends, context, recent developments)
    4. Execute 4-5 targeted web searches using google_search
    5. Synthesize findings into a comprehensive summary

    Current date: {datetime.datetime.now().strftime("%Y-%m-%d")}

    Focus on:
    - Recent developments and trends
    - Expert analysis and commentary
    - Connections between patents and research
    - Industry context and applications
    """,
    tools=[google_search],
    output_key=StateKeys.WEB_RESEARCH_FINDINGS,
    after_agent_callback=collect_research_sources_callback,
)

report_composer = LlmAgent(
    model=config.synthesis_model,  # Use Pro for report synthesis
    name="report_composer",
    include_contents="none",
    description="Composes final research report with citations.",
    instruction=f"""
    Create a comprehensive research report combining BigQuery data and web research.

    ### INPUT DATA
    * BigQuery Results: `{{+bigquery_query_result}}`
    * Web Research: `{{+{StateKeys.WEB_RESEARCH_FINDINGS}}}`
    * Citation Sources: `{{+{StateKeys.WEB_RESEARCH_SOURCES}}}`

    ### CITATION SYSTEM
    **CRITICAL: Use TWO citation formats:**

    1. **For BigQuery data** (patents/articles): Use inline format
       - Patents: [Patent:US-2024123456-A1]
       - Articles: [PMID:40123456](<PMC_LINK>) (Use PMC link from BigQuery data)

    2. **For web sources**: Use citation tags
       - `<cite source="src-ID_NUMBER" />` after claims from web sources

    ### REQUIRED STRUCTURE
    1. **Executive Summary**
    2. **Data Analysis** (from BigQuery with [Patent:...] and [Article:...] citations)
    3. **Contextual Research** (from web with <cite> tags)
    4. **Key Findings**
    5. **Recommendations**
    6. **Sources** - MUST include at the end:
       - List ALL patents cited: [Patent:US-2024123456-A1] = "Patent Title"
       - List ALL articles cited: [PMID:40123456](<PMC_LINK>) (Use PMC link from BigQuery data) = "Article Title"
       - List ALL web sources: **DO NOT LIST HERE**. The system will automatically append the "Web Sources" section based on the tags you used.

    ### EXAMPLE OUTPUT FORMAT
    ```
    ## Executive Summary
    The mRNA vaccine landscape shows 145 patents [Patent:US-2024123456-A1] with
    Moderna leading at 34 patents [Patent:WO-2024567890-A1]. Recent trials show
    45% response rates [PMID:40123456](<PMC_LINK>) (Use PMC link from BigQuery data) <cite source="src-1" />.

    ...

    ## Sources

    ### Patents Cited
    - [Patent:US-2024123456-A1] - "Novel battery systems based on two-additive electrolyte systems"
    - [Patent:WO-2024567890-A1] - "Lipid nanoparticle formulations for mRNA vaccines"

    ### Scientific Articles Cited
    - [PMID:40123456](<PMC_LINK>) (Use PMC link from BigQuery data) - "mRNA-based cancer vaccines: mechanisms and clinical applications"

    ### Web Sources
    (This section will be auto-generated by the system)
    ```

    Note: If BigQuery data is not available, focus on web research findings only.
    All citations must be in-line. Sources section is MANDATORY at the end for Patents and Articles only.
    """,
    output_key=StateKeys.WEB_RESEARCH_FINAL_CITED_REPORT,
    after_agent_callback=citation_replacement_callback,
)

# Main research pipeline
research_pipeline = SequentialAgent(
    name="research_pipeline",
    description="Executes web research and report composition.",
    sub_agents=[
        web_researcher,
        report_composer,
    ],
)


# Wrapper function for calling research_pipeline as a tool
async def perform_web_research(
    question: str,
    run_web_research: bool = True,
    tool_context: ToolContext = None,
) -> dict:
    """
    Perform web research using Google Search to supplement BigQuery data.

    This tool executes a research pipeline that:
    - Conducts targeted Google web searches
    - Evaluates research quality iteratively
    - Synthesizes findings with citations
    - Generates a comprehensive report

    Use this tool when you need to:
    - Get recent trends and developments (via Google Search)
    - Find expert analysis and commentary from the web
    - Understand industry context and real-world applications
    - Discover recent news, blog posts, and articles

    Args:
        question: The research question to execute
        run_web_research: Whether to run web research (default: True)
        tool_context: The tool context

    Returns:
        Dictionary with:
            - status (str): "success" or "error"
            - message (str): Human-readable status message
            - preview (str): Preview of results (first 500 chars)
        Full results are stored in state under 'web_research_results' key.
    """
    from google.adk.tools.agent_tool import AgentTool

    logger.info(f"Performing web research: {question} (run_web_research={run_web_research})")

    # Check if disabled
    if not run_web_research:
        return {
            "status": "skipped",
            "message": "Skipped Web Research - disabled by research plan",
            "preview": "",
        }

    try:
        # Make BigQuery results available to research pipeline
        if StateKeys.BIGQUERY_RESULTS in tool_context.state:
            tool_context.state[StateKeys.WEB_RESEARCH_PLAN] = (
                f"{question}\n\nBigQuery data is available in state under key '{StateKeys.BIGQUERY_RESULTS}'."
            )
        else:
            tool_context.state[StateKeys.WEB_RESEARCH_PLAN] = question

        agent_tool = AgentTool(agent=research_pipeline)
        result = await agent_tool.run_async(
            args={"request": question}, tool_context=tool_context
        )

        # Store full results in state (already stored by citation_replacement_callback)
        # Get the processed report from state, or use the raw result as fallback
        final_report = tool_context.state.get(StateKeys.WEB_RESEARCH_RESULTS, result)
        
        # Count sources found
        sources = tool_context.state.get(StateKeys.WEB_RESEARCH_SOURCES, {})
        source_count = len(sources)

        # Return brief summary
        result_preview = final_report[:500] + "..." if len(final_report) > 500 else final_report
        return {
            "status": "success",
            "message": f"✓ Web research completed successfully. Found {source_count} relevant web sources.",
            "count": source_count,
            "preview": result_preview,
            "details": f"Full results stored in state under key '{StateKeys.WEB_RESEARCH_RESULTS}'."
        }
    except Exception as e:
        logger.error(f"Error performing web research: {e}", exc_info=True)
        return {
            "status": "error",
            "error_message": f"An error occurred while performing web research: {str(e)}",
            "error_type": type(e).__name__
        }
