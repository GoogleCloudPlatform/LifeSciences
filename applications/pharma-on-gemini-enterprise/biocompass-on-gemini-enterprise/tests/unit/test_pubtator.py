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

"""Unit tests for PubTator3 client rate limiting and API wrappers."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.tools import pubtator


@pytest.mark.asyncio
async def test_rate_limit_concurrent_spacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrent calls to _rate_limit() must be spaced by at least _RATE_LIMIT_S."""
    interval = 0.05
    monkeypatch.setattr(pubtator, "_RATE_LIMIT_S", interval)
    monkeypatch.setattr(pubtator, "_last_request", 0.0)

    timestamps: list[float] = []

    async def worker() -> None:
        await pubtator._rate_limit()
        timestamps.append(time.monotonic())

    await asyncio.gather(*(worker() for _ in range(4)))

    assert len(timestamps) == 4
    timestamps.sort()
    for i in range(1, len(timestamps)):
        gap = timestamps[i] - timestamps[i - 1]
        assert gap >= interval * 0.9, (
            f"Requests {i - 1} and {i} executed {gap:.4f}s apart "
            f"(expected >= {interval:.4f}s)"
        )


@pytest.mark.asyncio
async def test_annotate_articles_concurrent_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent annotate_articles() calls must throttle outbound HTTP requests."""
    interval = 0.05
    monkeypatch.setattr(pubtator, "_RATE_LIMIT_S", interval)
    monkeypatch.setattr(pubtator, "_last_request", 0.0)

    request_times: list[float] = []

    async def fake_get_json(*args, **kwargs):
        request_times.append(time.monotonic())
        return {"PubTator3": [{"id": "123"}]}

    with patch.object(
        pubtator._http, "get_json", side_effect=AsyncMock(side_effect=fake_get_json)
    ):
        results = await asyncio.gather(
            pubtator.annotate_articles(["1"]),
            pubtator.annotate_articles(["2"]),
            pubtator.annotate_articles(["3"]),
        )

    assert len(results) == 3
    assert len(request_times) == 3
    request_times.sort()
    for i in range(1, len(request_times)):
        gap = request_times[i] - request_times[i - 1]
        assert gap >= interval * 0.9, (
            f"HTTP requests {i - 1} and {i} fired {gap:.4f}s apart "
            f"(expected >= {interval:.4f}s)"
        )
