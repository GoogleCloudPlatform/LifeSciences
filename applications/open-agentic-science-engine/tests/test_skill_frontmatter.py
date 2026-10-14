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

"""Validate the metadata Antigravity requires for every OASE skill."""

import unittest
from pathlib import Path

import yaml

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
SKILL_FILES = sorted(SKILLS_DIR.glob("*/SKILL.md"))


class SkillFrontmatterTest(unittest.TestCase):
    def test_all_skills_have_valid_frontmatter(self) -> None:
        self.assertTrue(SKILL_FILES, "no OASE skills found")

        for skill_path in SKILL_FILES:
            with self.subTest(skill=skill_path.parent.name):
                text = skill_path.read_text(encoding="utf-8")
                self.assertTrue(
                    text.startswith("---\n"),
                    "missing opening YAML frontmatter delimiter",
                )

                closing_delimiter = text.find("\n---\n", 4)
                self.assertNotEqual(
                    closing_delimiter,
                    -1,
                    "missing closing YAML frontmatter delimiter",
                )

                try:
                    frontmatter = yaml.safe_load(text[4:closing_delimiter])
                except yaml.YAMLError as exc:
                    self.fail(f"invalid YAML frontmatter: {exc}")

                self.assertIsInstance(
                    frontmatter,
                    dict,
                    "frontmatter must be a YAML mapping",
                )

                name = frontmatter.get("name")
                self.assertIsInstance(name, str, "name must be a string")
                self.assertTrue(name.strip(), "name must be non-empty")
                self.assertEqual(
                    name,
                    skill_path.parent.name,
                    "name must match the skill directory",
                )

                description = frontmatter.get("description")
                self.assertIsInstance(
                    description,
                    str,
                    "description must be a string",
                )
                self.assertTrue(
                    description.strip(),
                    "description must be non-empty",
                )


if __name__ == "__main__":
    unittest.main()
