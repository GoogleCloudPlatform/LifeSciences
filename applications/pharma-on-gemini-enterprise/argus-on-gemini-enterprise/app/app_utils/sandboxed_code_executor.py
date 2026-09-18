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

"""Sandboxed code executor for skill scripts.

Executes Python skill scripts in an isolated subprocess using `uv run --no-project`
so that PEP 723 inline script dependencies (e.g. `polite-http`, `python-dotenv`)
are dynamically resolved and executed in an isolated sandbox environment.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
from typing import override

from google.adk.agents.invocation_context import InvocationContext
from google.adk.code_executors.base_code_executor import BaseCodeExecutor
from google.adk.code_executors.code_execution_utils import (
    CodeExecutionInput,
    CodeExecutionResult,
)
from pydantic import Field

logger = logging.getLogger("argus.sandboxed_code_executor")

_ALLOWED_ENV_VARS: frozenset[str] = frozenset(
    {
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "HOME",
        "TMPDIR",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "UV_CACHE_DIR",
        "UV_PYTHON",
        "UV_OFFLINE",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
    }
)


def _build_sanitized_env(temp_dir: str) -> dict[str, str]:
    """Constructs a minimal allowlisted environment dictionary for subprocess execution."""
    sanitized = {
        key: val for key, val in os.environ.items() if key in _ALLOWED_ENV_VARS
    }
    sanitized["TMPDIR"] = temp_dir
    return sanitized


def _transform_skill_wrapper_code(code: str) -> str:
    """Transforms ADK wrapper code to run Python scripts via `uv run` subprocess.

    ADK's `_SkillScriptCodeExecutor` generates wrapper code that invokes
    `runpy.run_path(...)` in-process. This function replaces that block with
    an isolated `uv run --no-project` subprocess call so that PEP 723 metadata
    headers are respected and dependencies are provisioned dynamically.
    """
    pattern = r"(\s*)runpy\.run_path\(([^,]+),\s*run_name=[\'\"]__main__[\'\"]\)"

    def repl(match: re.Match[str]) -> str:
        indent = match.group(1)
        target = match.group(2).strip()
        return (
            f"{indent}import shutil as _shutil, subprocess as _subp, sys as _sys\n"
            f"{indent}_uv_bin = _shutil.which('uv')\n"
            f"{indent}_cmd = ([_uv_bin, 'run', '--no-project'] if _uv_bin else [_sys.executable]) + [{target}] + _sys.argv[1:]\n"
            f"{indent}_proc = _subp.run(_cmd)\n"
            f"{indent}if _proc.returncode != 0:\n"
            f"{indent}  _sys.exit(_proc.returncode)"
        )

    if "runpy.run_path(" in code:
        return re.sub(pattern, repl, code, count=1)

    return code


class SandboxedCodeExecutor(BaseCodeExecutor):
    """Executes skill scripts in an isolated sandboxed subprocess using `uv run`.

    Provides:
    1. PEP 723 inline dependency resolution on-demand via `uv run`.
    2. Subprocess, environment, and directory isolation using temporary directories.
    3. Strict timeout enforcement with process-group termination and stdout/stderr capture.
    """

    stateful: bool = Field(default=False, frozen=True, exclude=True)
    optimize_data_file: bool = Field(default=False, frozen=True, exclude=True)
    timeout_seconds: int = 120

    def __init__(self, timeout_seconds: int = 120, **data):
        super().__init__(timeout_seconds=timeout_seconds, **data)

    @override
    def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        logger.debug("Executing sandboxed code with timeout %ss", self.timeout_seconds)

        transformed_code = _transform_skill_wrapper_code(code_execution_input.code)

        try:
            with tempfile.TemporaryDirectory(prefix="argus_sandbox_") as temp_dir:
                sanitized_env = _build_sanitized_env(temp_dir)
                proc = subprocess.Popen(
                    [sys.executable, "-c", transformed_code],
                    cwd=temp_dir,
                    env=sanitized_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
                try:
                    stdout, stderr = proc.communicate(timeout=self.timeout_seconds)
                    if proc.returncode != 0 and not stderr:
                        stderr = f"Process exited with code {proc.returncode}"
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.communicate()
                    stdout = ""
                    stderr = f"Code execution timed out after {self.timeout_seconds} seconds."
        except Exception as e:
            stdout = ""
            stderr = f"Subprocess execution error: {e}"

        return CodeExecutionResult(
            stdout=stdout,
            stderr=stderr or None,
            output_files=[],
        )
