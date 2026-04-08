# Testing Guide

This document covers how to run, write, and maintain tests for the AF2 ADK Agent.

## Directory Structure

```
tests/
├── conftest.py                    # Shared fixtures (mock_env_vars, mock_config, etc.)
├── .env.test                      # Test environment variables
├── unit/                          # Unit tests (no GCP, no LLM)
│   ├── test_agent.py              # Agent creation and configuration (9 tests)
│   ├── test_config.py             # Config loading and validation (7 tests)
│   ├── test_skills.py             # Skill wrapper → tool registry routing (30 tests)
│   └── test_tool_registry.py      # Singleton registry behavior (4 tests)
└── integration/                   # Integration tests (requires Gemini API)
    ├── conftest.py                # InMemoryRunner fixtures and helpers
    ├── test_tool_routing.py       # Prompt → tool routing via LLM (15 tests)
    ├── test_conversation_flow.py  # Multi-turn session continuity (3 tests)
    └── test_response_quality.py   # Response content validation (2 tests)
```

## Quick Start

```bash
# Run unit tests only (no external dependencies)
.venv/bin/python -m pytest tests/unit/ -v

# Run all tests except integration
.venv/bin/python -m pytest tests/ -m "not integration" -v

# Run integration tests (requires ADC: gcloud auth application-default login)
.venv/bin/python -m pytest tests/integration/ -v -m integration

# Run everything
.venv/bin/python -m pytest tests/ -v
```

## Unit Tests

Unit tests mock all external dependencies (GCP APIs, Vertex AI, GCS). They verify
internal logic without network calls and run in CI without credentials.

### What They Cover

| File | What It Tests |
|------|---------------|
| `test_agent.py` | Agent creation, model selection, tool count (24), tool names, instruction content |
| `test_config.py` | Config loading from env vars, defaults, GPU list parsing |
| `test_skills.py` | All 24 skill wrappers call the correct registry tool with correct args |
| `test_tool_registry.py` | Lazy initialization, singleton behavior, 25 tools registered, unknown tool error |

### Key Fixture: `mock_env_vars`

Defined in `tests/conftest.py`, this fixture reads `tests/.env.test` and injects all
variables into the test environment via `monkeypatch.setenv`. Most tests depend on it
directly or indirectly.

### Patching Pattern for Skill Tests

The skill wrapper functions import `get_tool` from `_tool_registry` at module level:

```python
# foldrun_app/skills/job_submission/tools.py
from foldrun_app.skills._tool_registry import get_tool

def submit_monomer_prediction(sequence, ...):
    return get_tool('af2_submit_monomer').run({...})
```

Because Python caches modules in `sys.modules`, once a module is imported the local
`get_tool` name is bound to the real function. Patching at the registry definition
site (`foldrun_app.skills._tool_registry.get_tool`) has no effect on already-imported
modules.

**The fix**: patch at each skill module's **use site**:

```python
# Correct - patches the name where it's used
patch("foldrun_app.skills.job_submission.tools.get_tool", return_value=mock)

# Incorrect - doesn't affect already-imported modules
patch("foldrun_app.skills._tool_registry.get_tool", return_value=mock)
```

The `_patch_get_tool(mock_tool, module_path)` helper in `test_skills.py` handles this.
Module path constants are defined at the top of the file for each skill package.

### Running a Single Test

```bash
# By test class
.venv/bin/python -m pytest tests/unit/test_skills.py::TestJobSubmissionSkills -v

# By specific test
.venv/bin/python -m pytest tests/unit/test_skills.py::TestJobSubmissionSkills::test_submit_monomer_calls_correct_tool -v
```

## Integration Tests

Integration tests use ADK's `InMemoryRunner` to create a real agent instance, send
natural language prompts, and verify the LLM routes to the correct tools. GCP tool
backends are mocked so no real cloud resources are consumed, but the LLM call is real.

### Prerequisites

- Application Default Credentials configured via `gcloud auth application-default login`

### How They Work

```
User Prompt  →  InMemoryRunner  →  Gemini LLM  →  FunctionTool call  →  Mocked backend
                                       ↑                                      ↓
                              Real LLM routing                    Returns {"status": "success"}
```

1. **`InMemoryRunner`** wraps the real agent with in-memory session storage
2. **`RunConfig(max_llm_calls=10)`** prevents runaway loops
3. **`mock_tool_backends`** patches `get_tool` at all 8 skill module use sites so tool execution returns mock data
4. The LLM sees real tool schemas and makes real routing decisions
5. Tests assert which tools were called via `collect_function_calls(events)`

### Key Fixtures (integration/conftest.py)

| Fixture | Description |
|---------|-------------|
| `runner` | `InMemoryRunner` wrapping the real agent with mocked GCP startup |
| `session_id` | Unique `test-session-{uuid}` per test |
| `run_config` | `RunConfig(max_llm_calls=10)` to bound LLM calls |
| `mock_tool_backends` | Patches `get_tool` at all skill use sites |

### Helper Functions

```python
from tests.integration.conftest import make_user_message, collect_function_calls, get_final_text

# Build a user message
msg = make_user_message("Check GPU quota")

# Extract tool call names from events
calls = collect_function_calls(events)  # e.g., ["check_gpu_quota"]

# Get the agent's final text response
text = get_final_text(events)
```

### Test Categories

#### Tool Routing (`test_tool_routing.py`)

Sends a single prompt and asserts the agent picks the right tool:

```python
async def test_check_status(self, runner, session_id, run_config):
    events = []
    async for event in runner.run_async(
        user_id="test",
        session_id=session_id,
        new_message=make_user_message("What is the status of job pipeline-run-abc123?"),
        run_config=run_config,
    ):
        events.append(event)

    calls = collect_function_calls(events)
    assert "check_job_status" in calls
```

#### Conversation Flow (`test_conversation_flow.py`)

Multi-turn tests that reuse the same `session_id` across multiple `run_async` calls
to verify context is maintained:

- Submit a job, then ask about its status
- Query AlphaFold DB, then submit a prediction
- Check infrastructure, then set up missing components

#### Response Quality (`test_response_quality.py`)

Validates the agent's text responses contain expected content:

- Greeting should include the configuration table (Project ID, Region, etc.)
- Invalid FASTA sequence should produce helpful guidance

### Tool Name Convention

`FunctionTool` uses the Python function's `__name__` as the tool name the LLM sees.
Integration test assertions use these names (not registry names):

| LLM Tool Name (function `__name__`) | Registry Name |
|--------------------------------------|---------------|
| `submit_monomer_prediction` | `af2_submit_monomer` |
| `check_job_status` | `af2_check_job_status` |
| `check_infrastructure` | `af2_infra_check` |
| `setup_infrastructure` | `af2_infra_setup` |
| `query_alphafold_db_prediction` | `alphafold_db_get_prediction` |

### pytest Marker

Integration tests are marked with `@pytest.mark.integration`. This marker is
registered in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    "integration: tests that call real LLM (deselect with '-m not integration')",
]
```

Exclude them in CI with:

```bash
.venv/bin/python -m pytest tests/ -m "not integration"
```

## Writing New Tests

### Adding a Unit Test for a New Skill Wrapper

1. Add the wrapper function to the appropriate `foldrun_app/skills/<domain>/tools.py`
2. Add the module path constant if it's a new skill package:
   ```python
   _MY_NEW_SKILL = "foldrun_app.skills.my_new_skill.tools"
   ```
3. Add a test class or test method:
   ```python
   def test_my_function_calls_correct_tool(self):
       mock = _mock_tool({"status": "success"})

       with _patch_get_tool(mock, _MY_NEW_SKILL) as mock_get:
           from foldrun_app.skills.my_new_skill import my_function

           result = my_function(arg1="value1")

           mock_get.assert_called_with("registry_tool_name")
           args = mock.run.call_args[0][0]
           assert args["arg1"] == "value1"
   ```

### Adding an Integration Test for Tool Routing

1. Add a test method in `TestToolRouting`:
   ```python
   async def test_my_new_tool(self, runner, session_id, run_config):
       events = []
       async for event in runner.run_async(
           user_id="test",
           session_id=session_id,
           new_message=make_user_message("Natural language prompt that should trigger my_tool"),
           run_config=run_config,
       ):
           events.append(event)

       calls = collect_function_calls(events)
       assert "my_tool_function_name" in calls
   ```

2. If the LLM could reasonably pick from multiple tools, use set intersection:
   ```python
   acceptable_tools = {"tool_a", "tool_b"}
   assert acceptable_tools & set(calls), f"Expected one of {acceptable_tools}, got {calls}"
   ```

### Updating Tool Counts

When adding new tools, update these locations:

| Location | What to Update |
|----------|----------------|
| `foldrun_app/agent.py` | Comment: "Build the list of all N native FunctionTools" |
| `tests/unit/test_agent.py` | `assert len(agent.tools) == N` and `expected` set in `test_agent_tool_names_complete` |
| `tests/unit/test_tool_registry.py` | `expected_tools` list and `assert len(reg._agents) == N` |
| `pyproject.toml` | `packages` list under `[tool.setuptools]` if new skill package |

## Test Environment

Test environment variables are stored in `tests/.env.test`:

```
GCP_PROJECT_ID=test-project
GCP_REGION=us-central1
GCS_BUCKET_NAME=test-af2-bucket
FILESTORE_ID=test-filestore
ALPHAFOLD_COMPONENTS_IMAGE=us-central1-docker.pkg.dev/.../alphafold-components:latest
GEMINI_MODEL=gemini-flash-latest
```

These values are injected by the `mock_env_vars` fixture. They don't need to point to
real resources for unit tests — only integration tests need ADC configured.

## CI Configuration

Recommended CI setup:

```yaml
# Run only unit tests (no credentials needed)
- name: Unit Tests
  run: .venv/bin/python -m pytest tests/ -m "not integration" -v

# Optional: run integration tests with a service account
- name: Integration Tests
  env:
    GOOGLE_APPLICATION_CREDENTIALS: ${{ secrets.GOOGLE_APPLICATION_CREDENTIALS }}
    GOOGLE_GENAI_USE_VERTEXAI: "true"
  run: .venv/bin/python -m pytest tests/integration/ -v -m integration
```
