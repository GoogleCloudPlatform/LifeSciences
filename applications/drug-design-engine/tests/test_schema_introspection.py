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

"""Tests for schema introspection and self-documenting errors (#139).

Covers:
  - schema show outputs field table
  - schema show --json outputs valid JSON
  - schema list includes known schemas
  - schema template produces valid skeleton
  - template has all required fields
  - SchemaError on bad enum includes accepted values
  - SchemaError suggests nearest match
  - SchemaError on missing field lists required fields
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.core.errors import Refusal, SchemaError
from dde.core.schema_registry import (
    FieldDef,
    get_schema,
    list_schemas,
    make_template,
    show_table,
    suggest_match,
    validate_enum,
    validate_required_fields,
)

# ---------------------------------------------------------------------------
# schema list
# ---------------------------------------------------------------------------


class TestSchemaList:
    """Test that schema list includes known schemas."""

    def test_list_returns_known_schemas(self):
        ids = list_schemas()
        assert len(ids) >= 5, f"Expected at least 5 schemas, got {len(ids)}"

    def test_list_includes_pk_study(self):
        ids = list_schemas()
        assert "dde.pk-study.v1" in ids

    def test_list_includes_tox_repeat_dose(self):
        ids = list_schemas()
        assert "dde.tox-repeat-dose.v1" in ids

    def test_list_includes_tox_safety_pharm(self):
        ids = list_schemas()
        assert "dde.tox-safety-pharm.v1" in ids

    def test_list_includes_tox_genotox(self):
        ids = list_schemas()
        assert "dde.tox-genotox.v1" in ids

    def test_list_includes_pk_ddi_input(self):
        ids = list_schemas()
        assert "dde.pk-ddi-input.v1" in ids

    def test_list_is_sorted(self):
        ids = list_schemas()
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# schema show (field table)
# ---------------------------------------------------------------------------


class TestSchemaShow:
    """Test that schema show outputs a readable field table."""

    def test_show_pk_study_contains_header(self):
        table = show_table("dde.pk-study.v1")
        assert "Schema: dde.pk-study.v1" in table

    def test_show_contains_field_names(self):
        table = show_table("dde.pk-study.v1")
        assert "route" in table
        assert "species" in table
        assert "dose_mg_kg" in table
        assert "time_points" in table
        assert "concentrations" in table

    def test_show_contains_type_info(self):
        table = show_table("dde.pk-study.v1")
        assert "string" in table
        assert "number" in table

    def test_show_contains_required_info(self):
        table = show_table("dde.pk-study.v1")
        assert "yes" in table  # required fields

    def test_show_contains_enum_values(self):
        table = show_table("dde.pk-study.v1")
        # route enum values should be visible
        assert "iv" in table
        assert "oral" in table
        assert "sc" in table

    def test_show_tox_repeat_dose_contains_species_enum(self):
        table = show_table("dde.tox-repeat-dose.v1")
        assert "rat" in table
        assert "mouse" in table
        assert "dog" in table

    def test_show_unknown_schema_raises(self):
        try:
            show_table("dde.nonexistent.v99")
            assert False, "Expected KeyError"
        except KeyError:
            pass


# ---------------------------------------------------------------------------
# schema show --json
# ---------------------------------------------------------------------------


class TestSchemaShowJson:
    """Test that schema show --json outputs valid JSON."""

    def test_json_is_valid(self):
        schema = get_schema("dde.pk-study.v1")
        assert schema is not None
        # Build the same JSON record the command would emit
        record = {
            "schema_id": schema.schema_id,
            "description": schema.description,
            "fields": [
                {
                    "name": f.name,
                    "type": f.type,
                    "required": f.required,
                    **({"enum_values": list(f.enum_values)} if f.enum_values else {}),
                    **({"const": f.const} if f.const is not None else {}),
                    **({"description": f.description} if f.description else {}),
                }
                for f in schema.fields
            ],
        }
        # Verify it's valid JSON by round-tripping
        text = json.dumps(record, indent=2)
        parsed = json.loads(text)
        assert parsed["schema_id"] == "dde.pk-study.v1"
        assert isinstance(parsed["fields"], list)
        assert len(parsed["fields"]) > 0

    def test_json_contains_enum_values(self):
        schema = get_schema("dde.pk-study.v1")
        assert schema is not None
        route_field = schema.get_field("route")
        assert route_field is not None
        assert len(route_field.enum_values) > 0
        assert "iv" in route_field.enum_values
        assert "oral" in route_field.enum_values

    def test_json_contains_required_flag(self):
        schema = get_schema("dde.pk-study.v1")
        assert schema is not None
        route_field = schema.get_field("route")
        assert route_field is not None
        assert route_field.required is True

        notes_field = schema.get_field("notes")
        assert notes_field is not None
        assert notes_field.required is False


# ---------------------------------------------------------------------------
# schema template
# ---------------------------------------------------------------------------


class TestSchemaTemplate:
    """Test that schema template produces a valid skeleton."""

    def test_template_is_valid_json(self):
        doc = make_template("dde.pk-study.v1")
        # Round-trip through JSON serialization
        text = json.dumps(doc, indent=2)
        parsed = json.loads(text)
        assert isinstance(parsed, dict)

    def test_template_has_all_required_fields(self):
        schema = get_schema("dde.pk-study.v1")
        assert schema is not None
        doc = make_template("dde.pk-study.v1")
        for f in schema.required_fields():
            assert f.name in doc, f"Required field {f.name!r} missing from template"

    def test_template_has_schema_tag(self):
        doc = make_template("dde.pk-study.v1")
        assert doc["schema"] == "dde.pk-study.v1"

    def test_template_enum_uses_first_value(self):
        doc = make_template("dde.pk-study.v1")
        # route should use the first sorted enum value
        schema = get_schema("dde.pk-study.v1")
        route_field = schema.get_field("route")
        assert doc["route"] == route_field.enum_values[0]

    def test_template_string_uses_placeholder(self):
        doc = make_template("dde.pk-study.v1")
        assert doc["study_id"] == "<study_id>"

    def test_template_number_uses_zero(self):
        doc = make_template("dde.pk-study.v1")
        assert doc["dose_mg_kg"] == 0.0

    def test_template_optional_in_separate_block(self):
        doc = make_template("dde.pk-study.v1")
        assert "_optional" in doc
        assert "notes" in doc["_optional"]
        assert "blq_value" in doc["_optional"]

    def test_template_tox_repeat_dose(self):
        doc = make_template("dde.tox-repeat-dose.v1")
        assert doc["schema"] == "dde.tox-repeat-dose.v1"
        assert "study_id" in doc
        assert "species" in doc
        assert "route" in doc
        assert "duration_days" in doc
        assert "noael_mg_kg" in doc
        assert "dose_groups" in doc

    def test_template_unknown_schema_raises(self):
        try:
            make_template("dde.nonexistent.v99")
            assert False, "Expected KeyError"
        except KeyError:
            pass


# ---------------------------------------------------------------------------
# SchemaError on bad enum — includes accepted values
# ---------------------------------------------------------------------------


class TestSchemaErrorEnum:
    """Test that schema validation errors include accepted values."""

    def test_validate_enum_raises_with_accepted_values(self):
        try:
            validate_enum("route", "topical", {"iv", "oral", "sc", "im", "ip"})
            assert False, "Expected SchemaError"
        except SchemaError as exc:
            assert "accepted values" in exc.detail
            assert "iv" in exc.detail
            assert "oral" in exc.detail

    def test_validate_enum_passes_valid_value(self):
        # Should not raise
        validate_enum("route", "iv", {"iv", "oral", "sc", "im", "ip"})

    def test_pk_route_error_includes_accepted_values(self):
        """Test that pk.py route validation includes accepted values."""
        from dde.commands.pk import _validate_study

        doc = {
            "schema": "dde.pk-study.v1",
            "study_id": "test",
            "species": "rat",
            "route": "rectal",  # invalid
            "dose_mg_kg": 10.0,
            "time_units": "h",
            "concentration_units": "ng/mL",
            "time_points": [0, 1, 2, 3],
            "concentrations": [0, 100, 50, 25],
        }
        try:
            _validate_study(doc)
            assert False, "Expected Refusal"
        except Refusal as exc:
            assert "accepted values" in exc.detail

    def test_tox_glp_error_includes_accepted_values(self):
        """Test that tox.py glp_status validation includes accepted values."""
        from dde.commands.tox import _validate_repeat_dose

        doc = {
            "schema": "dde.tox-repeat-dose.v1",
            "study_id": "test",
            "species": "rat",
            "route": "oral",
            "duration_days": 28,
            "noael_mg_kg": 10.0,
            "noael_basis": "body weight",
            "dose_groups": [
                {"dose_mg_kg": 0, "n_animals": 5, "findings": []},
                {"dose_mg_kg": 10.0, "n_animals": 5, "findings": []},
            ],
            "glp_status": "non-glp",  # invalid
        }
        try:
            _validate_repeat_dose(doc)
            assert False, "Expected Refusal"
        except Refusal as exc:
            assert "accepted values" in exc.detail


# ---------------------------------------------------------------------------
# SchemaError suggests nearest match
# ---------------------------------------------------------------------------


class TestSchemaErrorFuzzyMatch:
    """Test that schema validation suggests nearest match."""

    def test_suggest_match_finds_close(self):
        result = suggest_match("complant", {"compliant", "non-compliant", "not_stated"})
        assert result == "compliant"

    def test_suggest_match_returns_none_for_distant(self):
        result = suggest_match("zzzzzzz", {"compliant", "non-compliant", "not_stated"})
        assert result is None

    def test_validate_enum_suggests_nearest(self):
        try:
            validate_enum(
                "glp_status",
                "non-complant",
                {"compliant", "non-compliant", "not_stated"},
            )
            assert False, "Expected SchemaError"
        except SchemaError as exc:
            assert "Did you mean" in exc.message
            assert "non-compliant" in exc.message

    def test_pk_route_suggests_nearest(self):
        """Test that pk.py route validation suggests nearest match."""
        from dde.commands.pk import _validate_study

        doc = {
            "schema": "dde.pk-study.v1",
            "study_id": "test",
            "species": "rat",
            "route": "orla",  # close to "oral"
            "dose_mg_kg": 10.0,
            "time_units": "h",
            "concentration_units": "ng/mL",
            "time_points": [0, 1, 2, 3],
            "concentrations": [0, 100, 50, 25],
        }
        try:
            _validate_study(doc)
            assert False, "Expected Refusal"
        except Refusal as exc:
            assert "Did you mean" in exc.message
            assert "oral" in exc.message

    def test_tox_severity_suggests_nearest(self):
        """Test that tox.py severity validation suggests nearest match."""
        from dde.commands.tox import _validate_finding

        finding = {
            "finding": "hepatocellular hypertrophy",
            "organ_system": "liver",
            "severity": "moderat",  # close to "moderate"
        }
        try:
            _validate_finding(0, 0, finding)
            assert False, "Expected Refusal"
        except Refusal as exc:
            assert "Did you mean" in exc.message
            assert "moderate" in exc.message


# ---------------------------------------------------------------------------
# SchemaError on missing field lists required fields
# ---------------------------------------------------------------------------


class TestSchemaErrorMissingField:
    """Test that missing-field errors list all required fields."""

    def test_validate_required_fields_raises(self):
        doc = {"schema": "dde.pk-study.v1", "study_id": "test"}
        try:
            validate_required_fields(doc, "dde.pk-study.v1")
            assert False, "Expected SchemaError"
        except SchemaError as exc:
            assert "missing required field" in exc.message
            assert "all required fields" in exc.detail

    def test_validate_required_fields_passes_complete(self):
        schema = get_schema("dde.pk-study.v1")
        doc = {}
        for f in schema.required_fields():
            if f.const is not None:
                doc[f.name] = f.const
            elif f.enum_values:
                doc[f.name] = f.enum_values[0]
            elif f.type == "string":
                doc[f.name] = "test"
            elif f.type == "number":
                doc[f.name] = 1.0
            elif f.type == "integer":
                doc[f.name] = 1
            elif f.type.startswith("array"):
                doc[f.name] = [1.0, 2.0, 3.0]
            elif f.type == "boolean":
                doc[f.name] = True
            else:
                doc[f.name] = "test"

        # Should not raise
        validate_required_fields(doc, "dde.pk-study.v1")

    def test_pk_missing_field_lists_required(self):
        """Test that pk.py missing-field errors list required fields."""
        from dde.commands.pk import _validate_study

        doc = {
            "schema": "dde.pk-study.v1",
            "study_id": "test",
            "species": "rat",
            # route is missing
            "dose_mg_kg": 10.0,
            "time_units": "h",
            "concentration_units": "ng/mL",
            "time_points": [0, 1, 2, 3],
            "concentrations": [0, 100, 50, 25],
        }
        try:
            _validate_study(doc)
            assert False, "Expected SchemaError"
        except SchemaError as exc:
            assert "route" in exc.message
            # Should list all required fields in detail
            assert "all required fields" in exc.detail


# ---------------------------------------------------------------------------
# Registry consistency
# ---------------------------------------------------------------------------


class TestRegistryConsistency:
    """Test that the schema registry is internally consistent."""

    def test_every_schema_has_schema_field(self):
        for sid in list_schemas():
            schema = get_schema(sid)
            assert schema is not None
            schema_field = schema.get_field("schema")
            assert schema_field is not None, f"{sid} missing 'schema' field"
            assert schema_field.const == sid

    def test_every_schema_has_description(self):
        for sid in list_schemas():
            schema = get_schema(sid)
            assert schema.description, f"{sid} has empty description"

    def test_get_unknown_returns_none(self):
        assert get_schema("dde.nonexistent.v99") is None

    def test_field_def_frozen(self):
        f = FieldDef("test", "string", True)
        try:
            f.name = "changed"  # type: ignore
            assert False, "Expected frozen error"
        except AttributeError:
            pass


# ---------------------------------------------------------------------------
# Main: run tests with pytest or standalone
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v"],
        cwd=str(Path(__file__).parent.parent),
    )
    sys.exit(result.returncode)
