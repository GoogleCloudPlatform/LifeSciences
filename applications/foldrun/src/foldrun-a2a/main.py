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

"""FoldRun A2A proxy — thin Cloud Run service that exposes the A2A protocol
and forwards requests to the FoldRun Agent Engine deployment.
"""

import asyncio
import logging
import os
from uuid import uuid4

import uvicorn
import vertexai
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    UnsupportedOperationError,
    TextPart,
)
from a2a.utils import new_agent_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext

from a2a_agent_card import foldrun_agent_card

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AGENT_ENGINE_RESOURCE = os.environ.get(
    "AGENT_ENGINE_RESOURCE",
    "projects/your-project-id/locations/us-central1/reasoningEngines/your-engine-id",
)

class AgentEngineProxyExecutor(AgentExecutor):
    """A2A executor that proxies requests to Agent Engine."""

    def __init__(self):
        self._engine = None
        self._sessions: dict[str, str] = {}

    def _get_engine(self):
        if self._engine is None:
            from vertexai import agent_engines
            self._engine = agent_engines.get(AGENT_ENGINE_RESOURCE)
            logger.info(f"Connected to Agent Engine: {AGENT_ENGINE_RESOURCE}")
        return self._engine

    def _get_or_create_session(self, context_id: str) -> str:
        if context_id in self._sessions:
            return self._sessions[context_id]

        engine = self._get_engine()
        session = engine.create_session(user_id=f"a2a-{context_id}")
        session_id = session["id"]
        self._sessions[context_id] = session_id
        logger.info(f"Created session {session_id} for context {context_id}")
        return session_id

    async def execute(self, context: RequestContext, event_queue) -> None:
        user_text = ""
        if context.message and context.message.parts:
            for part in context.message.parts:
                if isinstance(part.root, TextPart):
                    user_text += part.root.text

        task_id = context.task_id or uuid4().hex
        context_id = context.context_id

        if not user_text:
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    taskId=task_id,
                    contextId=context_id,
                    final=True,
                    status=TaskStatus(
                        state=TaskState.completed,
                        message=new_agent_text_message(
                            "No text content in request.",
                            task_id,
                            context_id,
                        ),
                    ),
                )
            )
            return

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                taskId=task_id,
                contextId=context_id,
                final=False,
                status=TaskStatus(state=TaskState.working),
            )
        )

        session_id = self._get_or_create_session(context_id)
        engine = self._get_engine()

        try:
            full_response = ""
            for chunk in engine.stream_query(
                user_id=f"a2a-{context_id}",
                session_id=session_id,
                message=user_text,
            ):
                chunk_text = ""
                if isinstance(chunk, dict):
                    content = chunk.get("content", {})
                    parts = content.get("parts", [])
                    for part in parts:
                        if "text" in part:
                            chunk_text += part["text"]
                elif hasattr(chunk, "content"):
                    content = chunk.content
                    if hasattr(content, "parts"):
                        for part in content.parts:
                            if hasattr(part, "text"):
                                chunk_text += part.text

                if chunk_text:
                    full_response += chunk_text

            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    taskId=task_id,
                    contextId=context_id,
                    final=True,
                    status=TaskStatus(
                        state=TaskState.completed,
                        message=new_agent_text_message(
                            full_response or "No response from agent.",
                            task_id,
                            context_id,
                        ),
                    ),
                )
            )

        except Exception as e:
            logger.exception(f"Error proxying to Agent Engine: {e}")
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    taskId=task_id,
                    contextId=context_id,
                    final=True,
                    status=TaskStatus(
                        state=TaskState.failed,
                        message=new_agent_text_message(
                            f"Error communicating with agent: {e}",
                            task_id,
                            context_id,
                        ),
                    ),
                )
            )

    async def cancel(self, context: RequestContext, event_queue) -> None:
        raise UnsupportedOperationError("Cancel not supported")

def create_app():
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
    location = os.environ.get("GOOGLE_CLOUD_REGION", "us-central1")
    vertexai.init(project=project, location=location)

    request_handler = DefaultRequestHandler(
        agent_executor=AgentEngineProxyExecutor(),
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=foldrun_agent_card,
        http_handler=request_handler,
    )

    return server.build()

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    logger.info(f"Starting FoldRun A2A proxy on port {port}...")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
