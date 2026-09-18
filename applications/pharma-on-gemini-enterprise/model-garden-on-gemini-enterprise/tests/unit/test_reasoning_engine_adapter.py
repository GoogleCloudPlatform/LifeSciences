# Copyright 2026 Google LLC
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

"""Unit tests for reasoning_engine_adapter route concurrency and dispatch."""

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from app.app_utils.reasoning_engine_adapter import attach_reasoning_engine_routes


class FakeAdkApp:
    """Fake AdkApp with a blocking synchronous query method and async_query."""

    last_query_thread: threading.Thread | None = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    def set_up(self) -> None:
        pass

    def register_operations(self) -> dict[str, list[str]]:
        return {
            "": ["query"],
            "async": ["async_query"],
            "stream": [],
            "async_stream": [],
        }

    def query(self, **kwargs):
        FakeAdkApp.last_query_thread = threading.current_thread()
        time.sleep(0.3)
        return {"echo": kwargs, "mode": "sync"}

    async def async_query(self, **kwargs):
        await asyncio.sleep(0.05)
        return {"echo": kwargs, "mode": "async"}


@pytest.mark.asyncio
async def test_sync_query_does_not_block_event_loop() -> None:
    """Ensure synchronous method invocations on /api/reasoning_engine offload to a threadpool and do not block the event loop."""
    app = FastAPI()
    loop_thread = threading.current_thread()
    FakeAdkApp.last_query_thread = None

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    fake_agent_module = MagicMock()
    fake_agent_module.app = MagicMock()

    with (
        patch(
            "app.app_utils.reasoning_engine_adapter.AdkApp",
            side_effect=FakeAdkApp,
        ),
        patch.dict("sys.modules", {"app.agent": fake_agent_module}),
    ):
        attach_reasoning_engine_routes(app)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            start = time.monotonic()

            async def call_sync_query():
                resp = await client.post(
                    "/api/reasoning_engine",
                    json={"class_method": "query", "input": {"message": "Hello"}},
                )
                assert resp.status_code == 200
                return resp.json()

            async def call_health_after_start():
                # Give the sync query request a head start to enter execution
                await asyncio.sleep(0.02)
                resp = await client.get("/health")
                total_elapsed = time.monotonic() - start
                assert resp.status_code == 200
                return total_elapsed

            query_result, health_total_elapsed = await asyncio.gather(
                call_sync_query(),
                call_health_after_start(),
            )

    assert query_result == {"output": {"echo": {"message": "Hello"}, "mode": "sync"}}
    assert FakeAdkApp.last_query_thread is not None
    assert FakeAdkApp.last_query_thread is not loop_thread, (
        "Synchronous query executed directly on the main asyncio event loop thread"
    )
    # If the event loop was blocked by time.sleep(0.3), health_total_elapsed would be >= 0.3s.
    assert health_total_elapsed < 0.2, (
        f"Event loop was blocked by synchronous query (health check took {health_total_elapsed:.3f}s)"
    )


@pytest.mark.asyncio
async def test_async_query_dispatch() -> None:
    """Ensure coroutine methods on /api/reasoning_engine are awaited properly."""
    app = FastAPI()
    fake_agent_module = MagicMock()
    fake_agent_module.app = MagicMock()

    with (
        patch(
            "app.app_utils.reasoning_engine_adapter.AdkApp",
            side_effect=FakeAdkApp,
        ),
        patch.dict("sys.modules", {"app.agent": fake_agent_module}),
    ):
        attach_reasoning_engine_routes(app)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/reasoning_engine",
                json={"class_method": "async_query", "input": {"message": "Hi"}},
            )
            assert resp.status_code == 200
            assert resp.json() == {
                "output": {"echo": {"message": "Hi"}, "mode": "async"}
            }
