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

"""Tests for capability state stamping and adopted-set strategy correctness (#153).

Covers:
  1. Adopted-set analyze does NOT fire hypothesis.strategy_fallback relay
     ("adopted" IS the strategy — not a fallback from a missing tournament)
  2. Adopted-set assessment does NOT contain strategy_requested/strategy_used
  3. Capability snapshot captures current state from get_capability_snapshot()
  4. Capability snapshot is stamped into analysis records
  5. Capability snapshot is stamped into sidecars (adopt)
  6. Adopt sidecar does NOT contain strategy fallback fields
  7. Doctor --json output includes capability_snapshot
  8. hypothesis.strategy_fallback is registered in RELAY_CODES
  9. select_strategy returns None fallback_info for available strategies
  10. Old sidecars without capability_state are treated as unknown (schema
      migration)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Ensure the tools package is importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from dde.core.provenance import RELAY_CODES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(base: Path) -> Path:
    """Create a minimal dde project directory for CliRunner tests."""
    project = base / "test-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    (project / "raw" / "hypotheses").mkdir(parents=True, exist_ok=True)
    return project


def _make_hypothesis_set(project: Path, slug: str = "test-hyps") -> Path:
    """Write a minimal hypothesis set JSON file."""
    hyps = [
        {"statement": "Hypothesis A is testable."},
        {"statement": "Hypothesis B is testable."},
        {"statement": "Hypothesis C is testable."},
    ]
    path = project / f"{slug}.json"
    path.write_text(json.dumps(hyps, indent=2) + "\n", encoding="utf-8")
    return path


def _adopt_hypothesis_set(project: Path, slug: str = "test-hyps") -> Path:
    """Adopt a hypothesis set and return the normalised artifact path."""
    from click.testing import CliRunner
    from dde.cli import cli

    source_path = _make_hypothesis_set(project, slug)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--project",
            str(project),
            "hypothesis",
            "adopt",
            str(source_path),
            "--origin",
            "charter",
            "--attest",
            "Test attestation for unit tests",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, (
        f"adopt failed with exit {result.exit_code}\n{result.output}"
    )
    adopted = project / "raw" / "hypotheses" / f"{slug}.charter.json"
    assert adopted.is_file(), f"Adopted artifact not found: {adopted}"
    return adopted


# ---------------------------------------------------------------------------
# 1. Adopted-set analyze does NOT fire strategy_fallback relay
# ---------------------------------------------------------------------------


def test_adopted_set_analyze_no_strategy_fallback_relay() -> None:
    """Adopted-set analyze must NOT fire the strategy_fallback relay.

    "adopted" IS the strategy — it is a deliberate choice made by
    sponsor judgment, not a fallback from a tournament that was never
    being run.  The absence of hypex binaries is irrelevant to a set
    that entered the project through attestation.
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        adopted = _adopt_hypothesis_set(project)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "analyze",
                str(adopted),
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"analyze failed with exit {result.exit_code}\n{result.output}"
        )

        analysis_path = project / "raw" / "hypotheses" / "test-hyps.analysis.json"
        assert analysis_path.is_file(), f"Analysis not found: {analysis_path}"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))

        relay_codes = [r["code"] for r in analysis.get("mandatory_relays", [])]
        assert "hypothesis.strategy_fallback" not in relay_codes, (
            f"hypothesis.strategy_fallback must NOT fire for adopted sets; "
            f"got codes: {relay_codes}"
        )
    print("  PASS: adopted-set analyze does NOT fire strategy_fallback relay")


# ---------------------------------------------------------------------------
# 2. Adopted-set assessment does NOT contain fallback fields
# ---------------------------------------------------------------------------


def test_adopted_set_no_fallback_fields_in_assessment() -> None:
    """Adopted-set analysis must NOT contain strategy_requested/strategy_used.

    An adopted-set analysis records strategy: "adopted" and nothing
    else strategy-related.  The fields strategy_requested,
    strategy_used, and strategy_fallback only apply to tournament
    workflows.
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        adopted = _adopt_hypothesis_set(project)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "analyze",
                str(adopted),
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"analyze failed with exit {result.exit_code}\n{result.output}"
        )

        analysis_path = project / "raw" / "hypotheses" / "test-hyps.analysis.json"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        assessment = analysis.get("assessment", {})

        assert assessment.get("strategy") == "adopted", (
            f"strategy should be 'adopted'; got: {assessment.get('strategy')!r}"
        )
        assert "strategy_requested" not in assessment, (
            f"strategy_requested must NOT be in adopted-set assessment; "
            f"got: {assessment.get('strategy_requested')!r}"
        )
        assert "strategy_used" not in assessment, (
            f"strategy_used must NOT be in adopted-set assessment; "
            f"got: {assessment.get('strategy_used')!r}"
        )
        assert "strategy_fallback" not in assessment, (
            f"strategy_fallback must NOT be in adopted-set assessment; "
            f"got: {assessment.get('strategy_fallback')!r}"
        )
    print("  PASS: adopted-set assessment has no fallback fields")


# ---------------------------------------------------------------------------
# 3. Capability snapshot captures current state
# ---------------------------------------------------------------------------


def test_capability_snapshot_captures_state() -> None:
    """get_capability_snapshot returns a dict of capability statuses."""
    from dde.commands.doctor import get_capability_snapshot

    snapshot = get_capability_snapshot()

    assert isinstance(snapshot, dict), (
        f"snapshot should be a dict; got {type(snapshot).__name__}"
    )

    # Must contain the provisioned binaries
    from dde.commands.doctor import _PROVISIONED_BINARIES

    for binary in _PROVISIONED_BINARIES:
        assert binary in snapshot, (
            f"snapshot should contain {binary!r}; keys: {list(snapshot.keys())}"
        )
        assert snapshot[binary] in ("available", "unavailable", "degraded"), (
            f"{binary} status should be available/unavailable/degraded; "
            f"got {snapshot[binary]!r}"
        )

    # Must contain credential checks
    assert "alphagenome_credential" in snapshot
    assert "gcp_credential" in snapshot
    assert snapshot["alphagenome_credential"] in ("available", "unavailable")
    assert snapshot["gcp_credential"] in ("available", "unavailable")
    print("  PASS: capability snapshot captures current state")


# ---------------------------------------------------------------------------
# 4. Capability snapshot is stamped into analysis records
# ---------------------------------------------------------------------------


def test_capability_snapshot_in_analysis() -> None:
    """Analysis records include capability_state."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        adopted = _adopt_hypothesis_set(project)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "analyze",
                str(adopted),
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"analyze failed with exit {result.exit_code}\n{result.output}"
        )

        analysis_path = project / "raw" / "hypotheses" / "test-hyps.analysis.json"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))

        assert "capability_state" in analysis, (
            f"analysis should contain capability_state; keys: {list(analysis.keys())}"
        )
        cap_state = analysis["capability_state"]
        assert isinstance(cap_state, dict), (
            f"capability_state should be a dict; got {type(cap_state).__name__}"
        )
        assert "hypex" in cap_state, (
            f"capability_state should contain 'hypex'; keys: {list(cap_state.keys())}"
        )
    print("  PASS: capability snapshot in analysis records")


# ---------------------------------------------------------------------------
# 5. Capability snapshot is stamped into sidecars (adopt)
# ---------------------------------------------------------------------------


def test_capability_snapshot_in_sidecar() -> None:
    """Adopt sidecar includes capability_state."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        source_path = _make_hypothesis_set(project)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(source_path),
                "--origin",
                "sponsor",
                "--attest",
                "Test attestation",
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"adopt failed with exit {result.exit_code}\n{result.output}"
        )

        meta_path = project / "raw" / "hypotheses" / "test-hyps.adopted.meta.json"
        assert meta_path.is_file(), f"Sidecar not found: {meta_path}"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        assert "capability_state" in meta, (
            f"sidecar should contain capability_state; keys: {list(meta.keys())}"
        )
        cap_state = meta["capability_state"]
        assert isinstance(cap_state, dict)
        assert "hypex" in cap_state
    print("  PASS: capability snapshot in sidecar")


# ---------------------------------------------------------------------------
# 6. Adopt sidecar does NOT record strategy fallback info
# ---------------------------------------------------------------------------


def test_adopt_sidecar_no_fallback_fields() -> None:
    """Adopt sidecar must NOT contain strategy fallback fields.

    Adoption is the strategy — there is nothing to fall back from.
    The sidecar should NOT contain strategy_requested, strategy_used,
    fallback_reason, or the hypothesis.strategy_fallback relay.
    capability_state is still recorded (informational).
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))
        source_path = _make_hypothesis_set(project)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "adopt",
                str(source_path),
                "--origin",
                "charter",
                "--attest",
                "Test attestation",
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"adopt failed with exit {result.exit_code}\n{result.output}"
        )

        meta_path = project / "raw" / "hypotheses" / "test-hyps.charter.meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        # Adoption is deliberate — no fallback fields
        assert "strategy_requested" not in meta, (
            f"strategy_requested must NOT be in adopt sidecar; "
            f"got: {meta.get('strategy_requested')!r}"
        )
        assert "strategy_used" not in meta, (
            f"strategy_used must NOT be in adopt sidecar; "
            f"got: {meta.get('strategy_used')!r}"
        )
        assert "fallback_reason" not in meta, (
            f"fallback_reason must NOT be in adopt sidecar; "
            f"got: {meta.get('fallback_reason')!r}"
        )

        # The strategy_fallback relay must NOT be in mandatory_relays
        relay_codes = [r["code"] for r in meta.get("mandatory_relays", [])]
        assert "hypothesis.strategy_fallback" not in relay_codes, (
            f"hypothesis.strategy_fallback must NOT be in adopt sidecar "
            f"relays; got: {relay_codes}"
        )

        # But capability_state IS still present (informational)
        assert "capability_state" in meta, (
            f"capability_state should still be in adopt sidecar; "
            f"keys: {list(meta.keys())}"
        )
    print("  PASS: adopt sidecar has no fallback fields")


# ---------------------------------------------------------------------------
# 7. Doctor --json output includes capability_snapshot
# ---------------------------------------------------------------------------


def test_doctor_json_includes_capability_snapshot() -> None:
    """dde doctor --json includes a capability_snapshot field."""
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--project", str(project), "doctor", "--json"],
        )

        # doctor may exit 0 or 1 depending on environment; we only
        # care about the JSON structure.
        import re

        # Extract the JSON object from the output (doctor may print
        # to stderr as well).
        match = re.search(r"\{.*\}", result.output, re.DOTALL)
        assert match, f"Could not find JSON in doctor output:\n{result.output}"
        doc = json.loads(match.group())

        assert "capability_snapshot" in doc, (
            f"doctor --json should include capability_snapshot; "
            f"keys: {list(doc.keys())}"
        )
        assert isinstance(doc["capability_snapshot"], dict), (
            "capability_snapshot should be a dict"
        )

        # Verify checks list includes name, status, detail
        assert "checks" in doc
        assert isinstance(doc["checks"], list)
        if doc["checks"]:
            check = doc["checks"][0]
            assert "name" in check, "each check should have a 'name'"
            assert "status" in check, "each check should have a 'status'"
            assert "detail" in check, "each check should have a 'detail'"
    print("  PASS: doctor --json includes capability_snapshot")


# ---------------------------------------------------------------------------
# 8. hypothesis.strategy_fallback is registered in RELAY_CODES
# ---------------------------------------------------------------------------


def test_strategy_fallback_registered() -> None:
    """hypothesis.strategy_fallback is registered in RELAY_CODES."""
    assert "hypothesis.strategy_fallback" in RELAY_CODES, (
        "hypothesis.strategy_fallback should be registered in RELAY_CODES"
    )
    assert isinstance(RELAY_CODES["hypothesis.strategy_fallback"], str)
    assert len(RELAY_CODES["hypothesis.strategy_fallback"]) > 0
    print("  PASS: strategy_fallback registered in RELAY_CODES")


# ---------------------------------------------------------------------------
# 9. select_strategy returns None fallback_info for available strategies
# ---------------------------------------------------------------------------


def test_select_strategy_no_fallback_when_available() -> None:
    """select_strategy returns None fallback_info for always-available strategies."""
    from dde.commands.hypothesis import select_strategy

    # charter is always available (no binary/package requirements)
    actual, fallback_info = select_strategy("charter")
    assert actual == "charter", f"actual strategy should be 'charter'; got {actual!r}"
    assert fallback_info is None, (
        f"fallback_info should be None for available strategy; got {fallback_info!r}"
    )

    # sponsor is always available
    actual, fallback_info = select_strategy("sponsor")
    assert actual == "sponsor"
    assert fallback_info is None
    print("  PASS: select_strategy no fallback when available")


# ---------------------------------------------------------------------------
# 10. Old sidecars without capability_state treated as unknown
# ---------------------------------------------------------------------------


def test_old_sidecar_without_capability_state_treated_as_unknown() -> None:
    """When analyzing an artifact whose sidecar lacks capability_state,
    the assessment marks source_capability_state as 'unknown'.

    Schema migration: absent field must not read as 'no degradation'.
    """
    from click.testing import CliRunner
    from dde.cli import cli

    with tempfile.TemporaryDirectory() as td:
        project = _make_project(Path(td))

        # 1. Adopt normally (this creates a sidecar WITH capability_state)
        adopted = _adopt_hypothesis_set(project)

        # 2. Strip capability_state from the sidecar to simulate a
        #    pre-#153 artifact
        meta_path = project / "raw" / "hypotheses" / "test-hyps.charter.meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.pop("capability_state", None)
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

        # 3. Analyze the artifact
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--project",
                str(project),
                "hypothesis",
                "analyze",
                str(adopted),
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"analyze failed with exit {result.exit_code}\n{result.output}"
        )

        analysis_path = project / "raw" / "hypotheses" / "test-hyps.analysis.json"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        assessment = analysis.get("assessment", {})

        assert assessment.get("source_capability_state") == "unknown", (
            f"source_capability_state should be 'unknown' for old "
            f"sidecars; got: {assessment.get('source_capability_state')!r}"
        )
    print("  PASS: old sidecar without capability_state treated as unknown")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    tests = [
        (
            "test_adopted_set_analyze_no_strategy_fallback_relay",
            test_adopted_set_analyze_no_strategy_fallback_relay,
        ),
        (
            "test_adopted_set_no_fallback_fields_in_assessment",
            test_adopted_set_no_fallback_fields_in_assessment,
        ),
        (
            "test_capability_snapshot_captures_state",
            test_capability_snapshot_captures_state,
        ),
        ("test_capability_snapshot_in_analysis", test_capability_snapshot_in_analysis),
        ("test_capability_snapshot_in_sidecar", test_capability_snapshot_in_sidecar),
        (
            "test_adopt_sidecar_no_fallback_fields",
            test_adopt_sidecar_no_fallback_fields,
        ),
        (
            "test_doctor_json_includes_capability_snapshot",
            test_doctor_json_includes_capability_snapshot,
        ),
        ("test_strategy_fallback_registered", test_strategy_fallback_registered),
        (
            "test_select_strategy_no_fallback_when_available",
            test_select_strategy_no_fallback_when_available,
        ),
        (
            "test_old_sidecar_without_capability_state_treated_as_unknown",
            test_old_sidecar_without_capability_state_treated_as_unknown,
        ),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:
            print(f"  FAIL: {name} -- {exc}")
            import traceback

            traceback.print_exc()
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    if failed:
        sys.exit(1)
    else:
        print("All tests passed.")


if __name__ == "__main__":
    main()
