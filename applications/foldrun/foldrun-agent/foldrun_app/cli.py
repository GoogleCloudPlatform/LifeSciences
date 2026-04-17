#!/usr/bin/env python3
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

"""AlphaFold2 ADK Agent - AI Agent for protein structure prediction using native FunctionTools."""

import asyncio
import logging
import os
import warnings

# ADK imports
from google.adk.agents import Agent
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from google.genai.errors import ClientError, ServerError
from rich.console import Console
from rich.panel import Panel

from foldrun_app.agent import create_alphafold_agent as create_base_agent

# Initialize console for rich output
console = Console()

# Suppress experimental warnings from ADK
warnings.filterwarnings("ignore", message=".*BaseAuthenticatedTool: This feature is experimental.*")
# Suppress GenAI non-text parts warning
warnings.filterwarnings("ignore", message=".*there are non-text parts in the response.*")
# Suppress ADK progressive streaming warning
warnings.filterwarnings(
    "ignore", message=".*feature FeatureName.PROGRESSIVE_SSE_STREAMING is enabled.*"
)


# Configure logging to suppress specific ADK warnings
class AppNameMismatchFilter(logging.Filter):
    def filter(self, record):
        return "App name mismatch detected" not in record.getMessage()


class NonTextPartsFilter(logging.Filter):
    def filter(self, record):
        return "there are non-text parts in the response" not in record.getMessage()


logging.getLogger("google.adk.runners.runner").addFilter(AppNameMismatchFilter())
logging.getLogger("google_genai.types").addFilter(NonTextPartsFilter())

# Retry configuration
MAX_RETRIES = int(os.getenv("AF2_MAX_RETRIES", "3"))
RETRY_DELAY = float(os.getenv("AF2_RETRY_DELAY", "2.0"))


async def run_with_retry(runner, user_id, session_id, message, max_retries=MAX_RETRIES):
    """Run agent query with automatic retry on 500 errors and rate limits."""
    last_error = None

    for attempt in range(max_retries):
        try:
            async for event in runner.run_async(
                user_id=user_id, session_id=session_id, new_message=message
            ):
                yield event
            return

        except ClientError as e:
            last_error = e
            if e.status_code == 429:
                retry_delay = 60
                if hasattr(e, "retry_after"):
                    retry_delay = e.retry_after

                console.print(
                    f"\n[yellow]⚠ Rate limit exceeded (429). Please wait {retry_delay:.0f}s before trying again.[/yellow]"
                )
                raise

        except ServerError as e:
            last_error = e
            if e.status == 500:
                if attempt < max_retries - 1:
                    wait_time = RETRY_DELAY * (2**attempt)
                    console.print(
                        f"\n[yellow]⚠ Server error (500). Retrying in {wait_time:.1f}s... (attempt {attempt + 1}/{max_retries})[/yellow]"
                    )
                    await asyncio.sleep(wait_time)
                    continue
            raise
        except Exception:
            raise

    if last_error:
        raise last_error


async def create_pretty_agent():
    """Create and configure the AlphaFold2 ADK agent with pretty console output."""

    # Display configuration banner
    project_id = os.getenv("GCP_PROJECT_ID", "Not configured")
    region = os.getenv("GCP_REGION", "Not configured")
    gcs_bucket = os.getenv("GCS_BUCKET_NAME", "Not configured")
    viewer_base_url = os.getenv("AF2_VIEWER_URL", "Not configured")
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")

    config_info = f"""[bold cyan]AlphaFold2 Agent Configuration[/bold cyan]

[bold]Google Cloud:[/bold]
  Project ID: [yellow]{project_id}[/yellow]
  Region: [yellow]{region}[/yellow]

[bold]Storage:[/bold]
  GCS Bucket: [yellow]gs://{gcs_bucket}/[/yellow]

[bold]Services:[/bold]
  Analysis Viewer: [yellow]{viewer_base_url}[/yellow]
  AI Model: [yellow]{gemini_model}[/yellow]
"""

    console.print(Panel(config_info, border_style="cyan", padding=(1, 2)))

    console.print("\n[bold cyan]Initializing agent...[/bold cyan]")

    try:
        # Create the base agent using central configuration
        agent = create_base_agent()

        tool_count = len(agent.tools)
        console.print(f"[green]✓[/green] Agent created with {tool_count} native FunctionTools")
        console.print("[green]✓[/green] Ready\n")

        return agent

    except Exception as e:
        console.print(f"[red]✗[/red] Failed to create agent: {e}")
        console.print("\n[bold]Troubleshooting:[/bold]")
        console.print(
            "  Check that required environment variables are set (GCP_PROJECT_ID, GCS_BUCKET_NAME, etc.)"
        )
        console.print("  Ensure you have authenticated: gcloud auth application-default login")
        raise


async def run_interactive_session(agent: Agent):
    """Run an interactive session with the AlphaFold2 agent."""

    # Create session and artifact services
    session_service = InMemorySessionService()
    artifact_service = InMemoryArtifactService()

    # Create runner
    runner = Runner(
        app_name="foldrun_app",
        agent=agent,
        session_service=session_service,
        artifact_service=artifact_service,
    )

    # Welcome message
    console.print(
        Panel.fit(
            "[bold green]AlphaFold2 ADK Agent[/bold green]\n\n"
            "I can help you with:\n"
            "• Submitting protein structure predictions\n"
            "• Monitoring job progress\n"
            "• Analyzing prediction quality\n"
            "• Downloading and visualizing results\n\n"
            "Type 'exit' or 'quit' to end the session.",
            border_style="green",
            title="Welcome",
        )
    )

    console.print("\n[dim]Example queries:[/dim]")
    console.print("  [cyan]List my recent AlphaFold jobs[/cyan]")
    console.print("  [cyan]Submit a monomer prediction for hemoglobin alpha[/cyan]")
    console.print("  [cyan]Check status of job <job_id>[/cyan]")
    console.print("  [cyan]Analyze quality for job <job_id>[/cyan]\n")

    # Start session
    session = await session_service.create_session(app_name="foldrun_app", user_id="local_user")
    session_id = session.id

    try:
        while True:
            # Get user input
            try:
                user_input = console.input("[bold blue]You:[/bold blue] ")
            except (EOFError, KeyboardInterrupt):
                console.print("\n[yellow]Session ended.[/yellow]")
                break

            # Check for exit commands
            if user_input.lower().strip() in ["exit", "quit", "bye"]:
                console.print("[yellow]Goodbye![/yellow]")
                break

            if not user_input.strip():
                continue

            # Process user message
            console.print("\n[bold green]Agent:[/bold green] ", end="")

            try:
                # Create user message content
                message = types.Content(parts=[types.Part(text=user_input)], role="user")

                # Stream the response with automatic retry on 500 errors
                async for event in run_with_retry(
                    runner=runner, user_id="local_user", session_id=session_id, message=message
                ):
                    # Extract and display text from events
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.text:
                                console.print(part.text, end="")

                console.print("\n")

            except ClientError as e:
                # Handle rate limits cleanly
                if e.status_code == 429:
                    console.print(
                        "\n[yellow]⚠ Rate limit exceeded. Please wait a minute before trying again.[/yellow]\n"
                    )
                else:
                    console.print(
                        f"\n[red]API Error ({e.status_code}):[/red] {e.message if hasattr(e, 'message') else str(e)}\n"
                    )
            except ServerError as e:
                # Handle 500 errors cleanly
                console.print(
                    f"\n[yellow]⚠ The AI service is temporarily unavailable (error {e.status}).[/yellow]"
                )
                console.print("[dim]Please try your request again in a moment.[/dim]\n")
            except Exception as e:
                # For unexpected errors, show a clean message
                error_msg = str(e)
                # Don't show full stack traces for known error types
                if "RESOURCE_EXHAUSTED" in error_msg or "INTERNAL" in error_msg:
                    console.print(
                        "\n[yellow]⚠ Temporary service issue. Please try again.[/yellow]\n"
                    )
                else:
                    console.print(f"\n[red]Error:[/red] {error_msg}\n")

    finally:
        pass


async def run_single_query(agent: Agent, query: str):
    """Run a single query against the agent (useful for testing/scripting)."""

    session_service = InMemorySessionService()
    artifact_service = InMemoryArtifactService()

    runner = Runner(
        app_name="foldrun_app",
        agent=agent,
        session_service=session_service,
        artifact_service=artifact_service,
    )

    session = await session_service.create_session(app_name="foldrun_app", user_id="local_user")
    session_id = session.id

    try:
        console.print(f"[bold blue]Query:[/bold blue] {query}\n")
        console.print("[bold green]Response:[/bold green] ", end="")

        # Create user message content
        message = types.Content(parts=[types.Part(text=query)], role="user")

        # Run with automatic retry on 500 errors
        async for event in run_with_retry(
            runner=runner, user_id="local_user", session_id=session_id, message=message
        ):
            # Extract and display text from events
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        console.print(part.text, end="")

        console.print("\n")

    except ServerError as e:
        console.print(f"\n[red]Error:[/red] Server error ({e.status}): {e}\n")
        if e.status == 500:
            console.print(
                "[yellow]Tip:[/yellow] The Gemini API is experiencing issues. Please try again later.\n"
            )
    except Exception as e:
        console.print(f"\n[red]Error:[/red] {e}\n")
    finally:
        pass


async def main():
    """Main entry point for the AlphaFold2 ADK agent."""

    import argparse

    parser = argparse.ArgumentParser(
        description="AlphaFold2 ADK Agent - AI assistant for protein structure prediction"
    )
    parser.add_argument("--query", type=str, help="Run a single query instead of interactive mode")
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        choices=["gemini-3-flash-preview", "gemini-3.1-pro-preview"],
        help="Gemini model to use (default: from .env or gemini-3-flash-preview)",
    )

    args = parser.parse_args()

    # Only override GEMINI_MODEL if explicitly specified via --model flag
    if args.model is not None:
        os.environ["GEMINI_MODEL"] = args.model

    # Check for authentication
    if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        console.print(
            "[yellow]Note:[/yellow] Using Application Default Credentials (ADC)\n"
            "[dim]If not authenticated, run: gcloud auth application-default login[/dim]\n"
        )

    # Create agent
    try:
        agent = await create_pretty_agent()
    except Exception:
        return

    # Run query or interactive session
    if args.query:
        await run_single_query(agent, args.query)
    else:
        await run_interactive_session(agent)


if __name__ == "__main__":
    asyncio.run(main())
