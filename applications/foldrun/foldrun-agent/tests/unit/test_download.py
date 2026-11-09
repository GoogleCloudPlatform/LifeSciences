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
        assert expected.issubset(af2_dbs := of3_dbs), f"Missing OF3 databases: {expected - af2_dbs}"

    def test_of3_params_checks_expected_file_in_gcs(self, manifest):
        """of3_params specifies expected_file so legacy weights in of3/params/ do not falsely satisfy check_existing."""
        from unittest.mock import MagicMock, patch

        from foldrun_app.core.download import check_existing
        from foldrun_app.models.of3.config import DEFAULT_OF3_CHECKPOINT

        assert manifest["databases"]["of3_params"].get("expected_file") == DEFAULT_OF3_CHECKPOINT

        with patch("foldrun_app.core.download.storage.Client") as mock_client:
            mock_bucket = MagicMock()
            mock_client.return_value.bucket.return_value = mock_bucket
            mock_bucket.list_blobs.return_value = []

            res = check_existing(["of3_params"], "test-gdbs", manifest)
            assert res == {"of3_params": False}
            mock_bucket.list_blobs.assert_called_once_with(
                prefix=f"of3/params/{DEFAULT_OF3_CHECKPOINT}", max_results=1
            )

    def test_boltz2_cache_checks_expected_file_in_gcs(self, manifest):
        """boltz2_cache specifies expected_file so partial boltz2/cache/ prefixes do not falsely satisfy check_existing."""
        from unittest.mock import MagicMock, patch

        from foldrun_app.core.download import check_existing

        assert manifest["databases"]["boltz2_cache"].get("expected_file") == "boltz2_conf.ckpt"

        with patch("foldrun_app.core.download.storage.Client") as mock_client:
            mock_bucket = MagicMock()
            mock_client.return_value.bucket.return_value = mock_bucket
            mock_bucket.list_blobs.return_value = []

            res = check_existing(["boltz2_cache"], "test-gdbs", manifest)
            assert res == {"boltz2_cache": False}
            mock_bucket.list_blobs.assert_called_once_with(
                prefix="boltz2/cache/boltz2_conf.ckpt", max_results=1
            )


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

    def test_af2_core_mode(self, manifest):
        dbs = get_databases_for_models(manifest, ["af2"], mode="core")
        assert dbs == ["alphafold_params"]

    def test_of3_core_mode(self, manifest):
        dbs = get_databases_for_models(manifest, ["of3"], mode="core")
        assert dbs == ["of3_params", "of3_ccd"]
        assert "rfam" not in dbs

    def test_boltz_and_boltz2_core_mode(self, manifest):
        assert get_databases_for_models(manifest, ["boltz"], mode="core") == ["boltz2_cache"]
        assert get_databases_for_models(manifest, ["boltz2"], mode="core") == ["boltz2_cache"]

    def test_combined_models_core_mode(self, manifest):
        dbs = get_databases_for_models(manifest, ["af2", "of3", "boltz"], mode="core")
        assert dbs == ["alphafold_params", "of3_params", "of3_ccd", "boltz2_cache"]

    def test_setup_data_respects_download_mode_core_env_var(self, monkeypatch, capsys):
        """scripts/setup_data.py resolves DOWNLOAD_MODE=core across af2, of3, and boltz."""
        import importlib.util
        import sys
        from pathlib import Path
        from unittest.mock import patch

        script_path = Path(__file__).resolve().parents[2] / "scripts" / "setup_data.py"
        spec = importlib.util.spec_from_file_location("setup_data_under_test", script_path)
        assert spec is not None and spec.loader is not None
        setup_data = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup_data)

        monkeypatch.setenv("DOWNLOAD_MODELS", "af2,of3,boltz")
        monkeypatch.setenv("DOWNLOAD_MODE", "core")
        monkeypatch.setattr(sys, "argv", ["setup_data.py", "--dry-run", "--force"])

        class _DummyCoreConfig:
            databases_bucket_name = ""

            def __init__(self, *args, **kwargs):
                pass

        with patch.object(setup_data, "CoreConfig", _DummyCoreConfig):
            setup_data.main()

        out = capsys.readouterr().out
        assert "alphafold_params" in out
        assert "of3_params" in out
        assert "of3_ccd" in out
        assert "boltz2_cache" in out
        assert "uniref90" not in out
        assert "mgnify" not in out

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
# Security: Command injection prevention in Batch job scripts
# ------------------------------------------------------------------ #


class TestDownloadSecurity:
    """Verify protection against command injection in core.download Batch scripts."""

    def test_submit_download_rejects_malicious_dest_path(self):
        """submit_download validates dest_path against command injection and path traversal."""
        from foldrun_app.core.download import submit_download

        for bad_nfs_path in [
            "uniref90/$(id)",
            "uniref90; rm -rf /",
            "../../etc/passwd",
            "uniref90/path with spaces",
        ]:
            db_config = {
                "models": ["af2"],
                "display_name": "UniRef90",
                "nfs_path": bad_nfs_path,
                "source": "https://example.com/uniref90.fasta.gz",
            }
            with pytest.raises(ValueError, match="Invalid dest_path"):
                submit_download(
                    db_name="uniref90",
                    db_config=db_config,
                    project_id="proj",
                    region="us-central1",
                    zone="us-central1-a",
                    filestore_ip="10.0.0.1",
                    filestore_network="default",
                    nfs_share="share",
                    nfs_mount="/mnt/nfs",
                    gcs_bucket="my-bucket",
                )

    def test_submit_download_gcs_sync_conditional_assignment(self):
        """submit_download uses safe conditional shell assignment without double-quoting single-quoted literals."""
        import subprocess
        from unittest.mock import patch

        from foldrun_app.core.download import submit_download

        db_config = {
            "models": ["af2"],
            "display_name": "UniRef90",
            "nfs_path": "uniref90",
            "source": "https://example.com/uniref90.fasta.gz",
        }
        with patch("foldrun_app.core.batch.submit_batch_job") as mock_submit:
            mock_submit.return_value = {
                "job_id": "test-123",
                "job_name": "test",
                "console_url": "http://test",
            }
            result = submit_download(
                db_name="uniref90",
                db_config=db_config,
                project_id="proj",
                region="us-central1",
                zone="us-central1-a",
                filestore_ip="10.0.0.1",
                filestore_network="default",
                nfs_share="share",
                nfs_mount="/mnt/nfs/foldrun",
                gcs_bucket="my-bucket",
            )
            assert result["status"] == "submitted"
            script = mock_submit.call_args[1]["script"]

        assert "${GCS_SYNC_SOURCE:-/" not in script
        assert 'if [ -n "${GCS_SYNC_SOURCE:-}" ]; then' in script

        # Verify shell evaluation when GCS_SYNC_SOURCE is unset vs set
        snippet = "\n".join(
            script.splitlines()[
                script.splitlines().index(
                    'if [ -n "${GCS_SYNC_SOURCE:-}" ]; then'
                ) : script.splitlines().index("fi") + 1
            ]
        )
        default_val = subprocess.check_output(
            ["bash", "-c", f"{snippet}\nprintf '%s' \"$SYNC_SOURCE\""],
            env={},
            text=True,
        )
        assert default_val == "/mnt/nfs/foldrun/uniref90/"

        override_val = subprocess.check_output(
            ["bash", "-c", f"{snippet}\nprintf '%s' \"$SYNC_SOURCE\""],
            env={"GCS_SYNC_SOURCE": "/mnt/scratch/uniref90/"},
            text=True,
        )
        assert override_val == "/mnt/scratch/uniref90/"

    def test_submit_download_sanitizes_model_underscores_in_batch_job_id(self):
        """Cloud Batch job_id replaces underscores in model and db_name with hyphens."""
        import re
        from unittest.mock import patch

        from foldrun_app.core.download import submit_download

        db_config = {
            "models": ["custom_model_v2"],
            "display_name": "Custom Params",
            "nfs_path": "custom/params",
            "source": "https://example.com/params.tar",
        }
        with patch("foldrun_app.core.batch.submit_batch_job") as mock_submit:
            mock_submit.return_value = {
                "job_id": "ok",
                "job_name": "ok",
                "console_url": "http://test",
            }
            submit_download(
                db_name="custom_params_db",
                db_config=db_config,
                project_id="proj",
                region="us-central1",
                zone="us-central1-a",
                filestore_ip="10.0.0.1",
                filestore_network="default",
                nfs_share="share",
                nfs_mount="/mnt/nfs/foldrun",
                gcs_bucket="my-bucket",
            )
            job_id = mock_submit.call_args[1]["job_id"]
            assert "_" not in job_id
            assert job_id.startswith("custom-model-v2-dl-custom-params-db-")
            assert re.fullmatch(r"^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$", job_id)

    def test_load_manifest_falls_back_to_bundled_path(self, tmp_path, monkeypatch):
        """load_manifest falls back to BUNDLED_DATABASES_YAML when DATABASES_YAML is absent."""
        import foldrun_app.core.download as dl_mod

        missing_parent = tmp_path / "missing_parent" / "databases.yaml"
        bundled_yaml = tmp_path / "bundled" / "databases.yaml"
        bundled_yaml.parent.mkdir(parents=True)
        bundled_yaml.write_text("databases:\n  test_db:\n    nfs_path: test\nmodes: {}\n")

        monkeypatch.setattr(dl_mod, "DATABASES_YAML", missing_parent)
        monkeypatch.setattr(dl_mod, "BUNDLED_DATABASES_YAML", bundled_yaml)

        loaded = dl_mod.load_manifest()
        assert "test_db" in loaded["databases"]


# ------------------------------------------------------------------ #
# Security / Reliability: Database setup scripts
# ------------------------------------------------------------------ #


class TestDatabaseSetupScripts:
    """Validate database setup scripts for multi-line FASTA handling and background exit checks."""

    def test_pdb_seqres_preserves_multiline_fasta_sequences(self, tmp_path):
        """pdb_seqres filter preserves multi-line protein sequences and filters non-proteins."""
        import subprocess

        manifest = load_manifest()
        script = manifest["databases"]["pdb_seqres"]["script"]

        # Extract the filter line that writes to {dest}/pdb_seqres_filtered.txt
        filter_lines = [
            line.strip()
            for line in script.splitlines()
            if "pdb_seqres_filtered.txt" in line and not line.strip().startswith("mv ")
        ]
        assert len(filter_lines) == 1, f"Expected 1 filter line, found: {filter_lines}"
        filter_cmd = filter_lines[0].replace("{dest}", str(tmp_path))

        # Sample input with multi-line protein, multi-line RNA, and single-line protein
        sample_fasta = (
            ">1abc_A mol:protein length:66  TEST PROTEIN 1\n"
            "MKTAYIAKQRQISFVKSHFSRQ\n"
            "LEERLGLIEVQAPILSRVGDGT\n"
            "QDNLSGAEKAVQVKVKALPDAQ\n"
            ">1xyz_B mol:na length:32  TEST RNA\n"
            "ACGUACGUACGUACGU\n"
            "CGUACGUACGUACGUA\n"
            ">2def_A mol:protein length:10  TEST PROTEIN 2\n"
            "ACDEFGHIKL\n"
        )
        input_file = tmp_path / "pdb_seqres.txt"
        input_file.write_text(sample_fasta)

        subprocess.run(["bash", "-e", "-c", filter_cmd], check=True)

        filtered_content = (tmp_path / "pdb_seqres_filtered.txt").read_text()
        expected_content = (
            ">1abc_A mol:protein length:66  TEST PROTEIN 1\n"
            "MKTAYIAKQRQISFVKSHFSRQ\n"
            "LEERLGLIEVQAPILSRVGDGT\n"
            "QDNLSGAEKAVQVKVKALPDAQ\n"
            ">2def_A mol:protein length:10  TEST PROTEIN 2\n"
            "ACDEFGHIKL\n"
        )
        assert filtered_content == expected_content

    def test_pdb_mmcif_waits_for_background_rsync_exit_status(self):
        """pdb_mmcif setup waits on $RSYNC_PID after loop so non-zero exit fails under set -e."""
        import subprocess

        manifest = load_manifest()
        script = manifest["databases"]["pdb_mmcif"]["script"]
        lines = [line.strip() for line in script.splitlines() if line.strip()]

        # Verify wait $RSYNC_PID immediately follows the monitoring loop's `done`
        done_indices = [i for i, line in enumerate(lines) if line == "done"]
        assert len(done_indices) == 1, (
            f"Expected 1 'done' statement in pdb_mmcif script, got {done_indices}"
        )
        done_idx = done_indices[0]
        assert done_idx + 1 < len(lines) and lines[done_idx + 1] == "wait $RSYNC_PID", (
            "Expected 'wait $RSYNC_PID' immediately after the background rsync monitoring loop 'done'"
        )

        # Verify behavior under `set -e` when background process fails
        loop_and_post_check = "\n".join(lines[done_idx - 1 : done_idx + 2])
        test_harness = (
            "set -e\n"
            "(exit 23) &\n"
            "RSYNC_PID=$!\n"
            "while kill -0 $RSYNC_PID 2>/dev/null; do\n"
            f"  {loop_and_post_check}\n"
            "echo 'SHOULD_NOT_REACH_HERE'\n"
        )
        result = subprocess.run(["bash", "-c", test_harness], capture_output=True, text=True)
        assert result.returncode != 0, (
            "Script should fail under 'set -e' when background rsync exits with non-zero status"
        )
        assert "SHOULD_NOT_REACH_HERE" not in result.stdout

    def test_pdb_mmcif_streams_decompressed_files_directly_to_nfs(self):
        """pdb_mmcif moves decompressed .cif files directly to {dest}/mmcif_files/ so 1x 375 GiB Local SSD does not fill up."""
        manifest = load_manifest()
        script = build_script(
            "pdb_mmcif", manifest["databases"]["pdb_mmcif"], "/mnt/nfs/foldrun/pdb_mmcif"
        )
        assert "rm -rf /mnt/scratch/raw" in script
        assert "mv -t /mnt/nfs/foldrun/pdb_mmcif/mmcif_files/" in script
        assert "/mnt/scratch/pdb_mmcif/mmcif_files" not in script
        assert "cp -au" not in script
        assert "GCS_SYNC_SOURCE" not in script

    def test_boltz2_cache_pins_immutable_commit_and_verifies_sha256(self, tmp_path):
        """boltz2_cache pins Hugging Face commit SHA and fails fast on SHA-256 mismatch before extraction."""
        import subprocess

        manifest = load_manifest()
        script = build_script("boltz2_cache", manifest["databases"]["boltz2_cache"], str(tmp_path))

        assert "resolve/main/" not in script
        assert "model-gateway.boltz.bio" not in script
        assert "6fdef46d763fee7fbb83ca5501ccceff43b85607" in script
        assert "sha256sum -c" in script

        # Extract the sha256sum -c heredoc block and ensure tampered files fail under set -e
        lines = script.splitlines()
        start_idx = next(i for i, ln in enumerate(lines) if "sha256sum -c" in ln)
        end_idx = next(i for i in range(start_idx + 1, len(lines)) if lines[i].strip() == "EOF")
        checksum_block = "\n".join(lines[start_idx : end_idx + 1])

        for fname in ("boltz2_conf.ckpt", "boltz2_aff.ckpt", "mols.tar"):
            (tmp_path / fname).write_bytes(b"corrupted-payload")

        res = subprocess.run(["bash", "-e", "-c", checksum_block], capture_output=True, text=True)
        assert res.returncode != 0

    def test_mmseqs2_conversion_pins_tagged_release_and_verifies_sha256(self):
        """submit_mmseqs2_conversions uses a tagged MMseqs2 release with SHA-256 verification."""
        from unittest.mock import patch

        from foldrun_app.core.download import submit_mmseqs2_conversions

        with patch("foldrun_app.core.batch.submit_batch_job") as mock_submit:
            mock_submit.return_value = {
                "job_id": "mmseqs-1",
                "job_name": "mmseqs",
                "console_url": "http://test",
            }
            out = submit_mmseqs2_conversions(
                project_id="proj",
                region="us-central1",
                zone="us-central1-a",
                filestore_ip="10.0.0.1",
                filestore_network="default",
                nfs_share="datasets",
                nfs_mount="/mnt/nfs/foldrun",
                gcs_bucket="my-bucket",
                databases=["uniref90"],
            )
            assert out["status"] == "submitted"
            script = mock_submit.call_args[1]["script"]
            assert "mmseqs.com/latest" not in script
            assert (
                "soedinglab/MMseqs2/releases/download/18-8cc5c/mmseqs-linux-avx2.tar.gz" in script
            )
            assert "sha256sum -c -" in script
            assert "rm -rf /mnt/localssd && ln -sfnT /mnt/scratch /mnt/localssd" in script
            assert (
                "rm -rf /mnt/localssd && ln -sfnT '/mnt/nfs/foldrun/uniref90/mmseqs_scratch' /mnt/localssd"
                in script
            )
