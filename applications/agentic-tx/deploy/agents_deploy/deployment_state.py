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

"""Deployment state management."""

import json
import os

from typing import Any


class DeploymentState:
    """Manages deployment state persistence for agent deployments."""

    def __init__(self, *, package_name: str, google_cloud_project: str, base_dir: str = "./deployments"):
        """Initialize deployment state manager.

        Args:
            package_name: Agent package name (e.g., "onedrive-agent")
            google_cloud_project: GCP project ID
            base_dir: Base directory for deployment state files
        """
        self.package_name = package_name
        self.google_cloud_project = google_cloud_project
        self.base_dir = base_dir
        self._state_file = self._get_state_file_path()
        self._state: dict[str, Any] | None = None

    @staticmethod
    def from_file(state_file: str) -> "DeploymentState":
        """Load deployment state from a specific file path.

        Args:
            state_file: Path to the state file

        Returns:
            DeploymentState instance with loaded state

        Raises:
            FileNotFoundError: If state file doesn't exist
        """
        if not os.path.exists(state_file):
            raise FileNotFoundError(f"State file not found: {state_file}")

        # Extract package name and project from filename
        # Format: {package_name}_{google_cloud_project}.json
        filename = os.path.basename(state_file)
        name_without_ext = filename.replace(".json", "")
        parts = name_without_ext.rsplit("_", 1)

        if len(parts) != 2:
            raise ValueError(
                f"Invalid state file name format: {filename}. "
                "Expected format: {{package_name}}_{{project}}.json"
            )

        package_name, google_cloud_project = parts
        base_dir = os.path.dirname(state_file)

        # Create instance
        instance = DeploymentState(
            package_name=package_name,
            google_cloud_project=google_cloud_project,
            base_dir=base_dir,
        )

        # Load the state
        instance.load()

        return instance

    def _get_state_file_path(self) -> str:
        """Get the full path to the state file."""
        filename = f"{self.package_name}_{self.google_cloud_project}.json"
        return os.path.join(self.base_dir, filename)

    @property
    def state_file(self) -> str:
        """Get the state file path."""
        return self._state_file

    @property
    def exists(self) -> bool:
        """Check if state file exists."""
        return os.path.exists(self._state_file)

    def load(self) -> dict[str, Any] | None:
        """Load deployment state from file.

        Returns:
            Deployment state dictionary or None if file doesn't exist
        """
        if not self.exists:
            print("No deployment state file found")
            self._state = None
            return None

        if not self._state:
            with open(self._state_file) as f:
                self._state = json.load(f)
        print(f"Loaded deployment state from: {self._state_file}")
        return self._state

    def save(self, *, state: dict[str, Any]) -> None:
        """Save deployment state to file.

        Args:
            state: Deployment state dictionary
        """
        os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
        with open(self._state_file, "w") as f:
            json.dump(state, f, indent=2)
        self._state = state
        print(f"Deployment state saved to: {self._state_file}")

    def delete(self) -> None:
        """Delete deployment state file."""
        if self.exists:
            os.remove(self._state_file)
            self._state = None
            print(f"Deleted deployment state file: {self._state_file}")

    def __repr__(self) -> str:
        """Return repr string for DeploymentState."""
        return (
            f"DeploymentState(package_name={self.package_name!r}, "
            f"google_cloud_project={self.google_cloud_project!r}, "
            f"base_dir={self.base_dir!r})"
        )

    def __str__(self) -> str:
        """Return formatted deployment state string."""
        if self._state is None:
            return "No state loaded. Call load() first."

        lines = ["\nDeployment State:"]

        if self._state.get("authorization"):
            auth = self._state["authorization"]
            lines.append("\n Authorization:")
            lines.append(f"     Resource: {auth['resource_name']}")
            lines.append(f"     ID: {auth['authorization_id']}")

        if self._state.get("agent_engine"):
            engine = self._state["agent_engine"]
            lines.append("\n Agent Engine:")
            lines.append(f"     Resource: {engine['resource_name']}")
            lines.append(f"     Display Name: {engine['display_name']}")
            lines.append(f"     State: {engine.get('state', 'Unknown')}")

        if self._state.get("agentspace_agent"):
            agentspace = self._state["agentspace_agent"]
            lines.append("\n Agentspace Agent:")
            lines.append(f"     Name: {agentspace['name']}")
            lines.append(f"     Display Name: {agentspace['display_name']}")
            lines.append(f"     State: {agentspace.get('state', 'Unknown')}")

        return "\n".join(lines)

    def print(self) -> None:
        """Pretty print deployment state."""
        print(str(self))

    def get_project(self) -> str | None:
        return self._state.get("project") if self._state else None

    def get_location(self) -> str | None:
        return self._state.get("location") if self._state else None

    def get_authorization(self) -> dict[str, Any] | None:
        """Get authorization data from state."""
        return self._state.get("authorization") if self._state else None

    def get_agent_engine(self) -> dict[str, Any] | None:
        """Get agent engine data from state."""
        return self._state.get("agent_engine") if self._state else None

    def get_agentspace_agent(self) -> dict[str, Any] | None:
        """Get agentspace agent data from state."""
        return self._state.get("agentspace_agent") if self._state else None
