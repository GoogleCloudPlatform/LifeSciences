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

"""Tests for dde cbioportal search and analyze.

Asserts:
1. Query returning results — correct schema and count.
2. Zero results — fires ``cbioportal.no_results``.
3. Relay guard: ``cbioportal.no_results`` does NOT fire when results exist.
4. Registration: relay codes and threshold set.
5. Analyze end-to-end.
6. Phase-two guard on analyze.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Patch optional dependencies before importing the module under test.
# Keep the patch active for the entire module so @patch decorators work.
# Note: click is NOT mocked — CliRunner tests need the real click package.
_mock_requests = MagicMock()
_mock_yaml = MagicMock()
_module_patches = patch.dict(
    "sys.modules",
    {"requests": _mock_requests, "yaml": _mock_yaml},
)
_module_patches.start()

from dde.commands import cbioportal as cbioportal_mod  # noqa: E402
from dde.commands.cbioportal import (  # noqa: E402
    ARTIFACT_CLASS,
    TOOL,
    _fetch_studies,
    _slugify,
)
from dde.core.provenance import RELAY_CODES  # noqa: E402
from dde.core.thresholds import _DEFAULTS  # noqa: E402

# Sample cBioPortal study records for mocking
_SAMPLE_STUDIES = [
    {
        "studyId": "brca_tcga_pan_can_atlas_2018",
        "name": "Breast Invasive Carcinoma (TCGA, PanCancer Atlas)",
        "description": "Breast cancer study from TCGA PanCancer Atlas",
        "cancerTypeId": "brca",
        "referenceGenome": "hg19",
        "allSampleCount": 1084,
        "citation": "TCGA PanCancer Atlas 2018",
    },
    {
        "studyId": "luad_tcga",
        "name": "Lung Adenocarcinoma (TCGA, Nature 2014)",
        "description": "Comprehensive molecular profiling of lung adenocarcinoma",
        "cancerTypeId": "luad",
        "referenceGenome": "hg38",
        "allSampleCount": 517,
        "citation": "TCGA, Nature 2014",
    },
    {
        "studyId": "brca_metabric",
        "name": "Breast Cancer (METABRIC, Nature 2012 & Nat Commun 2016)",
        "description": "Breast cancer genomics from the METABRIC study",
        "cancerTypeId": "brca",
        "referenceGenome": "hg19",
        "allSampleCount": 2509,
        "citation": "Curtis et al. Nature 2012",
    },
]


class TestSlugify(unittest.TestCase):
    """_slugify produces filesystem-safe slugs."""

    def test_simple_query(self):
        self.assertEqual(_slugify("breast cancer"), "breast-cancer")

    def test_truncation(self):
        long_query = "a" * 100
        self.assertLessEqual(len(_slugify(long_query)), 80)

    def test_empty_query(self):
        self.assertEqual(_slugify(""), "cbioportal-search")


class TestFetchStudies(unittest.TestCase):
    """_fetch_studies filters and structures API responses correctly."""

    def test_query_returning_results(self):
        """Query returning results — correct schema and count."""
        with patch.object(
            cbioportal_mod.http, "get_json", return_value=list(_SAMPLE_STUDIES)
        ):
            _raw, artifact, truncated = _fetch_studies("breast", None, None, 25)

        self.assertEqual(artifact["schema"], "dde.cbioportal-search.v1")
        self.assertEqual(artifact["query"], "breast")
        # Two breast-related studies should match
        self.assertEqual(len(artifact["results"]), 2)
        self.assertFalse(truncated)
        # Verify structure of a result
        result = artifact["results"][0]
        self.assertIn("study_id", result)
        self.assertIn("name", result)
        self.assertIn("cancer_type", result)
        self.assertIn("sample_count", result)
        self.assertEqual(result["source"], "cbioportal")

    def test_zero_results(self):
        """Zero results — produces empty results list."""
        with patch.object(
            cbioportal_mod.http, "get_json", return_value=list(_SAMPLE_STUDIES)
        ):
            _raw, artifact, _truncated = _fetch_studies(
                "nonexistent-query-xyz",
                None,
                None,
                25,
            )

        self.assertEqual(len(artifact["results"]), 0)
        self.assertEqual(artifact["total_results"], 0)

    def test_cancer_type_filter(self):
        """Cancer type filter narrows results."""
        with patch.object(
            cbioportal_mod.http, "get_json", return_value=list(_SAMPLE_STUDIES)
        ):
            _raw, artifact, _truncated = _fetch_studies(
                "cancer",
                "brca",
                None,
                25,
            )

        for r in artifact["results"]:
            self.assertEqual(r["cancer_type"], "brca")

    def test_truncation(self):
        """Results are capped at max_results."""
        with patch.object(
            cbioportal_mod.http, "get_json", return_value=list(_SAMPLE_STUDIES)
        ):
            _raw, artifact, truncated = _fetch_studies("cancer", None, None, 1)

        self.assertEqual(len(artifact["results"]), 1)
        self.assertTrue(truncated)


class TestRelayCodeRegistration(unittest.TestCase):
    """Relay codes and threshold set are registered."""

    def test_relay_codes_registered(self):
        self.assertIn("cbioportal.no_results", RELAY_CODES)
        self.assertIn("cbioportal.query_truncated", RELAY_CODES)

    def test_threshold_set_registered(self):
        self.assertIn("cbioportal-search", _DEFAULTS)
        ts = _DEFAULTS["cbioportal-search"]
        self.assertEqual(ts.version, "1.0")
        self.assertIn("max_results_default", ts.values)
        self.assertEqual(ts.values["max_results_default"], 25)


class TestRelayGuards(unittest.TestCase):
    """Relay codes fire conditionally in search_cmd via CliRunner."""

    def _run_search(self, query, mock_studies, max_results=25):
        """Run search_cmd through CliRunner with mocked HTTP, return sidecar."""
        import tempfile

        from click.testing import CliRunner

        from dde.cli import cli

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "test-project"
            project.mkdir()
            (project / ".dde").mkdir()
            (project / "raw" / "expression").mkdir(parents=True)

            with patch.object(
                cbioportal_mod.http,
                "get_json",
                return_value=mock_studies,
            ):
                args = [
                    "--project",
                    str(project),
                    "cbioportal",
                    "search",
                    query,
                    "--max-results",
                    str(max_results),
                ]
                result = runner.invoke(cli, args)

            self.assertEqual(result.exit_code, 0, f"search failed: {result.output}")

            # Find and parse the sidecar
            meta_files = list((project / "raw" / "expression").glob("*.meta.json"))
            self.assertEqual(len(meta_files), 1, "expected exactly one sidecar")
            sidecar = json.loads(meta_files[0].read_text(encoding="utf-8"))
            return sidecar

    def _relay_codes(self, sidecar):
        """Extract relay codes from a sidecar dict."""
        relays = sidecar.get("mandatory_relays", [])
        return [r["code"] for r in relays if "code" in r]

    def test_no_results_relay_fires_on_empty(self):
        """cbioportal.no_results fires in sidecar when search returns zero results."""
        sidecar = self._run_search("nonexistent-xyz-query", [])
        codes = self._relay_codes(sidecar)
        self.assertIn("cbioportal.no_results", codes)

    def test_no_results_relay_does_not_fire_when_results_exist(self):
        """cbioportal.no_results does NOT fire when results exist."""
        sidecar = self._run_search("breast", list(_SAMPLE_STUDIES))
        codes = self._relay_codes(sidecar)
        self.assertNotIn("cbioportal.no_results", codes)

    def test_query_truncated_relay_fires(self):
        """cbioportal.query_truncated fires when results exceed max_results."""
        # All 3 studies match "cancer" — set max_results=1 to trigger truncation
        sidecar = self._run_search("cancer", list(_SAMPLE_STUDIES), max_results=1)
        codes = self._relay_codes(sidecar)
        self.assertIn("cbioportal.query_truncated", codes)


class TestArtifactClass(unittest.TestCase):
    """ARTIFACT_CLASS is correct."""

    def test_artifact_class_is_expression(self):
        self.assertEqual(ARTIFACT_CLASS, "expression")

    def test_tool_name(self):
        self.assertEqual(TOOL, "cbioportal")


class TestAnalyzeEndToEnd(unittest.TestCase):
    """Analyze subcommand via CliRunner — search then analyze end-to-end."""

    def _run_search_then_analyze(self, query, mock_studies, max_results=25):
        """Run search_cmd + analyze_cmd through CliRunner, return analysis dict."""
        import tempfile

        from click.testing import CliRunner

        from dde.cli import cli

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "test-project"
            project.mkdir()
            (project / ".dde").mkdir()
            expr_dir = project / "raw" / "expression"
            expr_dir.mkdir(parents=True)

            # Step 1: run search
            with patch.object(
                cbioportal_mod.http,
                "get_json",
                return_value=mock_studies,
            ):
                search_result = runner.invoke(
                    cli,
                    [
                        "--project",
                        str(project),
                        "cbioportal",
                        "search",
                        query,
                        "--max-results",
                        str(max_results),
                    ],
                )
            self.assertEqual(
                search_result.exit_code,
                0,
                f"search failed: {search_result.output}",
            )

            # Locate the search artifact written by search_cmd
            search_artifacts = list(expr_dir.glob("*.cbioportal-search.json"))
            self.assertEqual(
                len(search_artifacts),
                1,
                f"expected 1 search artifact, got {search_artifacts}",
            )
            artifact_path = search_artifacts[0]

            # Step 2: run analyze on the artifact
            analyze_result = runner.invoke(
                cli,
                [
                    "--project",
                    str(project),
                    "cbioportal",
                    "analyze",
                    str(artifact_path),
                ],
            )
            self.assertEqual(
                analyze_result.exit_code,
                0,
                f"analyze failed: {analyze_result.output}",
            )

            # Read the analysis JSON
            analysis_files = list(expr_dir.glob("*.analysis.json"))
            self.assertEqual(
                len(analysis_files),
                1,
                f"expected 1 analysis file, got {analysis_files}",
            )
            analysis = json.loads(analysis_files[0].read_text(encoding="utf-8"))
            return analysis

    def test_analyze_produces_analysis(self):
        """Search → analyze pipeline: correct metrics, assessment, and outcome."""
        analysis = self._run_search_then_analyze("breast", list(_SAMPLE_STUDIES))

        self.assertEqual(analysis["threshold_set"], "cbioportal-search")
        assessment = analysis["assessment"]
        self.assertEqual(assessment["outcome"], "results-found")
        self.assertEqual(assessment["n_results"], 2)
        # Two breast studies: total samples = 1084 + 2509 = 3593
        self.assertEqual(assessment["total_samples"], 3593)
        self.assertIn("brca", assessment["cancer_types"])
        self.assertEqual(assessment["cancer_types"]["brca"], 2)

        metrics = analysis["metrics"]
        self.assertEqual(metrics["n_results"], 2)
        self.assertEqual(metrics["total_samples"], 3593)
        self.assertEqual(metrics["n_cancer_types"], 1)

    def test_analyze_no_results_verdict(self):
        """Search → analyze pipeline with no results produces no-results verdict."""
        analysis = self._run_search_then_analyze("nonexistent-xyz-query", [])

        assessment = analysis["assessment"]
        self.assertEqual(assessment["outcome"], "no-results")
        self.assertEqual(assessment["n_results"], 0)
        self.assertEqual(assessment["total_samples"], 0)

        metrics = analysis["metrics"]
        self.assertEqual(metrics["n_results"], 0)
        self.assertEqual(metrics["total_samples"], 0)
        self.assertEqual(metrics["n_cancer_types"], 0)


class TestPhaseTwoGuard(unittest.TestCase):
    """Phase-two guard is applied to analyze by enforce_phase_two."""

    def test_analyze_cmd_is_phase_two_guarded(self):
        """enforce_phase_two wraps the analyze subcommand."""
        # Import the CLI to trigger enforce_phase_two
        from dde.cli import cli

        # Walk the cli tree to find cbioportal -> analyze
        cbioportal_group = cli.commands.get("cbioportal")
        self.assertIsNotNone(cbioportal_group, "cbioportal not registered in CLI")
        analyze = cbioportal_group.commands.get("analyze")
        self.assertIsNotNone(analyze, "analyze not registered under cbioportal")
        # enforce_phase_two marks the callback
        self.assertTrue(
            getattr(analyze.callback, "_phase_two_guarded", False),
            "analyze callback is not phase-two guarded",
        )


class TestSchemaAndStructure(unittest.TestCase):
    """Search artifact has the correct schema structure."""

    def test_artifact_schema_version(self):
        with patch.object(
            cbioportal_mod.http, "get_json", return_value=list(_SAMPLE_STUDIES)
        ):
            _raw, artifact, _truncated = _fetch_studies("breast", None, None, 25)

        self.assertEqual(artifact["schema"], "dde.cbioportal-search.v1")
        self.assertIn("query", artifact)
        self.assertIn("searched_at", artifact)
        self.assertIn("total_results", artifact)
        self.assertIn("results", artifact)
        self.assertIsInstance(artifact["results"], list)

    def test_result_record_fields(self):
        with patch.object(
            cbioportal_mod.http, "get_json", return_value=list(_SAMPLE_STUDIES)
        ):
            _raw, artifact, _truncated = _fetch_studies("breast", None, None, 25)

        for result in artifact["results"]:
            self.assertIn("study_id", result)
            self.assertIn("name", result)
            self.assertIn("description", result)
            self.assertIn("cancer_type", result)
            self.assertIn("reference_genome", result)
            self.assertIn("sample_count", result)
            self.assertIn("citation", result)
            self.assertIn("source", result)
            self.assertEqual(result["source"], "cbioportal")


if __name__ == "__main__":
    unittest.main()
