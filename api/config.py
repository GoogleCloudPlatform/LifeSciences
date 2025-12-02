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
