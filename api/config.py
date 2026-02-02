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
Configuration management for Sentinel API.

This module handles environment variables and application settings using Pydantic.
"""

from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    Attributes:
        google_cloud_project: Google Cloud Project ID for Vertex AI (optional)
        google_cloud_location: Google Cloud Location for Vertex AI (default: global)
        gemini_api_key: Google Gemini API key for video analysis (optional if using Vertex AI)
        gemini_model_fast: Model name for fast processing (default: gemini-3-flash-preview)
        gemini_model_powerful: Model name for complex processing (default: gemini-3-pro-preview)
        google_genai_use_vertexai: Whether to explicitly use Vertex AI (default: False)
        api_host: Host address for the API server
        api_port: Port number for the API server
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        cors_origins: Comma-separated list of allowed CORS origins
    """

    google_cloud_project: Optional[str] = None
    google_cloud_location: str = "global"
    gemini_api_key: Optional[str] = None
    gemini_model_fast: str = "gemini-3-flash-preview"
    gemini_model_powerful: str = "gemini-3-pro-preview"
    google_genai_use_vertexai: bool = False
    gcs_bucket_name: Optional[str] = None
    gcs_media_folder: str = "dev"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000,http://localhost:8080"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @model_validator(mode="after")
    def validate_genai_config(self):
        if self.google_genai_use_vertexai:
            if not self.google_cloud_project:
                raise ValueError(
                    "GOOGLE_CLOUD_PROJECT is required when GOOGLE_GENAI_USE_VERTEXAI is True"
                )
            if not self.google_cloud_location:
                raise ValueError(
                    "GOOGLE_CLOUD_LOCATION is required when GOOGLE_GENAI_USE_VERTEXAI is True"
                )
        else:
            if not self.gemini_api_key:
                raise ValueError(
                    "GEMINI_API_KEY is required when GOOGLE_GENAI_USE_VERTEXAI is False"
                )
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        """
        Parse CORS origins from comma-separated string to list.

        Returns:
            List of allowed CORS origin URLs
        """
        return [origin.strip() for origin in self.cors_origins.split(",")]


# Global settings instance
settings = Settings()
