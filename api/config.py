# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
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

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    Attributes:
        gemini_api_key: Google Gemini API key for video analysis
        api_host: Host address for the API server
        api_port: Port number for the API server
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        cors_origins: Comma-separated list of allowed CORS origins
    """

    gemini_api_key: str
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000,http://localhost:8080"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

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
