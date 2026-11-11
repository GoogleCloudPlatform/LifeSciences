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

"""Unit tests for the clinical trials search coverage relay (#245).

Covers:
  1. ``oase trials search`` requests ``countTotal=true`` on page 1 only and
     records ``total_results`` in the artifact summary and sidecar
  2. A capped set (500 retained of >500 matched) emits
     ``trials.result_set_capped`` in the sidecar
  3. A complete set (including exact 500 of 500) emits no coverage relay
  4. When upstream omits ``totalCount`` at the 500-study cap, ``total_results``
     remains ``None`` and ``trials.result_set_capped`` fires via the
     ``_MAX_STUDIES`` fallback
  5. ``oase trials analyze`` carries ``trials.result_set_capped`` forward,
     including on legacy artifacts written before ``total_results`` existed
  6. Malformed summary counts are guarded without raising
  7. ``trials.result_set_capped`` is registered in ``RELAY_CODES`` and
     attributed by ``oase relays``
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


class _Response:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None):
        self.status_code = status_code
        self.content = json.dumps(payload or {}).encode("utf-8")


def _page(
    start: int,
    n: int,
    total: int | None = None,
    next_token: str | None = None,
    phase: str = "PHASE1",
    status: str = "COMPLETED",
) -> _Response:
    """One page of ClinicalTrials.gov v2 study results."""
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
    return _Response(200, payload)


def _search(project: Path, pages: list[_Response], query: str = "EGFR"):
    """Run ``oase trials search`` against canned pages; return (result, urls)."""
    from click.testing import CliRunner
    from oase.cli import cli

    urls: list[str] = []
    remaining = list(pages)

    def fake_request(method: str, url: str, **kwargs: Any) -> _Response:
        urls.append(url)
        return remaining.pop(0) if remaining else _page(0, 0, 0)

    with mock.patch("oase.commands.trials.http.request", side_effect=fake_request):
        result = CliRunner().invoke(
            cli,
            ["--project", str(project), "trials", "search", query],
            catch_exceptions=False,
        )
    assert result.exit_code == 0, f"Exit {result.exit_code}\n{result.output}"
    return result, urls


def _read(project: Path, suffix: str) -> dict[str, Any]:
    paths = list((project / "raw" / "pipeline").glob(f"*{suffix}"))
    assert len(paths) == 1, f"expected one *{suffix}, found {paths}"
    return json.loads(paths[0].read_text(encoding="utf-8"))


def _codes(record: dict[str, Any]) -> set[str]:
    return {r["code"] for r in record.get("mandatory_relays", [])}


def _analyze(project: Path, query: str = "EGFR") -> dict[str, Any]:
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
# 1. Request parameters & total_results recording
# ---------------------------------------------------------------------------


def test_search_requests_count_total() -> None:
    """First page requests countTotal=true; follow-up pages omit it."""
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        pages = [
            _page(0, 100, total=150, next_token="tok1"),
            _page(100, 50, next_token=None),
        ]
        _, urls = _search(project, pages)
        artifact = _read(project, ".artifact.json")
        meta = _read(project, ".meta.json")

    assert len(urls) == 2, urls
    assert all("pageSize=100" in u for u in urls), urls
    assert "countTotal=true" in urls[0], urls[0]
    assert "pageToken=" not in urls[0], urls[0]
    assert "countTotal=" not in urls[1], urls[1]
    assert "pageToken=tok1" in urls[1], urls[1]
    assert artifact["summary"]["n_studies"] == 150
    assert artifact["summary"]["total_results"] == 150
    assert meta["n_studies"] == 150
    assert meta["total_results"] == 150
    print("  PASS: search requests countTotal=true on page 1 and records total_results")


# ---------------------------------------------------------------------------
# 2-3. Search sidecar coverage relay
# ---------------------------------------------------------------------------


def test_capped_set_emits_result_set_capped() -> None:
    """500 retained of 8080 matched emits trials.result_set_capped."""
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        pages = [
            _page(
                i * 100,
                100,
                total=8080 if i == 0 else None,
                next_token=f"tok{i + 1}",
            )
            for i in range(5)
        ]
        _, urls = _search(project, pages, query="EGFR")
        artifact = _read(project, ".artifact.json")
        meta = _read(project, ".meta.json")

    assert len(urls) == 5, urls
    assert artifact["summary"]["n_studies"] == 500
    assert artifact["summary"]["total_results"] == 8080
    assert COVERAGE_CODE in _codes(meta), meta
    relay_msg = next(
        r["message"] for r in meta["mandatory_relays"] if r["code"] == COVERAGE_CODE
    )
    assert "500 of 8080" in relay_msg, relay_msg
    print("  PASS: capped set emits trials.result_set_capped")


def test_complete_set_emits_no_coverage_relay() -> None:
    """A complete set emits no coverage relay (tool-design-guidance §5.1 rule 4)."""
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _, urls = _search(project, [_page(0, 12, total=12)])
        meta = _read(project, ".meta.json")

    assert len(urls) == 1, urls
    assert COVERAGE_CODE not in _codes(meta), meta
    print("  PASS: complete set emits no coverage relay")


def test_exact_500_total_is_not_capped() -> None:
    """When totalCount is exactly 500, retaining 500 is complete, not capped."""
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        pages = [
            _page(
                i * 100,
                100,
                total=500 if i == 0 else None,
                next_token=f"tok{i + 1}" if i < 4 else None,
            )
            for i in range(5)
        ]
        _, urls = _search(project, pages, query="EXACT500")
        artifact = _read(project, ".artifact.json")
        meta = _read(project, ".meta.json")

    assert len(urls) == 5, urls
    assert artifact["summary"]["n_studies"] == 500
    assert artifact["summary"]["total_results"] == 500
    assert COVERAGE_CODE not in _codes(meta), meta
    print("  PASS: exact 500 of 500 is not marked capped")


def test_missing_total_count_at_cap_falls_back_to_max_studies() -> None:
    """If upstream omits totalCount at 500 studies, total_results is None and cap fires."""
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        pages = [
            _page(
                i * 100,
                100,
                total=None,
                next_token=f"tok{i + 1}",
            )
            for i in range(5)
        ]
        _, urls = _search(project, pages, query="OMITTED_TOTAL")
        artifact = _read(project, ".artifact.json")
        meta = _read(project, ".meta.json")
        analysis = _analyze(project, query="OMITTED_TOTAL")

    assert len(urls) == 5, urls
    assert artifact["summary"]["n_studies"] == 500
    assert artifact["summary"]["total_results"] is None
    assert COVERAGE_CODE in _codes(meta), meta
    assert COVERAGE_CODE in _codes(analysis), analysis
    print("  PASS: omitted totalCount at 500 studies falls back to _MAX_STUDIES relay")


# ---------------------------------------------------------------------------
# 4. trials analyze offline propagation
# ---------------------------------------------------------------------------


def test_analyze_carries_capped_relay_forward() -> None:
    """trials analyze carries trials.result_set_capped into .analysis.json."""
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        pages = [
            _page(
                i * 100,
                100,
                total=8080 if i == 0 else None,
                next_token=f"tok{i + 1}",
                phase="PHASE1",
                status="COMPLETED",
            )
            for i in range(5)
        ]
        _search(project, pages, query="EGFR")
        analysis = _analyze(project, query="EGFR")

    assert analysis["assessment"]["verdict"] == "early_pipeline"
    assert COVERAGE_CODE in _codes(analysis), analysis
    relay_msg = next(
        r["message"] for r in analysis["mandatory_relays"] if r["code"] == COVERAGE_CODE
    )
    assert "500 of 8080" in relay_msg, relay_msg
    print("  PASS: trials analyze carries trials.result_set_capped forward")


def test_analyze_relays_cap_on_legacy_artifact_without_total() -> None:
    """A pre-#245 artifact with n_studies=500 and no total_results still relays."""
    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        pipe_dir = project / "raw" / "pipeline"
        pipe_dir.mkdir(parents=True)
        studies = [
            {
                "nct_id": f"NCT{i:08d}",
                "title": f"Study {i}",
                "status": "COMPLETED",
                "phases": ["PHASE1"],
                "phase_label": "Phase1",
                "conditions": ["Cancer"],
                "interventions": [{"type": "DRUG", "name": "Drug"}],
                "sponsor": "Sponsor",
                "enrollment": 20,
                "start_date": "2020-01-01",
                "completion_date": "2022-01-01",
            }
            for i in range(500)
        ]
        legacy = {
            "schema": "oase.clinical-trials.v1",
            "query": {
                "term": "LEGACY_TARGET",
                "search_by": "target",
                "source": "clinicaltrials.gov",
            },
            "summary": {
                "n_studies": 500,
                "by_phase": {"Phase1": 500},
                "by_status": {"COMPLETED": 500},
                "top_sponsors": ["Sponsor"],
            },
            "studies": studies,
        }
        (pipe_dir / "legacy_target.trials-clinicaltrials.artifact.json").write_text(
            json.dumps(legacy), encoding="utf-8"
        )
        analysis = _analyze(project, query="LEGACY_TARGET")

    assert COVERAGE_CODE in _codes(analysis), analysis
    print("  PASS: legacy 500-study artifact without total_results gets cap relay")


def test_analyze_no_cap_relay_on_small_legacy_artifact() -> None:
    """A pre-#245 artifact with n_studies < 500 and no total_results does not fire."""
    from oase.commands.trials import coverage_relays

    assert coverage_relays({"n_studies": 25}) == []
    print("  PASS: small legacy artifact without total_results emits no cap relay")


# ---------------------------------------------------------------------------
# 5-6. Malformed summary guard & relay registration
# ---------------------------------------------------------------------------


def test_malformed_summary_does_not_raise() -> None:
    from oase.commands.trials import capped_counts, coverage_relays

    for summary in (
        {"n_studies": "many", "total_results": 10},
        {"n_studies": 3, "total_results": "lots"},
        {"n_studies": "many"},
    ):
        assert capped_counts(summary) is None, summary
        assert coverage_relays(summary) == [], summary
    print("  PASS: malformed counts are guarded")


def test_code_registered_and_attributed() -> None:
    from oase.cli import _enumerate_emission_sites
    from oase.core.provenance import RELAY_CODES

    assert COVERAGE_CODE in RELAY_CODES
    sites = set(_enumerate_emission_sites().get(COVERAGE_CODE, []))
    assert {"trials fetch", "trials analyze"} <= sites, sites
    print("  PASS: trials.result_set_capped registered and attributed")


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
