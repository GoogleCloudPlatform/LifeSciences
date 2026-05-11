"""Entry point for ADK Agent Engine deployment."""

# Import the app from the current package using relative import
# This file is inside the research_agent package, so use relative import
from . import app

# Export for ADK
__all__ = ["app"]
