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

"""Unit tests for reasoning_engine_adapter route handlers."""

import asyncio
import json
import threading
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from app.app_utils.reasoning_engine_adapter import attach_reasoning_engine_routes


class _FakeAdkApp:
    """Fake AdkApp exposing both sync and async operations for testing."""

    def __init__(self, unblock_event: threading.Event, started_event: threading.Event):
        self._unblock_event = unblock_event
        self._started_event = started_event

    def set_up(self) -> None:
        pass

    def register_operations(self) -> dict[str, list[str]]:
        return {
            "": ["query"],
            "async": ["async_query"],
            "stream": ["stream_query"],
            "async_stream": ["async_stream_query"],
        }

    def query(self, **kwargs):
        self._started_event.set()
        was_unblocked = self._unblock_event.wait(timeout=0.5)
        return {
            "kwargs": kwargs,
            "thread_id": threading.get_ident(),
            "was_unblocked": was_unblocked,
        }

    async def async_query(self, **kwargs):
        return {
            "kwargs": kwargs,
            "thread_id": threading.get_ident(),
        }

    def stream_query(self, **kwargs):
        yield {
            "event": "sync_chunk_1",
            "thread_id": threading.get_ident(),
            "kwargs": kwargs,
        }
        yield {"event": "sync_chunk_2", "thread_id": threading.get_ident()}

    async def async_stream_query(self, **kwargs):
        yield {
            "event": "async_chunk_1",
            "thread_id": threading.get_ident(),
            "kwargs": kwargs,
        }


def _create_test_app(
    unblock_event: threading.Event,
    started_event: threading.Event,
) -> tuple[FastAPI, _FakeAdkApp]:
    app = FastAPI()
    fake_runtime = _FakeAdkApp(unblock_event, started_event)

    @app.get("/health")
    async def health():
        unblock_event.set()
        return {"status": "ok"}

    attach_reasoning_engine_routes(app)
    return app, fake_runtime


@pytest.mark.asyncio
async def test_sync_query_offloaded_to_threadpool_without_blocking_event_loop():
    """Synchronous reasoning_engine methods must run in a worker thread pool
    so concurrent async requests (e.g. health checks) are not blocked."""
    unblock_event = threading.Event()
    started_event = threading.Event()
    app, fake_runtime = _create_test_app(unblock_event, started_event)
    event_loop_thread_id = threading.get_ident()

    with patch(
        "app.app_utils.reasoning_engine_adapter.AdkApp",
        return_value=fake_runtime,
    ):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            query_task = asyncio.create_task(
                client.post(
                    "/api/reasoning_engine",
                    json={"class_method": "query", "input": {"message": "hello"}},
                )
            )

            # Give the query task a brief moment to start executing.
            await asyncio.sleep(0.05)

            # While the sync query method is waiting, a concurrent health check
            # must be served immediately by the event loop and unblock it.
            health_resp = await client.get("/health")
            assert health_resp.status_code == 200

            resp = await query_task
            assert resp.status_code == 200
            data = resp.json()["output"]
            assert data["kwargs"] == {"message": "hello"}
            assert data["was_unblocked"] is True
            assert data["thread_id"] != event_loop_thread_id


@pytest.mark.asyncio
async def test_async_query_executes_coroutine_directly():
    """Asynchronous reasoning_engine methods should still be awaited properly."""
    unblock_event = threading.Event()
    started_event = threading.Event()
    app, fake_runtime = _create_test_app(unblock_event, started_event)

    with patch(
        "app.app_utils.reasoning_engine_adapter.AdkApp",
        return_value=fake_runtime,
    ):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/reasoning_engine",
                json={"class_method": "async_query", "input": {"message": "async"}},
            )
            assert resp.status_code == 200
            assert resp.json()["output"]["kwargs"] == {"message": "async"}


@pytest.mark.asyncio
async def test_stream_query_supports_both_sync_and_async_generators():
    """Streaming endpoint should support both sync generators (via threadpool)
    and async generators without blocking the event loop."""
    unblock_event = threading.Event()
    started_event = threading.Event()
    app, fake_runtime = _create_test_app(unblock_event, started_event)
    event_loop_thread_id = threading.get_ident()

    with patch(
        "app.app_utils.reasoning_engine_adapter.AdkApp",
        return_value=fake_runtime,
    ):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            # Test synchronous generator method
            sync_resp = await client.post(
                "/api/stream_reasoning_engine",
                json={"class_method": "stream_query", "input": {"q": 1}},
            )
            assert sync_resp.status_code == 200
            lines = [json.loads(line) for line in sync_resp.text.strip().splitlines()]
            assert len(lines) == 2
            assert lines[0]["event"] == "sync_chunk_1"
            assert lines[0]["thread_id"] != event_loop_thread_id

            # Test asynchronous generator method
            async_resp = await client.post(
                "/api/stream_reasoning_engine",
                json={"class_method": "async_stream_query", "input": {"q": 2}},
            )
            assert async_resp.status_code == 200
            async_lines = [
                json.loads(line) for line in async_resp.text.strip().splitlines()
            ]
            assert len(async_lines) == 1
            assert async_lines[0]["event"] == "async_chunk_1"
