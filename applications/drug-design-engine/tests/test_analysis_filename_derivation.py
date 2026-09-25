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

"""Tests for analysis filename derivation (#128).

Verifies that:
  - Stem derivation includes the record type from the schema
  - Two endpoints in the same artifact class produce different filenames
  - Cross-WO overwrite is refused by default
  - --overwrite-cross-wo flag allows explicit cross-WO overwrite
  - Relay fires on cross-WO overwrite
  - Backward compat: old-format files still readable
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest
from dde.core import provenance

# ---------------------------------------------------------------------------
# record_type_from_schema
# ---------------------------------------------------------------------------


class TestRecordTypeFromSchema:
    """Test that schema tags produce the correct record type."""

    def test_tox_margins(self):
        assert provenance.record_type_from_schema("dde.tox-margins.v1") == "tox-margins"

    def test_tox_genotox_assessment(self):
        assert (
            provenance.record_type_from_schema("dde.tox-genotox-assessment.v1")
            == "tox-genotox-assessment"
        )

    def test_tox_safety_pharm(self):
        assert (
            provenance.record_type_from_schema("dde.tox-safety-pharm.v1")
            == "tox-safety-pharm"
        )

    def test_pk_nca(self):
        assert provenance.record_type_from_schema("dde.pk-nca.v1") == "pk-nca"

    def test_pk_scaling(self):
        assert provenance.record_type_from_schema("dde.pk-scaling.v1") == "pk-scaling"

    def test_pk_ddi(self):
        assert provenance.record_type_from_schema("dde.pk-ddi.v1") == "pk-ddi"

    def test_selectivity_panel(self):
        assert (
            provenance.record_type_from_schema("dde.selectivity-panel.v1")
            == "selectivity-panel"
        )

    def test_fallback_unrecognised(self):
        """Unrecognised format returns the full string."""
        assert provenance.record_type_from_schema("not-a-schema") == "not-a-schema"

    def test_fallback_no_version(self):
        assert (
            provenance.record_type_from_schema("dde.tox-margins") == "dde.tox-margins"
        )


# ---------------------------------------------------------------------------
# record_type_from_filename
# ---------------------------------------------------------------------------


class TestRecordTypeFromFilename:
    def test_tox_margins(self):
        assert (
            provenance.record_type_from_filename("mc-klk5.tox-margins.json")
            == "tox-margins"
        )

    def test_pk_nca(self):
        assert provenance.record_type_from_filename("study1.pk-nca.json") == "pk-nca"

    def test_no_json_suffix(self):
        assert provenance.record_type_from_filename("foo.txt") is None


# ---------------------------------------------------------------------------
# Two endpoints in the same class produce different filenames
# ---------------------------------------------------------------------------


class TestNoCollision:
    """Two endpoints in the same artifact class MUST produce different filenames."""

    def test_tox_margins_vs_genotox(self):
        """tox-margins and tox-genotox-assessment for the same entity."""
        stem = "mc-klk5-lead-02"
        margins_type = provenance.record_type_from_schema("dde.tox-margins.v1")
        genotox_type = provenance.record_type_from_schema(
            "dde.tox-genotox-assessment.v1"
        )
        margins_name = f"{stem}.{margins_type}.analysis.json"
        genotox_name = f"{stem}.{genotox_type}.analysis.json"
        assert margins_name != genotox_name
        assert margins_name == "mc-klk5-lead-02.tox-margins.analysis.json"
        assert genotox_name == "mc-klk5-lead-02.tox-genotox-assessment.analysis.json"

    def test_tox_three_way(self):
        """All three tox schemas produce distinct filenames."""
        stem = "compound-x"
        schemas = [
            "dde.tox-margins.v1",
            "dde.tox-genotox-assessment.v1",
            "dde.tox-safety-pharm.v1",
        ]
        names = {
            f"{stem}.{provenance.record_type_from_schema(s)}.analysis.json"
            for s in schemas
        }
        assert len(names) == 3

    def test_pk_three_way(self):
        """All three pk schemas produce distinct filenames."""
        stem = "study-123"
        schemas = ["dde.pk-nca.v1", "dde.pk-scaling.v1", "dde.pk-ddi.v1"]
        names = {
            f"{stem}.{provenance.record_type_from_schema(s)}.analysis.json"
            for s in schemas
        }
        assert len(names) == 3


# ---------------------------------------------------------------------------
# Cross-WO overwrite protection
# ---------------------------------------------------------------------------


class TestCrossWOOverwrite:
    """Cross-work-order overwrite must be refused by default."""

    def _write_existing_analysis(self, path: Path, work_order_id: str) -> None:
        record = {
            "source": "test",
            "threshold_set": "test",
            "thresholds_applied": {},
            "metrics": {},
            "assessment": {"verdict": "ok"},
            "work_order_id": work_order_id,
            "written_by": "test-agent",
            "timestamp": "2026-01-01T00:00:00Z",
        }
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    def test_cross_wo_refused_by_default(self, tmp_path):
        """Overwriting a record from a different WO raises Refusal."""
        analysis_path = tmp_path / "test.analysis.json"
        self._write_existing_analysis(analysis_path, "WO-001")

        provenance.allow_overwrite(True)
        provenance.allow_overwrite_cross_wo(False)

        try:
            with mock.patch.dict(os.environ, {"DDE_WORK_ORDER_ID": "WO-002"}):
                with pytest.raises(provenance.Refusal) as exc_info:
                    provenance.write_analysis(
                        analysis_path,
                        source="test-source",
                        threshold_set="test",
                        thresholds_applied={},
                        metrics={},
                        assessment={"verdict": "ok"},
                    )
                assert "WO-001" in str(exc_info.value)
                assert "WO-002" in str(exc_info.value)
                assert "overwrite-cross-wo" in str(exc_info.value)
        finally:
            provenance.allow_overwrite(False)
            provenance.allow_overwrite_cross_wo(False)

    def test_cross_wo_allowed_with_flag(self, tmp_path):
        """--overwrite-cross-wo allows the overwrite and fires a relay."""
        analysis_path = tmp_path / "test.analysis.json"
        self._write_existing_analysis(analysis_path, "WO-001")

        provenance.allow_overwrite(True)
        provenance.allow_overwrite_cross_wo(True)

        try:
            with mock.patch.dict(os.environ, {"DDE_WORK_ORDER_ID": "WO-002"}):
                result = provenance.write_analysis(
                    analysis_path,
                    source="test-source",
                    threshold_set="test",
                    thresholds_applied={},
                    metrics={},
                    assessment={"verdict": "ok"},
                )
                # Verify the file was written
                assert result.exists()
                written = json.loads(result.read_text(encoding="utf-8"))
                assert written["work_order_id"] == "WO-002"
        finally:
            provenance.allow_overwrite(False)
            provenance.allow_overwrite_cross_wo(False)

    def test_cross_wo_relay_fires(self, tmp_path):
        """Cross-WO overwrite fires the provenance.cross_wo_overwrite relay."""
        analysis_path = tmp_path / "test.analysis.json"
        self._write_existing_analysis(analysis_path, "WO-001")

        provenance.allow_overwrite(True)
        provenance.allow_overwrite_cross_wo(True)

        try:
            with mock.patch.dict(os.environ, {"DDE_WORK_ORDER_ID": "WO-002"}):
                provenance.write_analysis(
                    analysis_path,
                    source="test-source",
                    threshold_set="test",
                    thresholds_applied={},
                    metrics={},
                    assessment={"verdict": "ok"},
                )
                written = json.loads(analysis_path.read_text(encoding="utf-8"))
                relays = written.get("mandatory_relays", [])
                cross_wo_relays = [
                    r
                    for r in relays
                    if r.get("code") == "provenance.cross_wo_overwrite"
                ]
                assert len(cross_wo_relays) == 1
                assert "WO-001" in cross_wo_relays[0]["message"]
                assert "WO-002" in cross_wo_relays[0]["message"]
        finally:
            provenance.allow_overwrite(False)
            provenance.allow_overwrite_cross_wo(False)

    def test_same_wo_overwrite_allowed(self, tmp_path):
        """Overwriting within the same WO does not trigger cross-WO protection."""
        analysis_path = tmp_path / "test.analysis.json"
        self._write_existing_analysis(analysis_path, "WO-001")

        provenance.allow_overwrite(True)
        provenance.allow_overwrite_cross_wo(False)

        try:
            with mock.patch.dict(os.environ, {"DDE_WORK_ORDER_ID": "WO-001"}):
                # Should not raise — same WO
                result = provenance.write_analysis(
                    analysis_path,
                    source="test-source",
                    threshold_set="test",
                    thresholds_applied={},
                    metrics={},
                    assessment={"verdict": "ok"},
                )
                assert result.exists()
        finally:
            provenance.allow_overwrite(False)
            provenance.allow_overwrite_cross_wo(False)

    def test_no_wo_set_allows_overwrite(self, tmp_path):
        """When no work order is set, cross-WO protection does not apply."""
        analysis_path = tmp_path / "test.analysis.json"
        self._write_existing_analysis(analysis_path, "WO-001")

        provenance.allow_overwrite(True)
        provenance.allow_overwrite_cross_wo(False)

        try:
            # No DDE_WORK_ORDER_ID set → current_wo is None → no cross-WO issue
            with mock.patch.dict(os.environ, {}, clear=True):
                os.environ.pop("DDE_WORK_ORDER_ID", None)
                result = provenance.write_analysis(
                    analysis_path,
                    source="test-source",
                    threshold_set="test",
                    thresholds_applied={},
                    metrics={},
                    assessment={"verdict": "ok"},
                )
                assert result.exists()
        finally:
            provenance.allow_overwrite(False)
            provenance.allow_overwrite_cross_wo(False)


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    """Old-format analysis filenames should still be readable."""

    def test_old_format_analysis_is_valid_json(self, tmp_path):
        """An old-format .tox.analysis.json is valid and parseable."""
        old_path = tmp_path / "compound-x.tox.analysis.json"
        record = {
            "source": "compound-x.tox-margins.json",
            "threshold_set": "tox-safety-package",
            "thresholds_applied": {"ti_minimum": 10},
            "metrics": {"analysis_type": "margins"},
            "assessment": {"verdict": "acceptable"},
        }
        old_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

        # Old-format file should be readable
        loaded = json.loads(old_path.read_text(encoding="utf-8"))
        assert loaded["assessment"]["verdict"] == "acceptable"

    def test_new_format_does_not_collide_with_old(self):
        """New-format filenames are different from old-format for multi-schema modules."""
        stem = "compound-x"
        old_name = f"{stem}.tox.analysis.json"
        new_margins = f"{stem}.tox-margins.analysis.json"
        new_genotox = f"{stem}.tox-genotox-assessment.analysis.json"
        assert old_name != new_margins
        assert old_name != new_genotox
        assert new_margins != new_genotox

    def test_old_and_new_can_coexist(self, tmp_path):
        """Old-format and new-format files can exist side by side."""
        old_path = tmp_path / "compound-x.tox.analysis.json"
        new_path = tmp_path / "compound-x.tox-margins.analysis.json"

        old_record = {"verdict": "old"}
        new_record = {"verdict": "new"}

        old_path.write_text(json.dumps(old_record) + "\n", encoding="utf-8")
        new_path.write_text(json.dumps(new_record) + "\n", encoding="utf-8")

        assert old_path.exists()
        assert new_path.exists()
        assert json.loads(old_path.read_text(encoding="utf-8"))["verdict"] == "old"
        assert json.loads(new_path.read_text(encoding="utf-8"))["verdict"] == "new"


# ---------------------------------------------------------------------------
# Relay code registration
# ---------------------------------------------------------------------------


class TestRelayCodeRegistration:
    """The cross-WO overwrite relay code must be registered."""

    def test_cross_wo_relay_code_registered(self):
        assert "provenance.cross_wo_overwrite" in provenance.RELAY_CODES

    def test_relay_builder_accepts_code(self):
        r = provenance.relay(
            "provenance.cross_wo_overwrite",
            "test message",
        )
        assert r["code"] == "provenance.cross_wo_overwrite"
        assert r["message"] == "test message"

    def test_relay_builder_rejects_unknown_code(self):
        with pytest.raises(KeyError):
            provenance.relay("provenance.unknown_code", "test")
