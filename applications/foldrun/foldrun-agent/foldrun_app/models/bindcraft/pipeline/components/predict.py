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

"""KFP component that runs BindCraft generative binder design for a single seed."""

import config as config
from kfp import dsl
from kfp.dsl import Artifact, Input, Output


@dsl.component(base_image=config.BINDCRAFT_COMPONENTS_IMAGE)
def predict_bindcraft(
    settings_json: Input[Artifact],
    seed_value: int,
    num_designs_per_seed: int,
    af2_params_nfs_path: str,
    mpnn_weights_nfs_path: str,
    predicted_structure: Output[Artifact],
    confidence_json: Output[Artifact],
    target_structure: Input[Artifact] = None,
    extra_cli_args: list = None,  # noqa: RUF013
):
    """Runs BindCraft design for a single seed using FreeBindCraft in --no-pyrosetta mode.

    Symlinks AF2 and SolubleMPNN weights from NFS, executes ColabDesign trajectory optimization,
    and normalizes ranked design CIFs and confidence JSONs into predictions/ output.
    """
    import csv
    import json
    import logging
    import math
    import os
    import shutil
    import subprocess
    import time

    extra_cli_args = list(extra_cli_args or [])

    logging.info(
        f"Starting BindCraft prediction (seed={seed_value}, num_designs={num_designs_per_seed})"
    )
    t0 = time.time()

    # Output paths
    output_dir = os.path.dirname(predicted_structure.path)
    os.makedirs(output_dir, exist_ok=True)
    predictions_dir = os.path.join(output_dir, "predictions")
    os.makedirs(predictions_dir, exist_ok=True)

    predicted_structure.uri = f"{predicted_structure.uri}.cif"
    confidence_json.uri = f"{confidence_json.uri}.json"

    # Set up runtime symlinks for AF2 params and MPNN weights
    bindcraft_dir = "/opt/bindcraft"
    if os.path.exists(bindcraft_dir):
        # AF2 params
        os.makedirs(os.path.join(bindcraft_dir, "params"), exist_ok=True)
        if os.path.exists(af2_params_nfs_path):
            try:
                for item in os.listdir(af2_params_nfs_path):
                    s = os.path.join(af2_params_nfs_path, item)
                    d = os.path.join(bindcraft_dir, "params", item)
                    if not os.path.exists(d):
                        os.symlink(s, d)
            except Exception as e:
                logging.warning(f"Could not symlink AF2 params: {e}")

        # MPNN weights
        os.makedirs(os.path.join(bindcraft_dir, "mpnn_weights"), exist_ok=True)
        if os.path.exists(mpnn_weights_nfs_path):
            try:
                for item in os.listdir(mpnn_weights_nfs_path):
                    s = os.path.join(mpnn_weights_nfs_path, item)
                    d = os.path.join(bindcraft_dir, "mpnn_weights", item)
                    if not os.path.exists(d):
                        os.symlink(s, d)
            except Exception as e:
                logging.warning(f"Could not symlink MPNN weights: {e}")

    # Copy settings JSON to local work directory
    settings_local = os.path.join(output_dir, "settings.json")
    shutil.copy2(settings_json.path, settings_local)

    with open(settings_local) as f:
        try:
            settings_data = json.load(f)
        except Exception:
            settings_data = {}

    # Stage target structure if provided and ensure a valid .pdb file exists for BindCraft
    default_target_pdb = os.path.join(output_dir, "target.pdb")
    default_target_cif = os.path.join(output_dir, "target.cif")
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
            try:
                import gemmi

                st = gemmi.read_structure(target_structure.path)
                st.write_pdb(default_target_pdb)
                st.make_mmcif_document().write_file(default_target_cif)
            except Exception as e:
                logging.warning(f"gemmi conversion fallback for target structure: {e}")
                if not os.path.exists(default_target_pdb):
                    shutil.copy2(target_structure.path, default_target_pdb)
                if not os.path.exists(default_target_cif):
                    shutil.copy2(target_structure.path, default_target_cif)

    bindcraft_out = os.path.join(output_dir, "bindcraft_out")
    os.makedirs(bindcraft_out, exist_ok=True)

    # Populate FreeBindCraft target settings JSON fields with runtime paths and seed parameters
    import re

    settings_data["design_path"] = bindcraft_out
    base_binder_name = str(settings_data.get("binder_name") or "FoldRunBinder").strip()
    if f"seed{seed_value}" not in base_binder_name:
        settings_data["binder_name"] = f"{base_binder_name}_seed{seed_value}"
    else:
        settings_data["binder_name"] = base_binder_name
    if os.path.exists(default_target_pdb):
        settings_data["starting_pdb"] = default_target_pdb
    elif "starting_pdb" not in settings_data:
        settings_data["starting_pdb"] = settings_data.get(
            "target_pdb", settings_data.get("target_structure", default_target_pdb)
        )
    if "chains" not in settings_data:
        settings_data["chains"] = settings_data.get("target_chains", "A")
    if "target_hotspot_residues" not in settings_data or any(
        c.isalpha() for c in str(settings_data.get("target_hotspot_residues", ""))
    ):
        hotspots = settings_data.get("hotspot_residues") or str(
            settings_data.get("target_hotspot_residues", "")
        ).split(",")
        cleaned = [re.sub(r"^[A-Za-z]+:?", "", str(r).strip()) for r in hotspots if str(r).strip()]
        settings_data["target_hotspot_residues"] = ",".join(c for c in cleaned if c)
    if "lengths" not in settings_data:
        length_str = str(settings_data.get("binder_length_range", "80..140"))
        m = re.search(r"(\d+)\s*(?:\.\.|\-)\s*(\d+)", length_str)
        if m:
            settings_data["lengths"] = [int(m.group(1)), int(m.group(2))]
        else:
            val = int(re.sub(r"[^\d]", "", length_str) or 100)
            settings_data["lengths"] = [val, val]
    settings_data["number_of_final_designs"] = int(num_designs_per_seed)

    with open(settings_local, "w") as sf:
        json.dump(settings_data, sf, indent=2)

    os.environ["PYTHONHASHSEED"] = str(seed_value)
    os.environ["FOLDRUN_SEED"] = str(seed_value)
    ld_paths = ["/usr/local/nvidia/lib64", "/usr/local/nvidia/lib", "/opt/conda/lib"]
    existing_ld = [p for p in os.environ.get("LD_LIBRARY_PATH", "").split(":") if p]
    for p in reversed(ld_paths):
        if p not in existing_ld:
            existing_ld.insert(0, p)
    os.environ["LD_LIBRARY_PATH"] = ":".join(existing_ld)

    filters_json = os.path.join(bindcraft_dir, "settings_filters", "default_filters.json")
    default_advanced_json = os.path.join(
        bindcraft_dir, "settings_advanced", "default_4stage_multimer.json"
    )
    user_filters_override = settings_data.pop("_foldrun_filters_path", None)
    user_advanced_override = settings_data.pop("_foldrun_advanced_path", None)

    remaining_cli_args = []
    idx_arg = 0
    while idx_arg < len(extra_cli_args):
        raw_a = str(extra_cli_args[idx_arg]).strip()
        if raw_a.startswith("--filters="):
            user_filters_override = raw_a.split("=", 1)[1]
            idx_arg += 1
            continue
        if raw_a in ("--filters", "-f") and idx_arg + 1 < len(extra_cli_args):
            user_filters_override = str(extra_cli_args[idx_arg + 1]).strip()
            idx_arg += 2
            continue
        if raw_a.startswith("--advanced="):
            user_advanced_override = raw_a.split("=", 1)[1]
            idx_arg += 1
            continue
        if raw_a in ("--advanced", "-a") and idx_arg + 1 < len(extra_cli_args):
            user_advanced_override = str(extra_cli_args[idx_arg + 1]).strip()
            idx_arg += 2
            continue
        remaining_cli_args.append(raw_a)
        idx_arg += 1

    def _stage_optional_json(src_ref, dest_name, preset_subdir=None):
        if not src_ref:
            return None
        ref_str = str(src_ref).strip()
        dest_p = os.path.join(output_dir, dest_name)
        if ref_str.startswith("gs://"):
            from google.cloud import storage as _gcs

            bkt_name, blb_name = ref_str[5:].split("/", 1)
            _gcs.Client().bucket(bkt_name).blob(blb_name).download_to_filename(dest_p)
            return dest_p
        if ref_str.startswith("/gcs/") and os.path.exists(ref_str):
            shutil.copy2(ref_str, dest_p)
            return dest_p
        candidates = [ref_str]
        if not os.path.isabs(ref_str):
            candidates.append(os.path.join(bindcraft_dir, ref_str))
            if preset_subdir and "/" not in ref_str.replace("\\", "/"):
                candidates.append(os.path.join(bindcraft_dir, preset_subdir, ref_str))
        for cand in candidates:
            if os.path.exists(cand):
                shutil.copy2(cand, dest_p)
                return dest_p
        return None

    staged_filters = _stage_optional_json(
        user_filters_override, "custom_filters.json", preset_subdir="settings_filters"
    )
    if staged_filters:
        filters_json = staged_filters

    base_adv_source = (
        _stage_optional_json(
            user_advanced_override, "base_advanced.json", preset_subdir="settings_advanced"
        )
        or default_advanced_json
    )
    advanced_local = os.path.join(output_dir, "advanced_settings.json")
    try:
        with open(base_adv_source) as af:
            advanced_data = json.load(af)
    except Exception:
        advanced_data = {}
    advanced_data["af_params_dir"] = bindcraft_dir
    advanced_data["dssp_path"] = os.path.join(bindcraft_dir, "functions", "dssp")
    advanced_data["dalphaball_path"] = os.path.join(bindcraft_dir, "functions", "DAlphaBall.gcc")
    if os.path.exists(os.path.join(bindcraft_dir, "functions", "faspr")):
        advanced_data["faspr_path"] = os.path.join(bindcraft_dir, "functions", "faspr")
    if not user_advanced_override:
        advanced_data["max_trajectories"] = max(int(num_designs_per_seed) * 3, 4)
        advanced_data["num_seqs"] = min(int(advanced_data.get("num_seqs", 4)), 4)
        advanced_data["max_mpnn_sequences"] = min(
            int(advanced_data.get("max_mpnn_sequences", 2)), 2
        )
    advanced_data.setdefault("save_design_animations", False)
    with open(advanced_local, "w") as af:
        json.dump(advanced_data, af, indent=2)
    advanced_json = advanced_local

    # Re-save settings_local in case internal _foldrun_* keys were popped
    with open(settings_local, "w") as sf:
        json.dump(settings_data, sf, indent=2)

    # Inject sitecustomize.py to seed RNGs across seeds, shim ColabDesign mk_mpnn_model 'weights' kwarg,
    # and ensure OpenMM falls back to CPU when the host driver rejects CUDA PTX version (error 222)
    shim_dir = os.path.join(output_dir, "_py_shim")
    os.makedirs(shim_dir, exist_ok=True)
    with open(os.path.join(shim_dir, "sitecustomize.py"), "w") as scf:
        scf.write(
            "import inspect, os, random\n"
            "try:\n"
            "    _s = int(os.environ.get('FOLDRUN_SEED', '0'))\n"
            "    random.seed(_s)\n"
            "    import numpy as _np\n"
            "    _np.random.seed(_s % (2**32 - 1))\n"
            "except Exception:\n"
            "    pass\n"
            "try:\n"
            "    import colabdesign.mpnn.model as _mpnn_mod\n"
            "    _orig_init = _mpnn_mod.mk_mpnn_model.__init__\n"
            "    if 'weights' not in inspect.signature(_orig_init).parameters:\n"
            "        def _patched_init(self, *args, weights='original', **kwargs):\n"
            "            return _orig_init(self, *args, **kwargs)\n"
            "        _mpnn_mod.mk_mpnn_model.__init__ = _patched_init\n"
            "except Exception:\n"
            "    pass\n"
            "try:\n"
            "    import openmm\n"
            "    _orig_get_plat = openmm.Platform.getPlatformByName\n"
            "    def _safe_get_platform(name):\n"
            "        if name == 'CUDA':\n"
            "            return _orig_get_plat('CPU')\n"
            "        return _orig_get_plat(name)\n"
            "    openmm.Platform.getPlatformByName = staticmethod(_safe_get_platform)\n"
            "except Exception:\n"
            "    pass\n"
        )
    existing_pypath = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = f"{shim_dir}:{existing_pypath}" if existing_pypath else shim_dir
    os.environ["OPENMM_DEFAULT_PLATFORM"] = "CPU"

    # Also patch /opt/bindcraft/functions/*.py in-place for OpenMM CPU fallback
    funcs_dir = os.path.join(bindcraft_dir, "functions")
    if os.path.exists(funcs_dir):
        for fname in os.listdir(funcs_dir):
            if fname.endswith(".py"):
                fpath = os.path.join(funcs_dir, fname)
                try:
                    with open(fpath) as pf:
                        src = pf.read()
                    new_src = src.replace(
                        "getPlatformByName('CUDA')", "getPlatformByName('CPU')"
                    ).replace('getPlatformByName("CUDA")', 'getPlatformByName("CPU")')
                    if new_src != src:
                        with open(fpath, "w") as pf:
                            pf.write(new_src)
                except Exception:
                    pass

    # Verify pandas/scipy imports without attempting internet pip install inside VPC-isolated workers
    try:
        import pandas  # noqa: F401
        import scipy  # noqa: F401
    except ImportError as abi_err:
        logging.warning(f"Optional pandas/scipy import warning in container: {abi_err}")

    # Build FreeBindCraft execution command
    cmd = [
        "python3",
        "-u",
        f"{bindcraft_dir}/bindcraft.py",
        f"--settings={settings_local}",
        f"--filters={filters_json}",
        f"--advanced={advanced_json}",
        "--no-pyrosetta",
    ]

    for arg_str in remaining_cli_args:
        if arg_str and arg_str not in cmd:
            cmd.append(arg_str)

    logging.info(f"Running BindCraft command: {' '.join(cmd)}")
    exec_cwd = bindcraft_dir if os.path.exists(bindcraft_dir) else output_dir
    subprocess.run(cmd, cwd=exec_cwd, check=True)

    # Locate metrics CSV — prioritize final_design_stats.csv over trajectory/MPNN intermediate CSVs
    metrics_records = []
    csv_candidates = []
    for root, _, files in os.walk(bindcraft_out):
        for f in sorted(files):
            if f.endswith(".csv") and (
                "final_design_stats" in f or "metric" in f or "ranked" in f or "design" in f
            ):
                csv_candidates.append(os.path.join(root, f))
    csv_candidates.sort(key=lambda p: (0 if "final_design_stats" in os.path.basename(p) else 1, p))
    for csv_path in csv_candidates:
        try:
            with open(csv_path, newline="") as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    metrics_records.append(row)
        except Exception as ex:
            logging.warning(f"Failed to read CSV {csv_path}: {ex}")

    # Collect ONLY accepted final designs — NEVER ingest Rejected/, Trajectory/, or MPNN/
    accepted_dir = os.path.join(bindcraft_out, "Accepted")
    structure_files = []
    if os.path.isdir(accepted_dir):
        for root, _, files in os.walk(accepted_dir):
            for f in sorted(files):
                if f.endswith((".cif", ".pdb")):
                    structure_files.append(os.path.join(root, f))
        if not structure_files:
            raise RuntimeError(
                f"BindCraft completed trajectory sampling cleanly, but 0 designs passed acceptance filters "
                f"in {accepted_dir} (increase max_trajectories/num_designs or relax filter thresholds)."
            )
    else:
        excluded_dirs = {"rejected", "trajectory", "mpnn", "animation"}
        for root, dirs, files in os.walk(bindcraft_out):
            dirs[:] = [d for d in dirs if d.lower() not in excluded_dirs]
            for f in sorted(files):
                if f.endswith((".cif", ".pdb")):
                    structure_files.append(os.path.join(root, f))

    logging.info(
        f"Discovered {len(structure_files)} accepted structures and {len(metrics_records)} metric records"
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

    def _first_present(rec: dict, *keys: str):
        for k in keys:
            if k in rec and rec[k] is not None and str(rec[k]).strip() != "":
                return rec[k]
        return None

    best_cif = None
    best_conf_data = None
    best_sort_key = (False, -1e9)

    # Normalize outputs into predictions/
    for i, struct_path in enumerate(structure_files):
        design_name = os.path.splitext(os.path.basename(struct_path))[0]
        design_stem = re.sub(r"_model\d+$", "", design_name)
        dest_cif = os.path.join(predictions_dir, f"design_seed{seed_value}_{i}.cif")

        # Convert PDB to CIF if needed via gemmi
        if struct_path.endswith(".pdb"):
            try:
                import gemmi

                st = gemmi.read_structure(struct_path)
                st.make_mmcif_document().write_file(dest_cif)
            except Exception:
                shutil.copy2(struct_path, dest_cif)
        else:
            shutil.copy2(struct_path, dest_cif)

        # Match metrics against BindCraft's final_design_stats.csv ('Design' column) or fallback keys
        matched_metric = {}
        for rec in metrics_records:
            rec_design = str(_first_present(rec, "Design", "design", "id", "name") or "").strip()
            if rec_design and (
                rec_design in (design_name, design_stem) or design_name.startswith(rec_design)
            ):
                matched_metric = rec
                break
        if not matched_metric and i < len(metrics_records):
            matched_metric = metrics_records[i]

        plddt_raw = _opt_float(
            _first_present(matched_metric, "Average_pLDDT", "plddt", "mean_plddt", "pLDDT")
        )
        plddt_val = (
            plddt_raw * 100.0 if (plddt_raw is not None and 0.0 < plddt_raw <= 1.0) else plddt_raw
        )
        iptm_val = _opt_float(
            _first_present(matched_metric, "Average_i_pTM", "iptm", "interface_ptm", "i_pTM")
        )
        ptm_val = _opt_float(_first_present(matched_metric, "Average_pTM", "ptm", "pTM"))
        ranking_score_val = _opt_float(_first_present(matched_metric, "ranking_score", "score"))
        if ranking_score_val is None and plddt_val is not None and iptm_val is not None:
            ranking_score_val = (plddt_val / 100.0) * 0.5 + iptm_val * 0.5
        interface_energy_val = _opt_float(
            _first_present(matched_metric, "Average_dG", "interface_energy", "dG")
        )
        contact_surface_val = _opt_float(
            _first_present(matched_metric, "Average_dSASA", "contact_surface_area", "dSASA")
        )
        sc_rmsd_val = _opt_float(
            _first_present(matched_metric, "Average_Binder_RMSD", "Binder_RMSD", "sc_score")
        )
        seq_val = str(
            _first_present(matched_metric, "Sequence", "designed_sequence", "sequence") or ""
        )

        conf_data = {
            "design_id": f"design_seed{seed_value}_{i}",
            "source_design_name": design_name,
            "seed_value": seed_value,
            "plddt": plddt_val,
            "mean_plddt": plddt_val,
            "iptm": iptm_val,
            "ptm": ptm_val,
            "ranking_score": ranking_score_val,
            "interface_energy": interface_energy_val,
            "contact_surface_area": contact_surface_val,
            "sc_score": sc_rmsd_val,
            "designed_sequence": seq_val,
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
            f"BindCraft execution completed but produced no accepted structure files (.cif/.pdb) in {bindcraft_out}"
        )

    # Copy top design to KFP outputs
    shutil.copy2(best_cif, predicted_structure.path)
    with open(confidence_json.path, "w") as jf:
        json.dump(best_conf_data, jf, indent=2)

    predicted_structure.metadata["category"] = "predicted_structure"
    predicted_structure.metadata["seed_value"] = seed_value
    best_rank_val = best_conf_data.get("ranking_score") if best_conf_data else None
    if best_rank_val is not None:
        predicted_structure.metadata["ranking_score"] = float(best_rank_val)

    confidence_json.metadata["category"] = "confidence"
    if best_rank_val is not None:
        confidence_json.metadata["ranking_score"] = float(best_rank_val)

    t1 = time.time()
    rank_str = f"{best_rank_val:.3f}" if best_rank_val is not None else "None"
    logging.info(
        f"BindCraft prediction completed (seed={seed_value}). Best ranking_score={rank_str}. "
        f"Elapsed time: {t1 - t0:.1f}s"
    )
