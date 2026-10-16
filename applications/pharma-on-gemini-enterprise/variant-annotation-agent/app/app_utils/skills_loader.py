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

"""Utilities for loading and assembling ADK skills for the Variant Annotation Agent.

Loads custom clinical/genomics domain playbooks from the local app/skills directory
and dynamic science skills from Google Cloud Agent Registry via ADK's native
GCPSkillRegistry.
"""

from __future__ import annotations

import functools
import logging
import os
import pathlib
import re
from collections.abc import Sequence

import httpx
from google.adk.integrations.skill_registry import GCPSkillRegistry
from google.adk.skills import load_skill_from_dir
from google.adk.skills.models import Frontmatter, Skill
from google.adk.skills.skill_registry import SkillRegistry
from google.adk.tools.skill_toolset import SkillToolset

logger = logging.getLogger(__name__)

LOCAL_SKILLS_DIR = pathlib.Path(__file__).parent.parent / "skills"

CUSTOM_SKILL_NAMES: tuple[str, ...] = (
    "acmg-clinvar-gnomad-triage",
    "alphagenome-avi-interpretation",
    "gwas-noncoding-variant-mechanisms",
    "pharmacogenomics-star-allele-review",
)

SCIENCE_SKILL_IDS: tuple[str, ...] = (
    "private-alphagenome-single-variant-analysis",
    "private-alphagenome-variant-impact-score",
    "private-alphagenome-atlas-website-links",
    "private-clinvar-database",
    "private-gnomad-database",
    "private-gwas-catalog-database",
    "private-ensembl-database",
    "private-opentargets-database",
    "private-chembl-database",
    "private-uniprot-database",
    "private-pubmed-database",
)


def strip_skill_prefix(name: str) -> str:
    """Strips namespace prefixes like 'private-' or 'cloud.google.com-' from skill identifier."""
    if name.startswith("private-"):
        return name.removeprefix("private-")
    if name.startswith("cloud.google.com-"):
        return name.removeprefix("cloud.google.com-")
    return name


class ScopedGCPSkillRegistry(GCPSkillRegistry):
    """ADK GCPSkillRegistry wrapper that enforces specialist domain lane boundaries."""

    def __init__(
        self,
        project_id: str | None = None,
        location: str | None = None,
        allowed_skill_ids: Sequence[str] | None = None,
    ):
        super().__init__(project_id=project_id, location=location)
        self.allowed_skill_ids = (
            {
                strip_skill_prefix(s.strip().lower().replace("_", "-"))
                for s in allowed_skill_ids
            }
            if allowed_skill_ids is not None
            else None
        )

    def _create_httpx_client(self) -> httpx.AsyncClient:
        """Creates an httpx client with redirect following enabled for media downloads."""
        ssl_ctx = getattr(self, "_ssl_context", None)
        if ssl_ctx is not None:
            return httpx.AsyncClient(verify=ssl_ctx, follow_redirects=True)
        return httpx.AsyncClient(follow_redirects=True)

    async def get_skill(self, *, name: str) -> Skill:
        """Retrieves a skill from Agent Registry if allowed by domain lane scoping."""
        raw_id = name.split("/")[-1].strip().lower().replace("_", "-")
        canonical_name = strip_skill_prefix(raw_id)

        if (
            self.allowed_skill_ids is not None
            and canonical_name not in self.allowed_skill_ids
        ):
            raise ValueError(
                f"Skill '{canonical_name}' is not in the allowed skills for this agent ({sorted(self.allowed_skill_ids)})"
            )

        target_name = (
            raw_id
            if (raw_id.startswith("private-") or raw_id.startswith("cloud.google.com-"))
            else f"private-{raw_id}"
        )

        try:
            return await super().get_skill(name=target_name)
        except Exception:
            if target_name != raw_id:
                return await super().get_skill(name=raw_id)
            raise

    async def search_skills(self, *, query: str) -> list[Frontmatter]:
        """Searches skills in Agent Registry filtered to this agent's domain lane."""
        async with self._create_httpx_client() as client:
            url = f"{self.base_url}/projects/{self.project_id}/locations/{self.location}/skills:search"
            params = {"search_string": query}
            response = await self._make_request(client, url, params=params)
            response_data = response.json()

            results: list[Frontmatter] = []
            for s in response_data.get("skills", []):
                raw_name = s.get("name", "").split("/")[-1]
                canonical = strip_skill_prefix(raw_name)

                if (
                    self.allowed_skill_ids is not None
                    and canonical not in self.allowed_skill_ids
                    and raw_name not in self.allowed_skill_ids
                ):
                    continue

                clean_name = re.sub(r"[^a-z0-9\-]", "-", raw_name.lower()).strip("-")
                clean_name = re.sub(r"-+", "-", clean_name)

                try:
                    results.append(
                        Frontmatter(
                            name=clean_name,
                            description=s.get("description", "") or "",
                        )
                    )
                except Exception as e:
                    logger.debug(
                        "Could not construct Frontmatter for skill '%s': %s",
                        raw_name,
                        e,
                    )
            return results


@functools.lru_cache(maxsize=8)
def get_skill_registry(
    project_id: str | None = None,
    location: str | None = None,
    allowed_skill_ids_tuple: tuple[str, ...] | None = None,
) -> ScopedGCPSkillRegistry | None:
    """Returns a ScopedGCPSkillRegistry client for Google Cloud Agent Registry."""
    if os.environ.get("SKIP_DEEPMIND_SKILLS_FETCH", "").lower() in ("1", "true", "yes"):
        return None

    proj = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT")
    loc = location or os.environ.get("AGENT_REGISTRY_LOCATION") or "global"

    if not proj:
        logger.debug(
            "GOOGLE_CLOUD_PROJECT not set; ScopedGCPSkillRegistry will not be attached."
        )
        return None

    try:
        logger.debug(
            "Initializing ScopedGCPSkillRegistry for project '%s' in '%s'", proj, loc
        )
        return ScopedGCPSkillRegistry(
            project_id=proj,
            location=loc,
            allowed_skill_ids=allowed_skill_ids_tuple,
        )
    except Exception as e:
        logger.warning("Could not initialize ScopedGCPSkillRegistry: %s", e)
        return None


@functools.lru_cache(maxsize=32)
def get_local_skill(skill_name: str, skills_dir: pathlib.Path | None = None) -> Skill:
    """Loads a custom domain methodology skill from the local app/skills/ directory."""
    if not re.match(r"^[a-zA-Z0-9_-]+$", skill_name):
        raise ValueError(f"Invalid skill name: '{skill_name}'")

    base_dir = skills_dir or LOCAL_SKILLS_DIR
    skill_dir = base_dir / skill_name
    if not skill_dir.resolve().is_relative_to(base_dir.resolve()):
        raise ValueError(
            f"Skill directory '{skill_dir}' resolves outside of '{base_dir}'"
        )

    if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").exists():
        raise FileNotFoundError(f"Local skill '{skill_name}' not found at {skill_dir}")

    logger.debug("Loading local skill '%s' from %s", skill_name, skill_dir)
    return load_skill_from_dir(skill_dir)


def create_agent_skill_toolset(
    science_skill_ids: Sequence[str] = (),
    local_skill_names: Sequence[str] = CUSTOM_SKILL_NAMES,
    include_registry: bool = True,
    registry: SkillRegistry | None = None,
    script_timeout: int = 120,
) -> SkillToolset:
    """Builds an ADK SkillToolset combining local domain playbooks and Agent Registry."""
    skills: list[Skill] = []

    for lname in local_skill_names:
        try:
            skills.append(get_local_skill(lname))
        except Exception as e:
            logger.warning("Could not load local skill '%s': %s", lname, e)

    if registry is not None:
        target_registry = registry
    elif include_registry:
        allowed_tuple = (
            tuple(sorted(science_skill_ids)) if science_skill_ids is not None else None
        )
        target_registry = get_skill_registry(allowed_skill_ids_tuple=allowed_tuple)
    else:
        target_registry = None

    return SkillToolset(
        skills=skills,
        registry=target_registry,
        script_timeout=script_timeout,
    )
