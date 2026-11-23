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

COMPONENT_FILE = (
    Path(__file__).resolve().parents[5]
    / "src"
    / "alphafold-components"
    / "src"
    / "components"
    / "alphafold_utils.py"
)


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
            self.assertEqual(mono_meta.get("single_sequence_fallback"), 1)
            self.assertEqual(len(mono_meta), 2)

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
            self.assertEqual(homo_meta.get("single_sequence_fallback"), 1)
            self.assertEqual(len(homo_meta), 3)

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
            self.assertEqual(hetero_meta.get("single_sequence_fallback"), 1)
            self.assertEqual(len(hetero_meta), 3)

            # 4. Empty FASTA headers and duplicate sequence IDs in _write_a3m_as_sto
            empty_hdr_fasta = os.path.join(tmp, "empty_hdr.fasta")
            Path(empty_hdr_fasta).write_text(">\nACDEFGHIKL\n")
            empty_hdr_msas = os.path.join(tmp, "empty_hdr_msas")
            empty_hdr_pkl = os.path.join(tmp, "empty_hdr.pkl")
            empty_feats, _ = self.af_utils._run_single_sequence_fallback(
                fasta_path=empty_hdr_fasta,
                run_multimer_system=False,
                msa_output_path=empty_hdr_msas,
                features_output_path=empty_hdr_pkl,
            )
            self.assertEqual(empty_feats["aatype"].shape[0], 10)

            dup_sto = os.path.join(tmp, "dup.sto")
            self.af_utils._write_a3m_as_sto(">\nACDEFG\n>dup\nACDEFG\n>dup\nacMNPQ\n", dup_sto)
            parsed_dup = self._fake_modules["alphafold.data.parsers"].parse_stockholm(
                Path(dup_sto).read_text()
            )
            self.assertEqual(parsed_dup.descriptions, ["seq_0", "dup", "dup_1"])
            self.assertEqual(parsed_dup.sequences, ["ACDEFG", "ACDEFG", "MNPQ"])

            # 5. Monomer chain count validation in _run_single_sequence_fallback
            with self.assertRaises(ValueError):
                self.af_utils._run_single_sequence_fallback(
                    fasta_path=hetero_fasta,
                    run_multimer_system=False,
                    msa_output_path=os.path.join(tmp, "bad_mono_msas"),
                    features_output_path=os.path.join(tmp, "bad_mono.pkl"),
                )
            empty_file = os.path.join(tmp, "empty.fasta")
            Path(empty_file).write_text("")
            with self.assertRaises(ValueError):
                self.af_utils._run_single_sequence_fallback(
                    fasta_path=empty_file,
                    run_multimer_system=False,
                    msa_output_path=os.path.join(tmp, "empty_msas"),
                    features_output_path=os.path.join(tmp, "empty.pkl"),
                )

    def test_mmseqs2_multimer_pipeline_preserves_duplicate_descriptions_and_pairs_heteromer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            uniref_db = os.path.join(tmp, "uniref90.fasta")
            seqres_db = os.path.join(tmp, "pdb_seqres.txt")
            small_bfd_db = os.path.join(tmp, "bfd-first_non_consensus_sequences.fasta")
            uniprot_db = os.path.join(tmp, "uniprot.fasta")
            for p in (uniref_db, seqres_db, small_bfd_db, uniprot_db):
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

            fake_jh_instance = mock.MagicMock()
            fake_jh_instance.query.return_value = [
                {"sto": "# STOCKHOLM 1.0\nhit1 ACDEFGHIKL\n//\n"}
            ]

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
                    self._fake_modules["alphafold.data.tools.jackhmmer"],
                    "Jackhmmer",
                    return_value=fake_jh_instance,
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
                    uniprot_database_path=uniprot_db,
                    pdb70_database_path="",
                    obsolete_pdbs_path=os.path.join(tmp, "obsolete.dat"),
                    seqres_database_path=seqres_db,
                    mmcif_path=tmp,
                    max_template_date="2025-01-01",
                    msa_output_path=msa_out,
                    features_output_path=feat_pkl,
                    small_bfd_database_path=small_bfd_db,
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
                self.assertEqual(meta.get("single_sequence_fallback"), 1)
                self.assertEqual(len(meta), 2)

            # 2. Monomer with pdb70_a3m.ffdata present (prefix exists via glob, though pdb70 itself is not a file)
            Path(f"{pdb70_prefix}_a3m.ffdata").write_text("index")
            msa_full = os.path.join(tmp, "msa_full")
            os.makedirs(msa_full, exist_ok=True)
            feat_full = os.path.join(tmp, "feat_full.pkl")

            def _make_fake_jh(**kwargs):
                inst = mock.MagicMock()
                inst.query.return_value = [{"sto": "# STOCKHOLM 1.0\nmono ACDEFGHIKLMN\n//\n"}]
                return inst

            fake_hh = mock.MagicMock()
            fake_hh.query.return_value = "Hit1\n"
            fake_hh.get_template_hits.return_value = ["hit1"]

            fake_feat = mock.MagicMock()
            fake_feat.get_templates.return_value = types.SimpleNamespace(
                features=self.af_utils._make_empty_template_features(12)
            )

            with (
                mock.patch.object(
                    self.af_utils, "_run_single_sequence_fallback"
                ) as spy_no_fallback,
                mock.patch.object(
                    self._fake_modules["alphafold.data.tools.jackhmmer"],
                    "Jackhmmer",
                    side_effect=_make_fake_jh,
                ),
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
                self.assertIn("aatype", feats_full)
                self.assertEqual(meta_full["msa_full/uniref90_hits.sto"], 1)
                self.assertEqual(meta_full["msa_full/mgnify_hits.sto"], 1)
                self.assertEqual(meta_full["msa_full/small_bfd_hits.sto"], 1)
                self.assertEqual(meta_full["msa_full/pdb_hits.hhr"], 1)
                self.assertNotIn("single_sequence_fallback", meta_full)

            # 3. Mixed per-model modes on shared NFS: uniref90, mgnify, seqres, uniprot, mmcif
            # exist (e.g. from OF3 full mode), but small_bfd is missing -> must fall back!
            self.assertFalse(
                self.af_utils._has_af2_raw_databases(
                    run_multimer_system=True,
                    use_small_bfd=True,
                    uniref90_database_path=uniref90_path,
                    mgnify_database_path=uniref90_path,
                    bfd_database_path="",
                    small_bfd_database_path=os.path.join(tmp, "missing_small_bfd.fasta"),
                    uniref30_database_path="",
                    uniprot_database_path=uniref90_path,
                    pdb70_database_path=pdb70_prefix,
                    seqres_database_path=uniref90_path,
                    mmcif_path=tmp,
                )
            )
            # 4. Partial Cloud Batch downloads: uniref90 + seqres + small_bfd exist, but uniprot is missing
            self.assertFalse(
                self.af_utils._has_af2_raw_databases(
                    run_multimer_system=True,
                    use_small_bfd=True,
                    uniref90_database_path=uniref90_path,
                    mgnify_database_path=uniref90_path,
                    bfd_database_path="",
                    small_bfd_database_path=uniref90_path,
                    uniref30_database_path="",
                    uniprot_database_path=os.path.join(tmp, "missing_uniprot.fasta"),
                    pdb70_database_path=pdb70_prefix,
                    seqres_database_path=uniref90_path,
                    mmcif_path=tmp,
                )
            )
            # 5. Full BFD mode (use_small_bfd=False) requires bfd_* and uniref30_* prefix files
            bfd_prefix = os.path.join(tmp, "bfd_prefix")
            uniref30_prefix = os.path.join(tmp, "uniref30_prefix")
            self.assertFalse(
                self.af_utils._has_af2_raw_databases(
                    run_multimer_system=False,
                    use_small_bfd=False,
                    uniref90_database_path=uniref90_path,
                    mgnify_database_path=uniref90_path,
                    bfd_database_path=bfd_prefix,
                    small_bfd_database_path="",
                    uniref30_database_path=uniref30_prefix,
                    uniprot_database_path="",
                    pdb70_database_path=pdb70_prefix,
                    seqres_database_path="",
                    mmcif_path=tmp,
                )
            )
            Path(f"{bfd_prefix}_a3m.ffdata").write_text("bfd")
            Path(f"{uniref30_prefix}_a3m.ffdata").write_text("u30")
            self.assertTrue(
                self.af_utils._has_af2_raw_databases(
                    run_multimer_system=False,
                    use_small_bfd=False,
                    uniref90_database_path=uniref90_path,
                    mgnify_database_path=uniref90_path,
                    bfd_database_path=bfd_prefix,
                    small_bfd_database_path="",
                    uniref30_database_path=uniref30_prefix,
                    uniprot_database_path="",
                    pdb70_database_path=pdb70_prefix,
                    seqres_database_path="",
                    mmcif_path=tmp,
                )
            )


class TestParallelDataPipelineAndSharedCache(unittest.TestCase):
    """Tests for parallel MSA/template execution, shared_msas_cache, and predict_in_subprocess."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._fake_modules = _build_fake_alphafold_modules()
        cls._modules_patcher = mock.patch.dict(sys.modules, cls._fake_modules)
        cls._modules_patcher.start()

        spec = importlib.util.spec_from_file_location(
            "foldrun_alphafold_utils_parallel_test", COMPONENT_FILE
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls.af_utils = mod

    @classmethod
    def tearDownClass(cls) -> None:
        cls._modules_patcher.stop()

    def test_resolve_search_cpus_weighting_and_bounds(self) -> None:
        self.assertEqual(self.af_utils._resolve_search_cpus([], 8), [])
        self.assertEqual(self.af_utils._resolve_search_cpus([("only", 100, 1.0)], 8), [8])
        self.assertEqual(
            self.af_utils._resolve_search_cpus([("a", 0, 2.0), ("b", 0, 1.0), ("c", 0, 0.3)], 8),
            [5, 3, 3],
        )
        # Real multi-GiB DB sizes split proportionally
        self.assertEqual(
            self.af_utils._resolve_search_cpus(
                [("mgnify", 120_000_000_000, 2.0), ("uniref90", 60_000_000_000, 1.0)],
                8,
            ),
            [5, 3],
        )
        self.assertEqual(
            self.af_utils._resolve_search_cpus([("a", 0, 2.0), ("b", 0, 1.0)], 1),
            [1, 1],
        )

    def test_truncate_stockholm_msa_handles_interleaved_blocks_and_gs_annotations(self) -> None:
        sto = (
            "# STOCKHOLM 1.0\n"
            "#=GF ID test\n"
            "#=GS q DE query\n"
            "#=GS h1 DE hit1\n"
            "#=GS h2 DE hit2\n"
            "q   ACDE\n"
            "h1  ACDE\n"
            "h2  ACDF\n"
            "#=GC RF xxxx\n"
            "\n"
            "q   FGHI\n"
            "h1  FGHI\n"
            "h2  FGHK\n"
            "#=GC RF xxxx\n"
            "//\n"
        )
        # Fast path when already within limit
        self.assertIs(self.af_utils._truncate_stockholm_msa(sto, 3), sto)

        # Truncate to 2 sequences -> keeps q & h1 across both blocks and drops h2 + its #=GS
        trunc = self.af_utils._truncate_stockholm_msa(sto, 2)
        self.assertIn("# STOCKHOLM 1.0", trunc)
        self.assertIn("#=GS q DE query", trunc)
        self.assertIn("#=GS h1 DE hit1", trunc)
        self.assertNotIn("#=GS h2", trunc)
        self.assertNotIn("h2 ", trunc)
        self.assertEqual(trunc.count("#=GC RF xxxx"), 2)
        self.assertEqual(trunc.count("h1  "), 2)
        self.assertTrue(trunc.endswith("//\n"))

    def _setup_fake_dbs(self, tmp: str) -> dict[str, str]:
        paths = {
            "uniref90": os.path.join(tmp, "uniref90.fasta"),
            "mgnify": os.path.join(tmp, "mgy_clusters.fa"),
            "small_bfd": os.path.join(tmp, "small_bfd.fasta"),
            "uniprot": os.path.join(tmp, "uniprot.fasta"),
            "seqres": os.path.join(tmp, "pdb_seqres.txt"),
            "bfd": os.path.join(tmp, "bfd_prefix"),
            "uniref30": os.path.join(tmp, "uniref30_prefix"),
            "pdb70": os.path.join(tmp, "pdb70"),
            "obsolete": os.path.join(tmp, "obsolete.dat"),
        }
        for key in ("uniref90", "mgnify", "small_bfd", "uniprot", "seqres", "obsolete"):
            Path(paths[key]).write_text(">dummy\nACDEFGHIKL\n")
        for prefix_key in ("bfd", "uniref30", "pdb70"):
            Path(f"{paths[prefix_key]}_a3m.ffdata").write_text("idx")
        return paths

    def test_parallel_monomer_searches_concurrently_and_promotes_untruncated_shared_cache(
        self,
    ) -> None:
        import hashlib
        import threading

        with tempfile.TemporaryDirectory() as tmp:
            dbs = self._setup_fake_dbs(tmp)
            seq = "ACDEFGHIKL"
            seq_hash = hashlib.sha256(seq.encode()).hexdigest()
            fasta_path = os.path.join(tmp, "target.fasta")
            Path(fasta_path).write_text(f">my_protein\n{seq}\n")
            msa_out = os.path.join(tmp, "msas_cold")
            feat_out = os.path.join(tmp, "feat_cold.pkl")
            shared_cache = os.path.join(tmp, "shared_msas_cache")
            shared_tmp = os.path.join(tmp, "shared_msas_tmp")

            # Build >501 MGnify hits so we prove untruncated promotion vs AF2 in-memory truncation
            mgnify_lines = ["# STOCKHOLM 1.0", f"protein_{seq_hash} {seq}"]
            for i in range(600):
                mgnify_lines.append(f"mgy_{i} {seq}")
            mgnify_lines.append("//")
            big_mgnify_sto = "\n".join(mgnify_lines) + "\n"
            uniref_sto = f"# STOCKHOLM 1.0\nprotein_{seq_hash} {seq}\nu1 {seq}\n//\n"
            small_bfd_sto = f"# STOCKHOLM 1.0\nprotein_{seq_hash} {seq}\nb1 {seq}\n//\n"

            barrier = threading.Barrier(2, timeout=3.0)
            called_dbs: list[tuple[str, int]] = []
            lock = threading.Lock()

            def _make_jh(binary_path=None, database_path="", n_cpu=8):
                db_base = os.path.basename(database_path)

                def _query(fasta_in):
                    # Verify deterministic query header for shared_msas_cache parity
                    self.assertTrue(Path(fasta_in).read_text().startswith(f">protein_{seq_hash}\n"))
                    with lock:
                        called_dbs.append((db_base, n_cpu))
                    if db_base in ("mgy_clusters.fa", "uniref90.fasta"):
                        barrier.wait()
                    if db_base == "mgy_clusters.fa":
                        return [{"sto": big_mgnify_sto}]
                    if db_base == "uniref90.fasta":
                        return [{"sto": uniref_sto}]
                    return [{"sto": small_bfd_sto}]

                inst = mock.MagicMock()
                inst.query.side_effect = _query
                return inst

            fake_hh = mock.MagicMock()
            fake_hh.query.return_value = "Hit1\n"
            fake_hh.get_template_hits.return_value = ["h1"]
            fake_feat = mock.MagicMock()
            fake_feat.get_templates.return_value = types.SimpleNamespace(
                features=self.af_utils._make_empty_template_features(len(seq))
            )

            with (
                mock.patch.object(os, "cpu_count", return_value=8),
                mock.patch.object(
                    self._fake_modules["alphafold.data.tools.jackhmmer"],
                    "Jackhmmer",
                    side_effect=_make_jh,
                ),
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
                feats, meta = self.af_utils.run_data_pipeline(
                    fasta_path=fasta_path,
                    run_multimer_system=False,
                    uniref90_database_path=dbs["uniref90"],
                    mgnify_database_path=dbs["mgnify"],
                    bfd_database_path=dbs["bfd"],
                    small_bfd_database_path=dbs["small_bfd"],
                    uniref30_database_path=dbs["uniref30"],
                    uniprot_database_path=dbs["uniprot"],
                    pdb70_database_path=dbs["pdb70"],
                    obsolete_pdbs_path=dbs["obsolete"],
                    seqres_database_path=dbs["seqres"],
                    mmcif_path=tmp,
                    max_template_date="2025-01-01",
                    msa_output_path=msa_out,
                    features_output_path=feat_out,
                    use_small_bfd=True,
                    shared_msa_cache_dir=shared_cache,
                    shared_msa_tmp_dir=shared_tmp,
                )

            cpu_map = dict(called_dbs)
            self.assertEqual(cpu_map["mgy_clusters.fa"], 5)
            self.assertEqual(cpu_map["uniref90.fasta"], 3)
            self.assertEqual(cpu_map["small_bfd.fasta"], 3)

            # Shared cache has UNTRUNCATED mgnify_hits.sto (601 seqs), no query.fasta or tmp leftovers
            cached_dir = Path(shared_cache) / f"protein_{seq_hash}"
            self.assertTrue((cached_dir / "uniref90_hits.sto").is_file())
            self.assertTrue((cached_dir / "mgnify_hits.sto").is_file())
            self.assertFalse((cached_dir / "query.fasta").exists())
            self.assertEqual(list(Path(shared_tmp).iterdir()), [])
            cached_mgnify = self._fake_modules["alphafold.data.parsers"].parse_stockholm(
                (cached_dir / "mgnify_hits.sto").read_text()
            )
            self.assertEqual(len(cached_mgnify), 601)

            # AF2 in-memory / chain output truncated MGnify to MGNIFY_MAX_HITS (501)
            self.assertEqual(meta["msas_cold/mgnify_hits.sto"], 501)
            self.assertEqual(meta["msas_cold/uniref90_hits.sto"], 2)
            self.assertEqual(meta["msas_cold/small_bfd_hits.sto"], 2)
            self.assertEqual(meta["msas_cold/pdb_hits.hhr"], 1)
            self.assertIn("aatype", feats)

            # Warm hit: second run with different FASTA header skips uniref90 + mgnify completely!
            called_dbs.clear()
            fasta_warm = os.path.join(tmp, "renamed.fasta")
            Path(fasta_warm).write_text(f">different_name\n{seq.lower()}\n")
            msa_warm = os.path.join(tmp, "msas_warm")
            feat_warm = os.path.join(tmp, "feat_warm.pkl")
            with (
                mock.patch.object(os, "cpu_count", return_value=8),
                mock.patch.object(
                    self._fake_modules["alphafold.data.tools.jackhmmer"],
                    "Jackhmmer",
                    side_effect=_make_jh,
                ),
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
                _, meta_warm = self.af_utils.run_data_pipeline(
                    fasta_path=fasta_warm,
                    run_multimer_system=False,
                    uniref90_database_path=dbs["uniref90"],
                    mgnify_database_path=dbs["mgnify"],
                    bfd_database_path=dbs["bfd"],
                    small_bfd_database_path=dbs["small_bfd"],
                    uniref30_database_path=dbs["uniref30"],
                    uniprot_database_path=dbs["uniprot"],
                    pdb70_database_path=dbs["pdb70"],
                    obsolete_pdbs_path=dbs["obsolete"],
                    seqres_database_path=dbs["seqres"],
                    mmcif_path=tmp,
                    max_template_date="2025-01-01",
                    msa_output_path=msa_warm,
                    features_output_path=feat_warm,
                    use_small_bfd=True,
                    shared_msa_cache_dir=shared_cache,
                    shared_msa_tmp_dir=shared_tmp,
                )

            # Only small_bfd ran (with all 8 vCPUs!)
            self.assertEqual(called_dbs, [("small_bfd.fasta", 8)])
            self.assertEqual(meta_warm["msas_warm/mgnify_hits.sto"], 501)
            self.assertEqual(meta_warm["msas_warm/uniref90_hits.sto"], 2)

    def test_multimer_homodimer_deduplicates_chains_and_heterodimer_runs_uniprot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dbs = self._setup_fake_dbs(tmp)
            called_dbs: list[str] = []

            def _make_jh(binary_path=None, database_path="", n_cpu=8):
                db_base = os.path.basename(database_path)

                def _query(fasta_in):
                    called_dbs.append(db_base)
                    lines = Path(fasta_in).read_text().splitlines()
                    hdr = lines[0][1:]
                    seq = lines[1]
                    return [{"sto": f"# STOCKHOLM 1.0\n{hdr} {seq}\n//\n"}]

                inst = mock.MagicMock()
                inst.query.side_effect = _query
                return inst

            fake_hmm = mock.MagicMock()
            fake_hmm.query.return_value = "# STOCKHOLM 1.0\ntpl ACDEFGHIKL\n//\n"
            fake_hmm.get_template_hits.return_value = []
            fake_feat = mock.MagicMock()
            fake_feat.get_templates.side_effect = lambda query_sequence, hits: (
                types.SimpleNamespace(
                    features=self.af_utils._make_empty_template_features(len(query_sequence))
                )
            )

            # 1. Homodimer -> searches unique chain ONCE and skips uniprot
            homo_fasta = os.path.join(tmp, "homo.fasta")
            Path(homo_fasta).write_text(">sub1\nACDEFGHIKL\n>sub2\nACDEFGHIKL\n")
            with (
                mock.patch.object(
                    self._fake_modules["alphafold.data.tools.jackhmmer"],
                    "Jackhmmer",
                    side_effect=_make_jh,
                ),
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
                homo_feats, homo_meta = self.af_utils.run_data_pipeline(
                    fasta_path=homo_fasta,
                    run_multimer_system=True,
                    uniref90_database_path=dbs["uniref90"],
                    mgnify_database_path=dbs["mgnify"],
                    bfd_database_path=dbs["bfd"],
                    small_bfd_database_path=dbs["small_bfd"],
                    uniref30_database_path=dbs["uniref30"],
                    uniprot_database_path=dbs["uniprot"],
                    pdb70_database_path=dbs["pdb70"],
                    obsolete_pdbs_path=dbs["obsolete"],
                    seqres_database_path=dbs["seqres"],
                    mmcif_path=tmp,
                    max_template_date="2025-01-01",
                    msa_output_path=os.path.join(tmp, "homo_msas"),
                    features_output_path=os.path.join(tmp, "homo.pkl"),
                    use_small_bfd=True,
                )

            self.assertEqual(homo_feats["chain_ids"], ["A", "B"])
            self.assertEqual(homo_feats["aatype"].shape[0], 20)
            self.assertNotIn("uniprot.fasta", called_dbs)
            # Only 3 DB calls (1 unique chain), not 6!
            self.assertEqual(len(called_dbs), 3)
            self.assertIn("A/uniref90_hits.sto", homo_meta)

            # 2. Heterodimer -> runs uniprot + pairs both chains
            called_dbs.clear()
            hetero_fasta = os.path.join(tmp, "hetero.fasta")
            Path(hetero_fasta).write_text(">cA\nACDEFGHIKL\n>cB\nMNPQRSTVWYAA\n")
            with (
                mock.patch.object(
                    self._fake_modules["alphafold.data.tools.jackhmmer"],
                    "Jackhmmer",
                    side_effect=_make_jh,
                ),
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
                hetero_feats, hetero_meta = self.af_utils.run_data_pipeline(
                    fasta_path=hetero_fasta,
                    run_multimer_system=True,
                    uniref90_database_path=dbs["uniref90"],
                    mgnify_database_path=dbs["mgnify"],
                    bfd_database_path=dbs["bfd"],
                    small_bfd_database_path=dbs["small_bfd"],
                    uniref30_database_path=dbs["uniref30"],
                    uniprot_database_path=dbs["uniprot"],
                    pdb70_database_path=dbs["pdb70"],
                    obsolete_pdbs_path=dbs["obsolete"],
                    seqres_database_path=dbs["seqres"],
                    mmcif_path=tmp,
                    max_template_date="2025-01-01",
                    msa_output_path=os.path.join(tmp, "hetero_msas"),
                    features_output_path=os.path.join(tmp, "hetero.pkl"),
                    use_small_bfd=True,
                )

            self.assertEqual(hetero_feats["chain_ids"], ["A", "B"])
            self.assertEqual(hetero_feats["aatype"].shape[0], 22)
            self.assertEqual(called_dbs.count("uniprot.fasta"), 2)
            self.assertIn("A/uniprot_hits.sto", hetero_meta)
            self.assertIn("B/uniprot_hits.sto", hetero_meta)

    def test_full_bfd_mode_runs_hhblits_and_core_mode_never_pollutes_shared_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dbs = self._setup_fake_dbs(tmp)
            fasta_path = os.path.join(tmp, "mono.fasta")
            Path(fasta_path).write_text(">mono\nACDEFGHIKL\n")

            # 1. Core mode (missing pdb70) never touches shared_msas_cache
            shared_cache = os.path.join(tmp, "shared_core")
            self.af_utils.run_data_pipeline(
                fasta_path=fasta_path,
                run_multimer_system=False,
                uniref90_database_path=dbs["uniref90"],
                mgnify_database_path=dbs["mgnify"],
                bfd_database_path=dbs["bfd"],
                small_bfd_database_path=dbs["small_bfd"],
                uniref30_database_path=dbs["uniref30"],
                uniprot_database_path=dbs["uniprot"],
                pdb70_database_path=os.path.join(tmp, "missing_pdb70"),
                obsolete_pdbs_path=dbs["obsolete"],
                seqres_database_path=dbs["seqres"],
                mmcif_path=tmp,
                max_template_date="2025-01-01",
                msa_output_path=os.path.join(tmp, "core_msas"),
                features_output_path=os.path.join(tmp, "core.pkl"),
                use_small_bfd=True,
                shared_msa_cache_dir=shared_cache,
            )
            self.assertFalse(os.path.exists(shared_cache))

            # 2. Full BFD mode invokes HHBlits on [bfd, uniref30]
            def _make_jh(**kwargs):
                inst = mock.MagicMock()
                inst.query.return_value = [{"sto": "# STOCKHOLM 1.0\nq ACDEFGHIKL\n//\n"}]
                return inst

            fake_hhblits = mock.MagicMock()
            fake_hhblits.query.return_value = [{"a3m": ">q\nACDEFGHIKL\n>h1\nACDEFGHIKL\n"}]
            fake_hhsearch = mock.MagicMock()
            fake_hhsearch.query.return_value = "Hit1\n"
            fake_hhsearch.get_template_hits.return_value = []
            fake_feat = mock.MagicMock()
            fake_feat.get_templates.return_value = types.SimpleNamespace(
                features=self.af_utils._make_empty_template_features(10)
            )

            with (
                mock.patch.object(
                    self._fake_modules["alphafold.data.tools.jackhmmer"],
                    "Jackhmmer",
                    side_effect=_make_jh,
                ),
                mock.patch.object(
                    self._fake_modules["alphafold.data.tools.hhblits"],
                    "HHBlits",
                    return_value=fake_hhblits,
                ) as spy_hhblits,
                mock.patch.object(
                    self._fake_modules["alphafold.data.tools.hhsearch"],
                    "HHSearch",
                    return_value=fake_hhsearch,
                ),
                mock.patch.object(
                    self._fake_modules["alphafold.data.templates"],
                    "HhsearchHitFeaturizer",
                    return_value=fake_feat,
                ),
            ):
                _, meta_bfd = self.af_utils.run_data_pipeline(
                    fasta_path=fasta_path,
                    run_multimer_system=False,
                    uniref90_database_path=dbs["uniref90"],
                    mgnify_database_path=dbs["mgnify"],
                    bfd_database_path=dbs["bfd"],
                    small_bfd_database_path=dbs["small_bfd"],
                    uniref30_database_path=dbs["uniref30"],
                    uniprot_database_path=dbs["uniprot"],
                    pdb70_database_path=dbs["pdb70"],
                    obsolete_pdbs_path=dbs["obsolete"],
                    seqres_database_path=dbs["seqres"],
                    mmcif_path=tmp,
                    max_template_date="2025-01-01",
                    msa_output_path=os.path.join(tmp, "bfd_msas"),
                    features_output_path=os.path.join(tmp, "bfd.pkl"),
                    use_small_bfd=False,
                )

            spy_hhblits.assert_called_once()
            self.assertEqual(meta_bfd["bfd_msas/bfd_uniref_hits.a3m"], 2)

    def test_predict_worker_and_predict_in_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            meta_file = os.path.join(tmp, "meta.json")
            # 1. Direct _predict_worker happy path
            with mock.patch.object(
                self.af_utils,
                "predict",
                return_value={"ranking_confidence": np.float32(91.5)},
            ):
                self.af_utils._predict_worker(
                    model_features_path="f.pkl",
                    model_params_path="p",
                    model_name="model_1",
                    num_ensemble=1,
                    run_multimer_system=False,
                    random_seed=7,
                    raw_prediction_path="raw.pkl",
                    unrelaxed_protein_path="unrel.pdb",
                    result_meta_path=meta_file,
                )
            import json

            self.assertAlmostEqual(
                json.loads(Path(meta_file).read_text())["ranking_confidence"], 91.5
            )

            # 2. predict_in_subprocess uses spawn context and returns ranking_confidence
            class _FakeProc:
                def __init__(self, target, kwargs, exitcode=0):
                    self._target = target
                    self._kwargs = kwargs
                    self.exitcode = exitcode

                def start(self):
                    try:
                        self._target(**self._kwargs)
                    except RuntimeError:
                        self.exitcode = 1

                def join(self):
                    pass

            fake_ctx = mock.MagicMock()
            fake_ctx.Process.side_effect = lambda target, kwargs: _FakeProc(target, kwargs)

            with (
                mock.patch.object(
                    self.af_utils.multiprocessing, "get_context", return_value=fake_ctx
                ) as spy_ctx,
                mock.patch.object(
                    self.af_utils,
                    "predict",
                    return_value={"ranking_confidence": 87.25},
                ),
            ):
                res = self.af_utils.predict_in_subprocess(
                    model_features_path="f.pkl",
                    model_params_path="p",
                    model_name="model_1",
                    num_ensemble=1,
                    run_multimer_system=False,
                    random_seed=42,
                    raw_prediction_path="raw.pkl",
                    unrelaxed_protein_path="unrel.pdb",
                )
            spy_ctx.assert_called_once_with("spawn")
            self.assertAlmostEqual(res["ranking_confidence"], 87.25)

            # 3. Failure in child subprocess propagates RuntimeError with error detail
            with (
                mock.patch.object(
                    self.af_utils.multiprocessing, "get_context", return_value=fake_ctx
                ),
                mock.patch.object(
                    self.af_utils,
                    "predict",
                    side_effect=RuntimeError("CUDA OOM"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "CUDA OOM"):
                    self.af_utils.predict_in_subprocess(
                        model_features_path="f.pkl",
                        model_params_path="p",
                        model_name="model_1",
                        num_ensemble=1,
                        run_multimer_system=False,
                        random_seed=42,
                        raw_prediction_path="raw.pkl",
                        unrelaxed_protein_path="unrel.pdb",
                    )

            # 4. Corrupt/truncated result_meta.json with non-zero exitcode reports exitcode
            #    instead of masking with json.JSONDecodeError
            class _KilledProc:
                def __init__(self, target, kwargs):
                    self._kwargs = kwargs
                    self.exitcode = -9

                def start(self):
                    Path(self._kwargs["result_meta_path"]).write_text('{"error": "trunc')

                def join(self):
                    pass

            killed_ctx = mock.MagicMock()
            killed_ctx.Process.side_effect = lambda target, kwargs: _KilledProc(target, kwargs)
            with mock.patch.object(
                self.af_utils.multiprocessing, "get_context", return_value=killed_ctx
            ):
                with self.assertRaisesRegex(RuntimeError, "exit code -9"):
                    self.af_utils.predict_in_subprocess(
                        model_features_path="f.pkl",
                        model_params_path="p",
                        model_name="model_1",
                        num_ensemble=1,
                        run_multimer_system=False,
                        random_seed=42,
                        raw_prediction_path="raw.pkl",
                        unrelaxed_protein_path="unrel.pdb",
                    )

            # 5. _predict_worker configures logging.basicConfig(level=INFO) in spawned child
            with (
                mock.patch.object(self.af_utils.logging, "basicConfig") as spy_log,
                mock.patch.object(
                    self.af_utils,
                    "predict",
                    return_value={"ranking_confidence": 90.0},
                ),
            ):
                self.af_utils._predict_worker(
                    model_features_path="f.pkl",
                    model_params_path="p",
                    model_name="model_1",
                    num_ensemble=1,
                    run_multimer_system=False,
                    random_seed=1,
                    raw_prediction_path="raw.pkl",
                    unrelaxed_protein_path="unrel.pdb",
                    result_meta_path=os.path.join(tmp, "meta_log.json"),
                )
            spy_log.assert_called_once_with(level=self.af_utils.logging.INFO)

    def test_db_size_bytes_uses_hhsuite_cs219_and_escapes_globs(self) -> None:
        """Full-BFD CPU weighting reads *_cs219.ffdata instead of ~1.7 TB a3m/hhm archives."""
        with tempfile.TemporaryDirectory() as tmp:
            # Put DB prefixes in a directory containing glob metacharacters (`dir[1]`)
            weird_dir = Path(tmp) / "dir[1]"
            weird_dir.mkdir()
            bfd_prefix = str(weird_dir / "bfd")
            Path(f"{bfd_prefix}_cs219.ffdata").write_bytes(b"x" * 100)
            Path(f"{bfd_prefix}_a3m.ffdata").write_bytes(b"x" * 5000)
            Path(f"{bfd_prefix}_hhm.ffdata").write_bytes(b"x" * 3000)
            self.assertEqual(self.af_utils._db_size_bytes(bfd_prefix), 100)

            # Fallback without cs219.ffdata ignores *_a3m.ffdata and *_hhm.ffdata
            no_cs_prefix = str(weird_dir / "nocs")
            Path(f"{no_cs_prefix}_a3m.ffdata").write_bytes(b"x" * 4000)
            Path(f"{no_cs_prefix}_hhm.ffdata").write_bytes(b"x" * 2000)
            Path(f"{no_cs_prefix}_cs219.ffindex").write_bytes(b"x" * 42)
            self.assertEqual(self.af_utils._db_size_bytes(no_cs_prefix), 42)

            # _has_af2_raw_databases finds prefixes inside bracketed paths
            for suffix in ("_cs219.ffdata",):
                Path(f"{weird_dir / 'uniref30'}{suffix}").write_bytes(b"u")
                Path(f"{weird_dir / 'pdb70'}{suffix}").write_bytes(b"p")
            for fname in ("uniref90.fa", "mgnify.fa"):
                (weird_dir / fname).write_bytes(b"f")
            self.assertTrue(
                self.af_utils._has_af2_raw_databases(
                    run_multimer_system=False,
                    use_small_bfd=False,
                    uniref90_database_path=str(weird_dir / "uniref90.fa"),
                    mgnify_database_path=str(weird_dir / "mgnify.fa"),
                    bfd_database_path=bfd_prefix,
                    small_bfd_database_path="",
                    uniref30_database_path=str(weird_dir / "uniref30"),
                    uniprot_database_path="",
                    pdb70_database_path=str(weird_dir / "pdb70"),
                    seqres_database_path="",
                    mmcif_path=str(weird_dir),
                )
            )

        # Realistic full-BFD sizes (GB): mgnify=120, uniprot=108, bfd+uniref30 cs219=79, uniref90=67
        gb = 1_000_000_000
        mono_specs = [
            ("mgnify", 120 * gb, 2.0),
            ("bfd", (57 + 22) * gb, 4.0),
            ("uniref90", 67 * gb, 1.0),
        ]
        self.assertEqual(self.af_utils._resolve_search_cpus(mono_specs, 8), [5, 3, 3])
        self.assertEqual(self.af_utils._resolve_search_cpus(mono_specs, 16), [10, 6, 6])

        hetero_specs = [
            ("mgnify", 120 * gb, 2.0),
            ("uniprot", 108 * gb, 1.8),
            ("bfd", (57 + 22) * gb, 4.0),
            ("uniref90", 67 * gb, 1.0),
        ]
        self.assertEqual(self.af_utils._resolve_search_cpus(hetero_specs, 8), [4, 4, 4, 4])
        self.assertEqual(self.af_utils._resolve_search_cpus(hetero_specs, 16), [8, 8, 8, 8])

    def test_promote_shared_msa_dir_heals_zero_byte_cache_files(self) -> None:
        """_promote_shared_msa_dir overwrites pre-existing 0-byte files while preserving valid ones."""
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "protein_abc"
            cache_dir.mkdir()
            (cache_dir / "mgnify_hits.sto").write_text("")  # corrupted 0-byte file
            (cache_dir / "uniref90_hits.sto").write_text("# STOCKHOLM 1.0\nvalid\n//\n")

            staged_dir = Path(tmp) / "staged_run1"
            staged_dir.mkdir()
            (staged_dir / "mgnify_hits.sto").write_text("# STOCKHOLM 1.0\nfresh\n//\n")
            (staged_dir / "uniref90_hits.sto").write_text("# STOCKHOLM 1.0\nother\n//\n")
            (staged_dir / "empty_src.sto").write_text("")

            self.af_utils._promote_shared_msa_dir(str(staged_dir), str(cache_dir), "run1")

            self.assertEqual(
                (cache_dir / "mgnify_hits.sto").read_text(),
                "# STOCKHOLM 1.0\nfresh\n//\n",
            )
            self.assertEqual(
                (cache_dir / "uniref90_hits.sto").read_text(),
                "# STOCKHOLM 1.0\nvalid\n//\n",
            )
            self.assertFalse((cache_dir / "empty_src.sto").exists())
            self.assertFalse(staged_dir.exists())

            # Fresh os.rename() branch also drops 0-byte staged files prior to promotion
            fresh_staged = Path(tmp) / "staged_fresh"
            fresh_staged.mkdir()
            (fresh_staged / "uniref90_hits.sto").write_text("# STOCKHOLM 1.0\nok\n//\n")
            (fresh_staged / "empty_fresh.sto").write_text("")
            fresh_cache = Path(tmp) / "protein_fresh"
            self.af_utils._promote_shared_msa_dir(str(fresh_staged), str(fresh_cache), "run2")
            self.assertTrue((fresh_cache / "uniref90_hits.sto").is_file())
            self.assertFalse((fresh_cache / "empty_fresh.sto").exists())

    def test_empty_fasta_record_rejected_in_data_pipeline_and_fallback(self) -> None:
        """Both run_data_pipeline and _run_single_sequence_fallback reject empty FASTA records."""
        with tempfile.TemporaryDirectory() as tmp:
            dbs = self._setup_fake_dbs(tmp)
            bad_fasta = os.path.join(tmp, "empty_rec.fasta")
            Path(bad_fasta).write_text(">empty\n\n>real\nACDEFGHIKL\n")

            with self.assertRaisesRegex(ValueError, "Empty FASTA record"):
                self.af_utils._run_single_sequence_fallback(
                    fasta_path=bad_fasta,
                    run_multimer_system=True,
                    msa_output_path=os.path.join(tmp, "fb_msas"),
                    features_output_path=os.path.join(tmp, "fb.pkl"),
                )

            with self.assertRaisesRegex(ValueError, "Empty FASTA record"):
                self.af_utils.run_data_pipeline(
                    fasta_path=bad_fasta,
                    run_multimer_system=True,
                    uniref90_database_path=dbs["uniref90"],
                    mgnify_database_path=dbs["mgnify"],
                    bfd_database_path=dbs["bfd"],
                    small_bfd_database_path=dbs["small_bfd"],
                    uniref30_database_path=dbs["uniref30"],
                    uniprot_database_path=dbs["uniprot"],
                    pdb70_database_path=dbs["pdb70"],
                    obsolete_pdbs_path=dbs["obsolete"],
                    seqres_database_path=dbs["seqres"],
                    mmcif_path=tmp,
                    max_template_date="2025-01-01",
                    msa_output_path=os.path.join(tmp, "msas"),
                    features_output_path=os.path.join(tmp, "feat.pkl"),
                    use_small_bfd=True,
                )

    def test_mmseqs2_normalizes_lowercase_sequences_and_rejects_empty_records(self) -> None:
        """run_mmseqs2_data_pipeline uppercases FASTA input and guards empty/missing records."""
        with tempfile.TemporaryDirectory() as tmp:
            dbs = self._setup_fake_dbs(tmp)

            # 1. Empty FASTA record and empty file rejected
            bad_fasta = os.path.join(tmp, "empty_rec.fasta")
            Path(bad_fasta).write_text(">empty\n\n>real\nACDEFGHIKL\n")
            empty_fasta = os.path.join(tmp, "empty.fasta")
            Path(empty_fasta).write_text("\n")

            with mock.patch.object(self.af_utils, "_check_gpu_available", return_value=False):
                with self.assertRaisesRegex(ValueError, "Empty FASTA record"):
                    self.af_utils.run_mmseqs2_data_pipeline(
                        fasta_path=bad_fasta,
                        run_multimer_system=True,
                        uniref90_mmseqs_path=os.path.join(tmp, "u_db"),
                        mgnify_mmseqs_path=os.path.join(tmp, "m_db"),
                        small_bfd_mmseqs_path=os.path.join(tmp, "s_db"),
                        uniref90_database_path=dbs["uniref90"],
                        mgnify_database_path=dbs["mgnify"],
                        uniprot_database_path=dbs["uniprot"],
                        pdb70_database_path=dbs["pdb70"],
                        obsolete_pdbs_path=dbs["obsolete"],
                        seqres_database_path=dbs["seqres"],
                        mmcif_path=tmp,
                        max_template_date="2025-01-01",
                        msa_output_path=os.path.join(tmp, "bad_msas"),
                        features_output_path=os.path.join(tmp, "bad.pkl"),
                        small_bfd_database_path=dbs["small_bfd"],
                    )

                with self.assertRaisesRegex(ValueError, "No input sequences found"):
                    self.af_utils.run_mmseqs2_data_pipeline(
                        fasta_path=empty_fasta,
                        run_multimer_system=False,
                        uniref90_mmseqs_path=os.path.join(tmp, "u_db"),
                        mgnify_mmseqs_path=os.path.join(tmp, "m_db"),
                        small_bfd_mmseqs_path=os.path.join(tmp, "s_db"),
                        uniref90_database_path=dbs["uniref90"],
                        mgnify_database_path=dbs["mgnify"],
                        uniprot_database_path=dbs["uniprot"],
                        pdb70_database_path=dbs["pdb70"],
                        obsolete_pdbs_path=dbs["obsolete"],
                        seqres_database_path=dbs["seqres"],
                        mmcif_path=tmp,
                        max_template_date="2025-01-01",
                        msa_output_path=os.path.join(tmp, "empty_msas"),
                        features_output_path=os.path.join(tmp, "empty.pkl"),
                        small_bfd_database_path=dbs["small_bfd"],
                    )

            # 2. Lowercase monomer FASTA emits uppercased query & sequence features
            seen_queries: list[str] = []

            def fake_mmseqs_search(query_fasta, target_db, output_a3m, tmp_dir, **kwargs):
                content = Path(query_fasta).read_text(encoding="utf-8")
                seen_queries.append(content)
                Path(output_a3m).write_text(content, encoding="utf-8")
                return 1

            mono_fasta = os.path.join(tmp, "lower_mono.fasta")
            Path(mono_fasta).write_text(">lower_target\nacdefghikl\n")
            fake_hh = mock.MagicMock()
            fake_hh.query.return_value = "Hit1\n"
            fake_hh.get_template_hits.return_value = []
            fake_hh_feat = mock.MagicMock()
            fake_hh_feat.get_templates.return_value = types.SimpleNamespace(
                features=self.af_utils._make_empty_template_features(10)
            )
            with (
                mock.patch.object(
                    self.af_utils, "run_mmseqs2_search", side_effect=fake_mmseqs_search
                ),
                mock.patch.object(self.af_utils, "_check_gpu_available", return_value=False),
                mock.patch.object(
                    self._fake_modules["alphafold.data.tools.hhsearch"],
                    "HHSearch",
                    return_value=fake_hh,
                ),
                mock.patch.object(
                    self._fake_modules["alphafold.data.templates"],
                    "HhsearchHitFeaturizer",
                    return_value=fake_hh_feat,
                ),
            ):
                mono_feats, _ = self.af_utils.run_mmseqs2_data_pipeline(
                    fasta_path=mono_fasta,
                    run_multimer_system=False,
                    uniref90_mmseqs_path=os.path.join(tmp, "u_db"),
                    mgnify_mmseqs_path=os.path.join(tmp, "m_db"),
                    small_bfd_mmseqs_path=os.path.join(tmp, "s_db"),
                    uniref90_database_path=dbs["uniref90"],
                    mgnify_database_path=dbs["mgnify"],
                    uniprot_database_path=dbs["uniprot"],
                    pdb70_database_path=dbs["pdb70"],
                    obsolete_pdbs_path=dbs["obsolete"],
                    seqres_database_path=dbs["seqres"],
                    mmcif_path=tmp,
                    max_template_date="2025-01-01",
                    msa_output_path=os.path.join(tmp, "mono_msas"),
                    features_output_path=os.path.join(tmp, "mono.pkl"),
                    small_bfd_database_path=dbs["small_bfd"],
                )
            self.assertEqual(mono_feats["sequence"][0], b"ACDEFGHIKL")
            self.assertTrue(all("ACDEFGHIKL" in q for q in seen_queries))
            fake_hh.get_template_hits.assert_called_once_with(
                output_string="Hit1\n", input_sequence="ACDEFGHIKL"
            )
            fake_hh_feat.get_templates.assert_called_once_with(query_sequence="ACDEFGHIKL", hits=[])

            # 3. Mixed-case homodimer is treated as homomer (skips UniProt jackhmmer)
            homo_fasta = os.path.join(tmp, "mixed_homo.fasta")
            Path(homo_fasta).write_text(">cA\nacdefghikl\n>cB\nACDEFGHIKL\n")
            fake_hmm = mock.MagicMock()
            fake_hmm.query.return_value = "# STOCKHOLM 1.0\nhit1 ACDEFGHIKL\n//\n"
            fake_hmm.get_template_hits.return_value = []
            fake_hmm_feat = mock.MagicMock()
            fake_hmm_feat.get_templates.return_value = types.SimpleNamespace(
                features=self.af_utils._make_empty_template_features(10)
            )
            with (
                mock.patch.object(
                    self.af_utils, "run_mmseqs2_search", side_effect=fake_mmseqs_search
                ),
                mock.patch.object(self.af_utils, "_check_gpu_available", return_value=False),
                mock.patch.object(
                    self._fake_modules["alphafold.data.tools.hmmsearch"],
                    "Hmmsearch",
                    return_value=fake_hmm,
                ),
                mock.patch.object(
                    self._fake_modules["alphafold.data.templates"],
                    "HmmsearchHitFeaturizer",
                    return_value=fake_hmm_feat,
                ),
                mock.patch.object(
                    self._fake_modules["alphafold.data.tools.jackhmmer"],
                    "Jackhmmer",
                ) as spy_jackhmmer,
            ):
                homo_feats, _ = self.af_utils.run_mmseqs2_data_pipeline(
                    fasta_path=homo_fasta,
                    run_multimer_system=True,
                    uniref90_mmseqs_path=os.path.join(tmp, "u_db"),
                    mgnify_mmseqs_path=os.path.join(tmp, "m_db"),
                    small_bfd_mmseqs_path=os.path.join(tmp, "s_db"),
                    uniref90_database_path=dbs["uniref90"],
                    mgnify_database_path=dbs["mgnify"],
                    uniprot_database_path=dbs["uniprot"],
                    pdb70_database_path=dbs["pdb70"],
                    obsolete_pdbs_path=dbs["obsolete"],
                    seqres_database_path=dbs["seqres"],
                    mmcif_path=tmp,
                    max_template_date="2025-01-01",
                    msa_output_path=os.path.join(tmp, "homo_msas"),
                    features_output_path=os.path.join(tmp, "homo.pkl"),
                    small_bfd_database_path=dbs["small_bfd"],
                )
            self.assertEqual(homo_feats["chain_ids"], ["A", "B"])
            spy_jackhmmer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
