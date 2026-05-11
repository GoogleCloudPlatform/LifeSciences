"""Synthesis Agent - Creates comprehensive research reports using Pro model."""

import datetime
import logging
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from ...config import config
from ...state_keys import StateKeys

logger = logging.getLogger(__name__)


def _clean_text_for_citation(text: str) -> str:
    """
    Clean text by removing newlines, carriage returns, and extra whitespace.
    Used to ensure citations stay on a single line.
    """
    if not text:
        return text
    # Replace newlines and carriage returns with spaces
    cleaned = text.replace('\n', ' ').replace('\r', ' ')
    # Replace multiple spaces with single space
    cleaned = ' '.join(cleaned.split())
    return cleaned


def _clean_results_data(results: list) -> list:
    """
    Clean results data by removing newlines from title and author fields.
    This ensures citations in the final report stay on single lines.
    """
    if not results or not isinstance(results, list):
        return results

    cleaned_results = []
    for item in results:
        if isinstance(item, dict):
            cleaned_item = item.copy()
            # Clean common fields that appear in citations
            if 'title' in cleaned_item:
                cleaned_item['title'] = _clean_text_for_citation(cleaned_item['title'])
            if 'author' in cleaned_item:
                cleaned_item['author'] = _clean_text_for_citation(cleaned_item['author'])
            cleaned_results.append(cleaned_item)
        else:
            cleaned_results.append(item)

    return cleaned_results


# Load synthesis instructions
_synthesis_instructions_path = Path(__file__).parent / "instructions.md"
_synthesis_instructions = _synthesis_instructions_path.read_text()

# Create the synthesis agent (simple LlmAgent with no callbacks)
synthesis_agent = LlmAgent(
    model=Gemini(
        model=config.synthesis_model,  # Uses gemini-2.5-pro
        retry_options=types.HttpRetryOptions(
            attempts=config.max_retry_count,
            exp_base=config.delay_multiplier,
            initial_delay=config.initial_retry_delay
        )
    ),
    name="synthesis_agent",
    instruction=f"""{_synthesis_instructions}

Current date: {datetime.datetime.now().strftime("%Y-%m-%d")}
""",
)


# Wrapper function that prepares data and calls the synthesis agent
async def synthesize_research_report(
    request: str,
    tool_context: ToolContext,
) -> dict:
    """
    Synthesize a comprehensive research report from all available data sources.

    This tool creates a comprehensive report by:
    1. Reading data from all available sources (BigQuery, ClinicalTrials, Web Research)
    2. Using the synthesis_agent (Pro model) to integrate findings
    3. Adding footer with original query, date, and research plan
    4. Saving full report as artifact (research_report.md)
    5. Creating a summary for display in the chat
    6. Storing the summary in state

    Args:
        request: Description of what to synthesize
        tool_context: The tool context

    Returns:
        Dictionary with:
            - status (str): "success" or "error"
            - message (str): Human-readable status message
            - report_length (int): Length of the full report in characters
            - artifact (str): Artifact filename where full report is saved
            - artifact_version (int): Version number of the artifact
            - next_step (str): Instructions for displaying the summary
        Summary is stored in state under 'synthesis_summary' key.
    """
    logger.info(f"Starting synthesis: {request}")

    try:
        # Build BigQuery data sections
        bigquery_data_sections = []

        pubmed_results = tool_context.state.get(StateKeys.PUBMED_RESULTS)
        pubmed_metadata = tool_context.state.get(StateKeys.PUBMED_METADATA)
        # Clean pubmed results to remove newlines from titles/authors
        if pubmed_results:
            pubmed_results = _clean_results_data(pubmed_results)
        if pubmed_results and pubmed_metadata:
            pubmed_section = f"""
## PubMed/PMC Articles

**Total Articles:** {pubmed_metadata.get('total_rows', 0)}

**Data:**
{pubmed_results}
"""
            bigquery_data_sections.append(pubmed_section)

        patents_results = tool_context.state.get(StateKeys.PATENTS_RESULTS)
        patents_metadata = tool_context.state.get(StateKeys.PATENTS_METADATA)
        # Clean patents results to remove newlines from titles
        if patents_results:
            patents_results = _clean_results_data(patents_results)
        if patents_results and patents_metadata:
            patents_section = f"""
## Patent Data

**Total Patents:** {patents_metadata.get('total_rows', 0)}

**Data:**
{patents_results}
"""
            bigquery_data_sections.append(patents_section)

        bigquery_data = "\n".join(bigquery_data_sections) if bigquery_data_sections else "No BigQuery data available"

        # Build Clinical Trials section
        clinical_trials_results = tool_context.state.get(StateKeys.CLINICAL_TRIALS_RESULTS)
        clinical_trials_metadata = tool_context.state.get("clinical_trials_metadata")

        if clinical_trials_results and clinical_trials_metadata:
            clinical_trials_data = f"""
**Total Trials:** {clinical_trials_metadata.get('fetched_count', 0)} (of {clinical_trials_metadata.get('total_count', 0)} total)

**Data:**
{clinical_trials_results}
"""
        else:
            clinical_trials_data = "No clinical trials data available"

        # Use FINAL_CITED_REPORT which has clean citations, not raw results with Vertex AI URLs
        web_research_data = tool_context.state.get(StateKeys.WEB_RESEARCH_FINAL_CITED_REPORT, "No web research data available")

        # Get Refinement Data
        refinement_data = tool_context.state.get(StateKeys.REFINEMENT_RESULTS, "No refinement data available")
        
        # Get Research Evaluation (Quality Grade)
        research_evaluation = tool_context.state.get(StateKeys.RESEARCH_EVALUATION, {})
        grade = research_evaluation.get("grade", "N/A").upper() if isinstance(research_evaluation, dict) else "N/A"

        # Build metadata summary
        metadata_summary = []
        if pubmed_metadata:
            metadata_summary.append(f"- **PubMed/PMC Articles**: {pubmed_metadata.get('total_rows', 0)} articles analyzed")
        if patents_metadata:
            metadata_summary.append(f"- **Patents**: {patents_metadata.get('total_rows', 0)} patents analyzed")
        if clinical_trials_metadata:
            metadata_summary.append(f"- **Clinical Trials**: {clinical_trials_metadata.get('fetched_count', 0)} trials analyzed")
        if web_research_data != "No web research data available":
            metadata_summary.append("- **Web Research**: Data available")
        if refinement_data != "No refinement data available":
            metadata_summary.append("- **Refinement Search**: Follow-up data available")
        
        metadata_summary.append(f"- **Research Quality Grade**: {grade}")

        metadata_summary_text = "\n".join(metadata_summary) if metadata_summary else "No data sources were queried"

        # Build available sources - EACH SOURCE AS SEPARATE TOP-LEVEL SECTION
        # Order: PubMed, Patents, Clinical Trials, Web Research, Refinement
        available_sources = []

        # Add PubMed as its own section (not nested under BigQuery)
        if pubmed_results and pubmed_metadata:
            pubmed_source = f"""## PubMed/PMC Articles Data

**Total Articles:** {pubmed_metadata.get('total_rows', 0)}

**Data:**
{pubmed_results}
"""
            available_sources.append(pubmed_source)

        # Add Patents as its own section (not nested under BigQuery)
        if patents_results and patents_metadata:
            patents_source = f"""## Patent Data

**Total Patents:** {patents_metadata.get('total_rows', 0)}

**Data:**
{patents_results}
"""
            available_sources.append(patents_source)

        # Add Clinical Trials as its own section
        if clinical_trials_data != "No clinical trials data available":
            available_sources.append("## Clinical Trials Data\n" + clinical_trials_data)

        # Add Web Research as its own section
        if web_research_data != "No web research data available":
            available_sources.append("## Web Research Data\n" + web_research_data)

        # Add Refinement Data as its own section
        if refinement_data != "No refinement data available":
            available_sources.append("## Refinement (Deep Search) Data\n" + refinement_data)

        # Add Web Sources Map for accurate citation lookup
        web_sources_raw = tool_context.state.get(StateKeys.WEB_RESEARCH_SOURCES, {})
        if web_sources_raw:
            web_sources_list = []
            for key, val in web_sources_raw.items():
                web_sources_list.append(f"- {key}: {val.get('title')} ({val.get('url')})")
            web_sources_text = "\n".join(web_sources_list)
            available_sources.append(f"## Web Research Sources Map (Use these URLs for citations)\n{web_sources_text}")

        sources_text = "\n\n".join(available_sources) if available_sources else "No data sources available"

        # Build the enhanced request
        enhanced_request = f"""
## Request
{request}

## Dataset Summary
You are synthesizing data from the following sources:

{metadata_summary_text}

**CRITICAL INSTRUCTIONS FOR BALANCED ANALYSIS:**
1. Analyze ALL data sources with EQUAL depth and detail
2. Each source (PubMed, Patents, Clinical Trials, Web) must receive comparable analysis in your report
3. Extract specific insights, patterns, and trends from EACH source
4. Your "Key Findings" section should draw from ALL sources proportionally

**CRITICAL INSTRUCTIONS FOR DATA SOURCES SECTION:**
1. In your "Data Sources" section, ONLY mention the data sources listed above that actually have data
2. DO NOT mention or speculate about data sources that show "No data available"
3. DO NOT say "No data was available from X" unless specifically relevant to the analysis
4. Focus your data sources description on what WAS analyzed, not what was NOT searched

## Available Data

{sources_text}

---

Now synthesize a comprehensive report integrating ALL the available data sources with EQUAL analysis depth.
"""

        # Call the synthesis agent
        agent_tool = AgentTool(agent=synthesis_agent)
        final_report = await agent_tool.run_async(
            args={"request": enhanced_request},
            tool_context=tool_context
        )

        # Add footer with metadata
        research_plan = tool_context.state.get(StateKeys.RESEARCH_PLAN, "Not specified")

        footer = f"""

---

## Report Metadata

**Research Plan:** {research_plan}
**Generated:** {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

**Data Sources & Queries:**
"""

        # Add specific questions and details for each data source
        if tool_context.state.get(StateKeys.RESEARCH_PLAN_PUBMED_RUN):
            pubmed_q = tool_context.state.get(StateKeys.RESEARCH_PLAN_PUBMED_QUESTION, "N/A")
            count = pubmed_metadata.get('total_rows', 0) if pubmed_metadata else 0
            sql = pubmed_metadata.get('sql_query', 'N/A') if pubmed_metadata else 'N/A'
            footer += f"\n### PubMed/PMC Articles\n- **Question:** {pubmed_q}\n- **Results Found:** {count}\n- **SQL Query:**\n```sql\n{sql}\n```\n"

        if tool_context.state.get(StateKeys.RESEARCH_PLAN_PATENTS_RUN):
            patents_q = tool_context.state.get(StateKeys.RESEARCH_PLAN_PATENTS_QUESTION, "N/A")
            count = patents_metadata.get('total_rows', 0) if patents_metadata else 0
            sql = patents_metadata.get('sql_query', 'N/A') if patents_metadata else 'N/A'
            footer += f"\n### Patents\n- **Question:** {patents_q}\n- **Results Found:** {count}\n- **SQL Query:**\n```sql\n{sql}\n```\n"

        if tool_context.state.get(StateKeys.RESEARCH_PLAN_CLINICAL_TRIALS_RUN):
            trials_q = tool_context.state.get(StateKeys.RESEARCH_PLAN_CLINICAL_TRIALS_QUESTION, "N/A")
            count = clinical_trials_metadata.get('fetched_count', 0) if clinical_trials_metadata else 0
            total = clinical_trials_metadata.get('total_count', 'Unknown') if clinical_trials_metadata else 'Unknown'
            params = clinical_trials_metadata.get('search_params', {}) if clinical_trials_metadata else {}
            footer += f"\n### Clinical Trials\n- **Question:** {trials_q}\n- **Results Fetched:** {count} (Total Available: {total})\n- **Search Parameters:** `{params}`\n"

        if tool_context.state.get(StateKeys.RESEARCH_PLAN_WEB_RESEARCH_RUN):
            web_q = tool_context.state.get(StateKeys.RESEARCH_PLAN_WEB_RESEARCH_QUESTION, "N/A")
            web_sources = tool_context.state.get(StateKeys.WEB_RESEARCH_SOURCES, {})
            web_count = len(web_sources)
            footer += f"\n### Web Research\n- **Question:** {web_q}\n- **Results Found:** {web_count}\n"

        # Add Refinement History if available
        refinement_history = tool_context.state.get(StateKeys.REFINEMENT_HISTORY, [])
        if refinement_history:
            footer += "\n### Research Plan Refinement History\n"
            for item in refinement_history:
                footer += f"- **Iteration {item.get('iteration')} ({item.get('source')}):** {item.get('question')}\n"

        final_report = final_report + footer

        logger.info(f"✓ Synthesis complete. Report generated (length: {len(final_report)} chars)")

        # Import here to avoid circular dependency
        from ..display.agent import _display_agent

        # Always save full report as artifact
        filename = "research_report.md"
        report_artifact = types.Part.from_bytes(
            data=final_report.encode('utf-8'),
            mime_type="text/markdown"
        )

        version = await tool_context.save_artifact(filename=filename, artifact=report_artifact)
        logger.info(f"✓ Saved full report as artifact: {filename} (version {version})")

        # Create summary using internal display agent (Pro model)
        
        # Build trusted web sources string from state for accuracy
        trusted_web_sources = "No web sources found in state."
        ws_state = tool_context.state.get(StateKeys.WEB_RESEARCH_SOURCES, {})
        if ws_state:
            ws_lines = []
            for _, val in ws_state.items():
                title = val.get('title', 'Source')
                url = val.get('url', '#')
                ws_lines.append(f"- [{title}]({url})")
            trusted_web_sources = "\n".join(ws_lines)

        display_request = f"""Create a comprehensive summary of this research report.

Keep the summary under 4000 characters so the Flash model can display it.

Include:
- Original title
- Key findings with CLICKABLE inline citations (include patents, articles, AND web sources)
- What data was analyzed
- Major conclusions

**CRITICAL - Citation Format (MANDATORY - NEVER SKIP THE URL):**

When you copy a citation from the full report, you MUST include BOTH parts:
1. The [[Type:ID]] wrapper
2. The (URL) that comes immediately after it

**CRITICAL - Web Source Citation Format:**
Web sources must be formatted as: `[Source Title](URL)`

**TRUSTED WEB SOURCES (Use these EXACT Links):**
{trusted_web_sources}

- **VERIFY** all web citations against the list above. Confirm that any URL you use comes directly from this trusted list.

**CORRECT citation copying:**
If you see: [[Trial:NCT04629950](https://clinicaltrials.gov/study/NCT04629950)]
You MUST write: [[Trial:NCT04629950](https://clinicaltrials.gov/study/NCT04629950)]

If you see: [[Patent:CN119386181A](https://patents.google.com/patent/CN119386181A)]
You MUST write: [[Patent:CN119386181A](https://patents.google.com/patent/CN119386181A)]

If you see: [Asthma Market Size](https://www.mordorintelligence.com/...)
You MUST write: [Asthma Market Size](https://www.mordorintelligence.com/...)

**FORBIDDEN - These are ALL WRONG:**
- ❌ [[Trial:NCT04629950]] (MISSING URL - this is NOT clickable!)
- ❌ [[Patent:CN119386181A]] (MISSING URL - this is NOT clickable!)
- ❌ [Patent:US-20220202796-A1] (missing double brackets AND URL)
- ❌ NCT04629950 (no markdown link at all)
- ❌ [Source 1](https://...) (Generic title)

**REMEMBER**: The format is ALWAYS `[[Type:ID](URL)]` or `[Title](URL)` - you cannot omit the `(URL)` part!

Copy the COMPLETE citation including the URL in parentheses!

At the end, add: "Full detailed report ({len(final_report):,} characters) available in artifacts: `{filename}` (version {version})"

---BEGIN FULL REPORT---
{final_report}
---END FULL REPORT---"""

        agent_tool_display = AgentTool(agent=_display_agent)
        summary_for_display = await agent_tool_display.run_async(
            args={"request": display_request},
            tool_context=tool_context
        )

        # Store summary in state
        tool_context.state[StateKeys.SYNTHESIS_SUMMARY] = summary_for_display
        logger.info(f"✓ Summary created and stored in state (length: {len(summary_for_display)} chars)")

        # Return message to root agent
        return {
            "status": "success",
            "message": "✓ Research report complete!",
            "report_length": len(final_report),
            "artifact": filename,
            "artifact_version": version,
            "next_step": f"Use display_content to show the summary from state['{StateKeys.SYNTHESIS_SUMMARY}']"
        }

    except Exception as e:
        logger.error(f"Error in synthesis: {e}", exc_info=True)
        return {
            "status": "error",
            "error_message": f"An error occurred during synthesis: {str(e)}",
            "error_type": type(e).__name__
        }
