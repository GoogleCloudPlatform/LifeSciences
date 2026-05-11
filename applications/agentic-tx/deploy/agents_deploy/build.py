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

"""Utility functions for agent deployment dependency management."""

import re
import subprocess

from pathlib import Path
from typing import TypedDict

from dotenv import dotenv_values, load_dotenv


class AgentDeploymentConfig(TypedDict):
    requirements: list[str]
    extra_packages: list[str]


class AgentDeploymentError(Exception):
    """Custom exception for agent deployment errors"""


def prepare_agent_deployment_dependencies(
    package_name: str, dist_dir: str = "dist", verbose: bool = True
) -> AgentDeploymentConfig:
    """Prepare agent deployment by building packages and processing dependencies.

    Args:
        package_name: Name of the agent package (e.g., "onedrive-agent")
        dist_dir: Directory for built packages (default: "dist")
        verbose: Whether to print progress messages

    Returns:
        AgentDeploymentConfig with 'requirements' and 'extra_packages' keys

    Raises:
        AgentDeploymentError: If any step fails
    """

    def log(message: str):
        if verbose:
            print(message)

    # Validate package exists
    package_dir = Path(f"packages/{package_name}")
    if not package_dir.exists():
        raise AgentDeploymentError(f"Package directory {package_dir} not found")

    dist_path = Path(dist_dir)
    dist_path.mkdir(exist_ok=True)

    log(f"Preparing deployment for {package_name}...")

    try:
        # Step 1: Build the main package
        log(f"Building {package_name}...")
        result = subprocess.run(["uv", "build", "--package", package_name], capture_output=True, text=True, check=True)

        # Step 2: Export dependencies with annotations for debugging
        log("Analyzing dependencies...")
        result = subprocess.run(
            [
                "uv",
                "export",
                "--package",
                package_name,
                "--no-hashes",
                "--no-header",
                "--no-dev",
                "--no-emit-project",
                "--no-annotate",
            ],
            cwd=package_dir,
            capture_output=True,
            text=True,
            check=True,
        )

        # Step 3: Process dependencies
        requirements = []
        local_packages = set()
        lines = result.stdout.split("\n")

        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue

            # Detect local workspace dependencies
            if line.startswith("-e ./packages/"):
                match = re.match(r"-e \./packages/([^/\s]+)", line)
                if match:
                    local_pkg = match.group(1)
                    local_packages.add(local_pkg)
                    log(f"Found local dependency: {local_pkg}")
            else:
                # External dependency
                requirement_line = line

                # Clean environment markers that are too complex
                # if " ; " in line and line.count(" or ") > 3:
                #     requirement_line = line.split(" ; ")[0]
                #     log(f"Simplified environment marker for: {requirement_line}")

                # # Check if next line is a comment
                # if i + 1 < len(lines) and lines[i + 1].strip().startswith("# via"):
                #     comment = lines[i + 1].strip()
                #     requirements.append(f"{requirement_line}\n    {comment}")
                #     i += 1  # Skip the comment line
                # else:
                requirements.append(requirement_line)

            i += 1

        # Step 4: Build local dependencies and collect wheels
        built_packages = []

        # Process local dependencies first
        for local_pkg in sorted(local_packages):
            log(f"Building local dependency: {local_pkg}...")
            subprocess.run(["uv", "build", "--package", local_pkg], capture_output=True, text=True, check=True)

        # Collect all relevant wheels
        all_packages = {package_name} | local_packages

        for pkg in all_packages:
            wheel_pattern = f"{pkg.replace('-', '_')}-*.whl"
            wheels = list(dist_path.glob(wheel_pattern))
            if wheels:
                # Use the most recent wheel if multiple exist
                latest_wheel = max(wheels, key=lambda x: x.stat().st_mtime)
                built_packages.append(str(latest_wheel))
                log(f"Including wheel: {latest_wheel.name}")
            else:
                raise AgentDeploymentError(f"No wheel found for package: {pkg}")

        return AgentDeploymentConfig(requirements=requirements + built_packages, extra_packages=built_packages)

    except subprocess.CalledProcessError as e:
        raise AgentDeploymentError(f"Command failed: {e.cmd}\nError: {e.stderr}")
    except Exception as e:
        raise AgentDeploymentError(f"Deployment preparation failed: {e!s}")


def load_environment_values(path: str) -> dict[str, str | None]:
    # this load_dotenv might not be needed as I believe it's already been executed previously
    load_dotenv(path)
    env_values = dotenv_values(path)
    # remove reserved environment variables
    if "GOOGLE_CLOUD_PROJECT" in env_values:
        del env_values["GOOGLE_CLOUD_PROJECT"]
    if "GOOGLE_CLOUD_LOCATION" in env_values:
        del env_values["GOOGLE_CLOUD_LOCATION"]
    return env_values
