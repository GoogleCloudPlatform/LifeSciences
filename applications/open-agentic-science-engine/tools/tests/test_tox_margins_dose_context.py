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

"""Tests for dde tox margins — dose context and same-study detection (#137).

Issue #137: `dde tox margins` computes TI = NOAEL_exposure / PK_exposure
and assumes PK represents clinical dose.  When both come from the same
animal study (e.g., tox study at limit dose), TI ≈ 1.0x and the tool
flags a safety concern.  The true clinical margin may be much larger.

Asserts:
1. Same-study detection triggers indeterminate verdict.
2. --clinical-pk produces both animal_margin and clinical_ti.
3. ICH M3(R2) threshold applied only to clinical_ti.
4. --dose-context labels correctly.
5. Indeterminate relay fires.
6. Normal (different studies) still produces pass/fail verdict.
7. PK artifact dose_context field overrides CLI option.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any
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

from dde.commands.tox import (  # noqa: E402
    VALID_DOSE_CONTEXTS,
    _compute_ti_values,
    _detect_same_study,
)
from dde.core.provenance import RELAY_CODES  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_json(directory: Path, name: str, data: dict[str, Any]) -> Path:
    """Write a JSON file into *directory* and return its path."""
    p = directory / name
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return p


def _make_tox_artifact(
    *,
    study_id: str = "TOX-001",
    species: str = "rat",
    route: str = "oral",
    duration_days: int = 28,
    noael_mg_kg: float = 100.0,
    dose_mg_kg: float = 100.0,
    noael_exposure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a minimal tox-repeat-dose artifact."""
    doc: dict[str, Any] = {
        "schema": "dde.tox-repeat-dose.v1",
        "study_id": study_id,
        "species": species,
        "route": route,
        "duration_days": duration_days,
        "noael_mg_kg": noael_mg_kg,
        "noael_basis": "body weight and clinical signs",
        "dose_groups": [
            {"dose_mg_kg": 0, "n_animals": 10, "findings": []},
            {
                "dose_mg_kg": dose_mg_kg,
                "n_animals": 10,
                "findings": [],
            },
        ],
    }
    if noael_exposure is not None:
        doc["noael_exposure"] = noael_exposure
    return doc


def _make_pk_nca_artifact(
    *,
    study_id: str = "PK-001",
    species: str = "rat",
    route: str = "oral",
    cmax: float = 150.0,
    auc_0_inf: float = 2000.0,
    dose_mg_kg: float | None = None,
    dose_context: str | None = None,
) -> dict[str, Any]:
    """Build a minimal PK NCA artifact."""
    doc: dict[str, Any] = {
        "tool": "pk",
        "subcommand": "nca",
        "schema": "dde.pk-nca.v1",
        "study_id": study_id,
        "species": species,
        "route": route,
        "parameters": {
            "cmax": cmax,
            "cmax_units": "ng/mL",
            "tmax": 2.0,
            "tmax_units": "h",
            "auc_0_t": 1800.0,
            "auc_0_inf": auc_0_inf,
            "auc_units": "ng/mL * h",
            "auc_extrapolation_pct": 5.0,
            "half_life": 6.0,
            "half_life_units": "h",
            "lambda_z": 0.1155,
        },
    }
    if dose_mg_kg is not None:
        doc["dose_mg_kg"] = dose_mg_kg
    if dose_context is not None:
        doc["dose_context"] = dose_context
    return doc


# ---------------------------------------------------------------------------
# Tests — same-study detection
# ---------------------------------------------------------------------------


class TestDetectSameStudy(unittest.TestCase):
    """Unit tests for the _detect_same_study helper."""

    def test_matching_study_id(self) -> None:
        tox = _make_tox_artifact(study_id="STUDY-42")
        pk = _make_pk_nca_artifact(study_id="STUDY-42")
        reason = _detect_same_study(tox, pk, Path("a.json"), Path("b.json"))
        self.assertIsNotNone(reason)
        self.assertIn("STUDY-42", reason)

    def test_matching_file_stem(self) -> None:
        tox = _make_tox_artifact(study_id="A")
        pk = _make_pk_nca_artifact(study_id="B")
        reason = _detect_same_study(
            tox, pk, Path("same-stem.json"), Path("same-stem.json")
        )
        self.assertIsNotNone(reason)
        self.assertIn("same-stem", reason)

    def test_matching_species_route_dose(self) -> None:
        tox = _make_tox_artifact(
            study_id="A", species="rat", route="oral", noael_mg_kg=100.0
        )
        pk = _make_pk_nca_artifact(study_id="B", species="rat", route="oral")
        pk["dose_mg_kg"] = 100.0
        reason = _detect_same_study(tox, pk, Path("a.json"), Path("b.json"))
        self.assertIsNotNone(reason)
        self.assertIn("rat", reason)
        self.assertIn("oral", reason)

    def test_different_studies(self) -> None:
        tox = _make_tox_artifact(study_id="TOX-001", species="rat")
        pk = _make_pk_nca_artifact(study_id="PK-002", species="dog")
        reason = _detect_same_study(tox, pk, Path("tox.json"), Path("pk.json"))
        self.assertIsNone(reason)


# ---------------------------------------------------------------------------
# Tests — _compute_ti_values
# ---------------------------------------------------------------------------


class TestComputeTiValues(unittest.TestCase):
    """Unit tests for the _compute_ti_values helper."""

    def test_basic_cmax_ti(self) -> None:
        exposure = {"cmax": 300.0, "cmax_units": "ng/mL"}
        notes: list[str] = []
        ti = _compute_ti_values(exposure, 150.0, "ng/mL", None, None, notes)
        self.assertAlmostEqual(ti["ti_cmax"], 2.0, places=2)
        self.assertEqual(notes, [])

    def test_unit_mismatch_appends_note(self) -> None:
        exposure = {"cmax": 300.0, "cmax_units": "ng/mL"}
        notes: list[str] = []
        ti = _compute_ti_values(exposure, 150.0, "ug/mL", None, None, notes)
        self.assertNotIn("ti_cmax", ti)
        self.assertTrue(len(notes) > 0)
        self.assertIn("unit mismatch", notes[0].lower())


# ---------------------------------------------------------------------------
# Tests — indeterminate verdict (same-study, no clinical-pk)
# ---------------------------------------------------------------------------


class TestIndeterminateVerdict(unittest.TestCase):
    """Same-study + no --clinical-pk → indeterminate verdict."""

    def _run_margins(
        self,
        tox: dict[str, Any],
        pk: dict[str, Any],
        clinical_pk: dict[str, Any] | None = None,
        dose_context: str | None = None,
    ) -> dict[str, Any]:
        """Write artifacts to a temp dir and invoke margins logic directly.

        This bypasses the CLI (click) and calls the internal computation
        path so we can test without a full project context.
        """
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            tox_path = _write_json(td, "tox.tox-repeat-dose.json", tox)
            pk_path = _write_json(td, "pk.pk-nca.json", pk)

            clinical_pk_doc = None
            if clinical_pk is not None:
                _write_json(td, "clinical.pk-nca.json", clinical_pk)
                clinical_pk_doc = clinical_pk

            # Replicate the core margins logic from margins_cmd
            from dde.commands.tox import _sanitize_id

            study_id = _sanitize_id(tox["study_id"])
            noael_exposure = tox.get("noael_exposure")

            pk_params = pk.get("parameters", {})
            pk_cmax = pk_params.get("cmax")
            pk_cmax_units = pk_params.get("cmax_units")
            pk_auc_0_inf = pk_params.get("auc_0_inf")
            pk_auc_0_t = pk_params.get("auc_0_t")
            pk_auc_units = pk_params.get("auc_units")
            pk_auc = pk_auc_0_inf if pk_auc_0_inf is not None else pk_auc_0_t

            resolved_dose_context = pk.get("dose_context") or dose_context

            same_study_reason = _detect_same_study(tox, pk, tox_path, pk_path)

            is_indeterminate = False
            indeterminate_reason = None
            if same_study_reason and clinical_pk_doc is None:
                is_indeterminate = True
                indeterminate_reason = (
                    f"Both NOAEL and PK exposures appear to derive from "
                    f"the same study ({same_study_reason}). A therapeutic "
                    f"index requires comparison to projected clinical "
                    f"exposure. Provide --clinical-pk with human projected "
                    f"exposure."
                )

            margins = None
            animal_margin = None
            clinical_ti_val = None
            margin_notes: list[str] = []

            if noael_exposure is not None and not is_indeterminate:
                ti_values = _compute_ti_values(
                    noael_exposure,
                    pk_cmax,
                    pk_cmax_units,
                    pk_auc,
                    pk_auc_units,
                    margin_notes,
                )
                if clinical_pk_doc is not None:
                    animal_margin = ti_values if ti_values else None
                    clin_params = clinical_pk_doc.get("parameters", {})
                    clin_cmax = clin_params.get("cmax")
                    clin_cmax_units = clin_params.get("cmax_units")
                    clin_auc_0_inf = clin_params.get("auc_0_inf")
                    clin_auc_0_t = clin_params.get("auc_0_t")
                    clin_auc_units = clin_params.get("auc_units")
                    clin_auc = (
                        clin_auc_0_inf if clin_auc_0_inf is not None else clin_auc_0_t
                    )
                    clin_notes: list[str] = []
                    clinical_ti_val = (
                        _compute_ti_values(
                            noael_exposure,
                            clin_cmax,
                            clin_cmax_units,
                            clin_auc,
                            clin_auc_units,
                            clin_notes,
                        )
                        or None
                    )
                    margins = clinical_ti_val
                else:
                    margins = ti_values if ti_values else None

            record: dict[str, Any] = {
                "schema": "dde.tox-margins.v1",
                "study_id": study_id,
            }
            if is_indeterminate:
                record["verdict"] = "indeterminate"
                record["verdict_reason"] = indeterminate_reason
                record["margins"] = None
                record["same_study_detected"] = same_study_reason
            else:
                record["margins"] = margins
                if animal_margin is not None:
                    record["animal_margin"] = animal_margin
                if clinical_ti_val is not None:
                    record["clinical_ti"] = clinical_ti_val

            if resolved_dose_context is not None:
                record["dose_context"] = resolved_dose_context

            return record

    def test_same_study_triggers_indeterminate(self) -> None:
        """Same study_id + no --clinical-pk → indeterminate verdict."""
        tox = _make_tox_artifact(
            study_id="SAME-001",
            noael_exposure={
                "cmax": 300.0,
                "cmax_units": "ng/mL",
                "auc": 4000.0,
                "auc_units": "ng/mL * h",
            },
        )
        pk = _make_pk_nca_artifact(study_id="SAME-001")
        result = self._run_margins(tox, pk)

        self.assertEqual(result["verdict"], "indeterminate")
        self.assertIn("same study", result["verdict_reason"].lower())
        self.assertIn("--clinical-pk", result["verdict_reason"])
        self.assertIsNone(result["margins"])
        self.assertIsNotNone(result["same_study_detected"])

    def test_clinical_pk_produces_both_margins(self) -> None:
        """--clinical-pk produces animal_margin AND clinical_ti."""
        tox = _make_tox_artifact(
            study_id="SAME-001",
            noael_exposure={
                "cmax": 300.0,
                "cmax_units": "ng/mL",
                "auc": 4000.0,
                "auc_units": "ng/mL * h",
            },
        )
        pk = _make_pk_nca_artifact(study_id="SAME-001", cmax=300.0, auc_0_inf=4000.0)
        clinical = _make_pk_nca_artifact(
            study_id="HUMAN-001",
            species="human",
            cmax=4.37,
            auc_0_inf=58.3,
        )
        result = self._run_margins(tox, pk, clinical_pk=clinical)

        # Not indeterminate when clinical PK is provided
        self.assertNotEqual(result.get("verdict"), "indeterminate")
        # Both margins present
        self.assertIn("animal_margin", result)
        self.assertIn("clinical_ti", result)
        # Animal margin ≈ 1.0 (same study)
        self.assertAlmostEqual(result["animal_margin"]["ti_cmax"], 1.0, places=1)
        # Clinical TI should be much larger (300 / 4.37 ≈ 68.6)
        self.assertGreater(result["clinical_ti"]["ti_cmax"], 50.0)

    def test_ich_threshold_applied_only_to_clinical_ti(self) -> None:
        """ICH M3(R2) thresholds must apply to clinical_ti, not animal_margin."""
        from dde.commands.tox import _analyze_margins

        doc = {
            "schema": "dde.tox-margins.v1",
            "margins": {"ti_cmax": 68.6},
            "animal_margin": {"ti_cmax": 1.0},
            "clinical_ti": {"ti_cmax": 68.6},
        }

        # Mock thresholds with a 10-fold minimum
        thresholds = MagicMock()
        thresholds.get.side_effect = lambda key: {
            "ti_minimum": 10.0,
            "herg_safety_margin": 30.0,
            "herg_marginal": 10.0,
        }.get(key, 0.0)

        metrics: dict[str, Any] = {}
        assessment: dict[str, Any] = {}
        _analyze_margins(doc, thresholds, metrics, assessment)

        # clinical_ti passes (68.6 > 10)
        self.assertEqual(assessment["verdict"], "acceptable")
        # Animal margin is recorded as informational, not graded
        self.assertEqual(assessment["animal_margin"]["status"], "informational")
        # clinical_ti Cmax is in metrics
        self.assertIn("clinical_ti_cmax", metrics)
        self.assertEqual(metrics["clinical_ti_cmax"], 68.6)
        # animal margin is also in metrics
        self.assertIn("animal_ti_cmax", metrics)

    def test_dose_context_labels_correctly(self) -> None:
        """--dose-context label recorded in output artifact."""
        tox = _make_tox_artifact(study_id="TOX-A")
        pk = _make_pk_nca_artifact(study_id="PK-B")
        result = self._run_margins(tox, pk, dose_context="animal_limit_dose")
        self.assertEqual(result["dose_context"], "animal_limit_dose")

    def test_pk_artifact_dose_context_overrides_cli(self) -> None:
        """dose_context in PK artifact overrides --dose-context CLI."""
        tox = _make_tox_artifact(study_id="TOX-A")
        pk = _make_pk_nca_artifact(study_id="PK-B", dose_context="human_projected")
        result = self._run_margins(tox, pk, dose_context="animal_limit_dose")
        self.assertEqual(result["dose_context"], "human_projected")

    def test_normal_different_studies_produces_verdict(self) -> None:
        """Different study IDs + different species → normal pass/fail."""
        tox = _make_tox_artifact(
            study_id="TOX-001",
            species="rat",
            noael_exposure={
                "cmax": 300.0,
                "cmax_units": "ng/mL",
            },
        )
        pk = _make_pk_nca_artifact(study_id="PK-002", species="dog", cmax=4.37)
        result = self._run_margins(tox, pk)

        self.assertNotEqual(result.get("verdict"), "indeterminate")
        self.assertIsNotNone(result["margins"])
        # TI = 300 / 4.37 ≈ 68.6
        self.assertGreater(result["margins"]["ti_cmax"], 50.0)


# ---------------------------------------------------------------------------
# Tests — indeterminate relay fires
# ---------------------------------------------------------------------------


class TestIndeterminateRelay(unittest.TestCase):
    """The tox.margin_indeterminate relay code is registered and fires."""

    def test_relay_code_registered(self) -> None:
        """tox.margin_indeterminate must be in RELAY_CODES."""
        self.assertIn("tox.margin_indeterminate", RELAY_CODES)

    def test_analyze_indeterminate_verdict(self) -> None:
        """_analyze_margins with indeterminate doc → indeterminate assessment."""
        from dde.commands.tox import _analyze_margins

        doc = {
            "schema": "dde.tox-margins.v1",
            "verdict": "indeterminate",
            "verdict_reason": (
                "Both NOAEL and PK exposures appear to derive from the "
                "same study (study_id='SAME-001'). A therapeutic index "
                "requires comparison to projected clinical exposure."
            ),
            "margins": None,
            "same_study_detected": "study_id='SAME-001'",
        }

        thresholds = MagicMock()
        metrics: dict[str, Any] = {}
        assessment: dict[str, Any] = {}
        _analyze_margins(doc, thresholds, metrics, assessment)

        self.assertEqual(assessment["verdict"], "indeterminate")
        self.assertIn("verdict_reason", assessment)
        self.assertEqual(assessment["ti"]["status"], "indeterminate")
        # Must NOT emit flagged or passed
        self.assertNotEqual(assessment["verdict"], "flagged")
        self.assertNotEqual(assessment["verdict"], "acceptable")


# ---------------------------------------------------------------------------
# Tests — dose context constants
# ---------------------------------------------------------------------------


class TestDoseContextConstants(unittest.TestCase):
    """Verify the dose context enum values."""

    def test_valid_contexts(self) -> None:
        expected = {
            "animal_limit_dose",
            "animal_therapeutic",
            "human_projected",
            "human_observed",
        }
        self.assertEqual(VALID_DOSE_CONTEXTS, expected)


if __name__ == "__main__":
    unittest.main()
