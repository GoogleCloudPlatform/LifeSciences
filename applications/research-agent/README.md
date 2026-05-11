# Research Agent

AI-powered research assistant that combines patent databases, scientific literature, clinical trials data, and web research to deliver comprehensive insights on technology, healthcare innovation, and drug development.

## Overview

Research Agent provides intelligent access to **four primary data sources**:

### Data Sources
1. **Google Patents Public Datasets** (BigQuery)
   - 160M+ patents with full metadata, claims, and citations
   - Natural language to SQL query generation
   - Semantic search via vector embeddings

2. **PubMed Central Open Access** (BigQuery)
   - 2M+ scientific articles with full text
   - Semantic search capabilities using vector embeddings
   - Author, citation, and publication metadata

3. **ClinicalTrials.gov** (API)
   - 500K+ clinical trials from ClinicalTrials.gov database
   - Search by condition, intervention, sponsor, location, and phase
   - Retrieve detailed study protocols, results, and adverse events
   - Analyze trends across thousands of trials
   - Compare studies side-by-side
   - Find eligible trials based on patient profiles

4. **Google Search** (Web Research)
   - Real-time web content and recent developments
   - News articles, expert analysis, and blog posts
   - FDA approvals and industry reports

Perfect for technology analysis, competitive intelligence, academic research, clinical research, and innovation tracking.

## Quick Start

### Prerequisites
- Python 3.12+
- Google Cloud Project with BigQuery enabled
- `gcloud` CLI authenticated

### Installation

```bash
# Clone or navigate to the repository
cd research-agent

# Install uv package manager (if not installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync

# Set up environment
cp .env.example .env
# Edit .env and set GOOGLE_CLOUD_PROJECT="your-project-id"
```

### Setting up Semantic Search (BigQuery)

To use semantic search capabilities (finding articles by meaning rather than just keywords), you must create a remote model in BigQuery that connects to Vertex AI's `text-embedding-005` model.

Run the following command in your terminal (using the `bq` CLI) or execute the SQL in the BigQuery console:

```bash
# 1. Create a Cloud Resource connection (if not already created)
bq mk --connection --location=US --project_id=YOUR_PROJECT_ID --connection_type=CLOUD_RESOURCE vertex_ai

# 2. Grant the service account (displayed in output of step 1) the "Vertex AI User" role in IAM

# 3. Create the remote model (replace YOUR_PROJECT_ID)
bq query --use_legacy_sql=false \
"CREATE OR REPLACE MODEL \`YOUR_PROJECT_ID.vais_demo.embedding_model\`
REMOTE WITH CONNECTION \`us.vertex_ai\`
OPTIONS (ENDPOINT = 'text-embedding-005');"
```

### Run the Agent

**CLI Mode:**
```bash
uv run adk run .
```

**Web UI Mode:**
```bash
uv run adk web .
# Open http://localhost:8000 in your browser
```

## Example Queries

### Patent Research
```
What patents has Tesla filed related to battery technology?
Find 500 Eliquis patents from the past 5 years
Find the latest 200 CRISPR patents from the past year
```

### Scientific Literature
```
Find research articles about CRISPR gene editing
Find 100 recent Eliquis articles
Find 1000 Eliquis articles across all years for comprehensive analysis
```

### Clinical Trials
```
Find all Phase 3 Alzheimer's trials currently recruiting in the United States
Search for Eliquis atrial fibrillation trials
Find completed Phase 3 Eliquis trials
```

### Semantic Search
```
Use semantic search to find articles similar to solid-state battery research
Find articles semantically related to electrolyte optimization in batteries
```

### Combined Analysis
```
Compare quantum computing: patents vs academic research
Analyze the intersection of recent mRNA vaccine patents and scientific literature on immunotherapy applications
```

### Multi-Source Clinical Research
```
Find Phase 3 diabetes trials, related patents, and recent scientific publications
Find ALL trials for Eliquis (apixaban), including BMS-562247
```

### Patient-Centric Research
```
Find recruiting trials for a 62-year-old female with Type 2 Diabetes and Hypertension in Boston, Massachusetts
```

### Data Summarization (NEW)
```
Summarize the patents collected
Analyze the clinical trials data
What are the key themes in the PubMed articles?
Tell me about the key findings in the patents
```

### Query Limits and Scope Control
The agent uses **user-specified limits** for flexible research:

- **Exploratory (default):** 100 most recent results
- **Focused:** 200-500 results for balanced depth
- **Comprehensive:** 1000 results for thorough analysis
- **Maximum:** 5000 results for exhaustive coverage

**How to Control Research Scope:**

**1. Exploratory Search (Default: 100 most recent)**
```
Find recent Eliquis articles
```
- Agent uses LIMIT 100
- Orders by last_updated DESC
- Returns 100 most recent articles

**2. Comprehensive Search (User says "all" or "comprehensive")**
```
Find all comprehensive research on Eliquis anticoagulation
```
- Agent uses LIMIT 5000 (comprehensive mode)
- Returns up to 5000 articles

**3. Exact Count (User specifies number)**
```
Find 500 Eliquis articles from the past 5 years
```
- Agent uses LIMIT 500
- Adds WHERE last_updated >= [5 years ago date]
- Returns exactly 500 articles

**4. Recent with Time Range**
```
Find the latest 200 anticoagulant patents from the past year
```
- Agent uses LIMIT 200
- Adds WHERE publication_date >= 20240101
- Orders by publication_date DESC
- Returns 200 most recent patents from 2024

**Query Limit Guidelines**

| Query Type | Recommended Limit | Max Limit | Rationale |
|------------|-------------------|-----------|-----------|
| **Exploratory** | 100 | - | Quick overview, most recent |
| **Focused Research** | 200-500 | - | Balanced depth and speed |
| **Comprehensive** | 1000-5000 | 5000 | Thorough analysis |
| **Deep Dive** | 2000-5000 | 5000 | Exhaustive coverage |

**Best Practices:**

✅ **DO:**
1. Be specific about count: "Find 500 articles" → Clear expectation
2. Specify time range when relevant: "Find articles from the past 3 years"
3. Use 'comprehensive' or 'all' for exhaustive searches: "Find all Eliquis trials"
4. Start small, scale up: Try 100 first to see relevance, then increase to 500 or 1000

❌ **DON'T:**
1. Use vague terms: ❌ "Find some articles" → ✅ "Find 100 recent articles"
2. Request absurdly large limits: ❌ "Find 100,000 patents" → ✅ "Find 1000 most recent patents"
3. Forget time ranges for large queries: ❌ "Find 1000 articles" → ✅ "Find 1000 articles from past 5 years"

## Features

### 🔍 Natural Language Queries
Ask questions in plain English - the agent automatically generates optimized SQL queries for BigQuery.

### 🧬 Semantic Search
Find conceptually related research using vector embeddings, not just keyword matching.

### 🔄 Deep Research Refinement (Deep Search)
Automated iterative loop that evaluates initial findings for quality and completeness. If gaps are found, it autonomously performs targeted follow-up searches to ensure comprehensive coverage before synthesis.

### 📊 Multi-Source Integration
Seamlessly combines data from **four sources**:
1. **Google Patents Public Datasets** (BigQuery) - 160M+ patents
2. **PubMed Central Open Access** (BigQuery) - 2M+ scientific articles
3. **ClinicalTrials.gov** (API) - 500K+ clinical trials
4. **Google Search** - Real-time web content and recent developments

### 🎯 Smart Routing
Automatically routes queries to the appropriate data sources - patents, articles, and/or web search.

### 📈 Rich Results with Inline Citations
Returns comprehensive metadata including:
- Patent/article titles and abstracts
- Authors and assignees
- Publication dates
- Links to original sources
- Text snippets from full articles
- **Inline citations** for all factual claims (e.g., [Patent:US-2024123456-A1], [Article:PMID-40123456], [Trial:NCT03372603])

### 📊 Dual Display Modes (NEW)
**Table View:**
- Full data tables saved as artifacts
- Preview of first 5 items in chat
- Download links for complete datasets
- Example: `"Show patents table"`

**Analytical Summaries:**
- AI-generated insights and analysis
- Key themes, findings, and trends
- Notable items with citations
- Example: `"Summarize the patents collected"`

### 📝 Report Generation
**Full Reports:**
- Always saved to artifacts as `research_report.md`
- Includes comprehensive analysis with citations
- Footer with original query, date, and research plan

**Chat Summaries:**
- Concise summary displayed in chat
- Under 4000 characters for quick reading
- Links to full report artifact

## How It Works

### Agent Workflow

```
┌──────────────────────────────────────────────────────────────────┐
│                            USER QUERY                            │
│   "Find Phase 3 Alzheimer's trials, related patents, and news"   │
└─────────────────────────────┬────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                    ROOT ORCHESTRATOR (Flash)                     │
│  • Analyzes query intent                                         │
│  • Creates research plan with user                               │
│  • Calls set_research_plan tool                                  │
└─────────────────────────────┬────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│              RESEARCH WORKFLOW (SequentialAgent)                 │
│  Automatically runs 3 steps:                                     │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ STEP 1: DEEP RESEARCH LOOP (Iterative)                     │  │
│  │                                                            │  │
│  │  1. GATHER (Parallel): Runs all sources simultaneously     │  │
│  │     [PubMed] [Patents] [Trials] [Web Search]               │  │
│  │                                                            │  │
│  │  2. EVALUATE (Pro): Checks *combined* findings against     │  │
│  │     Research Plan. Grades "PASS" or "FAIL".                │  │
│  │                                                            │  │
│  │  3. CHECK: If "PASS" -> Stop Loop.                         │  │
│  │            If "FAIL" -> Continue to Refine.                │  │
│  │                                                            │  │
│  │  4. REFINE (Flash): Strategist agent that updates the      │  │
│  │     Research Plan questions to fill identified gaps.       │  │
│  │     (e.g. "Find articles on side effects" -> added)        │  │
│  │                                                            │  │
│  │  (Loop restarts at Step 1 with NEW questions)              │  │
│  └──────────────────────────────┬─────────────────────────────┘  │
│                                 │                                │
│                                 ▼                                │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ STEP 2: SYNTHESIS (Gemini Pro)                             │  │
│  │  • Combines all 4 data sources                             │  │
│  │  • Analyzes patterns and trends                            │  │
│  │  • Generates comprehensive report                          │  │
│  │  • Adds inline citations                                   │  │
│  │  • Saves full report to artifacts (research_report.md)     │  │
│  │  • Creates summary (<4000 chars)                           │  │
│  │  • Footer: research plan, date, data sources               │  │
│  └──────────────────────────────┬─────────────────────────────┘  │
│                                 │                                │
│                                 ▼                                │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ STEP 3: DISPLAY (Gemini Pro)                               │  │
│  │  • Retrieves summary from state                            │  │
│  │  • Displays to user with clickable citations               │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
└─────────────────────────────┬────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                       USER OUTPUT                                │
│  • Summary in chat (<4000 chars) with citations                  │
│  • Full report in artifacts: research_report.md                  │
│  • Can request: "show all patents" or "show all articles"        │
└──────────────────────────────────────────────────────────────────┘
```

### Search Types

**1. Keyword Search (Fast)**
```sql
-- Searches titles and full text for exact terms
WHERE LOWER(title) LIKE '%battery%'
   OR LOWER(article_text) LIKE '%battery%'
```

**2. Semantic Search (Comprehensive)**
```sql
-- RECOMMENDED: Generate embedding directly from query text
WITH query_embedding AS (
  SELECT ml_generate_embedding_result AS embedding_col
  FROM ML.GENERATE_EMBEDDING(
    MODEL `YOUR_PROJECT_ID.vais_demo.embedding_model`,
    (SELECT 'battery technology advancements' AS content),
    STRUCT(TRUE AS flatten_json_output)
  )
)
SELECT pmid, title,
  COSINE_DISTANCE(a.ml_generate_embedding_result, q.embedding_col) as similarity
FROM articles a, query_embedding q
ORDER BY similarity ASC  -- Lower = more similar
```

### Architecture Overview

```
Research Agent
├── Root Orchestrator (Flash)
│   ├── Analyzes user queries and sets research plan
│   ├── Transfers to research_workflow for execution
│   └── Handles follow-up requests (e.g., "show all patents")
│
└── Research Workflow (SequentialAgent)
    │
    ├── Step 1: Deep Research Loop (LoopAgent)
    │   │
    │   ├── Gather (ParallelAgent) - Runs all sources
    │   │   ├── PubMed Agent
    │   │   ├── Patents Agent
    │   │   ├── Trials Agent
    │   │   └── Web Search Agent
    │   │
    │   ├── Evaluator (Pro) - Quality Assurance
    │   │   └── Checks sufficiency of combined data
    │   │
    │   ├── EscalationChecker - Flow Control
    │   │   └── Stops loop if PASS or max iterations reached
    │   │
    │   └── Plan Refiner (Flash) - Strategist
    │       └── Updates research questions based on gaps
    │
    ├── Step 2: Synthesis (Gemini Pro)
    │   ├── Combines verified data from all sources
    │   ├── Generates comprehensive reports with citations
    │   └── Saves full report to artifacts
    │
    └── Step 3: Display (Gemini Pro)
        ├── Extracts summary from full report
        ├── Displays to user with clickable citations
        └── Links to full report artifact
```

## Data Sources

### Patents
- **Dataset:** `patents-public-data.patents.publications`
- **Size:** 160M+ patent publications
- **Coverage:** Worldwide patents from USPTO, EPO, WIPO, and more
- **Fields:** Title, abstract, claims, assignees, inventors, CPC classifications

### Scientific Articles
- **Dataset:** `bigquery-public-data.pmc_open_access_commercial.articles`
- **Size:** ~2.2M+ open access articles
- **Coverage:** PubMed Central biomedical and life sciences research
- **Fields:** Title, full text, authors, citations, embeddings
- **Unique:** Vector embeddings for semantic search

### Clinical Trials
- **Source:** ClinicalTrials.gov API v2 (direct integration)
- **Size:** 500K+ clinical studies
- **Coverage:** Global clinical trials registry (US and international)
- **Fields:** NCT IDs, study protocols, eligibility criteria, results, adverse events, locations
- **Capabilities:** Search by condition/intervention/location, retrieve detailed study info, get trial summaries
- **Implementation:** Direct API client with ADK FunctionTools
- **Attribution:** API client and tool logic adapted from [clinicaltrialsgov-mcp-server](https://github.com/cyanheads/clinicaltrialsgov-mcp-server) by @cyanheads

## Inline Citations

All results include **inline citations** to ensure transparency and traceability.

### Citation Format

The agent uses a standardized citation format:

**Patents:**
```
Moderna leads with 34 patents on mRNA vaccines [Patent:US-2024123456-A1]
```

**Scientific Articles:**
```
Recent research shows 45% response rates in melanoma trials [Article:PMID-40123456]
```

**Clinical Trials:**
```
The Phase 3 trial enrolled 350 patients and showed promising outcomes [Trial:NCT03372603]
```

**Web Sources:**
```
FDA approved the first mRNA cancer vaccine trial in August 2024 [Source:Nature, Aug 2024]
```

### Why Inline Citations?

- ✅ **Transparency**: Every claim is traceable to its source
- ✅ **Verification**: Users can verify facts independently
- ✅ **Academic Rigor**: Meets research documentation standards
- ✅ **Compliance**: Supports regulatory and legal requirements
- ✅ **Trust**: Builds confidence in AI-generated insights

### Example Output with Citations

```
## Analysis of mRNA Vaccine Technology

### Executive Summary
The mRNA vaccine landscape has evolved rapidly, with 145 patents filed
since 2020 [Patent:US-2024123456-A1]. Moderna leads with 34 patents focusing
on lipid nanoparticle delivery systems [Patent:WO-2024567890-A1], while
BioNTech has filed 28 patents emphasizing personalized neoantigen vaccines
[Patent:EP-2024445566-A1].

### Clinical Results
Recent clinical trials demonstrate promising results, with Phase 2 studies
showing 45% objective response rates in melanoma patients [Article:PMID-40123456].
The FDA approved the first mRNA cancer vaccine trial in August 2024
[Nature, Aug 2024](https://nature.com/article), marking a significant milestone.

### Key Research Areas
- Personalized neoantigen identification [Article:PMID-39987654]
- Combination with checkpoint inhibitors [Article:PMID-38654321]
- Manufacturing optimization [Patent:US-2024098765-A1]

---

## Sources

### Patents Cited
- [Patent:US-2024123456-A1] - "Novel battery systems based on two-additive electrolyte systems"
- [Patent:WO-2024567890-A1] - "Lipid nanoparticle formulations for mRNA vaccines"
- [Patent:EP-2024445566-A1] - "Patient-specific mRNA vaccine design"
- [Patent:US-2024098765-A1] - "Modified mRNA constructs for enhanced immune response"

### Scientific Articles Cited
- [Article:PMID-40123456] - "mRNA-based cancer vaccines: mechanisms and clinical applications"
- [Article:PMID-39987654] - "Personalized mRNA vaccines for melanoma immunotherapy"
- [Article:PMID-38654321] - "Combination immunotherapy approaches with mRNA vaccines"

### Clinical Trials Cited
- [Trial:NCT03372603] - "Phase 3 Study of mRNA-4157 in Melanoma (KEYNOTE-942)"
- [Trial:NCT04516746] - "Personalized Cancer Vaccine Combined with Pembrolizumab"

### Web Sources
- [Nature, Aug 2024](https://nature.com/article) - "FDA approves first mRNA cancer vaccine trial"
```

## Configuration

### Environment Variables

Edit `.env` to customize:

```bash
# Required
GOOGLE_CLOUD_PROJECT=your-project-id

# Optional: Model Configuration
ROOT_MODEL=gemini-2.5-flash          # Main orchestration
WORKER_MODEL=gemini-2.5-flash        # Web searches
BIGQUERY_MODEL=gemini-2.5-flash      # SQL generation
CRITIC_MODEL=gemini-2.5-pro          # Quality evaluation
SYNTHESIS_MODEL=gemini-2.5-pro       # Report generation

# Optional: Research Settings
MAX_SEARCH_ITERATIONS=2              # Research refinement loops
```

### Model Strategy

The agent uses a **tiered model approach** for optimal cost and performance:

- **Flash models** (fast & efficient): Query routing, SQL generation, web searches
- **Pro models** (deep reasoning): Quality evaluation, final synthesis

## Advanced Usage

### Semantic Search

For deeper research beyond keyword matching:

```
Find articles semantically related to electrolyte optimization in batteries
```

This uses vector embeddings to find conceptually similar articles, even when keywords differ.

### Patent Analysis

```
Analyze Tesla's battery patent portfolio including filing trends
```

### Cross-Domain Research

```
Link AI patents to academic research papers
```

### Technology Trends

```
Compare quantum computing patents vs academic publications over the last 5 years
```

### Clinical Trials Research

**Find eligible trials:**
```
Find recruiting trials for a 62-year-old female with Type 2 Diabetes and Hypertension in Boston, Massachusetts
```

**Research specific trials:**
```
Tell me about trial NCT06297603
```

**Multi-source drug research:**
```
Analyze retatrutide: find related clinical trials, patents, and recent news
```

**Example output:**
The agent will:
1. Search ClinicalTrials.gov for matching trials
2. Retrieve detailed trial information including eligibility criteria, study design, and locations
3. Perform web research for recent developments, expert opinions, and trial results
4. Synthesize findings with inline citations: [Trial:NCT06297603]

## Project Structure

```
research-agent/
├── research_agent/
│   ├── agent.py                         # Root orchestrator + research_workflow (SequentialAgent)
│   ├── config.py                        # Configuration (models, settings)
│   ├── state_keys.py                    # Centralized state key constants
│   ├── set_research_plan.py             # Tool to set research plan in state
│   ├── parallel_data_gathering.py       # ParallelAgent for 4 data sources
│   ├── instructions.md                  # Root agent instructions
│   └── sub_agents/
│       ├── bigquery/                    # BigQuery data retrieval
│       │   ├── agent.py                # SQL generation and query execution
│       │   ├── instructions.md         # BigQuery agent instructions
│       │   └── tools.py                # ADK FunctionTools for BigQuery
│       ├── clinical_trials/             # Clinical trials integration
│       │   ├── __init__.py             # Module exports
│       │   ├── agent.py                # Clinical trials query tool
│       │   ├── api_client.py           # ClinicalTrials.gov API v2 client
│       │   └── tools.py                # ADK FunctionTools for trials API
│       │   # Based on: github.com/cyanheads/clinicaltrialsgov-mcp-server
│       ├── research/                    # Web research pipeline
│       │   └── agent.py                # Google Search with quality evaluation
│       ├── refinement/                  # Deep Research Loop
│       │   ├── checker.py              # Escalation logic (pass/fail)
│       │   ├── evaluator.py            # Research quality evaluator
│       │   └── executor.py             # Plan refinement strategist
│       ├── synthesis/                   # Report generation
│       │   ├── agent.py                # Synthesis tool and agent
│       │   └── instructions.md         # Synthesis instructions
│       └── display/                     # Display and formatting
│           └── agent.py                # Display content tool
├── llms-full.txt                        # ADK best practices reference
├── pyproject.toml                       # Dependencies
├── .env.example                         # Environment template
└── README.md                            # This file
```

## Common Use Cases

### 🔬 Academic Research
- Find related papers using semantic search
- Track citations and research evolution
- Identify key authors and institutions

### 🏢 Competitive Intelligence
- Analyze competitor patent portfolios
- Track technology trends
- Identify emerging innovations

### 💡 Innovation Tracking
- Monitor new patent filings
- Discover technology transfer opportunities
- Link academic research to commercial applications

### 📊 Technology Analysis
- Compare patent activity across companies
- Analyze research publication trends
- Evaluate technology maturity

## Troubleshooting

### "Permission denied on BigQuery"
Ensure your Google Cloud account has BigQuery access:
```bash
gcloud auth application-default login
```

### "No results found"
- Try broader search terms
- Use semantic search for conceptual matches
- Check dataset availability in BigQuery console

### Slow queries
- BigQuery scans large datasets (13GB+ for articles)
- First queries may be slower; subsequent queries benefit from caching
- Adjust LIMIT clauses for faster results

### Agent not using semantic search
Explicitly request it:
```
Use semantic search to find...
Find articles similar to...
Find semantically related articles about...
```

## Tips for Better Results

1. **Be Specific:** Include companies, timeframes, or technology areas
   - ✅ "Tesla battery patents filed in 2024"
   - ❌ "battery patents"

2. **Choose the Right Search:**
   - Keyword search: Exact terms, specific queries
   - Semantic search: Broad exploration, conceptual similarity

3. **Combine Sources:** Use both patents and articles for comprehensive analysis

4. **Iterate:** Refine questions based on initial results

## Security & Privacy

- Queries execute in **your** Google Cloud project
- No data is stored or shared by the agent
- BigQuery public datasets are read-only
- Follows Google Cloud security best practices

## Cost Considerations

- **BigQuery:** Queries charge based on data scanned (~13GB for article full-text searches)
- **Vertex AI:** Model API calls (Flash models are cost-effective)
- **Optimization:** Results are cached when possible

Typical costs: <$0.10 per query for most use cases.

## Development

### Agent Development with LLMs

If you are developing or modifying the agent using AI-assisted "vibe coding," use the provided context files:

- **`llms-full.txt`**: Complete ADK documentation with all best practices, patterns, and examples
- **`llms.txt`** (if available): Summarized version for smaller context windows

**Official Documentation:**
- [ADK Python Vibe Coding Guide](https://github.com/google/adk-python/blob/main/README.md#vibe-coding) - Official ADK documentation and best practices for LLM-assisted development

These files contain comprehensive ADK guidelines for:
- Agent design patterns
- Multi-agent orchestration
- Tool implementation
- State management
- Error handling
- Security best practices

**Example usage with Gemini CLI or similar tools:**
```bash
# Include ADK context when asking for agent improvements
"Review @research_agent/agent.py against ADK best practices in @llms-full.txt"
```

**Note:** The `llms-full.txt` context file is generated from the [official ADK Python documentation](https://github.com/google/adk-python/blob/main/README.md#vibe-coding) and provides the latest best practices for agent development.

### Architecture Alignment

This agent follows ADK best practices:
- ✅ **Modular design** with specialized sub-agents for each data source
- ✅ **Tiered model strategy** (Flash for speed, Pro for reasoning)
- ✅ **Clear agent descriptions** for automatic delegation
- ✅ **Structured state management** via StateKeys constants and tool contexts
- ✅ **Proper error handling** in orchestrator tools
- ✅ **Citation system** with inline references and source lists
- ✅ **Workflow agents** (SequentialAgent, ParallelAgent) for predictable execution
- ✅ **Agent callbacks** (before_agent_callback, after_agent_callback) for conditional execution
- ✅ **Single-purpose agents** - Each agent has one clear responsibility

## Support & Resources

- **BigQuery Console:** Monitor queries and costs
- **Dataset Documentation:**
  - [Google Patents Public Datasets](https://console.cloud.google.com/marketplace/browse?q=google%20patents)
  - [PubMed Central Open Access](https://www.ncbi.nlm.nih.gov/pmc/tools/openftlist/)
- **ClinicalTrials.gov:**
  - [API Documentation](https://clinicaltrials.gov/data-api/api)
  - [API Client Source](https://github.com/cyanheads/clinicaltrialsgov-mcp-server) (logic adapted for direct integration)
- **ADK Documentation:**
  - [ADK Home](https://google.github.io/adk-docs/)
  - [Built-in Tools](https://google.github.io/adk-docs/tools/built-in-tools/) (includes Google Search, BigQuery, and more)
  - [Google Search Tool](https://google.github.io/adk-docs/tools/built-in-tools/#google-search) (used for web research)

## Demo Guide

### Quick Demo (8-12 minutes)

**1. Run a Multi-Source Query:**
```
Find 1000 recent PubMed articles on Eliquis AND 1000 recent patents on anticoagulants
```

Then after the report is automatically displayed, test follow-up requests:

"show all patents" - Should display full patents table as artifact
"show all articles" - Should display full PubMed articles table as artifact
"summarize the patents collected" - Should generate analytical summary of patents data

**Recommended Query:**
```
Analyze the intersection of recent mRNA vaccine patents and scientific literature on immunotherapy applications. Compare patent activity vs academic research.
```

**What to highlight:**

1. **Planning (1-2 min)**: Root orchestrator creates research plan with user confirmation
2. **Parallel Execution (3-5 min)**: All 4 data sources queried simultaneously:
   - Google Patents (160M+ via BigQuery)
   - PubMed Central (2M+ via BigQuery)
   - ClinicalTrials.gov (500K+ via API)
   - Google Search (real-time web with quality evaluation)
3. **Synthesis (2-3 min)**: Combines all data sources with equal analysis, generates comprehensive report
4. **Display (instant)**: Summary automatically shown with clickable citations, full report in artifacts

**Follow-up query to show refinement:**
```
Tell me more about BioNTech's personalized cancer vaccine approach
```

## License

Copyright 2025 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
