# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for src/alphafold-components/src/components/alphafold_utils.py (C6)."""

from __future__ import annotations

import importlib.util
import os
import pickle
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

COMPONENT_FILE = Path(__file__).resolve().parents[1] / "src" / "components" / "alphafold_utils.py"


class _LockedConfigDict(dict):
    """Mimics ml_collections.ConfigDict disallowing assignment to undeclared keys."""

    def __getattr__(self, key: str):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key: str, value) -> None:
        if key not in self:
            raise KeyError(f"Did not find key {key!r} in locked ConfigDict")
        self[key] = value


def _build_fake_alphafold_modules() -> dict[str, types.ModuleType]:
    """Create minimal realistic alphafold.* stub modules for unit testing."""
    af_pkg = types.ModuleType("alphafold")
    af_common = types.ModuleType("alphafold.common")
    af_protein = types.ModuleType("alphafold.common.protein")
    af_res = types.ModuleType("alphafold.common.residue_constants")
    af_data = types.ModuleType("alphafold.data")
    af_parsers = types.ModuleType("alphafold.data.parsers")
    af_pipeline = types.ModuleType("alphafold.data.pipeline")
    af_pipeline_multimer = types.ModuleType("alphafold.data.pipeline_multimer")
    af_templates = types.ModuleType("alphafold.data.templates")
    af_tools = types.ModuleType("alphafold.data.tools")
    af_hhblits = types.ModuleType("alphafold.data.tools.hhblits")
    af_hhsearch = types.ModuleType("alphafold.data.tools.hhsearch")
    af_hmmsearch = types.ModuleType("alphafold.data.tools.hmmsearch")
    af_jackhmmer = types.ModuleType("alphafold.data.tools.jackhmmer")
    af_model_pkg = types.ModuleType("alphafold.model")
    af_config = types.ModuleType("alphafold.model.config")
    af_model_data = types.ModuleType("alphafold.model.data")
    af_model = types.ModuleType("alphafold.model.model")
    af_relax_pkg = types.ModuleType("alphafold.relax")
    af_relax = types.ModuleType("alphafold.relax.relax")

    af_res.atom_type_num = 37
    af_protein.PDB_CHAIN_IDS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    af_protein.from_prediction = (
        lambda features, result, b_factors, remove_leading_feature_dimension: {
            "b_factors": b_factors,
            "remove_leading": remove_leading_feature_dimension,
        }
    )
    af_protein.to_pdb = lambda prot: (
        "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 85.00           C\nEND\n"
    )
    af_protein.from_pdb_string = lambda pdb_str: {"pdb": pdb_str}

    # Parsers
    def parse_fasta(fasta_string: str) -> tuple[list[str], list[str]]:
        sequences: list[str] = []
        descriptions: list[str] = []
        cur_seq: list[str] = []
        cur_desc: str | None = None
        for raw_line in fasta_string.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(">"):
                if cur_desc is not None:
                    sequences.append("".join(cur_seq))
                    descriptions.append(cur_desc)
                cur_desc = line[1:].strip()
                cur_seq = []
            else:
                cur_seq.append(line)
        if cur_desc is not None:
            sequences.append("".join(cur_seq))
            descriptions.append(cur_desc)
        return sequences, descriptions

    def parse_a3m(a3m_string: str):
        seqs, descs = parse_fasta(a3m_string)
        cleaned = ["".join(c for c in s if not c.islower()) for s in seqs]
        return types.SimpleNamespace(
            sequences=cleaned,
            deletion_matrix=[[0] * len(s) for s in cleaned],
            descriptions=descs,
            __len__=lambda self: len(self.sequences),
        )

    class _ParsedMsa:
        def __init__(self, sequences: list[str], descriptions: list[str]):
            self.sequences = sequences
            self.descriptions = descriptions
            self.deletion_matrix = [[0] * len(s) for s in sequences]

        def __len__(self) -> int:
            return len(self.sequences)

    def parse_stockholm(sto_string: str) -> _ParsedMsa:
        if not isinstance(sto_string, str):
            raise TypeError(f"parse_stockholm expected str, got {type(sto_string).__name__}")
        seqs: list[str] = []
        descs: list[str] = []
        for raw in sto_string.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line == "//":
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                descs.append(parts[0])
                seqs.append(parts[1])
        return _ParsedMsa(seqs, descs)

    af_parsers.parse_fasta = parse_fasta
    af_parsers.parse_a3m = lambda s: _ParsedMsa(
        ["".join(c for c in seq if not c.islower()) for seq in parse_fasta(s)[0]],
        parse_fasta(s)[1],
    )
    af_parsers.parse_stockholm = parse_stockholm
    af_parsers.parse_hhr = lambda hhr_str: [1] if "Hit1" in hhr_str else []
    af_parsers.deduplicate_stockholm_msa = lambda s: s
    af_parsers.remove_empty_columns_from_stockholm_msa = lambda s: s
    af_parsers.convert_stockholm_to_a3m = lambda s: ">query\nACDEFG\n"

    # Pipeline monomer feature builders
    def make_sequence_features(sequence: str, description: str, num_res: int) -> dict:
        return {
            "aatype": np.zeros((num_res, 21), dtype=np.float32),
            "between_segment_residues": np.zeros((num_res,), dtype=np.int32),
            "domain_name": np.array([description.encode("utf-8")], dtype=np.object_),
            "residue_index": np.arange(num_res, dtype=np.int32),
            "seq_length": np.full((num_res,), num_res, dtype=np.int32),
            "sequence": np.array([sequence.encode("utf-8")], dtype=np.object_),
        }

    def make_msa_features(msas: list[_ParsedMsa]) -> dict:
        all_seqs: list[str] = []
        for m in msas:
            all_seqs.extend(m.sequences)
        num_msa = len(all_seqs)
        num_res = len(all_seqs[0]) if all_seqs else 0
        return {
            "deletion_matrix_int": np.zeros((num_msa, num_res), dtype=np.int32),
            "deletion_matrix": np.zeros((num_msa, num_res), dtype=np.float32),
            "msa": np.zeros((num_msa, num_res), dtype=np.int32),
            "msa_mask": np.ones((num_msa, num_res), dtype=np.float32),
            "num_alignments": np.full((num_res,), num_msa, dtype=np.int32),
            "msa_species_identifiers": np.array([b""] * num_msa, dtype=np.object_),
        }

    af_pipeline.make_sequence_features = make_sequence_features
    af_pipeline.make_msa_features = make_msa_features
    af_pipeline.DataPipeline = mock.MagicMock()

    # Templates
    af_templates.TEMPLATE_FEATURES = {
        "template_aatype": np.float32,
        "template_all_atom_masks": np.float32,
        "template_all_atom_positions": np.float32,
        "template_domain_names": np.object_,
        "template_sequence": np.object_,
        "template_sum_probs": np.float32,
    }
    af_templates.HhsearchHitFeaturizer = mock.MagicMock()
    af_templates.HmmsearchHitFeaturizer = mock.MagicMock()

    # Multimer pipeline (matches upstream AF2 contract)
    af_pipeline_multimer.PDB_CHAIN_IDS = af_protein.PDB_CHAIN_IDS
    af_pipeline_multimer.msa_pairing = types.SimpleNamespace(
        MSA_FEATURES=("msa", "msa_mask", "deletion_matrix", "deletion_matrix_int")
    )

    class _MultimerDataPipeline:
        def _all_seq_msa_features(self, single_chain_msas):
            # Instance method only — calling unbound with (chain_fasta, msa) fails
            if not isinstance(self, _MultimerDataPipeline):
                raise TypeError("unbound _all_seq_msa_features call")
            return {}

    af_pipeline_multimer.DataPipeline = _MultimerDataPipeline

    def convert_monomer_features(monomer_features: dict, chain_id: str) -> dict:
        converted = dict(monomer_features)
        # Upstream AF2 runs np.argmax(template_aatype, axis=-1) which produces 2D (n_tmpl, L)
        if converted["template_aatype"].ndim != 3:
            raise ValueError(
                f"Expected 3D template_aatype (n_templates, num_res, 22), got shape {converted['template_aatype'].shape}"
            )
        converted["template_aatype"] = np.argmax(converted["template_aatype"], axis=-1).astype(
            np.int32
        )
        converted["auth_chain_id"] = np.asarray(chain_id, dtype=np.object_)
        return converted

    def add_assembly_features(all_chain_features: dict) -> dict:
        out = {}
        for idx, (chain_id, feats) in enumerate(all_chain_features.items(), start=1):
            copied = dict(feats)
            copied["asym_id"] = np.full((copied["aatype"].shape[0],), idx, dtype=np.int32)
            out[chain_id] = copied
        return out

    def pair_and_merge(all_chain_features: dict) -> dict:
        chains = list(all_chain_features.values())
        seqs = [bytes(c["sequence"][0]) for c in chains]
        if len(set(seqs)) > 1:
            for c in chains:
                for req_key in (
                    "msa_all_seq",
                    "msa_mask_all_seq",
                    "deletion_matrix_all_seq",
                    "deletion_matrix_int_all_seq",
                    "msa_species_identifiers_all_seq",
                ):
                    if req_key not in c:
                        raise KeyError(f"Missing required heteromer pairing feature {req_key!r}")
        total_res = sum(c["aatype"].shape[0] for c in chains)
        return {
            "aatype": np.concatenate([c["aatype"] for c in chains], axis=0),
            "asym_id": np.concatenate([c["asym_id"] for c in chains], axis=0),
            "msa": np.zeros((4, total_res), dtype=np.int32),
            "chain_ids": list(all_chain_features.keys()),
            "seq_length": np.array(total_res, dtype=np.int32),
        }

    def pad_msa(np_example: dict, min_num_seq: int) -> dict:
        padded = dict(np_example)
        cur_seq, total_res = padded["msa"].shape
        if cur_seq < min_num_seq:
            pad_rows = np.zeros((min_num_seq - cur_seq, total_res), dtype=np.int32)
            padded["msa"] = np.concatenate([padded["msa"], pad_rows], axis=0)
        padded["padded_to"] = min_num_seq
        return padded

    af_pipeline_multimer.convert_monomer_features = convert_monomer_features
    af_pipeline_multimer.add_assembly_features = add_assembly_features
    af_pipeline_multimer.feature_processing = types.SimpleNamespace(pair_and_merge=pair_and_merge)
    af_pipeline_multimer.pad_msa = pad_msa

    # Tools
    af_hhblits.HHBlits = mock.MagicMock()
    af_hhsearch.HHSearch = mock.MagicMock()
    af_hmmsearch.Hmmsearch = mock.MagicMock()
    af_jackhmmer.Jackhmmer = mock.MagicMock()

    # Model / Relax
    af_config.model_config = mock.MagicMock()
    af_model_data.get_model_haiku_params = mock.MagicMock(return_value={"params": 1})
    af_model.RunModel = mock.MagicMock()
    af_relax.AmberRelaxation = mock.MagicMock()

    af_pkg.common = af_common
    af_common.protein = af_protein
    af_common.residue_constants = af_res
    af_pkg.data = af_data
    af_data.parsers = af_parsers
    af_data.pipeline = af_pipeline
    af_data.pipeline_multimer = af_pipeline_multimer
    af_data.templates = af_templates
    af_data.tools = af_tools
    af_tools.hhblits = af_hhblits
    af_tools.hhsearch = af_hhsearch
    af_tools.hmmsearch = af_hmmsearch
    af_tools.jackhmmer = af_jackhmmer
    af_pkg.model = af_model_pkg
    af_model_pkg.config = af_config
    af_model_pkg.data = af_model_data
    af_model_pkg.model = af_model
    af_pkg.relax = af_relax_pkg
    af_relax_pkg.relax = af_relax

    return {
        "alphafold": af_pkg,
        "alphafold.common": af_common,
        "alphafold.common.protein": af_protein,
        "alphafold.common.residue_constants": af_res,
        "alphafold.data": af_data,
        "alphafold.data.parsers": af_parsers,
        "alphafold.data.pipeline": af_pipeline,
        "alphafold.data.pipeline_multimer": af_pipeline_multimer,
        "alphafold.data.templates": af_templates,
        "alphafold.data.tools": af_tools,
        "alphafold.data.tools.hhblits": af_hhblits,
        "alphafold.data.tools.hhsearch": af_hhsearch,
        "alphafold.data.tools.hmmsearch": af_hmmsearch,
        "alphafold.data.tools.jackhmmer": af_jackhmmer,
        "alphafold.model": af_model_pkg,
        "alphafold.model.config": af_config,
        "alphafold.model.data": af_model_data,
        "alphafold.model.model": af_model,
        "alphafold.relax": af_relax_pkg,
        "alphafold.relax.relax": af_relax,
    }


class TestAlphaFoldUtilsC6(unittest.TestCase):
    """Tests covering C6 fixes in src/alphafold-components/src/components/alphafold_utils.py."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._fake_modules = _build_fake_alphafold_modules()
        cls._modules_patcher = mock.patch.dict(sys.modules, cls._fake_modules)
        cls._modules_patcher.start()

        spec = importlib.util.spec_from_file_location(
            "foldrun_alphafold_utils_under_test", COMPONENT_FILE
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls.af_utils = mod

    @classmethod
    def tearDownClass(cls) -> None:
        cls._modules_patcher.stop()

    def test_empty_template_features_shapes_and_normalization(self) -> None:
        feats = self.af_utils._make_empty_template_features(num_res=12)
        self.assertEqual(feats["template_aatype"].shape, (0, 12, 22))
        self.assertEqual(feats["template_all_atom_masks"].shape, (0, 12, 37))
        self.assertEqual(feats["template_all_atom_positions"].shape, (0, 12, 37, 3))
        self.assertEqual(feats["template_domain_names"].shape, (0,))
        self.assertEqual(feats["template_sequence"].shape, (0,))
        self.assertEqual(feats["template_sum_probs"].shape, (0, 1))

        # 1D empty arrays returned by 0-hit featurizers must be normalized to 3D/4D
        bad_zero_hits = {
            "template_aatype": np.array([], dtype=np.float32),
            "template_all_atom_masks": np.array([], dtype=np.float32),
            "template_all_atom_positions": np.array([], dtype=np.float32),
            "template_domain_names": np.array([], dtype=np.object_),
            "template_sequence": np.array([], dtype=np.object_),
            "template_sum_probs": np.array([], dtype=np.float32),
        }
        normalized = self.af_utils._normalize_template_features(bad_zero_hits, num_res=9)
        self.assertEqual(normalized["template_aatype"].shape, (0, 9, 22))
        self.assertEqual(normalized["template_all_atom_positions"].shape, (0, 9, 37, 3))

    def test_single_sequence_fallback_monomer_and_multimer_homodimer_and_heterodimer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # 1. Monomer fallback
            mono_fasta = os.path.join(tmp, "mono.fasta")
            with open(mono_fasta, "w") as f:
                f.write(">target\nACDEFGHIKLMN\n")
            mono_msa_dir = os.path.join(tmp, "mono_msas")
            mono_pkl = os.path.join(tmp, "mono_features.pkl")
            mono_feats, mono_meta = self.af_utils._run_single_sequence_fallback(
                fasta_path=mono_fasta,
                run_multimer_system=False,
                msa_output_path=mono_msa_dir,
                features_output_path=mono_pkl,
            )
            self.assertEqual(mono_feats["template_aatype"].shape, (0, 12, 22))
            self.assertTrue(os.path.exists(mono_pkl))
            self.assertEqual(len(mono_meta), 1)

            # 2. Homodimer with duplicate FASTA headers (>subunit twice)
            homo_fasta = os.path.join(tmp, "homodimer.fasta")
            with open(homo_fasta, "w") as f:
                f.write(">subunit\nACDEFGHIKL\n>subunit\nACDEFGHIKL\n")
            homo_msa_dir = os.path.join(tmp, "homo_msas")
            homo_pkl = os.path.join(tmp, "homo_features.pkl")
            homo_feats, homo_meta = self.af_utils._run_single_sequence_fallback(
                fasta_path=homo_fasta,
                run_multimer_system=True,
                msa_output_path=homo_msa_dir,
                features_output_path=homo_pkl,
            )
            # Both chains A and B must be preserved (total length 20, not 10)
            self.assertEqual(homo_feats["chain_ids"], ["A", "B"])
            self.assertEqual(homo_feats["aatype"].shape[0], 20)
            self.assertEqual(homo_feats["msa"].shape, (512, 20))
            self.assertEqual(len(homo_meta), 2)

            # 3. Heterodimer fallback (requires *_all_seq pairing features)
            hetero_fasta = os.path.join(tmp, "heterodimer.fasta")
            with open(hetero_fasta, "w") as f:
                f.write(">chain_a\nACDEFGHIKL\n>chain_b\nMNPQRSTVWYAA\n")
            hetero_msa_dir = os.path.join(tmp, "hetero_msas")
            hetero_pkl = os.path.join(tmp, "hetero_features.pkl")
            hetero_feats, hetero_meta = self.af_utils._run_single_sequence_fallback(
                fasta_path=hetero_fasta,
                run_multimer_system=True,
                msa_output_path=hetero_msa_dir,
                features_output_path=hetero_pkl,
            )
            self.assertEqual(hetero_feats["chain_ids"], ["A", "B"])
            self.assertEqual(hetero_feats["aatype"].shape[0], 22)
            self.assertEqual(hetero_feats["msa"].shape, (512, 22))
            self.assertEqual(len(hetero_meta), 2)

    def test_mmseqs2_multimer_pipeline_preserves_duplicate_descriptions_and_pairs_heteromer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            uniref_db = os.path.join(tmp, "uniref90.fasta")
            seqres_db = os.path.join(tmp, "pdb_seqres.txt")
            for p in (uniref_db, seqres_db):
                Path(p).write_text(">dummy\nACDE\n")

            fasta_path = os.path.join(tmp, "complex.fasta")
            Path(fasta_path).write_text(">chain\nACDEFGHIKL\n>chain\nMNPQRSTVWYAA\n")
            msa_out = os.path.join(tmp, "mmseqs_msas")
            feat_pkl = os.path.join(tmp, "mmseqs_features.pkl")

            def fake_mmseqs_search(
                query_fasta, target_db, output_a3m, tmp_dir, gpu=True, max_seqs=10000, split=0
            ):
                content = Path(query_fasta).read_text()
                Path(output_a3m).write_text(content)
                return 1

            fake_hmm_instance = mock.MagicMock()
            fake_hmm_instance.query.return_value = "# STOCKHOLM 1.0\nhit1 ACDEFGHIKL\n//\n"
            fake_hmm_instance.get_template_hits.return_value = []

            fake_featurizer_instance = mock.MagicMock()
            # Simulate 0-hit 1D arrays from HmmsearchHitFeaturizer
            fake_featurizer_instance.get_templates.return_value = types.SimpleNamespace(
                features={
                    name: np.array([], dtype=dtype)
                    for name, dtype in self._fake_modules[
                        "alphafold.data.templates"
                    ].TEMPLATE_FEATURES.items()
                }
            )

            with (
                mock.patch.object(
                    self.af_utils, "run_mmseqs2_search", side_effect=fake_mmseqs_search
                ),
                mock.patch.object(self.af_utils, "_check_gpu_available", return_value=False),
                mock.patch.object(
                    self._fake_modules["alphafold.data.tools.hmmsearch"],
                    "Hmmsearch",
                    return_value=fake_hmm_instance,
                ),
                mock.patch.object(
                    self._fake_modules["alphafold.data.templates"],
                    "HmmsearchHitFeaturizer",
                    return_value=fake_featurizer_instance,
                ),
            ):
                feats, meta = self.af_utils.run_mmseqs2_data_pipeline(
                    fasta_path=fasta_path,
                    run_multimer_system=True,
                    uniref90_mmseqs_path=os.path.join(tmp, "uniref90_db"),
                    mgnify_mmseqs_path=os.path.join(tmp, "mgnify_db"),
                    small_bfd_mmseqs_path=os.path.join(tmp, "small_bfd_db"),
                    uniref90_database_path=uniref_db,
                    mgnify_database_path=uniref_db,
                    uniprot_database_path="",  # Exercise fallback to uniref90.sto
                    pdb70_database_path="",
                    obsolete_pdbs_path=os.path.join(tmp, "obsolete.dat"),
                    seqres_database_path=seqres_db,
                    mmcif_path=tmp,
                    max_template_date="2025-01-01",
                    msa_output_path=msa_out,
                    features_output_path=feat_pkl,
                )

            self.assertEqual(feats["chain_ids"], ["A", "B"])
            self.assertEqual(feats["aatype"].shape[0], 22)
            self.assertEqual(feats["msa"].shape, (512, 22))
            self.assertTrue(os.path.exists(feat_pkl))
            self.assertTrue(any(k.startswith("A_chain/") for k in meta))
            self.assertTrue(any(k.startswith("B_chain/") for k in meta))

    def test_run_hhsearch_supports_a3m_and_sto(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seq_path = os.path.join(tmp, "seq.fasta")
            Path(seq_path).write_text(">q\nACDEFG\n")
            a3m_path = os.path.join(tmp, "input.a3m")
            Path(a3m_path).write_text(">q\nACDEFG\n>h1\nACDefg\n")
            hits_out = os.path.join(tmp, "hits.hhr")
            feats_out = os.path.join(tmp, "tmpl.pkl")

            fake_hh = mock.MagicMock()
            fake_hh.query.return_value = "Hit1\n"
            fake_hh.get_template_hits.return_value = ["hit_obj"]

            expected_feats = self.af_utils._make_empty_template_features(6)
            fake_feat = mock.MagicMock()
            fake_feat.get_templates.return_value = types.SimpleNamespace(features=expected_feats)

            with (
                mock.patch.object(
                    self._fake_modules["alphafold.data.tools.hhsearch"],
                    "HHSearch",
                    return_value=fake_hh,
                ),
                mock.patch.object(
                    self._fake_modules["alphafold.data.templates"],
                    "HhsearchHitFeaturizer",
                    return_value=fake_feat,
                ),
            ):
                parsed_hhr, out_feats = self.af_utils.run_hhsearch(
                    sequence_path=seq_path,
                    msa_path=a3m_path,
                    msa_data_format="a3m",
                    template_hits_path=hits_out,
                    template_features_path=feats_out,
                    template_dbs_paths=["/db/pdb70"],
                    mmcif_path=tmp,
                    obsolete_path=os.path.join(tmp, "obsolete.dat"),
                    max_template_date="2025-01-01",
                    max_template_hits=20,
                    maxseq=1000,
                )

            fake_hh.query.assert_called_once_with(">q\nACDEFG\n>h1\nACDefg\n")
            self.assertEqual(len(parsed_hhr), 1)
            self.assertIn("template_aatype", out_feats)

            with self.assertRaises(ValueError):
                self.af_utils.run_hhsearch(
                    sequence_path=seq_path,
                    msa_path=a3m_path,
                    msa_data_format="fasta",
                    template_hits_path=hits_out,
                    template_features_path=feats_out,
                    template_dbs_paths=["/db/pdb70"],
                    mmcif_path=tmp,
                    obsolete_path=os.path.join(tmp, "obsolete.dat"),
                    max_template_date="2025-01-01",
                    max_template_hits=20,
                    maxseq=1000,
                )

    def test_run_hmmsearch_parses_sto_string_not_template_hits_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seq_path = os.path.join(tmp, "seq.fasta")
            Path(seq_path).write_text(">q\nACDEFG\n")
            sto_path = os.path.join(tmp, "msa.sto")
            Path(sto_path).write_text("# STOCKHOLM 1.0\nq ACDEFG\n//\n")
            hits_out = os.path.join(tmp, "hits.sto")
            feats_out = os.path.join(tmp, "tmpl.pkl")

            fake_hmm = mock.MagicMock()
            fake_hmm.query.return_value = "# STOCKHOLM 1.0\ntmpl1 ACDEFG\n//\n"
            # Return a list of non-strings so parse_stockholm(template_hits) would raise TypeError!
            fake_hmm.get_template_hits.return_value = [object(), object()]

            expected_feats = self.af_utils._make_empty_template_features(6)
            fake_feat = mock.MagicMock()
            fake_feat.get_templates.return_value = types.SimpleNamespace(features=expected_feats)

            with (
                mock.patch.object(
                    self._fake_modules["alphafold.data.tools.hmmsearch"],
                    "Hmmsearch",
                    return_value=fake_hmm,
                ),
                mock.patch.object(
                    self._fake_modules["alphafold.data.templates"],
                    "HmmsearchHitFeaturizer",
                    return_value=fake_feat,
                ),
            ):
                parsed_sto, out_feats = self.af_utils.run_hmmsearch(
                    sequence_path=seq_path,
                    msa_path=sto_path,
                    msa_data_format="sto",
                    template_hits_path=hits_out,
                    template_features_path=feats_out,
                    template_db_path="/db/pdb_seqres.txt",
                    mmcif_path=tmp,
                    obsolete_path=os.path.join(tmp, "obsolete.dat"),
                    max_template_date="2025-01-01",
                    max_template_hits=20,
                )

            self.assertEqual(len(parsed_sto), 1)
            self.assertEqual(parsed_sto.descriptions, ["tmpl1"])
            self.assertIn("template_aatype", out_feats)

    def test_predict_and_predict_relax_respect_locked_config_keys_and_int_seed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feat_path = os.path.join(tmp, "features.pkl")
            with open(feat_path, "wb") as f:
                pickle.dump({"aatype": np.zeros((5, 21), dtype=np.float32)}, f)

            # Locked monomer config where data.eval ONLY defines "num_ensemble" (not "num_ensemble_eval")
            locked_monomer_cfg = _LockedConfigDict(
                {
                    "data": _LockedConfigDict({"eval": _LockedConfigDict({"num_ensemble": 1})}),
                    "model": _LockedConfigDict({"num_ensemble_eval": 1}),
                }
            )
            fake_runner = mock.MagicMock()
            fake_runner.multimer_mode = False
            fake_runner.process_features.return_value = {"processed": True}
            fake_runner.predict.return_value = {
                "plddt": np.array([85.0, 90.0, 88.0, 91.0, 87.0], dtype=np.float32),
                "ranking_confidence": 88.2,
            }

            with (
                mock.patch.object(
                    self._fake_modules["alphafold.model.config"],
                    "model_config",
                    return_value=locked_monomer_cfg,
                ),
                mock.patch.object(
                    self._fake_modules["alphafold.model.model"],
                    "RunModel",
                    return_value=fake_runner,
                ),
            ):
                raw_pred_file = os.path.join(tmp, "raw_pred.pkl")
                unrelaxed_pdb_file = os.path.join(tmp, "unrelaxed.pdb")
                res = self.af_utils.predict(
                    model_features_path=feat_path,
                    model_params_path=tmp,
                    model_name="model_1",
                    num_ensemble=4,
                    run_multimer_system=False,
                    random_seed=np.int64(42),
                    raw_prediction_path=raw_pred_file,
                    unrelaxed_protein_path=unrelaxed_pdb_file,
                )
                self.assertEqual(locked_monomer_cfg.data.eval.num_ensemble, 4)
                _, kwargs = fake_runner.predict.call_args
                self.assertIs(type(kwargs["random_seed"]), int)
                self.assertEqual(kwargs["random_seed"], 42)
                self.assertIn("plddt", res)

                # Also test predict_relax with locked eval config having num_ensemble_eval
                locked_eval_cfg = _LockedConfigDict(
                    {
                        "data": _LockedConfigDict(
                            {"eval": _LockedConfigDict({"num_ensemble_eval": 1})}
                        ),
                        "model": _LockedConfigDict({"num_ensemble_eval": 1}),
                    }
                )
                self._fake_modules[
                    "alphafold.model.config"
                ].model_config.return_value = locked_eval_cfg

                raw_dir = os.path.join(tmp, "raw_dir")
                unrel_dir = os.path.join(tmp, "unrel_dir")
                rel_dir = os.path.join(tmp, "rel_dir")
                for d in (raw_dir, unrel_dir, rel_dir):
                    os.makedirs(d, exist_ok=True)

                confidences = self.af_utils.predict_relax(
                    model_features_path=feat_path,
                    model_params_path=tmp,
                    prediction_runners=[
                        {
                            "model_name": "model_1_ptm",
                            "prediction_index": 0,
                            "random_seed": "123",
                        }
                    ],
                    num_ensemble=8,
                    run_multimer_system=False,
                    raw_prediction_path=raw_dir,
                    unrelaxed_protein_path=unrel_dir,
                    relaxed_protein_path=rel_dir,
                    run_relax=False,
                )
                self.assertEqual(locked_eval_cfg.data.eval.num_ensemble_eval, 8)
                self.assertAlmostEqual(confidences["model_1_ptm_pred_0"], 88.2)

    def test_resolve_chain_msa_dir_prevents_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            safe = self.af_utils._resolve_chain_msa_dir(tmp, "../../etc/passwd", 0)
            self.assertTrue(
                os.path.commonpath([os.path.realpath(tmp), safe]) == os.path.realpath(tmp)
            )
            self.assertNotEqual(safe, os.path.realpath(tmp))

    def test_pdb70_prefix_check_and_core_mode_routing_in_data_pipelines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fasta_path = os.path.join(tmp, "mono.fasta")
            Path(fasta_path).write_text(">mono\nACDEFGHIKLMN\n")
            uniref90_path = os.path.join(tmp, "uniref90.fasta")
            Path(uniref90_path).write_text(">u1\nACDEFGHIKLMN\n")
            pdb70_prefix = os.path.join(tmp, "pdb70")

            # 1. Monomer with uniref90 present but pdb70_* prefix files absent -> single-sequence fallback
            msa_fallback = os.path.join(tmp, "msa_fallback")
            feat_fallback = os.path.join(tmp, "feat_fallback.pkl")
            with mock.patch.object(
                self.af_utils,
                "_run_single_sequence_fallback",
                wraps=self.af_utils._run_single_sequence_fallback,
            ) as spy_fallback:
                feats, meta = self.af_utils.run_data_pipeline(
                    fasta_path=fasta_path,
                    run_multimer_system=False,
                    uniref90_database_path=uniref90_path,
                    mgnify_database_path=uniref90_path,
                    bfd_database_path="",
                    small_bfd_database_path=uniref90_path,
                    uniref30_database_path="",
                    uniprot_database_path="",
                    pdb70_database_path=pdb70_prefix,
                    obsolete_pdbs_path=os.path.join(tmp, "obsolete.dat"),
                    seqres_database_path="",
                    mmcif_path=tmp,
                    max_template_date="2025-01-01",
                    msa_output_path=msa_fallback,
                    features_output_path=feat_fallback,
                    use_small_bfd=True,
                )
                spy_fallback.assert_called_once()
                self.assertEqual(feats["template_aatype"].shape, (0, 12, 22))
                self.assertEqual(len(meta), 1)

            # 2. Monomer with pdb70_a3m.ffdata present (prefix exists via glob, though pdb70 itself is not a file)
            Path(f"{pdb70_prefix}_a3m.ffdata").write_text("index")
            msa_full = os.path.join(tmp, "msa_full")
            os.makedirs(msa_full, exist_ok=True)
            Path(os.path.join(msa_full, "uniref90_hits.sto")).write_text(
                "# STOCKHOLM 1.0\nmono ACDEFGHIKLMN\n//\n"
            )
            feat_full = os.path.join(tmp, "feat_full.pkl")
            fake_dp_instance = mock.MagicMock()
            fake_dp_instance.process.return_value = {"aatype": np.zeros((12, 21))}
            with (
                mock.patch.object(
                    self.af_utils, "_run_single_sequence_fallback"
                ) as spy_no_fallback,
                mock.patch.object(
                    self._fake_modules["alphafold.data.pipeline"],
                    "DataPipeline",
                    return_value=fake_dp_instance,
                ),
            ):
                feats_full, meta_full = self.af_utils.run_data_pipeline(
                    fasta_path=fasta_path,
                    run_multimer_system=False,
                    uniref90_database_path=uniref90_path,
                    mgnify_database_path=uniref90_path,
                    bfd_database_path="",
                    small_bfd_database_path=uniref90_path,
                    uniref30_database_path="",
                    uniprot_database_path="",
                    pdb70_database_path=pdb70_prefix,
                    obsolete_pdbs_path=os.path.join(tmp, "obsolete.dat"),
                    seqres_database_path="",
                    mmcif_path=tmp,
                    max_template_date="2025-01-01",
                    msa_output_path=msa_full,
                    features_output_path=feat_full,
                    use_small_bfd=True,
                )
                spy_no_fallback.assert_not_called()
                fake_dp_instance.process.assert_called_once()
                self.assertIn("aatype", feats_full)
                self.assertEqual(meta_full["msa_full/uniref90_hits.sto"], 1)


if __name__ == "__main__":
    unittest.main()
