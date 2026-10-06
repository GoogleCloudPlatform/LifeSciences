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

"""Tests for dde genetics analyze — indeterminate verdict on missing pLI/LOEUF.

Issue #84: gnomAD lacks pLI/oe_lof_upper for some genes (no_exp_lof flag —
gene too short or poorly covered).  The analyze command must return an
indeterminate verdict with reason ``constraint_not_estimable`` rather than
raising SchemaError.

Asserts:
1. Missing pLI + LOEUF → verdict "indeterminate", no SchemaError.
2. The ``gnomad.constraint_not_estimable`` relay fires.
3. Available constraint metrics (missense o/e, syn o/e, etc.) are present.
4. The relay code is registered in RELAY_CODES.
5. Normal (non-None) pLI/LOEUF still produce the expected verdicts.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Patch optional dependencies before importing the module under test.
_mock_requests = MagicMock()
_mock_yaml = MagicMock()
_module_patches = patch.dict(
    "sys.modules",
    {"requests": _mock_requests, "yaml": _mock_yaml},
)
_module_patches.start()

import unittest  # noqa: E402

from dde.core.provenance import RELAY_CODES  # noqa: E402


def _make_gnomad_payload(
    symbol: str = "MRGPRX2",
    gene_id: str = "ENSG00000183695",
    pli: float | None = None,
    oe_lof_upper: float | None = None,
    oe_mis: float | None = 0.89,
    oe_syn: float | None = 1.02,
    exp_lof: float | None = 0.3,
    obs_lof: int | None = 0,
    flags: list[str] | None = None,
) -> dict:
    """Build a minimal gnomAD JSON payload for testing."""
    constraint: dict = {
        "pLI": pli,
        "oe_lof": None,
        "oe_lof_lower": None,
        "oe_lof_upper": oe_lof_upper,
        "oe_lof_percentile": None,
        "oe_mis": oe_mis,
        "oe_mis_lower": None,
        "oe_mis_upper": None,
        "oe_syn": oe_syn,
        "exp_lof": exp_lof,
        "obs_lof": obs_lof,
        "exp_mis": 120.5,
        "obs_mis": 107,
        "exp_syn": 55.2,
        "obs_syn": 56,
        "lof_z": None,
        "mis_z": 0.8,
        "syn_z": -0.1,
        "flags": flags or ["no_exp_lof"],
    }
    return {
        "data": {
            "gene": {
                "gene_id": gene_id,
                "gene_version": "14",
                "symbol": symbol,
                "name": "MAS Related GPR Family Member X2",
                "chrom": "11",
                "start": 18962955,
                "stop": 18967605,
                "gnomad_constraint": constraint,
            }
        }
    }


def _write_constraint_json(directory: Path, symbol: str, payload: dict) -> Path:
    """Write a gnomAD constraint JSON file for ``analyze`` to read."""
    path = directory / f"{symbol}.gnomad-constraint.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _run_analyze(symbol: str, payload: dict) -> tuple[int, str, dict | None]:
    """Run ``dde genetics analyze`` via CliRunner, return (exit_code, output, analysis_dict)."""
    from click.testing import CliRunner

    from dde.cli import cli

    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        project = Path(td) / "test-project"
        project.mkdir()
        (project / ".dde").mkdir()
        genomics_dir = project / "raw" / "genomics"
        genomics_dir.mkdir(parents=True)

        _write_constraint_json(genomics_dir, symbol.upper(), payload)

        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "genetics",
                "analyze",
                symbol,
            ],
        )

        analysis_dict = None
        analysis_files = list(genomics_dir.glob("*.analysis.json"))
        if analysis_files:
            analysis_dict = json.loads(analysis_files[0].read_text(encoding="utf-8"))

        return result.exit_code, result.output, analysis_dict


class TestRelayCodeRegistration(unittest.TestCase):
    """gnomad.constraint_not_estimable is registered in RELAY_CODES."""

    def test_relay_code_registered(self):
        self.assertIn("gnomad.constraint_not_estimable", RELAY_CODES)

    def test_relay_code_prose_mentions_lof(self):
        prose = RELAY_CODES["gnomad.constraint_not_estimable"]
        self.assertIn("loss-of-function", prose.lower())


class TestIndeterminateVerdict(unittest.TestCase):
    """analyze returns indeterminate when pLI and LOEUF are both None."""

    def test_no_schema_error(self):
        """SchemaError must NOT be raised — exit 0 instead."""
        payload = _make_gnomad_payload(pli=None, oe_lof_upper=None)
        exit_code, output, _analysis = _run_analyze("MRGPRX2", payload)
        self.assertEqual(exit_code, 0, f"expected exit 0, got {exit_code}:\n{output}")

    def test_verdict_is_indeterminate(self):
        payload = _make_gnomad_payload(pli=None, oe_lof_upper=None)
        _, _, analysis = _run_analyze("MRGPRX2", payload)
        self.assertIsNotNone(analysis, "no analysis file written")
        self.assertEqual(analysis["assessment"]["verdict"], "indeterminate")

    def test_reason_is_constraint_not_estimable(self):
        payload = _make_gnomad_payload(pli=None, oe_lof_upper=None)
        _, _, analysis = _run_analyze("MRGPRX2", payload)
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis["assessment"]["reason"], "constraint_not_estimable")

    def test_relay_fires(self):
        """gnomad.constraint_not_estimable relay must be present."""
        payload = _make_gnomad_payload(pli=None, oe_lof_upper=None)
        _, _, analysis = _run_analyze("MRGPRX2", payload)
        self.assertIsNotNone(analysis)
        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        self.assertIn("gnomad.constraint_not_estimable", relay_codes)

    def test_available_metrics_present(self):
        """Constraint metrics that ARE available should still appear."""
        payload = _make_gnomad_payload(
            pli=None,
            oe_lof_upper=None,
            oe_mis=0.89,
            oe_syn=1.02,
            exp_lof=0.3,
            obs_lof=0,
        )
        _, _, analysis = _run_analyze("MRGPRX2", payload)
        self.assertIsNotNone(analysis)
        metrics = analysis["metrics"]
        self.assertIsNone(metrics["pLI"])
        self.assertIsNone(metrics["loeuf"])
        self.assertEqual(metrics["oe_mis"], 0.89)
        self.assertEqual(metrics["exp_lof"], 0.3)
        self.assertEqual(metrics["obs_lof"], 0)
        self.assertEqual(metrics["flags"], ["no_exp_lof"])

    def test_thresholds_still_recorded(self):
        """Applied thresholds must appear even when they could not be evaluated."""
        payload = _make_gnomad_payload(pli=None, oe_lof_upper=None)
        _, _, analysis = _run_analyze("MRGPRX2", payload)
        self.assertIsNotNone(analysis)
        self.assertTrue(
            analysis["threshold_set"].startswith("gnomad-constraint"),
            f"expected threshold_set starting with 'gnomad-constraint', "
            f"got {analysis['threshold_set']!r}",
        )
        self.assertIn("lof_intolerant_pli", analysis["thresholds_applied"])
        self.assertIn("loeuf_constrained", analysis["thresholds_applied"])


class TestIndeterminatePartialMissing(unittest.TestCase):
    """A record with only one of pLI/LOEUF missing also gets indeterminate."""

    def test_pli_none_loeuf_present(self):
        payload = _make_gnomad_payload(pli=None, oe_lof_upper=0.3)
        exit_code, output, analysis = _run_analyze("TESTGENE", payload)
        self.assertEqual(exit_code, 0, f"expected exit 0:\n{output}")
        self.assertEqual(analysis["assessment"]["verdict"], "indeterminate")

    def test_pli_present_loeuf_none(self):
        payload = _make_gnomad_payload(pli=0.95, oe_lof_upper=None)
        exit_code, output, analysis = _run_analyze("TESTGENE", payload)
        self.assertEqual(exit_code, 0, f"expected exit 0:\n{output}")
        self.assertEqual(analysis["assessment"]["verdict"], "indeterminate")


class TestNormalVerdictUnchanged(unittest.TestCase):
    """Normal (non-None) pLI/LOEUF still produce the expected verdicts."""

    def test_intolerant(self):
        payload = _make_gnomad_payload(pli=0.999, oe_lof_upper=0.1)
        exit_code, _, analysis = _run_analyze("TP53", payload)
        self.assertEqual(exit_code, 0)
        self.assertEqual(analysis["assessment"]["verdict"], "lof_intolerant")
        # No constraint_not_estimable relay on normal records
        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        self.assertNotIn("gnomad.constraint_not_estimable", relay_codes)

    def test_tolerant(self):
        payload = _make_gnomad_payload(pli=0.001, oe_lof_upper=1.2, flags=[])
        exit_code, _, analysis = _run_analyze("OR5A1", payload)
        self.assertEqual(exit_code, 0)
        self.assertEqual(analysis["assessment"]["verdict"], "lof_tolerant")


if __name__ == "__main__":
    unittest.main()
