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

"""End-to-end tests for ``oase structure-screen run`` without fpocket.

``structure-screen run`` drives ``oase pocket run``/``analyze`` in-process
through ``click.testing.CliRunner``. When that runner was built with the
Click < 8.2 ``mix_stderr`` keyword, every invocation crashed with a raw
``TypeError`` under the pinned Click. On a host without fpocket the designed
behaviour is a ``not_assessed (tool_failed)`` assessment and exit 0.

The command runs in a subprocess so the test sees the real exit status and
stderr, and so the in-process CLI state it sets cannot leak into other tests.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from oase.core.context import init_project


def _ca_trace_pdb(n_residues: int = 30) -> str:
    """A single-chain CA-only PDB; enough for the command to reach fpocket."""
    lines = [
        f"ATOM  {i + 1:5d}  CA  ALA A{i + 1:4d}    "
        f"{i * 3.8:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00 30.00           C  "
        for i in range(n_residues)
    ]
    return "\n".join(lines) + "\nEND\n"


def _path_without_fpocket() -> str:
    """The current PATH minus every directory that provides fpocket."""
    dirs = [
        d
        for d in os.environ.get("PATH", "").split(os.pathsep)
        if d and shutil.which("fpocket", path=d) is None
    ]
    return os.pathsep.join(dirs)


class TestStructureScreenRunWithoutFpocket(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project = Path(self._tmpdir.name) / "program"
        init_project(self.project)
        structures = self.project / "raw" / "structures"
        structures.mkdir(parents=True, exist_ok=True)
        (structures / "X.pdb").write_text(_ca_trace_pdb())

        self.env = dict(os.environ)
        self.env.pop("OASE_PROJECT", None)
        # Keep the dirty-source-tree warning out of stderr and the rationale.
        self.env["OASE_NO_DIRTY_WARNING"] = "1"
        self.env["PATH"] = _path_without_fpocket()
        self.assertIsNone(shutil.which("fpocket", path=self.env["PATH"]))

    def tearDown(self):
        self._tmpdir.cleanup()

    def _run(self, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "oase",
                "--project",
                str(self.project),
                "structure-screen",
                "run",
                "raw/structures/X.pdb",
                "--concept-ref",
                "IC-001",
                "--modality",
                "small_molecule",
                *extra,
            ],
            capture_output=True,
            text=True,
            env=self.env,
            timeout=120,
        )

    def test_reports_tool_failed_not_traceback(self):
        proc = self._run()
        self.assertNotIn("Traceback", proc.stderr)
        self.assertNotIn("Traceback", proc.stdout)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertIn("[AR-001] not_assessed (tool_failed)", proc.stdout)
        self.assertIn("fpocket is not", proc.stdout)

    def test_json_assessment_records_tool_failure(self):
        proc = self._run("--json")
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["n_assessments"], 1)
        (assessment,) = doc["assessments"]
        self.assertEqual(assessment["evidence_status"], "not_assessed")
        self.assertEqual(assessment["execution_outcome"], "tool_failed")
        # The pocket run error (written to its stderr) reaches the rationale.
        self.assertIn("fpocket is not on PATH", assessment["rationale"])


if __name__ == "__main__":
    unittest.main()
