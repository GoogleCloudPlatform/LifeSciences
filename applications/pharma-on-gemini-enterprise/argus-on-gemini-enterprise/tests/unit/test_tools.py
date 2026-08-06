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

import importlib
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from google.adk.tools import ToolContext

import app.tools.edgar
from app.tools.assets import asset_path, resolve_tokens_to_paths, save_asset
from app.tools.charts import bar_chart, horizontal_bar_chart, line_chart
from app.tools.edgar import _cik10
from app.tools.infographic import generate_slide, make_infographic
from app.tools.pdf_renderer import _dedupe_stutters, _sanitize_latex


def test_cik10() -> None:
    assert _cik10("1442836") == "0001442836"
    assert _cik10(1442836) == "0001442836"
    assert _cik10("0001442836") == "0001442836"


def test_edgar_user_agent_validation(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("EDGAR_USER_AGENT", raising=False)
    with pytest.raises(ValueError, match="EDGAR_USER_AGENT"):
        importlib.reload(app.tools.edgar)
    assert "EDGAR_USER_AGENT environment variable is not set" in caplog.text

    caplog.clear()
    monkeypatch.setenv("EDGAR_USER_AGENT", "   ")
    with pytest.raises(ValueError, match="EDGAR_USER_AGENT"):
        importlib.reload(app.tools.edgar)
    assert "EDGAR_USER_AGENT environment variable is not set" in caplog.text

    monkeypatch.setenv("EDGAR_USER_AGENT", "SampleCompany AdminContact@example.com")
    importlib.reload(app.tools.edgar)
    assert app.tools.edgar._USER_AGENT == "SampleCompany AdminContact@example.com"
    assert (
        app.tools.edgar._HEADERS["User-Agent"]
        == "SampleCompany AdminContact@example.com"
    )


@pytest.mark.asyncio
async def test_edgar_get_json_retry_on_429() -> None:
    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429
    mock_resp_429.headers = {"Retry-After": "0.5"}

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {"data": "test_data"}
    mock_resp_200.raise_for_status = MagicMock()

    with (
        patch(
            "httpx.AsyncClient.get", side_effect=[mock_resp_429, mock_resp_200]
        ) as mock_get,
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        res = await app.tools.edgar._get_json("https://data.sec.gov/test")
        assert res == {"data": "test_data"}
        assert mock_get.call_count == 2
        mock_sleep.assert_awaited_once_with(0.5)


@pytest.mark.asyncio
async def test_edgar_get_json_max_retries_exceeded() -> None:
    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429
    mock_resp_429.headers = {}
    mock_resp_429.raise_for_status.side_effect = httpx.HTTPStatusError(
        "429 Too Many Requests",
        request=MagicMock(),
        response=mock_resp_429,
    )

    with (
        patch("httpx.AsyncClient.get", return_value=mock_resp_429) as mock_get,
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        with pytest.raises(httpx.HTTPStatusError):
            await app.tools.edgar._get_json("https://data.sec.gov/test")
        assert mock_get.call_count == app.tools.edgar._MAX_RETRIES + 1
        assert mock_sleep.call_count == app.tools.edgar._MAX_RETRIES


def test_sanitize_latex() -> None:
    text = r"The value is $\ge 50\%$ with $\alpha = 0.05$."
    sanitized = _sanitize_latex(text)
    assert "≥ 50%" in sanitized
    assert "α = 0.05" in sanitized


def test_dedupe_stutters() -> None:
    assert _dedupe_stutters("SEC SEC Filings") == "SEC Filings"
    assert _dedupe_stutters("FDA FDA review") == "FDA review"


def test_asset_store() -> None:
    token = save_asset("test_asset", b"dummy_png_bytes")
    assert token == "asset://test_asset"
    path = asset_path("test_asset")
    assert path is not None
    assert os.path.exists(path)
    resolved = resolve_tokens_to_paths(f"![Image]({token})")
    assert path in resolved


def test_charts_generation() -> None:
    res = bar_chart(
        asset_id="test_bar",
        title="Test Bar",
        categories=["A", "B", "C"],
        values=[10.0, 20.0, 30.0],
    )
    assert res["status"] == "success"
    assert res["token"] == "asset://test_bar"

    res_line = line_chart(
        asset_id="test_line",
        title="Test Line",
        x_labels=["Q1", "Q2"],
        series={"Revenue": [10.0, 20.0]},
    )
    assert res_line["status"] == "success"

    res_hbar = horizontal_bar_chart(
        asset_id="test_hbar",
        title="Test Horizontal Bar",
        labels=["Alpha", "Beta"],
        values=[5.0, 15.0],
    )
    assert res_hbar["status"] == "success"


@pytest.mark.asyncio
async def test_make_infographic() -> None:
    mock_client = MagicMock()
    mock_aclient = AsyncMock()
    mock_client.aio.__aenter__.return_value = mock_aclient
    mock_client.aio.__aexit__.return_value = None

    mock_part = MagicMock()
    mock_part.inline_data = MagicMock(data=b"fake_image_bytes")
    mock_response = MagicMock()
    mock_response.candidates = [MagicMock(content=MagicMock(parts=[mock_part]))]
    mock_aclient.models.generate_content.return_value = mock_response

    with patch("app.tools.infographic._create_client", return_value=mock_client):
        res = await make_infographic("A 2x2 matrix", "test_info_asset", "16:9")
        assert res["status"] == "success"
        assert res["token"] == "asset://test_info_asset"
        mock_aclient.models.generate_content.assert_awaited()


@pytest.mark.asyncio
async def test_generate_slide() -> None:
    mock_client = MagicMock()
    mock_aclient = AsyncMock()
    mock_client.aio.__aenter__.return_value = mock_aclient
    mock_client.aio.__aexit__.return_value = None

    mock_part = MagicMock()
    mock_part.inline_data = MagicMock(data=b"fake_slide_bytes")
    mock_response = MagicMock()
    mock_response.candidates = [MagicMock(content=MagicMock(parts=[mock_part]))]
    mock_aclient.models.generate_content.return_value = mock_response

    mock_tool_context = MagicMock(spec=ToolContext)
    mock_tool_context.save_artifact = AsyncMock(return_value=1)

    with patch("app.tools.infographic._create_client", return_value=mock_client):
        res = await generate_slide(
            title="Overview",
            body="Key points",
            tool_context=mock_tool_context,
            subtitle="Subtitle",
        )
        assert res["status"] == "success"
        assert res["filename"] == "slide_overview.png"
        assert res["version"] == 1
        mock_tool_context.save_artifact.assert_awaited_once()
