# ==============================================================================
# Installation & Setup
# ==============================================================================

# Install dependencies using uv package manager
install:
	@command -v uv >/dev/null 2>&1 || { echo "uv is not installed. Installing uv..."; curl -LsSf https://astral.sh/uv/0.8.13/install.sh | sh; source $HOME/.local/bin/env; }
	uv sync

# ==============================================================================
# Playground Targets
# ==============================================================================

# Launch local dev playground (Web UI)
playground:
	@echo "==============================================================================="
	@echo "| 🚀 Starting your agent playground (Web UI)...                               |"
	@echo "|                                                                             |"
	@echo "| 💡 Try asking: help me submit a protein structure prediction                |"
	@echo "|                                                                             |"
	@echo "| 🔍 IMPORTANT: Select the 'foldrun_app' folder to interact with your agent.          |"
	@echo "==============================================================================="
	uv run adk web . --port 8501 --reload_agents

# Launch local CLI agent
run:
	@echo "==============================================================================="
	@echo "| 🚀 Starting your agent (CLI)...                                             |"
	@echo "==============================================================================="
	uv run python foldrun_app/cli.py

# Run a single query
run-query:
	@uv run python foldrun_app/cli.py --query "$(QUERY)"

# ==============================================================================
# Backend Deployment Targets
# ==============================================================================

# Deploy the agent remotely
deploy:
	# Export dependencies to requirements file using uv export.
	(uv export --no-hashes --no-header --no-dev --no-emit-project --no-annotate > foldrun_app/app_utils/.requirements.txt 2>/dev/null || \
	uv export --no-hashes --no-header --no-dev --no-emit-project > foldrun_app/app_utils/.requirements.txt) && \
	uv run -m foldrun_app.app_utils.deploy \
		--source-packages=./foldrun_app \
		--entrypoint-module=foldrun_app.agent_engine_app \
		--entrypoint-object=agent_engine \
		--requirements-file=foldrun_app/app_utils/.requirements.txt

# Alias for 'make deploy' for backward compatibility
backend: deploy

# ==============================================================================
# Testing & Code Quality
# ==============================================================================

# Run unit and integration tests
test:
	uv sync --dev
	uv run pytest tests/unit && uv run pytest tests/integration

# Run code quality checks (codespell, ruff, ty)
lint:
	uv sync --dev --extra lint
	uv run codespell
	uv run ruff check . --diff
	uv run ruff format . --check --diff
	uv run ty check .

# ==============================================================================
# Gemini Enterprise Integration
# ==============================================================================

# Register the deployed agent to Gemini Enterprise
# Usage: make register-gemini-enterprise (interactive - will prompt for required details)
# For non-interactive use, set env vars: ID or GEMINI_ENTERPRISE_APP_ID (full GE resource name)
# Optional env vars: GEMINI_DISPLAY_NAME, GEMINI_DESCRIPTION, GEMINI_TOOL_DESCRIPTION, AGENT_ENGINE_ID
register-gemini-enterprise:
	@uvx agent-starter-pack@0.25.0 register-gemini-enterprise
