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

"""Cross-model contract tests for shared protein MSA caching and parallel HMMER."""

import hashlib
import json
import os
import subprocess
import sys
import threading
import types

import pytest
import yaml


def _make_artifact(path: str, uri: str, metadata: dict | None = None):
    return types.SimpleNamespace(path=path, uri=uri, metadata=metadata or {})


def _load_msa_funcs(monkeypatch):
    """Import all three KFP MSA lightweight component Python functions safely."""
    from foldrun_app.models.boltz2.pipeline import config as boltz2_config
    from foldrun_app.models.esmfold2.pipeline import config as esmfold2_config
    from foldrun_app.models.of3.pipeline import config as of3_config

    monkeypatch.setitem(sys.modules, "config", of3_config)
    from foldrun_app.models.of3.pipeline.components.msa_pipeline import msa_pipeline_of3

    monkeypatch.setitem(sys.modules, "config", boltz2_config)
    from foldrun_app.models.boltz2.pipeline.components.msa_pipeline import msa_pipeline_boltz2

    monkeypatch.setitem(sys.modules, "config", esmfold2_config)
    from foldrun_app.models.esmfold2.pipeline.components.msa_pipeline import msa_pipeline_esmfold2

    return (
        msa_pipeline_of3.python_func,
        msa_pipeline_boltz2.python_func,
        msa_pipeline_esmfold2.python_func,
    )


def _setup_nfs(tmp_path, include_dbs: bool = True, include_rna_dbs: bool = False):
    nfs_mount = tmp_path / "nfs"
    nfs_mount.mkdir(parents=True, exist_ok=True)
    if include_dbs:
        for db_name in ("uniref90.fasta", "mgnify.fasta", "pdb_seqres.fasta"):
            (nfs_mount / db_name).write_text(">dummy\nACDEFGHIK\n")
    if include_rna_dbs:
        for db_name in ("rfam.fasta", "rnacentral.fasta"):
            (nfs_mount / db_name).write_text(">dummy_rna\nACGUACGU\n")
    ref_db = _make_artifact(
        path=str(nfs_mount),
        uri=str(nfs_mount),
        metadata={
            "uniref90": "uniref90.fasta",
            "mgnify": "mgnify.fasta",
            "pdb_seqres": "pdb_seqres.fasta",
            "rfam": "rfam.fasta" if include_rna_dbs else "",
            "rnacentral": "rnacentral.fasta" if include_rna_dbs else "",
        },
    )
    return nfs_mount, ref_db


def _write_sto_for_cmd(cmd: list[str], seq: str) -> None:
    """Write realistic Stockholm output with #=GC RF, insertions, and cross-DB overlap."""
    out_path = cmd[cmd.index("-A") + 1]
    db_path = cmd[-1]
    seq_hash = hashlib.sha256(seq.strip().encode()).hexdigest()
    # 9-residue seq "ACDEFGHIK" represented across 11 columns (2 insertion columns at idx 2, 7)
    assert len(seq.strip()) == 9
    s = seq.strip()
    q_blk1 = f"{s[:2]}.{s[2:5]}"
    q_blk2 = f"{s[5:6]}.{s[6:]}"
    rf_blk1 = "xx.xxx"
    rf_blk2 = "x.xxx"
    if db_path.endswith("uniref90.fasta"):
        content = (
            "# STOCKHOLM 1.0\n"
            f"protein_{seq_hash} {q_blk1}\n"
            f"uni_hit_1 {s[:2]}a{s[2:5]}\n"
            f"shared_hit {s[:2]}.{s[2:4]}-\n"
            f"malformed_hit {s[:4]}\n"
            f"#=GC RF {rf_blk1}\n"
            "\n"
            f"protein_{seq_hash} {q_blk2}\n"
            f"uni_hit_1 {s[5:6]}b{s[6:]}\n"
            f"shared_hit {s[5:6]}.{s[6:]}\n"
            f"malformed_hit {s[5:]}\n"
            f"#=GC RF {rf_blk2}\n"
            "//\n"
        )
    elif db_path.endswith("mgnify.fasta"):
        content = (
            "# STOCKHOLM 1.0\n"
            f"protein_{seq_hash} {q_blk1}{q_blk2}\n"
            f"shared_hit_dup {s[:2]}.{s[2:4]}-{s[5:6]}.{s[6:]}\n"
            f"mgy_hit_1 -{s[1:2]}g{s[2:5]}{s[5:6]}.{s[6:8]}.\n"
            f"#=GC RF {rf_blk1}{rf_blk2}\n"
            "//\n"
        )
    else:
        content = f"# STOCKHOLM 1.0\nprotein_{seq_hash} {s}\ntpl_hit {s}\n//\n"
    with open(out_path, "w") as f:
        f.write(content)


def _run_of3(msa_of3, tmp_path, ref_db, seq: str, tag: str, use_templates: bool = True):
    in_path = tmp_path / f"of3_in_{tag}.json"
    out_path = tmp_path / f"of3_out_{tag}.json"
    in_path.write_text(
        json.dumps(
            {
                "queries": {
                    f"q_{tag}": {
                        "chains": [
                            {"molecule_type": "protein", "chain_ids": ["A"], "sequence": seq}
                        ]
                    }
                }
            }
        )
    )
    out_art = _make_artifact(path=str(out_path), uri=str(out_path))
    msa_of3(
        query_json=_make_artifact(path=str(in_path), uri=str(in_path)),
        ref_databases=ref_db,
        updated_query_json=out_art,
        model_format="of3",
        use_templates=use_templates,
    )
    return json.loads(out_path.read_text())["queries"][f"q_{tag}"]["chains"][0], out_art


def _run_boltz2(msa_boltz2, tmp_path, ref_db, seq: str, tag: str):
    in_path = tmp_path / f"boltz2_in_{tag}.yaml"
    out_path = tmp_path / f"boltz2_out_{tag}.yaml"
    in_path.write_text(
        yaml.dump({"version": 1, "sequences": [{"protein": {"id": "A", "sequence": seq}}]})
    )
    out_art = _make_artifact(path=str(out_path), uri=str(out_path))
    msa_boltz2(
        query_json=_make_artifact(path=str(in_path), uri=str(in_path)),
        ref_databases=ref_db,
        updated_query_json=out_art,
        model_format="boltz2",
        use_templates=False,
    )
    return yaml.safe_load(out_path.read_text())["sequences"][0]["protein"], out_art


def _run_esmfold2(msa_esmfold2, tmp_path, ref_db, seq: str, tag: str):
    in_path = tmp_path / f"esm_in_{tag}.json"
    out_path = tmp_path / f"esm_out_{tag}.json"
    in_path.write_text(json.dumps({"sequences": [{"protein": {"id": "B", "sequence": seq}}]}))
    out_art = _make_artifact(path=str(out_path), uri=str(out_path))
    msa_esmfold2(
        query_json=_make_artifact(path=str(in_path), uri=str(in_path)),
        ref_databases=ref_db,
        updated_query_json=out_art,
        model_format="esmfold2",
        use_templates=False,
    )
    return json.loads(out_path.read_text())["sequences"][0]["protein"], out_art


class TestSharedMSACacheCrossModel:
    """Verify cross-model reuse across OF3, Boltz-2, and ESMFold2."""

    def test_of3_warms_cache_for_boltz2_and_esmfold2_via_lazy_a3m(self, tmp_path, monkeypatch):
        """OF3 -> Boltz-2 (lazy A3M, 0 jackhmmer) -> ESMFold2 (direct A3M hit, 0 jackhmmer)."""
        msa_of3, msa_boltz2, msa_esmfold2 = _load_msa_funcs(monkeypatch)
        nfs_mount, ref_db = _setup_nfs(tmp_path)
        seq = "ACDEFGHIK"
        seq_hash = hashlib.sha256(seq.encode()).hexdigest()

        called_cmds: list[list[str]] = []

        def _fake_run(cmd, check=True):
            called_cmds.append(cmd)
            _write_sto_for_cmd(cmd, seq)

        monkeypatch.setattr(subprocess, "run", _fake_run)

        # 1. Cold OF3 run populates shared_msas_cache with .sto files (no combined.a3m yet)
        of3_chain, _ = _run_of3(msa_of3, tmp_path, ref_db, seq, "1", use_templates=True)
        assert len(called_cmds) == 3
        cache_dir = nfs_mount / "shared_msas_cache" / f"protein_{seq_hash}"
        assert (cache_dir / "uniref90_hits.sto").is_file()
        assert (cache_dir / "mgnify_hits.sto").is_file()
        assert (cache_dir / "pdb_seqres.sto").is_file()
        assert not (cache_dir / "combined.a3m").exists()
        assert not (cache_dir / "query.fasta").exists()
        assert of3_chain["template_alignment_file_path"] == str(cache_dir / "pdb_seqres.sto")

        # 2. Boltz-2 lazily derives combined.a3m from shared .sto files with 0 jackhmmer calls
        boltz_prot, _ = _run_boltz2(msa_boltz2, tmp_path, ref_db, seq, "2")
        assert len(called_cmds) == 3
        assert boltz_prot["msa"] == str(cache_dir / "combined.a3m")
        assert (cache_dir / "combined.a3m").is_file()
        assert not any(p.name.endswith(".tmp") for p in cache_dir.iterdir())

        # 3. ESMFold2 hits combined.a3m directly with 0 jackhmmer calls
        esm_prot, _ = _run_esmfold2(msa_esmfold2, tmp_path, ref_db, seq, "3")
        assert len(called_cmds) == 3
        assert esm_prot["msa"] == str(cache_dir / "combined.a3m")

    def test_of3_warms_cache_for_esmfold2_first_via_lazy_a3m(self, tmp_path, monkeypatch):
        """OF3 -> ESMFold2 (lazy A3M, 0 jackhmmer) -> Boltz-2 (direct A3M hit, 0 jackhmmer)."""
        msa_of3, msa_boltz2, msa_esmfold2 = _load_msa_funcs(monkeypatch)
        nfs_mount, ref_db = _setup_nfs(tmp_path)
        seq = "ACDEFGHIK"
        seq_hash = hashlib.sha256(seq.encode()).hexdigest()

        called_cmds: list[list[str]] = []

        def _fake_run(cmd, check=True):
            called_cmds.append(cmd)
            _write_sto_for_cmd(cmd, seq)

        monkeypatch.setattr(subprocess, "run", _fake_run)

        _run_of3(msa_of3, tmp_path, ref_db, seq, "1", use_templates=False)
        assert len(called_cmds) == 2

        esm_prot, _ = _run_esmfold2(msa_esmfold2, tmp_path, ref_db, seq, "2")
        assert len(called_cmds) == 2
        cache_dir = nfs_mount / "shared_msas_cache" / f"protein_{seq_hash}"
        assert esm_prot["msa"] == str(cache_dir / "combined.a3m")

        boltz_prot, _ = _run_boltz2(msa_boltz2, tmp_path, ref_db, seq, "3")
        assert len(called_cmds) == 2
        assert boltz_prot["msa"] == str(cache_dir / "combined.a3m")

    @pytest.mark.parametrize("first_model", ["boltz2", "esmfold2"])
    def test_a3m_model_warms_cache_for_peer_and_of3_partial_template_hit(
        self, tmp_path, monkeypatch, first_model
    ):
        """Boltz-2/ESMFold2 -> peer (0 calls) -> OF3 no-tpl (0 calls) -> OF3 with-tpl (1 pdb_seqres call)."""
        msa_of3, msa_boltz2, msa_esmfold2 = _load_msa_funcs(monkeypatch)
        nfs_mount, ref_db = _setup_nfs(tmp_path)
        seq = "ACDEFGHIK"
        seq_hash = hashlib.sha256(seq.encode()).hexdigest()

        called_cmds: list[list[str]] = []

        def _fake_run(cmd, check=True):
            called_cmds.append(cmd)
            _write_sto_for_cmd(cmd, seq)

        monkeypatch.setattr(subprocess, "run", _fake_run)

        if first_model == "boltz2":
            _run_boltz2(msa_boltz2, tmp_path, ref_db, seq, "1")
        else:
            _run_esmfold2(msa_esmfold2, tmp_path, ref_db, seq, "1")
        assert len(called_cmds) == 2

        cache_dir = nfs_mount / "shared_msas_cache" / f"protein_{seq_hash}"
        assert {p.name for p in cache_dir.iterdir()} == {
            "uniref90_hits.sto",
            "mgnify_hits.sto",
            "combined.a3m",
        }

        # Peer A3M model gets instant hit
        if first_model == "boltz2":
            peer_prot, _ = _run_esmfold2(msa_esmfold2, tmp_path, ref_db, seq, "2")
        else:
            peer_prot, _ = _run_boltz2(msa_boltz2, tmp_path, ref_db, seq, "2")
        assert len(called_cmds) == 2
        assert peer_prot["msa"] == str(cache_dir / "combined.a3m")

        # OF3 without templates gets instant full hit (0 jackhmmer calls)
        of3_no_tpl, _ = _run_of3(msa_of3, tmp_path, ref_db, seq, "3", use_templates=False)
        assert len(called_cmds) == 2
        assert of3_no_tpl["main_msa_file_paths"] == [
            str(cache_dir / "uniref90_hits.sto"),
            str(cache_dir / "mgnify_hits.sto"),
        ]

        # OF3 with templates gets a partial hit -> runs ONLY pdb_seqres (1 jackhmmer call)
        called_cmds.clear()
        of3_with_tpl, _ = _run_of3(msa_of3, tmp_path, ref_db, seq, "4", use_templates=True)
        assert len(called_cmds) == 1
        assert called_cmds[0][-1] == str(nfs_mount / "pdb_seqres.fasta")
        assert of3_with_tpl["template_alignment_file_path"] == str(cache_dir / "pdb_seqres.sto")
        assert (cache_dir / "pdb_seqres.sto").is_file()
        assert (cache_dir / "combined.a3m").is_file()

    def test_whitespace_padded_sequences_share_identical_cache_key(self, tmp_path, monkeypatch):
        """Leading/trailing whitespace is stripped consistently before SHA-256 hashing."""
        msa_of3, msa_boltz2, msa_esmfold2 = _load_msa_funcs(monkeypatch)
        _, ref_db = _setup_nfs(tmp_path)
        called_cmds: list[list[str]] = []

        def _fake_run(cmd, check=True):
            called_cmds.append(cmd)
            _write_sto_for_cmd(cmd, "ACDEFGHIK")

        monkeypatch.setattr(subprocess, "run", _fake_run)

        _run_boltz2(msa_boltz2, tmp_path, ref_db, "  ACDEFGHIK\n", "1")
        assert len(called_cmds) == 2
        _run_esmfold2(msa_esmfold2, tmp_path, ref_db, "ACDEFGHIK", "2")
        _run_of3(msa_of3, tmp_path, ref_db, "\tACDEFGHIK ", "3", use_templates=False)
        assert len(called_cmds) == 2


class TestA3MParityAndFormat:
    """Verify Boltz-2 and ESMFold2 produce byte-for-byte identical, valid combined.a3m."""

    def test_boltz2_and_esmfold2_produce_identical_validated_a3m(self, tmp_path, monkeypatch):
        """Verify #=GC RF columns, single query header, dedup, insertion lowercasing, and filtering."""
        _, msa_boltz2, msa_esmfold2 = _load_msa_funcs(monkeypatch)
        seq = "ACDEFGHIK"
        seq_hash = hashlib.sha256(seq.encode()).hexdigest()

        def _fake_run(cmd, check=True):
            _write_sto_for_cmd(cmd, seq)

        monkeypatch.setattr(subprocess, "run", _fake_run)

        nfs1, ref_db1 = _setup_nfs(tmp_path / "run_boltz2")
        boltz_prot, _ = _run_boltz2(msa_boltz2, tmp_path / "run_boltz2", ref_db1, seq, "b")

        nfs2, ref_db2 = _setup_nfs(tmp_path / "run_esm")
        esm_prot, _ = _run_esmfold2(msa_esmfold2, tmp_path / "run_esm", ref_db2, seq, "e")

        boltz_a3m = open(boltz_prot["msa"]).read()
        esm_a3m = open(esm_prot["msa"]).read()
        assert boltz_a3m == esm_a3m

        expected_a3m = (
            f">protein_{seq_hash}\n"
            "ACDEFGHIK\n"
            ">uni_hit_1\n"
            "ACaDEFGbHIK\n"
            ">shared_hit\n"
            "ACDE-GHIK\n"
            ">mgy_hit_1\n"
            "-CgDEFGHI-\n"
        )
        assert boltz_a3m == expected_a3m
        assert boltz_a3m.count(f">protein_{seq_hash}") == 1
        assert "malformed_hit" not in boltz_a3m
        assert "shared_hit_dup" not in boltz_a3m
        assert (nfs1 / "shared_msas_cache" / f"protein_{seq_hash}" / "combined.a3m").is_file()
        assert (nfs2 / "shared_msas_cache" / f"protein_{seq_hash}" / "combined.a3m").is_file()

    def test_query_only_sto_emits_valid_single_row_a3m(self, tmp_path, monkeypatch):
        """Zero homolog hits still produces a valid single-sequence combined.a3m."""
        _, msa_boltz2, msa_esmfold2 = _load_msa_funcs(monkeypatch)
        seq = "ACDEFGHIK"
        seq_hash = hashlib.sha256(seq.encode()).hexdigest()

        def _fake_empty_hits(cmd, check=True):
            out_path = cmd[cmd.index("-A") + 1]
            with open(out_path, "w") as f:
                f.write(f"# STOCKHOLM 1.0\nprotein_{seq_hash} {seq}\n#=GC RF xxxxxxxxx\n//\n")

        monkeypatch.setattr(subprocess, "run", _fake_empty_hits)

        _, ref_db1 = _setup_nfs(tmp_path / "qonly_b")
        boltz_prot, _ = _run_boltz2(msa_boltz2, tmp_path / "qonly_b", ref_db1, seq, "b")

        _, ref_db2 = _setup_nfs(tmp_path / "qonly_e")
        esm_prot, _ = _run_esmfold2(msa_esmfold2, tmp_path / "qonly_e", ref_db2, seq, "e")

        expected = f">protein_{seq_hash}\n{seq}\n"
        assert open(boltz_prot["msa"]).read() == expected
        assert open(esm_prot["msa"]).read() == expected

    @pytest.mark.parametrize("model_name", ["boltz2", "esmfold2"])
    def test_combined_a3m_enforces_16384_sequence_cap(self, tmp_path, monkeypatch, model_name):
        """combined.a3m caps total unique sequences (including query) at 16384."""
        _, msa_boltz2, msa_esmfold2 = _load_msa_funcs(monkeypatch)
        seq = "ACDEFGHIK"
        seq_hash = hashlib.sha256(seq.encode()).hexdigest()
        alphabet = "ACDEFGHIKLMNPQRSTVWY"

        def _make_unique_seqs(prefix: str, count: int, offset: int) -> list[str]:
            lines = ["# STOCKHOLM 1.0", f"protein_{seq_hash} {seq}"]
            for idx in range(count):
                n = idx + offset
                # Encode n in base-20 across 4 positions + fixed suffix -> 160,000 unique 9-mers
                c0 = alphabet[n % 20]
                c1 = alphabet[(n // 20) % 20]
                c2 = alphabet[(n // 400) % 20]
                c3 = alphabet[(n // 8000) % 20]
                cand = f"{c0}{c1}{c2}{c3}FGHIK"
                if cand != seq:
                    lines.append(f"{prefix}_{idx} {cand}")
            lines.append("#=GC RF xxxxxxxxx")
            lines.append("//")
            return lines

        uniref_text = "\n".join(_make_unique_seqs("u", 10000, 1)) + "\n"
        mgnify_text = "\n".join(_make_unique_seqs("m", 8000, 12000)) + "\n"

        def _fake_large_run(cmd, check=True):
            out_path = cmd[cmd.index("-A") + 1]
            text = uniref_text if cmd[-1].endswith("uniref90.fasta") else mgnify_text
            with open(out_path, "w") as f:
                f.write(text)

        monkeypatch.setattr(subprocess, "run", _fake_large_run)

        subdir = tmp_path / f"cap_{model_name}"
        _, ref_db = _setup_nfs(subdir)
        if model_name == "boltz2":
            prot, _ = _run_boltz2(msa_boltz2, subdir, ref_db, seq, "cap")
        else:
            prot, _ = _run_esmfold2(msa_esmfold2, subdir, ref_db, seq, "cap")

        headers = [line for line in open(prot["msa"]) if line.startswith(">")]
        assert len(headers) == 16384
        assert headers[0] == f">protein_{seq_hash}\n"


class TestHMMERFlagsAndParallelExecution:
    """Verify HMMER CLI standardization, dynamic CPU budgeting, concurrency, and error cleanup."""

    @pytest.mark.parametrize(
        "host_cpus,expected_mgnify_cpu,expected_other_cpu",
        [(8, "5", "3"), (16, "11", "5"), (4, "3", "1"), (2, "1", "1"), (1, "1", "1")],
    )
    def test_jackhmmer_flags_and_dynamic_cpu_splitting(
        self, tmp_path, monkeypatch, host_cpus, expected_mgnify_cpu, expected_other_cpu
    ):
        """All 3 models enforce tuned flags and database-size-weighted CPU splitting."""
        msa_of3, msa_boltz2, msa_esmfold2 = _load_msa_funcs(monkeypatch)
        monkeypatch.setattr(os, "cpu_count", lambda: host_cpus)
        seq = "ACDEFGHIK"

        for model_name in ("of3", "boltz2", "esmfold2"):
            subdir = tmp_path / f"{model_name}_{host_cpus}"
            nfs_mount, ref_db = _setup_nfs(subdir)
            cmds: list[list[str]] = []

            def _fake_run(cmd, check=True, _cmds=cmds):
                _cmds.append(cmd)
                _write_sto_for_cmd(cmd, seq)

            monkeypatch.setattr(subprocess, "run", _fake_run)

            if model_name == "of3":
                _run_of3(msa_of3, subdir, ref_db, seq, "run", use_templates=True)
                assert len(cmds) == 3
            elif model_name == "boltz2":
                _run_boltz2(msa_boltz2, subdir, ref_db, seq, "run")
                assert len(cmds) == 2
            else:
                _run_esmfold2(msa_esmfold2, subdir, ref_db, seq, "run")
                assert len(cmds) == 2

            for cmd in cmds:
                assert cmd[0] == "jackhmmer"
                assert "--max" not in cmd
                assert "--noali" in cmd
                assert cmd[cmd.index("-o") + 1] == os.devnull
                assert cmd[cmd.index("-N") + 1] == "1"
                if cmd[-1] == str(nfs_mount / "mgnify.fasta"):
                    assert cmd[cmd.index("--cpu") + 1] == expected_mgnify_cpu
                    assert cmd[cmd.index("-E") + 1] == "0.0001"
                    assert cmd[cmd.index("--incE") + 1] == "0.0001"
                elif cmd[-1] == str(nfs_mount / "uniref90.fasta"):
                    assert cmd[cmd.index("--cpu") + 1] == expected_other_cpu
                    assert cmd[cmd.index("-E") + 1] == "0.0001"
                    assert cmd[cmd.index("--incE") + 1] == "0.0001"
                else:
                    assert cmd[cmd.index("--cpu") + 1] == expected_other_cpu

        # Partial template hit in OF3 (1 job) gets all host_cpus
        partial_dir = tmp_path / f"of3_partial_{host_cpus}"
        nfs_mount, ref_db = _setup_nfs(partial_dir)
        cmds_partial: list[list[str]] = []
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, check=True: (cmds_partial.append(cmd), _write_sto_for_cmd(cmd, seq)),
        )
        _run_of3(msa_of3, partial_dir, ref_db, seq, "no_tpl", use_templates=False)
        cmds_partial.clear()
        _run_of3(msa_of3, partial_dir, ref_db, seq, "with_tpl", use_templates=True)
        assert len(cmds_partial) == 1
        assert cmds_partial[0][cmds_partial[0].index("--cpu") + 1] == str(host_cpus)

    @pytest.mark.parametrize(
        "mgnify_bytes,uniref_bytes,host_cpus,expected_mgnify,expected_uniref",
        [
            (128_579_703_018, 62_746_679_540, 8, "5", "3"),
            (128_579_703_018, 62_746_679_540, 16, "11", "5"),
            (150_000_000_000, 50_000_000_000, 8, "6", "2"),
            (80_000_000_000, 80_000_000_000, 8, "4", "4"),
        ],
    )
    def test_file_size_weighted_cpu_allocation_uses_real_db_sizes(
        self,
        tmp_path,
        monkeypatch,
        mgnify_bytes,
        uniref_bytes,
        host_cpus,
        expected_mgnify,
        expected_uniref,
    ):
        """Real multi-GiB DB files split vCPUs dynamically in proportion to os.path.getsize."""
        _, msa_boltz2, _ = _load_msa_funcs(monkeypatch)
        monkeypatch.setattr(os, "cpu_count", lambda: host_cpus)
        _, ref_db = _setup_nfs(tmp_path)
        seq = "ACDEFGHIK"
        real_getsize = os.path.getsize

        def _fake_getsize(path: str) -> int:
            if path.endswith("mgnify.fasta"):
                return mgnify_bytes
            if path.endswith("uniref90.fasta"):
                return uniref_bytes
            return real_getsize(path)

        monkeypatch.setattr(os.path, "getsize", _fake_getsize)
        cmds: list[list[str]] = []
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, check=True: (cmds.append(cmd), _write_sto_for_cmd(cmd, seq)),
        )
        _run_boltz2(msa_boltz2, tmp_path, ref_db, seq, "weighted")
        cpu_by_db = {os.path.basename(cmd[-1]): cmd[cmd.index("--cpu") + 1] for cmd in cmds}
        assert cpu_by_db == {
            "mgnify.fasta": expected_mgnify,
            "uniref90.fasta": expected_uniref,
        }

    @pytest.mark.parametrize("model_name", ["of3", "boltz2", "esmfold2"])
    def test_protein_databases_execute_concurrently(self, tmp_path, monkeypatch, model_name):
        """A threading.Barrier(2) proves mgnify and uniref90 run simultaneously in ThreadPoolExecutor."""
        msa_of3, msa_boltz2, msa_esmfold2 = _load_msa_funcs(monkeypatch)
        _, ref_db = _setup_nfs(tmp_path / model_name)
        seq = "ACDEFGHIK"
        barrier = threading.Barrier(2, timeout=2.0)

        def _fake_concurrent_run(cmd, check=True):
            barrier.wait()
            _write_sto_for_cmd(cmd, seq)

        monkeypatch.setattr(subprocess, "run", _fake_concurrent_run)

        if model_name == "of3":
            _run_of3(msa_of3, tmp_path / model_name, ref_db, seq, "c", use_templates=False)
        elif model_name == "boltz2":
            _run_boltz2(msa_boltz2, tmp_path / model_name, ref_db, seq, "c")
        else:
            _run_esmfold2(msa_esmfold2, tmp_path / model_name, ref_db, seq, "c")

    def test_of3_rna_nhmmer_executes_concurrently_with_tuned_flags(self, tmp_path, monkeypatch):
        """OF3 RNA nhmmer runs rfam + rnacentral concurrently with -o /dev/null and split CPUs."""
        msa_of3, _, _ = _load_msa_funcs(monkeypatch)
        monkeypatch.setattr(os, "cpu_count", lambda: 16)
        _, ref_db = _setup_nfs(tmp_path / "rna", include_dbs=False, include_rna_dbs=True)

        in_path = tmp_path / "rna_in.json"
        out_path = tmp_path / "rna_out.json"
        in_path.write_text(
            json.dumps(
                {
                    "queries": {
                        "rna_q": {
                            "chains": [
                                {"molecule_type": "rna", "chain_ids": ["R"], "sequence": "ACGUACGU"}
                            ]
                        }
                    }
                }
            )
        )
        barrier = threading.Barrier(2, timeout=2.0)
        cmds: list[list[str]] = []

        def _fake_nhmmer(cmd, check=True):
            cmds.append(cmd)
            barrier.wait()
            out_idx = cmd.index("-A") + 1
            with open(cmd[out_idx], "w") as f:
                f.write("# STOCKHOLM 1.0\n//\n")

        monkeypatch.setattr(subprocess, "run", _fake_nhmmer)

        msa_of3(
            query_json=_make_artifact(path=str(in_path), uri=str(in_path)),
            ref_databases=ref_db,
            updated_query_json=_make_artifact(path=str(out_path), uri=str(out_path)),
            use_templates=False,
        )
        assert len(cmds) == 2
        cpu_by_db: dict[str, str] = {}
        for cmd in cmds:
            assert cmd[0] == "nhmmer"
            assert "--noali" in cmd
            assert cmd[cmd.index("-o") + 1] == os.devnull
            cpu_by_db[os.path.basename(cmd[-1])] = cmd[cmd.index("--cpu") + 1]
        assert cpu_by_db == {"rnacentral.fasta": "14", "rfam.fasta": "2"}

    @pytest.mark.parametrize("model_name", ["of3", "boltz2", "esmfold2"])
    def test_failed_hmmer_cleans_up_staging_dir(self, tmp_path, monkeypatch, model_name):
        """A subprocess failure cleans up shared_msas_tmp staging dir and leaves cache untouched."""
        msa_of3, msa_boltz2, msa_esmfold2 = _load_msa_funcs(monkeypatch)
        nfs_mount, ref_db = _setup_nfs(tmp_path / model_name)
        seq = "ACDEFGHIK"

        def _fake_fail(cmd, check=True):
            if cmd[-1].endswith("mgnify.fasta"):
                raise subprocess.CalledProcessError(1, cmd)
            _write_sto_for_cmd(cmd, seq)

        monkeypatch.setattr(subprocess, "run", _fake_fail)

        with pytest.raises(subprocess.CalledProcessError):
            if model_name == "of3":
                _run_of3(msa_of3, tmp_path / model_name, ref_db, seq, "err", use_templates=False)
            elif model_name == "boltz2":
                _run_boltz2(msa_boltz2, tmp_path / model_name, ref_db, seq, "err")
            else:
                _run_esmfold2(msa_esmfold2, tmp_path / model_name, ref_db, seq, "err")

        assert list((nfs_mount / "shared_msas_tmp").iterdir()) == []
        assert list((nfs_mount / "shared_msas_cache").iterdir()) == []


class TestConcurrencyRaceAndCoreIsolation:
    """Verify atomic merge on rename collision and strict core-mode isolation."""

    @pytest.mark.parametrize("model_name", ["boltz2", "esmfold2"])
    def test_concurrent_of3_race_merges_combined_a3m_atomically(
        self, tmp_path, monkeypatch, model_name
    ):
        """If OF3 wins os.rename first (no combined.a3m), Boltz-2/ESMFold2 merges combined.a3m."""
        _, msa_boltz2, msa_esmfold2 = _load_msa_funcs(monkeypatch)
        nfs_mount, ref_db = _setup_nfs(tmp_path / model_name)
        seq = "ACDEFGHIK"
        seq_hash = hashlib.sha256(seq.encode()).hexdigest()
        cache_dir = nfs_mount / "shared_msas_cache" / f"protein_{seq_hash}"

        def _fake_run_with_race(cmd, check=True):
            _write_sto_for_cmd(cmd, seq)
            # Simulate OF3 populating seq_cache_dir mid-run without combined.a3m
            if cmd[-1].endswith("uniref90.fasta"):
                cache_dir.mkdir(parents=True, exist_ok=True)
                (cache_dir / "uniref90_hits.sto").write_text(
                    f"# STOCKHOLM 1.0\nprotein_{seq_hash} {seq}\n//\n"
                )
                (cache_dir / "mgnify_hits.sto").write_text(
                    f"# STOCKHOLM 1.0\nprotein_{seq_hash} {seq}\n//\n"
                )
                (cache_dir / "pdb_seqres.sto").write_text(
                    f"# STOCKHOLM 1.0\nprotein_{seq_hash} {seq}\n//\n"
                )

        monkeypatch.setattr(subprocess, "run", _fake_run_with_race)

        if model_name == "boltz2":
            prot, _ = _run_boltz2(msa_boltz2, tmp_path / model_name, ref_db, seq, "race")
        else:
            prot, _ = _run_esmfold2(msa_esmfold2, tmp_path / model_name, ref_db, seq, "race")

        assert prot["msa"] == str(cache_dir / "combined.a3m")
        assert (cache_dir / "combined.a3m").is_file()
        assert (cache_dir / "pdb_seqres.sto").is_file()
        assert not any(p.name.endswith(".tmp") for p in cache_dir.iterdir())
        assert list((nfs_mount / "shared_msas_tmp").iterdir()) == []

    def test_of3_core_mode_fallback_never_pollutes_shared_cache(self, tmp_path, monkeypatch):
        """OF3 core-mode fallback stays in of3_msas_cache; Boltz-2/ESMFold2 still emit msa: empty."""
        msa_of3, msa_boltz2, msa_esmfold2 = _load_msa_funcs(monkeypatch)
        nfs_mount, ref_db = _setup_nfs(tmp_path, include_dbs=False)
        seq = "ACDEFGHIK"
        called: list[list[str]] = []
        monkeypatch.setattr(subprocess, "run", lambda cmd, check=True: called.append(cmd))

        of3_chain, _ = _run_of3(msa_of3, tmp_path, ref_db, seq, "core", use_templates=True)
        assert called == []
        assert "of3_msas_cache" in of3_chain["main_msa_file_paths"][0]
        assert list((nfs_mount / "shared_msas_cache").iterdir()) == []

        boltz_prot, _ = _run_boltz2(msa_boltz2, tmp_path, ref_db, seq, "core")
        esm_prot, _ = _run_esmfold2(msa_esmfold2, tmp_path, ref_db, seq, "core")
        assert called == []
        assert boltz_prot["msa"] == "empty"
        assert esm_prot["msa"] == "empty"
        assert list((nfs_mount / "shared_msas_cache").iterdir()) == []
        assert list((nfs_mount / "shared_msas_tmp").iterdir()) == []

    def test_unknown_model_format_raises_value_error(self, tmp_path, monkeypatch):
        """Shared MSA component rejects unsupported model_format values."""
        msa_of3, _, _ = _load_msa_funcs(monkeypatch)
        _, ref_db = _setup_nfs(tmp_path)
        in_path = tmp_path / "in.json"
        in_path.write_text("{}")
        out_path = tmp_path / "out.json"
        with pytest.raises(ValueError, match="Unsupported model_format"):
            msa_of3(
                query_json=_make_artifact(path=str(in_path), uri=str(in_path)),
                ref_databases=ref_db,
                updated_query_json=_make_artifact(path=str(out_path), uri=str(out_path)),
                model_format="unknown_model",
            )

    def test_factory_preserves_distinct_images_and_kfp_install_across_models(
        self, tmp_path, monkeypatch
    ):
        """Compiling OF3, Boltz-2, and ESMFold2 in sequence binds each model's image and model_format."""
        from kfp import compiler

        monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
        monkeypatch.setenv("GCP_REGION", "us-central1")
        monkeypatch.setenv("GCS_BUCKET_NAME", "test-bucket")
        monkeypatch.setenv("FILESTORE_ID", "test-nfs")
        monkeypatch.setenv("NFS_SERVER", "10.1.0.2")
        monkeypatch.setenv("NFS_PATH", "/datasets")
        monkeypatch.setenv("NFS_MOUNT_POINT", "/mnt/nfs/foldrun")
        monkeypatch.setenv("NETWORK", "projects/123/global/networks/test-net")
        monkeypatch.setenv("OPENFOLD3_COMPONENTS_IMAGE", "of3-img:v1")
        monkeypatch.setenv("BOLTZ2_COMPONENTS_IMAGE", "boltz2-img:v1")
        monkeypatch.setenv("ESMFOLD2_COMPONENTS_IMAGE", "esmfold2-img:v1")

        from foldrun_app.models.boltz2.utils.pipeline_utils import (
            load_vertex_pipeline as load_boltz2,
        )
        from foldrun_app.models.esmfold2.utils.pipeline_utils import (
            load_vertex_pipeline as load_esmfold2,
        )
        from foldrun_app.models.of3.utils.pipeline_utils import load_vertex_pipeline as load_of3

        specs = {}
        for name, pipe_fn in (
            ("of3", load_of3(enable_flex_start=False)),
            ("boltz2", load_boltz2(enable_flex_start=False)),
            ("esmfold2", load_esmfold2(enable_flex_start=False, use_msa=True)),
        ):
            out_yaml = tmp_path / f"{name}_compiled.yaml"
            compiler.Compiler().compile(pipeline_func=pipe_fn, package_path=str(out_yaml))
            specs[name] = yaml.safe_load(out_yaml.read_text(encoding="utf-8"))

        for name, expected_img, expect_no_pip in (
            ("of3", "of3-img:v1", False),
            ("boltz2", "boltz2-img:v1", True),
            ("esmfold2", "esmfold2-img:v1", True),
        ):
            execs = specs[name]["deploymentSpec"]["executors"]
            assert "exec-msa-pipeline" in execs
            wps = specs[name]["components"]["comp-msa-pipeline"]["inputDefinitions"]["parameters"][
                "worker_pool_specs"
            ]["defaultValue"]
            inner_spec = wps[0]["container_spec"]
            assert inner_spec["image_uri"] == expected_img
            cmd_joined = " ".join(inner_spec["command"])
            if expect_no_pip:
                assert "pip install" not in cmd_joined
            else:
                assert "pip install" in cmd_joined
            task_params = specs[name]["root"]["dag"]["tasks"]["msa-pipeline"]["inputs"][
                "parameters"
            ]
            assert task_params["model_format"]["runtimeValue"]["constant"] == name

    def test_lowercase_sequence_normalizes_query_row_dedup_and_cache_key(
        self, tmp_path, monkeypatch
    ):
        """Lowercase/mixed-case inputs hash canonically, emit uppercase A3M query row, and dedup exact hits."""
        msa_of3, msa_boltz2, msa_esmfold2 = _load_msa_funcs(monkeypatch)
        nfs_mount, ref_db = _setup_nfs(tmp_path)
        upper_seq = "ACDEFGHIK"
        seq_hash = hashlib.sha256(upper_seq.encode()).hexdigest()
        called_cmds: list[list[str]] = []

        def _fake_run(cmd, check=True):
            called_cmds.append(cmd)
            out_path = cmd[cmd.index("-A") + 1]
            with open(out_path, "w") as f:
                f.write(
                    "# STOCKHOLM 1.0\n"
                    f"protein_{seq_hash} {upper_seq}\n"
                    f"exact_dup {upper_seq}\n"
                    "homolog_1 ACDEFGHIL\n"
                    "#=GC RF xxxxxxxxx\n"
                    "//\n"
                )

        monkeypatch.setattr(subprocess, "run", _fake_run)

        # Lowercase Boltz-2 input writes uppercase query row, deduplicates exact_dup, and warms canonical key
        b2_prot, _ = _run_boltz2(msa_boltz2, tmp_path, ref_db, "acdefghik", "lower_b2")
        assert len(called_cmds) == 2
        a3m_text = open(b2_prot["msa"]).read()
        assert a3m_text == f">protein_{seq_hash}\n{upper_seq}\n>homolog_1\nACDEFGHIL\n"
        assert "exact_dup" not in a3m_text

        # Mixed-case ESMFold2 and uppercase OF3 both hit the exact same canonical cache entry (0 calls)
        esm_prot, _ = _run_esmfold2(msa_esmfold2, tmp_path, ref_db, "AcDeFgHiK", "mixed_esm")
        of3_chain, _ = _run_of3(msa_of3, tmp_path, ref_db, upper_seq, "upper_of3", False)
        assert len(called_cmds) == 2
        assert esm_prot["msa"] == b2_prot["msa"]
        assert of3_chain["main_msa_file_paths"] == [
            str(nfs_mount / "shared_msas_cache" / f"protein_{seq_hash}" / "uniref90_hits.sto"),
            str(nfs_mount / "shared_msas_cache" / f"protein_{seq_hash}" / "mgnify_hits.sto"),
        ]

    def test_null_sequence_is_skipped_without_invoking_hmmer(self, tmp_path, monkeypatch):
        """Explicit JSON/YAML null sequence does not coerce to literal 'None' or invoke jackhmmer."""
        msa_of3, msa_boltz2, msa_esmfold2 = _load_msa_funcs(monkeypatch)
        nfs_mount, ref_db = _setup_nfs(tmp_path)
        called: list[list[str]] = []
        monkeypatch.setattr(subprocess, "run", lambda cmd, check=True: called.append(cmd))

        # OF3 with sequence: null
        of3_in = tmp_path / "of3_null.json"
        of3_out = tmp_path / "of3_null_out.json"
        of3_in.write_text(
            json.dumps(
                {
                    "queries": {
                        "q": {
                            "chains": [
                                {"molecule_type": "protein", "chain_ids": ["A"], "sequence": None}
                            ]
                        }
                    }
                }
            )
        )
        msa_of3(
            query_json=_make_artifact(path=str(of3_in), uri=str(of3_in)),
            ref_databases=ref_db,
            updated_query_json=_make_artifact(path=str(of3_out), uri=str(of3_out)),
            model_format="of3",
            use_templates=True,
        )

        # Boltz-2 with sequence: null
        b2_in = tmp_path / "b2_null.yaml"
        b2_out = tmp_path / "b2_null_out.yaml"
        b2_in.write_text(
            yaml.dump({"version": 1, "sequences": [{"protein": {"id": "A", "sequence": None}}]})
        )
        msa_boltz2(
            query_json=_make_artifact(path=str(b2_in), uri=str(b2_in)),
            ref_databases=ref_db,
            updated_query_json=_make_artifact(path=str(b2_out), uri=str(b2_out)),
            model_format="boltz2",
            use_templates=False,
        )

        # ESMFold2 with sequence: null
        esm_in = tmp_path / "esm_null.json"
        esm_out = tmp_path / "esm_null_out.json"
        esm_in.write_text(json.dumps({"sequences": [{"protein": {"id": "A", "sequence": None}}]}))
        msa_esmfold2(
            query_json=_make_artifact(path=str(esm_in), uri=str(esm_in)),
            ref_databases=ref_db,
            updated_query_json=_make_artifact(path=str(esm_out), uri=str(esm_out)),
            model_format="esmfold2",
            use_templates=False,
        )

        assert called == []
        assert list((nfs_mount / "shared_msas_cache").iterdir()) == []

    def test_empty_db_metadata_falls_back_to_core_mode_without_running_hmmer(
        self, tmp_path, monkeypatch
    ):
        """Empty uniref90/mgnify/pdb_seqres metadata must not match mount_path directory."""
        msa_of3, msa_boltz2, msa_esmfold2 = _load_msa_funcs(monkeypatch)
        nfs_mount = tmp_path / "nfs_empty_meta"
        nfs_mount.mkdir()
        ref_db = _make_artifact(
            path=str(nfs_mount),
            uri=str(nfs_mount),
            metadata={"uniref90": "", "mgnify": "", "pdb_seqres": "", "rfam": "", "rnacentral": ""},
        )
        called: list[list[str]] = []
        monkeypatch.setattr(subprocess, "run", lambda cmd, check=True: called.append(cmd))

        of3_chain, _ = _run_of3(msa_of3, tmp_path, ref_db, "ACDEFGHIK", "empty_meta", True)
        b2_prot, _ = _run_boltz2(msa_boltz2, tmp_path, ref_db, "ACDEFGHIK", "empty_meta")
        esm_prot, _ = _run_esmfold2(msa_esmfold2, tmp_path, ref_db, "ACDEFGHIK", "empty_meta")

        assert called == []
        assert "of3_msas_cache" in of3_chain["main_msa_file_paths"][0]
        assert "template_alignment_file_path" not in of3_chain
        assert b2_prot["msa"] == "empty"
        assert esm_prot["msa"] == "empty"
        assert list((nfs_mount / "shared_msas_cache").iterdir()) == []

    @pytest.mark.parametrize("model_name", ["boltz2", "esmfold2"])
    def test_whitespace_padded_custom_msa_path_is_normalized_in_output(
        self, tmp_path, monkeypatch, model_name
    ):
        """Pre-populated msa paths with surrounding whitespace are written back normalized."""
        _, msa_boltz2, msa_esmfold2 = _load_msa_funcs(monkeypatch)
        nfs_mount, ref_db = _setup_nfs(tmp_path / f"ws_{model_name}")
        custom_a3m = nfs_mount / "custom_chain.a3m"
        custom_a3m.write_text(">q\nACDEFGHIK\n")
        padded = f"  {custom_a3m}\n"

        called: list[list[str]] = []
        monkeypatch.setattr(subprocess, "run", lambda cmd, check=True: called.append(cmd))

        if model_name == "boltz2":
            in_path = tmp_path / "b2_ws.yaml"
            out_path = tmp_path / "b2_ws_out.yaml"
            in_path.write_text(
                yaml.dump(
                    {
                        "version": 1,
                        "sequences": [
                            {"protein": {"id": "A", "sequence": "ACDEFGHIK", "msa": padded}}
                        ],
                    }
                )
            )
            msa_boltz2(
                query_json=_make_artifact(path=str(in_path), uri=str(in_path)),
                ref_databases=ref_db,
                updated_query_json=_make_artifact(path=str(out_path), uri=str(out_path)),
                model_format="boltz2",
                use_templates=False,
            )
            result_msa = yaml.safe_load(out_path.read_text())["sequences"][0]["protein"]["msa"]
        else:
            in_path = tmp_path / "esm_ws.json"
            out_path = tmp_path / "esm_ws_out.json"
            in_path.write_text(
                json.dumps(
                    {
                        "sequences": [
                            {"protein": {"id": "A", "sequence": "ACDEFGHIK", "msa": padded}}
                        ]
                    }
                )
            )
            msa_esmfold2(
                query_json=_make_artifact(path=str(in_path), uri=str(in_path)),
                ref_databases=ref_db,
                updated_query_json=_make_artifact(path=str(out_path), uri=str(out_path)),
                model_format="esmfold2",
                use_templates=False,
            )
            result_msa = json.loads(out_path.read_text())["sequences"][0]["protein"]["msa"]

        assert called == []
        assert result_msa == os.path.realpath(str(custom_a3m))

    def test_normalized_sequence_persisted_into_updated_query_json(self, tmp_path, monkeypatch):
        """Lowercase/whitespace sequences are written back normalized across all 3 models (fresh & pre-populated)."""
        msa_of3, msa_boltz2, msa_esmfold2 = _load_msa_funcs(monkeypatch)
        nfs_mount, ref_db = _setup_nfs(tmp_path / "norm_seq")
        raw_seq = "  acdefghik \n"
        canonical_seq = "ACDEFGHIK"
        custom_a3m = nfs_mount / "custom_norm.a3m"
        custom_a3m.write_text(f">q\n{canonical_seq}\n")

        monkeypatch.setattr(
            subprocess, "run", lambda cmd, check=True: _write_sto_for_cmd(cmd, canonical_seq)
        )

        # Fresh runs across all 3 models
        of3_chain, _ = _run_of3(msa_of3, tmp_path, ref_db, raw_seq, "of3_fresh", False)
        b2_prot, _ = _run_boltz2(msa_boltz2, tmp_path, ref_db, raw_seq, "b2_fresh")
        esm_prot, _ = _run_esmfold2(msa_esmfold2, tmp_path, ref_db, raw_seq, "esm_fresh")
        assert of3_chain["sequence"] == canonical_seq
        assert b2_prot["sequence"] == canonical_seq
        assert esm_prot["sequence"] == canonical_seq

        # Pre-populated MSA paths across OF3, Boltz-2 (empty & custom), and ESMFold2 (custom)
        of3_in = tmp_path / "of3_pre.json"
        of3_out = tmp_path / "of3_pre_out.json"
        of3_in.write_text(
            json.dumps(
                {
                    "queries": {
                        "q": {
                            "chains": [
                                {
                                    "molecule_type": "protein",
                                    "chain_ids": ["A"],
                                    "sequence": raw_seq,
                                    "main_msa_file_paths": [str(custom_a3m)],
                                }
                            ]
                        }
                    }
                }
            )
        )
        msa_of3(
            query_json=_make_artifact(path=str(of3_in), uri=str(of3_in)),
            ref_databases=ref_db,
            updated_query_json=_make_artifact(path=str(of3_out), uri=str(of3_out)),
            model_format="of3",
            use_templates=False,
        )
        assert (
            json.loads(of3_out.read_text())["queries"]["q"]["chains"][0]["sequence"]
            == canonical_seq
        )

        b2_in = tmp_path / "b2_pre.yaml"
        b2_out = tmp_path / "b2_pre_out.yaml"
        b2_in.write_text(
            yaml.dump(
                {
                    "version": 1,
                    "sequences": [
                        {"protein": {"id": "A", "sequence": raw_seq, "msa": "empty"}},
                        {"protein": {"id": "B", "sequence": raw_seq, "msa": str(custom_a3m)}},
                    ],
                }
            )
        )
        msa_boltz2(
            query_json=_make_artifact(path=str(b2_in), uri=str(b2_in)),
            ref_databases=ref_db,
            updated_query_json=_make_artifact(path=str(b2_out), uri=str(b2_out)),
            model_format="boltz2",
            use_templates=False,
        )
        b2_seqs = yaml.safe_load(b2_out.read_text())["sequences"]
        assert b2_seqs[0]["protein"]["sequence"] == canonical_seq
        assert b2_seqs[1]["protein"]["sequence"] == canonical_seq

        esm_in = tmp_path / "esm_pre.json"
        esm_out = tmp_path / "esm_pre_out.json"
        esm_in.write_text(
            json.dumps(
                {
                    "sequences": [
                        {"protein": {"id": "A", "sequence": raw_seq, "msa": str(custom_a3m)}}
                    ]
                }
            )
        )
        msa_esmfold2(
            query_json=_make_artifact(path=str(esm_in), uri=str(esm_in)),
            ref_databases=ref_db,
            updated_query_json=_make_artifact(path=str(esm_out), uri=str(esm_out)),
            model_format="esmfold2",
            use_templates=False,
        )
        assert (
            json.loads(esm_out.read_text())["sequences"][0]["protein"]["sequence"] == canonical_seq
        )

    def test_trailing_hmmer_job_caps_cpus_at_min_when_first_db_smaller(self, tmp_path, monkeypatch):
        """When mgnify < uniref90 on disk, trailing pdb_seqres gets min(cpu0, cpu1) so active threads <= total."""
        msa_of3, _, _ = _load_msa_funcs(monkeypatch)
        monkeypatch.setattr(os, "cpu_count", lambda: 8)
        _, ref_db = _setup_nfs(tmp_path / "inverted_sizes")
        seq = "ACDEFGHIK"
        real_getsize = os.path.getsize

        def _fake_getsize(path: str) -> int:
            if path.endswith("mgnify.fasta"):
                return 10_000_000  # smaller -> 2 CPUs
            if path.endswith("uniref90.fasta"):
                return 30_000_000  # larger -> 6 CPUs
            return real_getsize(path)

        monkeypatch.setattr(os.path, "getsize", _fake_getsize)
        cmds: list[list[str]] = []
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, check=True: (cmds.append(cmd), _write_sto_for_cmd(cmd, seq)),
        )
        _run_of3(msa_of3, tmp_path / "inverted_sizes", ref_db, seq, "inv", use_templates=True)
        cpu_by_db = {os.path.basename(cmd[-1]): int(cmd[cmd.index("--cpu") + 1]) for cmd in cmds}
        assert cpu_by_db == {"mgnify.fasta": 2, "uniref90.fasta": 6, "pdb_seqres.fasta": 2}
        assert cpu_by_db["uniref90.fasta"] + cpu_by_db["pdb_seqres.fasta"] <= 8

    @pytest.mark.parametrize("failing_db", ["mgnify.fasta", "uniref90.fasta"])
    def test_failed_hmmer_cancels_queued_pdb_seqres_job(self, tmp_path, monkeypatch, failing_db):
        """When either mgnify or uniref90 fails in a 3-job OF3 run, queued pdb_seqres is not started."""
        msa_of3, _, _ = _load_msa_funcs(monkeypatch)
        nfs_mount, ref_db = _setup_nfs(tmp_path / f"cancel_{failing_db}")
        seq = "ACDEFGHIK"
        barrier = threading.Barrier(2, timeout=2.0)
        failed_started = threading.Event()
        invoked_dbs: list[str] = []

        def _fake_run(cmd, check=True):
            db = os.path.basename(cmd[-1])
            invoked_dbs.append(db)
            if db in ("mgnify.fasta", "uniref90.fasta"):
                barrier.wait()
            if db == failing_db:
                failed_started.set()
                raise subprocess.CalledProcessError(1, cmd)
            # Keep the surviving worker active briefly while the failure propagates
            failed_started.wait(timeout=2.0)
            threading.Event().wait(0.05)
            _write_sto_for_cmd(cmd, seq)

        monkeypatch.setattr(subprocess, "run", _fake_run)

        with pytest.raises(subprocess.CalledProcessError):
            _run_of3(msa_of3, tmp_path / f"cancel_{failing_db}", ref_db, seq, "fail", True)

        assert "pdb_seqres.fasta" not in invoked_dbs
        assert list((nfs_mount / "shared_msas_tmp").iterdir()) == []
        assert list((nfs_mount / "shared_msas_cache").iterdir()) == []

    def test_lazy_a3m_skips_mgnify_sto_when_uniref90_fills_cap(self, tmp_path, monkeypatch):
        """When uniref90_hits.sto alone fills MAX_COMBINED_A3M_SEQS (16,384), mgnify_hits.sto is not opened."""
        _, msa_boltz2, _ = _load_msa_funcs(monkeypatch)
        _, ref_db = _setup_nfs(tmp_path / "lazy_skip")
        seq = "ACDEFGHIK"
        seq_hash = hashlib.sha256(seq.encode()).hexdigest()

        def _write_deep_uniref(cmd, check=True):
            out_sto = cmd[cmd.index("-A") + 1]
            if out_sto.endswith("uniref90_hits.sto"):
                lines = ["# STOCKHOLM 1.0\n", f"protein_{seq_hash} {seq}\n"]
                alphabet = "ACDEFGHIKLMNPQRSTVWY"
                for idx in range(16500):
                    a = alphabet[(idx // 8000) % 20]
                    b = alphabet[(idx // 400) % 20]
                    c = alphabet[(idx // 20) % 20]
                    d = alphabet[idx % 20]
                    lines.append(f"UniRef_{idx} {a}{b}{c}{d}FGHIK\n")
                lines.append("#=GC RF xxxxxxxxx\n//\n")
                with open(out_sto, "w") as f:
                    f.writelines(lines)
            else:
                # Write a sentinel invalid file that would fail or be caught if opened
                with open(out_sto, "w") as f:
                    f.write("# STOCKHOLM 1.0\n//\n")

        monkeypatch.setattr(subprocess, "run", _write_deep_uniref)

        real_open = open
        opened_mgnify_sto = False

        def _tracking_open(file, mode="r", *args, **kwargs):
            nonlocal opened_mgnify_sto
            if "r" in mode and str(file).endswith("mgnify_hits.sto"):
                opened_mgnify_sto = True
            return real_open(file, mode, *args, **kwargs)

        monkeypatch.setattr("builtins.open", _tracking_open)
        prot_out, _ = _run_boltz2(msa_boltz2, tmp_path / "lazy_skip", ref_db, seq, "lazy")

        assert not opened_mgnify_sto
        headers = [ln for ln in open(prot_out["msa"]).readlines() if ln.startswith(">")]
        assert len(headers) == 16384
