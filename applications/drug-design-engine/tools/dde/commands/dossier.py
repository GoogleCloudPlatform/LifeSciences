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

"""dde dossier — IND evidence-package readiness checker and CTD exporter.

Scans the project tree (read-only) and reports which required nonclinical
study types have supporting evidence for an IND Module 4 submission.
This is a completeness checker, not a quality assessor.

The ``export`` subcommand builds CTD Module 2.6 / Module 4 summary
tables from Layer 0 artifacts, forwarding upstream relays into the
export so provenance caveats are not lost in hand-transcription.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from ..common import (
    AppState,
    DDEGroup,
    emitter,
    output_options,
    pass_state,
)
from ..core import provenance
from ..core.errors import Refusal
from ..core.paths import is_safe_to_open

# ---------------------------------------------------------------------------
# IND Module 4 checklist — small-molecule FDA IND default
# ---------------------------------------------------------------------------
# Future extension point: the checklist could be made configurable per
# jurisdiction (e.g., EMA, PMDA) by loading from an external YAML/JSON
# file or providing a --jurisdiction flag.  For now, this is hardcoded for
# a standard small-molecule FDA IND.

SCOPE_CAVEAT = (
    "A complete checklist means the required evidence types are present; "
    "it does not mean the evidence meets regulatory standards, that the "
    "studies are GLP-compliant, or that the dossier is ready for filing."
)

IND_MODULE4_CHECKLIST: list[dict[str, Any]] = [
    # -- Pharmacology -------------------------------------------------------
    # Known limitation: all three pharmacology sections share the same
    # artifact suffix (.assay.json) because the assay artifact schema does
    # not encode pharmacology sub-type.  Artifact matching therefore cannot
    # distinguish primary, secondary, and safety pharmacology at the file
    # level — a single assay artifact will satisfy all three sections.
    {
        "section": "pharmacology.primary",
        "label": "Primary Pharmacodynamics",
        "keywords": [
            "primary pharmacodynamics",
            "primary pd",
            "primary-pd",
            "primary_pd",
        ],
        "dir_hints": ["pharmacology"],
        "raw_dirs": ["assays"],
        "artifact_suffixes": [".assay.json"],
    },
    {
        "section": "pharmacology.secondary",
        "label": "Secondary Pharmacodynamics",
        "keywords": [
            "secondary pharmacodynamics",
            "secondary pd",
            "secondary-pd",
            "secondary_pd",
        ],
        "dir_hints": ["pharmacology"],
        "raw_dirs": ["assays"],
        "artifact_suffixes": [".assay.json"],
    },
    {
        "section": "pharmacology.safety",
        "label": "Safety Pharmacology",
        "keywords": [
            "safety pharmacology",
            "safety pharm",
            "safety-pharm",
            "safety_pharm",
            "safety-pharmacology",
        ],
        "dir_hints": ["pharmacology"],
        "raw_dirs": ["assays"],
        "artifact_suffixes": [".assay.json"],
    },
    # -- Pharmacokinetics ---------------------------------------------------
    {
        "section": "pk.adme",
        "label": "PK/ADME",
        "keywords": [
            "pk",
            "adme",
            "pharmacokinetics",
            "absorption",
            "distribution",
            "metabolism",
            "excretion",
        ],
        "dir_hints": ["pk", "pharmacokinetics", "adme"],
        "raw_dirs": ["pk"],
        "artifact_suffixes": [".pk-study.json", ".pk-nca.json", ".pk-scaling.json"],
    },
    {
        "section": "pk.ddi",
        "label": "Drug-Drug Interaction Studies",
        "keywords": [
            "drug-drug interaction",
            "ddi",
            "drug interaction",
            "drug_drug_interaction",
        ],
        "dir_hints": ["pk", "pharmacokinetics", "ddi"],
        "raw_dirs": ["pk"],
        "artifact_suffixes": [".pk-ddi.json"],
    },
    # -- Toxicology ---------------------------------------------------------
    {
        "section": "tox.repeat_dose",
        "label": "Repeat-Dose Toxicity",
        "keywords": [
            "repeat-dose",
            "repeat dose",
            "repeat_dose",
            "subchronic",
            "chronic toxicity",
        ],
        "dir_hints": ["tox", "toxicology", "repeat-dose"],
        "raw_dirs": ["tox"],
        "artifact_suffixes": [".tox-repeat-dose.json"],
    },
    {
        "section": "tox.safety_pharm",
        "label": "Safety Pharmacology (Toxicology)",
        "keywords": [
            "safety pharmacology",
            "safety pharm",
            "herg",
            "cardiovascular safety",
            "safety-pharmacology",
        ],
        "dir_hints": ["tox", "toxicology", "safety-pharm"],
        "raw_dirs": ["tox"],
        "artifact_suffixes": [".tox-safety-pharm.json"],
    },
    {
        "section": "tox.genotox",
        "label": "Genotoxicity",
        "keywords": [
            "genotoxicity",
            "genotox",
            "mutagenicity",
            "ames",
            "micronucleus",
            "clastogenicity",
        ],
        "dir_hints": ["tox", "toxicology", "genotox"],
        "raw_dirs": ["tox"],
        "artifact_suffixes": [".tox-genotox.json", ".tox-genotox-assessment.json"],
    },
    {
        "section": "tox.repro",
        "label": "Reproductive Toxicology",
        "keywords": [
            "reproductive",
            "repro",
            "fertility",
            "teratogenicity",
            "developmental toxicology",
        ],
        "dir_hints": ["tox", "toxicology", "repro", "reproductive"],
        "raw_dirs": ["tox"],
        "artifact_suffixes": [],  # no artifact type exists yet
        "note": "May be deferred for first-in-human IND",
    },
]


# ---------------------------------------------------------------------------
# Project tree scanning
# ---------------------------------------------------------------------------


def _scan_findings(project_root: Path) -> list[dict[str, Any]]:
    """Walk ``findings/`` and collect markdown findings with titles.

    Each result contains:
      path         relative path from project root
      title        first ``# `` heading text (or filename-derived fallback)
      dir_parts    lowercase directory components under ``findings/``
      stem         lowercase filename without extension
      content_lower  full file content, lowercased, for keyword matching
    """
    findings_dir = project_root / "findings"
    if not findings_dir.is_dir():
        return []

    root_resolved = project_root.resolve()
    results: list[dict[str, Any]] = []

    for md_file in sorted(findings_dir.rglob("*.md")):
        # Confine each file — never follow symlinks outside the project.
        if not md_file.resolve().is_relative_to(root_resolved):
            continue
        if not md_file.is_file():
            continue

        rel_path = md_file.relative_to(project_root)
        # Directory components between findings/ and the file.
        parts_under_findings = list(rel_path.parts[1:-1])

        # Parse first heading.
        title = md_file.stem.replace("-", " ").replace("_", " ").title()
        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("# "):
                    title = stripped[2:].strip()
                    break
        except OSError:
            content = ""

        results.append(
            {
                "path": str(rel_path),
                "title": title,
                "dir_parts": [p.lower() for p in parts_under_findings],
                "stem": md_file.stem.lower(),
                "content_lower": content.lower(),
            }
        )

    return results


def _scan_raw_dirs(project_root: Path) -> dict[str, list[str]]:
    """Scan ``raw/`` subdirectories and return ``{dir_name: [file_paths]}``.

    File paths are relative to the project root.  Provenance sidecars
    (``.meta.json``) are excluded — they are provenance, not evidence.
    """
    raw_dir = project_root / "raw"
    if not raw_dir.is_dir():
        return {}

    root_resolved = project_root.resolve()
    result: dict[str, list[str]] = {}

    for subdir in sorted(raw_dir.iterdir()):
        if not subdir.is_dir():
            continue
        if not subdir.resolve().is_relative_to(root_resolved):
            continue

        name = subdir.name.lower()
        files: list[str] = []
        for f in sorted(subdir.rglob("*")):
            if not f.is_file():
                continue
            if not f.resolve().is_relative_to(root_resolved):
                continue
            if f.name.endswith(".meta.json"):
                continue
            files.append(str(f.relative_to(project_root)))

        result[name] = files

    return result


# ---------------------------------------------------------------------------
# Evidence matching
# ---------------------------------------------------------------------------


def _match_section(
    section: dict[str, Any],
    findings: list[dict[str, Any]],
    raw_files: dict[str, list[str]],
) -> dict[str, Any]:
    """Match a single checklist section against scanned findings and artifacts.

    Returns a result dict with ``status`` as one of ``covered``,
    ``finding_only``, or ``missing``.
    """
    keywords = section["keywords"]
    dir_hints = section["dir_hints"]
    raw_dirs = section["raw_dirs"]

    matched_findings: list[str] = []

    # The section's local name (e.g., "repeat_dose" from "tox.repeat_dose")
    # can serve as an exact directory-name match.
    section_local = section["section"].split(".")[-1]
    section_local_variants = {
        section_local,
        section_local.replace("_", "-"),
    }

    for finding in findings:
        # Step 1: directory path must match a dir_hint.
        dir_match = any(hint in finding["dir_parts"] for hint in dir_hints)
        if not dir_match:
            continue

        # Step 2: disambiguate within the directory using keywords or
        # an exact subdirectory match on the section's local name.
        #
        # Known limitation: substring keyword matching cannot distinguish
        # positive from negative statements.  A finding stating "No DDI
        # study conducted" still matches the keyword "drug-drug interaction".
        # The artifact-suffix filter on Layer 0 files mitigates the worst
        # consequence: a section whose keyword matches a negation but has
        # no matching artifact will be classified as "finding_only" rather
        # than the previous false "covered".
        searchable = (
            f"{finding['stem']} {finding['title'].lower()} {finding['content_lower']}"
        )
        keyword_match = any(kw in searchable for kw in keywords)
        dir_exact_match = bool(section_local_variants & set(finding["dir_parts"]))

        if keyword_match or dir_exact_match:
            matched_findings.append(finding["path"])

    # Collect supporting Layer 0 artifacts from the relevant raw/ dirs,
    # filtered by the section's artifact_suffixes so that sections sharing
    # the same raw_dirs (e.g. pk.adme and pk.ddi both use raw/pk/) only
    # match the specific artifact types that constitute evidence for that
    # section.
    matched_artifacts: list[str] = []
    suffixes = section.get("artifact_suffixes", [])
    for raw_dir_name in raw_dirs:
        for f in raw_files.get(raw_dir_name, []):
            if any(f.endswith(s) for s in suffixes):
                matched_artifacts.append(f)

    # Three-way classification.
    if matched_findings and matched_artifacts:
        status = "covered"
    elif matched_findings:
        status = "finding_only"
    else:
        status = "missing"

    result: dict[str, Any] = {
        "section": section["section"],
        "label": section["label"],
        "status": status,
        "findings": sorted(matched_findings),
        "artifacts": sorted(matched_artifacts),
    }
    if "note" in section:
        result["note"] = section["note"]

    return result


def _build_report(project_root: Path) -> dict[str, Any]:
    """Scan the project tree and build the dossier readiness report."""
    findings = _scan_findings(project_root)
    raw_files = _scan_raw_dirs(project_root)

    checklist_results: list[dict[str, Any]] = []
    summary_counts: dict[str, int] = {
        "covered": 0,
        "finding_only": 0,
        "missing": 0,
    }

    for section in IND_MODULE4_CHECKLIST:
        result = _match_section(section, findings, raw_files)
        checklist_results.append(result)
        summary_counts[result["status"]] += 1

    return {
        "schema": "dde.dossier-check.v1",
        "scope_caveat": SCOPE_CAVEAT,
        "checklist": checklist_results,
        "summary": {
            "total_sections": len(IND_MODULE4_CHECKLIST),
            **summary_counts,
        },
    }


# ---------------------------------------------------------------------------
# ICH guidance references (Item 3)
# ---------------------------------------------------------------------------

ICH_GUIDANCE_REFERENCES: dict[str, list[dict[str, str]]] = {
    "2.6.2": [
        {
            "code": "ICH S7A",
            "title": "Safety Pharmacology Studies for Human Pharmaceuticals",
        },
        {
            "code": "ICH S7B",
            "title": (
                "Nonclinical Evaluation of the Potential for Delayed "
                "Ventricular Repolarization (hERG) by Human Pharmaceuticals"
            ),
        },
    ],
    "2.6.4": [
        {
            "code": "ICH S3A",
            "title": (
                "Toxicokinetics: Assessment of Systemic Exposure in Toxicity Studies"
            ),
        },
    ],
    "2.6.6": [
        {
            "code": "ICH S2(R1)",
            "title": "Guidance on Genotoxicity Testing and Data Interpretation",
        },
        {
            "code": "ICH M3(R2)",
            "title": (
                "Nonclinical Safety Studies for the Conduct of Human "
                "Clinical Trials and Marketing Authorization for "
                "Pharmaceuticals"
            ),
        },
        {
            "code": "ICH S5(R3)",
            "title": (
                "Detection of Reproductive and Developmental Toxicity "
                "for Human Pharmaceuticals"
            ),
        },
    ],
}


# ---------------------------------------------------------------------------
# CTD section → artifact mapping for export
# ---------------------------------------------------------------------------

# Maps CTD sections to the raw/ subdirectories and artifact suffixes they
# consume.  Each entry also carries a ``subsections`` list describing the
# tabular columns the export should populate.

CTD_SECTION_MAP: list[dict[str, Any]] = [
    {
        "ctd_section": "2.6.2",
        "title": "Pharmacology Tabulated Summary",
        "subsections": [
            {
                "name": "Primary Pharmacodynamics",
                "raw_dirs": ["assays"],
                "artifact_suffixes": [".assay.json", ".selectivity.json"],
                "fields": [
                    "study_type",
                    "target",
                    "species",
                    "assay_type",
                    "result",
                    "conclusion",
                ],
            },
            {
                "name": "Safety Pharmacology",
                "raw_dirs": ["tox"],
                "artifact_suffixes": [".tox-safety-pharm.json"],
                "fields": [
                    "study_type",
                    "species",
                    "route",
                    "dose",
                    "findings",
                    "conclusion",
                ],
            },
        ],
    },
    {
        "ctd_section": "2.6.4",
        "title": "Pharmacokinetics Tabulated Summary",
        "subsections": [
            {
                "name": "Absorption",
                "raw_dirs": ["pk"],
                "artifact_suffixes": [
                    ".pk-study.json",
                    ".pk-nca.json",
                ],
                "fields": [
                    "species",
                    "route",
                    "dose_mg_kg",
                    "dose_units",
                    "parameters",
                ],
            },
            {
                "name": "Distribution",
                "raw_dirs": ["pk"],
                "artifact_suffixes": [".pk-study.json"],
                "fields": [
                    "species",
                    "route",
                    "dose_mg_kg",
                    "vd",
                ],
            },
            {
                "name": "Metabolism",
                "raw_dirs": ["pk"],
                "artifact_suffixes": [".pk-study.json"],
                "fields": [
                    "species",
                    "route",
                    "metabolites",
                    "enzyme",
                ],
            },
            {
                "name": "Excretion",
                "raw_dirs": ["pk"],
                "artifact_suffixes": [".pk-study.json", ".pk-nca.json"],
                "fields": [
                    "species",
                    "route",
                    "clearance",
                    "half_life",
                ],
            },
        ],
    },
    {
        "ctd_section": "2.6.6",
        "title": "Toxicology Tabulated Summary",
        "subsections": [
            {
                "name": "Single-Dose Toxicity",
                "raw_dirs": ["tox"],
                "artifact_suffixes": [".tox-single-dose.json"],
                "fields": [
                    "species",
                    "route",
                    "dose",
                    "ld50",
                    "findings",
                    "noael",
                ],
            },
            {
                "name": "Repeat-Dose Toxicity",
                "raw_dirs": ["tox"],
                "artifact_suffixes": [".tox-repeat-dose.json"],
                "fields": [
                    "species",
                    "route",
                    "duration",
                    "dose",
                    "noael",
                    "loael",
                    "findings",
                ],
            },
            {
                "name": "Genotoxicity",
                "raw_dirs": ["tox"],
                "artifact_suffixes": [
                    ".tox-genotox.json",
                    ".tox-genotox-assessment.json",
                ],
                "fields": [
                    "test_system",
                    "result",
                    "conclusion",
                ],
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# Relay codes that MUST propagate through the export
# ---------------------------------------------------------------------------

_MUST_PROPAGATE_RELAY_CODES: set[str] = {
    "pk.single_species_scaling",
    "selectivity.ratio_not_affinity",
    "pk.linear_exposure_assumed",
    "tox.margin_indeterminate",
    "admet.prediction_not_measurement",
}


# ---------------------------------------------------------------------------
# Export builder
# ---------------------------------------------------------------------------


def _read_artifact(path: Path) -> dict[str, Any] | None:
    """Read a JSON artifact, returning None on any failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _extract_fields(
    artifact: dict[str, Any],
    fields: list[str],
    artifact_path: str,
    work_order_id: str | None,
) -> dict[str, Any]:
    """Extract requested fields from an artifact, labelling gaps."""
    row: dict[str, Any] = {}
    for field in fields:
        value = artifact.get(field)
        if value is not None:
            row[field] = value
        else:
            row[field] = "NOT AVAILABLE — field not present in source artifact"
    row["source"] = {
        "artifact_path": artifact_path,
        "work_order_id": work_order_id or "NOT AVAILABLE — no work order",
    }
    return row


def _collect_relays_from_artifact(
    artifact_path: Path,
    project_root: Path,
) -> list[dict[str, Any]]:
    """Collect mandatory relays from sidecars and analysis records near an artifact."""
    relays: list[dict[str, Any]] = []

    # Check for .meta.json sidecar beside the artifact.
    meta_path = artifact_path.with_suffix(".meta.json")
    if not meta_path.is_file():
        # Try the <stem>.meta.json pattern (for .something.json artifacts).
        meta_path = artifact_path.parent / (
            artifact_path.name.rsplit(".json", 1)[0] + ".meta.json"
        )

    if meta_path.is_file():
        if not is_safe_to_open(meta_path):
            raise Refusal(f"Refusing to read through symlink: {meta_path}")
        meta = _read_artifact(meta_path)
        if meta and isinstance(meta.get("mandatory_relays"), list):
            for r in meta["mandatory_relays"]:
                if isinstance(r, dict) and "code" in r:
                    relays.append(
                        {
                            "code": r["code"],
                            "message": r.get("message", ""),
                            "source_artifact": str(
                                artifact_path.relative_to(project_root)
                            ),
                        }
                    )

    # Check for .analysis.json beside the artifact.
    analysis_path = artifact_path.with_name(
        artifact_path.name.rsplit(".json", 1)[0] + ".analysis.json"
    )
    if analysis_path.is_file():
        analysis = _read_artifact(analysis_path)
        if analysis and isinstance(analysis.get("mandatory_relays"), list):
            for r in analysis["mandatory_relays"]:
                if isinstance(r, dict) and "code" in r:
                    relays.append(
                        {
                            "code": r["code"],
                            "message": r.get("message", ""),
                            "source_artifact": str(
                                artifact_path.relative_to(project_root)
                            ),
                        }
                    )

    return relays


def _build_export(
    project_root: Path,
    work_order_id: str | None,
) -> dict[str, Any]:
    """Build the CTD Module 2.6 / Module 4 summary tables.

    Scans ``raw/`` directories for Layer 0 artifacts, maps them to CTD
    sections, and assembles structured summary tables.  Uncovered
    sections are emitted with explicit NOT_AVAILABLE status.
    """
    raw_dir = project_root / "raw"
    root_resolved = project_root.resolve()
    all_relays: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []

    for section_def in CTD_SECTION_MAP:
        ctd_section = section_def["ctd_section"]
        subsection_results: list[dict[str, Any]] = []

        for subsection in section_def["subsections"]:
            entries: list[dict[str, Any]] = []
            matched_paths: list[Path] = []

            for raw_dir_name in subsection["raw_dirs"]:
                scan_dir = raw_dir / raw_dir_name
                if not scan_dir.is_dir():
                    continue
                for f in sorted(scan_dir.rglob("*.json")):
                    if not f.is_file():
                        continue
                    if f.name.endswith(".meta.json"):
                        continue
                    if f.name.endswith(".analysis.json"):
                        continue
                    if not f.resolve().is_relative_to(root_resolved):
                        continue
                    if not any(
                        f.name.endswith(sfx) for sfx in subsection["artifact_suffixes"]
                    ):
                        continue

                    artifact = _read_artifact(f)
                    if artifact is None:
                        continue

                    rel_path = str(f.relative_to(project_root))
                    wo_id = artifact.get("work_order_id", work_order_id)

                    # Skip artifacts belonging to a different work order.
                    if (
                        work_order_id is not None
                        and artifact.get("work_order_id") is not None
                        and artifact.get("work_order_id") != work_order_id
                    ):
                        continue

                    row = _extract_fields(
                        artifact,
                        subsection["fields"],
                        rel_path,
                        wo_id,
                    )
                    entries.append(row)
                    matched_paths.append(f)

            # Collect relays from matched artifacts.
            for p in matched_paths:
                art_relays = _collect_relays_from_artifact(p, project_root)
                all_relays.extend(art_relays)

            if entries:
                subsection_results.append(
                    {
                        "name": subsection["name"],
                        "status": "POPULATED",
                        "entries": entries,
                    }
                )
            else:
                subsection_results.append(
                    {
                        "name": subsection["name"],
                        "status": "NOT_AVAILABLE",
                        "reason": "No source artifact found",
                        "entries": [],
                    }
                )

        sections.append(
            {
                "ctd_section": ctd_section,
                "title": section_def["title"],
                "guidance_references": ICH_GUIDANCE_REFERENCES.get(ctd_section, []),
                "subsections": subsection_results,
            }
        )

    # Deduplicate relays by (code, source_artifact).
    seen_relay_keys: set[tuple[str, str]] = set()
    unique_relays: list[dict[str, Any]] = []
    for r in all_relays:
        key = (r["code"], r["source_artifact"])
        if key not in seen_relay_keys:
            seen_relay_keys.add(key)
            unique_relays.append(r)

    # Filter to must-propagate relays plus any prediction_not_measurement.
    forwarded_relays: list[dict[str, Any]] = []
    for r in unique_relays:
        code = r["code"]
        if code in _MUST_PROPAGATE_RELAY_CODES or "prediction_not_measurement" in code:
            forwarded_relays.append(r)

    # Also include all other relays — the must-propagate set is a
    # minimum, not a filter.  All upstream relays are forwarded so
    # provenance is not lost.
    other_relays = [r for r in unique_relays if r not in forwarded_relays]
    forwarded_relays.extend(other_relays)

    return {
        "schema": "dde.dossier-export.v1",
        "work_order_id": work_order_id,
        "format": "ctd",
        "scope_caveat": SCOPE_CAVEAT,
        "sections": sections,
        "relays": forwarded_relays,
        "relay_count": len(forwarded_relays),
    }


# ---------------------------------------------------------------------------
# Export formatters
# ---------------------------------------------------------------------------


def _export_to_json(export: dict[str, Any]) -> str:
    """Render the export as indented JSON."""
    return json.dumps(export, indent=2) + "\n"


def _export_to_tsv(export: dict[str, Any]) -> str:
    """Render the export as tab-separated values."""
    lines: list[str] = []

    for section in export["sections"]:
        lines.append(f"# {section['ctd_section']} — {section['title']}")
        refs = section.get("guidance_references", [])
        if refs:
            ref_str = ", ".join(r["code"] for r in refs)
            lines.append(f"# Guidance: {ref_str}")

        for subsection in section["subsections"]:
            lines.append(f"## {subsection['name']}")
            if subsection["status"] == "NOT_AVAILABLE":
                lines.append(
                    f"NOT_AVAILABLE\t{subsection.get('reason', 'No source artifact found')}"
                )
                continue

            entries = subsection["entries"]
            if not entries:
                continue

            # Collect all field keys across entries (excluding source).
            all_keys: list[str] = []
            for entry in entries:
                for k in entry:
                    if k != "source" and k not in all_keys:
                        all_keys.append(k)
            all_keys.append("source_path")

            lines.append("\t".join(all_keys))
            for entry in entries:
                row_values: list[str] = []
                for k in all_keys:
                    if k == "source_path":
                        src = entry.get("source", {})
                        row_values.append(str(src.get("artifact_path", "")))
                    else:
                        val = entry.get(k, "")
                        if isinstance(val, (dict, list)):
                            val = json.dumps(val)
                        row_values.append(str(val))
                lines.append("\t".join(row_values))

        lines.append("")

    if export.get("relays"):
        lines.append("# Forwarded Relays")
        lines.append("code\tmessage\tsource_artifact")
        for r in export["relays"]:
            lines.append(f"{r['code']}\t{r['message']}\t{r['source_artifact']}")

    return "\n".join(lines) + "\n"


def _export_to_markdown(export: dict[str, Any]) -> str:
    """Render the export as human-readable Markdown."""
    lines: list[str] = []

    lines.append("# CTD Module 2.6 / Module 4 Summary Tables")
    lines.append("")
    if export.get("work_order_id"):
        lines.append(f"**Work Order:** {export['work_order_id']}")
        lines.append("")
    lines.append(f"> {export['scope_caveat']}")
    lines.append("")

    for section in export["sections"]:
        lines.append(f"## {section['ctd_section']} {section['title']}")
        lines.append("")

        refs = section.get("guidance_references", [])
        if refs:
            ref_parts = [f"{r['code']} ({r['title']})" for r in refs]
            lines.append(f"**Guidance:** {'; '.join(ref_parts)}")
            lines.append("")

        for subsection in section["subsections"]:
            lines.append(f"### {subsection['name']}")
            lines.append("")

            if subsection["status"] == "NOT_AVAILABLE":
                reason = subsection.get("reason", "No source artifact found")
                lines.append(f"*NOT AVAILABLE — {reason}*")
                lines.append("")
                continue

            entries = subsection["entries"]
            if not entries:
                lines.append("*No entries.*")
                lines.append("")
                continue

            # Build a Markdown table.
            all_keys: list[str] = []
            for entry in entries:
                for k in entry:
                    if k != "source" and k not in all_keys:
                        all_keys.append(k)

            header = "| " + " | ".join(all_keys) + " | Source |"
            separator = "| " + " | ".join("---" for _ in all_keys) + " | --- |"
            lines.append(header)
            lines.append(separator)

            for entry in entries:
                cells: list[str] = []
                for k in all_keys:
                    val = entry.get(k, "")
                    if isinstance(val, (dict, list)):
                        val = json.dumps(val)
                    cells.append(str(val).replace("|", "\\|"))
                src = entry.get("source", {})
                source_str = str(src.get("artifact_path", "")).replace("|", "\\|")
                cells.append(source_str)
                lines.append("| " + " | ".join(cells) + " |")

            lines.append("")

    if export.get("relays"):
        lines.append("## Forwarded Relays")
        lines.append("")
        lines.append(f"**{len(export['relays'])} upstream relay(s) forwarded.**")
        lines.append("")
        lines.append("| Code | Message | Source Artifact |")
        lines.append("| --- | --- | --- |")
        for r in export["relays"]:
            code = str(r["code"]).replace("|", "\\|")
            msg = str(r["message"]).replace("|", "\\|")
            src = str(r["source_artifact"]).replace("|", "\\|")
            lines.append(f"| {code} | {msg} | {src} |")
        lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.group(cls=DDEGroup)
def dossier() -> None:
    """IND evidence-package readiness checker and CTD exporter."""


@dossier.command("check")
@output_options
@pass_state
def check_cmd(state: AppState, as_json: bool, quiet: bool) -> None:
    """Scan the project for IND Module 4 evidence coverage.

    Walks ``findings/`` for Layer 1 evidence and ``raw/`` for Layer 0
    artifacts, then classifies each required nonclinical study type as
    *covered*, *finding_only*, or *missing*.

    Results are written to ``gates/dossier-check/``.  This command is
    read-only with respect to ``findings/`` and ``raw/``.
    """
    emit = emitter(as_json, quiet)
    project = state.project()

    report = _build_report(project.root)

    # Write output to gates/dossier-check/.
    gates_dir = project.root / "gates" / "dossier-check"
    gates_dir.mkdir(parents=True, exist_ok=True)

    output_path = gates_dir / "dossier-check.json"
    if not is_safe_to_open(output_path):
        raise Refusal(f"Refusing to write through symlink: {output_path}")
    output_path.write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )

    # Provenance sidecar.
    sidecar = provenance.Sidecar(
        tool="dossier",
        subcommand="check",
        parameters={},
    )
    sidecar.add_output(output_path)
    sidecar.note("scope_caveat", SCOPE_CAVEAT)
    sidecar_path = gates_dir / "dossier-check.meta.json"
    if not is_safe_to_open(sidecar_path):
        raise Refusal(f"Refusing to write through symlink: {sidecar_path}")
    sidecar.write(sidecar_path)

    # -- stdout output ------------------------------------------------------
    summary = report["summary"]

    # JSON mode: mirror the report structure.
    emit.data("schema", report["schema"])
    emit.data("scope_caveat", report["scope_caveat"])
    emit.data("checklist", report["checklist"])
    emit.data("summary", report["summary"])

    # Human-readable summary.
    emit.line(f"IND Module 4 Readiness — {summary['total_sections']} sections")
    emit.line(f"  covered:      {summary['covered']}")
    emit.line(f"  finding_only: {summary['finding_only']}")
    emit.line(f"  missing:      {summary['missing']}")
    emit.line()

    status_marks = {
        "covered": "[OK]",
        "finding_only": "[PARTIAL]",
        "missing": "[MISSING]",
    }
    for item in report["checklist"]:
        mark = status_marks.get(item["status"], "[?]")
        line = f"  {mark:10s} {item['label']}: {item['status']}"
        if item.get("note"):
            line += f"  ({item['note']})"
        emit.line(line)

    emit.path(output_path, role="report")
    emit.path(sidecar_path, role="sidecar")
    emit.flush()


@dossier.command("export")
@click.argument("wo_id")
@click.option(
    "--format",
    "fmt",
    default="ctd",
    show_default=True,
    help="Export format (currently only 'ctd' is supported).",
)
@click.option(
    "--output-format",
    "output_fmt",
    default="json",
    type=click.Choice(["json", "tsv", "markdown"], case_sensitive=False),
    show_default=True,
    help="Output rendering: json (structured), tsv (tabular), markdown.",
)
@output_options
@pass_state
def export_cmd(
    state: AppState,
    wo_id: str,
    fmt: str,
    output_fmt: str,
    as_json: bool,
    quiet: bool,
) -> None:
    """Export CTD Module 2.6 / Module 4 summary tables for a work order.

    Scans ``raw/`` for Layer 0 artifacts, maps them into CTD tabulated
    summaries (2.6.2 Pharmacology, 2.6.4 Pharmacokinetics, 2.6.6
    Toxicology), and forwards upstream relays so that provenance caveats
    reach the regulatory submission.

    Sections without supporting artifacts are emitted with explicit
    NOT_AVAILABLE status — never silently omitted.

    \b
    Output is written to ``gates/dossier-export/``.
    """
    emit = emitter(as_json, quiet)
    project = state.project()

    export = _build_export(project.root, wo_id)

    # Write output to gates/dossier-export/.
    gates_dir = project.root / "gates" / "dossier-export"
    gates_dir.mkdir(parents=True, exist_ok=True)

    # Always write the canonical JSON for provenance.
    json_path = gates_dir / "dossier-export.json"
    json_path.write_text(
        json.dumps(export, indent=2) + "\n",
        encoding="utf-8",
    )

    # Write the requested output format.
    _FORMATTERS = {
        "json": (_export_to_json, ".json"),
        "tsv": (_export_to_tsv, ".tsv"),
        "markdown": (_export_to_markdown, ".md"),
    }
    formatter, ext = _FORMATTERS[output_fmt]
    rendered = formatter(export)

    if output_fmt != "json":
        rendered_path = gates_dir / f"dossier-export{ext}"
        rendered_path.write_text(rendered, encoding="utf-8")
    else:
        rendered_path = json_path

    # Provenance sidecar.
    sidecar = provenance.Sidecar(
        tool="dossier",
        subcommand="export",
        parameters={"work_order_id": wo_id, "format": fmt, "output_format": output_fmt},
    )
    sidecar.add_output(json_path)
    if rendered_path != json_path:
        sidecar.add_output(rendered_path)
    sidecar.note("scope_caveat", SCOPE_CAVEAT)

    # Fire relay for forwarded upstream relays.
    relay_count = export["relay_count"]
    if relay_count > 0:
        sidecar.warn(
            f"{relay_count} upstream relay(s) forwarded into CTD export. "
            "Review the relays section for caveats that affect regulatory "
            "interpretation.",
            code="dossier.relays_forwarded",
        )

    sidecar_path = gates_dir / "dossier-export.meta.json"
    sidecar.write(sidecar_path)

    # -- stdout output ------------------------------------------------------
    emit.data("schema", export["schema"])
    emit.data("work_order_id", export["work_order_id"])
    emit.data("sections", export["sections"])
    emit.data("relays", export["relays"])
    emit.data("relay_count", export["relay_count"])

    # Human-readable summary.
    section_count = len(export["sections"])
    populated = sum(
        1
        for s in export["sections"]
        for ss in s["subsections"]
        if ss["status"] == "POPULATED"
    )
    not_available = sum(
        1
        for s in export["sections"]
        for ss in s["subsections"]
        if ss["status"] == "NOT_AVAILABLE"
    )
    emit.line(f"CTD Export — {section_count} sections, {relay_count} relay(s)")
    emit.line(f"  populated:     {populated}")
    emit.line(f"  not_available: {not_available}")
    emit.line()

    for section in export["sections"]:
        emit.line(f"  [{section['ctd_section']}] {section['title']}")
        for ss in section["subsections"]:
            status = ss["status"]
            mark = "[OK]" if status == "POPULATED" else "[GAP]"
            entry_count = len(ss.get("entries", []))
            emit.line(f"    {mark:6s} {ss['name']}: {entry_count} entries")

    if relay_count > 0:
        emit.line()
        emit.line(f"  {relay_count} upstream relay(s) forwarded:")
        for r in export["relays"]:
            emit.line(f"    - {r['code']} (from {r['source_artifact']})")

    emit.path(json_path, role="export")
    emit.path(sidecar_path, role="sidecar")
    if rendered_path != json_path:
        emit.path(rendered_path, role="rendered")
    emit.flush()
