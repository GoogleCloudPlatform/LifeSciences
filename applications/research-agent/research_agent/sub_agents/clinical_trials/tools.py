"""
ClinicalTrials.gov Tools

ADK FunctionTools for querying clinical trials data.
Based on the MCP server tool definitions but simplified for ADK.
"""

import json
from typing import Optional

from google.adk.tools import ToolContext

from .api_client import ClinicalTrialsAPIError, ClinicalTrialsClient

# Shared client instance
_client = ClinicalTrialsClient(timeout=30)


async def search_clinical_trials(
    query: Optional[str] = None,
    filter_expr: Optional[str] = None,
    page_size: int = 10,
    max_results: int = 100,
    country: Optional[str] = None,
    state: Optional[str] = None,
    city: Optional[str] = None,
    tool_context: Optional[ToolContext] = None,
) -> str:
    """
    Search for clinical trial studies from ClinicalTrials.gov with pagination support.

    Use this to find clinical trials by condition, intervention, sponsor, location, or other criteria.
    Can fetch large datasets by automatically paginating through results (similar to BigQuery).

    Args:
        query: Search terms for conditions, interventions, sponsors (e.g., "diabetes", "Alzheimer's", "Moderna")
        filter_expr: Advanced filter using ClinicalTrials.gov syntax (e.g., 'AREA[OverallStatus]RECRUITING')
        page_size: Number of results per page (1-200, default: 10). API supports up to 200 per page.
        max_results: Maximum total results to fetch across all pages (1-5000, default: 100). Set higher for comprehensive research.
        country: Filter by country name (e.g., "United States", "Canada", "United Kingdom")
        state: Filter by state/province (e.g., "California", "Ontario", "Texas")
        city: Filter by city (e.g., "New York", "Boston", "Toronto")

    Returns:
        JSON string with:
        - totalCount: Total matching studies in ClinicalTrials.gov
        - fetchedCount: Number of trials actually fetched (limited by max_results)
        - studies: Preview of first 5 trials
        - message: Summary of operation

        Full structured data is stored in tool_context.state[StateKeys.CLINICAL_TRIALS_RESULTS] as a list
        of dictionaries, similar to how BigQuery stores results.

    Examples:
        - search_clinical_trials(query="diabetes", filter='AREA[Phase]PHASE3', country="United States", max_results=200)
        - search_clinical_trials(query="Alzheimer's disease", filter='AREA[OverallStatus]RECRUITING', state="California", max_results=500)
        - search_clinical_trials(query="cancer immunotherapy", max_results=5000)  # Fetch maximum allowed
    """
    try:
        import logging
        logger = logging.getLogger(__name__)

        # Log the actual parameters received
        logger.info(f"search_clinical_trials called with: query={query}, filter={filter_expr}, country={country}, state={state}, city={city}, max_results={max_results}")

        # Limit max_results to prevent overwhelming the system
        max_results = min(max_results, 5000)
        # Optimize page_size (API allows up to 200)
        # If requesting many results, maximize page size to reduce API calls
        if max_results > 100:
            page_size = 200
        else:
            page_size = min(max(page_size, 1), 200)

        # Request key fields to keep payload small
        fields = [
            "NCTId",
            "BriefTitle",
            "BriefSummary",
            "OverallStatus",
            "Condition",
            "InterventionName",
            "LocationFacility",
            "LocationCity",
            "LocationState",
            "LocationCountry",
            "Phase",
            "EnrollmentCount",
            "StudyType",
        ]

        # Fetch multiple pages if needed
        all_studies = []
        page_token = None
        total_count = None  # Use None to track if we've set it yet

        while len(all_studies) < max_results:
            result = await _client.search_studies(
                query=query,
                filter=filter_expr,
                page_size=page_size,
                page_token=page_token,
                fields=fields,
                country=country,
                state=state,
                city=city,
            )

            studies = result.get("studies", [])
            # Only set total_count from first response (to avoid it being reset to 0 on subsequent pages)
            if total_count is None:
                total_count = result.get("totalCount", 0)
            next_page_token = result.get("nextPageToken")

            all_studies.extend(studies)

            # Stop if no more pages or reached max_results
            if not next_page_token or len(all_studies) >= max_results:
                break

            page_token = next_page_token

        # Trim to max_results if we fetched more
        all_studies = all_studies[:max_results]

        # Format response - store structured data in state
        structured_trials = []

        for study in all_studies:
            protocol = study.get("protocolSection", {})
            identification = protocol.get("identificationModule", {})
            status_module = protocol.get("statusModule", {})
            conditions_module = protocol.get("conditionsModule", {})
            interventions_module = protocol.get("armsInterventionsModule", {})
            design_module = protocol.get("designModule", {})

            # Extract locations (limit to 3 for brevity)
            locations_module = protocol.get("contactsLocationsModule", {})
            locations = locations_module.get("locations", [])[:3]
            location_strs = []
            for loc in locations:
                loc_city = loc.get("city", "")
                loc_state = loc.get("state", "")
                loc_country = loc.get("country", "")
                loc_str = ", ".join(filter(None, [loc_city, loc_state, loc_country]))
                if loc_str:
                    location_strs.append(loc_str)

            # Get description
            description_module = protocol.get("descriptionModule", {})
            brief_summary = description_module.get("briefSummary", "")

            # Get NCT ID and construct URL
            nct_id = identification.get("nctId")
            trial_url = f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else None

            study_summary = {
                "nct_id": nct_id,
                "title": identification.get("briefTitle"),
                "status": status_module.get("overallStatus"),
                "conditions": conditions_module.get("conditions", [])[:3],  # Limit to 3
                "interventions": [
                    i.get("name")
                    for i in interventions_module.get("interventions", [])[:3]
                ],
                "phase": design_module.get("phases", []),
                "locations": location_strs,
                "brief_summary": brief_summary[:500] if brief_summary else "",  # Limit for brevity
                "trial_url": trial_url,  # Direct link to trial on ClinicalTrials.gov
            }

            structured_trials.append(study_summary)

        # Store structured data in state (similar to BigQuery pattern)
        if tool_context:
            from ...state_keys import StateKeys
            tool_context.state[StateKeys.CLINICAL_TRIALS_RESULTS] = structured_trials

            # Build metadata with search parameters
            metadata = {
                "total_count": total_count,
                "fetched_count": len(structured_trials),
                "max_results": max_results,
                "search_params": {}
            }

            # Add search parameters that were actually used
            if query:
                metadata["search_params"]["query"] = query
            if filter_expr:
                metadata["search_params"]["filter"] = filter_expr
            if country:
                metadata["search_params"]["country"] = country
            if state:
                metadata["search_params"]["state"] = state
            if city:
                metadata["search_params"]["city"] = city

            logger.info(f"Built metadata: {metadata}")
            logger.info(f"Setting clinical_trials_metadata in state")

            tool_context.state["clinical_trials_metadata"] = metadata

            logger.info(f"Metadata stored in state: {tool_context.state.get('clinical_trials_metadata')}")

        # Return summary
        summary = {
            "totalCount": total_count,
            "fetchedCount": len(structured_trials),
            "studies": structured_trials[:5],  # Preview of first 5
            "message": f"Fetched {len(structured_trials)} trials out of {total_count} total. Full data stored in state."
        }

        return json.dumps(summary, indent=2)

    except ClinicalTrialsAPIError as e:
        return json.dumps({"error": str(e), "totalCount": 0, "studies": []})
    except Exception as e:
        return json.dumps({"error": f"Unexpected error: {str(e)}", "totalCount": 0, "studies": []})


async def get_clinical_trial_details(
    nct_id: str,
    tool_context: Optional[ToolContext] = None,
) -> str:
    """
    Get detailed information about a specific clinical trial by its NCT ID.

    Use this when you need comprehensive information about a specific trial, including:
    - Full protocol details
    - Eligibility criteria
    - Study design
    - Outcome measures
    - Results (if available)
    - Contact information

    Args:
        nct_id: The NCT identifier (e.g., "NCT03372603", "NCT04516746")

    Returns:
        JSON string with detailed study information including protocol sections,
        eligibility criteria, study design, interventions, outcomes, results, and contacts.

    Examples:
        - get_clinical_trial_details(nct_id="NCT03372603")
        - get_clinical_trial_details(nct_id="NCT04516746")
    """
    try:
        # Get full study details
        result = await _client.get_study(nct_id)

        # Extract key sections for a comprehensive but organized response
        protocol = result.get("protocolSection", {})
        results_section = result.get("resultsSection", {})

        # Identification
        identification = protocol.get("identificationModule", {})

        # Status
        status_module = protocol.get("statusModule", {})

        # Description
        description = protocol.get("descriptionModule", {})

        # Conditions
        conditions = protocol.get("conditionsModule", {})

        # Design
        design = protocol.get("designModule", {})

        # Arms and Interventions
        arms_interventions = protocol.get("armsInterventionsModule", {})

        # Outcomes
        outcomes = protocol.get("outcomesModule", {})

        # Eligibility
        eligibility = protocol.get("eligibilityModule", {})

        # Contacts and Locations
        contacts_locations = protocol.get("contactsLocationsModule", {})

        # Results (if available)
        outcome_measures = results_section.get("outcomeMeasuresModule", {})
        adverse_events = results_section.get("adverseEventsModule", {})

        study_details = {
            "nctId": identification.get("nctId"),
            "title": identification.get("briefTitle"),
            "officialTitle": identification.get("officialTitle"),
            "status": status_module.get("overallStatus"),
            "phase": design.get("phases"),
            "studyType": design.get("studyType"),
            "briefSummary": description.get("briefSummary"),
            "detailedDescription": description.get("detailedDescription"),
            "conditions": conditions.get("conditions"),
            "interventions": [
                {
                    "type": i.get("type"),
                    "name": i.get("name"),
                    "description": i.get("description"),
                }
                for i in arms_interventions.get("interventions", [])
            ],
            "primaryOutcomes": outcomes.get("primaryOutcomes", []),
            "secondaryOutcomes": outcomes.get("secondaryOutcomes", [])[:3],  # Limit
            "eligibility": {
                "criteria": eligibility.get("eligibilityCriteria"),
                "sex": eligibility.get("sex"),
                "minimumAge": eligibility.get("minimumAge"),
                "maximumAge": eligibility.get("maximumAge"),
                "healthyVolunteers": eligibility.get("healthyVolunteers"),
            },
            "enrollment": status_module.get("enrollmentInfo"),
            "startDate": status_module.get("startDateStruct"),
            "completionDate": status_module.get("completionDateStruct"),
            "sponsor": protocol.get("sponsorCollaboratorsModule", {}).get("leadSponsor"),
            "locations": contacts_locations.get("locations", [])[:5],  # First 5 locations
            "hasResults": bool(outcome_measures or adverse_events),
        }

        return json.dumps(study_details, indent=2)

    except ClinicalTrialsAPIError as e:
        return json.dumps({"error": str(e), "nctId": nct_id})
    except Exception as e:
        return json.dumps({"error": f"Unexpected error: {str(e)}", "nctId": nct_id})


async def get_trial_summary(
    nct_ids: str,
    tool_context: Optional[ToolContext] = None,
) -> str:
    """
    Get concise summaries for one or more clinical trials.

    Use this when you need quick overviews of multiple trials without full details.
    Useful for comparing trials or getting status updates.

    Args:
        nct_ids: Comma-separated NCT identifiers (e.g., "NCT03372603,NCT04516746,NCT05123456")
                 Maximum 5 trials recommended for performance.

    Returns:
        JSON string with array of trial summaries including NCT ID, title, status, and key dates.

    Examples:
        - get_trial_summary(nct_ids="NCT03372603")
        - get_trial_summary(nct_ids="NCT03372603,NCT04516746")
    """
    try:
        # Parse NCT IDs
        ids = [nct_id.strip() for nct_id in nct_ids.split(",")]

        # Limit to 5 for performance
        ids = ids[:5]

        summaries = []
        for nct_id in ids:
            try:
                metadata = await _client.get_study_metadata(nct_id)
                summaries.append(metadata)
            except ClinicalTrialsAPIError as e:
                summaries.append({"nctId": nct_id, "error": str(e)})

        return json.dumps({"trials": summaries}, indent=2)

    except Exception as e:
        return json.dumps({"error": f"Unexpected error: {str(e)}", "trials": []})
