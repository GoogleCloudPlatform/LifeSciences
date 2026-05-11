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

"""CLI for agent deployment and management."""

import sys

from pathlib import Path

import click

import vertexai

from .agent_deployment import AgentDeployment
from .deployment_state import DeploymentState


def find_state_files(package_name: str, deployments_dir: Path = Path("./deployments")) -> list[Path]:
    """Find deployment state files matching the package name.

    Args:
        package_name: Agent package name
        deployments_dir: Directory containing deployment state files

    Returns:
        List of matching state file paths
    """
    pattern = f"{package_name}_*.json"
    return list(deployments_dir.glob(pattern))


@click.group()
def cli():
    """Agent deployment CLI for managing Agentspace agents."""


@cli.command()
@click.argument("package_name")
@click.option(
    "--agent-env",
    type=click.Path(exists=True, path_type=Path),
    help="Path to agent runtime env file (default: ./packages/{package_name}/.env.agent)",
)
@click.option(
    "--deploy-env",
    type=click.Path(exists=True, path_type=Path),
    help="Path to deployment config file (default: ./packages/{package_name}/.env.deploy)",
)
@click.option(
    "--skip-validation",
    is_flag=True,
    help="Skip deployment configuration validation",
)
def deploy(
    package_name: str,
    agent_env: Path | None,
    deploy_env: Path | None,
    skip_validation: bool,
):
    """Deploy an agent to Agent Engine and optionally register in Agentspace.

    PACKAGE_NAME: Name of the agent package (e.g., onedrive-agent, teams-agent)

    This command performs the full deployment workflow:
    1. Validates deployment configuration (unless --skip-validation is set)
    2. Loads existing deployment state if available
    3. Prepares deployment dependencies
    4. Loads agent application dynamically from package
    5. Creates/verifies OAuth authorization resource
    6. Deploys to Agent Engine (creates or updates existing resource)
    7. Registers in Agentspace (if app ID configured)
    8. Saves deployment state to file

    The deployment is idempotent - re-running will update existing resources.
    """
    # Default file paths
    agent_env_file = str(agent_env) if agent_env else f"./packages/{package_name}/.env.agent"
    deploy_env_file = str(deploy_env) if deploy_env else f"./packages/{package_name}/.env.deploy"

    # Verify files exist
    if not Path(agent_env_file).exists():
        click.echo(f"Error: Agent env file not found: {agent_env_file}", err=True)
        sys.exit(1)
    if not Path(deploy_env_file).exists():
        click.echo(f"Error: Deploy env file not found: {deploy_env_file}", err=True)
        sys.exit(1)

    try:
        # Initialize deployment
        deployment = AgentDeployment(
            package_name=package_name,
            agent_env_file=agent_env_file,
            deploy_env_file=deploy_env_file,
        )

        # Initialize Vertex AI
        vertexai.init(
            project=deployment.get_project(),
            location=deployment.get_location(),
            staging_bucket=deployment.get_staging_bucket(),
        )

        click.echo("Deployment Configuration:")
        click.echo(f"   Package: {package_name}")
        click.echo(f"   Project: {deployment.get_project()}")
        click.echo(f"   Location: {deployment.get_location()}")
        click.echo(f"   Display Name: {deployment.get_display_name()}")
        click.echo(f"   Agentspace App: {deployment.get_agentspace_app_id() or 'Not configured'}")
        click.echo(f"   State File: {deployment.get_state_file()}")
        click.echo()

        # Validate configuration
        if not skip_validation:
            deployment.validate()
            click.echo()

        # Deploy
        _ = deployment.deploy()

        click.echo()
        click.echo("✓ Deployment completed successfully!")

    except Exception as e:
        click.echo(f"Error during deployment: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("package_name", required=False)
@click.option(
    "--file",
    type=click.Path(exists=True, path_type=Path),
    help="Path to the deployment state file",
)
def destroy(package_name: str | None, file: Path | None):
    """Remove all deployed resources for an agent.

    PACKAGE_NAME: Agent package name (e.g., onedrive-agent, teams-agent)

    This command can be invoked in two ways:

    \b
    1. By package name: agents-deploy destroy onedrive-agent
       - Searches for state files in ./deployments/ matching the package name
       - If one file found, uses it automatically
       - If multiple files found, prompts for selection

    \b
    2. By file path: agents-deploy destroy --file ./deployments/onedrive-agent_hcls-agentspace.json
       - Uses the specified state file directly

    The cleanup workflow:
    1. Loads deployment state from the saved state file
    2. Removes resources in order: Agentspace agent → Agent Engine → Authorization
    3. Automatically deletes the state file when complete

    The cleanup process is resilient - it will continue even if some resources
    are already deleted.
    """
    # Validate arguments
    if not package_name and not file:
        click.echo("Error: Must provide either PACKAGE_NAME or --file option", err=True)
        sys.exit(1)

    if package_name and file:
        click.echo("Error: Cannot provide both PACKAGE_NAME and --file option", err=True)
        sys.exit(1)

    try:
        state_file_path: Path

        # Option 1: Find state file by package name
        if package_name:
            deployments_dir = Path("./deployments")
            if not deployments_dir.exists():
                click.echo(f"Error: Deployments directory not found: {deployments_dir}", err=True)
                sys.exit(1)

            # Find matching state files
            pattern = f"{package_name}_*.json"
            matching_files = list(deployments_dir.glob(pattern))

            if len(matching_files) == 0:
                click.echo(f"Error: No deployment state files found for package: {package_name}", err=True)
                click.echo(f"Searched for: {deployments_dir}/{pattern}", err=True)
                sys.exit(1)
            elif len(matching_files) == 1:
                state_file_path = matching_files[0]
                click.echo(f"Found deployment state file: {state_file_path}")
            else:
                # Multiple files found - prompt user to select
                click.echo(f"Multiple deployment state files found for {package_name}:")
                click.echo()
                for i, file_path in enumerate(matching_files, 1):
                    click.echo(f"  {i}. {file_path.name}")
                click.echo()

                # Prompt for selection
                choice = click.prompt(
                    "Select which deployment to destroy",
                    type=click.IntRange(1, len(matching_files)),
                )
                state_file_path = matching_files[choice - 1]
                click.echo()
        else:
            # Option 2: Use provided file path
            state_file_path = file

        # Load deployment state
        deployment_state = DeploymentState.from_file(str(state_file_path))

        # Initialize Vertex AI
        vertexai.init(
            project=deployment_state.get_project(),
            location=deployment_state.get_location(),
        )

        click.echo(f"Removing deployment: {state_file_path}")
        click.echo(f"   Package: {deployment_state.package_name}")
        click.echo(f"   Project: {deployment_state.get_project()}")
        click.echo()

        # Confirm before destroying
        if not click.confirm("Are you sure you want to destroy this deployment?"):
            click.echo("Aborted.")
            sys.exit(0)

        # Remove using static method
        cleanup_result = AgentDeployment.remove(deployment_state)

        # Show summary
        click.echo()
        click.echo("Removal Summary:")
        click.echo(f"   Agentspace Agent: {'✓ Removed' if cleanup_result['agentspace_agent'] else '✗ Not found'}")
        click.echo(f"   Agent Engine: {'✓ Removed' if cleanup_result['agent_engine'] else '✗ Not found'}")
        click.echo(f"   Authorization: {'✓ Removed' if cleanup_result['authorization'] else '✗ Not found'}")
        click.echo()
        click.echo("✓ Cleanup completed successfully!")

    except Exception as e:
        click.echo(f"Error during cleanup: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
