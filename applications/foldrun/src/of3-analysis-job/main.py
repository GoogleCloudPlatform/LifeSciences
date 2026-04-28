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

"""Cloud Run Job for analyzing OpenFold3 predictions in parallel.

OF3 outputs JSON (not pickle like AF2), so this job is lighter:
no jax, no dm-tree, no pickle. Reads *_confidences.json and
*_confidences_aggregated.json to produce plots and Gemini analysis.
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

from gcs_retry import GCS_RETRY

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
    blob.download_to_filename(local_path, retry=GCS_RETRY)

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
    blob.upload_from_filename(local_path, retry=GCS_RETRY)

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

    content = blob.download_as_string(retry=GCS_RETRY)
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

    return blob.download_as_bytes(retry=GCS_RETRY)


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


def parse_cif_chains(cif_text: str) -> list[dict]:
    """Parse CIF atom_site records to extract per-chain info.

    Returns list of dicts sorted by chain order:
        [{'chain_id': 'A', 'atom_count': 601, 'residue_count': 76,
          'comp_ids': {'MET','GLN',...}, 'molecule_type': 'protein'}, ...]
    """
    chains = {}  # chain_id -> {atoms, residues set, comp_ids set}
    chain_order = []

    for line in cif_text.split("\n"):
        if not (line.startswith("ATOM") or line.startswith("HETATM")):
            continue
        parts = line.split()
        if len(parts) < 12:
            continue
        chain_id = parts[11]  # auth_asym_id
        comp_id = parts[10]  # auth_comp_id
        seq_id = parts[9]  # auth_seq_id

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

    return result


def download_text_from_gcs(gcs_uri: str) -> str:
    """Download text file from GCS."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")
    parts = gcs_uri[5:].split("/", 1)
    storage_client = storage.Client()
    bucket = storage_client.bucket(parts[0])
    blob = bucket.blob(parts[1] if len(parts) > 1 else "")
    return blob.download_as_text(retry=GCS_RETRY)


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

    sample_name = pred["sample_name"]
    cif_uri = pred["cif_uri"]
    confidences_uri = pred["confidences_uri"]
    aggregated_uri = pred["aggregated_uri"]
    output_uri = pred["output_uri"]

    logger.info(f"Starting analysis for sample: {sample_name}")
    logger.info(f"Job ID: {job_id}")

    try:
        # Load aggregated confidences (summary scores)
        logger.info("Loading aggregated confidences...")
        aggregated = download_json_from_gcs(aggregated_uri)

        # Load per-residue confidences
        logger.info("Loading per-residue confidences...")
        confidences = download_json_from_gcs(confidences_uri)

        # Parse CIF to get chain info (atom counts, molecule types)
        logger.info("Parsing CIF for chain info...")
        chain_info = []
        try:
            cif_text = download_text_from_gcs(cif_uri)
            chain_info = parse_cif_chains(cif_text)
            for ci in chain_info:
                logger.info(
                    f"  Chain {ci['chain_id']}: {ci['molecule_type']}, "
                    f"{ci['atom_count']} atoms, {ci['residue_count']} residues, "
                    f"comps: {ci['comp_ids'][:5]}"
                )
        except Exception as e:
            logger.warning(f"Could not parse CIF for chain info: {e}")

        # Extract metrics from aggregated
        ranking_score = aggregated.get(
            "sample_ranking_score", aggregated.get("ranking_score", 0.0)
        )
        ptm = aggregated.get("ptm", 0.0)
        iptm = aggregated.get("iptm", 0.0)
        avg_plddt = aggregated.get("avg_plddt", 0.0)
        gpde = aggregated.get("gpde", None)
        has_clash = aggregated.get("has_clash", 0)
        disorder = aggregated.get("disorder", 0)
        chain_ptm = aggregated.get("chain_ptm", {})
        chain_pair_iptm = aggregated.get("chain_pair_iptm", {})

        # Get per-atom pLDDT from confidences
        plddt_scores = confidences.get("plddt", [])
        pde_matrix = confidences.get("pde", None)

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
                blobs = bucket_obj.list_blobs(prefix=analysis_prefix, retry=GCS_RETRY)
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
                consolidate_results(job_id, analysis_path)
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

        prompt = f"""You are an expert structural biologist reviewing OpenFold3 prediction results.

**OpenFold3 Reference (use to interpret results):**
- Diffusion-based structure prediction model (similar to AlphaFold3). Supports proteins, RNA, DNA, and ligands.
- Generates multiple diffusion samples per seed. Seeds = independent weight initializations (different conformations). Samples = different denoising trajectories from same seed (variations on a theme).
- **Metric interpretation thresholds:**
  - sample_ranking_score (0-1): Overall quality. >0.7 = good, >0.8 = very good.
  - pTM (0-1): Global fold accuracy. >0.8 = high confidence fold. >0.9 = very high.
  - ipTM (0-1): Interface quality for complexes. >0.7 = reliable interface. >0.8 = high confidence.
  - pLDDT (0-100): Per-atom confidence. >80 = good, >90 = very high. <50 in a region = likely disordered (real biology, not prediction failure).
  - gpde: Global predicted distance error. Lower = better. Complements pTM.
  - has_clash: Steric clash detected. 0 = clean, 1 = problem (try more seeds).
- **Key interpretation patterns:**
  - ipTM low but pTM high → individual chains fold well but interface/binding pose is uncertain
  - Low ligand pLDDT (e.g. <60) → binding mode is uncertain, try more seeds/samples
  - High variance in ranking_score across samples → conformational flexibility or poorly defined binding site
  - Chain pTM on diagonal of ipTM matrix shows per-chain fold quality independently of interface

**Prediction Summary:**
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

        # Add input info if available
        if summary["protein_info"].get("fasta_sequence"):
            fasta_seq = summary["protein_info"]["fasta_sequence"]
            fasta_header = summary["protein_info"].get("fasta_header", "Unknown")
            prompt += f"\n**Input Sequence:**\n>{fasta_header}\n{fasta_seq}\n"

        prompt += """

**Expert Analysis Request:**

Provide a comprehensive assessment covering:

1. **Overall Prediction Quality**: Evaluate the ranking_score, pTM, ipTM, and pLDDT. How reliable is this prediction?
2. **Per-Chain Confidence Analysis**: Compare pLDDT across chains. For protein chains, identify well-folded vs disordered regions. For ligand chains, assess binding pose confidence — low ligand pLDDT suggests uncertain binding mode.
3. **Ligand Binding Assessment** (if ligand present): Evaluate the protein-ligand interface quality using chain_pair_iptm and per-chain pLDDT. Is the ligand binding pose reliable? What does the protein-ligand PDE/PAE tell us about the binding site geometry?
4. **Sample Consistency**: How consistent are the top-ranked diffusion samples across seeds? High variability in ligand poses specifically suggests the binding site is uncertain.
5. **Interface Analysis** (if multi-chain): Interpret the chain_pair_iptm matrix. Which interfaces are confident? Which are uncertain?
6. **Clash Assessment**: If clashes are present, what might cause them and how to address them?
7. **Biological Insights**: What can we infer about binding affinity, specificity, and mechanism from these predictions?
8. **Limitations & Next Steps**: Key caveats and recommended follow-up (experimental validation, docking studies, additional seeds, etc.)

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
            f"Initializing Google GenAI (Vertex AI): project={project_id}, location={location}, model={gemini_model}"
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


def consolidate_results(job_id: str, analysis_path: str):
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

    blobs = bucket.list_blobs(prefix=prefix, retry=GCS_RETRY)
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

    # Get job metadata from Vertex AI
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
            pipeline_parameters = dict(job.runtime_config.parameter_values)

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

    # Sort by ranking_score (descending — higher is better)
    all_analyses.sort(key=lambda x: x.get("ranking_score", 0), reverse=True)

    # Re-rank
    for i, analysis in enumerate(all_analyses):
        analysis["rank"] = i + 1

    best = all_analyses[0]

    # Calculate summary statistics
    ranking_scores = [a["ranking_score"] for a in all_analyses]
    plddt_means = [a["plddt_mean"] for a in all_analyses]

    # Try to get input sequence and raw query JSON
    fasta_sequence = None
    fasta_header = None
    input_query_json = None
    try:
        if hasattr(job, "runtime_config") and hasattr(
            job.runtime_config, "parameter_values"
        ):
            query_json_path = job.runtime_config.parameter_values.get("query_json_path")
            if query_json_path and isinstance(query_json_path, str):
                query_data = download_json_from_gcs(query_json_path)
                queries = query_data.get("queries", {})
                if queries:
                    query_name = list(queries.keys())[0]
                    fasta_header = query_name
                    chains = queries[query_name].get("chains", [])
                    seqs = [
                        c.get("sequence", "")
                        for c in chains
                        if c.get("molecule_type") == "protein"
                    ]
                    fasta_sequence = "/".join(seqs) if seqs else None

                    # Build clean query JSON for resubmission (strip internal paths)
                    clean_queries = {}
                    for qname, qdata in queries.items():
                        clean_chains = []
                        for chain in qdata.get("chains", []):
                            clean_chain = {
                                "molecule_type": chain.get("molecule_type"),
                                "chain_ids": chain.get("chain_ids"),
                            }
                            if chain.get("sequence"):
                                clean_chain["sequence"] = chain["sequence"]
                            if chain.get("smiles"):
                                clean_chain["smiles"] = chain["smiles"]
                            if chain.get("ccd_codes"):
                                clean_chain["ccd_codes"] = chain["ccd_codes"]
                            clean_chains.append(clean_chain)
                        clean_queries[qname] = {"chains": clean_chains}
                    input_query_json = {"queries": clean_queries}
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
            "model_type": "openfold3",
            "chain_composition": chain_composition,
            "input_query_json": input_query_json,
        },
        "quality_metrics": {
            "best_ranking_score": best["ranking_score"],
            "best_ptm": best["ptm"],
            "best_iptm": best["iptm"],
            "best_plddt": best["plddt_mean"],
            "quality_assessment": best["quality_assessment"],
        },
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
        "model_type": "openfold3",
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
