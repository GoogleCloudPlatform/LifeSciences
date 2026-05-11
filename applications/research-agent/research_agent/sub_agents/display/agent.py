"""Display agent for showing reports, tables, and item details."""

import logging

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.tools import ToolContext
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


def _format_table_preview(data_source: str, results: list, metadata: dict) -> str:
    """Helper to format a small preview of results as a table (for summaries)."""
    if not results:
        return f"❌ No {data_source} data found."

    source_names = {
        "patents": "Patents",
        "pubmed": "PubMed/PMC Articles",
        "clinical_trials": "Clinical Trials"
    }

    display_name = source_names.get(data_source, data_source)

    # Key columns
    key_columns_map = {
        "pubmed": ["id", "title", "link"],
        "patents": ["publication_number", "title", "publication_date"],
        "clinical_trials": ["nct_id", "title", "status"],
    }

    columns = key_columns_map.get(data_source, list(results[0].keys())[:3] if results else [])

    # Build small table
    table_lines = []
    table_lines.append("| # | " + " | ".join(columns) + " |")
    table_lines.append("|" + "|".join(["---"] * (len(columns) + 1)) + "|")

    for idx, row in enumerate(results, 1):
        values = []
        for key in columns:
            val = str(row.get(key, ""))

            if data_source == "pubmed":
                if key == "id":
                    pmc_id = row.get('pmc_id')
                    pmc_link = row.get('pmc_link')
                    if pmc_id:
                        link = pmc_link if pmc_link else f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}"
                        val = f"[PMC:{pmc_id}]({link})"
                    else:
                        val = "N/A"
                elif key == "link":
                    pmc_link = row.get('pmc_link')
                    if pmc_link:
                        val = f"[Link]({pmc_link})"
                    else:
                        val = "N/A"

            elif data_source == "clinical_trials":
                if key == "nct_id":
                    nct_id = row.get('nct_id')
                    trial_url = row.get('trial_url')
                    if nct_id and trial_url:
                        val = f"[{nct_id}]({trial_url})"
                    elif nct_id:
                        val = nct_id
                    else:
                        val = "N/A"

            # Escape pipe characters
            val = val.replace("|", "\\|")
            # Limit to 60 chars for preview
            values.append(val[:60] + ("..." if len(val) > 60 else ""))
        table_lines.append(f"| {idx} | " + " | ".join(values) + " |")

    return "\n".join(table_lines)


def _format_table(data_source: str, results: list, metadata: dict) -> str:
    """Helper to format full results as a table (for artifacts)."""
    if not results:
        return f"❌ No {data_source} data found."

    source_names = {
        "patents": "Patents",
        "pubmed": "PubMed/PMC Articles",
        "clinical_trials": "Clinical Trials"
    }

    display_name = source_names.get(data_source, data_source)
    total_count = len(results)

    # Key columns
    key_columns_map = {
        "pubmed": ["id", "title", "author", "link"],
        "patents": ["publication_number", "title", "publication_date"],
        "clinical_trials": ["nct_id", "title", "status"],
    }

    columns = key_columns_map.get(data_source, list(results[0].keys())[:4] if results else [])

    # Build table
    table_lines = [f"# {display_name}\n"]
    table_lines.append(f"**Total rows:** {total_count}\n")
    table_lines.append("| # | " + " | ".join(columns) + " |")
    table_lines.append("|" + "|".join(["---"] * (len(columns) + 1)) + "|")

    for idx, row in enumerate(results, 1):
        values = []
        for key in columns:
            val = str(row.get(key, ""))

            if data_source == "pubmed":
                if key == "id":
                    pmc_id = row.get('pmc_id')
                    pmc_link = row.get('pmc_link')
                    if pmc_id:
                        link = pmc_link if pmc_link else f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}"
                        val = f"[PMC:{pmc_id}]({link})"
                    else:
                        val = "N/A"
                elif key == "link":
                    pmc_link = row.get('pmc_link')
                    if pmc_link:
                        val = f"[Link]({pmc_link})"
                    else:
                        val = "N/A"
                # Truncate long title/author to prevent line wrapping
                elif key == "title" and len(val) > 80:
                    val = val[:77] + "..."
                elif key == "author" and len(val) > 60:
                    val = val[:57] + "..."

            elif data_source == "clinical_trials":
                if key == "nct_id":
                    nct_id = row.get('nct_id')
                    trial_url = row.get('trial_url')
                    if nct_id and trial_url:
                        val = f"[{nct_id}]({trial_url})"
                    elif nct_id:
                        val = nct_id
                    else:
                        val = "N/A"
                # Truncate long titles
                elif key == "title" and len(val) > 80:
                    val = val[:77] + "..."

            elif data_source == "patents":
                # Truncate long titles
                if key == "title" and len(val) > 80:
                    val = val[:77] + "..."

            # Escape pipe characters that would break markdown tables
            val = val.replace("|", "\\|")
            # Replace newlines with spaces to keep everything on one line
            val = val.replace("\n", " ").replace("\r", " ")
            values.append(val)
        table_lines.append(f"| {idx} | " + " | ".join(values) + " |")

    return "\n".join(table_lines)


# Create the display agent using Pro model
_display_agent = LlmAgent(
    model=Gemini(
        model=config.synthesis_model,  # Use Pro model for better instruction following
        retry_options=types.HttpRetryOptions(
            attempts=config.max_retry_count,
            exp_base=config.delay_multiplier,
            initial_delay=config.initial_retry_delay
        )
    ),
    name="display_agent",
    instruction="""
    You are a Display Agent. Your job is simple: output content EXACTLY as provided.

    When given content between ---BEGIN CONTENT--- and ---END CONTENT--- markers:
    1. Output the content verbatim
    2. Do NOT add any text before or after
    3. Do NOT summarize or modify the content
    4. Do NOT add explanations

    For summarization requests (when given full report with instructions):
    - Follow the specific instructions provided
    - Create summaries as requested
    - **CRITICAL**: ALWAYS preserve the COMPLETE clickable citation format from the original text
    - **NEVER** remove URLs or shorten citation markdown syntax

    **Citation Format Preservation (MANDATORY):**
    When you encounter citations in the source material, you MUST preserve them EXACTLY:
    - ✅ PRESERVE: [[Article:PMC7468408](https://pmc.ncbi.nlm.nih.gov/articles/PMC7468408/)]
    - ✅ PRESERVE: [[Patent:US20240123456A1](https://patents.google.com/patent/US20240123456A1)]
    - ✅ PRESERVE: [[Trial:NCT03372603](https://clinicaltrials.gov/study/NCT03372603)]
    - ❌ NEVER DO: [[Article:PMC7468408]] (missing URL)
    - ❌ NEVER DO: [Article:PMC7468408] (wrong format)
    - ❌ NEVER DO: PMC7468408 (no link at all)

    **When summarizing, copy citations character-for-character from the original - do NOT reconstruct them.**

    Your primary mode is VERBATIM OUTPUT - only create summaries when explicitly instructed.
    """,
    generate_content_config=types.GenerateContentConfig(
        temperature=0.1,  # Slightly higher temperature for better instruction following
    ),
)


# Wrapper function that fetches content from state and displays it
async def display_content(
    request: str,
    tool_context: ToolContext,
) -> dict:
    """
    Display content from state to the user.

    This tool retrieves content from state and displays it to the user.
    For large tables, saves full data to artifacts and returns a summary.
    For summarization requests, generates analytical summaries of the data.

    Examples:
    - "Show the report from state['synthesis_summary']"
    - "Show a table of patents from state['patents_results']"
    - "Summarize the patents collected"
    - "Analyze the clinical trials data"
    - "What are the key themes in the PubMed articles?"

    Args:
        request: Description of what to display
        tool_context: The tool context

    Returns:
        Dictionary with 'status' and 'content' keys. Status is 'success' or 'error'.
        The 'content' key contains the formatted content to display to the user.
    """
    import logging
    from google.adk.tools.agent_tool import AgentTool

    logger = logging.getLogger(__name__)
    logger.info(f"Display request: {request}")

    request_lower = request.lower()
    content_to_display = None

    # Check if this is a summarization/analysis request
    is_summarization = any(keyword in request_lower for keyword in [
        "summarize", "summary", "analyze", "analysis", "key themes",
        "key findings", "insights", "trends", "what are", "tell me about"
    ])

    # Parse the request and fetch from state
    if StateKeys.SYNTHESIS_SUMMARY in request_lower or "synthesis_summary" in request_lower or "show the report" in request_lower:
        # Get report from state
        content_to_display = tool_context.state.get(StateKeys.SYNTHESIS_SUMMARY)
        logger.info(f"📋 Retrieved synthesis summary from state (length: {len(content_to_display) if content_to_display else 0} chars)")

    elif "patents" in request_lower or "patent" in request_lower:
        # Get patents data
        results = tool_context.state.get(StateKeys.PATENTS_RESULTS)
        metadata = tool_context.state.get(StateKeys.PATENTS_METADATA)

        if results:
            if is_summarization:
                # Generate analytical summary using display agent
                logger.info(f"📊 Generating analytical summary of {len(results)} patents")

                # Prepare data for summarization
                patents_data = ""
                for idx, patent in enumerate(results, 1):
                    # Clean title to remove newlines
                    title = _clean_text_for_citation(patent.get('title', 'N/A'))

                    patents_data += f"\n{idx}. **{title}**\n"
                    patents_data += f"   - Publication Number: {patent.get('publication_number', 'N/A')}\n"
                    patents_data += f"   - Date: {patent.get('publication_date', 'N/A')}\n"
                    if patent.get('abstract'):
                        patents_data += f"   - Abstract: {patent.get('abstract', 'N/A')[:200]}...\n"

                summarization_prompt = f"""Analyze and summarize the following {len(results)} patents.

Provide:
1. **Key Themes**: What are the main technological areas or innovations covered?
2. **Key Findings**: What are the most significant or interesting patents?
3. **Trends**: Any patterns in publication dates, assignees, or technology areas?
4. **Notable Patents**: Highlight 2-3 most relevant or impactful patents with [Patent:...] citations

Keep the summary under 2000 characters.

**Patents Data:**
{patents_data}"""

                agent_tool = AgentTool(agent=_display_agent)
                analytical_summary = await agent_tool.run_async(
                    args={"request": summarization_prompt},
                    tool_context=tool_context
                )

                total_rows = metadata.get('total_rows', len(results)) if metadata else len(results)
                content_to_display = f"""## Patents Analysis

**Total patents analyzed:** {total_rows}
**Data source:** BigQuery Patents Public Dataset

{analytical_summary}

*Full patents table available - use "show patents table" to view all details.*"""

            else:
                # Show table preview (original behavior)
                full_table = _format_table("patents", results, metadata)
                logger.info(f"📋 Formatted patents table ({len(results)} rows, {len(full_table)} chars)")

                # Save full table to artifacts
                filename = "patents_table.md"
                table_artifact = types.Part.from_bytes(
                    data=full_table.encode('utf-8'),
                    mime_type="text/markdown"
                )
                version = await tool_context.save_artifact(filename=filename, artifact=table_artifact)
                logger.info(f"✓ Saved full patents table to artifacts: {filename} (version {version})")

                # Return summary with link to artifact
                total_rows = metadata.get('total_rows', len(results)) if metadata else len(results)
                content_to_display = f"""## Patents Data Summary

**Total patents:** {total_rows} rows
**Data source:** BigQuery Patents Public Dataset

The complete patents table has been saved to artifacts for your review.

**Download:** `{filename}` (version {version})

**Preview of first 5 patents:**

{_format_table_preview("patents", results[:5], metadata)}

*Full table with all {len(results)} rows available in artifacts.*"""

    elif "pubmed" in request_lower or "article" in request_lower:
        # Get articles data
        results = tool_context.state.get(StateKeys.PUBMED_RESULTS)
        metadata = tool_context.state.get(StateKeys.PUBMED_METADATA)

        if results:
            if is_summarization:
                # Generate analytical summary using display agent
                logger.info(f"📊 Generating analytical summary of {len(results)} PubMed articles")

                # Prepare data for summarization
                articles_data = ""
                for idx, article in enumerate(results, 1):
                    # Clean title and author to remove newlines
                    title = _clean_text_for_citation(article.get('title', 'N/A'))
                    author = _clean_text_for_citation(article.get('author', 'N/A'))

                    articles_data += f"\n{idx}. **{title}**\n"

                    pmc_id = article.get('pmc_id')
                    pmc_link = article.get('pmc_link')

                    if pmc_id:
                        article_id = f"PMC: {pmc_id}"
                        article_link = pmc_link if pmc_link else f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}"
                    else:
                        article_id = "ID: N/A"
                        article_link = None

                    articles_data += f"   - {article_id}\n"
                    if article_link:
                        articles_data += f"   - Link: {article_link}\n"
                    articles_data += f"   - Author: {author}\n"
                    articles_data += f"   - Date: {article.get('last_updated', 'N/A')}\n"
                    if article.get('abstract'):
                        articles_data += f"   - Abstract: {article.get('abstract', 'N/A')[:200]}...\n"

                summarization_prompt = f"""Analyze and summarize the following {len(results)} PubMed/PMC articles.

Provide:
1. **Key Themes**: What are the main research topics or focus areas?
2. **Key Findings**: What are the most significant or relevant articles?
3. **Research Trends**: Any patterns in publication dates, authors, or methodologies?
4. **Notable Articles**: Highlight 2-3 most relevant articles with CLICKABLE citations

**CRITICAL - Citation Format:**
When citing articles, you MUST use clickable markdown links with PMC IDs:
- Format: [[Article:PMC12345](pmc_link_from_data)]
- Use the PMC ID and pmc_link from the article data provided above
- DO NOT use PMID - only use PMC IDs

Example: "Recent research shows 45% response rates [[Article:PMC7468408](https://pmc.ncbi.nlm.nih.gov/articles/PMC7468408/)]"

Keep the summary under 2000 characters.

**Articles Data:**
{articles_data}"""

                agent_tool = AgentTool(agent=_display_agent)
                analytical_summary = await agent_tool.run_async(
                    args={"request": summarization_prompt},
                    tool_context=tool_context
                )

                total_rows = metadata.get('total_rows', len(results)) if metadata else len(results)
                content_to_display = f"""## PubMed/PMC Articles Analysis

**Total articles analyzed:** {total_rows}
**Data source:** BigQuery PubMed Central Dataset

{analytical_summary}

*Full articles table available - use "show articles table" to view all details.*"""

            else:
                # Show table preview (original behavior)
                full_table = _format_table("pubmed", results, metadata)
                logger.info(f"📋 Formatted articles table ({len(results)} rows, {len(full_table)} chars)")

                # Save full table to artifacts
                filename = "pubmed_table.md"
                table_artifact = types.Part.from_bytes(
                    data=full_table.encode('utf-8'),
                    mime_type="text/markdown"
                )
                version = await tool_context.save_artifact(filename=filename, artifact=table_artifact)
                logger.info(f"✓ Saved full PubMed table to artifacts: {filename} (version {version})")

                # Return summary with link to artifact
                total_rows = metadata.get('total_rows', len(results)) if metadata else len(results)
                content_to_display = f"""## PubMed/PMC Articles Summary

**Total articles:** {total_rows} rows
**Data source:** BigQuery PubMed Central Dataset

The complete articles table has been saved to artifacts for your review.

**Download:** `{filename}` (version {version})

**Preview of first 5 articles:**

{_format_table_preview("pubmed", results[:5], metadata)}

*Full table with all {len(results)} rows available in artifacts.*"""

    elif "trial" in request_lower:
        # Get clinical trials data
        results = tool_context.state.get(StateKeys.CLINICAL_TRIALS_RESULTS)

        if results:
            if is_summarization:
                # Generate analytical summary using display agent
                logger.info(f"📊 Generating analytical summary of clinical trials")

                # Prepare data for summarization (handle both list and string formats)
                if isinstance(results, str):
                    trials_data = results
                else:
                    trials_data = ""
                    for idx, trial in enumerate(results, 1):
                        trials_data += f"\n{idx}. **{trial.get('title', 'N/A')}**\n"
                        trials_data += f"   - NCT ID: {trial.get('nct_id', 'N/A')}\n"
                        trials_data += f"   - Status: {trial.get('status', 'N/A')}\n"
                        if trial.get('conditions'):
                            trials_data += f"   - Conditions: {trial.get('conditions', 'N/A')}\n"
                        if trial.get('brief_summary'):
                            trials_data += f"   - Summary: {trial.get('brief_summary', 'N/A')[:200]}...\n"

                summarization_prompt = f"""Analyze and summarize the following clinical trials data.

Provide:
1. **Key Themes**: What conditions or interventions are being studied?
2. **Trial Status**: Overview of trial statuses (recruiting, completed, etc.)
3. **Key Findings**: What are the most relevant or significant trials?
4. **Notable Trials**: Highlight 2-3 most relevant trials with [Trial:NCT...] citations

Keep the summary under 2000 characters.

**Clinical Trials Data:**
{trials_data[:5000]}"""  # Limit input size

                agent_tool = AgentTool(agent=_display_agent)
                analytical_summary = await agent_tool.run_async(
                    args={"request": summarization_prompt},
                    tool_context=tool_context
                )

                content_to_display = f"""## Clinical Trials Analysis

**Data source:** ClinicalTrials.gov

{analytical_summary}

*Full clinical trials data available - use "show trials table" to view all details.*"""

            else:
                # Show table preview (original behavior)
                # Handle both list and string formats
                if isinstance(results, str):
                    # Already formatted text
                    full_table = f"# Clinical Trials\n\n{results}"
                else:
                    full_table = _format_table("clinical_trials", results, None)

                logger.info(f"📋 Formatted trials table ({len(full_table)} chars)")

                # Save full table to artifacts
                filename = "clinical_trials_table.md"
                table_artifact = types.Part.from_bytes(
                    data=full_table.encode('utf-8'),
                    mime_type="text/markdown"
                )
                version = await tool_context.save_artifact(filename=filename, artifact=table_artifact)
                logger.info(f"✓ Saved full trials table to artifacts: {filename} (version {version})")

                # Return summary with link to artifact
                if isinstance(results, list):
                    preview = _format_table_preview("clinical_trials", results[:5], None)
                else:
                    preview = results[:500] + "..." if len(results) > 500 else results

                content_to_display = f"""## Clinical Trials Summary

**Data source:** ClinicalTrials.gov

The complete clinical trials data has been saved to artifacts for your review.

**Download:** `{filename}` (version {version})

**Preview:**

{preview}

*Full data available in artifacts.*"""

    # If we found content, return it as a dictionary
    if content_to_display:
        logger.info(f"✅ Returning content directly (length: {len(content_to_display)} chars)")
        return {
            "status": "success",
            "content": content_to_display
        }
    else:
        # Content not found - pass request to internal display agent for special handling
        # (might be a summarization request or other special request)
        logger.info("⚠️ Content not found in state, passing to internal display agent")
        agent_tool = AgentTool(agent=_display_agent)
        result = await agent_tool.run_async(
            args={"request": request},
            tool_context=tool_context
        )
        return {
            "status": "success",
            "content": result
        }
