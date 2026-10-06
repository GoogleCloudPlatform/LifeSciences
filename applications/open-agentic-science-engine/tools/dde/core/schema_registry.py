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

"""Declarative schema registry for dde ingest schemas.

Provides a single mechanism that discovers schemas from the codebase:
each schema is defined once as a ``SchemaDef`` with typed fields,
required/optional flags, and enum sets.  The registry supports:

  - ``list_schemas()``     — enumerate all known schema IDs
  - ``get_schema(id)``     — retrieve a schema definition by ID
  - ``show_table(id)``     — render a human-readable field table
  - ``make_template(id)``  — generate a skeleton JSON document
  - ``validate_field_enum()`` — self-documenting enum validation
  - ``validate_required()``   — self-documenting required-field check

All schemas are registered in ``_REGISTRY`` at module load time.
This is generic infrastructure — it works for all groups, not
hand-coded per group.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Field and SchemaDef definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldDef:
    """One field in a schema definition.

    Attributes:
        name:        JSON key name.
        type:        Human-readable type (``"string"``, ``"integer"``,
                     ``"number"``, ``"boolean"``, ``"array[number]"``,
                     ``"array[object]"``, ``"object"``).
        required:    Whether the field must be present.
        enum_values: Accepted string values (empty for non-enum fields).
        const:       Fixed constant value (e.g. the schema tag itself).
        description: Short human-readable note about the field.
    """

    name: str
    type: str = "string"
    required: bool = True
    enum_values: tuple[str, ...] = ()
    const: str | None = None
    description: str = ""


@dataclass(frozen=True)
class SchemaDef:
    """A complete schema definition.

    Attributes:
        schema_id:   Dotted identifier, e.g. ``"dde.pk-study.v1"``.
        description: Human-readable one-liner.
        fields:      Ordered list of field definitions.
    """

    schema_id: str
    description: str
    fields: tuple[FieldDef, ...]

    def required_fields(self) -> list[FieldDef]:
        """Return only the required fields."""
        return [f for f in self.fields if f.required]

    def optional_fields(self) -> list[FieldDef]:
        """Return only the optional fields."""
        return [f for f in self.fields if not f.required]

    def field_names(self) -> list[str]:
        """Return all field names in order."""
        return [f.name for f in self.fields]

    def get_field(self, name: str) -> FieldDef | None:
        """Look up a field by name."""
        for f in self.fields:
            if f.name == name:
                return f
        return None


# ---------------------------------------------------------------------------
# Registry — all known schemas
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, SchemaDef] = {}


def _register(schema: SchemaDef) -> SchemaDef:
    """Register a schema definition. Returns it for chaining."""
    _REGISTRY[schema.schema_id] = schema
    return schema


def list_schemas() -> list[str]:
    """Return all registered schema IDs, sorted."""
    return sorted(_REGISTRY)


def get_schema(schema_id: str) -> SchemaDef | None:
    """Look up a schema by ID, returning None if unknown."""
    return _REGISTRY.get(schema_id)


# ---------------------------------------------------------------------------
# Human-readable field table
# ---------------------------------------------------------------------------


def show_table(schema_id: str) -> str:
    """Render a human-readable field table for a schema.

    Returns a formatted string suitable for terminal display.
    Raises ``KeyError`` if the schema ID is unknown.
    """
    schema = _REGISTRY.get(schema_id)
    if schema is None:
        raise KeyError(f"unknown schema: {schema_id!r}")

    lines: list[str] = []
    lines.append(f"Schema: {schema.schema_id}")
    lines.append(f"  {schema.description}")
    lines.append("")

    # Column widths
    name_w = max(len("Field"), max(len(f.name) for f in schema.fields))
    type_w = max(len("Type"), max(len(f.type) for f in schema.fields))
    req_w = len("Required")

    header = (
        f"{'Field':<{name_w}}  {'Type':<{type_w}}  {'Required':<{req_w}}  Enum Values"
    )
    separator = f"{'─' * name_w}  {'─' * type_w}  {'─' * req_w}  {'─' * 11}"

    lines.append(header)
    lines.append(separator)

    for f in schema.fields:
        req_str = "yes" if f.required else "no"
        if f.const is not None:
            enum_str = f"(constant: {f.const})"
        elif f.enum_values:
            vals = ", ".join(f.enum_values)
            enum_str = vals
        else:
            enum_str = ""
        lines.append(
            f"{f.name:<{name_w}}  {f.type:<{type_w}}  {req_str:<{req_w}}  {enum_str}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Template generation
# ---------------------------------------------------------------------------


def _placeholder_value(f: FieldDef) -> Any:
    """Return a sensible placeholder value for a field."""
    if f.const is not None:
        return f.const
    if f.enum_values:
        return f.enum_values[0]

    type_lower = f.type.lower()
    if type_lower == "string":
        return f"<{f.name}>"
    elif type_lower == "integer":
        return 0
    elif type_lower == "number":
        return 0.0
    elif type_lower == "boolean":
        return False
    elif type_lower.startswith("array[number]"):
        return [0.0, 0.0, 0.0]
    elif type_lower.startswith("array[object]"):
        return [{}]
    elif type_lower.startswith("array"):
        return []
    elif type_lower == "object":
        return {}
    else:
        return None


def make_template(schema_id: str) -> dict[str, Any]:
    """Generate a skeleton JSON document for a schema.

    All required fields are present with placeholder values.
    Optional fields are included in a separate ``"_optional"`` block.
    Raises ``KeyError`` if the schema ID is unknown.
    """
    schema = _REGISTRY.get(schema_id)
    if schema is None:
        raise KeyError(f"unknown schema: {schema_id!r}")

    doc: dict[str, Any] = {}

    # Required fields first
    for f in schema.fields:
        if f.required:
            doc[f.name] = _placeholder_value(f)

    # Optional fields in a separate section
    optional = {}
    for f in schema.fields:
        if not f.required:
            optional[f.name] = _placeholder_value(f)

    if optional:
        doc["_optional"] = optional

    return doc


# ---------------------------------------------------------------------------
# Self-documenting validation helpers
# ---------------------------------------------------------------------------


def validate_enum(
    field_name: str,
    value: str,
    accepted: set[str] | frozenset[str] | tuple[str, ...] | list[str],
    *,
    schema_id: str | None = None,
) -> None:
    """Validate a string value against an enum set.

    On failure, raises :class:`SchemaError` (imported lazily to avoid
    circular imports) with:
      - the list of accepted values
      - the nearest match via ``difflib.get_close_matches``
    """
    if value in accepted:
        return

    sorted_accepted = sorted(accepted)
    detail = f"accepted values: {sorted_accepted}"

    # Fuzzy match suggestion
    matches = difflib.get_close_matches(value, sorted_accepted, n=1, cutoff=0.5)
    suggestion = ""
    if matches:
        suggestion = f" Did you mean {matches[0]!r}?"

    from .errors import SchemaError

    raise SchemaError(
        f"{value!r} is not a valid value for {field_name!r}.{suggestion}",
        detail=detail,
        remedy=f"use one of: {', '.join(sorted_accepted)}",
    )


def validate_required_fields(
    doc: dict[str, Any],
    schema_id: str,
) -> None:
    """Check that all required fields for a schema are present.

    On failure, raises :class:`SchemaError` listing all required fields
    for the schema.
    """
    schema = _REGISTRY.get(schema_id)
    if schema is None:
        return  # Unknown schema — cannot validate

    required = schema.required_fields()
    missing = [f.name for f in required if f.name not in doc or doc[f.name] is None]

    if not missing:
        return

    all_required_names = [f.name for f in required]

    from .errors import SchemaError

    raise SchemaError(
        f"missing required field(s): {missing}",
        detail=f"all required fields for {schema_id}: {all_required_names}",
        remedy="add the missing field(s) to the input JSON",
    )


def suggest_match(
    value: str,
    accepted: set[str] | frozenset[str] | tuple[str, ...] | list[str],
) -> str | None:
    """Return the closest match for *value* in *accepted*, or None."""
    matches = difflib.get_close_matches(value, sorted(accepted), n=1, cutoff=0.5)
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Schema definitions — all groups
# ---------------------------------------------------------------------------

# ── PK schemas ──────────────────────────────────────────────────────────

_register(
    SchemaDef(
        schema_id="dde.pk-study.v1",
        description="PK study input for ingest — concentration-time data",
        fields=(
            FieldDef("schema", "string", required=True, const="dde.pk-study.v1"),
            FieldDef(
                "study_id",
                "string",
                required=True,
                description="Unique identifier for the study",
            ),
            FieldDef("species", "string", required=True, description="Animal species"),
            FieldDef(
                "route",
                "string",
                required=True,
                enum_values=(
                    "dermal",
                    "im",
                    "inhaled",
                    "intranasal",
                    "ip",
                    "iv",
                    "ophthalmic",
                    "oral",
                    "sc",
                    "topical",
                ),
                description="Administration route",
            ),
            FieldDef(
                "dose_mg_kg",
                "number",
                required=True,
                description="Administered dose in mg/kg (positive)",
            ),
            FieldDef(
                "time_units",
                "string",
                required=True,
                enum_values=("h", "min", "s"),
                description="Units for time_points",
            ),
            FieldDef(
                "concentration_units",
                "string",
                required=True,
                enum_values=("mg/mL", "nM", "ng/mL", "uM", "ug/mL"),
                description="Units for concentrations",
            ),
            FieldDef(
                "time_points",
                "array[number]",
                required=True,
                description="Monotonically increasing time values",
            ),
            FieldDef(
                "concentrations",
                "array[number]",
                required=True,
                description="Concentration values matching time_points",
            ),
            FieldDef(
                "dose_units",
                "string",
                required=False,
                description="Dose units (default: mg/kg)",
            ),
            FieldDef(
                "blq_value",
                "number",
                required=False,
                description="BLQ marker value (positive number)",
            ),
            FieldDef(
                "body_weight_kg",
                "number",
                required=False,
                description="Animal body weight in kg",
            ),
            FieldDef("notes", "string", required=False, description="Free-text notes"),
        ),
    )
)

_register(
    SchemaDef(
        schema_id="dde.pk-ddi-input.v1",
        description="DDI input — unbound Cmax and CYP inhibition data",
        fields=(
            FieldDef("schema", "string", required=True, const="dde.pk-ddi-input.v1"),
            FieldDef(
                "compound_id",
                "string",
                required=True,
                description="Compound identifier",
            ),
            FieldDef(
                "cmax_unbound",
                "number",
                required=True,
                description="Unbound Cmax at therapeutic dose (positive)",
            ),
            FieldDef(
                "cmax_units",
                "string",
                required=True,
                description="Concentration units for Cmax",
            ),
            FieldDef(
                "cyp_inhibition",
                "array[object]",
                required=True,
                description="Array of CYP inhibition entries (isoform, ic50_or_ki, value_type, units)",
            ),
        ),
    )
)

# ── Tox schemas ─────────────────────────────────────────────────────────

_register(
    SchemaDef(
        schema_id="dde.tox-repeat-dose.v1",
        description="Repeat-dose tox study input",
        fields=(
            FieldDef("schema", "string", required=True, const="dde.tox-repeat-dose.v1"),
            FieldDef(
                "study_id",
                "string",
                required=True,
                description="Unique identifier for the study",
            ),
            FieldDef(
                "species",
                "string",
                required=True,
                enum_values=("dog", "minipig", "monkey", "mouse", "rabbit", "rat"),
                description="Animal species",
            ),
            FieldDef(
                "route",
                "string",
                required=True,
                enum_values=("dermal", "im", "inhalation", "ip", "iv", "oral", "sc"),
                description="Administration route",
            ),
            FieldDef(
                "duration_days",
                "integer",
                required=True,
                description="Study duration in days (positive)",
            ),
            FieldDef(
                "noael_mg_kg",
                "number",
                required=True,
                description="No-adverse-effect level in mg/kg (positive)",
            ),
            FieldDef(
                "noael_basis",
                "string",
                required=True,
                description="Basis for NOAEL determination",
            ),
            FieldDef(
                "dose_groups",
                "array[object]",
                required=True,
                description="Array of dose group objects (dose_mg_kg, n_animals, findings)",
            ),
            FieldDef(
                "glp_status",
                "string",
                required=False,
                enum_values=("compliant", "non-compliant", "not_stated"),
                description="GLP compliance status",
            ),
            FieldDef("strain", "string", required=False, description="Animal strain"),
            FieldDef(
                "loael_mg_kg",
                "number",
                required=False,
                description="Lowest observed adverse effect level in mg/kg",
            ),
            FieldDef(
                "noael_exposure",
                "object",
                required=False,
                description="NOAEL exposure data (auc and/or cmax with units)",
            ),
            FieldDef(
                "body_weight_kg",
                "number",
                required=False,
                description="Animal body weight in kg",
            ),
            FieldDef("notes", "string", required=False, description="Free-text notes"),
        ),
    )
)

_register(
    SchemaDef(
        schema_id="dde.tox-safety-pharm.v1",
        description="Safety pharmacology input — hERG and core battery",
        fields=(
            FieldDef(
                "schema", "string", required=True, const="dde.tox-safety-pharm.v1"
            ),
            FieldDef(
                "compound_id",
                "string",
                required=True,
                description="Compound identifier",
            ),
            FieldDef(
                "herg_ic50",
                "number",
                required=True,
                description="hERG IC50 value (positive)",
            ),
            FieldDef(
                "herg_ic50_units",
                "string",
                required=True,
                enum_values=("mg/mL", "nM", "ng/mL", "uM", "ug/mL"),
                description="Concentration units for hERG IC50",
            ),
            FieldDef(
                "cardiovascular",
                "object",
                required=False,
                description="Cardiovascular findings (qtc_prolongation, blood_pressure_effect, etc.)",
            ),
            FieldDef(
                "respiratory",
                "object",
                required=False,
                description="Respiratory findings (tidal_volume_effect, respiratory_rate_effect, summary)",
            ),
            FieldDef(
                "cns",
                "object",
                required=False,
                description="CNS findings (irwin_fob_summary, findings)",
            ),
            FieldDef("notes", "string", required=False, description="Free-text notes"),
        ),
    )
)

_register(
    SchemaDef(
        schema_id="dde.tox-genotox.v1",
        description="Genotoxicity battery input — ICH S2(R1) assay results",
        fields=(
            FieldDef("schema", "string", required=True, const="dde.tox-genotox.v1"),
            FieldDef(
                "compound_id",
                "string",
                required=True,
                description="Compound identifier",
            ),
            FieldDef(
                "assays",
                "array[object]",
                required=True,
                description="Array of assay result objects (type, result, metabolic_activation)",
            ),
            FieldDef(
                "battery_complete",
                "boolean",
                required=True,
                description="Whether the ICH S2(R1) standard battery is complete",
            ),
            FieldDef("notes", "string", required=False, description="Free-text notes"),
        ),
    )
)
