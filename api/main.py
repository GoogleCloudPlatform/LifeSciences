# Copyright 2026 Google LLC
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

"""
Sentinel API - Medical Literature Review Tool

FastAPI application for analyzing YouTube medical videos using Google's Gemini AI.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.cloud import storage

from api import __version__
from api.config import settings
from api.routes import analysis_router, health_router, storage_router
from api.services.gemini_client import GeminiClient

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
    logger.info(f"GCS Bucket configured: {settings.gcs_bucket_name}")

    # Initialize shared Storage Client
    app.state.storage_client = None
    if settings.google_genai_use_vertexai:
        try:
            app.state.storage_client = storage.Client(
                project=settings.google_cloud_project
            )
            logger.info("GCS Storage client initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Storage Client: {e}")

    # Initialize shared Gemini Client
    try:
        app.state.gemini_client = GeminiClient(storage_client=app.state.storage_client)
        logger.info("Gemini API client initialized")
    except Exception as e:
        logger.error(f"Failed to initialize Gemini Client: {e}")
        app.state.gemini_client = None

    yield

    # Shutdown
    if app.state.storage_client:
        app.state.storage_client.close()
    if app.state.gemini_client:
        await app.state.gemini_client.close()
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
app.include_router(storage_router)

# Mount static files if directory exists (for Docker build)
# We check if /app/static exists (Docker) or fallback to local development path if needed
static_dir = "/app/static" if os.path.exists("/app/static") else "frontend/dist"

if os.path.exists(static_dir):
    app.mount("/assets", StaticFiles(directory=f"{static_dir}/assets"), name="assets")


@app.get("/", include_in_schema=False)
async def root():
    """
    Serve the Single Page Application (SPA) entry point.
    """
    if os.path.exists(static_dir):
        return FileResponse(f"{static_dir}/index.html")

    # Fallback for local dev without build
    return {
        "name": "Sentinel API",
        "version": __version__,
        "description": "Medical literature review tool for YouTube videos. Frontend not found (run 'npm run build' or use Docker).",
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
