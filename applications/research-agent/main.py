"""Entry point for ADK deployment."""

# Import the app directly to avoid any circular import or initialization issues
from research_agent.agent import app

# Make app available at module level for ADK
__all__ = ["app"]
