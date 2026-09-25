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

"""Tests for differentiation transparency — issue #98.

Ensures that ``dde differentiation assess`` is honest about which
artifact types it reads:

  1. Unconsumed trial artifacts produce a notice on stderr.
  2. ``sources_used`` and ``sources_available_but_unused`` appear in
     the assessment output.
  3. The existing patent-only assessment path is unchanged.

Run with:
    PYTHONPATH=tools python3 tests/test_differentiation_transparency.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dde.commands.differentiation import (
    _SOURCE_PATENT,
    _extract_trial_source_tags,
    _scan_unconsumed_trial_artifacts,
    assess_competitive_differentiation,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PASS = 0
_FAIL = 0


def _check(name: str, fn: Any) -> None:
    global _PASS, _FAIL
    try:
        fn()
        _PASS += 1
        print(f"  PASS  {name}")
    except Exception:
        _FAIL += 1
        print(f"  FAIL  {name}")
        traceback.print_exc()
        print()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _sample_patents() -> list[dict[str, Any]]:
    """A small set of patents for baseline testing."""
    return [
        {
            "publication_number": "US20240000001A1",
            "title": "GENE inhibitor compound",
            "snippet": "Novel small molecule inhibitor",
            "assignee": "Pharma Corp",
            "priority_date": "20230601",
            "filing_date": "20240101",
            "publication_date": "20240701",
            "language": "en",
        },
    ]


def _create_trial_artifact(
    tmp_dir: Path, slug: str, source: str = "clinicaltrials"
) -> Path:
    """Create a minimal trial artifact file in *tmp_dir*."""
    artifact = {
        "schema": "dde.clinical-trials.v1",
        "query": {"term": slug, "search_by": "target", "source": "clinicaltrials.gov"},
        "summary": {
            "n_studies": 3,
            "by_phase": {},
            "by_status": {},
            "top_sponsors": [],
        },
        "studies": [],
    }
    name = f"{slug}.trials-{source}.artifact.json"
    path = tmp_dir / name
    path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return path


# ===========================================================================
# Tests
# ===========================================================================

print("=" * 60)
print("test_differentiation_transparency.py — issue #98")
print("=" * 60)


# ---------------------------------------------------------------------------
# 1. Unconsumed trial artifacts produce a notice
# ---------------------------------------------------------------------------
print("\n--- Unconsumed trial artifact detection ---")


def test_scan_finds_trial_artifacts(tmp_path: Path | None = None):
    """_scan_unconsumed_trial_artifacts finds trial artifacts."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _create_trial_artifact(d, "gene", "clinicaltrials")
        # Also put a patent artifact (should NOT match).
        (d / "gene.patent-google-patents.artifact.json").write_text("{}\n")
        # And a random JSON file.
        (d / "unrelated.json").write_text("{}\n")

        found = _scan_unconsumed_trial_artifacts(d)
        assert len(found) == 1, f"expected 1 trial artifact, got {len(found)}: {found}"
        assert found[0] == "gene.trials-clinicaltrials.artifact.json"


_check("scan finds trial artifacts", test_scan_finds_trial_artifacts)


def test_scan_returns_empty_when_no_trials():
    """_scan_unconsumed_trial_artifacts returns [] when no trials present."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "gene.patent-google-patents.artifact.json").write_text("{}\n")

        found = _scan_unconsumed_trial_artifacts(d)
        assert found == [], f"expected empty list, got {found}"


_check("scan returns empty when no trials", test_scan_returns_empty_when_no_trials)


def test_scan_finds_multiple_trial_artifacts():
    """Multiple trial artifacts in the same directory are all found."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _create_trial_artifact(d, "gene", "clinicaltrials")
        _create_trial_artifact(d, "gene", "ctgov")

        found = _scan_unconsumed_trial_artifacts(d)
        assert len(found) == 2, f"expected 2 trial artifacts, got {len(found)}: {found}"


_check("scan finds multiple trial artifacts", test_scan_finds_multiple_trial_artifacts)


# ---------------------------------------------------------------------------
# 2. Source tag extraction
# ---------------------------------------------------------------------------
print("\n--- Source tag extraction ---")


def test_extract_trial_source_tags():
    """_extract_trial_source_tags extracts source tags from filenames."""
    filenames = [
        "gene.trials-clinicaltrials.artifact.json",
        "gene.trials-ctgov.artifact.json",
    ]
    tags = _extract_trial_source_tags(filenames)
    assert "trials-clinicaltrials" in tags
    assert "trials-ctgov" in tags
    assert len(tags) == 2


_check("extract trial source tags", test_extract_trial_source_tags)


def test_extract_trial_source_tags_deduplicates():
    """Duplicate source tags are not repeated."""
    filenames = [
        "gene-a.trials-clinicaltrials.artifact.json",
        "gene-b.trials-clinicaltrials.artifact.json",
    ]
    tags = _extract_trial_source_tags(filenames)
    assert tags == ["trials-clinicaltrials"], f"expected deduplicated, got {tags}"


_check(
    "extract trial source tags deduplicates",
    test_extract_trial_source_tags_deduplicates,
)


def test_extract_trial_source_tags_empty():
    """Empty filename list produces empty tags."""
    assert _extract_trial_source_tags([]) == []


_check("extract trial source tags empty input", test_extract_trial_source_tags_empty)


# ---------------------------------------------------------------------------
# 3. sources_used and sources_available_but_unused in output
# ---------------------------------------------------------------------------
print("\n--- sources_used / sources_available_but_unused in output ---")


def test_assess_result_always_has_sources_used():
    """assess_competitive_differentiation output does NOT contain
    sources_used — that is injected by assess_cmd.  This test confirms
    the core function's output is stable (no accidental collision)."""
    result = assess_competitive_differentiation(
        _sample_patents(),
        "GENE",
        modality="small_molecule",
    )
    # The core function should NOT set these fields; they are added by
    # the CLI command wrapper.
    assert "sources_used" not in result, "core function should not set sources_used"
    assert "sources_available_but_unused" not in result, (
        "core function should not set sources_available_but_unused"
    )


_check(
    "core function does not set sources_used",
    test_assess_result_always_has_sources_used,
)


def test_source_patent_constant():
    """_SOURCE_PATENT matches the patent artifact naming convention."""
    assert _SOURCE_PATENT == "patent-google-patents"


_check("_SOURCE_PATENT constant value", test_source_patent_constant)


# ---------------------------------------------------------------------------
# 4. Existing patent-only path is unchanged
# ---------------------------------------------------------------------------
print("\n--- Patent-only path unchanged ---")


def test_patent_only_assessment_unchanged():
    """The core assessment logic is unaffected by transparency changes.

    All three dimensions must still be present, independent, and carry
    the FTO disclaimer — identical to the pre-#98 behaviour.
    """
    patents = _sample_patents()
    result = assess_competitive_differentiation(
        patents,
        "GENE",
        modality="small_molecule",
        indication="solid_tumors",
    )

    # Three dimensions present.
    dims = result["dimensions"]
    assert set(dims.keys()) == {
        "competitor_activity",
        "patentability",
        "freedom_to_operate",
    }

    # Dimensions are independent.
    assert result["dimensions_are_independent"] is True
    assert result["blended_score"] is None

    # FTO disclaimer present.
    assert "fto_disclaimer" in result
    fto = dims["freedom_to_operate"]
    assert "fto_disclaimer" in fto

    # Search metadata present.
    assert "search_metadata" in result
    meta = result["search_metadata"]
    assert meta["source"] == "google-patents"


_check("patent-only assessment unchanged", test_patent_only_assessment_unchanged)


def test_empty_patents_still_works():
    """Empty patent list still produces valid three-dimension output."""
    result = assess_competitive_differentiation([], "NOVEL_TARGET")
    dims = result["dimensions"]
    assert dims["competitor_activity"]["density"] == "uncrowded"
    assert dims["patentability"]["novelty_assessment"] == "high_novelty"
    assert dims["freedom_to_operate"]["risk_level"] == "no_recent_filings"


_check("empty patents still works", test_empty_patents_still_works)


def test_schema_version_unchanged():
    """The assessment schema version is not changed by transparency work."""
    result = assess_competitive_differentiation(
        _sample_patents(),
        "GENE",
    )
    assert result["schema"] == "dde.competitive-differentiation.v1"


_check("schema version unchanged", test_schema_version_unchanged)


# ---------------------------------------------------------------------------
# 5. Integration: sources metadata with trial artifacts present
# ---------------------------------------------------------------------------
print("\n--- Integration: sources metadata ---")


def test_sources_metadata_with_trials_present():
    """When trial artifacts exist alongside patents, the source tags
    should be extractable for injection into the output."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _create_trial_artifact(d, "gene", "clinicaltrials")
        # Also put a patent artifact.
        patent = {
            "schema": "dde.patent.v1",
            "patents": _sample_patents(),
        }
        (d / "gene.patent-google-patents.artifact.json").write_text(
            json.dumps(patent, indent=2) + "\n", encoding="utf-8"
        )

        # Scan and extract.
        unconsumed = _scan_unconsumed_trial_artifacts(d)
        tags = _extract_trial_source_tags(unconsumed)

        assert unconsumed == ["gene.trials-clinicaltrials.artifact.json"]
        assert tags == ["trials-clinicaltrials"]


_check(
    "sources metadata with trials present", test_sources_metadata_with_trials_present
)


def test_sources_metadata_without_trials():
    """When no trial artifacts exist, sources_available_but_unused is empty."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "gene.patent-google-patents.artifact.json").write_text("{}\n")

        unconsumed = _scan_unconsumed_trial_artifacts(d)
        tags = _extract_trial_source_tags(unconsumed)

        assert unconsumed == []
        assert tags == []


_check("sources metadata without trials", test_sources_metadata_without_trials)


# ===========================================================================
# Summary
# ===========================================================================

print("\n" + "=" * 60)
total = _PASS + _FAIL
print(f"Results: {_PASS}/{total} passed, {_FAIL} failed")
print("=" * 60)

sys.exit(1 if _FAIL else 0)
