"""
API route handlers.
"""

from api.routes.analysis import router as analysis_router
from api.routes.health import router as health_router

__all__ = [
    "analysis_router",
    "health_router",
]
