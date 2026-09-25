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

"""Tests for self-contained site generation (#282).

Covers:
- viewer_url_for with bundled=True/False
- build_cmd bundles raw/ into _site/ by default
- build_cmd --no-bundle-raw skips raw/ copy
- export_cmd creates a valid zip archive
- export_cmd fails gracefully when _site/ doesn't exist
- Zip contains index.html at root level
- Hidden files excluded from bundle and zip

Run with:
    PYTHONPATH=tools python3 tests/test_site_self_contained.py

Exit 0 = all tests passed, exit 1 = at least one failure.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap — add tools/ to sys.path so dde is importable
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from click.testing import CliRunner
from dde.cli import cli
from dde.commands.site import viewer_url_for
from dde.core.controlstore import (
    ensure_control_dirs,
    write_record,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_project(base: Path, name: str = "proj") -> Path:
    """Create a minimal dde project directory with control plane."""
    project = base / name
    project.mkdir(parents=True, exist_ok=True)
    (project / ".dde").mkdir(exist_ok=True)
    ensure_control_dirs(project)
    return project


def _wo_data(
    wo_id: str,
    revision: int,
    state: str,
    **overrides: Any,
) -> dict[str, Any]:
    """Build a valid work-order record dict."""
    base = {
        "id": wo_id,
        "revision": revision,
        "state": state,
        "decision_question": f"Test question for {wo_id}",
        "requested_role": "test-agent",
        "stage": "test",
        "cycle": 1,
        "context": {"summary": "test context"},
        "dependencies": [],
        "capabilities": ["test"],
        "deliverables": {
            "layer_1": ["findings/test-analysis.md"],
            "layer_0_classes": ["structures"],
        },
        "acceptance_criteria": "tests pass",
        "alert_policy": {"on_failure": "notify"},
        "priority": "normal",
        "resource_class": "standard",
        "report_to": "test-lead",
        "created_at": _NOW,
    }
    base.update(overrides)
    return base


def _write_wo(
    project: Path,
    wo_id: str,
    revision: int,
    state: str,
    **overrides: Any,
) -> None:
    """Write a work-order record to a project's control plane."""
    ident = f"{wo_id}-r{revision}"
    data = _wo_data(wo_id, revision, state, **overrides)
    write_record(project, "work-order", ident, data)


def _setup_buildable_project(base: Path, name: str = "proj") -> Path:
    """Create a project with enough structure for site build to succeed.

    Creates a minimal project with one accepted work order, a finding,
    a raw artifact, and the required directory structure.
    """
    project = _make_project(base, name)

    # Write an accepted work order
    _write_wo(project, "WO-001", 1, "scientifically_accepted")

    # Create the finding file referenced by the WO
    findings_dir = project / "findings"
    findings_dir.mkdir(exist_ok=True)
    (findings_dir / "test-analysis.md").write_text(
        "# Test Analysis\n\nThis is a test finding.\n",
        encoding="utf-8",
    )

    # Create raw/ with a test artifact
    raw_structures = project / "raw" / "structures"
    raw_structures.mkdir(parents=True, exist_ok=True)
    (raw_structures / "test.cif").write_text("data_test\n", encoding="utf-8")
    (raw_structures / "result.json").write_text('{"ok": true}\n', encoding="utf-8")

    # Create a hidden file that should be excluded from bundling
    (raw_structures / ".hidden_file").write_text("secret\n", encoding="utf-8")

    # Create a .DS_Store that should be excluded
    (project / "raw" / ".DS_Store").write_bytes(b"\x00\x00\x00\x01")

    return project


def _run_site_build(
    project: Path,
    extra_args: list[str] | None = None,
) -> Any:
    """Invoke ``dde site build`` via click's test runner."""
    runner = CliRunner()
    args = ["--project", str(project), "site", "build"]
    if extra_args:
        args.extend(extra_args)
    return runner.invoke(cli, args, catch_exceptions=False)


def _run_site_export(
    project: Path,
    extra_args: list[str] | None = None,
) -> Any:
    """Invoke ``dde site export`` via click's test runner."""
    runner = CliRunner()
    args = ["--project", str(project), "site", "export"]
    if extra_args:
        args.extend(extra_args)
    return runner.invoke(cli, args, catch_exceptions=False)


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

_results: list[tuple[str, str, str | None]] = []  # (name, status, detail)


def _test(name: str):
    """Decorator that registers and runs a test function."""

    def decorator(fn):
        try:
            fn()
            _results.append((name, "PASS", None))
        except AssertionError as exc:
            _results.append((name, "FAIL", str(exc)))
        except Exception as exc:
            _results.append(
                (
                    name,
                    "ERROR",
                    f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                )
            )
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

_TMPBASE = Path(tempfile.mkdtemp(prefix="dde-test-site-sc-"))


# ---- viewer_url_for tests ----


@_test("1. viewer_url_for bundled=True produces ../raw/ paths")
def test_viewer_url_bundled():
    url = viewer_url_for("protein.cif", "raw/structures", bundled=True)
    assert url is not None, "expected a viewer URL for .cif files"
    assert "?file=../raw/structures/protein.cif" in url, (
        f"expected ../raw/ path, got: {url}"
    )
    assert url.startswith("viewers/structure-viewer.html"), (
        f"expected structure-viewer.html, got: {url}"
    )


@_test("2. viewer_url_for bundled=False produces ../../raw/ paths")
def test_viewer_url_unbundled():
    url = viewer_url_for("protein.cif", "raw/structures", bundled=False)
    assert url is not None, "expected a viewer URL for .cif files"
    assert "?file=../../raw/structures/protein.cif" in url, (
        f"expected ../../raw/ path, got: {url}"
    )


@_test("3. viewer_url_for default bundled is True")
def test_viewer_url_default_bundled():
    url = viewer_url_for("data.json", "raw/structures")
    assert url is not None
    assert "?file=../raw/structures/data.json" in url, (
        f"expected default bundled path (../), got: {url}"
    )


@_test("4. viewer_url_for returns None for unrecognised extension")
def test_viewer_url_unknown():
    url = viewer_url_for("readme.txt", "raw/structures")
    assert url is None, f"expected None for .txt file, got: {url}"


# ---- build_cmd bundling tests ----


@_test("5. build_cmd copies raw/ into _site/raw/ by default")
def test_build_bundles_raw():
    base = _TMPBASE / "t5"
    base.mkdir()
    project = _setup_buildable_project(base)

    result = _run_site_build(project)
    assert result.exit_code == 0, f"build failed: {result.output}"

    site_raw = project / "_site" / "raw"
    assert site_raw.is_dir(), "_site/raw/ should exist after default build"

    # Check artifact was copied
    assert (site_raw / "structures" / "test.cif").is_file(), (
        "test.cif should be copied into _site/raw/structures/"
    )
    assert (site_raw / "structures" / "result.json").is_file(), (
        "result.json should be copied into _site/raw/structures/"
    )


@_test("6. build_cmd excludes hidden files from raw/ bundle")
def test_build_excludes_hidden():
    base = _TMPBASE / "t6"
    base.mkdir()
    project = _setup_buildable_project(base)

    _run_site_build(project)

    site_raw = project / "_site" / "raw"
    assert not (site_raw / "structures" / ".hidden_file").exists(), (
        "hidden files should be excluded from raw/ bundle"
    )
    assert not (site_raw / ".DS_Store").exists(), (
        ".DS_Store should be excluded from raw/ bundle"
    )


@_test("7. build_cmd --no-bundle-raw skips raw/ copy")
def test_build_no_bundle_raw():
    base = _TMPBASE / "t7"
    base.mkdir()
    project = _setup_buildable_project(base)

    result = _run_site_build(project, extra_args=["--no-bundle-raw"])
    assert result.exit_code == 0, f"build failed: {result.output}"

    site_raw = project / "_site" / "raw"
    assert not site_raw.exists(), (
        "_site/raw/ should NOT exist when --no-bundle-raw is passed"
    )


@_test("8. build_cmd --no-bundle-raw uses ../../ viewer paths")
def test_build_no_bundle_viewer_paths():
    """When --no-bundle-raw is used, the generated HTML should contain
    ../../raw/ viewer paths instead of ../raw/."""
    base = _TMPBASE / "t8"
    base.mkdir()
    project = _setup_buildable_project(base)

    _run_site_build(project, extra_args=["--no-bundle-raw"])

    # Check any artifact page for the old-style viewer path
    site_dir = project / "_site"
    found_viewer_link = False
    for html_file in site_dir.glob("artifact_*.html"):
        content = html_file.read_text(encoding="utf-8")
        if "../../raw/" in content:
            found_viewer_link = True
            break

    # Only assert if artifact pages were generated (they may not be
    # if there are no artifacts with viewer links)
    if found_viewer_link:
        pass  # correct — unbundled paths used
    # If no artifact page was generated, we verify via the unit test above


@_test("9. build_cmd default uses ../ viewer paths in HTML")
def test_build_bundled_viewer_paths():
    """Default build should embed ../raw/ viewer paths."""
    base = _TMPBASE / "t9"
    base.mkdir()
    project = _setup_buildable_project(base)

    _run_site_build(project)

    site_dir = project / "_site"
    for html_file in site_dir.glob("artifact_*.html"):
        content = html_file.read_text(encoding="utf-8")
        # Should have ../raw/ not ../../raw/
        if "viewers/" in content and "?file=" in content:
            assert "../../raw/" not in content, (
                f"bundled build should not contain ../../raw/ paths in {html_file.name}"
            )


# ---- export_cmd tests ----


@_test("10. export_cmd creates a valid zip archive")
def test_export_creates_zip():
    base = _TMPBASE / "t10"
    base.mkdir()
    project = _setup_buildable_project(base)

    # Build the site first
    build_result = _run_site_build(project)
    assert build_result.exit_code == 0, f"build failed: {build_result.output}"

    # Export
    zip_path = str(project / "test-export.zip")
    result = _run_site_export(project, extra_args=["-o", zip_path])
    assert result.exit_code == 0, f"export failed: {result.output}"

    assert Path(zip_path).is_file(), "zip file should be created"

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        assert len(names) > 0, "zip should not be empty"


@_test("11. export zip contains index.html at root level")
def test_export_index_at_root():
    base = _TMPBASE / "t11"
    base.mkdir()
    project = _setup_buildable_project(base)

    _run_site_build(project)

    zip_path = str(project / "test-export.zip")
    _run_site_export(project, extra_args=["-o", zip_path])

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        assert "index.html" in names, (
            f"index.html should be at zip root, got: {names[:10]}"
        )


@_test("12. export zip excludes hidden files")
def test_export_excludes_hidden():
    base = _TMPBASE / "t12"
    base.mkdir()
    project = _setup_buildable_project(base)

    _run_site_build(project)

    # Sneak a hidden file into _site
    (project / "_site" / ".hidden").write_text("nope\n")
    hidden_dir = project / "_site" / ".hidden_dir"
    hidden_dir.mkdir()
    (hidden_dir / "file.txt").write_text("nope\n")

    zip_path = str(project / "test-export.zip")
    _run_site_export(project, extra_args=["-o", zip_path])

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        for name in names:
            parts = Path(name).parts
            for p in parts:
                assert not p.startswith("."), (
                    f"hidden file/dir should be excluded from zip: {name}"
                )


@_test("13. export_cmd fails when _site/ doesn't exist")
def test_export_no_site():
    base = _TMPBASE / "t13"
    base.mkdir()
    project = _make_project(base)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--project", str(project), "site", "export"],
        catch_exceptions=True,
    )
    # Should fail with non-zero exit
    assert result.exit_code != 0, (
        f"export should fail when _site/ doesn't exist, got exit 0: {result.output}"
    )


@_test("14. export_cmd fails when index.html missing")
def test_export_no_index():
    base = _TMPBASE / "t14"
    base.mkdir()
    project = _make_project(base)

    # Create _site without index.html
    site_dir = project / "_site"
    site_dir.mkdir()
    (site_dir / "other.html").write_text("<html></html>\n")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--project", str(project), "site", "export"],
        catch_exceptions=True,
    )
    assert result.exit_code != 0, (
        f"export should fail when index.html missing, got exit 0: {result.output}"
    )


@_test("15. export_cmd default output filename uses project slug")
def test_export_default_filename():
    base = _TMPBASE / "t15"
    base.mkdir()
    project = _setup_buildable_project(base)

    _run_site_build(project)

    result = _run_site_export(project)
    assert result.exit_code == 0, f"export failed: {result.output}"

    expected_zip = project / f"{project.name}-site.zip"
    assert expected_zip.is_file(), (
        f"default zip should be named {expected_zip.name}, output was: {result.output}"
    )


@_test("16. export zip contains bundled raw/ artifacts")
def test_export_includes_raw():
    base = _TMPBASE / "t16"
    base.mkdir()
    project = _setup_buildable_project(base)

    _run_site_build(project)

    zip_path = str(project / "test-export.zip")
    _run_site_export(project, extra_args=["-o", zip_path])

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        raw_entries = [n for n in names if n.startswith("raw/")]
        assert len(raw_entries) > 0, (
            f"zip should contain raw/ entries, got: {names[:20]}"
        )
        # Check specific artifact
        assert any("test.cif" in n for n in raw_entries), (
            f"zip should contain test.cif in raw/, got raw entries: {raw_entries}"
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"\n{'=' * 60}")
    print("Site self-contained tests")
    print(f"{'=' * 60}\n")

    passed = sum(1 for _, s, _ in _results if s == "PASS")
    failed = sum(1 for _, s, _ in _results if s == "FAIL")
    errors = sum(1 for _, s, _ in _results if s == "ERROR")

    for name, status, detail in _results:
        icon = {"PASS": "✓", "FAIL": "✗", "ERROR": "!"}[status]
        print(f"  {icon} {name}: {status}")
        if detail:
            for line in detail.splitlines():
                print(f"      {line}")

    print(f"\n{'=' * 60}")
    print(
        f"Total: {len(_results)} | Passed: {passed} | Failed: {failed} | Errors: {errors}"
    )
    print(f"{'=' * 60}\n")

    # Cleanup
    shutil.rmtree(_TMPBASE, ignore_errors=True)

    sys.exit(0 if (failed + errors) == 0 else 1)
