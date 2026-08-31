# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pyyaml>=6.0.1",
# ]
# ///
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

"""Syncs science skills from GitHub to Google Cloud Agent Registry.

This script clones a GitHub repository containing agent skills (by default
https://github.com/google-deepmind/science-skills), discovers all skill packages
containing a SKILL.md, packages them into zip archives, and registers/activates
them in Google Cloud Agent Registry via `gcloud alpha agent-registry`.

Includes incremental change detection: skips uploading skills whose Agent
Registry update/create timestamp is newer than the latest Git commit affecting
that skill directory.

Usage:
    uv run shared/scripts/sync_skills_to_registry.py --project my-gcp-project --location global
    uv run shared/scripts/sync_skills_to_registry.py --dry-run
    uv run shared/scripts/sync_skills_to_registry.py --force
    uv run shared/scripts/sync_skills_to_registry.py --activate-all
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("sync_skills_to_registry")

DEFAULT_REPO_URL = "https://github.com/google-deepmind/science-skills.git"
DEFAULT_LOCATION = "global"
DEFAULT_CONCURRENCY = 5
DEFAULT_CLONE_DEPTH = 50


def parse_frontmatter(content: str) -> dict[str, Any]:
    """Parses YAML frontmatter from markdown file content."""
    pattern = r"^---\s*\n(.*?)\n---\s*\n"
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return {}
    try:
        return yaml.safe_load(match.group(1)) or {}
    except Exception as e:
        logger.warning("Failed to parse YAML frontmatter: %s", e)
        return {}


def normalize_skill_id(raw_name: str) -> str:
    """Normalizes skill ID to lowercase alphanumeric and hyphens, enforcing min 4 chars."""
    normalized = raw_name.strip().lower().replace("_", "-")
    normalized = re.sub(r"[^a-z0-9\-]", "", normalized)
    normalized = re.sub(r"^-+|-+$", "", normalized)
    # Agent Registry resource ID regex requires at least 4 characters: ^[a-z][a-z0-9-.]{2,126}[a-z0-9]$
    if len(normalized) < 4:
        normalized = f"{normalized}-tool"
    return normalized


def package_skill_to_zip(skill_dir: pathlib.Path, zip_path: pathlib.Path) -> None:
    """Packages a skill directory into a zip archive file, omitting bulky media files."""
    excluded_extensions = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".mp4",
        ".mov",
        ".zip",
        ".tar",
        ".gz",
    }
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for file_path in skill_dir.rglob("*"):
            if file_path.is_file():
                if (
                    "__pycache__" in file_path.parts
                    or file_path.name.endswith(".pyc")
                    or file_path.name.startswith(".git")
                ):
                    continue
                if file_path.suffix.lower() in excluded_extensions:
                    continue
                arcname = file_path.relative_to(skill_dir).as_posix()
                z.write(file_path, arcname=arcname)


def get_git_last_commit_time(
    repo_dir: pathlib.Path, rel_path: pathlib.Path
) -> datetime | None:
    """Returns the UTC datetime of the latest git commit affecting the relative path."""
    try:
        res = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", str(rel_path)],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        ts_str = res.stdout.strip()
        if ts_str.isdigit():
            return datetime.fromtimestamp(int(ts_str), tz=UTC)
    except Exception as e:
        logger.debug("Could not determine git commit time for '%s': %s", rel_path, e)
    return None


def discover_skills(repo_dir: pathlib.Path) -> list[dict[str, Any]]:
    """Discovers all skills with SKILL.md inside the repository directory."""
    skills: list[dict[str, Any]] = []

    for skill_md_path in repo_dir.rglob("SKILL.md"):
        skill_dir = skill_md_path.parent
        content = skill_md_path.read_text(encoding="utf-8")
        frontmatter = parse_frontmatter(content)

        raw_name = frontmatter.get("name") or skill_dir.name
        skill_id = normalize_skill_id(raw_name)
        description = frontmatter.get("description", "").strip()
        display_name = (
            frontmatter.get("display_name")
            or raw_name.replace("-", " ").replace("_", " ").title()
        )

        if not description:
            body = re.sub(
                r"^---\s*\n.*?\n---\s*\n", "", content, flags=re.DOTALL
            ).strip()
            lines = [
                line.strip()
                for line in body.splitlines()
                if line.strip() and not line.startswith("#")
            ]
            description = lines[0] if lines else f"Skill {display_name}"

        files = [
            str(p.relative_to(skill_dir)) for p in skill_dir.rglob("*") if p.is_file()
        ]
        git_commit_time = get_git_last_commit_time(
            repo_dir, skill_dir.relative_to(repo_dir)
        )

        skills.append(
            {
                "skill_id": skill_id,
                "display_name": display_name,
                "description": description[:2048],  # Max 2048 chars for Agent Registry
                "local_path": skill_dir,
                "files": files,
                "git_commit_time": git_commit_time,
            }
        )

    skills.sort(key=lambda x: x["skill_id"])
    return skills


def is_skill_up_to_date(
    matched_item: dict[str, Any] | None,
    git_commit_time: datetime | None,
) -> bool:
    """Determines whether an existing skill in Agent Registry is up-to-date and active."""
    if not matched_item:
        return False

    state = matched_item.get("state")
    target_state = matched_item.get("targetState")
    default_rev = matched_item.get("defaultRevision")

    # Must be active and have defaultRevision assigned
    if (
        state != "STATE_ACTIVE"
        or target_state != "TARGET_STATE_ACTIVE"
        or not default_rev
    ):
        return False

    if git_commit_time is None:
        return False

    up_str = matched_item.get("updateTime") or matched_item.get("createTime")
    if not up_str:
        return False

    try:
        reg_time = datetime.fromisoformat(up_str.replace("Z", "+00:00"))
        return reg_time > git_commit_time
    except Exception:
        return False


class AgentRegistryGcloudClient:
    """Client for managing skills in Google Cloud Agent Registry via gcloud CLI."""

    def __init__(self, project_id: str, location: str):
        self.project_id = project_id
        self.location = location

    def list_existing_skills(self) -> dict[str, dict[str, Any]]:
        """Lists existing skills and returns mapping of skill_id to skill dictionary."""
        cmd = [
            "gcloud",
            "alpha",
            "agent-registry",
            "skills",
            "list",
            f"--location={self.location}",
            f"--project={self.project_id}",
            "--format=json",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            logger.warning("Could not list skills from Agent Registry: %s", res.stderr)
            return {}

        mapping: dict[str, dict[str, Any]] = {}
        try:
            items = json.loads(res.stdout or "[]")
            for item in items:
                name = item.get("name", "")
                if not name:
                    continue
                last_segment = name.split("/")[-1]
                mapping[last_segment] = item
                if last_segment.startswith("private-"):
                    mapping[last_segment.removeprefix("private-")] = item
                elif "-" in last_segment:
                    prefix, _, suffix = last_segment.partition("-")
                    if "." in prefix:
                        mapping[suffix] = item
        except Exception as e:
            logger.warning("Failed to parse existing skills JSON: %s", e)
        return mapping

    def get_latest_revision(self, target_skill: str) -> str | None:
        """Retrieves the full resource name of the latest revision for a skill."""
        cmd = [
            "gcloud",
            "alpha",
            "agent-registry",
            "skills",
            "revisions",
            "list",
            f"--skill={target_skill}",
            f"--location={self.location}",
            f"--project={self.project_id}",
            "--format=json",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            logger.warning(
                "Could not list revisions for skill '%s': %s", target_skill, res.stderr
            )
            return None
        try:
            items = json.loads(res.stdout or "[]")
            if items:
                return items[0].get("name")
        except Exception as e:
            logger.warning(
                "Failed to parse revisions JSON for skill '%s': %s", target_skill, e
            )
        return None

    def create_revision(
        self,
        target_skill: str,
        zip_path: pathlib.Path,
        max_retries: int = 2,
    ) -> str:
        """Creates a new revision with the zip payload for a skill and returns its resource name."""
        rev_id = f"rev-{int(time.time())}-{os.urandom(2).hex()}"
        rev_cmd = [
            "gcloud",
            "alpha",
            "agent-registry",
            "skills",
            "revisions",
            "create",
            rev_id,
            f"--skill={target_skill}",
            f"--location={self.location}",
            f"--project={self.project_id}",
            f"--payload={zip_path}",
        ]
        total_attempts = max_retries + 1
        for attempt in range(total_attempts):
            rev_res = subprocess.run(rev_cmd, capture_output=True, text=True)
            if rev_res.returncode == 0:
                return f"projects/{self.project_id}/locations/{self.location}/skills/{target_skill}/revisions/{rev_id}"
            if attempt < max_retries:
                logger.warning(
                    "Revision creation for '%s' failed (attempt %d/%d), retrying in %ds: %s",
                    target_skill,
                    attempt + 1,
                    total_attempts,
                    attempt + 1,
                    rev_res.stderr.strip(),
                )
                time.sleep(1.0 * (attempt + 1))
            else:
                raise RuntimeError(
                    f"Failed to create revision '{rev_id}' for skill '{target_skill}': {rev_res.stderr.strip()}"
                )

    def activate_skill(
        self,
        target_skill: str,
        default_revision: str,
        display_name: str | None = None,
        description: str | None = None,
        max_retries: int = 2,
    ) -> None:
        """Updates a skill to set its default revision and target state to ACTIVE."""
        update_cmd = [
            "gcloud",
            "alpha",
            "agent-registry",
            "skills",
            "update",
            target_skill,
            f"--location={self.location}",
            f"--project={self.project_id}",
            f"--default-revision={default_revision}",
            "--target-state=active",
        ]
        if display_name:
            update_cmd.append(f"--display-name={display_name}")
        if description:
            update_cmd.append(f"--description={description}")

        total_attempts = max_retries + 1
        for attempt in range(total_attempts):
            update_res = subprocess.run(update_cmd, capture_output=True, text=True)
            if update_res.returncode == 0:
                return
            if attempt < max_retries:
                logger.warning(
                    "Activation for '%s' failed (attempt %d/%d), retrying in %ds: %s",
                    target_skill,
                    attempt + 1,
                    total_attempts,
                    attempt + 1,
                    update_res.stderr.strip(),
                )
                time.sleep(1.0 * (attempt + 1))
            else:
                raise RuntimeError(
                    f"Failed to activate skill '{target_skill}': {update_res.stderr.strip()}"
                )

    def sync_skill(
        self,
        skill_id: str,
        display_name: str,
        description: str,
        zip_path: pathlib.Path,
        existing_skills: dict[str, dict[str, Any]],
    ) -> None:
        """Creates or updates a skill in Agent Registry and ensures it is active with a revision."""
        matched_item = existing_skills.get(skill_id) or existing_skills.get(
            f"private-{skill_id}"
        )

        if not matched_item:
            logger.info("Creating new skill '%s' in Agent Registry...", skill_id)
            create_cmd = [
                "gcloud",
                "alpha",
                "agent-registry",
                "skills",
                "create",
                skill_id,
                f"--location={self.location}",
                f"--project={self.project_id}",
                f"--display-name={display_name}",
                f"--description={description}",
            ]
            create_res = subprocess.run(create_cmd, capture_output=True, text=True)
            if create_res.returncode != 0:
                raise RuntimeError(
                    f"Failed to create skill '{skill_id}': {create_res.stderr.strip()}"
                )
            target_skill = (
                skill_id if skill_id.startswith("private-") else f"private-{skill_id}"
            )
        else:
            target_skill = matched_item["name"].split("/")[-1]
            logger.info(
                "Skill '%s' already exists (%s); uploading revision...",
                skill_id,
                target_skill,
            )

        # 1. Create a revision containing the zip payload
        full_rev_name = self.create_revision(target_skill, zip_path)
        rev_id = full_rev_name.split("/")[-1]

        # 2. Update default revision and set target-state to ACTIVE
        self.activate_skill(
            target_skill=target_skill,
            default_revision=full_rev_name,
            display_name=display_name,
            description=description,
        )
        logger.info(
            "✓ Synced and activated skill '%s' (%s, revision: %s)",
            skill_id,
            target_skill,
            rev_id,
        )


def _sync_single_skill(
    item: dict[str, Any],
    client: AgentRegistryGcloudClient,
    temp_path: pathlib.Path,
    existing_skills: dict[str, dict[str, Any]],
) -> tuple[str, bool, str | None]:
    """Worker function to package, upload, and activate a single skill."""
    skill_id = item["skill_id"]
    display_name = item["display_name"]
    description = item["description"]
    local_path = item["local_path"]
    zip_path = temp_path / f"{skill_id}.zip"

    try:
        package_skill_to_zip(local_path, zip_path)
        logger.info(
            "[%s] Syncing skill (%d bytes payload)...",
            skill_id,
            zip_path.stat().st_size,
        )
        client.sync_skill(
            skill_id=skill_id,
            display_name=display_name,
            description=description,
            zip_path=zip_path,
            existing_skills=existing_skills,
        )
        return skill_id, True, None
    except Exception as e:
        logger.error("[%s] ✗ Failed to sync skill: %s", skill_id, e)
        return skill_id, False, str(e)
    finally:
        zip_path.unlink(missing_ok=True)


def activate_all_draft_skills(
    client: AgentRegistryGcloudClient, concurrency: int = DEFAULT_CONCURRENCY
) -> tuple[int, int]:
    """Activates all draft skills in the Agent Registry in parallel.

    Returns:
        tuple[int, int]: (activated_count, failed_count)
    """
    existing = client.list_existing_skills()
    logger.info("Checking %d skills for activation in Agent Registry...", len(existing))

    # De-duplicate by unique skill resource name
    unique_skills: dict[str, dict[str, Any]] = {}
    for item in existing.values():
        name = item.get("name")
        if name and name not in unique_skills:
            unique_skills[name] = item

    to_activate: list[tuple[str, str | None]] = []
    for name, item in unique_skills.items():
        target_skill = name.split("/")[-1]
        state = item.get("state")
        target_state = item.get("targetState")
        default_rev = item.get("defaultRevision")

        if (
            state != "STATE_ACTIVE"
            or target_state != "TARGET_STATE_ACTIVE"
            or not default_rev
        ):
            to_activate.append((target_skill, default_rev))

    if not to_activate:
        logger.info("All skills are already active. Nothing to activate.")
        return 0, 0

    def _activate_single(
        target_skill: str, default_rev: str | None
    ) -> tuple[str, bool, str | None]:
        if not default_rev:
            default_rev = client.get_latest_revision(target_skill)
        if default_rev:
            try:
                client.activate_skill(
                    target_skill=target_skill, default_revision=default_rev
                )
                logger.info("✓ Activated '%s'", target_skill)
                return target_skill, True, None
            except Exception as e:
                logger.warning("Could not activate '%s': %s", target_skill, e)
                return target_skill, False, str(e)
        else:
            err = f"Skill '{target_skill}' has no revisions; skipping activation."
            logger.warning(err)
            return target_skill, False, err

    effective_concurrency = max(1, min(concurrency, len(to_activate)))
    logger.info(
        "Activating %d draft skills in parallel (concurrency: %d)...",
        len(to_activate),
        effective_concurrency,
    )

    activated_count = 0
    failed_count = 0
    failed_skills: list[tuple[str, str | None]] = []

    with ThreadPoolExecutor(max_workers=effective_concurrency) as executor:
        futures = [
            executor.submit(_activate_single, target_skill, default_rev)
            for target_skill, default_rev in to_activate
        ]
        for future in as_completed(futures):
            target_skill, success, err = future.result()
            if success:
                activated_count += 1
            else:
                failed_count += 1
                failed_skills.append((target_skill, err))

    logger.info(
        "Activation complete. Successful: %d, Failed: %d.",
        activated_count,
        failed_count,
    )
    if failed_skills:
        logger.error("Failed activation summary:")
        for t_skill, err in failed_skills:
            logger.error("  - %s: %s", t_skill, err)

    return activated_count, failed_count


def sync_skills(
    project_id: str,
    location: str,
    repo_url: str = DEFAULT_REPO_URL,
    dry_run: bool = False,
    force_update: bool = False,
    activate_only: bool = False,
    concurrency: int = DEFAULT_CONCURRENCY,
    clone_depth: int = DEFAULT_CLONE_DEPTH,
) -> None:
    """Clones repo and syncs/activates all skills to Google Cloud Agent Registry in parallel."""
    client = AgentRegistryGcloudClient(project_id=project_id, location=location)

    if activate_only:
        _, failed_count = activate_all_draft_skills(client, concurrency=concurrency)
        if failed_count > 0:
            sys.exit(1)
        return

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = pathlib.Path(temp_dir)
        clone_path = temp_path / "repo"
        logger.info("Cloning skills repository from %s ...", repo_url)

        try:
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    str(clone_depth),
                    repo_url,
                    str(clone_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error("Failed to clone repository: %s\nStderr: %s", e, e.stderr)
            sys.exit(1)

        discovered = discover_skills(clone_path)
        logger.info(
            "Discovered %d skills with SKILL.md in repository:", len(discovered)
        )

        logger.info(
            "\nConnecting to Google Cloud Agent Registry (project: %s, location: %s)...",
            project_id,
            location,
        )
        existing_skills = client.list_existing_skills()
        logger.info(
            "Found %d existing skill entries in Agent Registry.", len(existing_skills)
        )

        if dry_run:
            logger.info(
                "\n[DRY RUN] Evaluating sync status for %d skills:", len(discovered)
            )
            for item in discovered:
                skill_id = item["skill_id"]
                git_time = item.get("git_commit_time")
                matched_item = existing_skills.get(skill_id) or existing_skills.get(
                    f"private-{skill_id}"
                )
                up_to_date = (
                    is_skill_up_to_date(matched_item, git_time)
                    if not force_update
                    else False
                )

                git_str = git_time.isoformat()[:19] if git_time else "N/A"
                reg_str = (
                    (
                        (
                            matched_item.get("updateTime")
                            or matched_item.get("createTime")
                            or ""
                        )[:19]
                    )
                    if matched_item
                    else "None"
                )
                status = (
                    "✓ Up-to-date (Would Skip)"
                    if up_to_date
                    else "⟳ Needs Sync (Would Sync)"
                )
                logger.info(
                    "  - %-38s | Git: %-19s | Registry: %-19s | %s",
                    skill_id,
                    git_str,
                    reg_str,
                    status,
                )
            return

        to_sync: list[dict[str, Any]] = []
        skipped_count = 0

        for item in discovered:
            skill_id = item["skill_id"]
            git_commit_time = item.get("git_commit_time")
            matched_item = existing_skills.get(skill_id) or existing_skills.get(
                f"private-{skill_id}"
            )

            # Incremental Change Check: skip if already active and up to date
            if not force_update and is_skill_up_to_date(matched_item, git_commit_time):
                reg_ts = (
                    matched_item.get("updateTime")
                    or matched_item.get("createTime")
                    or ""
                )[:19]
                git_ts = git_commit_time.isoformat()[:19] if git_commit_time else "N/A"
                logger.info(
                    "✓ Skill '%s' is up-to-date (Registry: %s > Git: %s) -> Skipping",
                    skill_id,
                    reg_ts,
                    git_ts,
                )
                skipped_count += 1
            else:
                to_sync.append(item)

        if not to_sync:
            logger.info(
                "\nAll %d skills are up-to-date. Nothing to sync.", skipped_count
            )
            return

        effective_concurrency = max(1, min(concurrency, len(to_sync)))
        logger.info(
            "\nSyncing %d skills in parallel (concurrency: %d)...",
            len(to_sync),
            effective_concurrency,
        )

        success_count = 0
        failure_count = 0
        failed_skills: list[tuple[str, str | None]] = []

        with ThreadPoolExecutor(max_workers=effective_concurrency) as executor:
            futures = {
                executor.submit(
                    _sync_single_skill,
                    item,
                    client,
                    temp_path,
                    existing_skills,
                ): item["skill_id"]
                for item in to_sync
            }
            for future in as_completed(futures):
                skill_id, success, err = future.result()
                if success:
                    success_count += 1
                else:
                    failure_count += 1
                    failed_skills.append((skill_id, err))

        logger.info(
            "\nSync complete. Successful: %d, Skipped (up-to-date): %d, Failed: %d",
            success_count,
            skipped_count,
            failure_count,
        )
        if failed_skills:
            logger.error("\nFailed skills summary:")
            for s_id, err in failed_skills:
                logger.error("  - %s: %s", s_id, err)
            sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync and activate skills from GitHub in Google Cloud Agent Registry with change detection and parallel sync."
    )
    parser.add_argument(
        "--project",
        default=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        help="GCP Project ID (defaults to GOOGLE_CLOUD_PROJECT env var).",
    )
    parser.add_argument(
        "--location",
        default=os.environ.get("AGENT_REGISTRY_LOCATION")
        or os.environ.get("SKILLS_REGISTRY_LOCATION")
        or os.environ.get("GOOGLE_CLOUD_LOCATION")
        or DEFAULT_LOCATION,
        help=f"Agent Registry location (defaults to {DEFAULT_LOCATION}).",
    )
    parser.add_argument(
        "--repo-url",
        default=DEFAULT_REPO_URL,
        help=f"Skills Git repository URL (defaults to {DEFAULT_REPO_URL}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform a dry run and evaluate which skills are up-to-date vs need sync.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force upload revisions for all skills even if they are up-to-date.",
    )
    parser.add_argument(
        "--activate-all",
        action="store_true",
        help="Activate all draft skills in the Agent Registry without re-syncing from GitHub.",
    )
    parser.add_argument(
        "--concurrency",
        "-c",
        type=int,
        default=int(
            os.environ.get("AGENT_REGISTRY_SYNC_CONCURRENCY", str(DEFAULT_CONCURRENCY))
        ),
        help=f"Number of parallel worker threads for syncing skills (defaults to {DEFAULT_CONCURRENCY}).",
    )
    parser.add_argument(
        "--clone-depth",
        type=int,
        default=int(
            os.environ.get("AGENT_REGISTRY_CLONE_DEPTH", str(DEFAULT_CLONE_DEPTH))
        ),
        help=f"Git clone depth for change detection history (defaults to {DEFAULT_CLONE_DEPTH}).",
    )

    args = parser.parse_args()

    if not args.dry_run and not args.project:
        logger.error(
            "Error: --project or GOOGLE_CLOUD_PROJECT environment variable must be specified."
        )
        sys.exit(1)

    sync_skills(
        project_id=args.project or "dry-run-project",
        location=args.location,
        repo_url=args.repo_url,
        dry_run=args.dry_run,
        force_update=args.force,
        activate_only=args.activate_all,
        concurrency=args.concurrency,
        clone_depth=args.clone_depth,
    )


if __name__ == "__main__":
    main()
