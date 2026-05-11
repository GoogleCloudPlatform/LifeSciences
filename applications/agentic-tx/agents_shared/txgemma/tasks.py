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

"""Function tools for Task Selection Agent.

Single Responsibility: Provide function tools for the agent.
"""

from .metadata_loader import TaskMetadataLoader


class TaskTools:
    """Tools class for Task Selection Agent."""

    def __init__(self, loader: TaskMetadataLoader):
        """Initialize with TaskMetadataLoader dependency.

        Args:
            loader: TaskMetadataLoader instance
        """
        self.loader = loader

    def get_tasks_in_category(self, category_name: str) -> str:
        """Get list of tasks in a TxGemma category.

        Args:
            category_name: Name of the category (e.g., "Cardiotoxicity (hERG)", "CYP Metabolism")

        Returns:
            Plain text string with tasks, one per line: "task_id - description"
        """
        try:
            # Get tasks from cached index (O(1) lookup)
            tasks = self.loader.get_category_tasks(category_name)

            if tasks is None:
                # Category not found
                available_categories = self.loader.categories
                categories_preview = ", ".join(available_categories[:10])
                return f"ERROR: Category '{category_name}' not found. Available categories: {categories_preview}..."

            # Format tasks as plain text
            lines = [f"{task['task_id']} - {task['description']}" for task in tasks]
            return "\n".join(lines)

        except Exception as e:
            return f"ERROR: Failed to load tasks: {e!s}"
