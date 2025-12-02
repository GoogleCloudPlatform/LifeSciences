"""
Health check endpoints.

Provides system health and status information.
"""

from datetime import datetime

from fastapi import APIRouter

from api import __version__
from api.models.schemas import HealthResponse

router = APIRouter(
    prefix="/health",
    tags=["health"],
)


@router.get(
    "",
    response_model=HealthResponse,
    summary="Health Check",
    description="Check the health status of the API service",
)
async def health_check() -> HealthResponse:
    """
    Health check endpoint.

    Returns:
        Health status information including version and timestamp
    """
    return HealthResponse(
        status="healthy",
        version=__version__,
        timestamp=datetime.utcnow(),
    )
