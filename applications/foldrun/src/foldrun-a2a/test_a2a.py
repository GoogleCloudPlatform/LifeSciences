"""Test the FoldRun A2A Cloud Run proxy.

Usage:
    cd src/foldrun-a2a
    python test_a2a.py [AGENT_URL]

Defaults to the deployed Cloud Run service URL.
Requires: gcloud auth login (uses ID token for Cloud Run auth)
"""

import asyncio
import sys
import subprocess
from uuid import uuid4
from typing import Any

import httpx
from a2a.client import ClientFactory
from a2a.client.client import ClientConfig
from a2a.client.middleware import ClientCallInterceptor, ClientCallContext
from a2a.types import Message, TextPart, Role, TransportProtocol, AgentCard

AGENT_URL = "https://your-cloud-run-url.a.run.app"


class CloudRunAuthInterceptor(ClientCallInterceptor):
    """Injects an ID token for Cloud Run authentication."""

    def __init__(self, agent_url: str):
        self.agent_url = agent_url
        self._token = None

    def _get_token(self) -> str:
        if self._token is None:
            result = subprocess.run(
                ["gcloud", "auth", "print-identity-token",
                 f"--audiences={self.agent_url}"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                # Fall back to access token
                result = subprocess.run(
                    ["gcloud", "auth", "print-access-token"],
                    capture_output=True, text=True,
                )
            self._token = result.stdout.strip()
        return self._token

    async def intercept(
        self,
        method_name: str,
        request_payload: dict[str, Any],
        http_kwargs: dict[str, Any],
        agent_card: AgentCard | None,
        context: ClientCallContext | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        headers = http_kwargs.get("headers", {})
        headers["Authorization"] = f"Bearer {self._get_token()}"
        http_kwargs["headers"] = headers
        return request_payload, http_kwargs


async def main():
    agent_url = sys.argv[1] if len(sys.argv) > 1 else AGENT_URL

    print(f"Connecting to: {agent_url}")

    # Get auth token
    auth = CloudRunAuthInterceptor(agent_url)
    token = auth._get_token()

    # Connect A2A client
    client = await ClientFactory.connect(
        agent=agent_url,
        client_config=ClientConfig(
            streaming=True,
            supported_transports=[TransportProtocol.jsonrpc],
            httpx_client=httpx.AsyncClient(
                headers={"Authorization": f"Bearer {token}"},
                timeout=120.0,
            ),
        ),
    )

    print("Connected. Sending request...")
    try:
        message = Message(
            role=Role.user,
            parts=[TextPart(text="What can you do? Reply in one sentence.")],
            message_id=uuid4().hex,
        )

        streaming_response = client.send_message(message)

        async for chunk in streaming_response:
            task, _ = chunk
            print(f"EVENT: state={task.status.state}")
            if task.status.message:
                for part in task.status.message.parts:
                    if hasattr(part.root, "text"):
                        print(f"  TEXT: {part.root.text[:200]}")

        print("\nTest passed!")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

    await client.close()


asyncio.run(main())
