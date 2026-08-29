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
    assert _cik10("CIK0001442836") == "0001442836"
    assert _cik10("CIK1442836") == "0001442836"
    assert _cik10("cik0001442836") == "0001442836"
    assert _cik10("CIK 1442836") == "0001442836"
    assert _cik10("CIK0") == "0000000000"
    assert _cik10("0") == "0000000000"
    assert _cik10(0) == "0000000000"
    assert _cik10("") == ""
    assert _cik10("   ") == ""
    assert _cik10(None) == ""
    assert _cik10("invalid") == ""
    assert _cik10("CIKinvalid") == ""


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
async def test_edgar_get_json_retry_on_500() -> None:
    mock_resp_500 = MagicMock()
    mock_resp_500.status_code = 500
    mock_resp_500.headers = {}

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {"hits": {"total": {"value": 1}, "hits": []}}
    mock_resp_200.raise_for_status = MagicMock()

    with (
        patch(
            "httpx.AsyncClient.get", side_effect=[mock_resp_500, mock_resp_200]
        ) as mock_get,
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        res = await app.tools.edgar._get_json(
            "https://efts.sec.gov/LATEST/search-index"
        )
        assert res == {"hits": {"total": {"value": 1}, "hits": []}}
        assert mock_get.call_count == 2
        assert mock_sleep.await_count == 1
        sleep_delay = mock_sleep.call_args[0][0]
        assert 1.0 <= sleep_delay <= 1.25


@pytest.mark.asyncio
async def test_edgar_get_json_retry_on_request_error() -> None:
    mock_req_err = httpx.ConnectTimeout("connection timed out")

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {"data": "ok"}
    mock_resp_200.raise_for_status = MagicMock()

    with (
        patch(
            "httpx.AsyncClient.get", side_effect=[mock_req_err, mock_resp_200]
        ) as mock_get,
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        res = await app.tools.edgar._get_json("https://data.sec.gov/test")
        assert res == {"data": "ok"}
        assert mock_get.call_count == 2
        assert mock_sleep.await_count == 1
        sleep_delay = mock_sleep.call_args[0][0]
        assert 1.0 <= sleep_delay <= 1.25


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


@pytest.mark.asyncio
async def test_edgar_get_json_request_error_max_retries() -> None:
    mock_req_err = httpx.ConnectTimeout("connection timed out")

    with (
        patch("httpx.AsyncClient.get", side_effect=mock_req_err) as mock_get,
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        with pytest.raises(httpx.RequestError):
            await app.tools.edgar._get_json("https://data.sec.gov/test")
        assert mock_get.call_count == app.tools.edgar._MAX_RETRIES + 1
        assert mock_sleep.call_count == app.tools.edgar._MAX_RETRIES


@pytest.mark.asyncio
async def test_edgar_full_text_search_tolerates_500_error() -> None:
    with patch(
        "app.tools.edgar._get_json",
        side_effect=httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        ),
    ):
        res = await app.tools.edgar.edgar_full_text_search(
            query='"Day One Biopharmaceuticals"', forms="10-Q"
        )
        assert res["total"] == 0
        assert res["hits"] == []
        assert "error" in res
        assert "500" in res["error"]


@pytest.mark.asyncio
async def test_edgar_get_json_non_retryable_status_code() -> None:
    mock_resp_404 = MagicMock()
    mock_resp_404.status_code = 404
    mock_resp_404.raise_for_status.side_effect = httpx.HTTPStatusError(
        "404 Not Found",
        request=MagicMock(),
        response=mock_resp_404,
    )

    with patch("httpx.AsyncClient.get", return_value=mock_resp_404) as mock_get:
        with pytest.raises(httpx.HTTPStatusError):
            await app.tools.edgar._get_json("https://data.sec.gov/test")
        # Should fail immediately without retrying
        assert mock_get.call_count == 1


@pytest.mark.asyncio
async def test_edgar_get_json_respects_retry_after() -> None:
    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429
    mock_resp_429.headers = {"Retry-After": "30.0"}

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {"data": "ok"}
    mock_resp_200.raise_for_status = MagicMock()

    with (
        patch(
            "httpx.AsyncClient.get", side_effect=[mock_resp_429, mock_resp_200]
        ) as mock_get,
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        res = await app.tools.edgar._get_json("https://data.sec.gov/test")
        assert res == {"data": "ok"}
        assert mock_get.call_count == 2
        mock_sleep.assert_awaited_once_with(30.0)


@pytest.mark.asyncio
async def test_edgar_get_json_retry_on_503_with_retry_after() -> None:
    mock_resp_503 = MagicMock()
    mock_resp_503.status_code = 503
    mock_resp_503.headers = {"Retry-After": "2.5"}

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {"data": "ok"}
    mock_resp_200.raise_for_status = MagicMock()

    with (
        patch(
            "httpx.AsyncClient.get", side_effect=[mock_resp_503, mock_resp_200]
        ) as mock_get,
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        res = await app.tools.edgar._get_json("https://data.sec.gov/test")
        assert res == {"data": "ok"}
        assert mock_get.call_count == 2
        mock_sleep.assert_awaited_once_with(2.5)


@pytest.mark.asyncio
async def test_edgar_get_json_caps_retry_after_at_max_delay() -> None:
    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429
    mock_resp_429.headers = {"Retry-After": "3600.0"}

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {"data": "ok"}
    mock_resp_200.raise_for_status = MagicMock()

    with (
        patch(
            "httpx.AsyncClient.get", side_effect=[mock_resp_429, mock_resp_200]
        ) as mock_get,
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        res = await app.tools.edgar._get_json("https://data.sec.gov/test")
        assert res == {"data": "ok"}
        assert mock_get.call_count == 2
        mock_sleep.assert_awaited_once_with(60.0)


@pytest.mark.asyncio
async def test_edgar_get_json_retry_on_json_decode_error() -> None:
    mock_resp_corrupt = MagicMock()
    mock_resp_corrupt.status_code = 200
    mock_resp_corrupt.json.side_effect = ValueError("Invalid JSON")
    mock_resp_corrupt.raise_for_status = MagicMock()

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {"data": "ok"}
    mock_resp_200.raise_for_status = MagicMock()

    with (
        patch(
            "httpx.AsyncClient.get",
            side_effect=[mock_resp_corrupt, mock_resp_200],
        ) as mock_get,
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        res = await app.tools.edgar._get_json("https://data.sec.gov/test")
        assert res == {"data": "ok"}
        assert mock_get.call_count == 2
        assert mock_sleep.await_count == 1
        sleep_delay = mock_sleep.call_args[0][0]
        assert 1.0 <= sleep_delay <= 1.25


@pytest.mark.asyncio
async def test_edgar_get_json_retry_after_http_date() -> None:
    mock_resp_503 = MagicMock()
    mock_resp_503.status_code = 503
    mock_resp_503.headers = {"Retry-After": "Wed, 21 Oct 2099 07:28:00 GMT"}

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {"data": "ok"}
    mock_resp_200.raise_for_status = MagicMock()

    with (
        patch(
            "httpx.AsyncClient.get", side_effect=[mock_resp_503, mock_resp_200]
        ) as mock_get,
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        res = await app.tools.edgar._get_json("https://data.sec.gov/test")
        assert res == {"data": "ok"}
        assert mock_get.call_count == 2
        mock_sleep.assert_awaited_once_with(60.0)


@pytest.mark.asyncio
async def test_edgar_find_company_tolerates_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app.tools.edgar, "_TICKERS_CACHE", None)
    with patch(
        "app.tools.edgar._get_json",
        side_effect=httpx.ConnectError("failed to connect"),
    ):
        res = await app.tools.edgar.edgar_find_company("Day One")
        assert res["matches"] == []
        assert "error" in res


@pytest.mark.asyncio
async def test_edgar_key_financials_handles_none_end_date() -> None:
    mock_payload = {
        "entityName": "Test Biotech",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "val": 100,
                                "end": None,
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                            },
                            {
                                "val": 200,
                                "end": "2023-12-31",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                            },
                        ]
                    }
                }
            }
        },
    }
    with patch("app.tools.edgar._get_json", return_value=mock_payload):
        res = await app.tools.edgar.edgar_key_financials("0001831868")
        assert res["entity"] == "Test Biotech"
        assert "Revenues" in res["facts"]
        assert len(res["facts"]["Revenues"]["recent"]) == 2


@pytest.mark.asyncio
async def test_edgar_recent_filings_handles_none_fields() -> None:
    mock_payload = {
        "name": "Test Biotech",
        "filings": {
            "recent": {
                "form": ["10-K", None],
                "filingDate": ["2024-01-01", None],
                "accessionNumber": ["000123-24-000001", None],
                "primaryDocument": ["doc.htm", None],
                "primaryDocDescription": ["Annual report", None],
            }
        },
    }
    with patch("app.tools.edgar._get_json", return_value=mock_payload):
        res = await app.tools.edgar.edgar_recent_filings("0001831868")
        assert res["name"] == "Test Biotech"
        assert len(res["filings"]) == 1
        assert res["filings"][0]["form"] == "10-K"


@pytest.mark.asyncio
async def test_edgar_recent_filings_tolerates_error() -> None:
    with patch(
        "app.tools.edgar._get_json",
        side_effect=httpx.HTTPStatusError(
            "404 Not Found",
            request=MagicMock(),
            response=MagicMock(status_code=404),
        ),
    ):
        res = await app.tools.edgar.edgar_recent_filings("0001831868")
        assert res["name"] is None
        assert res["industry"] is None
        assert res["fiscal_year_end"] is None
        assert res["exchanges"] is None
        assert res["filings"] == []
        assert "error" in res


@pytest.mark.asyncio
async def test_edgar_key_financials_tolerates_error() -> None:
    with patch(
        "app.tools.edgar._get_json",
        side_effect=httpx.ConnectTimeout("timeout"),
    ):
        res = await app.tools.edgar.edgar_key_financials("0001831868")
        assert res["entity"] is None
        assert res["facts"] == {}
        assert "error" in res


@pytest.mark.asyncio
async def test_edgar_find_company_empty_query() -> None:
    res = await app.tools.edgar.edgar_find_company("")
    assert res == {"matches": []}
    res_spaces = await app.tools.edgar.edgar_find_company("   ")
    assert res_spaces == {"matches": []}


@pytest.mark.asyncio
async def test_edgar_recent_filings_handles_none_forms() -> None:
    mock_payload = {
        "name": "Test Biotech",
        "filings": {
            "recent": {
                "form": ["10-K", "8-K"],
                "filingDate": ["2024-01-01", "2024-02-01"],
                "accessionNumber": ["000123-24-000001", "000123-24-000002"],
                "primaryDocument": ["doc1.htm", "doc2.htm"],
                "primaryDocDescription": ["Annual report", "Current report"],
            }
        },
    }
    with patch("app.tools.edgar._get_json", return_value=mock_payload):
        # Passing forms=None should include all forms without raising AttributeError
        res = await app.tools.edgar.edgar_recent_filings("0001831868", forms=None)  # type: ignore[arg-type]
        assert res["name"] == "Test Biotech"
        assert len(res["filings"]) == 2


def test_parse_retry_after_handling() -> None:
    # Seconds
    assert app.tools.edgar._parse_retry_after("120") == 120.0
    assert app.tools.edgar._parse_retry_after("2.5") == 2.5
    assert app.tools.edgar._parse_retry_after(None) is None
    assert app.tools.edgar._parse_retry_after("") is None
    assert app.tools.edgar._parse_retry_after("invalid_header") is None
    # HTTP date in the future
    future_delay = app.tools.edgar._parse_retry_after("Wed, 21 Oct 2099 07:28:00 GMT")
    assert future_delay is not None
    assert future_delay > 0


@pytest.mark.asyncio
async def test_edgar_full_text_search_handles_string_display_names() -> None:
    mock_payload = {
        "hits": {
            "total": {"value": 1},
            "hits": [
                {
                    "_id": "000123-24-000001",
                    "_source": {
                        "display_names": "Direct String Company",
                        "root_forms": "10-K",
                        "file_date": "2024-01-01",
                    },
                }
            ],
        }
    }
    with patch("app.tools.edgar._get_json", return_value=mock_payload):
        res = await app.tools.edgar.edgar_full_text_search("cancer therapy")
        assert res["total"] == 1
        assert len(res["hits"]) == 1
        assert res["hits"][0]["company"] == "Direct String Company"
        assert res["hits"][0]["form"] == "10-K"


@pytest.mark.asyncio
async def test_edgar_full_text_search_handles_integer_total() -> None:
    mock_payload = {
        "hits": {
            "total": 42,
            "hits": [],
        }
    }
    with patch("app.tools.edgar._get_json", return_value=mock_payload):
        res = await app.tools.edgar.edgar_full_text_search("cancer therapy")
        assert res["total"] == 42
        assert res["hits"] == []


@pytest.mark.asyncio
async def test_load_tickers_concurrent_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app.tools.edgar, "_TICKERS_CACHE", None)
    monkeypatch.setattr(app.tools.edgar, "_TICKERS_LOCK", None)
    mock_data = {
        "0": {
            "cik_str": 1442836,
            "ticker": "MRSN",
            "title": "Mersana Therapeutics",
        }
    }

    with patch(
        "app.tools.edgar._get_json",
        new_callable=AsyncMock,
        return_value=mock_data,
    ) as mock_get:
        results = await asyncio.gather(
            app.tools.edgar._load_tickers(),
            app.tools.edgar._load_tickers(),
            app.tools.edgar._load_tickers(),
        )
        assert len(results) == 3
        assert results[0] == [
            {
                "cik_str": 1442836,
                "ticker": "MRSN",
                "title": "Mersana Therapeutics",
            }
        ]
        assert results[1] == results[0]
        assert results[2] == results[0]
        assert mock_get.call_count == 1


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
