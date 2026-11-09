#!/usr/bin/env python3
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

"""Standalone wrapper for `python run_alphafold.py ...` CLI and notebook invocations.

Automatically dispatches `--json_path=...` invocations to AlphaFold 3 (`alphafold3_main`)
and `--fasta_paths=...` invocations to AlphaFold 2 (`alphafold2_main`).
"""

import sys

from foldrun_shim.cli import alphafold2_main, alphafold3_main


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if any(a == "--json_path" or a.startswith("--json_path=") for a in args):
        return alphafold3_main(args)
    return alphafold2_main(args)


if __name__ == "__main__":
    sys.exit(main())
