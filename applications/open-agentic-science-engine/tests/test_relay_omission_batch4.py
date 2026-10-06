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

"""Regression tests for Silent Relay Omission fixes (Batch 4).

Covers:
  #250 — cellxgene.search_is_metadata_only fires with zero datasets
  #255 — differentiation assess emits relay data in JSON output mode
  #256 — disco.search_is_sample_metadata fires with zero samples
  #267 — scp.search_is_study_metadata fires with zero results
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(base: Path) -> Path:
    """Create a minimal dde project directory."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    return project


def _write_json(path: Path, data: Any) -> Path:
    """Write JSON to a file, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Issue #250 — cellxgene.search_is_metadata_only with zero datasets
# ---------------------------------------------------------------------------


def _make_cellxgene_artifact(
    project: Path,
    query: str = "BRCA1",
    *,
    collections: list[dict[str, Any]] | None = None,
) -> Path:
    """Write a minimal CELLxGENE search artifact with zero or more collections."""
    import re

    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", query).strip("-").lower()[:80]
    slug = slug or "cellxgene-search"

    artifact: dict[str, Any] = {
        "schema": "dde.cellxgene-search.v1",
        "query": {
            "text": query,
            "tissue": "",
            "cell_type": "",
            "organism": "",
            "disease": "",
        },
        "collections": collections if collections is not None else [],
    }

    sc_dir = project / "raw" / "single-cell"
    sc_dir.mkdir(parents=True, exist_ok=True)
    path = sc_dir / f"{slug}.cellxgene.artifact.json"
    path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return path


def test_cellxgene_relay_fires_with_zero_datasets() -> None:
    """#250: cellxgene.search_is_metadata_only MUST fire even when total
    datasets is zero — the relay is unconditional per tool-design-guidance §8.
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _make_cellxgene_artifact(project, "BRCA1", collections=[])

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "cellxgene", "analyze", "BRCA1"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        # Verify the relay appears in the analysis sidecar.
        sc_dir = project / "raw" / "single-cell"
        analysis_files = list(sc_dir.glob("*.cellxgene.analysis.json"))
        assert analysis_files, f"No cellxgene analysis file found in {sc_dir}"
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "cellxgene.search_is_metadata_only" in relay_codes, (
            f"cellxgene.search_is_metadata_only relay should fire even with "
            f"zero datasets; got relay codes: {relay_codes}"
        )

        # Also verify the relay is present in text output.
        assert "cellxgene.search_is_metadata_only" in result.output, (
            f"Relay should appear in CLI output; got:\n{result.output}"
        )
    print("  PASS: cellxgene relay fires with zero datasets")


def test_cellxgene_relay_still_fires_with_datasets() -> None:
    """Sanity check: relay also fires when datasets ARE present."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _make_cellxgene_artifact(
            project,
            "TP53",
            collections=[
                {
                    "collection_id": "col-1",
                    "name": "Test Collection",
                    "datasets": [
                        {
                            "dataset_id": "ds-1",
                            "cell_count": 100,
                            "tissues": ["brain"],
                            "cell_types": ["neuron"],
                            "organisms": ["Homo sapiens"],
                            "diseases": ["normal"],
                            "assays": ["10x 3' v3"],
                        }
                    ],
                }
            ],
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "cellxgene", "analyze", "TP53"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        sc_dir = project / "raw" / "single-cell"
        analysis_files = list(sc_dir.glob("*.cellxgene.analysis.json"))
        assert analysis_files
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))
        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "cellxgene.search_is_metadata_only" in relay_codes
    print("  PASS: cellxgene relay fires with datasets present (sanity)")


# ---------------------------------------------------------------------------
# Issue #255 — differentiation.not_legal_clearance relay in JSON output
# ---------------------------------------------------------------------------


def _make_patent_artifact(
    project: Path,
    query_term: str = "CDK4-inhibitor",
    *,
    patents: list[dict[str, Any]] | None = None,
) -> Path:
    """Write a minimal patent search artifact for differentiation assess."""
    import re

    slug = re.sub(r"[^a-z0-9._-]+", "-", query_term.lower()).strip("-")[:80] or "query"

    artifact: dict[str, Any] = {
        "schema": "dde.patent.v1",
        "query": {"text": query_term},
        "patents": patents if patents is not None else [],
    }

    ip_dir = project / "raw" / "ip"
    ip_dir.mkdir(parents=True, exist_ok=True)
    path = ip_dir / f"{slug}.patent-google-patents.artifact.json"
    path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return path


def test_differentiation_assess_emits_relay_data_in_json_mode() -> None:
    """#255: differentiation assess MUST include relays in the JSON payload
    via emit.data('relays', ...). Before the fix, relay data was only
    emitted via emit.line (text mode) and absent from JSON output.
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _make_patent_artifact(project, "CDK4-inhibitor", patents=[])

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "differentiation",
                "assess",
                "CDK4-inhibitor",
                "--json",
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        # Parse the JSON output to verify relays are included.
        # The output may contain trailing warnings mixed into stdout by
        # CliRunner, so we extract the first JSON object.
        raw = result.output.strip()
        brace_depth = 0
        json_end = 0
        for i, ch in enumerate(raw):
            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    json_end = i + 1
                    break
        output_data = json.loads(raw[:json_end])
        assert "relays" in output_data, (
            f"JSON output must include 'relays' key; got keys: "
            f"{list(output_data.keys())}"
        )

        relay_codes = [r["code"] for r in output_data["relays"]]
        assert "differentiation.not_legal_clearance" in relay_codes, (
            f"differentiation.not_legal_clearance relay must appear in "
            f"JSON output; got relay codes: {relay_codes}"
        )
    print("  PASS: differentiation assess emits relay data in JSON mode")


def test_differentiation_assess_relays_in_analysis_sidecar() -> None:
    """#255: relay data MUST also appear in the analysis sidecar's
    mandatory_relays field.
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _make_patent_artifact(project, "CDK4-inhibitor", patents=[])

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "differentiation",
                "assess",
                "CDK4-inhibitor",
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        ip_dir = project / "raw" / "ip"
        analysis_files = list(ip_dir.glob("*.differentiation.analysis.json"))
        assert analysis_files, f"No differentiation analysis found in {ip_dir}"
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "differentiation.not_legal_clearance" in relay_codes, (
            f"differentiation.not_legal_clearance relay must be in analysis "
            f"sidecar mandatory_relays; got codes: {relay_codes}"
        )
    print("  PASS: differentiation relays in analysis sidecar")


# ---------------------------------------------------------------------------
# Issue #256 — disco.search_is_sample_metadata with zero samples
# ---------------------------------------------------------------------------


def _make_disco_artifact(
    project: Path,
    query: str = "BRCA1",
    *,
    samples: list[dict[str, Any]] | None = None,
) -> Path:
    """Write a minimal DISCO search artifact with zero or more samples."""
    import re

    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", query).strip("-").lower()[:80]
    slug = slug or "disco-search"

    artifact: dict[str, Any] = {
        "schema": "dde.disco-search.v1",
        "query": {"text": query, "tissue": "", "disease": "", "species": ""},
        "samples": samples if samples is not None else [],
    }

    sc_dir = project / "raw" / "single-cell"
    sc_dir.mkdir(parents=True, exist_ok=True)
    path = sc_dir / f"{slug}.disco.artifact.json"
    path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return path


def test_disco_relay_fires_with_zero_samples() -> None:
    """#256: disco.search_is_sample_metadata MUST fire even when zero
    samples are returned — the relay is unconditional per §8.
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _make_disco_artifact(project, "BRCA1", samples=[])

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "disco", "analyze", "BRCA1"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        sc_dir = project / "raw" / "single-cell"
        analysis_files = list(sc_dir.glob("*.disco.analysis.json"))
        assert analysis_files, f"No disco analysis file found in {sc_dir}"
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "disco.search_is_sample_metadata" in relay_codes, (
            f"disco.search_is_sample_metadata relay should fire even with "
            f"zero samples; got relay codes: {relay_codes}"
        )

        assert "disco.search_is_sample_metadata" in result.output, (
            f"Relay should appear in CLI output; got:\n{result.output}"
        )
    print("  PASS: disco relay fires with zero samples")


def test_disco_relay_still_fires_with_samples() -> None:
    """Sanity check: relay also fires when samples ARE present."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _make_disco_artifact(
            project,
            "TP53",
            samples=[
                {
                    "sample_id": "s-1",
                    "tissue": "brain",
                    "disease": "normal",
                    "platform": "10x Genomics",
                    "project": "P1",
                    "cell_number": 500,
                }
            ],
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "disco", "analyze", "TP53"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        sc_dir = project / "raw" / "single-cell"
        analysis_files = list(sc_dir.glob("*.disco.analysis.json"))
        assert analysis_files
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))
        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "disco.search_is_sample_metadata" in relay_codes
    print("  PASS: disco relay fires with samples present (sanity)")


# ---------------------------------------------------------------------------
# Issue #267 — scp.search_is_study_metadata with zero results
# ---------------------------------------------------------------------------


def _make_scp_artifact(
    project: Path,
    query: str = "BRCA1",
    *,
    results: list[dict[str, Any]] | None = None,
    total_found: int = 0,
) -> Path:
    """Write a minimal SCP search artifact with zero or more results."""
    import re

    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", query).strip("-").lower()[:80]
    slug = slug or "scp-search"

    artifact: dict[str, Any] = {
        "schema": "dde.scp-search.v1",
        "query": {"text": query, "total_found": total_found},
        "results": results if results is not None else [],
    }

    sc_dir = project / "raw" / "single-cell"
    sc_dir.mkdir(parents=True, exist_ok=True)
    path = sc_dir / f"{slug}.scp.json"
    path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return path


def test_scp_relay_fires_with_zero_results() -> None:
    """#267: scp.search_is_study_metadata MUST fire even when zero results
    are returned — the relay is unconditional per §8.
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _make_scp_artifact(project, "BRCA1", results=[], total_found=0)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "scp", "analyze", "BRCA1"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}\n{result.output}"
        )

        sc_dir = project / "raw" / "single-cell"
        analysis_files = list(sc_dir.glob("*.scp.analysis.json"))
        assert analysis_files, f"No SCP analysis file found in {sc_dir}"
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "scp.search_is_study_metadata" in relay_codes, (
            f"scp.search_is_study_metadata relay should fire even with "
            f"zero results; got relay codes: {relay_codes}"
        )

        assert "scp.search_is_study_metadata" in result.output, (
            f"Relay should appear in CLI output; got:\n{result.output}"
        )
    print("  PASS: scp relay fires with zero results")


def test_scp_relay_still_fires_with_results() -> None:
    """Sanity check: relay also fires when results ARE present."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        _make_scp_artifact(
            project,
            "TP53",
            results=[
                {
                    "accession": "SCP123",
                    "name": "Test Study",
                    "cell_count": 1000,
                    "study_url": "https://example.com/SCP123",
                }
            ],
            total_found=1,
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "scp", "analyze", "TP53"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        sc_dir = project / "raw" / "single-cell"
        analysis_files = list(sc_dir.glob("*.scp.analysis.json"))
        assert analysis_files
        analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))
        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "scp.search_is_study_metadata" in relay_codes
    print("  PASS: scp relay fires with results present (sanity)")


# ---------------------------------------------------------------------------
# Runner (for manual execution outside pytest)
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        (
            "test_cellxgene_relay_fires_with_zero_datasets",
            test_cellxgene_relay_fires_with_zero_datasets,
        ),
        (
            "test_cellxgene_relay_still_fires_with_datasets",
            test_cellxgene_relay_still_fires_with_datasets,
        ),
        (
            "test_differentiation_assess_emits_relay_data_in_json_mode",
            test_differentiation_assess_emits_relay_data_in_json_mode,
        ),
        (
            "test_differentiation_assess_relays_in_analysis_sidecar",
            test_differentiation_assess_relays_in_analysis_sidecar,
        ),
        (
            "test_disco_relay_fires_with_zero_samples",
            test_disco_relay_fires_with_zero_samples,
        ),
        (
            "test_disco_relay_still_fires_with_samples",
            test_disco_relay_still_fires_with_samples,
        ),
        (
            "test_scp_relay_fires_with_zero_results",
            test_scp_relay_fires_with_zero_results,
        ),
        (
            "test_scp_relay_still_fires_with_results",
            test_scp_relay_still_fires_with_results,
        ),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:
            print(f"  FAIL: {name} -- {exc}")
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
