"""
Sentinel API - Medical Literature Review Tool

FastAPI application for analyzing YouTube medical videos using Google's Gemini AI.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import __version__
from api.config import settings
from api.routes import analysis_router, health_router

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.

    Handles startup and shutdown events for the FastAPI application.
    """
    # Startup
    logger.info(f"Starting Sentinel API v{__version__}")
    logger.info(f"Log level: {settings.log_level}")
    logger.info("Gemini API client initialized")

    yield

    # Shutdown
    logger.info("Shutting down Sentinel API")


# Create FastAPI application
app = FastAPI(
    title="Sentinel API",
    description="Medical literature review tool for analyzing YouTube videos using AI",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health_router)
app.include_router(analysis_router)


@app.get(
    "/",
    summary="Root Endpoint",
    description="API information and welcome message",
)
async def root():
    """
    Root endpoint providing API information.

    Returns:
        Basic API information and links to documentation
    """
    return {
        "name": "Sentinel API",
        "version": __version__,
        "description": "Medical literature review tool for YouTube videos",
        "docs": "/docs",
        "health": "/health",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
    )
