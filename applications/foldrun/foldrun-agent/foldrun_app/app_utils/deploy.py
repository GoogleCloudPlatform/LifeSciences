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

import asyncio
import datetime
import importlib
import inspect
import json
import logging
import os
import warnings
from typing import Any

import click
import google.auth
import vertexai
from vertexai._genai import _agent_engines_utils
from vertexai._genai.types import AgentEngine, AgentEngineConfig

# Suppress google-cloud-storage version compatibility warning
warnings.filterwarnings("ignore", category=FutureWarning, module="google.cloud.aiplatform")


def generate_class_methods_from_agent(agent_instance: Any) -> list[dict[str, Any]]:
    """Generate method specifications with schemas from agent's register_operations().

    See: https://docs.cloud.google.com/agent-builder/agent-engine/use/custom#supported-operations
    """
    registered_operations = _agent_engines_utils._get_registered_operations(agent=agent_instance)
    class_methods_spec = _agent_engines_utils._generate_class_methods_spec_or_raise(
        agent=agent_instance,
        operations=registered_operations,
    )
    class_methods_list = [
        _agent_engines_utils._to_dict(method_spec) for method_spec in class_methods_spec
    ]
    return class_methods_list


def parse_key_value_pairs(kv_string: str | None) -> dict[str, str]:
    """Parse key-value pairs from a comma-separated KEY=VALUE string."""
    result = {}
    if kv_string:
        for pair in kv_string.split(","):
            if "=" in pair:
                key, value = pair.split("=", 1)
                result[key.strip()] = value.strip()
            else:
                logging.warning(f"Skipping malformed key-value pair: {pair}")
    return result


def write_deployment_metadata(
    remote_agent: Any,
    metadata_file: str = "deployment_metadata.json",
) -> None:
    """Write deployment metadata to file."""
    metadata = {
        "remote_agent_engine_id": remote_agent.api_resource.name,
        "deployment_target": "agent_engine",
        "is_a2a": False,
        "deployment_timestamp": datetime.datetime.now().isoformat(),
    }

    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    logging.info(f"Agent Engine ID written to {metadata_file}")


def print_deployment_success(
    remote_agent: Any,
    location: str,
    project: str,
) -> None:
    """Print deployment success message with console URL."""
    # Extract agent engine ID and project number for console URL
    resource_name_parts = remote_agent.api_resource.name.split("/")
    agent_engine_id = resource_name_parts[-1]
    project_number = resource_name_parts[1]

    print("\n✅ Deployment successful!")
    service_account = remote_agent.api_resource.spec.service_account
    if service_account:
        print(f"Service Account: {service_account}")
    else:
        default_sa = f"service-{project_number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
        print(f"Service Account: {default_sa}")
    playground_url = f"https://console.cloud.google.com/vertex-ai/agents/agent-engines/locations/{location}/agent-engines/{agent_engine_id}/playground?project={project}"
    print(f"\n📊 Open Console Playground: {playground_url}\n")


@click.command()
@click.option(
    "--project",
    default=None,
    help="GCP project ID (defaults to application default credentials)",
)
@click.option(
    "--location",
    default="us-central1",
    help="GCP region (defaults to us-central1)",
)
@click.option(
    "--display-name",
    default="FoldRun_Agent",
    help="Display name for the agent engine",
)
@click.option(
    "--description",
    default="Expert AI assistant for FoldRun (AlphaFold2) protein structure prediction, job management, and results analysis",
    help="Description of the agent",
)
@click.option(
    "--source-packages",
    multiple=True,
    default=["./foldrun_app"],
    help="Source packages to deploy. Can be specified multiple times (e.g., --source-packages=./app --source-packages=./lib)",
)
@click.option(
    "--entrypoint-module",
    default="foldrun_app.agent_engine_app",
    help="Python module path for the agent entrypoint (required)",
)
@click.option(
    "--entrypoint-object",
    default="agent_engine",
    help="Name of the agent instance at module level (required)",
)
@click.option(
    "--requirements-file",
    default="foldrun_app/app_utils/.requirements.txt",
    help="Path to requirements.txt file",
)
@click.option(
    "--set-env-vars",
    default=None,
    help="Comma-separated list of environment variables in KEY=VALUE format",
)
@click.option(
    "--labels",
    default=None,
    help="Comma-separated list of labels in KEY=VALUE format",
)
@click.option(
    "--service-account",
    default=None,
    help="Service account email to use for the agent engine",
)
@click.option(
    "--min-instances",
    type=int,
    default=1,
    help="Minimum number of instances (default: 1)",
)
@click.option(
    "--max-instances",
    type=int,
    default=10,
    help="Maximum number of instances (default: 10)",
)
@click.option(
    "--cpu",
    default="4",
    help="CPU limit (default: 4)",
)
@click.option(
    "--memory",
    default="8Gi",
    help="Memory limit (default: 8Gi)",
)
@click.option(
    "--container-concurrency",
    type=int,
    default=9,
    help="Container concurrency (default: 9)",
)
@click.option(
    "--num-workers",
    type=int,
    default=1,
    help="Number of worker processes (default: 1)",
)
def deploy_agent_engine_app(
    project: str | None,
    location: str,
    display_name: str,
    description: str,
    source_packages: tuple[str, ...],
    entrypoint_module: str,
    entrypoint_object: str,
    requirements_file: str,
    set_env_vars: str | None,
    labels: str | None,
    service_account: str | None,
    min_instances: int,
    max_instances: int,
    cpu: str,
    memory: str,
    container_concurrency: int,
    num_workers: int,
) -> AgentEngine:
    """Deploy the agent engine app to Vertex AI."""

    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Parse CLI environment variables and labels
    env_vars = parse_key_value_pairs(set_env_vars)
    labels_dict = parse_key_value_pairs(labels)

    # Automatically load .env file from the agent directory
    # Try looking in common locations relative to the script or CWD
    from dotenv import dotenv_values

    # Try current directory first, then up one level (foldrun_app/..), then up two
    potential_env_paths = [".env", "foldrun_app/.env", "../.env", "../../.env"]

    loaded_env = {}
    for env_path in potential_env_paths:
        if os.path.exists(env_path):
            logging.info(f"Loading environment variables from {env_path}")
            # Use dotenv_values to get the dict without setting os.environ
            loaded_env = dotenv_values(env_path)
            break

    if not loaded_env:
        logging.info("No .env file found — using CLI-provided environment variables only.")

    # Merge loaded .env into env_vars (CLI args take precedence if they conflict,
    # but here we want to ensure .env vars are present if not explicitly passed)
    # Strategy: Start with .env, then update with explicit CLI env_vars
    full_env_vars = loaded_env.copy()
    full_env_vars.update(env_vars)

    # Filter out reserved environment variables
    # Agent Engine doesn't allow setting these as they are managed by the platform
    reserved_vars = [
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "PYTHONPATH",
        "USER",
    ]

    filtered_env_vars = {
        k: v
        for k, v in full_env_vars.items()
        if k not in reserved_vars and not k.startswith("AG_")  # specific internal prefixes if any
    }

    env_vars = filtered_env_vars

    # Set deployment-specific environment variables
    # Note: GOOGLE_CLOUD_REGION might be allowed or also reserved, usually REGION is fine but LOCATION is not.
    # We'll set it as requested by the original code, but keep an eye on it.
    env_vars["GOOGLE_CLOUD_REGION"] = location
    env_vars["NUM_WORKERS"] = str(num_workers)

    # Enable telemetry by default for Agent Engine
    env_vars.setdefault("GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", "true")
    # Capture telemetry metadata only — NOT prompt/response content (privacy)
    env_vars.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "false")

    if not service_account:
        sa_project = project or loaded_env.get("GCP_PROJECT_ID")
        if sa_project:
            service_account = f"foldrun-agent-sa@{sa_project}.iam.gserviceaccount.com"

    if not project:
        _, project = google.auth.default()

    print("""
    ╔═══════════════════════════════════════════════════════════╗
    ║                                                           ║
    ║   🤖 DEPLOYING AGENT TO VERTEX AI AGENT ENGINE 🤖         ║
    ║                                                           ║
    ╚═══════════════════════════════════════════════════════════╝
    """)

    # Log deployment parameters
    click.echo("\n📋 Deployment Parameters:")
    click.echo(f"  Project: {project}")
    click.echo(f"  Location: {location}")
    click.echo(f"  Display Name: {display_name}")
    click.echo(f"  Min Instances: {min_instances}")
    click.echo(f"  Max Instances: {max_instances}")
    click.echo(f"  CPU: {cpu}")
    click.echo(f"  Memory: {memory}")
    click.echo(f"  Container Concurrency: {container_concurrency}")
    if service_account:
        click.echo(f"  Service Account: {service_account}")
    if env_vars:
        click.echo("\n🌍 Environment Variables:")
        for key, value in sorted(env_vars.items()):
            click.echo(f"  {key}: {value}")

    source_packages_list = list(source_packages)

    # Initialize vertexai client
    client = vertexai.Client(
        project=project,
        location=location,
    )
    vertexai.init(project=project, location=location)

    # Add agent garden labels if configured

    # Dynamically import the agent instance to generate class_methods
    logging.info(f"Importing {entrypoint_module}.{entrypoint_object}")
    module = importlib.import_module(entrypoint_module)
    agent_instance = getattr(module, entrypoint_object)

    # If the agent_instance is a coroutine, await it to get the actual instance
    if inspect.iscoroutine(agent_instance):
        logging.info(f"Detected coroutine, awaiting {entrypoint_object}...")
        agent_instance = asyncio.run(agent_instance)

    # Generate class methods spec from register_operations
    class_methods_list = generate_class_methods_from_agent(agent_instance)

    config = AgentEngineConfig(
        display_name=display_name,
        description=description,
        source_packages=source_packages_list,
        entrypoint_module=entrypoint_module,
        entrypoint_object=entrypoint_object,
        class_methods=class_methods_list,
        env_vars=env_vars,
        service_account=service_account,
        requirements_file=requirements_file,
        labels=labels_dict,
        min_instances=min_instances,
        max_instances=max_instances,
        resource_limits={"cpu": cpu, "memory": memory},
        container_concurrency=container_concurrency,
        agent_framework="google-adk",
    )

    # Check if an agent with this name already exists
    existing_agents = list(client.agent_engines.list())
    matching_agents = [
        agent for agent in existing_agents if agent.api_resource.display_name == display_name
    ]

    # Deploy the agent (create or update)
    if matching_agents:
        click.echo(f"\n📝 Updating existing agent: {display_name}")
    else:
        click.echo(f"\n🚀 Creating new agent: {display_name}")

    click.echo("🚀 Deploying to Vertex AI Agent Engine (this can take 3-5 minutes)...")

    if matching_agents:
        remote_agent = client.agent_engines.update(
            name=matching_agents[0].api_resource.name, config=config
        )
    else:
        remote_agent = client.agent_engines.create(config=config)

    write_deployment_metadata(remote_agent)
    print_deployment_success(remote_agent, location, project)

    return remote_agent


if __name__ == "__main__":
    deploy_agent_engine_app()
