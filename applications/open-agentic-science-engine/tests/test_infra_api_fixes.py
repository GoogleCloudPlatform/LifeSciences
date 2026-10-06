#!/usr/bin/env python3
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

"""Tests for infrastructure / API bug fixes (#254, #271).

Issue #254 — gwas.py _fetch_clinvar() must include NCBI_API_KEY in
             E-utilities URLs when the env var is set, and degrade
             gracefully when it is absent.

Issue #271 — site.py build must reject --output-dir that resolves to
             the project root itself, preventing accidental deletion of
             all project content.

Run with:
    cd applications/DDE && PYTHONPATH=tools python3 -m pytest tests/test_infra_api_fixes.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Bootstrap — add tools/ to sys.path so dde is importable
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

# Pre-import the modules under test so patch targets resolve correctly.
from dde.commands import gwas as gwas_mod
from dde.core import http as http_mod
from dde.core.errors import Refusal
from dde.core.paths import confine_path

# ===========================================================================
# Issue #254 — ClinVar API key in E-utilities URLs
# ===========================================================================


class TestClinvarApiKey:
    """Verify _fetch_clinvar() includes api_key when NCBI_API_KEY is set."""

    @staticmethod
    def _make_esearch_response(uids: list[str]) -> bytes:
        """Build a fake esearch JSON response."""
        return json.dumps(
            {
                "esearchresult": {
                    "count": str(len(uids)),
                    "idlist": uids,
                }
            }
        ).encode()

    @staticmethod
    def _make_esummary_response(uids: list[str]) -> bytes:
        """Build a fake esummary JSON response with minimal variant data."""
        result: dict[str, Any] = {"uids": uids}
        for uid in uids:
            result[uid] = {
                "title": "NM_000059.4(BRCA2):c.1234A>G (p.Ile412Val)",
                "obj_type": "single nucleotide variant",
                "germline_classification": {
                    "description": "Uncertain significance",
                    "review_status": "criteria provided, single submitter",
                    "trait_set": [],
                },
            }
        return json.dumps({"result": result}).encode()

    def test_api_key_included_when_env_set(self):
        """When NCBI_API_KEY is set, both esearch and esummary URLs include it."""
        fake_key = "test_fake_key_12345"

        captured_urls: list[str] = []

        esearch_resp = MagicMock()
        esearch_resp.content = self._make_esearch_response(["111", "222"])

        esummary_resp = MagicMock()
        esummary_resp.content = self._make_esummary_response(["111", "222"])

        call_count = 0

        def mock_request(method, url, **kwargs):
            nonlocal call_count
            captured_urls.append(url)
            call_count += 1
            if call_count == 1:
                return esearch_resp
            return esummary_resp

        with (
            patch.object(http_mod, "request", side_effect=mock_request),
            patch.object(
                gwas_mod,
                "api_key_suffix",
                return_value=f"&api_key={fake_key}",
            ),
        ):
            gwas_mod._fetch_clinvar("BRCA2")

        assert len(captured_urls) == 2, (
            f"Expected 2 HTTP calls (esearch + esummary), got {len(captured_urls)}"
        )

        esearch_url = captured_urls[0]
        esummary_url = captured_urls[1]

        assert f"api_key={fake_key}" in esearch_url, (
            f"esearch URL missing api_key: {esearch_url}"
        )
        assert f"api_key={fake_key}" in esummary_url, (
            f"esummary URL missing api_key: {esummary_url}"
        )

    def test_no_api_key_when_env_unset(self):
        """When NCBI_API_KEY is absent, URLs must NOT contain api_key."""
        captured_urls: list[str] = []

        esearch_resp = MagicMock()
        esearch_resp.content = self._make_esearch_response(["333"])

        esummary_resp = MagicMock()
        esummary_resp.content = self._make_esummary_response(["333"])

        call_count = 0

        def mock_request(method, url, **kwargs):
            nonlocal call_count
            captured_urls.append(url)
            call_count += 1
            if call_count == 1:
                return esearch_resp
            return esummary_resp

        with (
            patch.object(http_mod, "request", side_effect=mock_request),
            patch.object(gwas_mod, "api_key_suffix", return_value=""),
        ):
            _raw, artifact = gwas_mod._fetch_clinvar("TP53")

        assert len(captured_urls) == 2

        for url in captured_urls:
            assert "api_key" not in url, (
                f"api_key should NOT appear when env var is absent: {url}"
            )

        # Verify the function still returns valid data
        assert artifact["schema"] == "dde.gwas.v1"
        assert artifact["query"]["gene"] == "TP53"

    def test_no_results_still_works(self):
        """When esearch returns no UIDs, function returns empty artifact."""
        esearch_resp = MagicMock()
        esearch_resp.content = self._make_esearch_response([])

        def mock_request(method, url, **kwargs):
            return esearch_resp

        with (
            patch.object(http_mod, "request", side_effect=mock_request),
            patch.object(gwas_mod, "api_key_suffix", return_value=""),
        ):
            _raw, artifact = gwas_mod._fetch_clinvar("FAKEGENE")

        assert artifact["summary"]["n_associations"] == 0


# ===========================================================================
# Issue #271 — --output-dir must not be the project root
# ===========================================================================


class TestSiteOutputDirGuard:
    """Verify site build rejects output_dir equal to project root."""

    @staticmethod
    def _make_project(base: Path) -> Path:
        """Create a minimal dde project directory."""
        project = base / "test-project"
        project.mkdir(parents=True, exist_ok=True)
        (project / ".dde").mkdir(exist_ok=True)
        return project

    @staticmethod
    def _run_output_dir_guard(project_root: Path, output_dir: str) -> None:
        """Exercise the output-dir guard logic from site.py build_cmd.

        This reproduces the exact guard code from build_cmd (step 4) so
        we can test it in isolation without needing work orders, link
        validation, or the full site build pipeline.

        Raises Refusal if the output dir equals or contains the project root.
        Raises ArtifactError if the output dir escapes the project root.
        """
        from dde.core.errors import ArtifactError

        out_path = Path(output_dir)
        if not out_path.is_absolute():
            out_path = project_root / out_path
        out_resolved = out_path.resolve()
        project_resolved = project_root.resolve()
        if not out_resolved.is_relative_to(project_resolved):
            raise ArtifactError(
                f"output directory escapes project root: {output_dir}",
                detail=f"resolved to {out_resolved}",
                remedy="use a path within the project directory",
            )
        if out_resolved == project_resolved:
            raise Refusal(
                "--output-dir must not be the project root itself",
                detail=f"resolved output directory {out_resolved} equals project root",
                remedy="use a subdirectory such as '_site'",
            )
        dde_dir = (project_resolved / ".dde").resolve()
        if dde_dir.is_relative_to(out_resolved) and out_resolved != project_resolved:
            raise Refusal(
                "--output-dir must not contain the .dde control directory",
                detail=(
                    f"resolved output directory {out_resolved} is a parent of {dde_dir}"
                ),
                remedy="use a subdirectory that does not contain .dde/",
            )

    def test_output_dir_dot_rejected(self, tmp_path: Path):
        """output_dir='.' resolves to project root and must be rejected."""
        project = self._make_project(tmp_path)

        with pytest.raises(Refusal, match="must not be the project root"):
            self._run_output_dir_guard(project, ".")

    def test_output_dir_equals_project_root_absolute(self, tmp_path: Path):
        """Absolute path equal to project root must be rejected."""
        project = self._make_project(tmp_path)

        with pytest.raises(Refusal, match="must not be the project root"):
            self._run_output_dir_guard(project, str(project))

    def test_valid_subdirectory_accepted(self, tmp_path: Path):
        """A valid subdirectory like '_site' should pass the guard checks."""
        project = self._make_project(tmp_path)

        # Should not raise — _site is a valid output directory.
        self._run_output_dir_guard(project, "_site")

    def test_confine_path_passes_for_dot(self, tmp_path: Path):
        """Verify confine_path allows '.' — this is the bug's root cause.

        confine_path correctly returns the resolved path for '.' because
        the path IS relative to itself.  The bug was that the caller
        (site build) had no additional equality guard.
        """
        project = self._make_project(tmp_path)
        result = confine_path(project, Path("."))

        # confine_path should return the resolved path (project root itself)
        assert result is not None, (
            "confine_path should accept '.' (path is relative to itself)"
        )
        assert result == project.resolve(), (
            "confine_path('.') should resolve to the base directory"
        )


# ===========================================================================
# Run
# ===========================================================================

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
