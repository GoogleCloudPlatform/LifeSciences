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

"""Regression tests for immutable Hub-template specialist dispatch."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


OASE_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_INSTRUCTIONS = (
    OASE_ROOT / "templates" / "research-operations-controller" / "agents.md"
)


class TestControllerTemplateDispatch(unittest.TestCase):
    def setUp(self):
        self.instructions = CONTROLLER_INSTRUCTIONS.read_text(encoding="utf-8")
        match = re.search(
            r"## 5\. Dispatch and run identity(?P<section>.*?)(?:\n---\n)",
            self.instructions,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "controller dispatch section is missing")
        self.dispatch_section = match.group("section")

    def test_dispatch_resolves_approved_project_template_from_hub(self):
        self.assertIn(
            "scion template show <template> --hub --format json",
            self.dispatch_section,
        )
        self.assertIn("template ID and content hash", self.dispatch_section)
        self.assertIn('`scope` is `project`', self.dispatch_section)

    def test_dispatch_cannot_upload_a_stale_local_template(self):
        command = re.search(
            r"scion start <name>[^\n`]*",
            self.dispatch_section,
        )
        self.assertIsNotNone(command, "specialist launch command is missing")
        launch = command.group(0)
        self.assertIn("--type project:<template>", launch)
        self.assertIn("--no-upload", launch)


if __name__ == "__main__":
    unittest.main()
