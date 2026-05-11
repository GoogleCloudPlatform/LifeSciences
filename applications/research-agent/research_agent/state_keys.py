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

"""State key constants for the research agent.

This module provides centralized constants for all state keys used across
the research agent. Using constants instead of string literals provides:
- IDE autocomplete support
- Typo prevention (caught at import time)
- Easy refactoring (rename in one place)
- Self-documenting code
"""


class StateKeys:
    """Centralized constants for all state keys used across the research agent.

    State keys are organized by the component that produces them.
    """

    # BigQuery results and metadata
    PUBMED_RESULTS = "pubmed_results"
    """List[dict]: PubMed/PMC article data from BigQuery."""

    PUBMED_METADATA = "pubmed_metadata"
    """dict: Metadata for PubMed query (total_rows, sql_query, console_link)."""

    PATENTS_RESULTS = "patents_results"
    """List[dict]: Patent data from BigQuery."""

    PATENTS_METADATA = "patents_metadata"
    """dict: Metadata for Patents query (total_rows, sql_query, console_link)."""

    BIGQUERY_METADATA = "bigquery_metadata"
    """dict: Generic BigQuery metadata when source type is unknown."""

    BIGQUERY_RESULTS = "bigquery_results"
    """Any: Generic BigQuery results (used in research pipeline)."""

    # Clinical Trials results
    CLINICAL_TRIALS_RESULTS = "clinical_trials_results"
    """str: Clinical trial data from ClinicalTrials.gov API."""

    # Web Research results and metadata
    WEB_RESEARCH_RESULTS = "web_research_results"
    """str: Final web research report with citations."""

    WEB_RESEARCH_SOURCES = "web_research_sources"
    """dict: Web research sources metadata (URL, title, supported claims) keyed by short ID."""

    WEB_RESEARCH_URL_TO_ID = "web_research_url_to_id"
    """dict: Mapping of web URLs to short citation IDs for web research."""

    WEB_RESEARCH_PLAN = "web_research_plan"
    """str: Research plan passed to web research pipeline (internal to web research, set by perform_web_research)."""

    WEB_RESEARCH_FINDINGS = "web_research_findings"
    """str: Intermediate research findings from web search agent (internal to web research pipeline)."""

    WEB_RESEARCH_EVALUATION = "web_research_evaluation"
    """dict: Quality evaluation of web research findings (internal to web research pipeline)."""

    WEB_RESEARCH_FINAL_CITED_REPORT = "web_research_final_cited_report"
    """str: Final cited report from report composer (internal to web research pipeline)."""

    # Refinement Loop results
    REFINEMENT_RESULTS = "refinement_results"
    """str: Results from follow-up searches executed by the refinement loop."""

    REFINEMENT_LOOP_COUNT = "refinement_loop_count"
    """int: Current number of refinement iterations executed."""

    REFINEMENT_HISTORY = "refinement_history"
    """List[dict]: History of refinement updates (iteration, source, new_question)."""

    RESEARCH_EVALUATION = "research_evaluation"
    """dict: The evaluation feedback (grade, critique, queries) from the refinement evaluator."""

    # Research Plan (set by root agent after user approval)
    RESEARCH_PLAN = "research_plan"
    """str: The overall research plan created by root agent and approved by user (for reference)."""

    # Refinement Settings
    RESEARCH_PLAN_REFINEMENT_ITERATIONS = "research_plan_refinement_iterations"
    """int: Maximum number of refinement iterations allowed (user configurable)."""

    # PubMed-specific plan
    RESEARCH_PLAN_PUBMED_QUESTION = "research_plan_pubmed_question"
    """str: Specific question/query for PubMed article search."""

    RESEARCH_PLAN_PUBMED_RUN = "research_plan_pubmed_run"
    """bool: Whether to run PubMed/article search."""

    # Patents-specific plan
    RESEARCH_PLAN_PATENTS_QUESTION = "research_plan_patents_question"
    """str: Specific question/query for patent search."""

    RESEARCH_PLAN_PATENTS_RUN = "research_plan_patents_run"
    """bool: Whether to run patent search."""

    # Clinical Trials-specific plan
    RESEARCH_PLAN_CLINICAL_TRIALS_QUESTION = "research_plan_clinical_trials_question"
    """str: Specific question/query for clinical trials search."""

    RESEARCH_PLAN_CLINICAL_TRIALS_RUN = "research_plan_clinical_trials_run"
    """bool: Whether to run clinical trials search."""

    # Web Research-specific plan
    RESEARCH_PLAN_WEB_RESEARCH_QUESTION = "research_plan_web_research_question"
    """str: Specific question/query for web research."""

    RESEARCH_PLAN_WEB_RESEARCH_RUN = "research_plan_web_research_run"
    """bool: Whether to run web research."""

    # Parallel data gathering (deprecated - use RESEARCH_PLAN instead)
    RESEARCH_REQUEST = "research_request"
    """str: User's research request stored for parallel data gathering agents (deprecated)."""

    # Synthesis results
    SYNTHESIS_SUMMARY = "synthesis_summary"
    """str: Synthesized summary ready for display to user (full report saved in artifacts)."""
