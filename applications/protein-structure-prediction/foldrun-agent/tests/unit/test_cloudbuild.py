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
            assert "python3.12" not in name, (
                f"{path} still references Python 3.12 builder image '{name}'"
            )


def test_cloudbuild_agent_steps_use_python_313_uv_image():
    """Verify deploy-agent-runtime and register-agent use uv Python 3.13 image."""
    config = yaml.safe_load(CLOUDBUILD_YAML_PATH.read_text(encoding="utf-8"))
    steps_by_id = {step.get("id"): step for step in config.get("steps", [])}

    for step_id in ("deploy-agent-runtime", "register-agent"):
        assert step_id in steps_by_id, f"Missing expected Cloud Build step '{step_id}'"
        assert steps_by_id[step_id]["name"] == "ghcr.io/astral-sh/uv:python3.13-bookworm-slim"


def test_cloudbuild_configures_high_cpu_and_disk_headroom():
    """Verify root and standalone Cloud Build configs use E2_HIGHCPU_8 and sufficient disk."""
    foldrun_root = CLOUDBUILD_YAML_PATH.parent
    root_cfg = yaml.safe_load(CLOUDBUILD_YAML_PATH.read_text(encoding="utf-8"))
    opts = root_cfg.get("options", {})
    assert opts.get("machineType") == "E2_HIGHCPU_8"
    assert int(opts.get("diskSizeGb", 0)) >= 200

    for rel in ("src/foldrun-viewer/cloudbuild.yaml", "src/foldrun-analysis-job/cloudbuild.yaml"):
        sub_cfg = yaml.safe_load((foldrun_root / rel).read_text(encoding="utf-8"))
        assert sub_cfg.get("options", {}).get("machineType") == "E2_HIGHCPU_8"
        step = sub_cfg["steps"][0]
        assert "DOCKER_BUILDKIT=1" in (step.get("env") or [])
        script = step["args"][1]
        assert _first_effective_line(script) == "set -euo pipefail"
        assert "docker pull" in script
        assert "BUILDKIT_INLINE_CACHE=1" in script
        assert "--cache-from" in script


def test_cloudbuild_all_docker_steps_enable_buildkit_and_inline_cache():
    """Verify every Docker build-push step in root cloudbuild.yaml uses BuildKit + registry cache."""
    config = yaml.safe_load(CLOUDBUILD_YAML_PATH.read_text(encoding="utf-8"))
    docker_steps = [
        step for step in config.get("steps", []) if (step.get("id") or "").startswith("build-push-")
    ]
    expected_ids = {
        "build-push-viewer",
        "build-push-analysis",
        "build-push-af2-components",
        "build-push-of3-components",
        "build-push-boltz2-components",
        "build-push-esmfold2-components",
    }
    assert {step["id"] for step in docker_steps} == expected_ids

    for step in docker_steps:
        step_id = step["id"]
        env_list = step.get("env") or []
        script = step["args"][1]
        assert "DOCKER_BUILDKIT=1" in env_list, f"{step_id} missing DOCKER_BUILDKIT=1"
        assert "docker pull " in script and "|| true" in script, (
            f"{step_id} missing cache warm pull"
        )
        assert "BUILDKIT_INLINE_CACHE=1" in script, f"{step_id} missing BUILDKIT_INLINE_CACHE=1"
        assert "--cache-from " in script, f"{step_id} missing --cache-from"


def test_cloud_run_dockerfiles_use_copy_chown_without_recursive_chown():
    """Verify Cloud Run Dockerfiles avoid post-COPY `chown -R` layer duplication of /app/.venv."""
    foldrun_root = CLOUDBUILD_YAML_PATH.parent
    for rel in ("src/foldrun-viewer/Dockerfile", "src/foldrun-analysis-job/Dockerfile"):
        raw = (foldrun_root / rel).read_text(encoding="utf-8")
        effective = "\n".join(
            line for line in raw.splitlines() if not line.lstrip().startswith("#")
        )
        assert "COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv" in effective, (
            f"{rel} should set ownership during COPY --from=builder"
        )
        assert "chown -R" not in effective, f"{rel} should not duplicate layers via chown -R"
