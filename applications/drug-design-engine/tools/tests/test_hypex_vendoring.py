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

"""Static contract for source-vendored Hypex provisioning."""

from __future__ import annotations

from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
VENDORED = TOOLS / "vendor" / "hypex"
EXPLORER = VENDORED / "hypothesis-explorer"
INSTALL = (TOOLS / "install.sh").read_text(encoding="utf-8")


def test_vendored_runtime_sources_are_complete() -> None:
    required = (
        "tools/hypex/go.mod",
        "tools/hypex/main.go",
        "tools/elo/go.mod",
        "tools/elo/main.go",
        "tools/prox/pyproject.toml",
        "tools/prox/prox/cli.py",
        "schemas/hypothesis.schema.json",
        "schemas/match.schema.json",
        "schemas/review.schema.json",
        "schemas/ratings.schema.json",
    )
    missing = [relative for relative in required if not (EXPLORER / relative).is_file()]
    assert not missing, f"missing vendored Hypex files: {missing}"


def test_installer_builds_vendored_source_without_clone_or_release_binary() -> None:
    assert 'HYPEX_SOURCE_ROOT="${SCRIPT_DIR}/vendor/hypex"' in INSTALL
    assert "go build -trimpath" in INSTALL
    assert "github.com/scion-frontiers/hypex.git" not in INSTALL
    assert "PLACEHOLDER://github.com/scion-frontiers/hypex" not in INSTALL


def test_prox_dependencies_have_an_independent_install_transaction() -> None:
    requirements = (TOOLS / "requirements-hypex.txt").read_text()
    for dependency in ("scipy", "scikit-learn", "networkx"):
        assert dependency in requirements
    assert 'pip install -r "${SCRIPT_DIR}/requirements-hypex.txt"' in INSTALL


def test_generated_environment_activates_configured_venv() -> None:
    assert 'source "${VENV_DIR}/bin/activate"' in INSTALL


def test_prox_launcher_calls_click_entrypoint() -> None:
    assert "from prox.cli import main; main()" in INSTALL
    assert "-m prox.cli" not in INSTALL
    assert 'grep -q "Commands:"' in INSTALL


def test_no_compiled_hypex_artifacts_are_committed() -> None:
    forbidden = []
    for path in VENDORED.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        magic = path.read_bytes()[:4]
        is_compiled = (
            path.name in {"hypex", "elo", "prox"}
            or suffix in {".a", ".dll", ".dylib", ".exe", ".o", ".so", ".wasm"}
            or magic == b"\x7fELF"
            or magic[:2] == b"MZ"
            or magic
            in {
                b"\xca\xfe\xba\xbe",
                b"\xce\xfa\xed\xfe",
                b"\xcf\xfa\xed\xfe",
                b"\xfe\xed\xfa\xce",
                b"\xfe\xed\xfa\xcf",
            }
        )
        if is_compiled:
            forbidden.append(str(path.relative_to(VENDORED)))
    assert not forbidden, f"compiled artifacts found in vendored source: {forbidden}"
