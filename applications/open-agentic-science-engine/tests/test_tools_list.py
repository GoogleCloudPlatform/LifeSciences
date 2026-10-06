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

"""Tests for `dde tools list` (#89).

Covers:
  - The command runs without error
  - Known command groups (genetics, pubchem, cellxgene, disco) appear
  - --json output is valid JSON with the expected schema
  - --quiet output lists names only
  - --domain filter narrows results correctly
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from click.testing import CliRunner

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.cli import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke(*args: str) -> object:
    """Run the CLI and return the CliRunner result."""
    runner = CliRunner()
    return runner.invoke(cli, ["tools", "list", *args])


# ---------------------------------------------------------------------------
# Basic smoke test
# ---------------------------------------------------------------------------


def test_tools_list_runs_without_error():
    result = _invoke()
    assert result.exit_code == 0, result.output


def test_tools_list_shows_known_groups():
    """genetics, pubchem, cellxgene, and disco must appear."""
    result = _invoke()
    assert result.exit_code == 0, result.output
    for name in ("genetics", "pubchem", "cellxgene", "disco"):
        assert name in result.output, f"{name!r} missing from tools list output"


def test_tools_list_excludes_itself():
    """The 'tools' group itself should not be listed."""
    result = _invoke("--quiet")
    assert result.exit_code == 0, result.output
    names = result.output.strip().splitlines()
    assert "tools" not in names


# ---------------------------------------------------------------------------
# --json
# ---------------------------------------------------------------------------


def test_json_output_is_valid():
    result = _invoke("--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) > 0


def test_json_records_have_expected_fields():
    result = _invoke("--json")
    data = json.loads(result.output)
    for record in data:
        assert "name" in record
        assert "purpose" in record
        assert "subcommands" in record
        assert "artifact_class" in record
        assert isinstance(record["subcommands"], list)


def test_json_contains_known_groups():
    result = _invoke("--json")
    data = json.loads(result.output)
    names = {r["name"] for r in data}
    for expected in ("genetics", "pubchem", "cellxgene", "disco"):
        assert expected in names, f"{expected!r} missing from JSON output"


def test_json_artifact_class_populated():
    """At least some groups should have a non-null artifact_class."""
    result = _invoke("--json")
    data = json.loads(result.output)
    classes = [r["artifact_class"] for r in data if r["artifact_class"]]
    assert len(classes) > 0, "No artifact_class values found in any record"


# ---------------------------------------------------------------------------
# --quiet
# ---------------------------------------------------------------------------


def test_quiet_output_names_only():
    result = _invoke("--quiet")
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    # Each line should be a single word (group name), no whitespace columns.
    for line in lines:
        assert " " not in line.strip(), (
            f"Unexpected whitespace in quiet output: {line!r}"
        )
    assert len(lines) > 10, "Expected at least 10 tool groups"


# ---------------------------------------------------------------------------
# --domain filter
# ---------------------------------------------------------------------------


def test_domain_filter_matches():
    """--domain 'single-cell' should include cellxgene and disco."""
    result = _invoke("--json", "--domain", "single-cell")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    names = {r["name"] for r in data}
    assert "cellxgene" in names, "cellxgene should match --domain single-cell"
    assert "disco" in names, "disco should match --domain single-cell"


def test_domain_filter_excludes_non_matching():
    """--domain 'single-cell' should NOT include genetics or pubchem."""
    result = _invoke("--json", "--domain", "single-cell")
    data = json.loads(result.output)
    names = {r["name"] for r in data}
    assert "genetics" not in names, "genetics should not match --domain single-cell"
    assert "pubchem" not in names, "pubchem should not match --domain single-cell"


def test_domain_filter_no_matches():
    """A domain with no matches should produce an empty list."""
    result = _invoke("--json", "--domain", "xyzzy-nonexistent-domain")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data == []


def test_domain_filter_case_insensitive():
    """--domain matching should be case-insensitive."""
    result_lower = _invoke("--json", "--domain", "single-cell")
    result_upper = _invoke("--json", "--domain", "SINGLE-CELL")
    data_lower = json.loads(result_lower.output)
    data_upper = json.loads(result_upper.output)
    assert {r["name"] for r in data_lower} == {r["name"] for r in data_upper}
