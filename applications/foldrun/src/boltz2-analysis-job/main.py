#!/usr/bin/env python3
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

"""Cloud Run Job for analyzing Boltz-2 predictions in parallel.

Reads confidence_{name}_model_N.json (aggregated confidence scores) and the
CIF file (per-atom pLDDT from B-factor column) to produce plots and Gemini analysis.
No separate per-residue confidence JSON — pLDDT is extracted directly from the CIF.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone

import matplotlib
import numpy as np
from google import genai
from google.cloud import storage
from google.genai import types

matplotlib.use("Agg")  # Use non-interactive backend for Cloud Run
import matplotlib.pyplot as plt
import seaborn as sns

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# pLDDT confidence bands (same as AF2)
PLDDT_BANDS = [
    (0, 50, "very_low_confidence"),
    (50, 70, "low_confidence"),
    (70, 90, "high_confidence"),
    (90, 100, "very_high_confidence"),
]


def download_from_gcs(gcs_uri: str, local_path: str) -> None:
    """Download file from GCS."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")

    parts = gcs_uri[5:].split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1] if len(parts) > 1 else ""

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.reload()
    blob.download_to_filename(local_path)

    size_mb = blob.size / 1024 / 1024 if blob.size else 0
    logger.info(f"Downloaded {gcs_uri} ({size_mb:.2f} MB)")


def upload_to_gcs(local_path: str, gcs_uri: str) -> None:
    """Upload file to GCS."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")

    parts = gcs_uri[5:].split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1] if len(parts) > 1 else ""

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(local_path)

    logger.info(f"Uploaded {local_path} to {gcs_uri}")


def download_json_from_gcs(gcs_uri: str) -> dict:
    """Download and parse JSON file from GCS."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")

    parts = gcs_uri[5:].split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1] if len(parts) > 1 else ""

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    content = blob.download_as_string()
    return json.loads(content)


def download_image_from_gcs(gcs_uri: str) -> bytes:
    """Download image bytes from GCS."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")

    parts = gcs_uri[5:].split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1] if len(parts) > 1 else ""

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    return blob.download_as_bytes()


def calculate_plddt_stats(plddt_scores: list) -> dict:
    """Calculate pLDDT statistics from per-residue array."""
    scores = np.array(plddt_scores)

    stats = {
        "mean": float(np.mean(scores)),
        "median": float(np.median(scores)),
        "min": float(np.min(scores)),
        "max": float(np.max(scores)),
        "std": float(np.std(scores)),
        "per_residue": plddt_scores,
    }

    # Distribution by confidence band
    distribution = {}
    for min_val, max_val, label in PLDDT_BANDS:
        count = int(np.sum((scores >= min_val) & (scores <= max_val)))
        distribution[label] = count

    stats["distribution"] = distribution
    return stats


def get_quality_assessment(plddt_mean: float) -> str:
    """Get quality assessment based on mean pLDDT."""
    if plddt_mean >= 90:
        return "very_high_confidence"
    elif plddt_mean >= 70:
        return "high_confidence"
    elif plddt_mean >= 50:
        return "low_confidence"
    else:
        return "very_low_confidence"


def _detect_atom_site_columns(cif_text: str) -> dict:
    """Parse the _atom_site loop header to build a column-name → index map.

    Returns e.g. {'group_PDB': 0, 'id': 1, ..., 'B_iso_or_equiv': 14, ...}
    """
    col_names = []
    in_atom_site_loop = False

    for line in cif_text.split("\n"):
        stripped = line.strip()
        if stripped == "loop_":
            col_names = []
            in_atom_site_loop = False
        elif stripped.startswith("_atom_site."):
            field = stripped.split(".", 1)[1].split()[0]
            col_names.append(field)
            in_atom_site_loop = True
        elif in_atom_site_loop and (stripped.startswith("ATOM") or stripped.startswith("HETATM")):
            break  # data starts — column list is complete
        elif in_atom_site_loop and stripped and not stripped.startswith("_atom_site."):
            if not stripped.startswith("ATOM") and not stripped.startswith("HETATM"):
                in_atom_site_loop = False

    return {name: idx for idx, name in enumerate(col_names)}


def parse_cif_chains(cif_text: str) -> tuple[list[dict], list[float]]:
    """Parse CIF atom_site records to extract per-chain info and per-atom pLDDT.

    Returns:
        chain_info: list of dicts sorted by chain order:
            [{'chain_id': 'A', 'atom_count': 601, 'residue_count': 76,
              'comp_ids': ['MET','GLN',...], 'molecule_type': 'protein'}, ...]
        plddt_scores: per-atom pLDDT from B_iso_or_equiv column (empty if not found)
    """
    # Detect column indices from CIF header (handles variable column ordering)
    col_map = _detect_atom_site_columns(cif_text)
    chain_col = col_map.get("auth_asym_id", 11)
    comp_col = col_map.get("auth_comp_id", 10)
    seq_col = col_map.get("auth_seq_id", 9)
    bfactor_col = col_map.get("B_iso_or_equiv")

    chains = {}  # chain_id -> {atoms, residues set, comp_ids set}
    chain_order = []
    plddt_scores = []

    for line in cif_text.split("\n"):
        if not (line.startswith("ATOM") or line.startswith("HETATM")):
            continue
        parts = line.split()
        if len(parts) <= max(chain_col, comp_col, seq_col):
            continue
        chain_id = parts[chain_col]
        comp_id = parts[comp_col]
        seq_id = parts[seq_col]

        if bfactor_col is not None and bfactor_col < len(parts):
            try:
                plddt_scores.append(float(parts[bfactor_col]))
            except ValueError:
                plddt_scores.append(0.0)

        if chain_id not in chains:
            chains[chain_id] = {"atoms": 0, "residues": set(), "comp_ids": set()}
            chain_order.append(chain_id)
        chains[chain_id]["atoms"] += 1
        chains[chain_id]["residues"].add(seq_id)
        chains[chain_id]["comp_ids"].add(comp_id)

    result = []
    for cid in chain_order:
        info = chains[cid]
        comp_ids = info["comp_ids"]
        # Classify molecule type from residue names
        standard_aa = {
            "ALA",
            "ARG",
            "ASN",
            "ASP",
            "CYS",
            "GLN",
            "GLU",
            "GLY",
            "HIS",
            "ILE",
            "LEU",
            "LYS",
            "MET",
            "PHE",
            "PRO",
            "SER",
            "THR",
            "TRP",
            "TYR",
            "VAL",
        }
        rna_bases = {"A", "C", "G", "U"}
        dna_bases = {"DA", "DC", "DG", "DT"}

        if comp_ids & standard_aa:
            mol_type = "protein"
        elif comp_ids & rna_bases:
            mol_type = "rna"
        elif comp_ids & dna_bases:
            mol_type = "dna"
        else:
            mol_type = "ligand"

        result.append(
            {
                "chain_id": cid,
                "atom_count": info["atoms"],
                "residue_count": len(info["residues"]),
                "comp_ids": sorted(comp_ids),
                "molecule_type": mol_type,
            }
        )

    return result, plddt_scores


def download_text_from_gcs(gcs_uri: str) -> str:
    """Download text file from GCS."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")
    parts = gcs_uri[5:].split("/", 1)
    storage_client = storage.Client()
    bucket = storage_client.bucket(parts[0])
    blob = bucket.blob(parts[1] if len(parts) > 1 else "")
    return blob.download_as_text()


def plot_plddt(
    plddt_scores: list,
    sample_name: str,
    output_path: str,
    chain_info: list[dict] = None,
) -> None:
    """Generate pLDDT per-atom plot with chain boundary annotations.

    Args:
        plddt_scores: Atom-level pLDDT scores
        sample_name: Name for plot title
        output_path: Local path to save plot PNG
        chain_info: Chain info from parse_cif_chains() for boundary lines
    """
    scores = np.array(plddt_scores)
    fig, ax = plt.subplots(figsize=(12, 4))

    residues = np.arange(1, len(scores) + 1)

    # If chain info available, color each chain differently
    if chain_info and len(chain_info) > 1:
        chain_colors = [
            "#1f77b4",
            "#ff7f0e",
            "#2ca02c",
            "#d62728",
            "#9467bd",
            "#8c564b",
            "#e377c2",
            "#7f7f7f",
            "#bcbd22",
            "#17becf",
        ]
        offset = 0
        for i, ci in enumerate(chain_info):
            n = ci["atom_count"]
            color = chain_colors[i % len(chain_colors)]
            label = f"Chain {ci['chain_id']} ({ci['molecule_type']}"
            if ci["molecule_type"] == "ligand":
                label += f": {', '.join(ci['comp_ids'][:3])}"
            label += f", {n} atoms)"
            ax.plot(
                residues[offset : offset + n],
                scores[offset : offset + n],
                linewidth=1.5,
                color=color,
                label=label,
            )

            # Draw chain boundary line
            if i < len(chain_info) - 1:
                boundary = offset + n
                ax.axvline(
                    x=boundary, color="gray", linestyle="--", alpha=0.5, linewidth=1
                )

            offset += n
    else:
        ax.plot(residues, scores, linewidth=1.5, color="#1f77b4")

    # Confidence band backgrounds
    ax.axhspan(90, 100, alpha=0.08, color="green")
    ax.axhspan(70, 90, alpha=0.08, color="yellow")
    ax.axhspan(50, 70, alpha=0.08, color="orange")
    ax.axhspan(0, 50, alpha=0.08, color="red")

    ax.set_xlabel("Atom Position", fontsize=10)
    ax.set_ylabel("pLDDT Score", fontsize=10)
    ax.set_title(
        f"Per-Atom Confidence (pLDDT) - {sample_name}", fontsize=11, fontweight="bold"
    )
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.3, linestyle="--")
    ax.legend(loc="lower right", fontsize=7)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Generated pLDDT plot: {output_path}")


def plot_pde(
    pde_matrix: list, sample_name: str, output_path: str, chain_info: list[dict] = None
) -> None:
    """Generate PDE heatmap with chain boundary lines.

    The PDE matrix is token-level (residue/entity level), not atom-level.
    Chain boundaries are drawn using residue counts.
    """
    pde = np.array(pde_matrix)
    fig, ax = plt.subplots(figsize=(8, 7))

    cmap = plt.cm.Greens_r
    im = ax.imshow(pde, vmin=0, vmax=np.max(pde), cmap=cmap)

    # Draw chain boundary lines on heatmap
    if chain_info and len(chain_info) > 1:
        offset = 0
        for i, ci in enumerate(chain_info):
            n = ci["residue_count"]
            if i < len(chain_info) - 1:
                boundary = offset + n - 0.5  # center on boundary
                ax.axhline(
                    y=boundary, color="white", linestyle="-", linewidth=1.5, alpha=0.8
                )
                ax.axvline(
                    x=boundary, color="white", linestyle="-", linewidth=1.5, alpha=0.8
                )
                # Label chain
                mid = offset + n / 2
                ax.text(
                    -2,
                    mid,
                    f"{ci['chain_id']}",
                    ha="right",
                    va="center",
                    fontsize=9,
                    fontweight="bold",
                    color="#333",
                )
            else:
                mid = offset + n / 2
                ax.text(
                    -2,
                    mid,
                    f"{ci['chain_id']}",
                    ha="right",
                    va="center",
                    fontsize=9,
                    fontweight="bold",
                    color="#333",
                )
            offset += n

    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label(
        "Predicted Distance Error (A)", rotation=270, labelpad=20, fontsize=10
    )

    ax.set_xlabel("Scored Residue", fontsize=10)
    ax.set_ylabel("Aligned Residue", fontsize=10)
    ax.set_title(
        f"Predicted Distance Error (PDE) - {sample_name}",
        fontsize=11,
        fontweight="bold",
    )

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Generated PDE plot: {output_path}")


def calculate_per_chain_plddt(plddt_scores: list, chain_info: list[dict]) -> dict:
    """Calculate per-chain pLDDT statistics.

    Args:
        plddt_scores: Atom-level pLDDT scores
        chain_info: Chain info from parse_cif_chains()

    Returns:
        Dict mapping chain_id to stats dict with mean, min, max, molecule_type, comp_ids
    """
    scores = np.array(plddt_scores)
    result = {}
    offset = 0

    for ci in chain_info:
        n = ci["atom_count"]
        chain_scores = scores[offset : offset + n]
        if len(chain_scores) > 0:
            result[ci["chain_id"]] = {
                "mean": float(np.mean(chain_scores)),
                "min": float(np.min(chain_scores)),
                "max": float(np.max(chain_scores)),
                "std": float(np.std(chain_scores)),
                "atom_count": n,
                "residue_count": ci["residue_count"],
                "molecule_type": ci["molecule_type"],
                "comp_ids": ci["comp_ids"],
            }
        offset += n

    return result


def plot_iptm_matrix(
    chain_pair_iptm: dict,
    sample_name: str,
    output_path: str,
    chain_ptm: dict = None,
    chain_info: list = None,
) -> None:
    """Generate chain x chain ipTM heatmap with per-chain pTM on diagonal.

    Args:
        chain_pair_iptm: Dict like {"(A, B)": 0.72}
        sample_name: Name for plot title
        output_path: Local path to save plot PNG
        chain_ptm: Dict like {"A": 0.87, "B": 0.46} for diagonal values
        chain_info: Chain info for molecule type labels
    """
    # Parse chain pairs and build matrix
    chains = set()
    pairs = {}
    for key, value in chain_pair_iptm.items():
        key_clean = key.strip("()").replace(" ", "")
        parts = key_clean.split(",")
        if len(parts) == 2:
            c1, c2 = parts
            chains.add(c1)
            chains.add(c2)
            pairs[(c1, c2)] = value

    # Also add chains from chain_ptm (for diagonal)
    if chain_ptm:
        chains.update(chain_ptm.keys())

    chains = sorted(chains)
    n = len(chains)
    if n < 2:
        logger.info("Skipping ipTM matrix — fewer than 2 chains")
        return

    matrix = np.zeros((n, n))
    for i, c1 in enumerate(chains):
        for j, c2 in enumerate(chains):
            if i == j and chain_ptm and c1 in chain_ptm:
                # Diagonal: per-chain pTM
                matrix[i, j] = chain_ptm[c1]
            else:
                matrix[i, j] = pairs.get((c1, c2), pairs.get((c2, c1), 0.0))

    # Build labels with molecule type
    chain_labels = []
    ci_map = {c["chain_id"]: c for c in (chain_info or [])}
    for c in chains:
        ci = ci_map.get(c)
        if ci:
            mol = ci["molecule_type"]
            if mol == "ligand":
                comps = ", ".join(ci["comp_ids"][:2])
                chain_labels.append(f"{c} ({comps})")
            else:
                chain_labels.append(f"{c} ({mol})")
        else:
            chain_labels.append(c)

    fig, ax = plt.subplots(figsize=(max(5, n + 3), max(4.5, n + 2.5)))

    sns.heatmap(
        matrix,
        annot=True,
        fmt=".2f",
        annot_kws={"size": 12},
        cmap="YlGnBu",
        vmin=0,
        vmax=1,
        xticklabels=chain_labels,
        yticklabels=chain_labels,
        square=True,
        ax=ax,
        cbar_kws={"label": "ipTM Score", "shrink": 0.8},
    )

    ax.set_title(
        f"Chain-Pair ipTM Matrix - {sample_name}",
        fontsize=11,
        fontweight="bold",
        pad=15,
    )
    ax.set_xlabel("Chain", fontsize=10)
    ax.set_ylabel("Chain", fontsize=10)
    ax.tick_params(axis="x", rotation=0)
    ax.tick_params(axis="y", rotation=0)

    plt.tight_layout(pad=1.5)
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Generated ipTM matrix plot: {output_path}")


def main():
    """Main entry point for Cloud Run Job task."""

    task_index = int(os.getenv("CLOUD_RUN_TASK_INDEX", "0"))
    task_count = int(os.getenv("CLOUD_RUN_TASK_COUNT", "1"))

    logger.info(f"Task {task_index}/{task_count}: Starting")

    bucket_name = os.getenv("GCS_BUCKET")
    analysis_path = os.getenv("ANALYSIS_PATH")

    if not bucket_name:
        logger.error("GCS_BUCKET environment variable not set")
        sys.exit(1)

    if not analysis_path:
        logger.error("ANALYSIS_PATH environment variable not set")
        sys.exit(1)

    logger.info(f"Bucket: {bucket_name}, Analysis path: {analysis_path}")

    # Load task configuration
    task_config_uri = f"{analysis_path}task_config.json"
    logger.info(f"Task {task_index}: Loading configuration from {task_config_uri}")

    try:
        task_config = download_json_from_gcs(task_config_uri)
    except Exception as e:
        logger.error(f"Failed to load task configuration: {e}")
        sys.exit(1)

    predictions = task_config.get("predictions", [])
    if task_index >= len(predictions):
        logger.error(
            f"Task index {task_index} out of range (total predictions: {len(predictions)})"
        )
        sys.exit(1)

    pred = predictions[task_index]
    job_id = task_config.get("job_id")
    affinity_uri = task_config.get("affinity_uri")  # None if affinity was not requested
    query_yaml_uri = task_config.get("query_yaml_uri")  # Resolved by agent at job dispatch time

    sample_name = pred["sample_name"]
    cif_uri = pred["cif_uri"]
    confidences_uri = pred["confidences_uri"]
    aggregated_uri = pred["aggregated_uri"]
    pde_uri = pred.get("pde_uri", "")
    output_uri = pred["output_uri"]

    logger.info(f"Starting analysis for sample: {sample_name}")
    logger.info(f"Job ID: {job_id}")

    try:
        # Load aggregated confidences (summary scores)
        logger.info("Loading aggregated confidences...")
        aggregated = download_json_from_gcs(aggregated_uri)

        # Parse CIF to get chain info and per-atom pLDDT (stored in B-factor column)
        logger.info("Parsing CIF for chain info and pLDDT scores...")
        chain_info = []
        cif_plddt_scores = []
        try:
            cif_text = download_text_from_gcs(cif_uri)
            chain_info, cif_plddt_scores = parse_cif_chains(cif_text)
            for ci in chain_info:
                logger.info(
                    f"  Chain {ci['chain_id']}: {ci['molecule_type']}, "
                    f"{ci['atom_count']} atoms, {ci['residue_count']} residues, "
                    f"comps: {ci['comp_ids'][:5]}"
                )
            if cif_plddt_scores:
                logger.info(f"  Extracted {len(cif_plddt_scores)} per-atom pLDDT scores from CIF B-factors")
        except Exception as e:
            logger.warning(f"Could not parse CIF for chain info: {e}")

        # Extract metrics from aggregated
        ranking_score = aggregated.get(
            "confidence_score", aggregated.get("sample_ranking_score", 0.0)
        )
        ptm = aggregated.get("ptm", 0.0)
        iptm = aggregated.get("iptm", 0.0)
        avg_plddt = aggregated.get("complex_plddt", 0.0)
        gpde = aggregated.get("complex_pde", None)
        has_clash = aggregated.get("has_clash", 0)
        disorder = aggregated.get("disorder", 0)
        # Boltz-2 uses integer string indices ("0", "1") for chain keys.
        # Remap to CIF chain letters (A, B, C...) using chain_info order.
        raw_chain_ptm = aggregated.get("chains_ptm", {})
        raw_pair_iptm = aggregated.get("pair_chains_iptm", {})

        # Build index → chain letter map from CIF chain_info
        idx_to_letter = {str(i): ci["chain_id"] for i, ci in enumerate(chain_info)}

        chain_ptm = {
            idx_to_letter.get(str(k), str(k)): v for k, v in raw_chain_ptm.items()
        }

        # Flatten nested pair_chains_iptm {"0": {"0": 0.85, "1": 0.81}, ...}
        # into flat {"(A, B)": 0.81, ...} format expected by plot_iptm_matrix
        chain_pair_iptm = {}
        for c1_idx, row in raw_pair_iptm.items():
            if isinstance(row, dict):
                for c2_idx, score in row.items():
                    c1 = idx_to_letter.get(str(c1_idx), str(c1_idx))
                    c2 = idx_to_letter.get(str(c2_idx), str(c2_idx))
                    if c1 != c2:
                        chain_pair_iptm[f"({c1}, {c2})"] = score
            else:
                # Unexpected flat format — pass through
                c1 = idx_to_letter.get(str(c1_idx), str(c1_idx))
                chain_pair_iptm[c1] = row

        # Per-atom pLDDT: Boltz-2 stores these in the CIF B-factor column (not a separate JSON).
        # We extracted them above via parse_cif_chains; fall back to empty if CIF parse failed.
        plddt_scores = cif_plddt_scores

        # PDE matrix: written as pde_{stem}_model_N.npz when --write_full_pde is passed.
        pde_matrix = None
        if pde_uri:
            try:
                import io
                pde_bytes = download_image_from_gcs(pde_uri)  # returns raw bytes
                pde_data = np.load(io.BytesIO(pde_bytes))
                pde_matrix = pde_data["pde"].tolist()
                logger.info(f"Loaded PDE matrix {np.array(pde_matrix).shape} from {pde_uri}")
            except Exception as e:
                logger.warning(f"Could not load PDE matrix from {pde_uri}: {e}")

        # Calculate overall pLDDT stats
        if plddt_scores:
            plddt_stats = calculate_plddt_stats(plddt_scores)
        else:
            plddt_stats = {
                "mean": avg_plddt,
                "median": avg_plddt,
                "min": 0.0,
                "max": 100.0,
                "std": 0.0,
                "per_residue": [],
                "distribution": {},
            }

        # Calculate per-chain pLDDT stats (protein vs ligand breakdown)
        per_chain_plddt = {}
        if plddt_scores and chain_info:
            per_chain_plddt = calculate_per_chain_plddt(plddt_scores, chain_info)
            for cid, stats in per_chain_plddt.items():
                logger.info(
                    f"  Chain {cid} ({stats['molecule_type']}): "
                    f"mean pLDDT={stats['mean']:.1f}"
                )

        quality = get_quality_assessment(plddt_stats["mean"])

        # Generate plots
        logger.info("Generating visualizations...")
        plot_files = {}
        output_base = output_uri.rsplit("/", 1)[0]

        # pLDDT plot (with chain boundaries and per-chain coloring)
        if plddt_scores:
            plddt_plot_path = f"/tmp/plddt_plot_{task_index}.png"
            plot_plddt(
                plddt_scores,
                sample_name,
                plddt_plot_path,
                chain_info=chain_info if chain_info else None,
            )
            plddt_plot_uri = f"{output_base}/plddt_plot_{task_index}.png"
            upload_to_gcs(plddt_plot_path, plddt_plot_uri)
            plot_files["plddt_plot"] = plddt_plot_uri
            os.remove(plddt_plot_path)

        # PDE heatmap with chain boundary lines
        if pde_matrix:
            pde_plot_path = f"/tmp/pde_plot_{task_index}.png"
            plot_pde(
                pde_matrix,
                sample_name,
                pde_plot_path,
                chain_info=chain_info if chain_info else None,
            )
            pde_plot_uri = f"{output_base}/pde_plot_{task_index}.png"
            upload_to_gcs(pde_plot_path, pde_plot_uri)
            plot_files["pde_plot"] = pde_plot_uri
            os.remove(pde_plot_path)

        # ipTM matrix heatmap (multi-chain: needs at least 2 chains)
        if chain_pair_iptm and len(chain_info) > 1:
            iptm_plot_path = f"/tmp/iptm_matrix_{task_index}.png"
            plot_iptm_matrix(
                chain_pair_iptm,
                sample_name,
                iptm_plot_path,
                chain_ptm=chain_ptm,
                chain_info=chain_info,
            )
            if os.path.exists(iptm_plot_path):
                iptm_plot_uri = f"{output_base}/iptm_matrix_{task_index}.png"
                upload_to_gcs(iptm_plot_path, iptm_plot_uri)
                plot_files["iptm_matrix_plot"] = iptm_plot_uri
                os.remove(iptm_plot_path)

        # Build analysis result
        analysis = {
            "prediction_index": task_index,
            "sample_name": sample_name,
            "rank": task_index + 1,
            "ranking_score": ranking_score,
            "ptm": ptm,
            "iptm": iptm,
            "avg_plddt": avg_plddt,
            "gpde": gpde,
            "has_clash": has_clash,
            "disorder": disorder,
            "chain_ptm": chain_ptm,
            "chain_pair_iptm": chain_pair_iptm,
            "per_chain_plddt": per_chain_plddt,
            "chain_info": chain_info,
            "plddt_mean": plddt_stats["mean"],
            "plddt_median": plddt_stats["median"],
            "plddt_min": plddt_stats["min"],
            "plddt_max": plddt_stats["max"],
            "plddt_std": plddt_stats["std"],
            "plddt_distribution": plddt_stats["distribution"],
            "plddt_scores": plddt_stats["per_residue"],
            "quality_assessment": quality,
            "has_pde": pde_matrix is not None,
            "cif_uri": cif_uri,
            "aggregated_uri": aggregated_uri,
            "confidences_uri": confidences_uri,
            "analyzed_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            + "Z",
            "plots": plot_files,
        }

        # Write to JSON and upload
        local_json = f"/tmp/analysis_{task_index}.json"
        with open(local_json, "w") as f:
            json.dump(analysis, f, indent=2)

        upload_to_gcs(local_json, output_uri)
        os.remove(local_json)

        logger.info(f"Completed analysis for sample {sample_name}")
        logger.info(f"   ranking_score: {ranking_score:.4f}")
        logger.info(f"   pLDDT mean: {plddt_stats['mean']:.2f}")
        logger.info(f"   Quality: {quality}")

        # Last task triggers consolidation
        if task_index == task_count - 1:
            logger.info(
                f"Last task ({task_index + 1}/{task_count}), waiting for all tasks..."
            )

            import time

            max_wait = 120
            wait_interval = 2
            waited = 0

            storage_client = storage.Client()
            bucket_obj = storage_client.bucket(bucket_name)
            analysis_prefix = analysis_path.replace(f"gs://{bucket_name}/", "")

            while waited < max_wait:
                blobs = bucket_obj.list_blobs(prefix=analysis_prefix)
                completed_files = [
                    b.name
                    for b in blobs
                    if "prediction_" in b.name and b.name.endswith("_analysis.json")
                ]

                if len(completed_files) >= task_count:
                    logger.info(
                        f"All {task_count} analysis files found, starting consolidation"
                    )
                    break

                logger.info(
                    f"Waiting... ({len(completed_files)}/{task_count} files found)"
                )
                time.sleep(wait_interval)
                waited += wait_interval

            try:
                consolidate_results(job_id, analysis_path, affinity_uri=affinity_uri, query_yaml_uri=query_yaml_uri)
            except Exception as e:
                logger.error(f"Consolidation failed: {e}", exc_info=True)

        sys.exit(0)

    except Exception as e:
        logger.error(f"Error analyzing sample {sample_name}: {e}", exc_info=True)
        sys.exit(1)


def generate_gemini_expert_analysis(summary_data: dict) -> dict:
    """Generate expert analysis using Gemini via Google GenAI SDK."""
    try:
        best = summary_data["best_prediction"]
        summary = summary_data["summary"]
        top_preds = summary_data["top_predictions"][:3]

        # Build chain composition description
        chain_desc = ""
        for ci in best.get("chain_info", []):
            if ci["molecule_type"] == "ligand":
                comps = ", ".join(ci.get("comp_ids", [])[:3])
                chain_desc += f"- Chain {ci['chain_id']}: {ci['molecule_type']} ({comps}), {ci['atom_count']} atoms\n"
            else:
                chain_desc += f"- Chain {ci['chain_id']}: {ci['molecule_type']}, {ci['residue_count']} residues, {ci['atom_count']} atoms\n"

        gpde_str = (
            f"{best.get('gpde', 0):.4f}" if best.get("gpde") is not None else "N/A"
        )

        prompt = f"""You are an expert structural biologist reviewing Boltz-2 prediction results.

**Boltz-2 Reference (use to interpret results):**
- Diffusion-based biomolecular structure prediction model (MIT, open-source). Supports proteins, RNA, DNA, ligands, covalent modifications, and glycans.
- Generates multiple diffusion samples per seed. Seeds = independent weight initializations (different conformations). Samples = different denoising trajectories from same seed (variations on a theme).
- **Metric interpretation thresholds:**
  - confidence_score/ranking_score (0-1): Overall quality. >0.7 = good, >0.8 = very good.
  - pTM (0-1): Global fold accuracy. >0.8 = high confidence fold. >0.9 = very high.
  - ipTM (0-1): Interface quality for complexes. >0.7 = reliable interface. >0.8 = high confidence.
  - pLDDT (0-100): Per-atom confidence. >80 = good, >90 = very high. <50 in a region = likely disordered (real biology, not prediction failure).
  - complex_pde: Global predicted distance error. Lower = better. Complements pTM.
  - has_clash: Steric clash detected. 0 = clean, 1 = problem (try more seeds).
- **Key interpretation patterns:**
  - ipTM low but pTM high → individual chains fold well but interface/binding pose is uncertain
  - Low ligand pLDDT (e.g. <60) → binding mode is uncertain, try more seeds/samples
  - High variance in ranking_score across samples → conformational flexibility or poorly defined binding site
  - Chain pTM on diagonal of ipTM matrix shows per-chain fold quality independently of interface

**Prediction Summary:**
- Model: Boltz-2
- Job ID: {summary_data["job_id"]}
- Total Tokens/Residues: {summary["protein_info"]["sequence_length"]}
- Total Samples: {summary["protein_info"]["total_predictions"]}
- Best Sample: {best["sample_name"]} (Rank #{best.get("rank", 1)})

**Chain Composition:**
{chain_desc}
**Quality Metrics (Best Sample):**
- Ranking Score: {best["ranking_score"]:.4f}
- pTM: {best["ptm"]:.4f}
- ipTM: {best["iptm"]:.4f}
- Mean pLDDT: {best["plddt_mean"]:.2f}
- GPDE: {gpde_str}
- Has Clash: {"Yes" if best.get("has_clash") else "No"}
- Disorder: {"Yes" if best.get("disorder") else "No"}
"""

        # Add chain-level metrics
        if best.get("chain_ptm"):
            prompt += "\n**Per-Chain pTM:**\n"
            for chain, score in best["chain_ptm"].items():
                prompt += f"- Chain {chain}: {score:.4f}\n"

        if best.get("chain_pair_iptm"):
            prompt += "\n**Chain-Pair ipTM (interface quality):**\n"
            for pair, score in best["chain_pair_iptm"].items():
                prompt += f"- {pair}: {score:.4f}\n"

        # Add per-chain pLDDT breakdown (protein vs ligand)
        if best.get("per_chain_plddt"):
            prompt += "\n**Per-Chain pLDDT (protein vs ligand breakdown):**\n"
            for cid, stats in best["per_chain_plddt"].items():
                mol_type = stats.get("molecule_type", "unknown")
                comps = ", ".join(stats.get("comp_ids", [])[:3])
                comp_str = f" ({comps})" if mol_type == "ligand" else ""
                prompt += (
                    f"- Chain {cid} ({mol_type}{comp_str}): "
                    f"mean pLDDT={stats['mean']:.1f}, "
                    f"range=[{stats['min']:.1f}, {stats['max']:.1f}], "
                    f"{stats['atom_count']} atoms\n"
                )

        prompt += "\n**Confidence Distribution (Best Sample):**\n"
        if best.get("plddt_distribution"):
            dist = best["plddt_distribution"]
            prompt += (
                f"- Very High (90-100): {dist.get('very_high_confidence', 0)} atoms\n"
            )
            prompt += f"- High (70-90): {dist.get('high_confidence', 0)} atoms\n"
            prompt += f"- Low (50-70): {dist.get('low_confidence', 0)} atoms\n"
            prompt += f"- Very Low (<50): {dist.get('very_low_confidence', 0)} atoms\n"

        # Add ranking score statistics across all samples
        all_preds = summary_data.get("all_predictions_summary", [])
        if len(all_preds) > 1:
            all_scores = [p["ranking_score"] for p in all_preds]
            prompt += f"\n**Sample Diversity (all {len(all_preds)} samples):**\n"
            prompt += f"- Ranking score range: {min(all_scores):.4f} - {max(all_scores):.4f} (spread: {max(all_scores) - min(all_scores):.4f})\n"
            prompt += f"- Mean ranking score: {sum(all_scores) / len(all_scores):.4f}\n"

        prompt += "\n**Top 3 Samples Comparison:**\n"
        for i, pred in enumerate(top_preds, 1):
            clash_str = " [CLASH]" if pred.get("has_clash") else ""
            prompt += f"{i}. {pred['sample_name']}: ranking_score {pred['ranking_score']:.4f}, pTM {pred['ptm']:.4f}, ipTM {pred['iptm']:.4f}{clash_str}\n"

        # Add affinity results if available
        affinity = summary_data.get("summary", {}).get("affinity")
        if affinity and affinity.get("affinity_pred_value") is not None:
            prompt += "\n**Binding Affinity Prediction (Boltz-2 Affinity Model):**\n"
            prompt += f"- affinity_pred_value: {affinity['affinity_pred_value']:.3f} (log10 IC50 in μM)\n"
            prompt += f"- IC50: {affinity.get('ic50_nm', 0):.1f} nM ({affinity.get('ic50_um', 0):.4f} μM)\n"
            prompt += f"- pIC50: {affinity.get('pic50', 0):.3f}\n"
            prompt += f"- ΔG (estimated): {affinity.get('delta_g_kcal_mol', 0):.2f} kcal/mol\n"
            prompt += f"- Binding probability: {affinity.get('affinity_probability_binary', 0):.3f} ({affinity.get('binding_likelihood', 'N/A')})\n"
            prompt += f"- Binding classification: {affinity.get('binding_classification', 'N/A').replace('_', ' ').title()}\n"
            prompt += "Note: affinity_pred_value is from the ensemble model; validated primarily for competitive inhibitors.\n"

        # Add input info if available
        if summary["protein_info"].get("fasta_sequence"):
            fasta_seq = summary["protein_info"]["fasta_sequence"]
            fasta_header = summary["protein_info"].get("fasta_header", "Unknown")
            prompt += f"\n**Input Sequence:**\n>{fasta_header}\n{fasta_seq}\n"

        has_affinity = affinity is not None and affinity.get("affinity_pred_value") is not None
        prompt += f"""

**Expert Analysis Request:**

Provide a comprehensive assessment covering:

1. **Overall Prediction Quality**: Evaluate the ranking_score, pTM, ipTM, and pLDDT. How reliable is this prediction?
2. **Per-Chain Confidence Analysis**: Compare pLDDT across chains. For protein chains, identify well-folded vs disordered regions. For ligand chains, assess binding pose confidence — low ligand pLDDT suggests uncertain binding mode.
3. **Ligand Binding Assessment** (if ligand present): Evaluate the protein-ligand interface quality using chain_pair_iptm and per-chain pLDDT. Is the ligand binding pose reliable?
4. **Binding Affinity** {"(provided)" if has_affinity else "(not predicted)"}: {"Interpret the IC50, pIC50, ΔG, and binding probability. Contextualize affinity: is this drug-like (<100 nM), moderate (100 nM–1 μM), or weak (>1 μM)? Note any caveats." if has_affinity else "Affinity was not predicted in this run. Consider whether affinity prediction would add value."}
5. **Sample Consistency**: How consistent are the top-ranked diffusion samples across seeds? High variability in ligand poses suggests the binding site is uncertain.
6. **Interface Analysis** (if multi-chain): Interpret the chain_pair_iptm matrix. Which interfaces are confident? Which are uncertain?
7. **Clash Assessment**: If clashes are present, what might cause them and how to address them?
8. **Biological Insights**: What can we infer about binding specificity and mechanism from these predictions?
9. **Limitations & Next Steps**: Key caveats and recommended follow-up (experimental validation, docking studies, additional seeds, etc.)

IMPORTANT: Begin your response directly with the analysis. Do NOT include any preamble."""

        logger.info("Generating Gemini expert analysis...")

        content_parts = [prompt]

        # Include plot images if available
        if best.get("plots", {}).get("plddt_plot"):
            try:
                img_uri = best["plots"]["plddt_plot"]
                content_parts.append("\n\n**pLDDT Plot (Best Sample):**")
                content_parts.append(
                    types.Part.from_uri(file_uri=img_uri, mime_type="image/png")
                )
            except Exception as e:
                logger.warning(f"Could not include pLDDT plot: {e}")

        if best.get("plots", {}).get("pde_plot"):
            try:
                img_uri = best["plots"]["pde_plot"]
                content_parts.append("\n\n**PDE Plot (Best Sample):**")
                content_parts.append(
                    types.Part.from_uri(file_uri=img_uri, mime_type="image/png")
                )
            except Exception as e:
                logger.warning(f"Could not include PDE plot: {e}")

        if best.get("plots", {}).get("iptm_matrix_plot"):
            try:
                img_uri = best["plots"]["iptm_matrix_plot"]
                content_parts.append("\n\n**ipTM Matrix (Best Sample):**")
                content_parts.append(
                    types.Part.from_uri(file_uri=img_uri, mime_type="image/png")
                )
            except Exception as e:
                logger.warning(f"Could not include ipTM matrix plot: {e}")

        gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.1-pro-preview")
        project_id = os.environ["PROJECT_ID"]
        location = (
            "global"
            if "preview" in gemini_model
            else os.getenv("REGION", "us-central1")
        )
        logger.info(
            f"Initializing Google GenAI (Agent Platform): project={project_id}, location={location}, model={gemini_model}"
        )

        with genai.Client(
            vertexai=True, project=project_id, location=location
        ) as client:
            # Generate content using Gemini API
            response = client.models.generate_content(
                model=gemini_model,
                contents=content_parts,
                config=types.GenerateContentConfig(temperature=1.0),
            )

        disclaimer = (
            "\n\n---\n"
            "*This analysis was generated by AI (Google Gemini) and has not been "
            "peer-reviewed. It is intended as a starting point for interpretation, "
            "not as expert biological advice. Predictions should be validated "
            "experimentally by qualified scientists before use in research or "
            "clinical decisions.*"
        )

        return {
            "status": "success",
            "analysis": response.text + disclaimer,
            "model": gemini_model,
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            + "Z",
            "ai_generated": True,
        }

    except Exception as e:
        logger.error(f"Error generating Gemini analysis: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def parse_affinity(affinity_data: dict) -> dict:
    """Parse Boltz-2 affinity JSON and compute human-readable metrics.

    affinity_pred_value = log10(IC50 in μM)
    Conversions follow the official Boltz-2 documentation.
    """
    val = affinity_data.get("affinity_pred_value")
    prob = affinity_data.get("affinity_probability_binary")

    result = {
        "affinity_pred_value": val,
        "affinity_probability_binary": prob,
        "affinity_pred_value1": affinity_data.get("affinity_pred_value1"),
        "affinity_probability_binary1": affinity_data.get("affinity_probability_binary1"),
        "affinity_pred_value2": affinity_data.get("affinity_pred_value2"),
        "affinity_probability_binary2": affinity_data.get("affinity_probability_binary2"),
    }

    if val is not None:
        ic50_um = 10 ** val
        ic50_nm = ic50_um * 1000
        pic50 = 6.0 - val          # pIC50 = -log10(IC50_M) = 6 - log10(IC50_μM)
        delta_g = pic50 * 1.364    # ΔG ≈ pIC50 × RT·ln(10) at 300 K

        result.update({
            "ic50_um": round(ic50_um, 4),
            "ic50_nm": round(ic50_nm, 2),
            "pic50": round(pic50, 3),
            "delta_g_kcal_mol": round(delta_g, 3),
            # Classify binding strength by IC50
            "binding_classification": (
                "very_strong" if ic50_nm < 10 else
                "strong"      if ic50_nm < 100 else
                "moderate"    if ic50_nm < 1000 else
                "weak"        if ic50_nm < 10000 else
                "very_weak"
            ),
        })

    if prob is not None:
        result["binding_likelihood"] = (
            "high"     if prob > 0.8 else
            "moderate" if prob > 0.5 else
            "low"
        )

    return result


def consolidate_results(job_id: str, analysis_path: str, affinity_uri: str | None = None, query_yaml_uri: str | None = None):
    """Consolidate per-sample analysis results into summary.json."""
    import time

    logger.info(f"Consolidating results from {analysis_path}")
    time.sleep(2)

    if not analysis_path.startswith("gs://"):
        raise ValueError(f"Invalid analysis_path format: {analysis_path}")

    parts = analysis_path[5:].split("/", 1)
    bucket_name = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""

    if prefix and not prefix.endswith("/"):
        prefix += "/"
    if prefix.startswith("/"):
        prefix = prefix[1:]

    # List all per-sample analysis files
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    blobs = bucket.list_blobs(prefix=prefix)
    completed_files = []

    for blob in blobs:
        if "prediction_" in blob.name and blob.name.endswith("_analysis.json"):
            completed_files.append(f"gs://{bucket_name}/{blob.name}")

    logger.info(f"Found {len(completed_files)} analysis files to consolidate")

    # Read all analyses
    all_analyses = []
    for file_path in completed_files:
        try:
            analysis = download_json_from_gcs(file_path)
            all_analyses.append(analysis)
        except Exception as e:
            logger.error(f"Failed to read {file_path}: {e}")

    if not all_analyses:
        logger.error("No analyses found to consolidate")
        return

    # Get job metadata from Agent Platform
    all_labels = {}
    job_metadata = {"labels": {}, "parameters": {}}
    try:
        from google.cloud import aiplatform_v1 as vertex_ai

        project_id = os.environ["PROJECT_ID"]
        region = os.getenv("REGION", "us-central1")

        client = vertex_ai.PipelineServiceClient(
            client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"}
        )

        full_job_id = (
            job_id
            if job_id.startswith("projects/")
            else f"projects/{project_id}/locations/{region}/pipelineJobs/{job_id}"
        )
        request = vertex_ai.GetPipelineJobRequest(name=full_job_id)
        job = client.get_pipeline_job(request=request)

        all_labels = dict(job.labels) if hasattr(job, "labels") and job.labels else {}

        pipeline_parameters = {}
        if hasattr(job, "runtime_config") and hasattr(
            job.runtime_config, "parameter_values"
        ):
            # parameter_values is map<string, google.protobuf.Value> via the raw
            # aiplatform_v1 client. Values are proto Value messages, not Python
            # primitives — extract the appropriate field based on value type.
            for k, v in job.runtime_config.parameter_values.items():
                if hasattr(v, "string_value") and v.string_value:
                    pipeline_parameters[k] = v.string_value
                elif hasattr(v, "number_value") and v.number_value:
                    pipeline_parameters[k] = v.number_value
                elif hasattr(v, "bool_value"):
                    pipeline_parameters[k] = v.bool_value
                else:
                    pipeline_parameters[k] = str(v)

        job_metadata = {
            "display_name": job.display_name if hasattr(job, "display_name") else None,
            "state": job.state.name if hasattr(job, "state") else None,
            "labels": all_labels,
            "parameters": pipeline_parameters,
        }

        if hasattr(job, "create_time") and job.create_time:
            job_metadata["created"] = job.create_time.isoformat()
        if hasattr(job, "start_time") and job.start_time:
            job_metadata["started"] = job.start_time.isoformat()
        if hasattr(job, "end_time") and job.end_time:
            job_metadata["completed"] = job.end_time.isoformat()

        if (
            hasattr(job, "start_time")
            and hasattr(job, "end_time")
            and job.start_time
            and job.end_time
        ):
            duration = job.end_time - job.start_time
            total_seconds = duration.total_seconds()
            job_metadata["duration_seconds"] = total_seconds
            hours = int(total_seconds // 3600)
            minutes = int((total_seconds % 3600) // 60)
            job_metadata["duration_formatted"] = (
                f"{hours}h {minutes}m" if hours > 0 else f"{minutes:.1f}m"
            )

    except Exception as e:
        logger.error(f"Could not get job metadata: {e}", exc_info=True)

    # Load and parse affinity results if they were requested
    affinity_results = None
    if affinity_uri:
        try:
            affinity_raw = download_json_from_gcs(affinity_uri)
            affinity_results = parse_affinity(affinity_raw)
            logger.info(
                f"Affinity results: IC50={affinity_results.get('ic50_nm', 'N/A'):.1f} nM, "
                f"binding_prob={affinity_results.get('affinity_probability_binary', 'N/A'):.3f}"
            )
        except Exception as e:
            logger.warning(f"Could not load affinity results from {affinity_uri}: {e}")

    # Sort by ranking_score (descending — higher is better)
    all_analyses.sort(key=lambda x: x.get("ranking_score", 0), reverse=True)

    # Re-rank
    for i, analysis in enumerate(all_analyses):
        analysis["rank"] = i + 1

    best = all_analyses[0]

    # Calculate summary statistics
    ranking_scores = [a["ranking_score"] for a in all_analyses]
    plddt_means = [a["plddt_mean"] for a in all_analyses]

    # Try to get input sequence from the uploaded Boltz-2 YAML query.
    # Primary: use the URI resolved by the agent at job dispatch time (task_config).
    # Fallback: reconstruct from the query_name label if primary is absent.
    fasta_sequence = None
    fasta_header = None
    input_query_yaml = None
    try:
        import yaml

        # Primary: query_yaml_uri was resolved by analyze_job.py before launching the
        # Cloud Run Job.  It is the exact GCS path written by submit_prediction.py.
        query_yaml_path = query_yaml_uri

        # Fallback: derive from query_name label (preserves backward compat for older
        # task_config files that don't have query_yaml_uri)
        if not query_yaml_path:
            query_name_label = all_labels.get("query_name", "")
            bucket_name_from_analysis = analysis_path[5:].split("/", 1)[0]
            query_yaml_path = (
                f"gs://{bucket_name_from_analysis}/boltz2_queries/{query_name_label}.yaml"
                if query_name_label else None
            )
            logger.info(f"Query YAML fallback: label={query_name_label!r} path={query_yaml_path!r} all_labels_keys={list(all_labels.keys())}")
        else:
            logger.info(f"Query YAML primary path from task_config: {query_yaml_path!r}")

        if query_yaml_path:
            raw_yaml = download_text_from_gcs(query_yaml_path)
            query_data = yaml.safe_load(raw_yaml)
            input_query_yaml = raw_yaml  # Store raw YAML text so viewer displays it as YAML, not JSON
            logger.info(f"Loaded input query YAML from {query_yaml_path}")

            # Extract protein sequences for display
            seqs = []
            for entry in query_data.get("sequences", []):
                if "protein" in entry:
                    seq_id = entry["protein"].get("id", "A")
                    if isinstance(seq_id, list):
                        seq_id = seq_id[0]
                    if not fasta_header:
                        fasta_header = str(seq_id)
                    seqs.append(entry["protein"].get("sequence", ""))
            fasta_sequence = "/".join(seqs) if seqs else None
    except Exception as e:
        logger.warning(f"Could not load input sequence: {e}")

    # Build chain composition from best prediction's chain_info
    chain_composition = []
    best_chain_info = best.get("chain_info", [])
    total_residues = 0
    for ci in best_chain_info:
        total_residues += ci["residue_count"]
        entry = {
            "chain_id": ci["chain_id"],
            "molecule_type": ci["molecule_type"],
            "residue_count": ci["residue_count"],
            "atom_count": ci["atom_count"],
        }
        if ci["molecule_type"] == "ligand":
            entry["comp_ids"] = ci["comp_ids"]
        chain_composition.append(entry)

    summary = {
        "protein_info": {
            "sequence_length": total_residues
            if total_residues > 0
            else len(best.get("plddt_scores", [])),
            "total_predictions": len(all_analyses),
            "fasta_sequence": fasta_sequence,
            "fasta_header": fasta_header,
            "job_metadata": job_metadata,
            "model_type": "boltz2",
            "chain_composition": chain_composition,
            "input_query_yaml": input_query_yaml,
        },
        "quality_metrics": {
            "best_ranking_score": best["ranking_score"],
            "best_ptm": best["ptm"],
            "best_iptm": best["iptm"],
            "best_plddt": best["plddt_mean"],
            "quality_assessment": best["quality_assessment"],
        },
        "affinity": affinity_results,  # None if affinity was not predicted
        "statistics": {
            "total_predictions": len(all_analyses),
            "analyzed_successfully": len(all_analyses),
            "ranking_score_stats": {
                "mean": sum(ranking_scores) / len(ranking_scores),
                "min": min(ranking_scores),
                "max": max(ranking_scores),
                "range": max(ranking_scores) - min(ranking_scores),
            },
            "plddt_stats": {
                "mean": sum(plddt_means) / len(plddt_means),
                "min": min(plddt_means),
                "max": max(plddt_means),
                "range": max(plddt_means) - min(plddt_means),
            },
        },
    }

    summary_data = {
        "job_id": job_id,
        "model_type": "boltz2",
        "analyzed_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        + "Z",
        "summary": summary,
        "best_prediction": best,
        "top_predictions": all_analyses[:10],
        "all_predictions_summary": [
            {
                "rank": a["rank"],
                "sample_name": a["sample_name"],
                "ranking_score": a["ranking_score"],
                "ptm": a["ptm"],
                "iptm": a["iptm"],
                "plddt_mean": a["plddt_mean"],
                "plddt_min": a["plddt_min"],
                "quality_assessment": a["quality_assessment"],
                "has_clash": a.get("has_clash", 0),
                "per_chain_plddt": a.get("per_chain_plddt", {}),
                "chain_info": a.get("chain_info", []),
                "cif_uri": a["cif_uri"],
                "plots": a.get("plots", {}),
            }
            for a in all_analyses
        ],
    }

    # Generate Gemini expert analysis
    logger.info("Generating Gemini expert analysis...")
    expert_analysis = generate_gemini_expert_analysis(summary_data)
    summary_data["expert_analysis"] = expert_analysis

    if expert_analysis["status"] == "success":
        logger.info("Expert analysis generated successfully")
    else:
        logger.error(
            f"Expert analysis failed: {expert_analysis.get('error', 'Unknown')}"
        )

    # Write summary to GCS
    summary_uri = f"{analysis_path}summary.json"
    local_summary = "/tmp/summary.json"

    with open(local_summary, "w") as f:
        json.dump(summary_data, f, indent=2)

    upload_to_gcs(local_summary, summary_uri)
    os.remove(local_summary)

    logger.info(f"Consolidation complete! Summary written to {summary_uri}")
    logger.info(
        f"   Best sample: {best['sample_name']} (ranking_score: {best['ranking_score']:.4f})"
    )
    logger.info(f"   Labels captured: {list(all_labels.keys())}")


if __name__ == "__main__":
    main()
