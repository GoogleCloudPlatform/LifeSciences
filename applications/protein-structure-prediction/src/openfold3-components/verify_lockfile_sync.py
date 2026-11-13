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

"""Verify that the active Python environment matches exported requirements.lock.

Executed at Docker build time after `pixi install --locked` so the OSV-scanned
`requirements.lock` cannot drift out of sync with the Pixi runtime environment.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <requirements.lock>", file=sys.stderr)
        return 2

    lock_path = Path(sys.argv[1])
    expected: dict[str, str] = {}
    for raw in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        spec = line.split()[0]
        name, ver = spec.split("==", 1)
        expected[_norm(name)] = ver

    pip_out = subprocess.check_output(
        [sys.executable, "-m", "pip", "list", "--format=json"],
        text=True,
    )
    installed = {_norm(item["name"]): item["version"] for item in json.loads(pip_out)}

    if installed != expected:
        missing = {k: expected[k] for k in sorted(set(expected) - set(installed))}
        extra = {k: installed[k] for k in sorted(set(installed) - set(expected))}
        mismatched = {
            k: {"expected": expected[k], "installed": installed[k]}
            for k in sorted(set(expected) & set(installed))
            if expected[k] != installed[k]
        }
        print(
            f"ERROR: {lock_path} does not match installed packages!\n"
            f"  missing={missing}\n  extra={extra}\n  mismatched={mismatched}",
            file=sys.stderr,
        )
        return 1

    print(f"Verified {len(installed)} installed Python packages match {lock_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
