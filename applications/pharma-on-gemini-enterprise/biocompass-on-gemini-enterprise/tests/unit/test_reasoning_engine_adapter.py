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

"""Unit tests for reasoning_engine_adapter async/threadpool & streaming dispatch."""

import asyncio
import json
import threading
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.app_utils.reasoning_engine_adapter import attach_reasoning_engine_routes


class _FakeAdkApp:
    """Fake AdkApp exposing both sync and async query and stream methods."""

    def __init__(self, **kwargs):
        self.last_sync_thread_id: int | None = None

    def set_up(self) -> None:
        pass

    def register_operations(self) -> dict[str, list[str]]:
        return {
            "": ["query"],
            "async": ["async_query"],
            "stream": ["stream_query"],
            "async_stream": ["async_stream_query"],
        }

    def query(self, message: str = "") -> dict[str, object]:
        self.last_sync_thread_id = threading.get_ident()
        time.sleep(0.15)
        return {"echo": message}

    async def async_query(self, message: str = "") -> dict[str, object]:
        await asyncio.sleep(0.01)
        return {"async_echo": message}

    def stream_query(self, message: str = ""):
        yield {"chunk": 1, "message": message}
        yield {"chunk": 2, "message": message}

    async def async_stream_query(self, message: str = ""):
        await asyncio.sleep(0.01)
        yield {"async_chunk": 1, "message": message}
        yield {"async_chunk": 2, "message": message}


@pytest.fixture
def fake_app_and_runtime():
    fake_runtime = _FakeAdkApp()
    app = FastAPI()
    attach_reasoning_engine_routes(app)
    with patch(
        "app.app_utils.reasoning_engine_adapter.AdkApp",
        return_value=fake_runtime,
    ):
        yield app, fake_runtime


@pytest.mark.asyncio
async def test_sync_method_offloaded_to_threadpool_and_does_not_block_loop(
    fake_app_and_runtime,
):
    """Sync methods on /api/reasoning_engine must run in a worker thread without blocking the event loop."""
    app, fake_runtime = fake_app_and_runtime
    loop_thread_id = threading.get_ident()
    ticks = 0
    stop = False

    async def ticker():
        nonlocal ticks
        while not stop:
            await asyncio.sleep(0.02)
            ticks += 1

    ticker_task = asyncio.create_task(ticker())
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/reasoning_engine",
                json={"class_method": "query", "input": {"message": "hello"}},
            )
    finally:
        stop = True
        await ticker_task

    assert resp.status_code == 200
    assert resp.json() == {"output": {"echo": "hello"}}
    assert fake_runtime.last_sync_thread_id is not None
    assert fake_runtime.last_sync_thread_id != loop_thread_id
    assert ticks >= 2


@pytest.mark.asyncio
async def test_sync_stream_method_streams_events_without_type_error(
    fake_app_and_runtime,
):
    """Sync generator methods registered in operations['stream'] must stream without TypeError."""
    app, _ = fake_app_and_runtime
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/stream_reasoning_engine",
            json={"class_method": "stream_query", "input": {"message": "sync"}},
        )

    assert resp.status_code == 200
    lines = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
    assert lines == [
        {"chunk": 1, "message": "sync"},
        {"chunk": 2, "message": "sync"},
    ]


@pytest.mark.asyncio
async def test_async_stream_method_streams_events(fake_app_and_runtime):
    """Async generator methods registered in operations['async_stream'] must stream events."""
    app, _ = fake_app_and_runtime
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/stream_reasoning_engine",
            json={
                "class_method": "async_stream_query",
                "input": {"message": "async"},
            },
        )

    assert resp.status_code == 200
    lines = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
    assert lines == [
        {"async_chunk": 1, "message": "async"},
        {"async_chunk": 2, "message": "async"},
    ]


@pytest.mark.asyncio
async def test_async_method_executes_directly(fake_app_and_runtime):
    """Async coroutine methods on /api/reasoning_engine must execute and return JSON."""
    app, _ = fake_app_and_runtime
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/reasoning_engine",
            json={"class_method": "async_query", "input": {"message": "hi"}},
        )

    assert resp.status_code == 200
    assert resp.json() == {"output": {"async_echo": "hi"}}
