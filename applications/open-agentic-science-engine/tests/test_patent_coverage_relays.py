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

"""Tests for the patent search coverage relays.

Covers:
  1. Every page requests ``num=100``, so the 300-patent cap is reachable
  2. A capped set emits ``patent.result_set_capped`` in the sidecar
  3. A set that reached the end of the results emits neither code
  4. A 503 on a later page is recorded and emits ``patent.fetch_fault``
  5. ``patent analyze`` carries the relay forward, including for an
     artifact written before ``fetch_faults`` existed
  6. ``differentiation assess`` carries it forward on an ``uncrowded``
     verdict and states the cap in its coverage limits
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path
from typing import Any

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

COVERAGE_CODES = {"patent.result_set_capped", "patent.fetch_fault"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(base: Path) -> Path:
    """Create a minimal OASE project directory."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".oase").mkdir(exist_ok=True)
    return project


class _Response:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None):
        self.status_code = status_code
        self.content = json.dumps(payload or {}).encode("utf-8")


def _page(start: int, n: int, total: int, year: str = "2010") -> _Response:
    """One page of Google Patents XHR results, ``n`` patents from ``start``."""
    results = [
        {
            "patent": {
                "publication_number": f"US{start + i:07d}A1",
                "title": f"Patent {start + i}",
                "assignee": f"Assignee {(start + i) % 7}",
                "priority_date": f"{year}-01-01",
            }
        }
        for i in range(n)
    ]
    return _Response(
        200,
        {"results": {"total_num_results": total, "cluster": [{"result": results}]}},
    )


def _search(project: Path, pages: list[_Response], query: str = "KRAS"):
    """Run `oase patent search` against canned pages; return (result, urls)."""
    from click.testing import CliRunner
    from oase.cli import cli

    urls: list[str] = []
    remaining = list(pages)

    def fake_request(method: str, url: str, **kwargs: Any) -> _Response:
        urls.append(url)
        return remaining.pop(0) if remaining else _page(0, 0, 0)

    with mock.patch("oase.commands.patent.http.request", side_effect=fake_request):
        result = CliRunner().invoke(
            cli,
            ["--project", str(project), "patent", "search", query],
            catch_exceptions=False,
        )
    assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"
    return result, urls


def _read(project: Path, suffix: str) -> dict[str, Any]:
    paths = list((project / "raw" / "ip").glob(f"*{suffix}"))
    assert len(paths) == 1, f"expected one *{suffix}, found {paths}"
    return json.loads(paths[0].read_text(encoding="utf-8"))


def _codes(record: dict[str, Any]) -> set[str]:
    return {r["code"] for r in record.get("mandatory_relays", [])}


# ---------------------------------------------------------------------------
# 1. Page size
# ---------------------------------------------------------------------------


def test_every_page_requests_page_size() -> None:
    """Without num= the endpoint serves 10 per page and the cap is unreachable."""
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        pages = [_page(0, 100, 500), _page(100, 100, 500), _page(200, 100, 500)]
        _, urls = _search(project, pages)

    assert len(urls) == 3, urls
    assert all("num%3D100" in u for u in urls), urls
    assert "page%3D" not in urls[0], urls[0]
    assert "page%3D1" in urls[1] and "page%3D2" in urls[2], urls
    print("  PASS: every page requests num=100")


# ---------------------------------------------------------------------------
# 2-4. Search sidecar
# ---------------------------------------------------------------------------


def test_capped_set_emits_result_set_capped() -> None:
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        pages = [_page(0, 100, 177123), _page(100, 100, 177123)]
        pages.append(_page(200, 100, 177123))
        _search(project, pages)
        artifact = _read(project, ".artifact.json")
        meta = _read(project, ".meta.json")

    assert artifact["summary"]["n_patents"] == 300
    assert artifact["summary"]["fetch_faults"] == []
    assert _codes(meta) & COVERAGE_CODES == {"patent.result_set_capped"}, meta
    print("  PASS: capped set emits patent.result_set_capped")


def test_complete_set_emits_no_coverage_relay() -> None:
    """A clean input emits no relays (tool-design-guidance §5.1 rule 4)."""
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _search(project, [_page(0, 5, 5)])
        meta = _read(project, ".meta.json")

    assert not _codes(meta) & COVERAGE_CODES, meta
    print("  PASS: complete set emits no coverage relay")


def test_later_page_503_is_a_fetch_fault() -> None:
    """A refused page must not read as the end of the results."""
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _search(project, [_page(0, 100, 250), _Response(503)])
        artifact = _read(project, ".artifact.json")
        meta = _read(project, ".meta.json")

    assert artifact["summary"]["n_patents"] == 100
    assert artifact["summary"]["fetch_faults"] == [{"page": 1, "status": 503}]
    assert meta["fetch_faults"] == [{"page": 1, "status": 503}]
    assert _codes(meta) & COVERAGE_CODES == COVERAGE_CODES, meta
    print("  PASS: later-page 503 emits patent.fetch_fault")


# ---------------------------------------------------------------------------
# 5. patent analyze
# ---------------------------------------------------------------------------


def _analyze(project: Path, query: str = "KRAS") -> dict[str, Any]:
    from click.testing import CliRunner
    from oase.cli import cli

    result = CliRunner().invoke(
        cli,
        ["--project", str(project), "patent", "analyze", query],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"
    return _read(project, ".patent-google-patents.analysis.json")


def test_analyze_carries_relays_forward() -> None:
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _search(project, [_page(0, 100, 250), _Response(503)])
        analysis = _analyze(project)

    assert _codes(analysis) & COVERAGE_CODES == COVERAGE_CODES, analysis
    print("  PASS: patent analyze carries both relays forward")


def test_analyze_relays_cap_on_legacy_artifact() -> None:
    """An artifact from before fetch_faults existed still gets the cap relay."""
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        ip_dir = project / "raw" / "ip"
        ip_dir.mkdir(parents=True)
        patents = [
            {"publication_number": f"US{i}", "assignee": "A", "priority_date": "2010"}
            for i in range(30)
        ]
        legacy = {
            "schema": "oase.patent.v1",
            "query": {"term": "KRAS", "source": "google-patents"},
            "summary": {"n_patents": 30, "total_results": 177123},
            "patents": patents,
        }
        (ip_dir / "kras.patent-google-patents.artifact.json").write_text(
            json.dumps(legacy), encoding="utf-8"
        )
        analysis = _analyze(project)

    assert analysis["assessment"]["verdict"] == "fto_risk_low"
    assert _codes(analysis) & COVERAGE_CODES == {"patent.result_set_capped"}
    print("  PASS: legacy 30-of-177123 artifact gets the cap relay")


# ---------------------------------------------------------------------------
# 6. differentiation assess
# ---------------------------------------------------------------------------


def test_differentiation_uncrowded_on_capped_set_is_qualified() -> None:
    """`uncrowded` over the top 300 by relevance must not go out bare."""
    from click.testing import CliRunner
    from oase.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        pages = [_page(0, 100, 177123), _page(100, 100, 177123)]
        pages.append(_page(200, 100, 177123))
        _search(project, pages)
        result = CliRunner().invoke(
            cli,
            ["--project", str(project), "differentiation", "assess", "KRAS"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"
        assessment = _read(project, ".differentiation.json")
        analysis = _read(project, ".differentiation.analysis.json")

    comp = assessment["dimensions"]["competitor_activity"]
    assert comp["density"] == "uncrowded", comp
    assert "patent.result_set_capped" in _codes(analysis), analysis
    assert comp["coverage_limits"].startswith("only the first 300 of 177123 results"), (
        comp["coverage_limits"]
    )
    print("  PASS: differentiation carries the cap relay and states it")


def test_differentiation_complete_set_keeps_default_limits() -> None:
    from click.testing import CliRunner
    from oase.cli import cli
    from oase.commands.differentiation import DEFAULT_COVERAGE_LIMITS

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _search(project, [_page(0, 5, 5)])
        result = CliRunner().invoke(
            cli,
            ["--project", str(project), "differentiation", "assess", "KRAS"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"
        assessment = _read(project, ".differentiation.json")
        analysis = _read(project, ".differentiation.analysis.json")

    comp = assessment["dimensions"]["competitor_activity"]
    assert comp["coverage_limits"] == DEFAULT_COVERAGE_LIMITS
    assert not _codes(analysis) & COVERAGE_CODES, analysis
    print("  PASS: complete set keeps the default coverage limits")


def test_codes_registered() -> None:
    from oase.core.provenance import RELAY_CODES

    assert COVERAGE_CODES <= set(RELAY_CODES)
    print("  PASS: both codes registered")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception:
            import traceback

            traceback.print_exc()
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    if failed:
        sys.exit(1)
    else:
        print("All tests passed.")


if __name__ == "__main__":
    main()
