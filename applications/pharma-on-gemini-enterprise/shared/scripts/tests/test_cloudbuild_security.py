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

"""Security unit tests for Cloud Build configurations (CWE-78 / CWE-88)."""

from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[5]
SHARED_CLOUDBUILD = (
    REPO_ROOT
    / "applications"
    / "pharma-on-gemini-enterprise"
    / "shared"
    / "cloudbuild.yaml"
)
FOLDRUN_CLOUDBUILD = REPO_ROOT / "applications" / "foldrun" / "cloudbuild.yaml"

# Matches a single unescaped $ followed by a variable name or brace,
# ignoring $$(...) or $$VAR or $${VAR} which Cloud Build unescapes to literal $.
UNESCAPED_SUBSTITUTION_RE = re.compile(r"(?<!\$)\$(?!\$)(?:\{?[A-Za-z_][A-Za-z0-9_]*\}?)")


class TestCloudBuildSecurity(unittest.TestCase):
    """Verifies Cloud Build configs are free of CWE-78 and CWE-88 injections."""

    def _check_no_inline_substitutions(self, config_path: Path):
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        for step in config.get("steps", []):
            entrypoint = step.get("entrypoint", "")
            if entrypoint not in ("sh", "bash"):
                continue

            step_id = step.get("id", "<unnamed>")
            for arg in step.get("args", []):
                matches = UNESCAPED_SUBSTITUTION_RE.findall(arg)
                self.assertEqual(
                    matches,
                    [],
                    f"Step '{step_id}' in {config_path.name} contains direct "
                    f"Cloud Build substitutions in inline shell script: {matches}. "
                    "Pass substitutions via 'env:' and reference with '$$' instead.",
                )

    def test_shared_cloudbuild_no_inline_substitutions(self):
        """Ensures shared/cloudbuild.yaml has no inline shell substitutions."""
        self._check_no_inline_substitutions(SHARED_CLOUDBUILD)

    def test_foldrun_cloudbuild_no_inline_substitutions(self):
        """Ensures foldrun/cloudbuild.yaml has no inline shell substitutions."""
        self._check_no_inline_substitutions(FOLDRUN_CLOUDBUILD)

    def test_tf_apply_prevents_argument_injection(self):
        """Verifies CWE-88 fix: tf-apply passes -var args without word splitting."""
        with open(SHARED_CLOUDBUILD, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        tf_apply_step = next(
            s for s in config["steps"] if s.get("id") == "tf-apply"
        )
        script = tf_apply_step["args"][1]
        # Cloud Build converts $$ to $ before passing to sh
        rendered_script = script.replace("$$", "$")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            workspace_dir = tmp_path / "workspace"
            workspace_dir.mkdir()
            # Replace /workspace paths in script with our temporary workspace dir
            test_script = rendered_script.replace("/workspace", str(workspace_dir))

            # Create a mock terraform executable that records received CLI args
            args_log = tmp_path / "terraform_args.log"
            mock_tf = tmp_path / "terraform"
            mock_tf.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "apply" ]; then\n'
                f'  for a in "$@"; do echo "ARG:$a" >> "{args_log}"; done\n'
                "fi\n"
                'echo "mock-output"\n',
                encoding="utf-8",
            )
            mock_tf.chmod(0o755)

            env = {
                "PATH": f"{tmp_path}:/usr/bin:/bin",
                "PROJECT_ID": "test-project",
                "REGION": "us-central1",
                # Malicious payload attempting argument injection via spaces
                "LOGS_BUCKET_NAME": "my-bucket -target=random_resource.example",
            }

            result = subprocess.run(
                ["sh", "-c", test_script],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)

            recorded_args = args_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                recorded_args,
                [
                    "ARG:apply",
                    "ARG:-auto-approve",
                    "ARG:-var=project_id=test-project",
                    "ARG:-var=region=us-central1",
                    "ARG:-var=logs_bucket_name=my-bucket -target=random_resource.example",
                ],
            )

    def test_deploy_agent_prevents_command_injection(self):
        """Verifies CWE-78 fix: malicious _ENV_VARS and _SECRETS do not execute."""
        with open(SHARED_CLOUDBUILD, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        deploy_step = next(
            s for s in config["steps"] if s.get("id") == "deploy-agent"
        )
        script = deploy_step["args"][1]
        rendered_script = script.replace("$$", "$")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            workspace_dir = tmp_path / "workspace"
            workspace_dir.mkdir()
            (workspace_dir / "reasoning_engine_id.txt").write_text("engine-123")
            (workspace_dir / "agent_display_name.txt").write_text("Test Agent")

            # Strip apt-get and curl installation lines for local isolated testing
            lines = [
                line
                for line in rendered_script.splitlines()
                if not line.strip().startswith(("apt-get", "curl", "export PATH="))
            ]
            test_script = "\n".join(lines).replace(
                "/workspace", str(workspace_dir)
            )

            pwned_file = tmp_path / "pwned.txt"
            args_log = tmp_path / "uvx_args.log"
            mock_uvx = tmp_path / "uvx"
            mock_uvx.write_text(
                "#!/bin/bash\n"
                f'for a in "$@"; do echo "ARG:$a" >> "{args_log}"; done\n',
                encoding="utf-8",
            )
            mock_uvx.chmod(0o755)

            env = {
                "PATH": f"{tmp_path}:/usr/bin:/bin",
                "PROJECT_ID": "test-project",
                "REGION": "us-central1",
                # Malicious command injection payload
                "ENV_VARS": f'"; touch {pwned_file}; echo "',
                "SECRETS": f'"; touch {pwned_file}; echo "',
            }

            result = subprocess.run(
                ["bash", "-c", test_script],
                cwd=tmp_path,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertFalse(
                pwned_file.exists(),
                "Command injection occurred: pwned.txt was created!",
            )


if __name__ == "__main__":
    unittest.main()
