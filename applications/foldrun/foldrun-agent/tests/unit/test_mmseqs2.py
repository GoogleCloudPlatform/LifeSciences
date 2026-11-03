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

"""Tests for MMseqs2-GPU integration across the pipeline.

Covers:
- Config constants (paths, machine types)
- Download scripts include mmseqs createdb commands
- ConvertMMseqs2Tool validation logic
- Hardware config with msa_method='mmseqs2'
- msa_method validation in submission tools
- Pipeline loading with msa_method
- Environment variable setup for mmseqs2
"""

import os
from unittest.mock import MagicMock, patch

# ------------------------------------------------------------------ #
# Config constants
# ------------------------------------------------------------------ #


class TestMMseqs2Config:
    """Verify MMseqs2 config constants exist with correct defaults."""

    def test_mmseqs2_db_paths_exist(self, mock_env_vars):
        """MMseqs2 database path constants are defined."""
        from foldrun_app.models.af2.pipeline import config

        assert hasattr(config, "UNIREF90_MMSEQS_PATH")
        assert hasattr(config, "MGNIFY_MMSEQS_PATH")
        assert hasattr(config, "SMALL_BFD_MMSEQS_PATH")

    def test_mmseqs2_db_path_defaults(self, mock_env_vars):
        """Default MMseqs2 database paths follow naming convention."""
        from foldrun_app.models.af2.pipeline import config

        assert "uniref90_mmseqs" in config.UNIREF90_MMSEQS_PATH
        assert "mgnify_mmseqs" in config.MGNIFY_MMSEQS_PATH
        assert "small_bfd_mmseqs" in config.SMALL_BFD_MMSEQS_PATH

    def test_mmseqs2_machine_type_constants(self, mock_env_vars):
        """MMseqs2 GPU machine type constants are defined."""
        from foldrun_app.models.af2.pipeline import config

        assert config.MMSEQS2_DATA_PIPELINE_MACHINE_TYPE == "g2-standard-12"
        assert config.MMSEQS2_ACCELERATOR_TYPE == "NVIDIA_L4"
        assert config.MMSEQS2_ACCELERATOR_COUNT == 1


# ------------------------------------------------------------------ #
# Download scripts include mmseqs createdb
# ------------------------------------------------------------------ #


class TestDownloadScriptsMMseqs2:
    """Verify download scripts are download-only and conversion is separate."""

    def test_download_scripts_are_download_only(self):
        """Download scripts should NOT include mmseqs createdb (conversion is separate)."""
        from foldrun_app.core.download import _get_models, build_script, load_manifest

        databases = load_manifest().get("databases", {})
        for db_name, db_info in databases.items():
            if "af2" not in _get_models(db_info):
                continue
            script = build_script(db_name, db_info, "/tmp/test")
            assert "mmseqs createdb" not in script, (
                f"Download script for '{db_name}' should not include mmseqs createdb "
                "(conversion is handled by submit_mmseqs2_conversions in a separate batch job)"
            )

    def test_indexable_databases_flag_conversion_needed(self):
        """MMSEQS2-indexable databases signal that conversion is needed."""
        from foldrun_app.core.download import (
            MMSEQS2_INDEXABLE_DATABASES,
            MMSEQS2_LOCAL_SSD_COUNT,
            MMSEQS2_MACHINE_TYPE,
        )

        for db_name in MMSEQS2_INDEXABLE_DATABASES:
            assert "fasta_file" in MMSEQS2_INDEXABLE_DATABASES[db_name]
            assert "mmseqs_name" in MMSEQS2_INDEXABLE_DATABASES[db_name]
        assert MMSEQS2_MACHINE_TYPE == "n1-highmem-32"
        assert MMSEQS2_LOCAL_SSD_COUNT == 2

    def test_mmseqs2_indexable_databases_registry(self):
        """MMSEQS2_INDEXABLE_DATABASES contains exactly 3 FASTA databases."""
        from foldrun_app.core.download import MMSEQS2_INDEXABLE_DATABASES

        assert set(MMSEQS2_INDEXABLE_DATABASES.keys()) == {"uniref90", "mgnify", "small_bfd"}

    def test_mmseqs2_indexable_databases_have_required_keys(self):
        """Each indexable database entry has fasta_file and mmseqs_name."""
        from foldrun_app.core.download import MMSEQS2_INDEXABLE_DATABASES

        for db_name, info in MMSEQS2_INDEXABLE_DATABASES.items():
            assert "fasta_file" in info, f"{db_name} missing fasta_file"
            assert "mmseqs_name" in info, f"{db_name} missing mmseqs_name"

    def test_default_download_machine_type(self):
        """Default download machine type is n1-standard-4 (cheap, network-bound)."""
        import inspect

        from foldrun_app.core.download import submit_download

        source = inspect.getsource(submit_download)
        assert "n1-standard-4" in source

    def test_index_splits_are_single_pass(self):
        """All indexable databases use --split 1 (single-pass on n1-highmem-32)."""
        from foldrun_app.core.download import MMSEQS2_INDEXABLE_DATABASES

        for db_name, info in MMSEQS2_INDEXABLE_DATABASES.items():
            assert info.get("index_splits", 1) == 1, (
                f"{db_name} should use index_splits=1 for single-pass indexing"
            )

    def test_submit_download_does_not_chain_conversion(self):
        """submit_download script should not contain createdb/createindex."""
        from foldrun_app.core.download import load_manifest, submit_download

        mgnify_cfg = load_manifest()["databases"]["mgnify"]
        with patch("foldrun_app.core.batch.submit_batch_job") as mock_submit:
            mock_submit.return_value = {
                "job_id": "test-123",
                "job_name": "test",
                "console_url": "http://test",
            }
            submit_download(
                db_name="mgnify",
                db_config=mgnify_cfg,
                project_id="proj",
                region="us-central1",
                zone="us-central1-a",
                filestore_ip="10.0.0.1",
                filestore_network="projects/123/global/networks/default",
                nfs_share="/datasets",
                nfs_mount="/mnt/nfs/foldrun",
                gcs_bucket="db-bucket",
            )

            script = mock_submit.call_args[1]["script"]
            assert "createdb" not in script, "Download should not chain MMseqs2 conversion"
            assert "createindex" not in script, "Download should not chain MMseqs2 conversion"


# ------------------------------------------------------------------ #
# Network qualification
# ------------------------------------------------------------------ #


class TestNetworkQualification:
    """Verify _get_filestore_info always returns fully qualified network."""

    def test_short_network_name_gets_qualified(self, mock_env_vars):
        """Filestore API returning short name gets fully qualified."""
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            from foldrun_app.models.af2.base import AF2Tool
            from foldrun_app.models.af2.config import Config

            class DummyTool(AF2Tool):
                def run(self, arguments):
                    return {}

            tool_config = {"name": "test", "description": "test"}
            config = Config()
            tool = DummyTool(tool_config, config)

            mock_instance = MagicMock()
            mock_instance.networks = [MagicMock()]
            mock_instance.networks[0].ip_addresses = ["10.1.0.2"]
            mock_instance.networks[0].network = "foldrun-network"  # Short name

            mock_project = MagicMock()
            mock_project.name = "projects/1051554583006"

            with patch("google.cloud.filestore_v1.CloudFilestoreManagerClient") as mock_fs:
                mock_fs.return_value.get_instance.return_value = mock_instance
                with patch("google.cloud.resourcemanager_v3.ProjectsClient") as mock_rm:
                    mock_rm.return_value.get_project.return_value = mock_project

                    ip, network = tool._get_filestore_info()

                    assert ip == "10.1.0.2"
                    assert network == "projects/1051554583006/global/networks/foldrun-network"

    def test_full_path_network_gets_requalified(self, mock_env_vars):
        """Filestore API returning project_id path gets converted to project_number."""
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            from foldrun_app.models.af2.base import AF2Tool
            from foldrun_app.models.af2.config import Config

            class DummyTool(AF2Tool):
                def run(self, arguments):
                    return {}

            tool_config = {"name": "test", "description": "test"}
            config = Config()
            tool = DummyTool(tool_config, config)

            mock_instance = MagicMock()
            mock_instance.networks = [MagicMock()]
            mock_instance.networks[0].ip_addresses = ["10.1.0.2"]
            mock_instance.networks[
                0
            ].network = "projects/my-project/global/networks/foldrun-network"

            mock_project = MagicMock()
            mock_project.name = "projects/1051554583006"

            with patch("google.cloud.filestore_v1.CloudFilestoreManagerClient") as mock_fs:
                mock_fs.return_value.get_instance.return_value = mock_instance
                with patch("google.cloud.resourcemanager_v3.ProjectsClient") as mock_rm:
                    mock_rm.return_value.get_project.return_value = mock_project

                    _ip, network = tool._get_filestore_info()

                    assert network == "projects/1051554583006/global/networks/foldrun-network"


# ------------------------------------------------------------------ #
# submit_mmseqs2_conversions validation & local SSD configuration
# ------------------------------------------------------------------ #


class TestSubmitMMseqs2Conversions:
    """Tests for foldrun_app.core.download.submit_mmseqs2_conversions."""

    def _call_convert(self, databases=None):
        from foldrun_app.core.download import submit_mmseqs2_conversions

        return submit_mmseqs2_conversions(
            project_id="test-proj",
            region="us-central1",
            zone="us-central1-a",
            filestore_ip="10.0.0.1",
            filestore_network="projects/123/global/networks/default",
            nfs_share="/datasets",
            nfs_mount="/mnt/nfs/foldrun",
            gcs_bucket="test-databases-bucket",
            databases=databases,
        )

    def test_conversion_passes_local_ssd_count(self):
        """Conversion jobs pass MMSEQS2_LOCAL_SSD_COUNT to submit_batch_job."""
        from foldrun_app.core.download import MMSEQS2_LOCAL_SSD_COUNT

        with patch("foldrun_app.core.batch.submit_batch_job") as mock_submit:
            mock_submit.return_value = {
                "job_id": "test-123",
                "job_name": "test",
                "console_url": "http://test",
            }
            self._call_convert(databases=["uniref90"])
            assert mock_submit.call_args[1]["local_ssd_count"] == MMSEQS2_LOCAL_SSD_COUNT
            assert MMSEQS2_LOCAL_SSD_COUNT == 2

    def test_invalid_database_returns_error(self):
        result = self._call_convert(databases=["invalid_db"])
        assert result["status"] == "error"
        assert "invalid_db" in result["message"]

    def test_mixed_valid_invalid_returns_error(self):
        result = self._call_convert(databases=["uniref90", "bfd"])
        assert result["status"] == "error"
        assert "bfd" in result["message"]

    def test_default_databases_when_none(self):
        with patch("foldrun_app.core.batch.submit_batch_job") as mock_submit:
            mock_submit.return_value = {
                "job_id": "test-123",
                "job_name": "test",
                "console_url": "http://test",
            }
            result = self._call_convert(databases=None)
            assert mock_submit.call_count == 3
            job_ids = [call[1]["job_id"] for call in mock_submit.call_args_list]
            assert any("uniref90" in jid for jid in job_ids)
            assert any("mgnify" in jid for jid in job_ids)
            assert any("small-bfd" in jid for jid in job_ids)
            assert result["status"] == "submitted"
            assert len(result["jobs"]) == 3

    def test_specific_databases_only(self):
        with patch("foldrun_app.core.batch.submit_batch_job") as mock_submit:
            mock_submit.return_value = {
                "job_id": "test-123",
                "job_name": "test",
                "console_url": "http://test",
            }
            result = self._call_convert(databases=["uniref90"])
            assert mock_submit.call_count == 1
            assert "uniref90" in mock_submit.call_args[1]["job_id"]
            assert len(result["jobs"]) == 1

    def test_conversion_script_includes_gpu_preparation_and_tmp_cleanup(self):
        with patch("foldrun_app.core.batch.submit_batch_job") as mock_submit:
            mock_submit.return_value = {
                "job_id": "test-123",
                "job_name": "test",
                "console_url": "http://test",
            }
            self._call_convert(databases=["uniref90"])
            script = mock_submit.call_args[1]["script"]
            assert "RAID-0" in script
            assert "mdadm" in script
            assert "/mnt/localssd" in script
            assert "createdb" in script
            assert "_tmp'" in script
            assert "makepaddedseqdb" in script
            assert "createindex" in script
            assert "--split 1" in script
            assert "--index-subset 2" in script
            assert "--remove-tmp-files 1" in script
            cleanup_lines = [
                line for line in script.split("\n") if "rm -f" in line and "Cleaning" not in line
            ]
            assert any("_tmp" in line for line in cleanup_lines)


# ------------------------------------------------------------------ #
# GPU auto-selection
# ------------------------------------------------------------------ #


class TestRecommendGpu:
    """Tests for AF2Tool._recommend_gpu() static method."""

    def _recommend(self, seq_length, is_multimer=False):
        from foldrun_app.models.af2.base import AF2Tool

        return AF2Tool._recommend_gpu(seq_length, is_multimer)

    # -- Monomer tiers --

    def test_monomer_small(self):
        """Monomer <=1500 residues → A100 (L4 no longer auto-selected)."""
        assert self._recommend(100) == "A100"
        assert self._recommend(499) == "A100"

    def test_monomer_medium(self):
        """Monomer 500-1500 residues → A100."""
        assert self._recommend(500) == "A100"
        assert self._recommend(1000) == "A100"
        assert self._recommend(1500) == "A100"

    def test_monomer_large(self):
        """Monomer >1500 residues → A100_80GB."""
        assert self._recommend(1501) == "A100_80GB"
        assert self._recommend(3000) == "A100_80GB"

    # -- Multimer tiers --

    def test_multimer_small(self):
        """Multimer <1000 total residues → A100."""
        assert self._recommend(500, is_multimer=True) == "A100"
        assert self._recommend(999, is_multimer=True) == "A100"

    def test_multimer_large(self):
        """Multimer >=1000 total residues → A100_80GB."""
        assert self._recommend(1000, is_multimer=True) == "A100_80GB"
        assert self._recommend(2000, is_multimer=True) == "A100_80GB"

    # -- Edge cases --

    def test_zero_length_monomer(self):
        """Zero-length monomer → A100 (L4 no longer auto-selected)."""
        assert self._recommend(0) == "A100"

    def test_zero_length_multimer(self):
        """Zero-length multimer → A100 (smallest multimer tier)."""
        assert self._recommend(0, is_multimer=True) == "A100"


# ------------------------------------------------------------------ #
# Auto-selection in _get_hardware_config
# ------------------------------------------------------------------ #


class TestAutoSelection:
    """Tests for gpu_type='auto' and msa_method='auto' in _get_hardware_config."""

    def _make_tool(self, mock_env_vars):
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            from foldrun_app.models.af2.base import AF2Tool
            from foldrun_app.models.af2.config import Config

            tool_config = {"name": "test_tool", "description": "test"}
            config = Config()
            return AF2Tool(tool_config, config)

    def test_auto_gpu_monomer_small(self, mock_env_vars):
        """gpu_type='auto' for 200-residue monomer → A100 (L4 no longer auto-selected)."""
        tool = self._make_tool(mock_env_vars)
        config = tool._get_hardware_config("auto", seq_length=200, is_multimer=False)
        assert config["predict_accel"] == "NVIDIA_TESLA_A100"

    def test_auto_gpu_monomer_medium(self, mock_env_vars):
        """gpu_type='auto' for 800-residue monomer → A100."""
        tool = self._make_tool(mock_env_vars)
        config = tool._get_hardware_config("auto", seq_length=800, is_multimer=False)
        assert config["predict_accel"] == "NVIDIA_TESLA_A100"

    def test_auto_gpu_monomer_large(self, mock_env_vars):
        """gpu_type='auto' for 2000-residue monomer → A100_80GB."""
        tool = self._make_tool(mock_env_vars)
        config = tool._get_hardware_config("auto", seq_length=2000, is_multimer=False)
        assert config["predict_accel"] == "NVIDIA_A100_80GB"

    def test_auto_gpu_multimer_small(self, mock_env_vars):
        """gpu_type='auto' for 600-residue multimer → A100."""
        tool = self._make_tool(mock_env_vars)
        config = tool._get_hardware_config("auto", seq_length=600, is_multimer=True)
        assert config["predict_accel"] == "NVIDIA_TESLA_A100"

    def test_auto_gpu_multimer_large(self, mock_env_vars):
        """gpu_type='auto' for 1500-residue multimer → A100_80GB."""
        tool = self._make_tool(mock_env_vars)
        config = tool._get_hardware_config("auto", seq_length=1500, is_multimer=True)
        assert config["predict_accel"] == "NVIDIA_A100_80GB"

    def test_explicit_gpu_overrides_auto(self, mock_env_vars):
        """Explicit gpu_type='L4' for large protein bypasses auto-selection."""
        tool = self._make_tool(mock_env_vars)
        config = tool._get_hardware_config("L4", seq_length=2000, is_multimer=False)
        assert config["predict_accel"] == "NVIDIA_L4"

    def test_auto_msa_always_jackhmmer(self, mock_env_vars):
        """msa_method='auto' always selects jackhmmer (default). MMseqs2 is opt-in."""
        tool = self._make_tool(mock_env_vars)
        config1 = tool._get_hardware_config("L4", msa_method="auto", use_small_bfd=True)
        assert config1["msa_method"] == "jackhmmer"
        config2 = tool._get_hardware_config("L4", msa_method="auto", use_small_bfd=False)
        assert config2["msa_method"] == "jackhmmer"

    def test_explicit_msa_overrides_auto(self, mock_env_vars):
        """Explicit msa_method='jackhmmer' with use_small_bfd=True bypasses auto."""
        tool = self._make_tool(mock_env_vars)
        config = tool._get_hardware_config("L4", msa_method="jackhmmer", use_small_bfd=True)
        assert config["msa_method"] == "jackhmmer"

    def test_auto_relax_gpu_downgraded(self, mock_env_vars):
        """Auto-selected A100_80GB has A100 relax GPU (downgraded)."""
        tool = self._make_tool(mock_env_vars)
        config = tool._get_hardware_config("auto", seq_length=2000, is_multimer=False)
        assert config["predict_accel"] == "NVIDIA_A100_80GB"
        assert config["relax_accel"] == "NVIDIA_TESLA_A100"

    def test_config_includes_dws_max_wait(self, mock_env_vars):
        """Hardware config includes dws_max_wait_hours from Config."""
        tool = self._make_tool(mock_env_vars)
        config = tool._get_hardware_config("L4")
        assert "dws_max_wait_hours" in config
        assert config["dws_max_wait_hours"] == 168  # default

    def test_config_includes_parallelism(self, mock_env_vars):
        """Hardware config includes parallelism from Config."""
        tool = self._make_tool(mock_env_vars)
        config = tool._get_hardware_config("L4")
        assert config["parallelism"] == 5  # default


# ------------------------------------------------------------------ #
# Config properties
# ------------------------------------------------------------------ #


class TestConfigProperties:
    """Tests for new Config properties (dws_max_wait_hours, parallelism)."""

    def test_dws_max_wait_hours_default(self, mock_env_vars):
        """Default DWS max wait is 168 hours (7 days)."""
        from foldrun_app.models.af2.config import Config

        config = Config()
        assert config.dws_max_wait_hours == 168

    def test_dws_max_wait_hours_from_env(self, mock_env_vars, monkeypatch):
        """DWS_MAX_WAIT_HOURS env var overrides default."""
        monkeypatch.setenv("DWS_MAX_WAIT_HOURS", "48")
        from foldrun_app.models.af2.config import Config

        config = Config()
        assert config.dws_max_wait_hours == 48

    def test_parallelism_default(self, mock_env_vars):
        """Default parallelism is 5."""
        from foldrun_app.models.af2.config import Config

        config = Config()
        assert config.parallelism == 5

    def test_parallelism_from_env(self, mock_env_vars, monkeypatch):
        """AF2_PARALLELISM env var overrides default."""
        monkeypatch.setenv("AF2_PARALLELISM", "10")
        from foldrun_app.models.af2.config import Config

        config = Config()
        assert config.parallelism == 10


# ------------------------------------------------------------------ #
# Hardware config with msa_method
# ------------------------------------------------------------------ #


class TestHardwareConfigMMseqs2:
    """Tests for _get_hardware_config with msa_method parameter."""

    def _make_tool(self, mock_env_vars):
        """Create a base AF2Tool for testing hardware config."""
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            from foldrun_app.models.af2.base import AF2Tool
            from foldrun_app.models.af2.config import Config

            tool_config = {"name": "test_tool", "description": "test"}
            config = Config()
            return AF2Tool(tool_config, config)

    def test_jackhmmer_no_gpu_data_pipeline(self, mock_env_vars):
        """Explicit jackhmmer has no GPU data pipeline config."""
        tool = self._make_tool(mock_env_vars)
        config = tool._get_hardware_config("L4", msa_method="jackhmmer")

        assert config["msa_method"] == "jackhmmer"
        assert "dp_machine" not in config
        assert "dp_accel" not in config

    def test_mmseqs2_includes_gpu_data_pipeline(self, mock_env_vars):
        """msa_method='mmseqs2' adds GPU data pipeline hardware."""
        tool = self._make_tool(mock_env_vars)
        config = tool._get_hardware_config("L4", msa_method="mmseqs2")

        assert config["msa_method"] == "mmseqs2"
        assert config["dp_machine"] == "g2-standard-12"
        assert config["dp_accel"] == "NVIDIA_L4"
        assert config["dp_accel_count"] == 1

    def test_mmseqs2_preserves_predict_config(self, mock_env_vars):
        """GPU data pipeline config doesn't affect predict/relax config."""
        tool = self._make_tool(mock_env_vars)
        config = tool._get_hardware_config("A100", msa_method="mmseqs2")

        # Predict should still use A100
        assert config["predict_accel"] == "NVIDIA_TESLA_A100"
        # Data pipeline GPU should be L4
        assert config["dp_accel"] == "NVIDIA_L4"


# ------------------------------------------------------------------ #
# Environment variable setup for mmseqs2
# ------------------------------------------------------------------ #


class TestCompileEnvMMseqs2:
    """Tests for _setup_compile_env with mmseqs2 hardware config."""

    def _make_tool(self, mock_env_vars):
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            from foldrun_app.models.af2.base import AF2Tool
            from foldrun_app.models.af2.config import Config

            tool_config = {"name": "test_tool", "description": "test"}
            config = Config()
            return AF2Tool(tool_config, config)

    def test_mmseqs2_env_vars_set(self, mock_env_vars):
        """MMseqs2 env vars are set when msa_method='mmseqs2'."""
        tool = self._make_tool(mock_env_vars)
        hw = tool._get_hardware_config("L4", msa_method="mmseqs2")

        tool._setup_compile_env(hw, "10.0.0.1", "projects/123/global/networks/default")

        assert os.environ.get("MMSEQS2_DATA_PIPELINE_MACHINE_TYPE") == "g2-standard-12"
        assert os.environ.get("MMSEQS2_ACCELERATOR_TYPE") == "NVIDIA_L4"
        assert os.environ.get("MMSEQS2_ACCELERATOR_COUNT") == "1"

    def test_jackhmmer_no_mmseqs2_env_vars(self, mock_env_vars):
        """MMseqs2 env vars are NOT set for jackhmmer."""
        tool = self._make_tool(mock_env_vars)
        hw = tool._get_hardware_config("L4", msa_method="jackhmmer")

        # Clear any previous values
        for key in [
            "MMSEQS2_DATA_PIPELINE_MACHINE_TYPE",
            "MMSEQS2_ACCELERATOR_TYPE",
            "MMSEQS2_ACCELERATOR_COUNT",
        ]:
            os.environ.pop(key, None)

        tool._setup_compile_env(hw, "10.0.0.1", "projects/123/global/networks/default")

        assert "MMSEQS2_DATA_PIPELINE_MACHINE_TYPE" not in os.environ


# ------------------------------------------------------------------ #
# Submission tool msa_method validation
# ------------------------------------------------------------------ #


class TestSubmitToolMMseqs2Validation:
    """Tests for msa_method validation in submission tools."""

    def _make_monomer_tool(self, mock_env_vars):
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            from foldrun_app.models.af2.config import Config
            from foldrun_app.models.af2.tools.submit_monomer import AF2SubmitMonomerTool

            tool_config = {"name": "af2_submit_monomer", "description": "test"}
            config = Config()
            return AF2SubmitMonomerTool(tool_config, config)

    def _make_multimer_tool(self, mock_env_vars):
        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            from foldrun_app.models.af2.config import Config
            from foldrun_app.models.af2.tools.submit_multimer import AF2SubmitMultimerTool

            tool_config = {"name": "af2_submit_multimer", "description": "test"}
            config = Config()
            return AF2SubmitMultimerTool(tool_config, config)

    def test_monomer_mmseqs2_without_small_bfd_returns_error(self, mock_env_vars):
        """Monomer: mmseqs2 + use_small_bfd=False returns validation error."""
        tool = self._make_monomer_tool(mock_env_vars)
        result = tool.run(
            {
                "sequence": ">test\nMKTIALSYIF",
                "msa_method": "mmseqs2",
                "use_small_bfd": False,
            }
        )

        assert result["status"] == "error"
        assert "use_small_bfd=True" in result["message"]
        assert "mmseqs2" in result["message"].lower() or "MMseqs2" in result["message"]

    def test_multimer_mmseqs2_without_small_bfd_returns_error(self, mock_env_vars):
        """Multimer: mmseqs2 + use_small_bfd=False returns validation error."""
        tool = self._make_multimer_tool(mock_env_vars)
        result = tool.run(
            {
                "sequence": ">A\nMKTIALSYIF\n>B\nACDEFGHIKL",
                "msa_method": "mmseqs2",
                "use_small_bfd": False,
            }
        )

        assert result["status"] == "error"
        assert "use_small_bfd=True" in result["message"]

    def _run_with_mocked_pipeline(self, tool, arguments):
        """Run tool.run() with all post-validation GCP calls mocked.

        Patches load_vertex_pipeline at the import site in each submit tool
        module so we don't need to import the pipeline components (which
        require sys.path manipulation for `import config`).
        """
        with (
            patch.object(tool, "_upload_to_gcs"),
            patch.object(
                tool,
                "_get_filestore_info",
                return_value=("10.0.0.1", "projects/123/global/networks/default"),
            ),
            patch("google.cloud.aiplatform.PipelineJob") as mock_pj,
            patch(
                "foldrun_app.models.af2.utils.pipeline_utils.load_vertex_pipeline",
                return_value=MagicMock(),
            ),
            patch("kfp.compiler.Compiler"),
            patch("os.remove"),
        ):
            mock_job = MagicMock()
            mock_job.resource_name = "projects/123/locations/us-central1/pipelineJobs/test-job"
            mock_pj.return_value = mock_job

            result = tool.run(arguments)
            return result, mock_pj

    def test_monomer_jackhmmer_allows_full_bfd(self, mock_env_vars):
        """Monomer: jackhmmer + use_small_bfd=False passes msa_method validation."""
        tool = self._make_monomer_tool(mock_env_vars)
        result, _ = self._run_with_mocked_pipeline(
            tool,
            {
                "sequence": ">test\nMKTIALSYIF",
                "msa_method": "jackhmmer",
                "use_small_bfd": False,
            },
        )

        assert result["status"] == "submitted"

    def test_monomer_auto_msa_resolves_jackhmmer_for_full_bfd(self, mock_env_vars):
        """Monomer: msa_method='auto' + use_small_bfd=False resolves to jackhmmer."""
        tool = self._make_monomer_tool(mock_env_vars)
        result, _ = self._run_with_mocked_pipeline(
            tool,
            {
                "sequence": ">test\nMKTIALSYIF",
                "use_small_bfd": False,
            },
        )

        assert result["status"] == "submitted"
        assert result["hardware"]["msa_method"] == "jackhmmer"

    def test_monomer_auto_msa_resolves_jackhmmer(self, mock_env_vars):
        """Monomer: msa_method='auto' always resolves to jackhmmer (default)."""
        tool = self._make_monomer_tool(mock_env_vars)
        result, _ = self._run_with_mocked_pipeline(
            tool,
            {
                "sequence": ">test\nMKTIALSYIF",
                "use_small_bfd": True,
            },
        )

        assert result["status"] == "submitted"
        assert result["hardware"]["msa_method"] == "jackhmmer"

    def test_monomer_mmseqs2_label_set(self, mock_env_vars):
        """Monomer: msa_method='mmseqs2' is included in pipeline labels."""
        tool = self._make_monomer_tool(mock_env_vars)
        _result, mock_pj = self._run_with_mocked_pipeline(
            tool,
            {
                "sequence": ">test\nMKTIALSYIF",
                "msa_method": "mmseqs2",
                "use_small_bfd": True,
            },
        )

        call_kwargs = mock_pj.call_args[1]
        assert call_kwargs["labels"]["msa_method"] == "mmseqs2"


# ------------------------------------------------------------------ #
# Pipeline loading with msa_method
# ------------------------------------------------------------------ #


class TestPipelineLoadingMMseqs2:
    """Tests for pipeline loading with msa_method parameter.

    The vertex_pipeline components use `import config` which requires
    the vertex_pipeline directory on sys.path. We add it here before
    importing the pipeline module.

    Note: pipelines/__init__.py imports `alphafold_inference_pipeline`
    (the KFP-decorated function), which shadows the module name. We
    access the actual module via sys.modules to patch correctly.
    """

    @staticmethod
    def _get_pipeline_module():
        """Import and return the pipeline module object.

        Adds vertex_pipeline to sys.path (required for `import config`)
        and returns the module via sys.modules to avoid the name-shadowing
        issue from pipelines/__init__.py.
        """
        import sys

        vp_dir = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "foldrun_app",
                "models",
                "af2",
                "pipeline",
            )
        )
        if vp_dir not in sys.path:
            sys.path.insert(0, vp_dir)

        # Trigger the import
        import foldrun_app.models.af2.pipeline.pipelines.alphafold_inference_pipeline  # noqa: F401

        # Get the actual module (not the shadowed GraphComponent)
        return sys.modules["foldrun_app.models.af2.pipeline.pipelines.alphafold_inference_pipeline"]

    def test_load_pipeline_passes_msa_method(self, mock_env_vars):
        """load_vertex_pipeline passes msa_method to pipeline factory."""
        import importlib

        pip_mod = self._get_pipeline_module()
        orig_reload = importlib.reload

        with (
            patch.object(pip_mod, "create_alphafold_inference_pipeline") as mock_create,
            patch(
                "foldrun_app.core.pipeline_utils.importlib.reload",
                side_effect=lambda mod: mod if mod is pip_mod else orig_reload(mod),
            ),
        ):
            mock_create.return_value = MagicMock()

            from foldrun_app.models.af2.utils.pipeline_utils import load_vertex_pipeline

            load_vertex_pipeline(msa_method="mmseqs2")

            mock_create.assert_called_once_with(strategy="STANDARD", msa_method="mmseqs2")

    def test_load_pipeline_default_jackhmmer(self, mock_env_vars):
        """load_vertex_pipeline defaults to jackhmmer."""
        import importlib

        pip_mod = self._get_pipeline_module()
        orig_reload = importlib.reload

        with (
            patch.object(pip_mod, "create_alphafold_inference_pipeline") as mock_create,
            patch(
                "foldrun_app.core.pipeline_utils.importlib.reload",
                side_effect=lambda mod: mod if mod is pip_mod else orig_reload(mod),
            ),
        ):
            mock_create.return_value = MagicMock()

            from foldrun_app.models.af2.utils.pipeline_utils import load_vertex_pipeline

            load_vertex_pipeline()

            mock_create.assert_called_once_with(strategy="STANDARD", msa_method="jackhmmer")

    def test_load_pipeline_flex_start_with_mmseqs2(self, mock_env_vars):
        """load_vertex_pipeline combines FLEX_START with mmseqs2."""
        import importlib

        pip_mod = self._get_pipeline_module()
        orig_reload = importlib.reload

        with (
            patch.object(pip_mod, "create_alphafold_inference_pipeline") as mock_create,
            patch(
                "foldrun_app.core.pipeline_utils.importlib.reload",
                side_effect=lambda mod: mod if mod is pip_mod else orig_reload(mod),
            ),
        ):
            mock_create.return_value = MagicMock()

            from foldrun_app.models.af2.utils.pipeline_utils import load_vertex_pipeline

            load_vertex_pipeline(enable_flex_start=True, msa_method="mmseqs2")

            mock_create.assert_called_once_with(strategy="FLEX_START", msa_method="mmseqs2")
