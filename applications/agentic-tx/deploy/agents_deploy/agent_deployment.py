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

"""High-level deployment orchestration."""

import os

from typing import Any

from dotenv import load_dotenv

from .agent_engine import AgentEngineManager
from .agentspace import AgentspaceManager
from .authorization import AuthorizationManager
from .build import (
    load_environment_values,
    prepare_agent_deployment_dependencies,
)
from .client import GoogleCloudRestApiClient
from .deployment_settings import DeploymentSettings
from .deployment_state import DeploymentState


class AgentDeployment:
    """High-level deployment orchestration with stateful, idempotent deployments."""

    def __init__(
        self,
        *,
        package_name: str,
        agent_env_file: str,
        deploy_env_file: str,
    ):
        """Initialize deployment orchestrator.

        Args:
            package_name: Agent package name (e.g., "onedrive-agent")
            agent_env_file: Path to .env.agent file
            deploy_env_file: Path to .env.deploy file
        """
        self.package_name = package_name
        self.agent_env_file = agent_env_file
        self.deploy_env_file = deploy_env_file

        # Load deployment settings
        self.settings = DeploymentSettings(_env_file=deploy_env_file)

        # Initialize managers
        self.client = GoogleCloudRestApiClient(project_number=self.settings.project_number)
        self.auth_manager = AuthorizationManager(client=self.client)
        self.engine_manager = AgentEngineManager(
            project=self.settings.google_cloud_project,
            location=self.settings.google_cloud_location,
        )
        self.agentspace_manager = AgentspaceManager(client=self.client)

        # Initialize deployment state
        self.deployment_state = DeploymentState(
            package_name=package_name,
            google_cloud_project=self.settings.google_cloud_project,
        )

        # Load agent environment for authorization ID
        load_dotenv(agent_env_file)
        self.authorization_id = os.getenv("AGENTENGINE_AUTHORIZATION_ID")

    def validate(self) -> None:
        """Validate deployment configuration.

        Raises:
            ValueError: If configuration is invalid
        """
        if not self.authorization_id:
            return

        # Validate OAuth config if authorization is configured
        if any(
            [
                self.settings.oauth2_client_id,
                self.settings.oauth2_authorization_url,
                self.settings.oauth2_token_url,
            ]
        ):
            if not all(
                [
                    self.settings.oauth2_client_id,
                    self.settings.oauth2_authorization_url,
                    self.settings.oauth2_token_url,
                ]
            ):
                raise ValueError("Partial OAuth2 configuration found. Either provide all OAuth2 settings or none.")

        print("Deployment configuration validated successfully")

    def get_state_file(self) -> str:
        """Get the path to the deployment state file."""
        return self.deployment_state.state_file

    def load_state(self) -> bool:
        """Load existing deployment state if available.

        Returns:
            True if state was loaded, False otherwise
        """
        return self.deployment_state.load() is not None

    def print_state(self) -> None:
        """Print the current deployment state."""
        self.deployment_state.print()

    def get_project(self) -> str:
        """Get the GCP project ID."""
        return self.settings.google_cloud_project

    def get_location(self) -> str:
        """Get the GCP location."""
        return self.settings.google_cloud_location

    def get_display_name(self) -> str:
        """Get the agent display name."""
        return self.settings.display_name

    def get_agentspace_app_id(self) -> str | None:
        """Get the Agentspace app ID."""
        return self.settings.agentspace_app_id

    def get_staging_bucket(self) -> str:
        return self.settings.staging_bucket

    def deploy(self) -> DeploymentState:
        """Full deployment: authorization → agent engine → agentspace registration.

        Returns:
            DeploymentState with saved deployment information
        """
        # Load existing state if available
        self.deployment_state.load()

        print("Starting deployment...")
        print("=" * 60)

        # Step 1: Create/verify authorization (if OAuth configured)
        auth_resource = None
        if all(
            [
                self.settings.oauth2_client_id,
                self.settings.oauth2_authorization_url,
                self.settings.oauth2_token_url,
            ]
        ):
            print(f"Creating/verifying authorization: {self.authorization_id}")
            auth_resource = self.auth_manager.create_or_update(
                authorization_id=self.authorization_id,
                oauth2_config=self.settings.oauth2_config,
                location=self.settings.authorization_location,
            )
            print(f"Authorization ready: {auth_resource.resource_name}")

        # Step 2: Prepare deployment dependencies
        print(f"Preparing deployment dependencies for {self.package_name}...")
        deployment_deps = prepare_agent_deployment_dependencies(package_name=self.package_name)

        # Step 3: Load agent environment variables
        agent_env_vars = load_environment_values(self.agent_env_file)

        # Step 4: Load agent app dynamically
        from .loader import load_agent_app

        print(f"Loading agent application from {self.package_name}...")
        agent_app = load_agent_app(
            package_name=self.package_name,
            agent_env_file=self.agent_env_file,
        )
        print("Agent application loaded")

        # Step 5: Deploy to Agent Engine
        print(f"Deploying agent to Agent Engine: {self.settings.display_name}")
        agent_engine = self.engine_manager.deploy(
            agent_app=agent_app,
            display_name=self.settings.display_name,
            description=self.settings.description,
            env_vars=agent_env_vars,
            package_name=self.package_name,
            requirements=deployment_deps["requirements"],
            extra_packages=[*deployment_deps["extra_packages"], "installation_scripts/install.sh"],
            build_options={"installation_scripts": ["installation_scripts/install.sh"]},
            service_account=self.settings.agent_engine_service_account,
        )
        print(f"Agent Engine deployed: {agent_engine.resource_name}")

        # Step 6: Register with Agentspace (if app_id provided)
        agentspace_agent = None
        if self.settings.agentspace_app_id:
            print(f"Registering agent in Agentspace: {self.settings.agentspace_app_id}")
            auth_resources = [auth_resource.resource_name] if auth_resource else []

            agentspace_agent = self.agentspace_manager.create(
                agentspace_app_id=self.settings.agentspace_app_id,
                display_name=self.settings.display_name,
                description=self.settings.description,
                tool_description=self.settings.tool_description,
                reasoning_engine_resource=agent_engine.resource_name,
                authorization_resources=auth_resources,
            )
            print(f"Agentspace agent registered: {agentspace_agent.name}")

        print("=" * 60)
        print("Deployment complete!")

        # Save deployment state
        state_data: dict[str, Any] = {"project": self.get_project(), "location": self.get_location()}
        if auth_resource:
            state_data["authorization"] = auth_resource.to_dict()
        if agent_engine:
            state_data["agent_engine"] = agent_engine.to_dict()
        if agentspace_agent:
            state_data["agentspace_agent"] = agentspace_agent.to_dict()

        self.deployment_state.save(state=state_data)
        return self.deployment_state

    @staticmethod
    def remove(deployment_state: DeploymentState) -> dict[str, bool]:
        """Remove deployment resources using state.

        Args:
            deployment_state: Deployment state with resource information

        Returns:
            Dictionary with removal status for each component
        """
        # Load state
        state = deployment_state.load()
        if not state:
            print("No deployment state found. Nothing to clean up.")
            return {
                "agentspace_agent": False,
                "agent_engine": False,
                "authorization": False,
            }

        print("Starting cleanup...")
        print("=" * 60)

        # Get resource information from state
        agent_engine_data = deployment_state.get_agent_engine()
        agentspace_agent_data = deployment_state.get_agentspace_agent()
        authorization_data = deployment_state.get_authorization()

        # Load deployment settings from state to initialize managers
        # We need to reconstruct the settings from state
        if not agent_engine_data:
            print("No agent engine resource found in state")
            return {
                "agentspace_agent": False,
                "agent_engine": False,
                "authorization": False,
            }

        # Extract project and location from agent engine resource name
        # Format: projects/{project}/locations/{location}/reasoningEngines/{id}
        resource_name = agent_engine_data["resource_name"]
        parts = resource_name.split("/")
        project = parts[1]
        location = parts[3]

        # Initialize managers
        # Note: We need project_number for client initialization
        from .deployment_settings import get_project_number

        project_number = get_project_number(project)
        client = GoogleCloudRestApiClient(project_number=project_number)
        auth_manager = AuthorizationManager(client=client)
        engine_manager = AgentEngineManager(
            project=project,
            location=location,
        )
        agentspace_manager = AgentspaceManager(client=client)

        results = {
            "agentspace_agent": False,
            "agent_engine": False,
            "authorization": False,
        }

        # Step 1: Remove from Agentspace if registered
        if agentspace_agent_data:
            agentspace_resource_name = agentspace_agent_data["name"]
            print(f"Removing from Agentspace: {agentspace_resource_name}")
            success = agentspace_manager.delete(agent_resource_name=agentspace_resource_name)
            if success:
                print("Removed from Agentspace")
                results["agentspace_agent"] = True
            else:
                print("Agentspace agent not found (may be already deleted)")

        # Step 2: Remove from Agent Engine
        print(f"Removing from Agent Engine: {resource_name}")
        success = engine_manager.delete(resource_name=resource_name)
        if success:
            print("Removed from Agent Engine")
            results["agent_engine"] = True
        else:
            print("Agent engine not found (may be already deleted)")

        # Step 3: Remove authorization if specified
        if authorization_data:
            authorization_id = authorization_data["authorization_id"]
            # Extract location from resource name if available
            auth_location = "global"
            if "resource_name" in authorization_data:
                auth_parts = authorization_data["resource_name"].split("/")
                if len(auth_parts) >= 4:
                    auth_location = auth_parts[3]

            print(f"Removing authorization: {authorization_id}")
            success = auth_manager.delete(
                authorization_id=authorization_id,
                location=auth_location,
            )
            if success:
                print("Removed authorization")
                results["authorization"] = True
            else:
                print("Authorization not found (may be already deleted)")

        print("=" * 60)
        print("Cleanup complete!")

        # Delete state file
        deployment_state.delete()

        return results
