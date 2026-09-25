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

"""Tests for the central per-host QPS registry (dde/core/qps.py, #70).

Asserts:
- The HOST_QPS table exists and maps every host used in command modules.
- No command module declares its own *_QPS constant (structural guard).
- ``qps_for_host`` returns the table value for known hosts and the
  default for unknown hosts.
- Every ``qps=`` call site in command modules uses ``qps_for_host``,
  not a local constant (prevents regression to per-module QPS).
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

# Patch optional dependencies before importing the module under test.
with patch.dict("sys.modules", {"requests": MagicMock(), "click": MagicMock()}):
    from dde.core.qps import HOST_QPS, qps_for_host


# Root of the commands package, relative to the repository layout.
_COMMANDS_DIR = Path(__file__).resolve().parent.parent / "dde" / "commands"


class TestHostQPSTable(unittest.TestCase):
    """The HOST_QPS table is well-formed and complete."""

    def test_table_is_nonempty(self):
        self.assertGreater(len(HOST_QPS), 0)

    def test_all_values_are_positive_floats(self):
        for host, qps in HOST_QPS.items():
            with self.subTest(host=host):
                self.assertIsInstance(qps, (int, float))
                self.assertGreater(qps, 0, f"{host} has non-positive QPS")

    def test_keys_are_bare_hostnames(self):
        """Keys must be plain hostnames, not URLs or paths."""
        for host in HOST_QPS:
            self.assertNotIn("/", host, f"{host!r} looks like a URL, not a hostname")
            self.assertNotIn(":", host, f"{host!r} contains a port or scheme")


class TestQpsForHost(unittest.TestCase):
    """``qps_for_host`` returns expected values."""

    def test_known_host(self):
        self.assertEqual(qps_for_host("pubchem.ncbi.nlm.nih.gov"), 2.0)

    def test_unknown_host_returns_default(self):
        self.assertEqual(qps_for_host("never-seen-before.example.com"), 2.0)

    def test_ebi_strictest(self):
        """www.ebi.ac.uk should use the strictest rate (1.0 from assay/ChEMBL)."""
        self.assertEqual(qps_for_host("www.ebi.ac.uk"), 1.0)

    def test_uniprot_strictest(self):
        """rest.uniprot.org should use 3.0, not 5.0 from pathway.py."""
        self.assertEqual(qps_for_host("rest.uniprot.org"), 3.0)

    def test_clinicaltrials_strictest(self):
        """clinicaltrials.gov should use 2.0, not 3.0 from trials.py."""
        self.assertEqual(qps_for_host("clinicaltrials.gov"), 2.0)


class TestNoLocalQPSConstants(unittest.TestCase):
    """No command module may declare its own ``*_QPS`` constant.

    This is the structural guard that makes host-QPS disagreement
    impossible by construction.  If a module assigns a module-level
    name ending in ``_QPS``, this test fails.
    """

    def test_no_qps_constants_in_commands(self):
        if not _COMMANDS_DIR.is_dir():
            self.skipTest(f"commands directory not found: {_COMMANDS_DIR}")

        violations: list[str] = []
        for py_file in sorted(_COMMANDS_DIR.rglob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                tree = ast.parse(py_file.read_text(), filename=str(py_file))
            except SyntaxError:
                continue  # skip unparseable files
            for node in ast.iter_child_nodes(tree):
                # Module-level assignments: X_QPS = ...
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id.endswith("_QPS"):
                            violations.append(
                                f"{py_file.name}:{node.lineno}: {target.id}"
                            )
                # Annotated assignments: X_QPS: float = ...
                if isinstance(node, ast.AnnAssign) and isinstance(
                    node.target, ast.Name
                ):
                    if node.target.id.endswith("_QPS"):
                        violations.append(
                            f"{py_file.name}:{node.lineno}: {node.target.id}"
                        )

        if violations:
            msg = (
                "Command modules must not declare local *_QPS constants.  "
                "Use qps_for_host() from dde.core.qps instead.\n"
                + "\n".join(f"  {v}" for v in violations)
            )
            self.fail(msg)


class TestCallSitesUseRegistry(unittest.TestCase):
    """Every ``qps=`` keyword in command modules must reference ``qps_for_host``.

    Catches regressions where a developer hard-codes ``qps=5.0`` instead
    of going through the registry.
    """

    # Allowlist: qps=0 (disables pacing), qps=qps (local forwarding of
    # an already-validated value from a function parameter).
    _ALLOWED_PATTERNS: ClassVar[set[str]] = {"qps_for_host", "0"}

    def test_qps_kwargs_use_registry(self):
        if not _COMMANDS_DIR.is_dir():
            self.skipTest(f"commands directory not found: {_COMMANDS_DIR}")

        violations: list[str] = []
        for py_file in sorted(_COMMANDS_DIR.rglob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                tree = ast.parse(py_file.read_text(), filename=str(py_file))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords:
                    if kw.arg != "qps":
                        continue
                    src = ast.dump(kw.value)
                    # Allow qps=qps_for_host(...), qps=0, qps=<variable named qps>
                    if any(pat in src for pat in self._ALLOWED_PATTERNS):
                        continue
                    # Allow forwarded parameter: qps=qps (Name(id='qps'))
                    if isinstance(kw.value, ast.Name) and kw.value.id == "qps":
                        continue
                    violations.append(
                        f"{py_file.name}:{node.lineno}: qps={ast.unparse(kw.value)}"
                    )

        if violations:
            msg = (
                "All qps= keyword arguments in command modules must use "
                "qps_for_host() from dde.core.qps.\n"
                + "\n".join(f"  {v}" for v in violations)
            )
            self.fail(msg)


class TestHostnameExistence(unittest.TestCase):
    """Every hostname passed to ``qps_for_host()`` in command modules must
    exist as a key in ``HOST_QPS``.

    This catches typos like ``qps_for_host("www.ebi.ac.uk.typo")`` that
    would silently fall back to the default 2.0 instead of using the
    intended registry entry.
    """

    def test_all_qps_for_host_args_in_registry(self):
        if not _COMMANDS_DIR.is_dir():
            self.skipTest(f"commands directory not found: {_COMMANDS_DIR}")

        missing: list[str] = []
        seen_hosts: set[str] = set()

        for py_file in sorted(_COMMANDS_DIR.rglob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                tree = ast.parse(py_file.read_text(), filename=str(py_file))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                # Match calls to qps_for_host(...)
                func = node.func
                if isinstance(func, ast.Name) and func.id == "qps_for_host":
                    pass
                elif isinstance(func, ast.Attribute) and func.attr == "qps_for_host":
                    pass
                else:
                    continue
                # Extract the first positional argument if it's a string literal
                if (
                    node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    hostname = node.args[0].value
                    seen_hosts.add(hostname)
                    if hostname not in HOST_QPS:
                        missing.append(
                            f"{py_file.name}:{node.lineno}: "
                            f"qps_for_host({hostname!r}) not in HOST_QPS"
                        )

        self.assertTrue(
            len(seen_hosts) > 0,
            "Expected to find at least one qps_for_host() call with a "
            "string literal hostname in command modules",
        )

        if missing:
            msg = (
                "Hostnames passed to qps_for_host() must exist in HOST_QPS.  "
                "Add missing hosts to dde/core/qps.py.\n"
                + "\n".join(f"  {m}" for m in missing)
            )
            self.fail(msg)


if __name__ == "__main__":
    unittest.main()
