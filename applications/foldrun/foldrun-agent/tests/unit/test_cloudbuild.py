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
        if (
            entrypoint in ("bash", "/bin/bash", "sh", "/bin/sh")
            and len(args) >= 2
            and args[0] == "-c"
        ):
            script = args[1]
            first_line = _first_effective_line(script)
            if first_line != "set -euo pipefail":
                missing_strict_mode.append((step_id, first_line))

    assert not missing_strict_mode, (
        "The following Cloud Build inline bash steps do not begin with "
        f"`set -euo pipefail` (CWE-252): {missing_strict_mode}"
    )


def test_cloudbuild_files_do_not_use_deprecated_builder_images():
    """Verify FoldRun cloudbuild.yaml files do not use deprecated builder images."""
    foldrun_root = CLOUDBUILD_YAML_PATH.parent
    cloudbuild_paths = [
        foldrun_root / "cloudbuild.yaml",
        foldrun_root / "src" / "foldrun-analysis-job" / "cloudbuild.yaml",
        foldrun_root / "src" / "foldrun-viewer" / "cloudbuild.yaml",
    ]
    deprecated_prefixes = (
        "gcr.io/cloud-builders/docker",
        "gcr.io/cloud-builders/gcloud",
        "gcr.io/google.com/cloudsdktool/cloud-sdk",
    )

    for path in cloudbuild_paths:
        assert path.exists(), f"Missing {path}"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        for step in config.get("steps", []):
            name = step.get("name", "")
            assert not name.startswith(deprecated_prefixes), (
                f"{path} uses deprecated builder image '{name}'"
            )
            assert name not in ("docker", "docker:latest"), (
                f"{path} should use a tagged CLI docker image (e.g. 'docker:29-cli'), got '{name}'"
            )


def test_cloudbuild_includes_generative_binder_components():
    """Verify cloudbuild.yaml builds BoltzGen, RFD4-Proteina, and BindCraft components and wires them into deploy-agent-runtime."""
    config = yaml.safe_load(CLOUDBUILD_YAML_PATH.read_text(encoding="utf-8"))
    steps_by_id = {step.get("id"): step for step in config.get("steps", [])}

    for step_id in (
        "build-push-boltzgen-components",
        "build-push-rfd4-proteina-components",
        "build-push-bindcraft-components",
    ):
        assert step_id in steps_by_id, f"Missing Cloud Build step '{step_id}'"

    deploy_agent = steps_by_id["deploy-agent-runtime"]
    wait_for = deploy_agent.get("waitFor", [])
    assert "build-push-boltzgen-components" in wait_for
    assert "build-push-rfd4-proteina-components" in wait_for
    assert "build-push-bindcraft-components" in wait_for

    deploy_script = deploy_agent["args"][1]
    assert "BOLTZGEN_COMPONENTS_IMAGE=" in deploy_script
    assert "RFD4_PROTEINA_COMPONENTS_IMAGE=" in deploy_script
    assert "BINDCRAFT_COMPONENTS_IMAGE=" in deploy_script

    substitutions = config.get("substitutions", {})
    for sub in (
        "_BOLTZGEN_VERSION",
        "_RFD4_VERSION",
        "_COLABDESIGN_VERSION",
        "_FREEBINDCRAFT_VERSION",
    ):
        assert sub in substitutions, f"Missing substitution '{sub}'"
