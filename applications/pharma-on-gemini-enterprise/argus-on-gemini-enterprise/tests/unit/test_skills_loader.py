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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.adk.skills.models import Skill
from google.adk.tools.skill_toolset import SkillToolset

from app.app_utils.sandboxed_code_executor import SandboxedCodeExecutor
from app.app_utils.skills_loader import (
    ScopedGCPSkillRegistry,
    create_agent_skill_toolset,
    get_local_skill,
    get_skill_registry,
)


def test_get_local_skill() -> None:
    get_local_skill.cache_clear()
    skill = get_local_skill("diligence-playbook")
    assert skill is not None
    assert skill.name == "diligence-playbook"

    skill_target = get_local_skill("target-screening")
    assert skill_target is not None
    assert skill_target.name == "target-screening"

    with pytest.raises(FileNotFoundError):
        get_local_skill("non_existent_skill_xyz")


def test_get_skill_registry_without_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    get_skill_registry.cache_clear()
    assert get_skill_registry() is None


@patch("app.app_utils.skills_loader.GCPSkillRegistry.__init__", return_value=None)
def test_get_skill_registry_with_project(
    mock_gcp_init: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project-123")
    monkeypatch.delenv("AGENT_REGISTRY_LOCATION", raising=False)
    monkeypatch.delenv("SKILLS_REGISTRY_LOCATION", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    get_skill_registry.cache_clear()

    reg = get_skill_registry()
    assert isinstance(reg, ScopedGCPSkillRegistry)
    mock_gcp_init.assert_called_once_with(
        project_id="test-project-123", location="global"
    )

    # Test explicit AGENT_REGISTRY_LOCATION
    mock_gcp_init.reset_mock()
    monkeypatch.setenv("AGENT_REGISTRY_LOCATION", "us")
    get_skill_registry.cache_clear()

    reg_us = get_skill_registry()
    assert isinstance(reg_us, ScopedGCPSkillRegistry)
    mock_gcp_init.assert_called_once_with(project_id="test-project-123", location="us")


@pytest.mark.asyncio
async def test_scoped_gcp_skill_registry_scoping() -> None:
    with patch(
        "app.app_utils.skills_loader.GCPSkillRegistry.__init__", return_value=None
    ):
        reg = ScopedGCPSkillRegistry(
            project_id="test-project-123",
            location="us-central1",
            allowed_skill_ids=["openfda-database", "clinical-trials-database"],
        )
        reg.project_id = "test-project-123"
        reg.location = "us-central1"
        reg.base_url = "https://agentregistry.googleapis.com/v1alpha"

    mock_skill = MagicMock(spec=Skill)
    mock_skill.name = "openfda-database"

    with patch(
        "google.adk.integrations.skill_registry.GCPSkillRegistry.get_skill",
        new_callable=AsyncMock,
    ) as mock_super_get:
        mock_super_get.return_value = mock_skill

        # 1. Allowed skill (translates to private- namespace in Agent Registry)
        res = await reg.get_skill(name="private-openfda-database")
        assert res.name == "openfda-database"
        mock_super_get.assert_called_once_with(name="private-openfda-database")

        # 2. Disallowed skill
        with pytest.raises(ValueError, match="is not in the allowed skills"):
            await reg.get_skill(name="private-chembl-database")

    # 3. Search filtered to allowed skills and sanitized with private- prefix
    mock_search_response = MagicMock()
    mock_search_response.json.return_value = {
        "skills": [
            {
                "name": "projects/p/locations/l/skills/private-openfda-database",
                "description": "FDA data",
            },
            {
                "name": "projects/p/locations/l/skills/private-chembl-database",
                "description": "Chemistry data",
            },
            {
                "name": "projects/p/locations/l/skills/cloud.google.com-bigtable-basics",
                "description": "Bigtable",
            },
        ]
    }
    with patch.object(
        reg, "_make_request", new_callable=AsyncMock, return_value=mock_search_response
    ):
        search_res = await reg.search_skills(query="database")
        assert len(search_res) == 1
        assert search_res[0].name == "private-openfda-database"
        assert search_res[0].description == "FDA data"


def test_create_agent_skill_toolset_without_registry() -> None:
    get_local_skill.cache_clear()

    toolset = create_agent_skill_toolset(
        local_skill_names=["diligence-playbook"],
        include_registry=False,
    )

    assert isinstance(toolset, SkillToolset)
    assert "diligence-playbook" in toolset._skills
    assert toolset._registry is None
    assert isinstance(toolset._code_executor, SandboxedCodeExecutor)


def test_create_agent_skill_toolset_with_scoped_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project-123")
    get_skill_registry.cache_clear()
    get_local_skill.cache_clear()

    with patch(
        "app.app_utils.skills_loader.GCPSkillRegistry.__init__", return_value=None
    ):
        toolset = create_agent_skill_toolset(
            science_skill_ids=[
                "private-openfda-database",
                "private-clinical-trials-database",
            ],
            local_skill_names=["diligence-playbook"],
        )

        assert isinstance(toolset, SkillToolset)
        assert "diligence-playbook" in toolset._skills
        assert isinstance(toolset._registry, ScopedGCPSkillRegistry)
        assert toolset._registry.allowed_skill_ids == {
            "openfda-database",
            "clinical-trials-database",
        }
        assert isinstance(toolset._code_executor, SandboxedCodeExecutor)
