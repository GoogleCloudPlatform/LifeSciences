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

"""Tests for core/download.py and databases.yaml manifest."""

import pytest

from foldrun_app.core.download import (
    build_script,
    get_databases_for_models,
    load_manifest,
)

# ------------------------------------------------------------------ #
# databases.yaml validation
# ------------------------------------------------------------------ #


class TestDatabasesYaml:
    """Validate the databases.yaml manifest structure."""

    @pytest.fixture(scope="class")
    def manifest(self):
        return load_manifest()

    def test_manifest_has_databases(self, manifest):
        assert "databases" in manifest
        assert len(manifest["databases"]) > 0

    def test_manifest_has_modes(self, manifest):
        assert "modes" in manifest

    def test_all_entries_have_required_fields(self, manifest):
        """Every database entry must have models, display_name, nfs_path."""
        for name, db in manifest["databases"].items():
            assert "models" in db or "model" in db, f"'{name}' missing 'models'"
            assert "display_name" in db, f"'{name}' missing 'display_name'"
            assert "nfs_path" in db, f"'{name}' missing 'nfs_path'"

    def test_all_entries_have_source_or_script(self, manifest):
        """Every database must have either 'source' or 'script'."""
        for name, db in manifest["databases"].items():
            assert "source" in db or "script" in db, f"'{name}' has neither 'source' nor 'script'"

    def test_nfs_paths_are_unique(self, manifest):
        """No two databases should write to the same NFS path."""
        paths = [db["nfs_path"] for db in manifest["databases"].values()]
        assert len(paths) == len(set(paths)), (
            f"Duplicate nfs_paths: {[p for p in paths if paths.count(p) > 1]}"
        )

    def test_modes_reference_valid_databases(self, manifest):
        """All databases listed in modes sections must exist in databases."""
        db_names = set(manifest["databases"].keys())
        for model, modes in manifest.get("modes", {}).items():
            for mode_name, db_list in modes.items():
                for db in db_list:
                    assert db in db_names, (
                        f"Mode '{model}/{mode_name}' references unknown database '{db}'"
                    )

    def test_extract_values_are_valid(self, manifest):
        """Extract field must be one of the supported values."""
        valid = {"gunzip", "tar", "tar_strip", "none"}
        for name, db in manifest["databases"].items():
            if "extract" in db:
                assert db["extract"] in valid, (
                    f"'{name}' has invalid extract '{db['extract']}'. Valid: {valid}"
                )

    def test_af2_databases_present(self, manifest):
        """AF2 model has the expected core databases."""
        from foldrun_app.core.download import _get_models

        af2_dbs = {name for name, db in manifest["databases"].items() if "af2" in _get_models(db)}
        expected = {"uniref90", "mgnify", "alphafold_params", "pdb_seqres"}
        assert expected.issubset(af2_dbs), f"Missing AF2 databases: {expected - af2_dbs}"

    def test_of3_databases_present(self, manifest):
        """OF3 model has the expected databases."""
        from foldrun_app.core.download import _get_models

        of3_dbs = {name for name, db in manifest["databases"].items() if "of3" in _get_models(db)}
        expected = {"of3_params", "of3_ccd", "uniref90", "mgnify"}
        assert expected.issubset(of3_dbs), f"Missing OF3 databases: {expected - of3_dbs}"


# ------------------------------------------------------------------ #
# build_script
# ------------------------------------------------------------------ #


class TestBuildScript:
    """Test script generation from YAML entries."""

    def test_source_with_gunzip(self):
        config = {"source": "https://example.com/data.gz", "extract": "gunzip"}
        script = build_script("test_db", config, "/mnt/nfs/test")
        assert "aria2c" in script
        assert "https://example.com/data.gz" in script
        assert "gunzip" in script
        assert "/mnt/nfs/test" in script

    def test_source_with_tar(self):
        config = {"source": "https://example.com/data.tar.gz", "extract": "tar"}
        script = build_script("test_db", config, "/mnt/nfs/test")
        assert "tar xf" in script
        assert "rm -f" in script  # cleanup tar file

    def test_source_with_tar_strip(self):
        config = {"source": "https://example.com/data.tar", "extract": "tar_strip"}
        script = build_script("test_db", config, "/mnt/nfs/test")
        assert "--strip-components=1" in script

    def test_source_no_extract(self):
        config = {"source": "https://example.com/data.txt", "extract": "none"}
        script = build_script("test_db", config, "/mnt/nfs/test")
        assert "aria2c" in script
        assert "gunzip" not in script
        assert "tar" not in script

    def test_source_default_extract_is_none(self):
        config = {"source": "https://example.com/data.txt"}
        script = build_script("test_db", config, "/mnt/nfs/test")
        assert "gunzip" not in script
        assert "tar" not in script

    def test_custom_script(self):
        config = {"script": "rsync foo {dest}/bar\necho done"}
        script = build_script("test_db", config, "/mnt/nfs/test")
        assert "rsync foo /mnt/nfs/test/bar" in script
        assert "echo done" in script

    def test_custom_script_with_braces(self):
        """Scripts with bash {} (find -exec) should not break."""
        config = {"script": "find {dest} -name '*.gz' -exec gunzip {} +"}
        script = build_script("test_db", config, "/mnt/nfs/test")
        assert "find /mnt/nfs/test" in script
        assert "-exec gunzip {} +" in script

    def test_no_source_no_script_raises(self):
        with pytest.raises(ValueError, match="neither 'script' nor 'source'"):
            build_script("test_db", {}, "/mnt/nfs/test")

    def test_invalid_extract_raises(self):
        config = {"source": "https://example.com/x", "extract": "bzip2"}
        with pytest.raises(ValueError, match="Unknown extract"):
            build_script("test_db", config, "/mnt/nfs/test")

    def test_all_yaml_entries_build_without_error(self):
        """Every database in databases.yaml produces a valid script."""
        manifest = load_manifest()
        for name, db in manifest["databases"].items():
            script = build_script(name, db, f"/mnt/nfs/{db['nfs_path']}")
            assert len(script) > 0, f"Empty script for '{name}'"


# ------------------------------------------------------------------ #
# get_databases_for_models
# ------------------------------------------------------------------ #


class TestGetDatabasesForModels:
    """Test model/mode filtering logic."""

    @pytest.fixture(scope="class")
    def manifest(self):
        return load_manifest()

    def test_filter_by_model(self, manifest):
        from foldrun_app.core.download import _get_models

        dbs = get_databases_for_models(manifest, ["af2"])
        assert all("af2" in _get_models(manifest["databases"][db]) for db in dbs)
        assert len(dbs) > 0

    def test_filter_by_mode(self, manifest):
        dbs = get_databases_for_models(manifest, ["af2"], mode="reduced")
        assert "small_bfd" in dbs
        assert "bfd" not in dbs

    def test_full_mode(self, manifest):
        dbs = get_databases_for_models(manifest, ["af2"], mode="full")
        assert "bfd" in dbs
        assert "small_bfd" not in dbs

    def test_of3_core_mode(self, manifest):
        dbs = get_databases_for_models(manifest, ["of3"], mode="core")
        assert "of3_params" in dbs
        assert "of3_ccd" in dbs
        assert "rfam" not in dbs

    def test_of3_full_mode(self, manifest):
        dbs = get_databases_for_models(manifest, ["of3"], mode="full")
        assert "of3_params" in dbs
        assert "of3_ccd" in dbs
        assert "uniref90" in dbs  # shared DB included in full mode

    def test_multiple_models(self, manifest):
        from foldrun_app.core.download import _get_models

        dbs = get_databases_for_models(manifest, ["af2", "of3"])
        all_models = set()
        for db in dbs:
            all_models.update(_get_models(manifest["databases"][db]))
        assert "af2" in all_models
        assert "of3" in all_models

    def test_shared_databases_not_duplicated(self, manifest):
        """When requesting multiple models, shared DBs appear only once."""
        dbs = get_databases_for_models(manifest, ["af2", "of3"])
        assert dbs.count("uniref90") == 1
        assert dbs.count("mgnify") == 1

    def test_of3_includes_shared_protein_dbs(self, manifest):
        """OF3 alone pulls in shared protein MSA databases."""
        dbs = get_databases_for_models(manifest, ["of3"])
        assert "uniref90" in dbs
        assert "mgnify" in dbs
        assert "of3_params" in dbs

    def test_invalid_mode_falls_back_to_all(self, manifest):
        """Unknown mode for a model falls back to all databases for that model."""
        dbs = get_databases_for_models(manifest, ["af2"], mode="nonexistent")
        # Should return all AF2 databases (fallback), not raise
        assert len(dbs) > 0

    def test_unknown_model_returns_empty(self, manifest):
        dbs = get_databases_for_models(manifest, ["nonexistent_model"])
        assert dbs == []

    def test_boltz_gets_shared_dbs(self, manifest):
        """Boltz (not yet fully implemented) still gets shared protein DBs."""
        dbs = get_databases_for_models(manifest, ["boltz"])
        assert "uniref90" in dbs
        assert "mgnify" in dbs


# ------------------------------------------------------------------ #
# AF2 registry consistency
# ------------------------------------------------------------------ #


class TestAF2RegistryConsistency:
    """Verify AF2 download tool registry matches databases.yaml."""

    def test_registry_loaded_from_yaml(self):
        from foldrun_app.core.download import _get_models
        from foldrun_app.models.af2.tools.download_database import (
            DATABASE_REGISTRY,
            DATABASE_SUBDIRS,
            VALID_DATABASE_NAMES,
        )

        manifest = load_manifest()
        af2_dbs = {name for name, db in manifest["databases"].items() if "af2" in _get_models(db)}
        assert set(DATABASE_REGISTRY.keys()) == af2_dbs
        assert set(DATABASE_SUBDIRS.keys()) == af2_dbs
        assert set(VALID_DATABASE_NAMES) == af2_dbs

    def test_subdirs_match_nfs_path(self):
        from foldrun_app.models.af2.tools.download_database import DATABASE_SUBDIRS

        manifest = load_manifest()
        for name, subdir in DATABASE_SUBDIRS.items():
            assert subdir == manifest["databases"][name]["nfs_path"], (
                f"Subdir mismatch for '{name}': {subdir} vs {manifest['databases'][name]['nfs_path']}"
            )
