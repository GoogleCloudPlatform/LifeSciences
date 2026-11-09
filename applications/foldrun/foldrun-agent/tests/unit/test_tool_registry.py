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

"""Tests for the tool registry singleton and typed ModelDescriptor registry."""

from unittest.mock import patch

import pytest


class TestToolRegistry:
    """Tests for foldrun_app.skills._tool_registry and foldrun_app.core.model_registry."""

    @pytest.fixture(autouse=True)
    def _mock_gpu_detection(self, monkeypatch):
        """Avoid real GCE quota/accelerator network calls during unit tests."""
        monkeypatch.setattr(
            "foldrun_app.core.startup_utils.auto_detect_model_gpus",
            lambda config, **kwargs: None,
        )
        monkeypatch.setattr(
            "foldrun_app.models.af2.startup._auto_detect_gpus",
            lambda config, **kwargs: None,
        )
        monkeypatch.setattr(
            "foldrun_app.models.of3.startup._auto_detect_gpus",
            lambda config, **kwargs: None,
        )
        monkeypatch.setattr(
            "foldrun_app.models.boltz2.startup._auto_detect_gpus",
            lambda config, **kwargs: None,
        )
        monkeypatch.setattr(
            "foldrun_app.models.esmfold2.startup._auto_detect_gpus",
            lambda config, **kwargs: None,
        )
        monkeypatch.setattr(
            "foldrun_app.models.boltzgen.startup._auto_detect_gpus",
            lambda config, **kwargs: None,
        )
        monkeypatch.setattr(
            "foldrun_app.models.rfd4_proteina.startup._auto_detect_gpus",
            lambda config, **kwargs: None,
        )
        monkeypatch.setattr(
            "foldrun_app.models.bindcraft.startup._auto_detect_gpus",
            lambda config, **kwargs: None,
        )

    def _reset_registry(self):
        """Reset the registry global state between tests."""
        import foldrun_app.models.af2.startup as af2_startup
        import foldrun_app.models.af3.startup as af3_startup
        import foldrun_app.models.bindcraft.startup as bindcraft_startup
        import foldrun_app.models.boltz2.startup as boltz2_startup
        import foldrun_app.models.boltzgen.startup as boltzgen_startup
        import foldrun_app.models.esmfold2.startup as esmfold2_startup
        import foldrun_app.models.of3.startup as of3_startup
        import foldrun_app.models.rfd4_proteina.startup as rfd4_startup
        import foldrun_app.skills._tool_registry as reg
        from foldrun_app.core.startup_utils import reset_core_config

        reg._agents = {}
        reg._initialized = False
        reset_core_config()
        af2_startup._config = None
        of3_startup._config = None
        boltz2_startup._config = None
        esmfold2_startup._config = None
        boltzgen_startup._config = None
        rfd4_startup._config = None
        bindcraft_startup._config = None
        af3_startup._config = None

    def test_get_tool_initializes_on_first_call(self, mock_env_vars):
        """Lazy init works — first call triggers _initialize_all_tools."""
        self._reset_registry()

        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            import foldrun_app.skills._tool_registry as reg
            from foldrun_app.skills._tool_registry import get_tool

            assert reg._initialized is False

            tool = get_tool("af2_submit_monomer")
            assert reg._initialized is True
            assert tool is not None

    def test_get_tool_returns_same_instance(self, mock_env_vars):
        """Singleton behavior — same tool instance returned on repeated calls."""
        self._reset_registry()

        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            from foldrun_app.skills._tool_registry import get_tool

            tool1 = get_tool("af2_submit_monomer")
            tool2 = get_tool("af2_submit_monomer")
            assert tool1 is tool2

    def test_get_tool_unknown_raises_keyerror(self, mock_env_vars):
        """Unknown tool name raises KeyError."""
        self._reset_registry()

        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            from foldrun_app.skills._tool_registry import get_tool

            with pytest.raises(KeyError):
                get_tool("nonexistent_tool")

    def test_all_expected_tools_registered(self, mock_env_vars):
        """All 31 tool names are registered (11 platform + 8 AF2 + 4 OF3 + 4 Boltz-2 + 4 ESMFold2)."""
        self._reset_registry()

        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            import foldrun_app.skills._tool_registry as reg
            from foldrun_app.skills._tool_registry import get_tool

            get_tool("af2_submit_monomer")

            expected_tools = [
                # Cross-model platform tools (11 in core/tools/)
                "check_job_status",
                "list_jobs",
                "get_job_details",
                "delete_job",
                "check_gpu_quota",
                "cleanup_gcs_files",
                "find_orphaned_files",
                "analyze_job_deep",
                "alphafold_db_get_prediction",
                "alphafold_db_get_summary",
                "alphafold_db_get_annotations",
                # AF2 tools (8)
                "af2_submit_monomer",
                "af2_submit_multimer",
                "af2_submit_batch",
                "af2_get_results",
                "af2_analyze_quality",
                "af2_get_analysis_results",
                "af2_analyze_parallel",
                "af2_open_viewer",
                # OF3 tools (4)
                "of3_submit_prediction",
                "of3_analyze_parallel",
                "of3_get_analysis_results",
                "of3_open_viewer",
                # Boltz-2 tools (4)
                "boltz2_submit_prediction",
                "boltz2_analyze_parallel",
                "boltz2_get_analysis_results",
                "boltz2_open_viewer",
                # ESMFold2 tools (4)
                "esmfold2_submit_prediction",
                "esmfold2_analyze_parallel",
                "esmfold2_get_analysis_results",
                "esmfold2_open_viewer",
            ]

            assert len(reg._agents) == 31
            for tool_name in expected_tools:
                assert tool_name in reg._agents, f"Tool '{tool_name}' not registered"

    def test_platform_tools_initialize_without_af2_image(self, mock_env_vars, monkeypatch):
        """Platform tools backed by CoreConfig initialize even when ALPHAFOLD_COMPONENTS_IMAGE is unset."""
        self._reset_registry()
        monkeypatch.delenv("ALPHAFOLD_COMPONENTS_IMAGE", raising=False)
        monkeypatch.delenv("OPENFOLD3_COMPONENTS_IMAGE", raising=False)
        monkeypatch.delenv("BOLTZ2_COMPONENTS_IMAGE", raising=False)
        monkeypatch.delenv("ESMFOLD2_COMPONENTS_IMAGE", raising=False)

        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            import foldrun_app.skills._tool_registry as reg
            from foldrun_app.skills._tool_registry import get_tool

            list_tool = get_tool("list_jobs")
            assert list_tool is not None
            assert len(reg._agents) == 11
            assert "af2_submit_monomer" not in reg._agents

    def test_generative_binder_tools_registered(self, mock_env_vars, monkeypatch):
        """When BoltzGen, RFD4-Proteina, and BindCraft images are set, all 43 tools register."""
        self._reset_registry()
        monkeypatch.setenv("BOLTZGEN_COMPONENTS_IMAGE", "test-boltzgen-image:latest")
        monkeypatch.setenv("RFD4_PROTEINA_COMPONENTS_IMAGE", "test-rfd4-image:latest")
        monkeypatch.setenv("BINDCRAFT_COMPONENTS_IMAGE", "test-bindcraft-image:latest")

        with patch("google.cloud.aiplatform.init"), patch("google.cloud.storage.Client"):
            import foldrun_app.skills._tool_registry as reg
            from foldrun_app.skills._tool_registry import get_tool

            get_tool("af2_submit_monomer")
            assert len(reg._agents) == 43
            for tool_name in [
                "boltzgen_submit_design",
                "boltzgen_analyze_parallel",
                "boltzgen_get_analysis_results",
                "boltzgen_open_viewer",
                "rfd4_proteina_submit_design",
                "rfd4_proteina_analyze_job",
                "rfd4_proteina_get_analysis_results",
                "rfd4_proteina_open_viewer",
                "bindcraft_submit_design",
                "bindcraft_analyze_job",
                "bindcraft_get_analysis_results",
                "bindcraft_open_viewer",
            ]:
                assert tool_name in reg._agents, f"Tool '{tool_name}' not registered"

    def test_model_registry_registers_all_models(self, mock_env_vars, monkeypatch):
        """ModelDescriptor registry registers all 8 structure prediction and binder design models."""
        from foldrun_app.core.model_registry import (
            get_model,
            infer_model_type_from_job_id,
            list_models,
            normalize_model_type,
        )

        all_models = list_models()
        assert set(all_models) == {
            "alphafold2",
            "openfold3",
            "boltz2",
            "esmfold2",
            "alphafold3",
            "boltzgen",
            "rfd4_proteina",
            "bindcraft",
        }

        assert normalize_model_type("af2") == "alphafold2"
        assert normalize_model_type("of3") == "openfold3"
        assert normalize_model_type("boltz-2") == "boltz2"
        assert normalize_model_type("esmfold2_fast") == "esmfold2"
        assert normalize_model_type("esmfold2-fast") is None
        assert normalize_model_type("esmfold") is None
        assert normalize_model_type("esm2") is None
        assert normalize_model_type("af3") == "alphafold3"
        assert normalize_model_type("boltzgen") == "boltzgen"
        assert normalize_model_type("rfd4-proteina") == "rfd4_proteina"
        assert normalize_model_type("bindcraft") == "bindcraft"

        assert infer_model_type_from_job_id("alphafold2-inference-pipeline-123") == "alphafold2"
        assert infer_model_type_from_job_id("of3-inference-pipeline-123") == "openfold3"
        assert infer_model_type_from_job_id("boltz2-inference-pipeline-123") == "boltz2"
        assert infer_model_type_from_job_id("esmfold2-inference-pipeline-123") == "esmfold2"
        assert infer_model_type_from_job_id("alphafold3-inference-pipeline-123") == "alphafold3"
        assert infer_model_type_from_job_id("boltzgen-design-pipeline-123") == "boltzgen"
        assert infer_model_type_from_job_id("rfd4-proteina-pipeline-123") == "rfd4_proteina"
        assert infer_model_type_from_job_id("bindcraft-design-pipeline-123") == "bindcraft"

        af3_desc = get_model("af3")
        assert af3_desc.display_name == "AlphaFold 3"
        assert af3_desc.analyze_tool_key == "af3_get_results"
        assert af3_desc.results_tool_key == "af3_get_results"
        assert af3_desc.viewer_tool_key == "af3_open_viewer"

        monkeypatch.setenv("AF3_VERTEX_ENDPOINT", "endpoint-123")
        enabled = list_models(enabled_only=True)
        assert set(enabled) == {"alphafold2", "openfold3", "boltz2", "esmfold2", "alphafold3"}
