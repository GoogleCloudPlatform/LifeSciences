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

"""KFP component that runs RFD4-Proteina generative protein and binder design."""

import config as config
from kfp import dsl
from kfp.dsl import Artifact, Input, Output


@dsl.component(base_image=config.RFD4_PROTEINA_COMPONENTS_IMAGE)
def predict_rfd4_proteina(
    spec_json: Input[Artifact],
    seed_value: int,
    design_mode: str,
    num_designs_per_seed: int,
    nfs_cache_path: str,
    predicted_structure: Output[Artifact],
    confidence_json: Output[Artifact],
    target_structure: Input[Artifact] = None,
    extra_cli_args: list = None,  # noqa: RUF013
):
    """Runs RFD4-Proteina generative design for a single seed.

    Executes latent diffusion sampling, normalizes ranked CIFs and confidence JSONs into
    predictions/ output directory.
    """
    import csv
    import json
    import logging
    import math
    import os
    import re
    import shutil
    import subprocess
    import time

    extra_cli_args = list(extra_cli_args or [])

    logging.info(
        f"Starting RFD4-Proteina prediction (seed={seed_value}, mode={design_mode}, "
        f"num_designs={num_designs_per_seed})"
    )
    t0 = time.time()

    # Output paths
    output_dir = os.path.dirname(predicted_structure.path)
    os.makedirs(output_dir, exist_ok=True)
    predictions_dir = os.path.join(output_dir, "predictions")
    os.makedirs(predictions_dir, exist_ok=True)

    predicted_structure.uri = f"{predicted_structure.uri}.cif"
    confidence_json.uri = f"{confidence_json.uri}.json"

    # Environment variables & CUDA Driver Safeguard
    os.environ["HF_HOME"] = nfs_cache_path
    os.environ["HF_HUB_CACHE"] = (
        os.path.join(nfs_cache_path, "hub")
        if os.path.isdir(os.path.join(nfs_cache_path, "hub"))
        else nfs_cache_path
    )
    os.environ["RFD4_CACHE"] = nfs_cache_path
    os.environ["DATA_PATH"] = nfs_cache_path

    # Check CUDA driver version and export fallbacks if custom C++/CUDA kernels cannot be loaded
    try:
        nvidia_smi_out = subprocess.run(["nvidia-smi"], capture_output=True, text=True)
        if nvidia_smi_out.returncode != 0:
            logging.warning("nvidia-smi not available or failed; enabling custom kernel fallbacks")
            os.environ["PROTEINA_DISABLE_CUSTOM_KERNELS"] = "1"
            os.environ["CUEQUIVARIANCE_OPS_FALLBACK"] = "1"
        else:
            # If on standard cloud VM, proactively enable safe PyTorch fallbacks to avoid driver mismatch
            os.environ["PROTEINA_DISABLE_CUSTOM_KERNELS"] = "1"
            os.environ["CUEQUIVARIANCE_OPS_FALLBACK"] = "1"
    except Exception as e:
        logging.warning(f"Error checking CUDA environment ({e}); setting fallback flags")
        os.environ["PROTEINA_DISABLE_CUSTOM_KERNELS"] = "1"
        os.environ["CUEQUIVARIANCE_OPS_FALLBACK"] = "1"

    # Copy spec to local work directory
    spec_local = os.path.join(output_dir, "spec.json")
    shutil.copy2(spec_json.path, spec_local)

    # Read spec
    with open(spec_local) as f:
        try:
            spec_data = json.load(f)
        except Exception:
            spec_data = {}

    spec_compute_metrics = bool(
        spec_data.pop("compute_metrics", False) if isinstance(spec_data, dict) else False
    )

    # Stage target structure if provided
    target_local_path = None
    if target_structure and target_structure.path and os.path.exists(target_structure.path):
        target_uri = getattr(target_structure, "uri", "") or ""
        target_basename = (
            os.path.basename(target_uri.rstrip("/"))
            if target_uri
            else os.path.basename(target_structure.path)
        )
        if target_basename.lower().endswith((".cif", ".pdb", ".bcif")):
            target_local_path = os.path.join(output_dir, target_basename)
            shutil.copy2(target_structure.path, target_local_path)
            default_target_cif = os.path.join(output_dir, "target.cif")
            if not os.path.exists(default_target_cif):
                shutil.copy2(target_structure.path, default_target_cif)

    rfd4_out = os.path.join(output_dir, "rfd4_out")
    os.makedirs(rfd4_out, exist_ok=True)

    inputs_arg: str
    if spec_data.get("benchmark_input"):
        rel_bench = str(spec_data["benchmark_input"]).strip().lstrip("/")
        if ".." in rel_bench.replace("\\", "/").split("/"):
            raise ValueError(
                f"Path traversal forbidden in benchmark_input: {spec_data['benchmark_input']!r}"
            )
        base_opt = os.path.realpath("/opt/rfd4-proteina")
        opt_bench = os.path.realpath(os.path.join(base_opt, rel_bench))
        if opt_bench != base_opt and not opt_bench.startswith(base_opt + os.sep):
            raise ValueError(
                f"benchmark_input escapes /opt/rfd4-proteina: {spec_data['benchmark_input']!r}"
            )
        if os.path.exists(opt_bench):
            resolved_bench = opt_bench
        else:
            try:
                import pathlib

                import rfproteina

                pkg_root = pathlib.Path(rfproteina.__file__).parent.resolve()
                cand_bench = (pkg_root / rel_bench).resolve()
                if not str(cand_bench).startswith(str(pkg_root) + os.sep):
                    raise ValueError(
                        f"benchmark_input escapes rfproteina package dir: {rel_bench!r}"
                    )
                resolved_bench = str(cand_bench)
            except ValueError:
                raise
            except Exception as exc:
                raise FileNotFoundError(
                    f"Benchmark input '{rel_bench}' not found under /opt/rfd4-proteina"
                ) from exc
        inputs_arg = str(resolved_bench)
    elif spec_data.get("raw_input_cif") and target_local_path:
        inputs_arg = target_local_path
    else:
        # Convert FoldRun spec_data into an rfd4-generate YAML specification (design.yaml)
        import re

        import yaml

        design_yaml_path = os.path.join(output_dir, "design.yaml")
        length_str = str(spec_data.get("binder_length_range", "80..120"))
        len_match = re.search(r"(\d+)\s*(?:\.\.|\-)\s*(\d+)", length_str)
        if len_match:
            binder_contig = f"{len_match.group(1)}-{len_match.group(2)}"
        else:
            clean_len = re.sub(r"[^\d]", "", length_str) or "100"
            binder_contig = f"{clean_len}-{clean_len}"

        target_chains = spec_data.get("target_chains") or ["A"]
        primary_chain = target_chains[0]
        target_span = f"{primary_chain}1-200"
        auth_to_label = {}
        if target_local_path and os.path.exists(target_local_path):
            try:
                import gemmi

                if target_local_path.lower().endswith(".cif"):
                    doc = gemmi.cif.read_file(target_local_path)
                    block = doc.sole_block()
                    auth_chains = list(block.find_values("_atom_site.auth_asym_id"))
                    auth_seqs = list(block.find_values("_atom_site.auth_seq_id"))
                    label_seqs = list(block.find_values("_atom_site.label_seq_id"))
                    valid_labels = set()
                    for ac, aseq, lseq in zip(auth_chains, auth_seqs, label_seqs):
                        if ac == primary_chain and lseq not in (".", "?"):
                            auth_to_label[str(aseq)] = str(lseq)
                            if str(lseq).isdigit():
                                valid_labels.add(int(lseq))
                    if valid_labels:
                        target_span = f"{primary_chain}{min(valid_labels)}-{max(valid_labels)}"
                        logging.info(
                            f"Resolved CIF target_span for chain {primary_chain} using label_seq_id: {target_span}"
                        )
                if not auth_to_label:
                    st = gemmi.read_structure(target_local_path)
                    for model in st:
                        for ch in model:
                            if ch.name == primary_chain:
                                polymer = ch.get_polymer()
                                seq_nums = [
                                    res.seqid.num
                                    for res in (polymer if len(polymer) > 0 else ch)
                                    if res.seqid.num is not None
                                ]
                                if seq_nums:
                                    target_span = f"{primary_chain}{min(seq_nums)}-{max(seq_nums)}"
                                break
                        break
            except Exception as e:
                logging.warning(f"Could not inspect target residue span via gemmi: {e}")

        if "design_mode" not in spec_data and any(
            isinstance(v, dict) and ("contig" in v or "input" in v or "length" in v)
            for v in spec_data.values()
        ):
            # User supplied native rfd4-generate YAML structure directly
            rfd4_yaml_data = {k: v for k, v in spec_data.items() if isinstance(v, dict)}
            staged_gcs_inputs = {}
            for task_cfg in rfd4_yaml_data.values():
                if not isinstance(task_cfg, dict):
                    continue
                if "length" in task_cfg and "contig" not in task_cfg:
                    task_cfg["contig"] = str(task_cfg.pop("length")).replace("..", "-")
                if "input" in task_cfg:
                    raw_task_in = str(task_cfg["input"]).strip()
                    if raw_task_in.startswith("gs://"):
                        if raw_task_in not in staged_gcs_inputs:
                            from google.cloud import storage as _gcs

                            bkt_name, blb_name = raw_task_in[5:].split("/", 1)
                            local_in_name = (
                                f"task_{len(staged_gcs_inputs)}_{os.path.basename(blb_name)}"
                            )
                            local_in_path = os.path.join(output_dir, local_in_name)
                            _gcs.Client().bucket(bkt_name).blob(blb_name).download_to_filename(
                                local_in_path
                            )
                            staged_gcs_inputs[raw_task_in] = local_in_path
                        task_cfg["input"] = staged_gcs_inputs[raw_task_in]
                    elif target_local_path:
                        task_cfg["input"] = target_local_path
                if "hotspots" in task_cfg and "conditions" not in task_cfg:
                    raw_hot = task_cfg.pop("hotspots")
                    hot_items = (
                        [x.strip() for x in str(raw_hot).split(",") if x.strip()]
                        if isinstance(raw_hot, str)
                        else list(raw_hot or [])
                    )
                    conditions = {
                        "C_CRDGRP": {
                            1: [{"select": f"sel('{primary_chain}/*/*/[N,CA,C,O]')"}],
                        },
                        "C_SEQ": {
                            True: [{"select": f"sel('{primary_chain}')"}],
                        },
                    }
                    if hot_items:
                        hotspot_selects = []
                        for res_item in hot_items:
                            res_s = str(res_item).strip()
                            ch_id = primary_chain
                            res_num = res_s
                            if ":" in res_s:
                                ch_id, res_num = res_s.split(":", 1)
                            elif res_s and res_s[0].isalpha():
                                ch_id, res_num = res_s[0], res_s[1:]
                            mapped_num = auth_to_label.get(
                                str(res_num).strip(), str(res_num).strip()
                            )
                            hotspot_selects.append(
                                {"select": f"sel('{ch_id}/*/{mapped_num}/[CA,CB]')"}
                            )
                        conditions["C_HOT"] = {True: hotspot_selects}
                    task_cfg["conditions"] = conditions
        else:
            if design_mode == "unconditional_monomer":
                rfd4_yaml_data = {
                    "monomer_design": {
                        "contig": binder_contig,
                    }
                }
            elif design_mode == "motif_scaffolding":
                contig_val = (
                    spec_data.get("scaffold_motif") or f"{binder_contig}, /0, {target_span}"
                )
                rfd4_yaml_data = {
                    "motif_scaffolding": {
                        "input": target_local_path
                        or spec_data.get("target_structure", "target.cif"),
                        "contig": contig_val,
                    }
                }
            else:
                conditions = {
                    "C_CRDGRP": {
                        1: [{"select": f"sel('{primary_chain}/*/*/[N,CA,C,O]')"}],
                    },
                    "C_SEQ": {
                        True: [{"select": f"sel('{primary_chain}')"}],
                    },
                }
                binding_res = spec_data.get("binding_residues") or []
                if binding_res:
                    hotspot_selects = []
                    for res_item in binding_res:
                        res_s = str(res_item).strip()
                        ch_id = primary_chain
                        res_num = res_s
                        if ":" in res_s:
                            ch_id, res_num = res_s.split(":", 1)
                        elif res_s and res_s[0].isalpha():
                            ch_id, res_num = res_s[0], res_s[1:]
                        mapped_num = auth_to_label.get(str(res_num).strip(), str(res_num).strip())
                        hotspot_selects.append({"select": f"sel('{ch_id}/*/{mapped_num}/[CA,CB]')"})
                    conditions["C_HOT"] = {True: hotspot_selects}

                rfd4_yaml_data = {
                    "binder_design": {
                        "input": target_local_path
                        or spec_data.get("target_structure", "target.cif"),
                        "contig": f"{binder_contig}, /0, {target_span}",
                        "conditions": conditions,
                    }
                }

        with open(design_yaml_path, "w") as yf:
            yaml.safe_dump(rfd4_yaml_data, yf, sort_keys=False)
        inputs_arg = design_yaml_path

    ckpt_arg = (
        nfs_cache_path
        if (
            nfs_cache_path
            and (
                os.path.isfile(nfs_cache_path)
                or os.path.isfile(os.path.join(nfs_cache_path, "model.safetensors"))
                or os.path.isfile(os.path.join(nfs_cache_path, "release-checkpoints.json"))
            )
        )
        else "nvidia/RFD4-Proteina"
    )

    # Explicit fast-mode (~metrics) vs self-consistency metrics control.
    # Default is explicit fast mode (compute_metrics=False -> passes ~metrics and emits None for uncomputed scores).
    compute_metrics = spec_compute_metrics
    filtered_hydra_args = []
    for arg in extra_cli_args:
        arg_str = str(arg).strip()
        if not arg_str:
            continue
        low = arg_str.lower()
        if low in ("--metrics", "compute_metrics=true", "metrics=true"):
            compute_metrics = True
            continue
        if low in ("--fast-no-metrics", "compute_metrics=false", "metrics=false", "~metrics"):
            compute_metrics = False
            continue
        # Block dangerous Hydra instantiation / interpolation directives in worker as defense-in-depth
        if "_target_" in arg_str or "${" in arg_str or arg_str.startswith("-"):
            logging.warning(f"Skipping disallowed non-Hydra or unsafe override: {arg_str}")
            continue
        if "=" in arg_str or arg_str.startswith("~"):
            filtered_hydra_args.append(arg_str)

    metrics_mode = "self_consistency" if compute_metrics else "fast_no_self_consistency"
    logging.info(f"RFD4-Proteina metrics_mode={metrics_mode} (compute_metrics={compute_metrics})")

    cmd = [
        "rfd4-generate",
        f"hf_ckpt_path={ckpt_arg}",
        f"inputs={inputs_arg}",
        f"num_replicates={num_designs_per_seed}",
        f"seed={seed_value}",
        f"out_dir={rfd4_out}",
    ]
    if not compute_metrics:
        cmd.append("~metrics")
    cmd.append("compile_lengths=null")

    for arg_str in filtered_hydra_args:
        if arg_str not in cmd:
            cmd.append(arg_str)

    logging.info(f"Running RFD4-Proteina command: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=output_dir, check=True)

    # Locate metrics CSV and ranked CIF structures
    metrics_records = []
    for root, _, files in os.walk(rfd4_out):
        for f in sorted(files):
            if f.endswith(".csv") and (
                "metric" in f or "ranked" in f or "design" in f or "score" in f
            ):
                csv_path = os.path.join(root, f)
                try:
                    with open(csv_path, newline="") as csvfile:
                        reader = csv.DictReader(csvfile)
                        for row in reader:
                            metrics_records.append(row)
                except Exception as ex:
                    logging.warning(f"Failed to read CSV {csv_path}: {ex}")

    # Discover generated CIF/PDB files (including gzip-compressed .cif.gz/.pdb.gz)
    structure_files = []
    for root, _, files in os.walk(rfd4_out):
        for f in sorted(files):
            if f.endswith((".cif", ".pdb", ".cif.gz", ".pdb.gz")):
                structure_files.append(os.path.join(root, f))

    logging.info(
        f"Discovered {len(structure_files)} structures and {len(metrics_records)} metric records"
    )

    def _opt_float(val):
        if val is None:
            return None
        s = str(val).strip()
        if not s:
            return None
        try:
            fval = float(s)
            if math.isnan(fval) or math.isinf(fval):
                return None
            return fval
        except (ValueError, TypeError):
            return None

    best_cif = None
    best_conf_data = None
    best_sort_key = (False, -1e9)

    # Normalize outputs into predictions/
    for i, struct_path in enumerate(structure_files):
        design_name = os.path.splitext(os.path.basename(struct_path.removesuffix(".gz")))[0]
        dest_cif = os.path.join(predictions_dir, f"design_seed{seed_value}_{i}.cif")

        # Convert PDB or compressed structure to uncompressed CIF via gemmi
        if struct_path.endswith((".pdb", ".cif.gz", ".pdb.gz")):
            try:
                import gemmi

                st = gemmi.read_structure(struct_path)
                st.make_mmcif_document().write_file(dest_cif)
            except Exception as conv_err:
                logging.warning(f"gemmi conversion fallback for {struct_path}: {conv_err}")
                if struct_path.endswith(".gz"):
                    import gzip

                    with gzip.open(struct_path, "rb") as gzf, open(dest_cif, "wb") as outf:
                        shutil.copyfileobj(gzf, outf)
                else:
                    shutil.copy2(struct_path, dest_cif)
        else:
            shutil.copy2(struct_path, dest_cif)

        # Sanitize NaN/Inf B-factor tokens strictly for 3D viewer parsing while preserving fixed-width PDB columns
        try:
            with open(dest_cif, encoding="utf-8", errors="ignore") as cf:
                cif_lines = cf.readlines()
            is_mmcif_doc = any(
                ln.lstrip().startswith(("data_", "_atom_site.", "loop_")) for ln in cif_lines[:100]
            )
            sanitized_lines = []
            modified_cif = False
            for line in cif_lines:
                if line.startswith(("ATOM ", "HETATM ")):
                    if is_mmcif_doc:
                        toks = line.split()
                        if any(t.lower() in ("nan", "inf", "-inf", "+inf") for t in toks):
                            toks = [
                                "0.00" if t.lower() in ("nan", "inf", "-inf", "+inf") else t
                                for t in toks
                            ]
                            sanitized_lines.append(" ".join(toks) + "\n")
                            modified_cif = True
                            continue
                    else:
                        new_line = re.sub(
                            r"(?i)(?<=\s)(?:[+-]?nan|[+-]?inf)(?=\s|$)",
                            lambda m: "0.00".rjust(len(m.group(0))),
                            line,
                        )
                        if new_line != line:
                            sanitized_lines.append(new_line)
                            modified_cif = True
                            continue
                sanitized_lines.append(line)
            if modified_cif:
                with open(dest_cif, "w", encoding="utf-8") as cf:
                    cf.writelines(sanitized_lines)
        except Exception as san_err:
            logging.warning(f"Could not sanitize NaN tokens in {dest_cif}: {san_err}")

        # Match metrics (only populated when self-consistency metrics were actually produced)
        matched_metric = {}
        for rec in metrics_records:
            if (
                rec.get("design") == design_name
                or rec.get("id") == design_name
                or rec.get("name") == design_name
            ):
                matched_metric = rec
                break
        if not matched_metric and i < len(metrics_records):
            matched_metric = metrics_records[i]

        plddt_raw = _opt_float(
            matched_metric.get("plddt")
            if matched_metric.get("plddt") is not None
            else matched_metric.get("mean_plddt")
        )
        plddt_val = (
            plddt_raw * 100.0 if (plddt_raw is not None and 0.0 < plddt_raw <= 1.0) else plddt_raw
        )
        iptm_val = _opt_float(
            matched_metric.get("iptm")
            if matched_metric.get("iptm") is not None
            else matched_metric.get("interface_ptm")
        )
        ptm_val = _opt_float(matched_metric.get("ptm"))
        sc_score_val = _opt_float(
            matched_metric.get("sc_score")
            if matched_metric.get("sc_score") is not None
            else (
                matched_metric.get("sc_rmsd")
                if matched_metric.get("sc_rmsd") is not None
                else matched_metric.get("self_consistency")
            )
        )
        clash_val = _opt_float(matched_metric.get("clash_score"))
        ranking_score_val = _opt_float(
            matched_metric.get("ranking_score")
            if matched_metric.get("ranking_score") is not None
            else matched_metric.get("score")
        )
        if ranking_score_val is None and plddt_val is not None and iptm_val is not None:
            ranking_score_val = (plddt_val / 100.0) * 0.5 + iptm_val * 0.5
        elif ranking_score_val is None and plddt_val is not None and ptm_val is not None:
            ranking_score_val = (plddt_val / 100.0) * 0.5 + ptm_val * 0.5

        seq_val = str(
            matched_metric.get("designed_sequence") or matched_metric.get("sequence") or ""
        )
        has_real_metrics = any(
            v is not None for v in (plddt_val, iptm_val, ptm_val, sc_score_val, ranking_score_val)
        )

        conf_data = {
            "design_id": f"design_seed{seed_value}_{i}",
            "seed_value": seed_value,
            "metrics_computed": has_real_metrics,
            "metrics_mode": metrics_mode,
            "plddt": plddt_val,
            "mean_plddt": plddt_val,
            "iptm": iptm_val,
            "ptm": ptm_val,
            "sc_score": sc_score_val,
            "clash_score": clash_val,
            "ranking_score": ranking_score_val,
            "designed_sequence": seq_val,
            "design_mode": design_mode,
        }

        dest_json = os.path.join(predictions_dir, f"confidence_design_seed{seed_value}_{i}.json")
        with open(dest_json, "w") as jf:
            json.dump(conf_data, jf, indent=2)

        cand_key = (
            ranking_score_val is not None,
            ranking_score_val if ranking_score_val is not None else -float(i),
        )
        if best_cif is None or cand_key > best_sort_key:
            best_sort_key = cand_key
            best_cif = dest_cif
            best_conf_data = conf_data

    if best_cif is None or not os.path.exists(best_cif):
        raise FileNotFoundError(
            f"RFD4-Proteina execution completed but produced no structure files (.cif/.pdb) in {rfd4_out}"
        )

    # Copy top design to KFP outputs
    shutil.copy2(best_cif, predicted_structure.path)
    with open(confidence_json.path, "w") as jf:
        json.dump(best_conf_data, jf, indent=2)

    predicted_structure.metadata["category"] = "predicted_structure"
    predicted_structure.metadata["seed_value"] = seed_value
    predicted_structure.metadata["design_mode"] = design_mode
    predicted_structure.metadata["metrics_mode"] = metrics_mode
    best_rank_val = best_conf_data.get("ranking_score") if best_conf_data else None
    if best_rank_val is not None:
        predicted_structure.metadata["ranking_score"] = float(best_rank_val)

    confidence_json.metadata["category"] = "confidence"
    confidence_json.metadata["metrics_mode"] = metrics_mode
    if best_rank_val is not None:
        confidence_json.metadata["ranking_score"] = float(best_rank_val)
    if best_conf_data and best_conf_data.get("sc_score") is not None:
        confidence_json.metadata["sc_score"] = float(best_conf_data["sc_score"])

    t1 = time.time()
    rank_str = f"{best_rank_val:.3f}" if best_rank_val is not None else f"None ({metrics_mode})"
    logging.info(
        f"RFD4-Proteina prediction completed (seed={seed_value}). Best ranking_score={rank_str}. "
        f"Elapsed time: {t1 - t0:.1f}s"
    )
