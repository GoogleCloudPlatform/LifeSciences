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

"""Security tests for FASTA header path traversal prevention in alphafold_utils."""

import importlib.util
import os
import sys
from unittest.mock import MagicMock, patch

import numpy as np  # noqa: F401
import pytest


def _load_alphafold_utils_module(rel_path: str, module_name: str):
    """Load an alphafold_utils.py module with mocked alphafold runtime dependencies."""
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..")
    )
    abs_path = os.path.join(repo_root, rel_path)

    mock_modules = {
        "alphafold": MagicMock(),
        "alphafold.common": MagicMock(),
        "alphafold.common.protein": MagicMock(),
        "alphafold.common.residue_constants": MagicMock(),
        "alphafold.data": MagicMock(),
        "alphafold.data.parsers": MagicMock(),
        "alphafold.data.pipeline": MagicMock(),
        "alphafold.data.pipeline_multimer": MagicMock(),
        "alphafold.data.templates": MagicMock(),
        "alphafold.data.tools": MagicMock(),
        "alphafold.data.tools.hhblits": MagicMock(),
        "alphafold.data.tools.hhsearch": MagicMock(),
        "alphafold.data.tools.hmmsearch": MagicMock(),
        "alphafold.data.tools.jackhmmer": MagicMock(),
        "alphafold.model": MagicMock(),
        "alphafold.model.config": MagicMock(),
        "alphafold.model.data": MagicMock(),
        "alphafold.model.model": MagicMock(),
        "alphafold.relax": MagicMock(),
        "alphafold.relax.relax": MagicMock(),
    }

    with patch.dict(sys.modules, mock_modules):
        spec = importlib.util.spec_from_file_location(module_name, abs_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


_COMPONENT_UTILS_PATH = "src/alphafold-components/src/components/alphafold_utils.py"
_AGENT_UTILS_PATH = "foldrun-agent/foldrun_app/models/af2/pipeline/components/alphafold_utils.py"


@pytest.mark.parametrize(
    "module_path,module_name",
    [
        (_COMPONENT_UTILS_PATH, "alphafold_utils_component"),
        (_AGENT_UTILS_PATH, "alphafold_utils_agent"),
    ],
)
class TestAlphaFoldUtilsPathTraversalSecurity:
    """Verify run_mmseqs2_data_pipeline and _resolve_chain_msa_dir prevent path traversal."""

    @pytest.mark.parametrize(
        "malicious_desc",
        [
            "../../escaped_dir",
            "../../../etc/cron.d/evil",
            "chainA/../../../../tmp/evil",
            "/tmp/absolute_escape_target",
            "/etc/passwd",
            "..\\..\\windows_escape",
            "..",
            ".",
            "",
            "   ",
            "///",
        ],
    )
    def test_resolve_chain_msa_dir_confines_malicious_descriptions(
        self, tmp_path, module_path, module_name, malicious_desc
    ):
        """All malicious/edge-case FASTA descriptions must resolve strictly inside msa_output_path."""
        mod = _load_alphafold_utils_module(module_path, module_name)
        msa_root = tmp_path / "msa_output"
        msa_root.mkdir()
        real_root = os.path.realpath(str(msa_root))

        resolved = mod._resolve_chain_msa_dir(str(msa_root), malicious_desc, chain_idx=0)
        assert os.path.commonpath([real_root, resolved]) == real_root
        assert resolved != real_root
        assert os.path.dirname(resolved) == real_root

    def test_resolve_chain_msa_dir_rejects_symlink_escape(self, tmp_path, module_path, module_name):
        """If a symlink inside msa_output_path points outside, canonical check must raise ValueError."""
        mod = _load_alphafold_utils_module(module_path, module_name)
        msa_root = tmp_path / "msa_output"
        outside_target = tmp_path / "outside_secret"
        msa_root.mkdir()
        outside_target.mkdir()

        symlink_path = msa_root / "chain_A"
        symlink_path.symlink_to(outside_target)

        with pytest.raises(ValueError, match="escapes msa_output_path"):
            mod._resolve_chain_msa_dir(str(msa_root), "chain_A", chain_idx=0)

    def test_resolve_chain_msa_dir_preserves_valid_fasta_headers(
        self, tmp_path, module_path, module_name
    ):
        """Valid FASTA headers (simple chain IDs and UniProt headers) resolve cleanly."""
        mod = _load_alphafold_utils_module(module_path, module_name)
        msa_root = tmp_path / "msa_output"
        msa_root.mkdir()
        real_root = os.path.realpath(str(msa_root))

        for idx, desc in enumerate(
            [
                "A",
                "chain_B",
                "sp|P04637|P53_HUMAN Cellular tumor antigen p53",
            ]
        ):
            resolved = mod._resolve_chain_msa_dir(str(msa_root), desc, chain_idx=idx)
            assert os.path.commonpath([real_root, resolved]) == real_root
            assert resolved != real_root

    def test_run_mmseqs2_data_pipeline_blocks_path_traversal_end_to_end(
        self, tmp_path, module_path, module_name
    ):
        """End-to-end test confirming run_mmseqs2_data_pipeline never creates dirs outside msa_output_path."""
        mod = _load_alphafold_utils_module(module_path, module_name)

        work_dir = tmp_path / "pipeline_run"
        work_dir.mkdir()
        msa_output_dir = work_dir / "msas"
        features_output_path = work_dir / "features.pkl"
        fasta_path = work_dir / "input.fasta"
        fasta_path.write_text(">../../escaped_traversal\nMKTIALSYIF\n>/tmp/evil_abs\nACDEFGHIKL\n")

        mod.parsers.parse_fasta.return_value = (
            ["MKTIALSYIF", "ACDEFGHIKL"],
            ["../../escaped_traversal", f"{tmp_path}/escaped_abs"],
        )
        mod.parsers.parse_stockholm.return_value = MagicMock()
        mod.pipeline_multimer.DataPipeline._all_seq_msa_features.return_value = {}
        mod.pipeline_multimer.DataPipeline._pair_and_merge.return_value = {"merged": True}
        mod.pipeline_multimer.DataPipeline._pad_features.return_value = {"padded": True}

        mock_jh_instance = MagicMock()
        mock_jh_instance.query.return_value = [{"sto": "# STOCKHOLM 1.0\n//\n"}]
        mod.jackhmmer.Jackhmmer.return_value = mock_jh_instance

        recorded_dirs = []

        def _fake_single_chain(**kwargs):
            recorded_dirs.append(kwargs["msa_output_dir"])
            return {"chain_feat": True}

        with (
            patch.object(mod, "_check_gpu_available", return_value=False),
            patch.object(mod, "_run_mmseqs2_single_chain", side_effect=_fake_single_chain),
        ):
            feature_dict, _ = mod.run_mmseqs2_data_pipeline(
                fasta_path=str(fasta_path),
                run_multimer_system=True,
                uniref90_mmseqs_path="/db/uniref90",
                mgnify_mmseqs_path="/db/mgnify",
                small_bfd_mmseqs_path="/db/small_bfd",
                uniref90_database_path="/db/uniref90.fasta",
                mgnify_database_path="/db/mgnify.fasta",
                uniprot_database_path="/db/uniprot.fasta",
                pdb70_database_path="/db/pdb70",
                obsolete_pdbs_path="/db/obsolete.dat",
                seqres_database_path="/db/pdb_seqres.txt",
                mmcif_path="/db/mmcif",
                max_template_date="2026-01-01",
                msa_output_path=str(msa_output_dir),
                features_output_path=str(features_output_path),
            )

        assert feature_dict == {"padded": True}
        assert len(recorded_dirs) == 2

        real_msa_root = os.path.realpath(str(msa_output_dir))
        for d in recorded_dirs:
            assert os.path.commonpath([real_msa_root, d]) == real_msa_root
            assert d != real_msa_root
            assert os.path.isdir(d)

        assert not (tmp_path / "escaped_traversal").exists()
        assert not (tmp_path / "escaped_abs").exists()
