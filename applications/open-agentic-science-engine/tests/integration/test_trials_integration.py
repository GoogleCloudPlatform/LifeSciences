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

"""Integration tests for ``oase trials search`` and ``oase trials analyze`` (#245).

Covers:
  1. End-to-end CLI ``search`` + ``analyze`` over real HTTP through
     ``oase.core.http.request`` against a local ``http.server.HTTPServer``
  2. Live ClinicalTrials.gov v2 API validation for both a capped query
     (``EGFR``, 500 of >5,000 studies -> emits ``trials.result_set_capped``)
     and an uncapped query (``NCT00001372``, 1 of 1 study -> no coverage relay)
     when network access to ``clinicaltrials.gov`` is available
"""

from __future__ import annotations

import http.server
import json
import sys
import tempfile
import threading
import unittest.mock as mock
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

COVERAGE_CODE = "trials.result_set_capped"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(base: Path) -> Path:
    """Create a minimal OASE project directory."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".oase").mkdir(exist_ok=True)
    return project


def _page_payload(
    start: int,
    n: int,
    total: int | None = None,
    next_token: str | None = None,
    phase: str = "PHASE2",
    status: str = "RECRUITING",
) -> dict[str, Any]:
    """Build one page of ClinicalTrials.gov v2 study JSON."""
    studies = [
        {
            "protocolSection": {
                "identificationModule": {
                    "nctId": f"NCT{start + i:08d}",
                    "briefTitle": f"Study {start + i}",
                },
                "statusModule": {
                    "overallStatus": status,
                    "startDateStruct": {"date": "2022-01-01"},
                    "completionDateStruct": {"date": "2024-01-01"},
                },
                "designModule": {
                    "phases": [phase],
                    "enrollmentInfo": {"count": 50},
                },
                "armsInterventionsModule": {
                    "interventions": [{"type": "DRUG", "name": "TestDrug"}],
                },
                "conditionsModule": {"conditions": ["Solid Tumor"]},
                "sponsorCollaboratorsModule": {
                    "leadSponsor": {"name": f"Sponsor {(start + i) % 5}"},
                },
            }
        }
        for i in range(n)
    ]
    payload: dict[str, Any] = {"studies": studies}
    if total is not None:
        payload["totalCount"] = total
    if next_token is not None:
        payload["nextPageToken"] = next_token
    return payload


def _read(project: Path, suffix: str) -> dict[str, Any]:
    paths = list((project / "raw" / "pipeline").glob(f"*{suffix}"))
    assert len(paths) == 1, f"expected one *{suffix}, found {paths}"
    return json.loads(paths[0].read_text(encoding="utf-8"))


def _codes(record: dict[str, Any]) -> set[str]:
    return {r["code"] for r in record.get("mandatory_relays", [])}


def _analyze(project: Path, query: str) -> dict[str, Any]:
    from click.testing import CliRunner
    from oase.cli import cli
    from oase.core import http as oase_http

    try:
        result = CliRunner().invoke(
            cli,
            ["--project", str(project), "trials", "analyze", query],
            env={"OASE_NO_DIRTY_WARNING": "1"},
            catch_exceptions=False,
        )
    finally:
        oase_http._network_forbidden = None
    assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"
    return _read(project, ".trials-clinicaltrials.analysis.json")


# ---------------------------------------------------------------------------
# 1. Local HTTP server end-to-end integration test
# ---------------------------------------------------------------------------


def test_local_http_server_end_to_end() -> None:
    """End-to-end CLI search + analyze over real HTTP via a local HTTPServer."""
    from click.testing import CliRunner
    from oase.cli import cli
    from oase.core import http as oase_http

    received_queries: list[dict[str, list[str]]] = []
    pages_payloads = [
        _page_payload(
            i * 100,
            100,
            total=1200 if i == 0 else None,
            next_token=f"cursor_{i + 1}",
        )
        for i in range(5)
    ]

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            idx = len(received_queries)
            received_queries.append(params)
            body = json.dumps(pages_payloads[idx]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    oase_http._network_forbidden = None
    try:
        local_api = f"http://127.0.0.1:{port}/api/v2/studies"
        with (
            tempfile.TemporaryDirectory() as td,
            mock.patch("oase.commands.trials.CLINICALTRIALS_API", local_api),
            mock.patch("oase.commands.trials.qps_for_host", return_value=0.0),
        ):
            project = _make_project(Path(td))
            runner = CliRunner()

            test_env = {
                "OASE_NO_DIRTY_WARNING": "1",
                "NO_PROXY": "127.0.0.1,localhost",
                "no_proxy": "127.0.0.1,localhost",
            }
            search_res = runner.invoke(
                cli,
                ["--project", str(project), "trials", "search", "--json", "KRAS"],
                env=test_env,
                catch_exceptions=False,
            )
            assert search_res.exit_code == 0, search_res.output
            search_json = json.loads(search_res.output)

            try:
                analyze_res = runner.invoke(
                    cli,
                    ["--project", str(project), "trials", "analyze", "--json", "KRAS"],
                    env=test_env,
                    catch_exceptions=False,
                )
            finally:
                oase_http._network_forbidden = None
            assert analyze_res.exit_code == 0, analyze_res.output
            analyze_json = json.loads(analyze_res.output)

            artifact = _read(project, ".artifact.json")
            meta = _read(project, ".meta.json")
            analysis = _read(project, ".trials-clinicaltrials.analysis.json")
    finally:
        server.shutdown()
        server.server_close()

    assert len(received_queries) == 5, received_queries
    assert received_queries[0].get("countTotal") == ["true"], received_queries[0]
    assert "pageToken" not in received_queries[0], received_queries[0]
    for idx in range(1, 5):
        assert "countTotal" not in received_queries[idx], received_queries[idx]
        assert received_queries[idx].get("pageToken") == [f"cursor_{idx}"]

    assert search_json["n_studies"] == 500
    assert search_json["total_results"] == 1200
    assert artifact["summary"]["n_studies"] == 500
    assert artifact["summary"]["total_results"] == 1200
    assert COVERAGE_CODE in _codes(meta), meta
    assert COVERAGE_CODE in _codes(analysis), analysis
    assert any(r["code"] == COVERAGE_CODE for r in analyze_json.get("relays", [])), (
        analyze_json
    )
    print("  PASS: local HTTP server end-to-end integration test")


# ---------------------------------------------------------------------------
# 2. Live ClinicalTrials.gov v2 API integration test
# ---------------------------------------------------------------------------


def _live_clinicaltrials_reachable() -> bool:
    """Return True if live ClinicalTrials.gov v2 API is reachable in this environment."""
    probe_url = (
        "https://clinicaltrials.gov/api/v2/studies"
        "?query.term=EGFR&pageSize=1&countTotal=true&format=json"
    )
    req = urllib.request.Request(probe_url, headers={"User-Agent": "oase-test/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def test_live_clinicaltrials_api() -> None:
    """Validate against live ClinicalTrials.gov v2 API when network is available."""
    if not _live_clinicaltrials_reachable():
        msg = "ClinicalTrials.gov v2 API not reachable in this network environment"
        if "pytest" in sys.modules:
            import pytest

            pytest.skip(msg)
        print(f"  SKIP: {msg}")
        return

    from click.testing import CliRunner
    from oase.cli import cli
    from oase.core import http as oase_http

    oase_http._network_forbidden = None
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))

        # 1. Capped query: EGFR has >5,000 studies on ClinicalTrials.gov.
        search_res = runner.invoke(
            cli,
            ["--project", str(project), "trials", "search", "--json", "EGFR"],
            env={"OASE_NO_DIRTY_WARNING": "1"},
            catch_exceptions=False,
        )
        assert search_res.exit_code == 0, search_res.output
        artifact = _read(project, ".artifact.json")
        meta = _read(project, ".meta.json")
        analysis = _analyze(project, query="EGFR")

        assert artifact["summary"]["n_studies"] == 500
        assert (
            isinstance(artifact["summary"]["total_results"], int)
            and artifact["summary"]["total_results"] > 500
        ), artifact["summary"]
        assert COVERAGE_CODE in _codes(meta), meta
        assert COVERAGE_CODE in _codes(analysis), analysis

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))

        # 2. Uncapped query: single NCT identifier returns 1 study of 1 total.
        search_res = runner.invoke(
            cli,
            ["--project", str(project), "trials", "search", "--json", "NCT00001372"],
            env={"OASE_NO_DIRTY_WARNING": "1"},
            catch_exceptions=False,
        )
        assert search_res.exit_code == 0, search_res.output
        artifact = _read(project, ".artifact.json")
        meta = _read(project, ".meta.json")
        analysis = _analyze(project, query="NCT00001372")

        assert 0 < artifact["summary"]["n_studies"] < 500
        assert artifact["summary"]["total_results"] == artifact["summary"]["n_studies"]
        assert COVERAGE_CODE not in _codes(meta), meta
        assert COVERAGE_CODE not in _codes(analysis), analysis

    print("  PASS: live ClinicalTrials.gov v2 API integration test")


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
