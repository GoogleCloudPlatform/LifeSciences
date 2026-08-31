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

"""Unit tests for sync_skills_to_registry.py."""

import json
import pathlib
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from sync_skills_to_registry import (
    AgentRegistryGcloudClient,
    _sync_single_skill,
    activate_all_draft_skills,
    is_skill_up_to_date,
    normalize_skill_id,
    parse_frontmatter,
    sync_skills,
)


class TestSyncSkillsToRegistry(unittest.TestCase):
    def test_normalize_skill_id(self):
        self.assertEqual(normalize_skill_id("abc"), "abc-tool")
        self.assertEqual(normalize_skill_id("my_cool_skill"), "my-cool-skill")
        self.assertEqual(
            normalize_skill_id("Target-Screening-101"), "target-screening-101"
        )
        self.assertEqual(normalize_skill_id("---foo---"), "foo-tool")

    def test_parse_frontmatter(self):
        content = """---
name: my-skill
display_name: My Cool Skill
description: A useful test skill
---
# Body content
Here is markdown content.
"""
        fm = parse_frontmatter(content)
        self.assertEqual(fm["name"], "my-skill")
        self.assertEqual(fm["display_name"], "My Cool Skill")
        self.assertEqual(fm["description"], "A useful test skill")

    def test_parse_frontmatter_empty(self):
        self.assertEqual(parse_frontmatter("# No frontmatter"), {})

    def test_list_existing_skills_mapping(self):
        client = AgentRegistryGcloudClient(project_id="test-proj", location="global")
        mock_json = json.dumps(
            [
                {
                    "name": "projects/test-proj/locations/global/skills/private-target-screening",
                    "displayName": "Target Screening",
                    "state": "STATE_ACTIVE",
                    "targetState": "TARGET_STATE_ACTIVE",
                    "defaultRevision": "projects/test-proj/locations/global/skills/private-target-screening/revisions/rev-1",
                },
                {
                    "name": "projects/test-proj/locations/global/skills/cloud.google.com-diligence-playbook",
                    "displayName": "Diligence Playbook",
                    "state": "STATE_ACTIVE",
                    "targetState": "TARGET_STATE_ACTIVE",
                    "defaultRevision": "projects/test-proj/locations/global/skills/cloud.google.com-diligence-playbook/revisions/rev-1",
                },
            ]
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_json, stderr="")
            mapping = client.list_existing_skills()

            # Check full identifiers
            self.assertIn("private-target-screening", mapping)
            self.assertIn("cloud.google.com-diligence-playbook", mapping)

            # Check prefix-stripped keys are preserved without greedy truncation
            self.assertIn("target-screening", mapping)
            self.assertIn("diligence-playbook", mapping)
            self.assertEqual(
                mapping["target-screening"]["displayName"], "Target Screening"
            )
            self.assertEqual(
                mapping["diligence-playbook"]["displayName"], "Diligence Playbook"
            )

    def test_is_skill_up_to_date(self):
        now = datetime.now(UTC)
        earlier = now - timedelta(hours=1)
        later = now + timedelta(hours=1)

        # Missing matched_item
        self.assertFalse(is_skill_up_to_date(None, now))

        # Draft state
        draft_item = {
            "state": "STATE_DRAFT",
            "targetState": "TARGET_STATE_DRAFT",
            "defaultRevision": None,
            "updateTime": later.isoformat(),
        }
        self.assertFalse(is_skill_up_to_date(draft_item, now))

        # Active state but missing default revision
        missing_rev_item = {
            "state": "STATE_ACTIVE",
            "targetState": "TARGET_STATE_ACTIVE",
            "defaultRevision": "",
            "updateTime": later.isoformat(),
        }
        self.assertFalse(is_skill_up_to_date(missing_rev_item, now))

        # Active and newer in registry
        active_item = {
            "state": "STATE_ACTIVE",
            "targetState": "TARGET_STATE_ACTIVE",
            "defaultRevision": "projects/test/locations/global/skills/s1/revisions/r1",
            "updateTime": later.isoformat(),
        }
        self.assertTrue(is_skill_up_to_date(active_item, now))

        # Active but older in registry than git commit
        older_item = {
            "state": "STATE_ACTIVE",
            "targetState": "TARGET_STATE_ACTIVE",
            "defaultRevision": "projects/test/locations/global/skills/s1/revisions/r1",
            "updateTime": earlier.isoformat(),
        }
        self.assertFalse(is_skill_up_to_date(older_item, now))

    def test_sync_skill_new_creates_revision_and_activates(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = pathlib.Path(tmp_dir)
            client = AgentRegistryGcloudClient(
                project_id="test-proj", location="global"
            )
            zip_file = tmp_path / "skill.zip"
            zip_file.write_bytes(b"PK000")

            existing_skills = {}
            commands_run = []

            def fake_run(cmd, capture_output=True, text=True):
                commands_run.append(cmd)
                return MagicMock(returncode=0, stdout="", stderr="")

            with patch("subprocess.run", side_effect=fake_run):
                client.sync_skill(
                    skill_id="target-screening",
                    display_name="Target Screening",
                    description="Screen targets",
                    zip_path=zip_file,
                    existing_skills=existing_skills,
                )

            self.assertEqual(len(commands_run), 3)

            create_cmd = commands_run[0]
            self.assertEqual(
                create_cmd[:5],
                ["gcloud", "alpha", "agent-registry", "skills", "create"],
            )
            self.assertIn("target-screening", create_cmd)
            self.assertFalse(any(arg.startswith("--payload") for arg in create_cmd))

            rev_cmd = commands_run[1]
            self.assertEqual(
                rev_cmd[:6],
                ["gcloud", "alpha", "agent-registry", "skills", "revisions", "create"],
            )
            self.assertIn("--skill=private-target-screening", rev_cmd)
            self.assertIn(f"--payload={zip_file}", rev_cmd)

            update_cmd = commands_run[2]
            self.assertEqual(
                update_cmd[:5],
                ["gcloud", "alpha", "agent-registry", "skills", "update"],
            )
            self.assertIn("private-target-screening", update_cmd)
            self.assertIn("--target-state=active", update_cmd)
            self.assertTrue(
                any(arg.startswith("--default-revision=") for arg in update_cmd)
            )

    def test_sync_skill_new_private_prefixed(self):
        """Verifies skill ID already starting with 'private-' does not get double prefixed."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = pathlib.Path(tmp_dir)
            client = AgentRegistryGcloudClient(
                project_id="test-proj", location="global"
            )
            zip_file = tmp_path / "skill.zip"
            zip_file.write_bytes(b"PK000")

            commands_run = []

            def fake_run(cmd, capture_output=True, text=True):
                commands_run.append(cmd)
                return MagicMock(returncode=0, stdout="", stderr="")

            with patch("subprocess.run", side_effect=fake_run):
                client.sync_skill(
                    skill_id="private-custom-tool",
                    display_name="Custom Tool",
                    description="Custom tool",
                    zip_path=zip_file,
                    existing_skills={},
                )

            rev_cmd = commands_run[1]
            self.assertIn("--skill=private-custom-tool", rev_cmd)
            self.assertNotIn("--skill=private-private-custom-tool", rev_cmd)

    def test_sync_skill_existing_creates_revision_and_activates(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = pathlib.Path(tmp_dir)
            client = AgentRegistryGcloudClient(
                project_id="test-proj", location="global"
            )
            zip_file = tmp_path / "skill.zip"
            zip_file.write_bytes(b"PK000")

            existing_skills = {
                "target-screening": {
                    "name": "projects/test-proj/locations/global/skills/private-target-screening",
                    "state": "STATE_ACTIVE",
                    "targetState": "TARGET_STATE_ACTIVE",
                    "defaultRevision": "projects/test-proj/locations/global/skills/private-target-screening/revisions/rev-1",
                }
            }

            commands_run = []

            def fake_run(cmd, capture_output=True, text=True):
                commands_run.append(cmd)
                return MagicMock(returncode=0, stdout="", stderr="")

            with patch("subprocess.run", side_effect=fake_run):
                client.sync_skill(
                    skill_id="target-screening",
                    display_name="Target Screening",
                    description="Screen targets",
                    zip_path=zip_file,
                    existing_skills=existing_skills,
                )

            self.assertEqual(len(commands_run), 2)
            self.assertEqual(
                commands_run[0][:6],
                ["gcloud", "alpha", "agent-registry", "skills", "revisions", "create"],
            )
            self.assertEqual(
                commands_run[1][:5],
                ["gcloud", "alpha", "agent-registry", "skills", "update"],
            )

    def test_sync_skills_force_update(self):
        """Verifies force_update=True syncs skills even when up-to-date."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = pathlib.Path(tmp_dir)
            skill1_dir = tmp_path / "skill1"
            skill1_dir.mkdir()
            (skill1_dir / "SKILL.md").write_text("# Skill 1", encoding="utf-8")

            discovered = [
                {
                    "skill_id": "skill-one",
                    "display_name": "Skill One",
                    "description": "First skill",
                    "local_path": skill1_dir,
                    "git_commit_time": datetime.now(UTC) - timedelta(days=1),
                },
            ]
            existing = {
                "skill-one": {
                    "name": "projects/test-proj/locations/global/skills/private-skill-one",
                    "state": "STATE_ACTIVE",
                    "targetState": "TARGET_STATE_ACTIVE",
                    "defaultRevision": "projects/test-proj/locations/global/skills/private-skill-one/revisions/rev-1",
                    "updateTime": datetime.now(UTC).isoformat(),
                }
            }

            with patch("subprocess.run") as mock_git_clone:
                mock_git_clone.return_value = MagicMock(returncode=0)
                with patch(
                    "sync_skills_to_registry.discover_skills", return_value=discovered
                ):
                    with patch.object(
                        AgentRegistryGcloudClient,
                        "list_existing_skills",
                        return_value=existing,
                    ):
                        with patch.object(
                            AgentRegistryGcloudClient, "sync_skill"
                        ) as mock_sync:
                            sync_skills(
                                project_id="test-proj",
                                location="global",
                                force_update=True,
                                concurrency=1,
                            )
                            mock_sync.assert_called_once()

    def test_sync_skill_raises_on_revision_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = pathlib.Path(tmp_dir)
            client = AgentRegistryGcloudClient(
                project_id="test-proj", location="global"
            )
            zip_file = tmp_path / "skill.zip"
            zip_file.write_bytes(b"PK000")

            def fake_run(cmd, capture_output=True, text=True):
                if "revisions" in cmd and "create" in cmd:
                    return MagicMock(
                        returncode=1,
                        stdout="",
                        stderr="Permission denied or invalid archive",
                    )
                return MagicMock(returncode=0, stdout="", stderr="")

            with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
                with self.assertRaises(RuntimeError) as ctx:
                    client.sync_skill(
                        skill_id="target-screening",
                        display_name="Target Screening",
                        description="Screen targets",
                        zip_path=zip_file,
                        existing_skills={},
                    )
                self.assertIn("Failed to create revision", str(ctx.exception))

    def test_activate_all_draft_skills_success_and_deduplication(self):
        client = AgentRegistryGcloudClient(project_id="test-proj", location="global")
        existing = {
            "private-skill-1": {
                "name": "projects/test-proj/locations/global/skills/private-skill-1",
                "state": "STATE_DRAFT",
                "targetState": "TARGET_STATE_DRAFT",
                "defaultRevision": "projects/test-proj/locations/global/skills/private-skill-1/revisions/rev-1",
            },
            "skill-1": {
                "name": "projects/test-proj/locations/global/skills/private-skill-1",
                "state": "STATE_DRAFT",
                "targetState": "TARGET_STATE_DRAFT",
                "defaultRevision": "projects/test-proj/locations/global/skills/private-skill-1/revisions/rev-1",
            },
        }

        with patch.object(client, "list_existing_skills", return_value=existing):
            with patch.object(client, "activate_skill") as mock_activate:
                activated, failed = activate_all_draft_skills(client, concurrency=2)
                self.assertEqual(activated, 1)
                self.assertEqual(failed, 0)
                mock_activate.assert_called_once_with(
                    target_skill="private-skill-1",
                    default_revision="projects/test-proj/locations/global/skills/private-skill-1/revisions/rev-1",
                )

    def test_activate_all_draft_skills_failure_reporting(self):
        client = AgentRegistryGcloudClient(project_id="test-proj", location="global")
        existing = {
            "private-skill-1": {
                "name": "projects/test-proj/locations/global/skills/private-skill-1",
                "state": "STATE_DRAFT",
                "targetState": "TARGET_STATE_DRAFT",
                "defaultRevision": None,
            },
        }

        with patch.object(client, "list_existing_skills", return_value=existing):
            with patch.object(client, "get_latest_revision", return_value=None):
                activated, failed = activate_all_draft_skills(client, concurrency=1)
                self.assertEqual(activated, 0)
                self.assertEqual(failed, 1)

    def test_sync_single_skill_success_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = pathlib.Path(tmp_dir)
            skill_dir = tmp_path / "skill_dir"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("# Skill", encoding="utf-8")

            item = {
                "skill_id": "test-skill",
                "display_name": "Test Skill",
                "description": "Test description",
                "local_path": skill_dir,
            }

            client = MagicMock(spec=AgentRegistryGcloudClient)
            skill_id, success, err = _sync_single_skill(
                item=item,
                client=client,
                temp_path=tmp_path,
                existing_skills={},
            )

            self.assertEqual(skill_id, "test-skill")
            self.assertTrue(success)
            self.assertIsNone(err)
            client.sync_skill.assert_called_once()
            # Verify zip was cleaned up in finally block
            self.assertFalse((tmp_path / "test-skill.zip").exists())

    def test_sync_single_skill_failure_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = pathlib.Path(tmp_dir)
            skill_dir = tmp_path / "skill_dir"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("# Skill", encoding="utf-8")

            item = {
                "skill_id": "test-skill",
                "display_name": "Test Skill",
                "description": "Test description",
                "local_path": skill_dir,
            }

            client = MagicMock(spec=AgentRegistryGcloudClient)
            client.sync_skill.side_effect = RuntimeError("API rate limit exceeded")

            skill_id, success, err = _sync_single_skill(
                item=item,
                client=client,
                temp_path=tmp_path,
                existing_skills={},
            )

            self.assertEqual(skill_id, "test-skill")
            self.assertFalse(success)
            self.assertIn("API rate limit exceeded", err)
            # Verify zip was cleaned up even on failure
            self.assertFalse((tmp_path / "test-skill.zip").exists())

    def test_sync_skills_parallel(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = pathlib.Path(tmp_dir)
            skill1_dir = tmp_path / "skill1"
            skill1_dir.mkdir()
            (skill1_dir / "SKILL.md").write_text("# Skill 1", encoding="utf-8")

            skill2_dir = tmp_path / "skill2"
            skill2_dir.mkdir()
            (skill2_dir / "SKILL.md").write_text("# Skill 2", encoding="utf-8")

            discovered = [
                {
                    "skill_id": "skill-one",
                    "display_name": "Skill One",
                    "description": "First skill",
                    "local_path": skill1_dir,
                    "git_commit_time": None,
                },
                {
                    "skill_id": "skill-two",
                    "display_name": "Skill Two",
                    "description": "Second skill",
                    "local_path": skill2_dir,
                    "git_commit_time": None,
                },
            ]

            with patch("subprocess.run") as mock_git_clone:
                mock_git_clone.return_value = MagicMock(returncode=0)
                with patch(
                    "sync_skills_to_registry.discover_skills", return_value=discovered
                ):
                    with patch.object(
                        AgentRegistryGcloudClient,
                        "list_existing_skills",
                        return_value={},
                    ):
                        with patch.object(
                            AgentRegistryGcloudClient, "sync_skill"
                        ) as mock_sync_skill:
                            sync_skills(
                                project_id="test-proj",
                                location="global",
                                concurrency=2,
                            )
                            self.assertEqual(mock_sync_skill.call_count, 2)

    def test_sync_skills_activate_only_exits_on_failure(self):
        with patch.object(
            AgentRegistryGcloudClient, "list_existing_skills", return_value={}
        ):
            with patch(
                "sync_skills_to_registry.activate_all_draft_skills", return_value=(0, 2)
            ):
                with self.assertRaises(SystemExit) as ctx:
                    sync_skills(
                        project_id="test-proj",
                        location="global",
                        activate_only=True,
                    )
                self.assertEqual(ctx.exception.code, 1)

    def test_sync_skills_sync_failure_exits(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = pathlib.Path(tmp_dir)
            skill1_dir = tmp_path / "skill1"
            skill1_dir.mkdir()
            (skill1_dir / "SKILL.md").write_text("# Skill 1", encoding="utf-8")

            discovered = [
                {
                    "skill_id": "skill-one",
                    "display_name": "Skill One",
                    "description": "First skill",
                    "local_path": skill1_dir,
                    "git_commit_time": None,
                },
            ]

            with patch("subprocess.run") as mock_git_clone:
                mock_git_clone.return_value = MagicMock(returncode=0)
                with patch(
                    "sync_skills_to_registry.discover_skills", return_value=discovered
                ):
                    with patch.object(
                        AgentRegistryGcloudClient,
                        "list_existing_skills",
                        return_value={},
                    ):
                        with patch.object(
                            AgentRegistryGcloudClient,
                            "sync_skill",
                            side_effect=RuntimeError("GCP Error"),
                        ):
                            with self.assertRaises(SystemExit) as ctx:
                                sync_skills(
                                    project_id="test-proj",
                                    location="global",
                                    concurrency=1,
                                )
                            self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
