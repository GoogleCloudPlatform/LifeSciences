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

"""KFP component that runs BOLTZGEN binder design for a single seed.

Each seed runs as a separate GPU task via ParallelFor.
Uses --seed CLI flag to control diffusion and sequence generation randomness.

CLI reference:
    boltzgen check query.yaml
    boltzgen run query.yaml --output PATH --protocol PROTOCOL
                            --num_designs N --budget B --cache PATH --seed S
"""

import config as config
from kfp import dsl
from kfp.dsl import Artifact, Input, Output


@dsl.component(base_image=config.BOLTZGEN_COMPONENTS_IMAGE)
def predict_boltzgen(
    query_yaml: Input[Artifact],
    seed_value: int,
    protocol: str,
    num_designs_per_seed: int,
    budget_per_seed: int,
    nfs_cache_path: str,
    predicted_structure: Output[Artifact],
    confidence_json: Output[Artifact],
    target_structure: Input[Artifact] = None,
    extra_cli_args: list = None,  # noqa: RUF013
):
    """Runs BoltzGen design for a single seed.

    Validates query specification with `boltzgen check`, executes `boltzgen run`,
    and normalizes ranked design CIFs and confidence JSONs into predictions/ output.
    """
    import csv
    import json
    import logging
    import os
    import shutil
    import subprocess
    import time

    logging.info(
        f"Starting BOLTZGEN prediction (seed={seed_value}, protocol={protocol}, "
        f"num_designs={num_designs_per_seed}, budget={budget_per_seed})"
    )
    t0 = time.time()

    # Output paths
    output_dir = os.path.dirname(predicted_structure.path)
    os.makedirs(output_dir, exist_ok=True)
    predictions_dir = os.path.join(output_dir, "predictions")
    os.makedirs(predictions_dir, exist_ok=True)

    predicted_structure.uri = f"{predicted_structure.uri}.cif"
    confidence_json.uri = f"{confidence_json.uri}.json"

    # Set HF, cache, and PyTorch Lightning seed environment variables
    os.environ["HF_HOME"] = nfs_cache_path
    os.environ["BOLTZ_CACHE"] = nfs_cache_path
    os.environ["PL_GLOBAL_SEED"] = str(seed_value)
    os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"

    # Inject sitecustomize.py so subprocesses (boltzgen/resources/main.py) default torch.load(weights_only=False) on PyTorch 2.6+
    site_dir = os.path.join(output_dir, "_py_site")
    os.makedirs(site_dir, exist_ok=True)
    with open(os.path.join(site_dir, "sitecustomize.py"), "w") as scf:
        scf.write(
            "try:\n"
            "    import torch\n"
            "    _orig_torch_load = torch.load\n"
            "    def _patched_torch_load(*args, **kwargs):\n"
            "        kwargs['weights_only'] = False\n"
            "        return _orig_torch_load(*args, **kwargs)\n"
            "    torch.load = _patched_torch_load\n"
            "except Exception:\n"
            "    pass\n"
        )
    existing_pypath = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = f"{site_dir}:{existing_pypath}" if existing_pypath else site_dir

    # Ensure 'python' binary exists on PATH (boltzgen CLI invokes 'python .../resources/main.py')
    if not shutil.which("python") and shutil.which("python3"):
        try:
            os.symlink(shutil.which("python3"), "/usr/local/bin/python")
        except Exception as sym_err:
            logging.warning(f"Could not symlink /usr/local/bin/python -> python3: {sym_err}")

    # Copy query YAML to local work directory
    query_local = os.path.join(output_dir, "query.yaml")
    shutil.copy2(query_yaml.path, query_local)

    # If target structure artifact is provided, stage it locally where query.yaml expects it
    target_dest = None
    if target_structure and target_structure.path and os.path.exists(target_structure.path):
        target_uri = getattr(target_structure, "uri", "") or ""
        target_basename = (
            os.path.basename(target_uri.rstrip("/"))
            if target_uri
            else os.path.basename(target_structure.path)
        )
        if target_basename.lower().endswith((".cif", ".pdb", ".bcif")):
            target_dest = os.path.join(output_dir, target_basename)
            shutil.copy2(target_structure.path, target_dest)
            # Also copy as target.cif if not already named that
            default_target_cif = os.path.join(output_dir, "target.cif")
            if not os.path.exists(default_target_cif):
                shutil.copy2(target_structure.path, default_target_cif)

    # Auto-map auth_seq_id to canonical 1-indexed label_seq_id in query.yaml if needed
    if target_dest and os.path.exists(target_dest):
        try:
            import gemmi
            import yaml

            with open(query_local) as qf:
                qdata = yaml.safe_load(qf)
            doc = gemmi.cif.read_file(target_dest)
            block = doc.sole_block()
            auth_chains = list(block.find_values("_atom_site.auth_asym_id"))
            auth_seqs = list(block.find_values("_atom_site.auth_seq_id"))
            label_seqs = list(block.find_values("_atom_site.label_seq_id"))

            modified = False
            if isinstance(qdata, dict) and isinstance(qdata.get("entities"), list):
                for ent in qdata["entities"]:
                    if isinstance(ent, dict) and isinstance(ent.get("file"), dict):
                        btypes = ent["file"].get("binding_types")
                        if isinstance(btypes, list):
                            for bt in btypes:
                                if isinstance(bt, dict) and isinstance(bt.get("chain"), dict):
                                    ch_id = str(bt["chain"].get("id", "A"))
                                    bval = bt["chain"].get("binding")
                                    if bval is None:
                                        continue
                                    auth_to_label = {}
                                    valid_labels = set()
                                    for ac, aseq, lseq in zip(auth_chains, auth_seqs, label_seqs):
                                        if ac == ch_id and lseq not in (".", "?"):
                                            auth_to_label[str(aseq)] = str(lseq)
                                            if str(lseq).isdigit():
                                                valid_labels.add(int(lseq))
                                    max_label = max(valid_labels) if valid_labels else 0
                                    tokens = [t.strip() for t in str(bval).split(",") if t.strip()]
                                    needs_map = any(
                                        t.isdigit() and int(t) > max_label and t in auth_to_label
                                        for t in tokens
                                    )
                                    if needs_map:
                                        mapped = [auth_to_label.get(t, t) for t in tokens]
                                        bt["chain"]["binding"] = ",".join(mapped)
                                        modified = True
                                        logging.info(
                                            f"Auto-mapped chain {ch_id} binding residues from auth_seq_id {tokens} to label_seq_id {mapped}"
                                        )
            if modified:
                with open(query_local, "w") as qf:
                    yaml.safe_dump(qdata, qf, sort_keys=False)
        except Exception as map_err:
            logging.warning(f"Could not inspect/map query.yaml residues via gemmi: {map_err}")

    # Validate specification with boltzgen check
    logging.info(f"Validating query specification with: boltzgen check {query_local}")
    check_cmd = ["boltzgen", "check", query_local, f"--cache={nfs_cache_path}"]
    try:
        subprocess.run(check_cmd, cwd=output_dir, check=True)
    except Exception as e:
        logging.warning(f"boltzgen check warning (proceeding): {e}")

    # Run BoltzGen design via CLI (boltzgen run rejects --seed CLI flag; propagate seed via
    # PL_GLOBAL_SEED, PYTHONHASHSEED, and sitecustomize.py seeding random/numpy/torch)
    import math as _math

    boltzgen_out = os.path.join(output_dir, "boltzgen_out")
    os.makedirs(boltzgen_out, exist_ok=True)

    shim_dir = os.path.join(output_dir, "_py_shim")
    os.makedirs(shim_dir, exist_ok=True)
    with open(os.path.join(shim_dir, "sitecustomize.py"), "w") as scf:
        scf.write(
            "import os, random\n"
            "try:\n"
            "    _s = int(os.environ.get('PL_GLOBAL_SEED', '0'))\n"
            "    random.seed(_s)\n"
            "    import numpy as _np\n"
            "    _np.random.seed(_s % (2**32 - 1))\n"
            "    import torch as _torch\n"
            "    _torch.manual_seed(_s)\n"
            "    if _torch.cuda.is_available():\n"
            "        _torch.cuda.manual_seed_all(_s)\n"
            "except Exception:\n"
            "    pass\n"
        )
    sub_env = os.environ.copy()
    sub_env["PL_GLOBAL_SEED"] = str(seed_value)
    sub_env["PYTHONHASHSEED"] = str(seed_value)
    existing_pypath = sub_env.get("PYTHONPATH", "")
    sub_env["PYTHONPATH"] = f"{shim_dir}:{existing_pypath}" if existing_pypath else shim_dir

    cmd = [
        "boltzgen",
        "run",
        query_local,
        f"--output={boltzgen_out}",
        f"--protocol={protocol}",
        f"--num_designs={num_designs_per_seed}",
        f"--budget={budget_per_seed}",
        f"--cache={nfs_cache_path}",
    ]
    if extra_cli_args:
        for arg in extra_cli_args:
            arg_str = str(arg)
            if arg_str not in cmd:
                cmd.append(arg_str)

    logging.info(f"Running (seed={seed_value}): {' '.join(cmd)}")
    subprocess.run(cmd, cwd=output_dir, env=sub_env, check=True)

    # Locate metrics CSV and ranked CIF structures
    metrics_records = []
    # Prefer final_designs_metrics_*.csv first, then fall back to other CSVs
    csv_candidates = []
    for root, _, files in os.walk(boltzgen_out):
        for f in sorted(files):
            if f.endswith(".csv") and ("metric" in f or "ranked" in f or "design" in f):
                csv_candidates.append(os.path.join(root, f))
    csv_candidates.sort(
        key=lambda p: (0 if "final_designs_metrics" in os.path.basename(p) else 1, p)
    )
    for csv_path in csv_candidates:
        logging.info(f"Found metrics CSV: {csv_path}")
        try:
            with open(csv_path, newline="") as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    metrics_records.append(row)
        except Exception as ex:
            logging.warning(f"Failed to read CSV {csv_path}: {ex}")

    # Discover generated CIF files: prefer final_ranked_designs/final_*_designs/*.cif,
    # then intermediate_designs_inverse_folded/refold_cif/*.cif, excluding input query.cif
    import glob as _glob
    import re as _re

    cif_files = sorted(
        _glob.glob(os.path.join(boltzgen_out, "final_ranked_designs", "final_*_designs", "*.cif"))
    )
    if not cif_files:
        cif_files = sorted(
            _glob.glob(
                os.path.join(
                    boltzgen_out, "intermediate_designs_inverse_folded", "refold_cif", "*.cif"
                )
            )
        )
    if not cif_files:
        for root, _, files in os.walk(boltzgen_out):
            if os.path.abspath(root) == os.path.abspath(boltzgen_out):
                continue
            for f in sorted(files):
                if f.endswith(".cif") and f != "query.cif":
                    cif_files.append(os.path.join(root, f))

    logging.info(f"Discovered {len(cif_files)} CIF files and {len(metrics_records)} metric records")

    best_cif = None
    best_conf_data = None
    best_sort_key = (False, -1e9)

    def _opt_float(val):
        try:
            if val is None or str(val).strip() == "":
                return None
            fval = float(val)
            if _math.isnan(fval) or _math.isinf(fval):
                return None
            return fval
        except (ValueError, TypeError):
            return None

    def _first_present(rec: dict, *keys: str):
        for k in keys:
            if k in rec and rec[k] is not None and str(rec[k]).strip() != "":
                return rec[k]
        return None

    # Normalize outputs into predictions/
    for i, cif_path in enumerate(cif_files):
        raw_stem = os.path.splitext(os.path.basename(cif_path))[0]
        clean_id = _re.sub(r"^rank\d+_", "", raw_stem)
        dest_cif = os.path.join(predictions_dir, f"design_seed{seed_value}_{i}.cif")
        shutil.copy2(cif_path, dest_cif)

        # Match metrics by id / design / file_name
        matched_metric = {}
        for rec in metrics_records:
            rec_id = str(rec.get("id") or rec.get("design") or rec.get("name") or "").strip()
            rec_fn = os.path.splitext(str(rec.get("file_name") or "").strip())[0]
            if rec_id in (raw_stem, clean_id) or rec_fn in (raw_stem, clean_id):
                matched_metric = rec
                break
        if not matched_metric and i < len(metrics_records):
            matched_metric = metrics_records[i]

        iptm_val = _opt_float(
            _first_present(matched_metric, "iptm", "design_to_target_iptm", "interface_ptm")
        )
        ptm_val = _opt_float(_first_present(matched_metric, "ptm", "design_ptm"))
        # Never substitute design_ptm for plddt
        plddt_raw = _opt_float(
            _first_present(matched_metric, "plddt", "mean_plddt", "design_plddt")
        )
        plddt_val = (
            plddt_raw * 100.0 if (plddt_raw is not None and 0.0 < plddt_raw <= 1.0) else plddt_raw
        )
        # Never substitute higher-is-better quality_score for RMSD sc_score (lower-is-better, Å)
        sc_score_val = _opt_float(
            _first_present(matched_metric, "sc_score", "sc_rmsd", "self_consistency")
        )
        quality_score_val = _opt_float(matched_metric.get("quality_score"))
        clash_val = _opt_float(matched_metric.get("clash_score"))
        ranking_score_val = _opt_float(
            _first_present(matched_metric, "ranking_score", "quality_score", "score")
        )
        if ranking_score_val is None and plddt_val is not None and iptm_val is not None:
            ranking_score_val = (plddt_val / 100.0) * 0.5 + iptm_val * 0.5
        elif ranking_score_val is None and ptm_val is not None and iptm_val is not None:
            ranking_score_val = ptm_val * 0.5 + iptm_val * 0.5
        seq_val = str(_first_present(matched_metric, "designed_sequence", "sequence") or "")

        conf_data = {
            "design_id": f"design_seed{seed_value}_{i}",
            "seed_value": seed_value,
            "plddt": plddt_val,
            "mean_plddt": plddt_val,
            "iptm": iptm_val,
            "ptm": ptm_val,
            "sc_score": sc_score_val,
            "quality_score": quality_score_val,
            "clash_score": clash_val,
            "ranking_score": ranking_score_val,
            "designed_sequence": seq_val,
            "protocol": protocol,
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
            f"BoltzGen execution completed but produced no .cif structure files in {boltzgen_out}"
        )

    # Copy top design to KFP outputs
    shutil.copy2(best_cif, predicted_structure.path)
    if best_conf_data:
        with open(confidence_json.path, "w") as jf:
            json.dump(best_conf_data, jf, indent=2)

    predicted_structure.metadata["category"] = "predicted_structure"
    predicted_structure.metadata["seed_value"] = seed_value
    predicted_structure.metadata["protocol"] = protocol
    best_rank_val = best_conf_data.get("ranking_score") if best_conf_data else None
    if best_rank_val is not None:
        predicted_structure.metadata["ranking_score"] = float(best_rank_val)

    confidence_json.metadata["category"] = "confidence"
    if best_rank_val is not None:
        confidence_json.metadata["ranking_score"] = float(best_rank_val)
    if best_conf_data and best_conf_data.get("sc_score") is not None:
        confidence_json.metadata["sc_score"] = float(best_conf_data["sc_score"])

    t1 = time.time()
    rank_str = f"{best_rank_val:.3f}" if best_rank_val is not None else "N/A"
    logging.info(
        f"BOLTZGEN prediction completed (seed={seed_value}). Best ranking_score={rank_str}. "
        f"Elapsed time: {t1 - t0:.1f}s"
    )
