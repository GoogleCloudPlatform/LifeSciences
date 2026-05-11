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

"""Configuration system for TxGemma agents using Pydantic Settings.

This module provides a hierarchical configuration system that:
- Loads base settings from environment variables
- Supports derived settings for specialized agents
- Allows constructor-time overrides
- Provides factory methods for ADK objects
"""

import os

from textwrap import dedent
from typing import Any

from pydantic import Field
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from google.adk.models import Gemini
from google.adk.planners import BuiltInPlanner
from google.genai import types

from .adk.gemini3 import Gemini3


class GeminiAgentConfig(BaseSettings):
    """Base configuration for Gemini-based agents.

    All settings can be loaded from environment variables with the prefix
    GEMINI_ (e.g., GEMINI_DEFAULT_MODEL_NAME=gemini-3-flash-preview).

    Settings can also be overridden at construction time.

    Example:
        # Load from environment variables
        config = GeminiAgentConfig()

        # Override specific settings
        config = GeminiAgentConfig(
            model_name="gemini-3-pro-preview",
            temperature=0.7
        )

        # Use factory methods
        model = config.get_model()
        gen_config = config.get_generate_content_config()
        planner = config.get_planner()
    """

    model_config = SettingsConfigDict(
        env_prefix="GEMINI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Model configuration
    model_name: str = Field(default="gemini-3-flash-preview", description="Gemini model name to use")

    # Generation parameters
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        description="Sampling temperature",
    )

    top_p: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Nucleus sampling parameter",
    )

    top_k: int | None = Field(
        default=None,
        ge=1,
        description="Top-k sampling parameter",
    )

    # Thinking configuration
    include_thoughts: bool = Field(
        default=False,
        description="Whether to include thinking process in responses",
    )

    thinking_budget: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Thinking budget (number of thinking tokens). "
            "Only used for older Gemini models. If set, takes precedence over thinking_level."
        ),
    )

    thinking_level: str = Field(
        default="LOW",
        description="Thinking level (THINKING_LEVEL_UNSPECIFIED, LOW, MEDIUM, HIGH, MINIMAL)",
    )

    # Retry configuration
    retry_initial_delay: int = Field(
        default=5,
        ge=0,
        description="Initial retry delay in seconds",
    )

    retry_attempts: int = Field(
        default=10,
        ge=1,
        description="Number of retry attempts",
    )

    use_interactions_api: bool = Field(default=False, description="True to use the interaction Api, false otherwise.")
    disable_parallel_tool_calling: bool = Field(default=False, description="Gemini Enterprise issue")

    def _get_thinking_level_enum(self) -> types.ThinkingLevel:
        """Convert thinking level string to enum.

        Returns:
            ThinkingLevel enum value, defaults to LOW if invalid
        """
        level_map = {
            "THINKING_LEVEL_UNSPECIFIED": types.ThinkingLevel.THINKING_LEVEL_UNSPECIFIED,
            "LOW": types.ThinkingLevel.LOW,
            "MEDIUM": types.ThinkingLevel.MEDIUM,
            "HIGH": types.ThinkingLevel.HIGH,
            "MINIMAL": types.ThinkingLevel.MINIMAL,
        }
        return level_map.get(self.thinking_level.upper(), types.ThinkingLevel.LOW)

    def _use_interactions_api(self) -> bool:
        """Get whether to enable the interaction api

        The interaction api currently only works with the api platform api key. So the
        api key must exist and interaction api set to turn
        """
        return bool(self.use_interactions_api and self._is_api_key_set())

    def _is_api_key_set(self) -> bool:
        return bool(os.environ.get("GOOGLE_API_KEY"))

    def get_safety_settings(self) -> list[types.SafetySetting]:
        """Get safety settings for content generation.

        This method can be overridden in derived classes to provide
        agent-specific safety configurations.

        Returns:
            List of SafetySetting configurations
        """
        safety_settings = [
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                threshold=types.HarmBlockThreshold.OFF,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                threshold=types.HarmBlockThreshold.OFF,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                threshold=types.HarmBlockThreshold.OFF,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                threshold=types.HarmBlockThreshold.OFF,
            ),
        ]
        return safety_settings

    def get_model(self) -> Gemini:
        """Create and return a configured Gemini model instance.

        Returns:
            Configured Gemini model with retry options
        """
        retry_options = types.HttpRetryOptions(
            initial_delay=self.retry_initial_delay,
            attempts=self.retry_attempts,
        )

        # The interaction api

        if self.model_name.startswith("gemini-3"):
            return Gemini3(
                model=self.model_name,
                retry_options=retry_options,
                use_interactions_api=self._use_interactions_api(),
            )
        else:
            return Gemini(
                model=self.model_name,
                retry_options=retry_options,
                use_interactions_api=self._use_interactions_api(),
            )

    def get_generate_content_config(self) -> types.GenerateContentConfig:
        """Create and return generation configuration.

        Returns:
            GenerateContentConfig with temperature, top_p, top_k, and safety settings
        """
        return types.GenerateContentConfig(
            temperature=self.temperature,
            top_p=self.top_p,
            top_k=self.top_k,
            safety_settings=self.get_safety_settings(),
        )

    def get_planner(self) -> BuiltInPlanner:
        """Create and return a configured planner.

        Uses thinking_budget if explicitly set (not None), otherwise uses thinking_level.
        This prevents API errors since thinking_budget and thinking_level cannot be used together.

        Default behavior: Uses thinking_level="LOW"
        Override: Set thinking_budget to use token-based thinking (for older models)

        Returns:
            BuiltInPlanner with thinking configuration
        """
        # If thinking_budget is explicitly set, use it instead of thinking_level
        if self.thinking_budget is not None:
            thinking_config = types.ThinkingConfig(
                include_thoughts=self.include_thoughts,
                thinking_budget=self.thinking_budget,
            )
        else:
            # Default: use thinking_level
            thinking_config = types.ThinkingConfig(
                include_thoughts=self.include_thoughts,
                thinking_level=self._get_thinking_level_enum(),
            )

        return BuiltInPlanner(thinking_config=thinking_config)

    def get_instruction_prompt(self, prompt: str) -> str:
        """Disables parallel function toolcalling by including a prompt instruction for GE"""
        disable_parallel_tool_calling_prompt = dedent("""\
            IMPORTANT: Do not make parallel tool calls. Only call one function at a time.
        """)

        if self.disable_parallel_tool_calling:
            return prompt + "\n\n" + disable_parallel_tool_calling_prompt
        return prompt


class FallbackEnvSettingsSource(PydanticBaseSettingsSource):
    """Custom settings source that checks both agent-specific and parent env vars.

    This enables automatic fallback from agent-specific env vars (e.g., BIOLOGY_AGENT_MODEL_NAME)
    to parent env vars (e.g., GEMINI_MODEL_NAME) without requiring manual AliasChoices on each field.

    Priority order:
    1. Agent-specific env var (e.g., BIOLOGY_AGENT_MODEL_NAME)
    2. Parent env var (e.g., GEMINI_MODEL_NAME)
    3. Not found (use default)
    """

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        agent_prefix: str,
        parent_prefix: str = "GEMINI_",
    ):
        """Initialize the fallback settings source.

        Args:
            settings_cls: The settings class being configured
            agent_prefix: Agent-specific prefix (e.g., "BIOLOGY_AGENT_")
            parent_prefix: Parent prefix to fall back to (default: "GEMINI_")
        """
        super().__init__(settings_cls)
        self.agent_prefix = agent_prefix.upper()
        self.parent_prefix = parent_prefix.upper()

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        """Get field value with fallback logic.

        This method is required by PydanticBaseSettingsSource but we implement
        the actual logic in __call__ for simplicity.

        Args:
            field: FieldInfo object for the field
            field_name: Name of the field

        Returns:
            Tuple of (value, key, is_none)
        """
        # This method is required by the abstract base class but we don't use it
        # since we override __call__ directly
        return None, field_name, True

    def __call__(self) -> dict[str, Any]:
        """Return all field values as a dict.

        Checks environment variables with agent-specific prefix first, then falls back
        to parent prefix if not found.

        Returns:
            Dictionary of field names to values from environment variables
        """
        d: dict[str, Any] = {}

        for field_name in self.settings_cls.model_fields:
            field_name_upper = field_name.upper()

            # Try agent-specific env var first
            agent_var = f"{self.agent_prefix}{field_name_upper}"
            if agent_var in os.environ:
                d[field_name] = os.environ[agent_var]
                continue

            # Fall back to parent env var
            parent_var = f"{self.parent_prefix}{field_name_upper}"
            if parent_var in os.environ:
                d[field_name] = os.environ[parent_var]

        return d
