"""
Clinical Trials Agent - Specialized agent for querying ClinicalTrials.gov

This agent provides access to the ClinicalTrials.gov database using direct API calls
wrapped as ADK FunctionTools. Based on the logic from clinicaltrialsgov-mcp-server.

Capabilities:
- Search for clinical studies using query terms and filters
- Retrieve detailed study information by NCT IDs
- Get concise summaries of multiple trials
"""

import logging

from google.adk.agents import LlmAgent
from google.adk.tools import ToolContext
from google.adk.tools.agent_tool import AgentTool

from ...config import config
from ...state_keys import StateKeys
from .tools import get_clinical_trial_details, get_trial_summary, search_clinical_trials

logger = logging.getLogger(__name__)

# Create the Clinical Trials Agent
clinical_trials_agent = LlmAgent(
    model=config.worker_model,
    name="clinical_trials_agent",
    instruction="""
You are a clinical trials research specialist with access to the ClinicalTrials.gov database.

Your capabilities include:

1. **Search Clinical Trials** (search_clinical_trials)
   - Search using free-text queries across conditions, interventions, sponsors, titles
   - Apply filters for status, phase, location (country, state, city)
   - **Supports pagination**: Use `max_results` parameter to fetch large datasets (up to 1000 trials)
   - Returns concise summaries with NCT IDs, titles, status, conditions, interventions, locations
   - Stores structured data in state for synthesis and display agents
   - Examples:
     * search_clinical_trials(query="diabetes", filter_expr='AREA[Phase]PHASE3', country="United States", max_results=200)
     * search_clinical_trials(query="Alzheimer's disease", filter_expr='AREA[OverallStatus]RECRUITING', state="California", max_results=500)
     * search_clinical_trials(query="cancer immunotherapy", max_results=1000)  # Fetch all available trials

2. **Get Trial Details** (get_clinical_trial_details)
   - Retrieve comprehensive information for a specific NCT ID
   - Returns full protocol, eligibility criteria, study design, interventions, outcomes, results
   - Examples:
     * get_clinical_trial_details(nct_id="NCT03372603")
     * get_clinical_trial_details(nct_id="NCT04516746")

3. **Get Trial Summaries** (get_trial_summary)
   - Get concise overviews of multiple trials (up to 5)
   - Returns NCT ID, title, status, key dates
   - Examples:
     * get_trial_summary(nct_ids="NCT03372603")
     * get_trial_summary(nct_ids="NCT03372603,NCT04516746,NCT05123456")

**Common Filter Syntax:**
- Status: `AREA[OverallStatus]RECRUITING` or `AREA[OverallStatus]COMPLETED`
- Phase: `AREA[Phase]PHASE3` or `AREA[Phase]PHASE2`
- Study Type: `AREA[StudyType]INTERVENTIONAL`
- Multiple filters: `AREA[Phase]PHASE3 AND AREA[OverallStatus]RECRUITING`

**Best Practices:**
- Use search_clinical_trials() first to find relevant trials
- **For comprehensive research**: Use `max_results=500` or higher to gather all relevant trials
- **For quick queries**: Use default `max_results=100` or smaller values
- Extract NCT IDs from search results
- Use get_clinical_trial_details() for comprehensive information on specific trials
- Use get_trial_summary() for quick overviews of multiple trials
- **Location filters**: ONLY use country/state/city when the user explicitly mentions a location
  - ✅ "Find trials in California" → use state="California"
  - ❌ "Find ALL trials" → DO NOT use location filters
  - ❌ "Find trials across all phases" → DO NOT use location filters
- When asked about "all trials" or comprehensive research, set `max_results` high (500-1000) and DO NOT add location filters
- Always include NCT IDs in your responses for reference

**Response Format:**
- Provide clear, actionable information from clinical trial data
- Include relevant NCT IDs for reference (format: [Trial:NCT03372603])
- Highlight key findings like enrollment status, eligibility criteria, and locations
- When comparing, clearly outline similarities and differences
- For trends, provide both counts and context

**Example Workflows:**

*Finding trials with location:*
User: "Find all Phase 3 Alzheimer's trials recruiting in California"
1. search_clinical_trials(query="Alzheimer's disease", filter_expr='AREA[Phase]PHASE3 AND AREA[OverallStatus]RECRUITING', state="California", max_results=500)
2. Present results with NCT IDs, titles, locations

*Finding ALL trials globally (NO location filters):*
User: "Find ALL clinical trials for Eliquis across all phases and statuses"
1. search_clinical_trials(query="Eliquis OR apixaban", max_results=1000)
   - NOTE: NO country, state, or city parameters - search worldwide!
2. Present comprehensive list with NCT IDs, titles, statuses, phases, locations

*Getting details:*
User: "What are the details of NCT03372603?"
1. get_clinical_trial_details(nct_id="NCT03372603")
2. Present protocol, eligibility, design, outcomes

*Comparing trials:*
User: "Compare NCT03372603 and NCT04516746"
1. get_clinical_trial_details(nct_id="NCT03372603")
2. get_clinical_trial_details(nct_id="NCT04516746")
3. Compare and contrast the key aspects
""",
    tools=[
        search_clinical_trials,
        get_clinical_trial_details,
        get_trial_summary,
    ],
)


# Wrapper function for calling clinical_trials_agent as a tool
async def query_clinical_trials(
    question: str,
    run_clinical_trials: bool = True,
    tool_context: ToolContext = None,
) -> dict:
    """
    Query clinical trial data from ClinicalTrials.gov.

    This tool provides access to the ClinicalTrials.gov database with capabilities to:
    - Search for clinical studies by condition, intervention, sponsor, or location
    - Retrieve detailed study information including protocols, results, and adverse events
    - Analyze trends across thousands of trials (status, phases, geographic distribution)
    - Compare multiple studies side-by-side
    - Find eligible trials based on patient profiles (age, sex, conditions, location)

    Use this tool when you need to:
    - Find clinical trials for specific diseases or conditions
    - Get trial status, phases, enrollment numbers, and eligibility criteria
    - Analyze clinical research trends by sponsor, location, or therapeutic area
    - Compare trial designs, interventions, or outcomes across studies
    - Match patients to potentially eligible clinical trials
    - Access trial results, adverse events, and outcome measures

    Examples:
    - "Find all Phase 3 diabetes trials currently recruiting in California"
    - "Get detailed information for trial NCT03372603"
    - "Compare the designs of NCT04516746 and NCT04516759"
    - "Analyze trends in cancer immunotherapy trials over the past 5 years"
    - "Find recruiting migraine studies for a 35-year-old female in Canada"

    Args:
        question: Natural language query for clinical trials data
        run_clinical_trials: Whether to search clinical trials (default: True)
        tool_context: The tool context

    Returns:
        Dictionary with:
            - status (str): "success" or "error"
            - message (str): Human-readable status message
            - preview (str): Preview of results (first 500 chars)
        Full results are stored in state under 'clinical_trials_results' key.
    """
    logger.info(f"Querying Clinical Trials: {question} (run_clinical_trials={run_clinical_trials})")

    # Check if disabled
    if not run_clinical_trials:
        return {
            "status": "skipped",
            "message": "Skipped Clinical Trials - disabled by research plan",
            "preview": "",
        }

    try:
        agent_tool = AgentTool(agent=clinical_trials_agent)
        result = await agent_tool.run_async(
            args={"request": question}, tool_context=tool_context
        )

        # NOTE: search_clinical_trials tool already stores structured data in state
        # Don't overwrite it with the agent's text response
        # The structured data is stored by the tool at StateKeys.CLINICAL_TRIALS_RESULTS

        # Return brief summary
        result_preview = result[:500] + "..." if len(result) > 500 else result
        return {
            "status": "success",
            "message": "✓ Clinical trials data retrieved successfully.",
            "preview": result_preview,
            "details": f"Full structured data stored in state under key '{StateKeys.CLINICAL_TRIALS_RESULTS}'."
        }
    except Exception as e:
        logger.error(f"Error querying ClinicalTrials agent: {e}", exc_info=True)
        return {
            "status": "error",
            "error_message": f"An error occurred while querying ClinicalTrials.gov: {str(e)}",
            "error_type": type(e).__name__
        }
