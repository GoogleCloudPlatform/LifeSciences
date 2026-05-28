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

"""Shared GCS, mathematical, and assessment utilities for FoldRun prediction analysis."""

import json
import logging
import numpy as np
import time
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
from google.cloud import storage

logger = logging.getLogger(__name__)

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


def download_text_from_gcs(gcs_uri: str) -> str:
    """Download text file from GCS."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")
    parts = gcs_uri[5:].split("/", 1)
    storage_client = storage.Client()
    bucket = storage_client.bucket(parts[0])
    blob = bucket.blob(parts[1] if len(parts) > 1 else "")
    return blob.download_as_text()


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
    """Parse the _atom_site loop header to build a column-name -> index map.

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
        elif in_atom_site_loop and (
            stripped.startswith("ATOM") or stripped.startswith("HETATM")
        ):
            break  # data starts — column list is complete
        elif in_atom_site_loop and stripped and not stripped.startswith("_atom_site."):
            if not stripped.startswith("ATOM") and not stripped.startswith("HETATM"):
                in_atom_site_loop = False

    return {name: idx for idx, name in enumerate(col_names)}


def parse_cif_chains(cif_text: str, extract_plddt: bool = False) -> tuple[list[dict], list[float]]:
    """Parse CIF atom_site records to extract per-chain info and optionally per-atom pLDDT.

    Supports dynamic index lookup based on loop headers, with a fallback to
    standard OpenFold3 hardcoded indices when loop headers are absent.
    """
    col_map = _detect_atom_site_columns(cif_text)
    
    # Fallback to OF3 hardcoded indices if header is missing
    if col_map:
        chain_col = col_map.get("auth_asym_id", 11)
        comp_col = col_map.get("auth_comp_id", 10)
        seq_col = col_map.get("auth_seq_id", 9)
        bfactor_col = col_map.get("B_iso_or_equiv")
    else:
        chain_col = 11
        comp_col = 10
        seq_col = 9
        bfactor_col = None

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

        if extract_plddt and bfactor_col is not None and bfactor_col < len(parts):
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
        standard_aa = {
            "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
            "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL"
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


def plot_plddt_distribution(
    plddt_scores: list,
    title_name: str,
    output_path: str,
    chain_info: list[dict] = None,
) -> None:
    """Generate unified pLDDT distribution plot (supporting chain coloring & boundaries)."""
    scores = np.array(plddt_scores)
    fig, ax = plt.subplots(figsize=(12, 4))
    residues = np.arange(1, len(scores) + 1)

    # If chain info available, color each chain differently
    if chain_info and len(chain_info) > 1:
        chain_colors = [
            "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
            "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"
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

    # X-label varies if we have atom-level (chain_info) vs residue-level (AF2)
    x_label = "Atom Position" if chain_info else "Residue Position"
    ax.set_xlabel(x_label, fontsize=10)
    ax.set_ylabel("pLDDT Score", fontsize=10)
    ax.set_title(
        f"Per-Residue Confidence (pLDDT) - {title_name}" if not chain_info
        else f"Per-Atom Confidence (pLDDT) - {title_name}",
        fontsize=11,
        fontweight="bold",
    )
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.3, linestyle="--")
    if chain_info:
        ax.legend(loc="lower right", fontsize=7)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Generated pLDDT plot: {output_path}")


def plot_error_matrix(
    matrix: list,
    sample_name: str,
    label: str,
    output_path: str,
    chain_info: list[dict] = None,
    max_value: float = None,
) -> None:
    """Generate error matrix (PDE/PAE) heatmap with optional chain gridlines."""
    data = np.array(matrix)
    fig, ax = plt.subplots(figsize=(8, 7))

    cmap = plt.cm.Greens_r
    vmax = max_value if max_value is not None else np.max(data)
    im = ax.imshow(data, vmin=0, vmax=vmax, cmap=cmap)

    # Draw chain boundary lines on heatmap if multi-chain
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
                mid = offset + n / 2
                ax.text(
                    -2, mid, f"{ci['chain_id']}", ha="right", va="center",
                    fontsize=9, fontweight="bold", color="#333"
                )
            else:
                mid = offset + n / 2
                ax.text(
                    -2, mid, f"{ci['chain_id']}", ha="right", va="center",
                    fontsize=9, fontweight="bold", color="#333"
                )
            offset += n

    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label(label, rotation=270, labelpad=20, fontsize=10)

    ax.set_xlabel("Scored Residue", fontsize=10)
    ax.set_ylabel("Aligned Residue", fontsize=10)
    ax.set_title(
        f"{label.split('(')[0].strip()} - {sample_name}",
        fontsize=11,
        fontweight="bold",
    )

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Generated error matrix plot: {output_path}")


def plot_iptm_matrix(
    chain_pair_iptm: dict,
    sample_name: str,
    output_path: str,
    chain_ptm: dict = None,
    chain_info: list = None,
) -> None:
    """Generate chain x chain ipTM heatmap with per-chain pTM on diagonal."""
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
                matrix[i, j] = chain_ptm[c1]
            else:
                matrix[i, j] = pairs.get((c1, c2), pairs.get((c2, c1), 0.0))

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


def wait_for_sibling_tasks(
    bucket_name: str,
    prefix: str,
    task_count: int,
    max_wait: int = 120,
    interval: int = 2,
) -> bool:
    """Wait for all other parallel task analysis files to be written to GCS."""
    waited = 0
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    
    # Ensure prefix doesn't start with / but ends with /
    prefix_clean = prefix.replace(f"gs://{bucket_name}/", "")
    if prefix_clean.startswith("/"):
        prefix_clean = prefix_clean[1:]

    while waited < max_wait:
        blobs = bucket.list_blobs(prefix=prefix_clean)
        completed_files = [
            b.name
            for b in blobs
            if "prediction_" in b.name and b.name.endswith("_analysis.json")
        ]

        if len(completed_files) >= task_count:
            logger.info(f"All {task_count} sibling task analysis files found successfully")
            return True

        logger.info(f"Waiting for sibling tasks... ({len(completed_files)}/{task_count} files found)")
        time.sleep(interval)
        waited += interval

    logger.warning(f"Timeout waiting for sibling tasks. Proceeding consolidation with {len(completed_files)}/{task_count} files.")
    return False


def calculate_per_chain_plddt(plddt_scores: list, chain_info: list[dict]) -> dict:
    """Calculate per-chain pLDDT statistics."""
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
