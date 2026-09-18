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

"""Unit tests for SandboxedCodeExecutor."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from google.adk.agents.invocation_context import InvocationContext
from google.adk.code_executors.code_execution_utils import CodeExecutionInput

from app.app_utils.sandboxed_code_executor import (
    SandboxedCodeExecutor,
    _transform_skill_wrapper_code,
)


class TestSandboxedCodeExecutor(unittest.TestCase):
    """Tests for the SandboxedCodeExecutor and wrapper transformation."""

    def setUp(self):
        self.executor = SandboxedCodeExecutor(timeout_seconds=10)
        self.mock_context = MagicMock(spec=InvocationContext)

    def test_transform_wrapper_code(self):
        original_code = (
            "import os\n"
            "import runpy\n"
            "def _materialize_and_run():\n"
            "  try:\n"
            "    sys.argv = ['scripts/test_script.py', '--limit', '5']\n"
            "    try:\n"
            "      runpy.run_path('scripts/test_script.py', run_name='__main__')\n"
            "    except SystemExit as e:\n"
            "      pass\n"
            "_materialize_and_run()\n"
        )
        transformed = _transform_skill_wrapper_code(original_code)
        self.assertIn("_uv_bin", transformed)
        self.assertIn("--no-project", transformed)
        self.assertNotIn(
            "runpy.run_path('scripts/test_script.py', run_name='__main__')", transformed
        )

    def test_execute_simple_python_code(self):
        code = "print('Hello from sandboxed test!')\n"
        result = self.executor.execute_code(
            self.mock_context,
            CodeExecutionInput(code=code),
        )
        self.assertEqual(result.stdout.strip(), "Hello from sandboxed test!")
        self.assertIsNone(result.stderr)

    def test_execute_script_with_args(self):
        code = "import sys\nprint('Received args:', sys.argv[1:])\n"
        result = self.executor.execute_code(
            self.mock_context,
            CodeExecutionInput(code=code),
        )
        self.assertIn("Received args:", result.stdout)

    def test_execute_simulated_adk_skill_wrapper(self):
        script_code = (
            "import os\n"
            "import tempfile\n"
            "import sys\n"
            "_files = {'scripts/sample.py': 'import sys\\nprint(\"SAMPLE OUTPUT: \", sys.argv[1:])\\n'}\n"
            "def _materialize_and_run():\n"
            "  _orig_cwd = os.getcwd()\n"
            "  with tempfile.TemporaryDirectory() as td:\n"
            "    for rel_path, content in _files.items():\n"
            "      p = os.path.join(td, rel_path)\n"
            "      os.makedirs(os.path.dirname(p), exist_ok=True)\n"
            "      with open(p, 'w') as f: f.write(content)\n"
            "    os.chdir(td)\n"
            "    try:\n"
            "      sys.argv = ['scripts/sample.py', '--query', 'oncology']\n"
            "      try:\n"
            "        runpy.run_path('scripts/sample.py', run_name='__main__')\n"
            "      except SystemExit as e:\n"
            "        if e.code is not None and e.code != 0: raise e\n"
            "    finally:\n"
            "      os.chdir(_orig_cwd)\n"
            "_materialize_and_run()\n"
        )
        result = self.executor.execute_code(
            self.mock_context,
            CodeExecutionInput(code=script_code),
        )
        self.assertIn("SAMPLE OUTPUT:", result.stdout)
        self.assertIn("oncology", result.stdout)

    def test_execution_timeout(self):
        quick_timeout_executor = SandboxedCodeExecutor(timeout_seconds=1)
        infinite_code = "import time\ntime.sleep(5)\n"
        result = quick_timeout_executor.execute_code(
            self.mock_context,
            CodeExecutionInput(code=infinite_code),
        )
        self.assertIn("timed out", result.stderr)

    def test_sanitizes_environment_variables_and_isolates_cwd(self):
        import json
        import os
        from unittest.mock import patch

        host_cwd = os.getcwd()
        probe_code = (
            "import json, os\n"
            "print(json.dumps({\n"
            '  "env": dict(os.environ),\n'
            '  "cwd": os.getcwd(),\n'
            "}))\n"
        )
        fake_secrets = {
            "GOOGLE_APPLICATION_CREDENTIALS": "/secret/sa.json",
            "OPENAI_API_KEY": "sk-test-secret",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
            "CUSTOM_HOST_SECRET": "leaked-host-secret",
        }
        with patch.dict(os.environ, fake_secrets):
            result = self.executor.execute_code(
                self.mock_context,
                CodeExecutionInput(code=probe_code),
            )
        self.assertIsNone(result.stderr)
        data = json.loads(result.stdout.strip())
        child_env = data["env"]
        for secret_key in fake_secrets:
            self.assertNotIn(
                secret_key,
                child_env,
                f"Sensitive env var {secret_key} leaked into sandboxed subprocess",
            )
        self.assertNotEqual(
            os.path.realpath(data["cwd"]),
            os.path.realpath(host_cwd),
            "Sandboxed subprocess executed in host CWD instead of isolated temporary directory",
        )

    def test_terminates_grandchild_processes_on_timeout(self):
        import os
        import tempfile
        import time

        with tempfile.NamedTemporaryFile(mode="r+", delete=False) as pid_file:
            pid_path = pid_file.name

        try:
            quick_timeout_executor = SandboxedCodeExecutor(timeout_seconds=1)
            grandchild_spawner_code = (
                "import subprocess, sys, time\n"
                f'p = subprocess.Popen([sys.executable, "-c", "import os, time; open({pid_path!r}, \\"w\\").write(str(os.getpid())); time.sleep(30)"])\n'
                "time.sleep(30)\n"
            )
            result = quick_timeout_executor.execute_code(
                self.mock_context,
                CodeExecutionInput(code=grandchild_spawner_code),
            )
            self.assertIn("timed out", result.stderr)

            # Give OS a brief moment to reap killed processes
            time.sleep(0.2)
            with open(pid_path) as f:
                pid_str = f.read().strip()
            self.assertTrue(pid_str, "Grandchild process did not write PID")
            grandchild_pid = int(pid_str)

            def _is_process_running(pid: int) -> bool:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return False
                try:
                    with open(f"/proc/{pid}/status") as status_file:
                        for line in status_file:
                            if line.startswith("State:") and "\tZ" in line:
                                return False
                except FileNotFoundError:
                    return False
                return True

            is_alive = _is_process_running(grandchild_pid)
            if is_alive:
                try:
                    os.kill(grandchild_pid, 9)
                except ProcessLookupError:
                    pass

            self.assertFalse(
                is_alive,
                f"Grandchild process {grandchild_pid} survived timeout as an orphan",
            )
        finally:
            if os.path.exists(pid_path):
                os.unlink(pid_path)


if __name__ == "__main__":
    unittest.main()
