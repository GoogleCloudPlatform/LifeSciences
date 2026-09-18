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

"""Unit tests verifying Cloud Build pipeline error propagation (CWE-252)."""

from pathlib import Path

import yaml

CLOUDBUILD_YAML_PATH = Path(__file__).resolve().parents[3] / "cloudbuild.yaml"


def _first_effective_line(script: str) -> str:
    """Return the first non-empty, non-comment line in a bash script."""
    for line in script.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def test_cloudbuild_yaml_is_valid():
    """Verify cloudbuild.yaml parses cleanly and contains build steps."""
    assert CLOUDBUILD_YAML_PATH.exists(), f"Missing {CLOUDBUILD_YAML_PATH}"
    config = yaml.safe_load(CLOUDBUILD_YAML_PATH.read_text(encoding="utf-8"))
    assert isinstance(config, dict)
    assert "steps" in config
    assert len(config["steps"]) > 0


def test_cloudbuild_inline_bash_steps_enable_strict_error_propagation():
    """Verify every inline bash step in cloudbuild.yaml starts with `set -euo pipefail`."""
    config = yaml.safe_load(CLOUDBUILD_YAML_PATH.read_text(encoding="utf-8"))
    missing_strict_mode = []

    for step in config.get("steps", []):
        step_id = step.get("id", "<unnamed>")
        entrypoint = step.get("entrypoint", "")
        args = step.get("args", [])
        if entrypoint in ("bash", "/bin/bash") and len(args) >= 2 and args[0] == "-c":
            script = args[1]
            first_line = _first_effective_line(script)
            if first_line != "set -euo pipefail":
                missing_strict_mode.append((step_id, first_line))

    assert not missing_strict_mode, (
        "The following Cloud Build inline bash steps do not begin with "
        f"`set -euo pipefail` (CWE-252): {missing_strict_mode}"
    )
