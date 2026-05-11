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

"""Task metadata loader with caching.

Single Responsibility: Load and cache task metadata.
"""

import json

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class TaskMetadata(BaseModel):
    """Complete task metadata from task_metadata.json."""

    category: str = Field(description="Task category")
    description: str = Field(description="Description of the task")
    required_variables: list[str] = Field(default_factory=list, description="Required input variables")
    output_type: str = Field(description="Type of output produced")
    prompt: str = Field(description="Prompt template for the task")
    output_labels: list[str] = Field(default_factory=list, description="Labels for output classes")


class TaskDefinition(BaseModel):
    """Task definition with essential fields for execution."""

    task_id: str = Field(description="Unique identifier for the task")
    description: str = Field(description="Description of the task")
    required_variables: list[str] = Field(default_factory=list, description="Required input variables")
    output_type: str | None = Field(default=None, description="Type of output produced")
    output_labels: list[str] | None = Field(default=None, description="Labels for output classes")


class TaskMetadataLoader:
    """Singleton class to load and cache task metadata.

    Loads task_metadata.json once and caches all derived data structures
    to avoid repeated file I/O and processing.
    """

    _instance: Optional["TaskMetadataLoader"] = None
    _initialized: bool = False

    def __new__(cls) -> "TaskMetadataLoader":
        """Singleton pattern - only create one instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Initialize loader. Only loads data on first instantiation."""
        if not TaskMetadataLoader._initialized:
            self._load_metadata()
            TaskMetadataLoader._initialized = True

    def _load_metadata(self) -> None:
        """Load task metadata from JSON file and build caches."""
        data_path = Path(__file__).parent / "task_metadata.json"

        with open(data_path) as f:
            self._all_tasks: dict = json.load(f)

        # Build category index (cache for O(1) lookup)
        self._category_index: dict[str, list[dict]] = {}
        for task_id, metadata in self._all_tasks.items():
            category = metadata.get("category", "Other")
            if category not in self._category_index:
                self._category_index[category] = []
            self._category_index[category].append({"task_id": task_id, "description": metadata.get("description", "")})

        # Cache sorted category list
        self._category_list: list[str] = sorted(self._category_index.keys())

    @property
    def all_tasks(self) -> dict:
        """Get all tasks metadata."""
        return self._all_tasks

    @property
    def categories(self) -> list[str]:
        """Get sorted list of all categories."""
        return self._category_list

    def get_category_tasks(self, category_name: str) -> list[dict] | None:
        """Get tasks for a specific category.

        Args:
            category_name: Name of the category

        Returns:
            List of tasks in category, or None if category not found
        """
        return self._category_index.get(category_name)

    def get_task_metadata(self, task_id: str) -> TaskMetadata | None:
        """Get metadata for a specific task.

        Args:
            task_id: Task identifier

        Returns:
            Task metadata model, or None if not found
        """
        task_data = self._all_tasks.get(task_id)
        if task_data is None:
            return None
        return TaskMetadata(**task_data)

    def map_to_definitions(self, task_ids: list[str]) -> list[TaskDefinition]:
        """Map task IDs to full task definitions.

        Args:
            task_ids: List of task IDs to map

        Returns:
            List of TaskDefinition objects with full metadata
        """
        results = []

        for task_id in task_ids:
            task_info = self.get_task_metadata(task_id)

            if task_info is not None:
                results.append(
                    TaskDefinition(
                        task_id=task_id,
                        description=task_info.description,
                        required_variables=task_info.required_variables,
                        output_type=task_info.output_type,
                        output_labels=task_info.output_labels,
                    )
                )

        return results
