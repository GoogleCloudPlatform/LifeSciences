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

"""Shared KFP v2 CPU MSA component factory for OpenFold 3, Boltz-2, and ESMFold2."""

__all__ = ["create_msa_pipeline_component"]


def create_msa_pipeline_component(
    base_image: str,
    *,
    install_kfp_package: bool = False,
):
    """Build a self-contained KFP MSA component bound to ``base_image``.

    All runtime imports and helpers live inside ``msa_pipeline`` so KFP v2 can
    serialize the function body with zero closure or ``foldrun_app`` runtime
    dependencies. Invoked dynamically on module load/reload so env-overridden
    component images inside ``model_pipeline_load_context`` are always honored.
    """
    from kfp import dsl
    from kfp.dsl import Artifact, Input, Output

    @dsl.component(base_image=base_image, install_kfp_package=install_kfp_package)
    def msa_pipeline(
        query_json: Input[Artifact],
        ref_databases: Input[Artifact],
        updated_query_json: Output[Artifact],
        model_format: str = "of3",
        use_templates: bool = True,
    ):
        """Runs CPU HMMER MSA searches and populates the cross-model NFS cache.

        Supports three query formats via ``model_format``:
        - ``"of3"``: OpenFold 3 InferenceQuerySet JSON (protein Jackhmmer + optional
          ``pdb_seqres`` templates + RNA ``nhmmer`` against Rfam/RNAcentral).
        - ``"boltz2"``: Boltz-2 YAML (protein Jackhmmer → validated ``combined.a3m``).
        - ``"esmfold2"``: ESMFold2 JSON (protein Jackhmmer → validated ``combined.a3m``).

        Shared protein MSA cache layout on NFS:
          shared_msas_cache/protein_<sha256>/uniref90_hits.sto  ← canonical UniRef90 Stockholm
          shared_msas_cache/protein_<sha256>/mgnify_hits.sto    ← canonical MGnify Stockholm
          shared_msas_cache/protein_<sha256>/combined.a3m       ← validated deduplicated A3M
          shared_msas_cache/protein_<sha256>/pdb_seqres.sto     ← optional OF3 template hits
          shared_msas_tmp/protein_<sha256>_<run_id>/            ← staging dir (atomic rename/merge)
        """
        import hashlib
        import json
        import logging
        import os
        import shutil
        import subprocess
        import threading
        import time
        import uuid
        from concurrent.futures import ThreadPoolExecutor, as_completed

        logging.basicConfig(level=logging.INFO)
        if model_format not in ("of3", "boltz2", "esmfold2"):
            raise ValueError(
                f"Unsupported model_format {model_format!r}; expected 'of3', 'boltz2', or 'esmfold2'."
            )

        model_label = {"of3": "OF3", "boltz2": "BOLTZ2", "esmfold2": "ESMFold2"}[model_format]
        logging.info(f"Starting {model_label} MSA pipeline")
        t0 = time.time()

        MAX_COMBINED_A3M_SEQS = 16384
        total_cpus = os.cpu_count() or 8
        mount_path = ref_databases.uri

        uniref90_rel = ref_databases.metadata.get("uniref90") or ""
        mgnify_rel = ref_databases.metadata.get("mgnify") or ""
        uniref90_path = os.path.join(mount_path, uniref90_rel) if uniref90_rel else ""
        mgnify_path = os.path.join(mount_path, mgnify_rel) if mgnify_rel else ""
        pdb_seqres_rel = ref_databases.metadata.get("pdb_seqres") or ""
        pdb_seqres_path = os.path.join(mount_path, pdb_seqres_rel) if pdb_seqres_rel else ""
        rfam_rel = ref_databases.metadata.get("rfam") or ""
        rnacentral_rel = ref_databases.metadata.get("rnacentral") or ""
        rfam_path = os.path.join(mount_path, rfam_rel) if rfam_rel else ""
        rnacentral_path = os.path.join(mount_path, rnacentral_rel) if rnacentral_rel else ""

        # Shared cross-model cache & staging dirs on NFS
        nfs_msa_cache_base = os.path.join(mount_path, "shared_msas_cache")
        nfs_msa_tmp_base = os.path.join(mount_path, "shared_msas_tmp")
        os.makedirs(nfs_msa_cache_base, exist_ok=True)
        os.makedirs(nfs_msa_tmp_base, exist_ok=True)

        run_id = str(uuid.uuid4())[:12]

        def _resolve_hmmer_cpus(jobs: list[tuple[str, list[str], float]]) -> list[int]:
            """Allocate vCPUs across HMMER jobs proportional to database size.

            Jobs are ordered largest-first and run with ``max_workers = min(2, len(jobs))``.
            A single job uses all ``total_cpus``; two or more jobs split ``total_cpus``
            across the initial concurrent pair proportional to file size (falling back to
            nominal weights when files are small test stubs), guaranteeing ``>= 1`` vCPU
            per concurrent job and ``cpu0 + cpu1 == total_cpus``. Any queued trailing job
            (e.g. ``pdb_seqres``) inherits ``cpu1`` so peak active threads never exceed
            ``total_cpus`` while ``jobs[0]`` is still running.
            """
            if not jobs:
                return []
            if len(jobs) == 1:
                return [max(1, total_cpus)]
            if total_cpus <= 1:
                return [1] * len(jobs)

            min_real_db_bytes = 1_048_576  # 1 MiB threshold vs tiny unit-test stubs
            try:
                sizes = [os.path.getsize(cmd[-1]) for _, cmd, _ in jobs[:2]]
            except OSError:
                sizes = [0, 0]

            if sizes[0] >= min_real_db_bytes and sizes[1] >= min_real_db_bytes:
                w0, w1 = float(sizes[0]), float(sizes[1])
            else:
                w0 = max(0.01, float(jobs[0][2]))
                w1 = max(0.01, float(jobs[1][2]))

            cpu0 = round(total_cpus * w0 / (w0 + w1))
            cpu0 = max(1, min(total_cpus - 1, cpu0))
            cpu1 = total_cpus - cpu0
            trailing_cpus = min(cpu0, cpu1)
            return [cpu0, cpu1] + [trailing_cpus] * (len(jobs) - 2)

        def _run_hmmer_parallel(jobs: list[tuple[str, list[str], float]]) -> None:
            """Run independent HMMER searches concurrently with size-weighted CPU splitting."""
            if not jobs:
                return
            max_workers = min(2, len(jobs))
            alloc_cpus = _resolve_hmmer_cpus(jobs)

            def _exec(job: tuple[str, list[str], float], job_cpus: int) -> None:
                label, cmd_template, _ = job
                cpu_str = str(job_cpus)
                cmd = [cpu_str if arg == "{CPU}" else arg for arg in cmd_template]
                logging.info(f"Running {label} (--cpu {cpu_str})")
                job_t0 = time.time()
                subprocess.run(cmd, check=True)
                logging.info(f"Completed {label} (--cpu {cpu_str}) in {time.time() - job_t0:.1f}s")

            if max_workers == 1:
                _exec(jobs[0], alloc_cpus[0])
                return

            failed = threading.Event()

            def _exec_checked(job: tuple[str, list[str], float], job_cpus: int) -> None:
                if failed.is_set():
                    return
                try:
                    _exec(job, job_cpus)
                except BaseException:
                    failed.set()
                    raise

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = [
                    pool.submit(_exec_checked, job, job_cpus)
                    for job, job_cpus in zip(jobs, alloc_cpus, strict=True)
                ]
                try:
                    for fut in as_completed(futures):
                        fut.result()
                except BaseException:
                    failed.set()
                    for f in futures:
                        f.cancel()
                    raise

        def _promote_cache_dir(seq_dir: str, seq_cache_dir: str) -> None:
            """Atomically rename seq_dir to seq_cache_dir, or merge missing files atomically."""
            try:
                os.rename(seq_dir, seq_cache_dir)
                logging.info(f"Cached MSAs at {seq_cache_dir}")
            except (FileExistsError, OSError):
                logging.info(
                    f"Cache already populated at {seq_cache_dir}; atomically merging newly generated files."
                )
                os.makedirs(seq_cache_dir, exist_ok=True)
                try:
                    for fname in os.listdir(seq_dir):
                        src_file = os.path.join(seq_dir, fname)
                        dst_file = os.path.join(seq_cache_dir, fname)
                        if os.path.isfile(src_file) and not os.path.exists(dst_file):
                            tmp_dst = f"{dst_file}.{run_id}.tmp"
                            try:
                                shutil.copy2(src_file, tmp_dst)
                                os.replace(tmp_dst, dst_file)
                            except BaseException:
                                if os.path.exists(tmp_dst):
                                    os.remove(tmp_dst)
                                raise
                finally:
                    shutil.rmtree(seq_dir, ignore_errors=True)

        def _sto_to_a3m_rows(sto_path: str, expected_len: int):
            """Yield validated A3M (header, seq) rows lazily from a Jackhmmer Stockholm file."""
            seqs: dict[str, list[str]] = {}
            order: list[str] = []
            rf_chunks: list[str] = []
            with open(sto_path) as f:
                for line in f:
                    line = line.rstrip()
                    if not line or line == "//":
                        continue
                    if line.startswith("#=GC RF"):
                        parts = line.split()
                        if len(parts) >= 3:
                            rf_chunks.append(parts[2])
                        continue
                    if line.startswith("#"):
                        continue
                    parts = line.split(None, 1)
                    if len(parts) == 2:
                        name, seq = parts
                        if name not in seqs:
                            seqs[name] = []
                            order.append(name)
                        seqs[name].append(seq)

            if not order:
                return

            rf_str = "".join(rf_chunks)
            col_ref = rf_str if rf_str else "".join(seqs[order[0]])
            is_match = tuple(c not in (".", "-") for c in col_ref)
            if sum(is_match) != expected_len:
                return

            for idx, name in enumerate(order):
                # Skip query row (idx == 0); caller prepends canonical query row once
                if idx == 0:
                    continue
                full_seq = "".join(seqs[name])
                if len(full_seq) != len(is_match):
                    continue
                a3m_chars: list[str] = []
                for match_col, c in zip(is_match, full_seq):
                    if match_col:
                        a3m_chars.append("-" if c in ("-", ".") else c.upper())
                    elif c not in ("-", "."):
                        a3m_chars.append(c.lower())
                yield name, "".join(a3m_chars)

        def _build_combined_a3m(
            uniref90_sto: str,
            mgnify_sto: str,
            out_a3m: str,
            seq_hash: str,
            sequence: str,
        ) -> tuple[int, int, int]:
            """Build deduplicated, match-length-validated combined.a3m from UniRef90 + MGnify .sto."""
            query_seq = sequence.upper()
            seen_seqs = {query_seq}
            uniref_added = 0
            mgnify_added = 0
            with open(out_a3m, "w") as fout:
                fout.write(f">protein_{seq_hash}\n{query_seq}\n")
                for hdr, a3m_seq in _sto_to_a3m_rows(uniref90_sto, len(query_seq)):
                    if a3m_seq in seen_seqs:
                        continue
                    seen_seqs.add(a3m_seq)
                    fout.write(f">{hdr}\n{a3m_seq}\n")
                    uniref_added += 1
                    if len(seen_seqs) >= MAX_COMBINED_A3M_SEQS:
                        break
                if len(seen_seqs) < MAX_COMBINED_A3M_SEQS:
                    for hdr, a3m_seq in _sto_to_a3m_rows(mgnify_sto, len(query_seq)):
                        if a3m_seq in seen_seqs:
                            continue
                        seen_seqs.add(a3m_seq)
                        fout.write(f">{hdr}\n{a3m_seq}\n")
                        mgnify_added += 1
                        if len(seen_seqs) >= MAX_COMBINED_A3M_SEQS:
                            break
            return len(seen_seqs), uniref_added, mgnify_added

        def _ensure_shared_protein_msa(
            seq_key: str,
            seq_hash: str,
            sequence: str,
            *,
            need_msa: bool,
            need_tpl: bool,
            build_a3m: bool,
        ) -> str:
            """Ensure required protein MSA, optional templates, and optional A3M exist in shared cache."""
            seq_cache_dir = os.path.join(nfs_msa_cache_base, f"protein_{seq_hash}")
            cached_a3m = os.path.join(seq_cache_dir, "combined.a3m")
            cached_uniref90 = os.path.join(seq_cache_dir, "uniref90_hits.sto")
            cached_mgnify = os.path.join(seq_cache_dir, "mgnify_hits.sto")

            if not need_msa and not need_tpl:
                if build_a3m and not os.path.exists(cached_a3m):
                    logging.info(
                        f"Cache hit (.sto) for chain {seq_key} (protein_{seq_hash}). "
                        "Deriving combined.a3m from shared Stockholm alignments."
                    )
                    tmp_a3m = f"{cached_a3m}.{run_id}.tmp"
                    try:
                        n_unique, n_uniref, n_mgnify = _build_combined_a3m(
                            cached_uniref90, cached_mgnify, tmp_a3m, seq_hash, sequence
                        )
                        os.replace(tmp_a3m, cached_a3m)
                    except BaseException:
                        if os.path.exists(tmp_a3m):
                            os.remove(tmp_a3m)
                        raise
                    logging.info(
                        f"Derived combined.a3m for chain {seq_key} with {n_unique} unique sequences "
                        f"(uniref90={n_uniref}, mgnify={n_mgnify})"
                    )
                else:
                    logging.info(
                        f"Cache hit for chain {seq_key} (protein_{seq_hash}). Reusing shared MSAs."
                    )
                return seq_cache_dir

            if not need_msa and need_tpl:
                logging.info(
                    f"Partial cache hit for chain {seq_key} (protein_{seq_hash}). "
                    "Reusing cached protein MSA; running pdb_seqres template search only."
                )
            else:
                logging.info(
                    f"Cache miss for chain {seq_key} (protein_{seq_hash}). Generating MSAs."
                )

            seq_dir = os.path.join(nfs_msa_tmp_base, f"protein_{seq_hash}_{run_id}")
            os.makedirs(seq_dir, exist_ok=True)
            tmp_fasta = os.path.join(seq_dir, "query.fasta")
            with open(tmp_fasta, "w") as f:
                f.write(f">protein_{seq_hash}\n{sequence}\n")

            uniref90_sto = os.path.join(seq_dir, "uniref90_hits.sto")
            mgnify_sto = os.path.join(seq_dir, "mgnify_hits.sto")
            combined_a3m = os.path.join(seq_dir, "combined.a3m")

            # Order largest DBs first so max_workers=2 keeps all vCPUs busy and hides
            # the fast pdb_seqres scan behind whichever of mgnify/uniref90 finishes first.
            # Filenames must use "_hits" suffix — OF3's MSASettings.max_seq_counts filters
            # by basename and expects "uniref90_hits.sto" and "mgnify_hits.sto".
            jobs: list[tuple[str, list[str], float]] = []
            if need_msa:
                jobs.append(
                    (
                        "jackhmmer mgnify",
                        [
                            "jackhmmer",
                            "--noali",
                            "-o",
                            os.devnull,
                            "-N",
                            "1",
                            "--cpu",
                            "{CPU}",
                            "-E",
                            "0.0001",
                            "--incE",
                            "0.0001",
                            "-A",
                            mgnify_sto,
                            tmp_fasta,
                            mgnify_path,
                        ],
                        2.0,
                    )
                )
                jobs.append(
                    (
                        "jackhmmer uniref90",
                        [
                            "jackhmmer",
                            "--noali",
                            "-o",
                            os.devnull,
                            "-N",
                            "1",
                            "--cpu",
                            "{CPU}",
                            "-E",
                            "0.0001",
                            "--incE",
                            "0.0001",
                            "-A",
                            uniref90_sto,
                            tmp_fasta,
                            uniref90_path,
                        ],
                        1.0,
                    )
                )
            if need_tpl:
                jobs.append(
                    (
                        "jackhmmer pdb_seqres",
                        [
                            "jackhmmer",
                            "--noali",
                            "-o",
                            os.devnull,
                            "-N",
                            "1",
                            "--cpu",
                            "{CPU}",
                            "-A",
                            os.path.join(seq_dir, "pdb_seqres.sto"),
                            tmp_fasta,
                            pdb_seqres_path,
                        ],
                        1.0,
                    )
                )

            try:
                _run_hmmer_parallel(jobs)
                if build_a3m:
                    n_unique, n_uniref, n_mgnify = _build_combined_a3m(
                        uniref90_sto, mgnify_sto, combined_a3m, seq_hash, sequence
                    )
                    logging.info(
                        f"Built combined.a3m for chain {seq_key} with {n_unique} unique sequences "
                        f"(uniref90={n_uniref}, mgnify={n_mgnify})"
                    )
            except BaseException:
                shutil.rmtree(seq_dir, ignore_errors=True)
                raise

            # Remove query.fasta only; retain uniref90_hits.sto, mgnify_hits.sto, and optional
            # combined.a3m / pdb_seqres.sto for cross-model reuse across OF3, Boltz-2, and ESMFold2.
            try:
                os.remove(tmp_fasta)
            except OSError:
                pass

            _promote_cache_dir(seq_dir, seq_cache_dir)
            logging.info(f"Protein MSA complete for {seq_key}")
            return seq_cache_dir

        if model_format == "of3":
            with open(query_json.path) as f:
                query_data = json.load(f)

            of3_local_cache_base = os.path.join(mount_path, "of3_msas_cache")
            of3_local_tmp_base = os.path.join(mount_path, "of3_msas_tmp")
            os.makedirs(of3_local_cache_base, exist_ok=True)
            os.makedirs(of3_local_tmp_base, exist_ok=True)

            num_msa_chains = 0
            num_template_chains = 0

            for query_name, query_entry in query_data.get("queries", {}).items():
                for chain_idx, chain in enumerate(query_entry.get("chains", [])):
                    seq_type = chain.get("molecule_type", "protein")
                    chain_ids = chain.get("chain_ids", [])
                    if isinstance(chain_ids, str):
                        chain_ids = [chain_ids]
                    chain_id = chain_ids[0] if chain_ids else f"chain_{chain_idx}"
                    seq_key = f"{query_name}_{chain_id}"

                    if seq_type not in ("protein", "rna"):
                        logging.info(f"Skipping MSA for {seq_type} chain {seq_key}")
                        continue

                    raw_seq = chain.get("sequence")
                    sequence = str(raw_seq).strip().upper() if raw_seq is not None else ""
                    if not sequence:
                        logging.warning(f"Empty sequence for chain {seq_key}, skipping MSA")
                        continue
                    chain["sequence"] = sequence

                    custom_msa_paths = (
                        list(chain["main_msa_file_paths"])
                        if chain.get("main_msa_file_paths")
                        else None
                    )
                    custom_template_path = (
                        chain["template_alignment_file_path"]
                        if chain.get("template_alignment_file_path")
                        else None
                    )
                    if custom_msa_paths and (
                        custom_template_path is not None
                        or not use_templates
                        or seq_type != "protein"
                    ):
                        logging.info(
                            f"Preserving pre-populated MSA/template paths for chain {seq_key} (skipping HMMER)"
                        )
                        num_msa_chains += 1
                        if custom_template_path is not None:
                            num_template_chains += 1
                        continue

                    seq_hash = hashlib.sha256(sequence.encode()).hexdigest()

                    if seq_type == "protein":
                        has_full_protein_dbs = (
                            bool(uniref90_path)
                            and os.path.isfile(uniref90_path)
                            and bool(mgnify_path)
                            and os.path.isfile(mgnify_path)
                        )
                        has_pdb_seqres = bool(pdb_seqres_path) and os.path.isfile(pdb_seqres_path)
                        shared_seq_cache_dir = os.path.join(
                            nfs_msa_cache_base, f"protein_{seq_hash}"
                        )
                        shared_has_full_msa = os.path.exists(
                            os.path.join(shared_seq_cache_dir, "uniref90_hits.sto")
                        ) and os.path.exists(os.path.join(shared_seq_cache_dir, "mgnify_hits.sto"))

                        # Core mode: isolate single-sequence fallback under of3_msas_cache
                        if (
                            custom_msa_paths is None
                            and not has_full_protein_dbs
                            and not shared_has_full_msa
                        ):
                            seq_cache_dir = os.path.join(
                                of3_local_cache_base, f"protein_{seq_hash}"
                            )
                            core_sto = os.path.join(seq_cache_dir, "uniref90_hits.sto")
                            need_core_sto = not os.path.exists(core_sto)
                            need_core_tpl = False
                            if use_templates and custom_template_path is None:
                                if has_pdb_seqres:
                                    need_core_tpl = not os.path.exists(
                                        os.path.join(seq_cache_dir, "pdb_seqres.sto")
                                    )
                                else:
                                    logging.warning(
                                        f"pdb_seqres not found at {pdb_seqres_path}; "
                                        f"skipping template search for chain {seq_key}. "
                                        f"Run database setup with --models of3 to download it."
                                    )

                            if not need_core_sto and not need_core_tpl:
                                logging.info(
                                    f"Cache hit (core mode) for chain {seq_key} (protein_{seq_hash}). Reusing MSA."
                                )
                            else:
                                seq_dir = os.path.join(
                                    of3_local_tmp_base, f"protein_{seq_hash}_{run_id}"
                                )
                                os.makedirs(seq_dir, exist_ok=True)
                                tmp_fasta = os.path.join(seq_dir, "query.fasta")
                                try:
                                    if need_core_sto:
                                        logging.warning(
                                            f"Reference sequence databases not found on NFS "
                                            f"({uniref90_path}, {mgnify_path}); "
                                            f"falling back to single-sequence Stockholm MSA for "
                                            f"protein_{seq_hash} (core mode)."
                                        )
                                        with open(
                                            os.path.join(seq_dir, "uniref90_hits.sto"), "w"
                                        ) as fout:
                                            fout.write(
                                                f"# STOCKHOLM 1.0\nprotein_{seq_hash} {sequence}\n//\n"
                                            )
                                    if need_core_tpl:
                                        with open(tmp_fasta, "w") as f:
                                            f.write(f">protein_{seq_hash}\n{sequence}\n")
                                        _run_hmmer_parallel(
                                            [
                                                (
                                                    "jackhmmer pdb_seqres",
                                                    [
                                                        "jackhmmer",
                                                        "--noali",
                                                        "-o",
                                                        os.devnull,
                                                        "-N",
                                                        "1",
                                                        "--cpu",
                                                        "{CPU}",
                                                        "-A",
                                                        os.path.join(seq_dir, "pdb_seqres.sto"),
                                                        tmp_fasta,
                                                        pdb_seqres_path,
                                                    ],
                                                    1.0,
                                                )
                                            ]
                                        )
                                except BaseException:
                                    shutil.rmtree(seq_dir, ignore_errors=True)
                                    raise
                                try:
                                    os.remove(tmp_fasta)
                                except OSError:
                                    pass
                                _promote_cache_dir(seq_dir, seq_cache_dir)

                            chain["main_msa_file_paths"] = [core_sto]
                            num_msa_chains += 1
                            if custom_template_path is not None:
                                chain["template_alignment_file_path"] = custom_template_path
                                num_template_chains += 1
                            elif use_templates and os.path.exists(
                                os.path.join(seq_cache_dir, "pdb_seqres.sto")
                            ):
                                chain["template_alignment_file_path"] = os.path.join(
                                    seq_cache_dir, "pdb_seqres.sto"
                                )
                                num_template_chains += 1
                            continue

                        need_msa = (custom_msa_paths is None) and not shared_has_full_msa
                        need_tpl = False
                        if use_templates and custom_template_path is None:
                            if has_pdb_seqres:
                                need_tpl = not os.path.exists(
                                    os.path.join(shared_seq_cache_dir, "pdb_seqres.sto")
                                )
                            else:
                                logging.warning(
                                    f"pdb_seqres not found at {pdb_seqres_path}; "
                                    f"skipping template search for chain {seq_key}. "
                                    f"Run database setup with --models of3 to download it."
                                )

                        seq_cache_dir = _ensure_shared_protein_msa(
                            seq_key,
                            seq_hash,
                            sequence,
                            need_msa=need_msa,
                            need_tpl=need_tpl,
                            build_a3m=False,
                        )

                        if custom_msa_paths is not None:
                            chain["main_msa_file_paths"] = custom_msa_paths
                        else:
                            chain["main_msa_file_paths"] = [
                                os.path.join(seq_cache_dir, f)
                                for f in ("uniref90_hits.sto", "mgnify_hits.sto")
                                if os.path.exists(os.path.join(seq_cache_dir, f))
                            ]
                        num_msa_chains += 1

                        if custom_template_path is not None:
                            chain["template_alignment_file_path"] = custom_template_path
                            num_template_chains += 1
                        elif use_templates and os.path.exists(
                            os.path.join(seq_cache_dir, "pdb_seqres.sto")
                        ):
                            chain["template_alignment_file_path"] = os.path.join(
                                seq_cache_dir, "pdb_seqres.sto"
                            )
                            num_template_chains += 1

                    elif seq_type == "rna":
                        seq_cache_dir = os.path.join(of3_local_cache_base, f"rna_{seq_hash}")
                        rfam_avail = bool(rfam_path and os.path.isfile(rfam_path))
                        rnacentral_avail = bool(rnacentral_path and os.path.isfile(rnacentral_path))

                        if not rfam_avail and not rnacentral_avail:
                            logging.info(
                                f"No RNA reference databases found for {seq_key}; skipping cache promotion"
                            )
                            continue

                        need_rfam = rfam_avail and not os.path.exists(
                            os.path.join(seq_cache_dir, "rfam_hits.sto")
                        )
                        need_rnacentral = rnacentral_avail and not os.path.exists(
                            os.path.join(seq_cache_dir, "rnacentral_hits.sto")
                        )

                        if not need_rfam and not need_rnacentral:
                            logging.info(
                                f"Cache hit for chain {seq_key} (rna_{seq_hash}). Reusing RNA MSAs."
                            )
                        else:
                            logging.info(
                                f"Cache miss for chain {seq_key} (rna_{seq_hash}). Generating RNA MSAs."
                            )
                            seq_dir = os.path.join(of3_local_tmp_base, f"rna_{seq_hash}_{run_id}")
                            os.makedirs(seq_dir, exist_ok=True)
                            tmp_fasta = os.path.join(seq_dir, "query.fasta")
                            with open(tmp_fasta, "w") as f:
                                f.write(f">rna_{seq_hash}\n{sequence}\n")

                            rna_jobs: list[tuple[str, list[str], float]] = []
                            if need_rnacentral:
                                rna_jobs.append(
                                    (
                                        "nhmmer rnacentral",
                                        [
                                            "nhmmer",
                                            "--noali",
                                            "-o",
                                            os.devnull,
                                            "--cpu",
                                            "{CPU}",
                                            "-A",
                                            os.path.join(seq_dir, "rnacentral_hits.sto"),
                                            tmp_fasta,
                                            rnacentral_path,
                                        ],
                                        7.0,
                                    )
                                )
                            if need_rfam:
                                rna_jobs.append(
                                    (
                                        "nhmmer rfam",
                                        [
                                            "nhmmer",
                                            "--noali",
                                            "-o",
                                            os.devnull,
                                            "--cpu",
                                            "{CPU}",
                                            "-A",
                                            os.path.join(seq_dir, "rfam_hits.sto"),
                                            tmp_fasta,
                                            rfam_path,
                                        ],
                                        1.0,
                                    )
                                )

                            try:
                                _run_hmmer_parallel(rna_jobs)
                            except BaseException:
                                shutil.rmtree(seq_dir, ignore_errors=True)
                                raise

                            try:
                                os.remove(tmp_fasta)
                            except OSError:
                                pass

                            _promote_cache_dir(seq_dir, seq_cache_dir)
                            logging.info(f"RNA MSA complete for {seq_key}")

                        cached_rna_paths = [
                            os.path.join(seq_cache_dir, f)
                            for f in ("rfam_hits.sto", "rnacentral_hits.sto")
                            if os.path.exists(os.path.join(seq_cache_dir, f))
                        ]
                        if cached_rna_paths:
                            chain["main_msa_file_paths"] = cached_rna_paths
                            num_msa_chains += 1

            updated_query_json.uri = f"{updated_query_json.uri}.json"
            with open(updated_query_json.path, "w") as f:
                json.dump(query_data, f, indent=2)

            updated_query_json.metadata["category"] = "updated_query_json"
            updated_query_json.metadata["msa_chains"] = num_msa_chains
            updated_query_json.metadata["template_chains"] = num_template_chains
            updated_query_json.metadata["nfs_msa_cache_base"] = nfs_msa_cache_base
            updated_query_json.metadata["use_templates"] = use_templates

            t1 = time.time()
            logging.info(
                f"OF3 MSA pipeline completed. "
                f"MSA chains: {num_msa_chains}, Template chains: {num_template_chains}. "
                f"Elapsed time: {t1 - t0:.1f}s"
            )
            return

        # Boltz-2 (YAML) and ESMFold2 (JSON) A3M protein MSA mode
        if model_format == "boltz2":
            import yaml

            with open(query_json.path) as f:
                query_data = yaml.safe_load(f)
        else:
            with open(query_json.path) as f:
                query_data = json.load(f)

        num_msa_sequences = 0

        for i, seq_entry in enumerate(query_data.get("sequences", [])):
            if "protein" not in seq_entry:
                continue

            prot_data = seq_entry["protein"]
            seq_id = prot_data.get("id", f"seq_{i}")
            if isinstance(seq_id, list):
                seq_id = seq_id[0]

            raw_seq = prot_data.get("sequence")
            sequence = str(raw_seq).strip().upper() if raw_seq is not None else ""
            if sequence:
                prot_data["sequence"] = sequence

            existing_msa = prot_data.get("msa")
            if existing_msa is not None and str(existing_msa).strip() != "":
                normalized_msa = str(existing_msa).strip()
                if normalized_msa.lower() == "empty":
                    prot_data["msa"] = "empty"
                    logging.info(f"Preserving single-sequence MSA (empty) for chain {seq_id}")
                    num_msa_sequences += 1
                    continue
                real_mount = os.path.realpath(mount_path)
                real_msa = os.path.realpath(normalized_msa)
                if os.path.commonpath([real_mount, real_msa]) == real_mount and os.path.isfile(
                    real_msa
                ):
                    prot_data["msa"] = real_msa
                    logging.info(
                        f"Preserving pre-populated MSA for chain {seq_id}: {real_msa} (skipping jackhmmer)"
                    )
                    num_msa_sequences += 1
                    continue
                logging.warning(
                    f"Ignoring invalid or missing pre-populated MSA path for chain {seq_id}: {existing_msa!r}"
                )
                prot_data.pop("msa", None)

            if not sequence:
                logging.warning(f"Empty sequence for chain {seq_id}, skipping MSA")
                continue

            seq_hash = hashlib.sha256(sequence.encode()).hexdigest()
            seq_cache_dir = os.path.join(nfs_msa_cache_base, f"protein_{seq_hash}")
            cached_a3m = os.path.join(seq_cache_dir, "combined.a3m")
            has_cached_a3m = os.path.exists(cached_a3m)
            has_cached_sto = os.path.exists(
                os.path.join(seq_cache_dir, "uniref90_hits.sto")
            ) and os.path.exists(os.path.join(seq_cache_dir, "mgnify_hits.sto"))

            if not has_cached_a3m and not has_cached_sto:
                if not (
                    bool(uniref90_path)
                    and os.path.isfile(uniref90_path)
                    and bool(mgnify_path)
                    and os.path.isfile(mgnify_path)
                ):
                    logging.warning(
                        f"Reference sequence databases not found on NFS ({uniref90_path}, {mgnify_path}); "
                        f"falling back to single-sequence mode (msa: empty) for chain {seq_id} (core mode)."
                    )
                    prot_data["msa"] = "empty"
                    continue
                _ensure_shared_protein_msa(
                    seq_id,
                    seq_hash,
                    sequence,
                    need_msa=True,
                    need_tpl=False,
                    build_a3m=True,
                )
            else:
                _ensure_shared_protein_msa(
                    seq_id,
                    seq_hash,
                    sequence,
                    need_msa=False,
                    need_tpl=False,
                    build_a3m=True,
                )

            if os.path.exists(cached_a3m):
                prot_data["msa"] = cached_a3m
                num_msa_sequences += 1

        if model_format == "boltz2":
            import yaml

            updated_query_json.uri = f"{updated_query_json.uri}.yaml"
            with open(updated_query_json.path, "w") as f:
                yaml.dump(query_data, f)
        else:
            updated_query_json.uri = f"{updated_query_json.uri}.json"
            with open(updated_query_json.path, "w") as f:
                json.dump(query_data, f, indent=2)

        updated_query_json.metadata["category"] = "updated_query_json"
        updated_query_json.metadata["msa_sequences"] = num_msa_sequences
        updated_query_json.metadata["nfs_msa_cache_base"] = nfs_msa_cache_base

        t1 = time.time()
        logging.info(
            f"{model_label} MSA pipeline completed. "
            f"MSA sequences: {num_msa_sequences}. "
            f"Elapsed time: {t1 - t0:.1f}s"
        )

    return msa_pipeline
