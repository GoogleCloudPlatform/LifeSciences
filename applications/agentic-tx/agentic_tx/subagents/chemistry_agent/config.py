# Copyright 2025 Google LLC
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

"""Configuration for Chemistry Agent.

This module provides specialized configuration for the chemistry agent,
inheriting from the base GeminiAgentConfig.
"""

from agentic_tx.config import FallbackEnvSettingsSource, GeminiAgentConfig
from pydantic import Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class ChemistryAgentConfig(GeminiAgentConfig):
    """Configuration for Chemistry Agent.

    Inherits from GeminiAgentConfig and provides chemistry-specific
    settings. Uses CHEMISTRY_AGENT_ prefix for environment variables.

    Environment variables with the CHEMISTRY_AGENT_ prefix will override
    the base GEMINI_ settings.

    Example:
        # Load from environment variables
        # GEMINI_DEFAULT_MODEL_NAME=gemini-3-flash-preview
        # CHEMISTRY_AGENT_TEMPERATURE=0.1
        config = ChemistryAgentConfig()

        # Override specific settings at construction
        config = ChemistryAgentConfig(
            model_name="gemini-3-pro-preview",
            temperature=0.2,
            thinking_level="MEDIUM"
        )

        # Use factory methods to create ADK objects
        model = config.get_model()
        gen_config = config.get_generate_content_config()
        planner = config.get_planner()
    """

    model_config = SettingsConfigDict(
        env_prefix="CHEMISTRY_AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Chemistry tasks need some precision but can tolerate minor variations
    temperature: float = Field(
        default=0.1,
        ge=0.0,
        le=2.0,
        description="Sampling temperature for chemistry tasks",
    )

    # Moderate thinking for structure conversions and property analysis
    thinking_level: str = Field(
        default="LOW",
        description="Thinking level for chemistry tasks",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Override settings sources to use fallback env lookup.

        This enables automatic fallback from CHEMISTRY_AGENT_* env vars to GEMINI_* env vars.

        Priority:
        1. Constructor arguments (init_settings)
        2. CHEMISTRY_AGENT_* environment variables
        3. GEMINI_* environment variables (fallback)
        4. .env file
        5. Secrets file
        """
        return (
            init_settings,
            FallbackEnvSettingsSource(
                settings_cls,
                agent_prefix="CHEMISTRY_AGENT_",
                parent_prefix="GEMINI_",
            ),
            dotenv_settings,
            file_secret_settings,
        )
