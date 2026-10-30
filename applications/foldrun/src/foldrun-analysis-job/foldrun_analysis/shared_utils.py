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

import io
import json
import logging
import math
import os
import tempfile
import time
from typing import Any

import numpy as np

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:  # pragma: no cover - optional in lightweight unit test envs
    matplotlib = None  # type: ignore[assignment]
    plt = None  # type: ignore[assignment]

try:
    import seaborn as sns
except ImportError:  # pragma: no cover - optional in lightweight unit test envs
    sns = None  # type: ignore[assignment]

from google.cloud import storage

logger = logging.getLogger(__name__)

MAX_ZST_DECOMPRESSED_BYTES = 512 * 1024 * 1024  # 512 MB cap against zip bombs

PLDDT_BANDS = [
    (0, 50, "very_low_confidence"),
    (50, 70, "low_confidence"),
    (70, 90, "high_confidence"),
    (90, 100, "very_high_confidence"),
]


def _sanitize_data(obj: Any) -> Any:
    """Recursively sanitize NaN and Inf floats into None (JSON null).

    Never fabricates placeholder confidence scores (such as 80.0 or 82.0).
    """
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, np.floating):
        val = float(obj)
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.ndarray):
        return _sanitize_data(obj.tolist())
    if isinstance(obj, dict):
        return {k: _sanitize_data(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_data(x) for x in obj]
    return obj


_sanitize_json_floats = _sanitize_data


def validate_gcs_uri_in_bucket(
    uri: str, expected_bucket: str | None = None
) -> tuple[str, str]:
    """Validate a GCS URI and enforce optional bucket confinement.

    Rejects non-gs:// schemes, null bytes, empty bucket/object paths,
    path traversal segments ('..'), and cross-bucket URIs when
    expected_bucket is provided.

    Returns:
        (bucket_name, blob_path)
    """
    if not uri or not isinstance(uri, str) or "\x00" in uri:
        raise ValueError(f"Invalid GCS URI: {uri!r}")
    if not uri.startswith("gs://"):
        raise ValueError(f"URI must start with 'gs://', got: {uri!r}")
    remainder = uri.removeprefix("gs://")
    bucket, _, blob_path = remainder.partition("/")
    if not bucket or not blob_path:
        raise ValueError(f"Malformed GCS URI (missing bucket or object): {uri!r}")
    if blob_path.startswith("/") or ".." in blob_path.split("/"):
        raise ValueError(f"Path traversal detected in GCS URI: {uri!r}")
    if expected_bucket and bucket != expected_bucket:
        raise ValueError(
            f"Unauthorized GCS bucket '{bucket}' in URI {uri!r}; expected '{expected_bucket}'"
        )
    return bucket, blob_path


def decompress_zst_bytes(
    raw_bytes: bytes, max_bytes: int = MAX_ZST_DECOMPRESSED_BYTES
) -> bytes:
    """Decompress zstandard-compressed bytes with a strict size limit."""
    import zstandard as zstd

    dctx = zstd.ZstdDecompressor()
    chunks: list[bytes] = []
    total = 0
    with dctx.stream_reader(io.BytesIO(raw_bytes)) as reader:
        while True:
            chunk = reader.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(
                    f"Decompressed .zst payload exceeds safety limit of {max_bytes} bytes"
                )
            chunks.append(chunk)
    return b"".join(chunks)


def download_from_gcs(
    gcs_uri: str, local_path: str, expected_bucket: str | None = None
) -> None:
    """Download file from GCS."""
    bucket_name, blob_name = validate_gcs_uri_in_bucket(gcs_uri, expected_bucket)

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.reload()
    blob.download_to_filename(local_path)

    size_mb = blob.size / 1024 / 1024 if blob.size else 0
    logger.info(f"Downloaded {gcs_uri} ({size_mb:.2f} MB)")


def upload_to_gcs(
    local_path: str,
    gcs_uri: str,
    content_type: str | None = None,
    expected_bucket: str | None = None,
) -> None:
    """Upload file to GCS."""
    bucket_name, blob_name = validate_gcs_uri_in_bucket(gcs_uri, expected_bucket)

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    if content_type:
        blob.upload_from_filename(local_path, content_type=content_type)
    else:
        blob.upload_from_filename(local_path)
    logger.info(f"Uploaded {local_path} to {gcs_uri}")


def download_json_from_gcs(
    gcs_uri: str, expected_bucket: str | None = None
) -> dict[str, Any]:
    """Download and parse JSON file from GCS (handling .json and .json.zst)."""
    bucket_name, blob_name = validate_gcs_uri_in_bucket(gcs_uri, expected_bucket)

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    if gcs_uri.endswith(".zst"):
        raw_bytes = blob.download_as_bytes()
        content = decompress_zst_bytes(raw_bytes).decode("utf-8")
    else:
        content = blob.download_as_text()
    parsed = json.loads(content, parse_constant=lambda _: None)
    return _sanitize_data(parsed)


def download_text_from_gcs(gcs_uri: str, expected_bucket: str | None = None) -> str:
    """Download text file from GCS (handling plain text and .zst)."""
    bucket_name, blob_name = validate_gcs_uri_in_bucket(gcs_uri, expected_bucket)
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    if gcs_uri.endswith(".zst"):
        raw_bytes = blob.download_as_bytes()
        return decompress_zst_bytes(raw_bytes).decode("utf-8")
    return blob.download_as_text()


def download_image_from_gcs(gcs_uri: str, expected_bucket: str | None = None) -> bytes:
    """Download image bytes from GCS."""
    bucket_name, blob_name = validate_gcs_uri_in_bucket(gcs_uri, expected_bucket)

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    return blob.download_as_bytes()


def calculate_plddt_stats(plddt_scores: list | np.ndarray | None) -> dict[str, Any]:
    """Calculate pLDDT statistics from per-residue or per-atom array.

    Filters out None, NaN, and Inf values. When no valid scores exist, returns
    None statistics and confidence_category='uncomputed' rather than fabricating
    placeholder confidence values.
    """
    empty_result: dict[str, Any] = {
        "mean": None,
        "median": None,
        "min": None,
        "max": None,
        "std": None,
        "confidence_category": "uncomputed",
        "per_residue": [],
        "distribution": {label: 0 for _, _, label in PLDDT_BANDS},
    }
    if plddt_scores is None:
        return empty_result

    valid_vals: list[float] = []
    for item in plddt_scores:
        if item is None:
            continue
        try:
            fval = float(item)
        except (TypeError, ValueError):
            continue
        if not (math.isnan(fval) or math.isinf(fval)):
            valid_vals.append(fval)

    if not valid_vals:
        return empty_result

    scores = np.array(valid_vals, dtype=float)
    if 0.0 < np.max(scores) <= 1.0:
        scores = scores * 100.0
    score_list = scores.tolist()

    mean_val = float(np.mean(scores))
    stats: dict[str, Any] = {
        "mean": mean_val,
        "median": float(np.median(scores)),
        "min": float(np.min(scores)),
        "max": float(np.max(scores)),
        "std": float(np.std(scores)),
        "confidence_category": get_quality_assessment(mean_val),
        "per_residue": score_list,
    }

    # Distribution by confidence band
    distribution = {}
    for min_val, max_val, label in PLDDT_BANDS:
        count = int(np.sum((scores >= min_val) & (scores <= max_val)))
        distribution[label] = count

    stats["distribution"] = distribution
    return stats


def get_quality_assessment(plddt_mean: float | None) -> str:
    """Get quality assessment based on mean pLDDT."""
    if plddt_mean is None:
        return "uncomputed"
    try:
        val = float(plddt_mean)
    except (TypeError, ValueError):
        return "uncomputed"
    if math.isnan(val) or math.isinf(val):
        return "uncomputed"
    if 0.0 < val <= 1.0:
        val = val * 100.0
    if val >= 90:
        return "very_high_confidence"
    elif val >= 70:
        return "high_confidence"
    elif val >= 50:
        return "low_confidence"
    else:
        return "very_low_confidence"


def _detect_atom_site_columns(cif_text: str) -> dict[str, int]:
    """Parse the _atom_site loop header to build a column-name -> index map.

    Returns e.g. {'group_PDB': 0, 'id': 1, ..., 'B_iso_or_equiv': 14, ...}
    """
    col_names: list[str] = []
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


def _is_fixed_width_pdb_line(line: str) -> bool:
    """Determine if an ATOM/HETATM line uses fixed-width PDB column formatting.

    Fixed-width PDB records use columns 1-6 ('ATOM  ' or 'HETATM'), integer serial
    in columns 7-11, residue name in 18-20, chain in 22, resSeq in 23-26, and three
    8.3 orthogonal coordinates in columns 31-54, without mmCIF '.' or '?' tokens.
    """
    if len(line) < 54:
        return False
    if line[:6] not in ("ATOM  ", "HETATM"):
        return False
    if not line[6:11].strip().isdigit():
        return False
    parts = line.split()
    if "." in parts or "?" in parts:
        return False
    if not line[17:20].strip() or not line[22:26].strip().lstrip("-").isdigit():
        return False
    try:
        float(line[30:38])
        float(line[38:46])
        float(line[46:54])
    except ValueError:
        return False
    return True


def _infer_headerless_cif_columns(
    parts: list[str],
) -> tuple[int, int, int, int | None]:
    """Infer (chain_col, comp_col, seq_col, bfactor_col) for headerless mmCIF lines."""
    if len(parts) >= 10 and parts[4] in (".", "?"):
        bfactor_col = 14 if len(parts) > 14 else None
        if len(parts) >= 19 and parts[9] in (".", "?") and parts[15] in (".", "?", "0"):
            return 18, 17, 16, bfactor_col
        return 6, 5, 8, bfactor_col

    # OpenFold3 compact 12-column headerless layout:
    # ATOM 1 N N MET A 1 1.0 2.0 1 MET A
    return 11, 10, 9, None


def parse_cif_chains(
    cif_text: str, extract_plddt: bool = False
) -> tuple[list[dict[str, Any]], list[float]]:
    """Parse CIF or PDB atom_site records to extract per-chain info and optionally per-atom pLDDT.

    Supports dynamic index lookup based on mmCIF loop headers, standard PDB fixed-width
    records, standard headerless PDBx/mmCIF records, and OpenFold3 compact CIF records.
    """
    col_map = _detect_atom_site_columns(cif_text)
    has_cif_markers = bool(col_map) or "loop_" in cif_text

    first_atom_line: str | None = None
    for raw_line in cif_text.split("\n"):
        if raw_line.startswith("ATOM") or raw_line.startswith("HETATM"):
            first_atom_line = raw_line
            break

    is_pdb = (
        not has_cif_markers
        and first_atom_line is not None
        and _is_fixed_width_pdb_line(first_atom_line)
    )

    if col_map:
        chain_col = col_map.get("auth_asym_id", col_map.get("label_asym_id", 11))
        comp_col = col_map.get("auth_comp_id", col_map.get("label_comp_id", 10))
        seq_col = col_map.get("auth_seq_id", col_map.get("label_seq_id", 9))
        bfactor_col: int | None = col_map.get("B_iso_or_equiv")
    elif not is_pdb and first_atom_line is not None:
        chain_col, comp_col, seq_col, bfactor_col = _infer_headerless_cif_columns(
            first_atom_line.split()
        )
    else:
        chain_col = 11
        comp_col = 10
        seq_col = 9
        bfactor_col = None

    chains: dict[str, dict[str, Any]] = {}
    chain_order: list[str] = []
    raw_plddt_scores: list[float | None] = []

    for line in cif_text.split("\n"):
        if not (line.startswith("ATOM") or line.startswith("HETATM")):
            continue
        if is_pdb:
            if len(line) < 26:
                continue
            comp_id = line[17:20].strip()
            chain_id = line[21:22].strip() or "A"
            seq_id = line[22:26].strip()
            if extract_plddt and len(line) >= 66:
                token = line[60:66].strip()
                try:
                    val = float(token)
                    if math.isnan(val) or math.isinf(val):
                        raw_plddt_scores.append(None)
                    else:
                        raw_plddt_scores.append(val)
                except ValueError:
                    raw_plddt_scores.append(None)
        else:
            parts = line.split()
            if len(parts) <= max(chain_col, comp_col, seq_col):
                continue
            chain_id = parts[chain_col]
            comp_id = parts[comp_col]
            seq_id = parts[seq_col]

            if extract_plddt and bfactor_col is not None and bfactor_col < len(parts):
                token = parts[bfactor_col]
                if token in (".", "?"):
                    raw_plddt_scores.append(None)
                else:
                    try:
                        val = float(token)
                        if math.isnan(val) or math.isinf(val):
                            raw_plddt_scores.append(None)
                        else:
                            raw_plddt_scores.append(val)
                    except ValueError:
                        raw_plddt_scores.append(None)

        if chain_id not in chains:
            chains[chain_id] = {"atoms": 0, "residues": set(), "comp_ids": set()}
            chain_order.append(chain_id)
        chains[chain_id]["atoms"] += 1
        chains[chain_id]["residues"].add(seq_id)
        chains[chain_id]["comp_ids"].add(comp_id)

    valid_only = [s for s in raw_plddt_scores if s is not None]
    if not valid_only:
        plddt_scores: list[Any] = []
    else:
        scale = 100.0 if 0.0 < max(valid_only) <= 1.0 else 1.0
        if len(valid_only) == len(raw_plddt_scores):
            plddt_scores = [
                round(s * scale, 2) if scale != 1.0 else s for s in valid_only
            ]
        else:
            plddt_scores = [
                (round(s * scale, 2) if scale != 1.0 else s) if s is not None else None
                for s in raw_plddt_scores
            ]

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

    result: list[dict[str, Any]] = []
    for cid in chain_order:
        info = chains[cid]
        comp_ids = info["comp_ids"]
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
    chain_info: list[dict] | None = None,
    expected_bucket: str | None = None,
) -> None:
    """Generate unified pLDDT distribution plot (supporting chain coloring & boundaries)."""
    if plt is None:
        logger.warning("matplotlib not available; skipping pLDDT plot generation")
        return

    if not plddt_scores:
        return

    scores = np.array(
        [np.nan if x is None else float(x) for x in plddt_scores], dtype=float
    )
    finite_mask = ~np.isnan(scores) & ~np.isinf(scores)
    if not np.any(finite_mask):
        return
    if 0.0 < np.nanmax(scores[finite_mask]) <= 1.0:
        scores = scores * 100.0

    fig, ax = plt.subplots(figsize=(12, 4))
    residues = np.arange(1, len(scores) + 1)

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

            if i < len(chain_info) - 1:
                boundary = offset + n
                ax.axvline(
                    x=boundary, color="gray", linestyle="--", alpha=0.5, linewidth=1
                )
            offset += n
    else:
        ax.plot(residues, scores, linewidth=1.5, color="#1f77b4")

    ax.axhspan(90, 100, alpha=0.08, color="green")
    ax.axhspan(70, 90, alpha=0.08, color="yellow")
    ax.axhspan(50, 70, alpha=0.08, color="orange")
    ax.axhspan(0, 50, alpha=0.08, color="red")

    x_label = "Atom Position" if chain_info else "Residue Position"
    ax.set_xlabel(x_label, fontsize=10)
    ax.set_ylabel("pLDDT Score", fontsize=10)
    ax.set_title(
        f"Per-Residue Confidence (pLDDT) - {title_name}"
        if not chain_info
        else f"Per-Atom Confidence (pLDDT) - {title_name}",
        fontsize=11,
        fontweight="bold",
    )
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.3, linestyle="--")
    if chain_info:
        ax.legend(loc="lower right", fontsize=7)

    plt.tight_layout()
    if output_path.startswith("gs://"):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            local_tmp = tf.name
        try:
            plt.savefig(local_tmp, dpi=150, bbox_inches="tight")
            plt.close(fig)
            upload_to_gcs(
                local_tmp,
                output_path,
                content_type="image/png",
                expected_bucket=expected_bucket,
            )
        finally:
            if os.path.exists(local_tmp):
                os.unlink(local_tmp)
    else:
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
    logger.info(f"Generated pLDDT plot: {output_path}")


def plot_error_matrix(
    matrix: list | np.ndarray,
    sample_name: str,
    label: str,
    output_path: str,
    chain_info: list[dict] | None = None,
    max_value: float | None = None,
    expected_bucket: str | None = None,
) -> None:
    """Generate error matrix (PDE/PAE) heatmap with optional chain gridlines."""
    if plt is None:
        logger.warning(
            "matplotlib not available; skipping error matrix plot generation"
        )
        return

    data = np.array(matrix, dtype=float)
    if data.ndim != 2 or data.size == 0 or np.all(np.isnan(data)):
        return

    fig, ax = plt.subplots(figsize=(8, 7))
    cmap = plt.cm.Greens_r
    vmax = max_value if max_value is not None else float(np.nanmax(data))
    im = ax.imshow(data, vmin=0, vmax=vmax, cmap=cmap)

    if chain_info and len(chain_info) > 1:
        offset = 0
        for i, ci in enumerate(chain_info):
            n = ci["residue_count"]
            if i < len(chain_info) - 1:
                boundary = offset + n - 0.5
                ax.axhline(
                    y=boundary, color="white", linestyle="-", linewidth=1.5, alpha=0.8
                )
                ax.axvline(
                    x=boundary, color="white", linestyle="-", linewidth=1.5, alpha=0.8
                )
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
    cbar.set_label(label, rotation=270, labelpad=20, fontsize=10)

    ax.set_xlabel("Scored Residue", fontsize=10)
    ax.set_ylabel("Aligned Residue", fontsize=10)
    ax.set_title(
        f"{label.split('(')[0].strip()} - {sample_name}",
        fontsize=11,
        fontweight="bold",
    )

    plt.tight_layout()
    if output_path.startswith("gs://"):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            local_tmp = tf.name
        try:
            plt.savefig(local_tmp, dpi=150, bbox_inches="tight")
            plt.close(fig)
            upload_to_gcs(
                local_tmp,
                output_path,
                content_type="image/png",
                expected_bucket=expected_bucket,
            )
        finally:
            if os.path.exists(local_tmp):
                os.unlink(local_tmp)
    else:
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
    logger.info(f"Generated error matrix plot: {output_path}")


def plot_iptm_matrix(
    chain_pair_iptm: dict,
    sample_name: str,
    output_path: str,
    chain_ptm: dict | None = None,
    chain_info: list | None = None,
    expected_bucket: str | None = None,
) -> None:
    """Generate chain x chain ipTM heatmap with per-chain pTM on diagonal."""
    if plt is None or sns is None:
        logger.warning("matplotlib/seaborn not available; skipping ipTM matrix plot")
        return

    chains = set()
    pairs: dict[tuple[str, str], float] = {}
    for key, value in (chain_pair_iptm or {}).items():
        if value is None:
            continue
        key_clean = str(key).strip("()").replace(" ", "")
        parts = key_clean.split(",")
        if len(parts) == 2:
            c1, c2 = parts
            try:
                fval = float(value)
            except (TypeError, ValueError):
                continue
            if not (math.isnan(fval) or math.isinf(fval)):
                chains.add(c1)
                chains.add(c2)
                pairs[(c1, c2)] = fval

    if chain_ptm:
        for k, v in chain_ptm.items():
            if v is not None:
                chains.add(str(k))

    sorted_chains = sorted(chains)
    n = len(sorted_chains)
    if n < 2:
        logger.info("Skipping ipTM matrix — fewer than 2 chains")
        return

    matrix = np.zeros((n, n))
    for i, c1 in enumerate(sorted_chains):
        for j, c2 in enumerate(sorted_chains):
            if i == j and chain_ptm and chain_ptm.get(c1) is not None:
                matrix[i, j] = float(chain_ptm[c1])
            else:
                matrix[i, j] = pairs.get((c1, c2), pairs.get((c2, c1), 0.0))

    chain_labels = []
    ci_map = {c["chain_id"]: c for c in (chain_info or [])}
    for c in sorted_chains:
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
    if output_path.startswith("gs://"):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            local_tmp = tf.name
        try:
            plt.savefig(local_tmp, dpi=150, bbox_inches="tight")
            plt.close(fig)
            upload_to_gcs(
                local_tmp,
                output_path,
                content_type="image/png",
                expected_bucket=expected_bucket,
            )
        finally:
            if os.path.exists(local_tmp):
                os.unlink(local_tmp)
    else:
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
    logger.info(f"Generated ipTM matrix plot: {output_path}")


def is_prediction_analysis_blob(blob_name: str) -> bool:
    """Return True if blob_name is a per-prediction analysis JSON artifact."""
    filename = blob_name.rsplit("/", 1)[-1]
    if not filename.endswith(".json"):
        return False
    if filename in ("summary.json", "task_config.json", "analysis_metadata.json"):
        return False
    return filename.startswith("analysis_") or filename.endswith("_analysis.json")


def wait_for_sibling_tasks(
    bucket_name: str,
    prefix: str,
    task_count: int,
    max_wait: int = 300,
    interval: int = 2,
) -> bool:
    """Wait for all expected per-prediction analysis files to be written to GCS."""
    expected_count = int(task_count)
    if expected_count <= 0:
        return True

    waited = 0
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    prefix_clean = prefix.removeprefix(f"gs://{bucket_name}/").lstrip("/")
    completed_files: list[str] = []

    while waited < max_wait:
        blobs = bucket.list_blobs(prefix=prefix_clean)
        completed_files = [b.name for b in blobs if is_prediction_analysis_blob(b.name)]

        if len(completed_files) >= expected_count:
            logger.info(
                f"All {expected_count} sibling task analysis files found successfully"
            )
            return True

        logger.info(
            f"Waiting for sibling tasks... ({len(completed_files)}/{expected_count} files found)"
        )
        time.sleep(interval)
        waited += interval

    logger.warning(
        f"Timeout waiting for sibling tasks. Proceeding consolidation with {len(completed_files)}/{expected_count} files."
    )
    return False


def calculate_per_chain_plddt(
    plddt_scores: list, chain_info: list[dict]
) -> dict[str, dict[str, Any]]:
    """Calculate per-chain pLDDT statistics without fabricating placeholder values."""
    if not plddt_scores or not chain_info:
        return {}

    raw_vals: list[float | None] = []
    for x in plddt_scores:
        if x is None:
            raw_vals.append(None)
            continue
        try:
            fx = float(x)
            raw_vals.append(None if (math.isnan(fx) or math.isinf(fx)) else fx)
        except (TypeError, ValueError):
            raw_vals.append(None)

    valid_all = [v for v in raw_vals if v is not None]
    scale = 100.0 if (valid_all and 0.0 < max(valid_all) <= 1.0) else 1.0
    if scale != 1.0:
        raw_vals = [v * scale if v is not None else None for v in raw_vals]

    result: dict[str, dict[str, Any]] = {}
    offset = 0

    for ci in chain_info:
        if not isinstance(ci, dict):
            continue
        n = ci.get("atom_count", 0)
        slice_vals = [v for v in raw_vals[offset : offset + n] if v is not None]
        if slice_vals:
            arr = np.array(slice_vals, dtype=float)
            result[ci["chain_id"]] = {
                "mean": float(np.mean(arr)),
                "min": float(np.min(arr)),
                "max": float(np.max(arr)),
                "std": float(np.std(arr)),
                "atom_count": n,
                "residue_count": ci["residue_count"],
                "molecule_type": ci["molecule_type"],
                "comp_ids": ci["comp_ids"],
            }
        else:
            result[ci["chain_id"]] = {
                "mean": None,
                "min": None,
                "max": None,
                "std": None,
                "atom_count": n,
                "residue_count": ci["residue_count"],
                "molecule_type": ci["molecule_type"],
                "comp_ids": ci["comp_ids"],
            }
        offset += n

    return result


def get_job_metadata(job_id: str) -> dict:
    """Retrieve PipelineJob metadata from Vertex AI.

    Args:
        job_id: The pipeline job ID.

    Returns:
        A dictionary containing job metadata (labels, parameters, display name, state,
        created/started/completed times, and duration).
    """
    job_metadata = {"labels": {}, "parameters": {}}
    try:
        import os

        from google.cloud import aiplatform_v1 as vertex_ai

        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project_id:
            logger.warning(
                "GOOGLE_CLOUD_PROJECT environment variable not set, cannot retrieve job metadata"
            )
            return job_metadata

        # Parse region from job_id if it's a full resource name
        region = os.environ.get("PIPELINE_JOB_LOCATION")
        if not region:
            logger.warning(
                "PIPELINE_JOB_LOCATION environment variable not set, cannot retrieve job metadata"
            )
            return job_metadata

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

    return job_metadata


def build_expert_analysis_markdown(summary_data: dict) -> str:
    """Format a standalone Markdown report from consolidated summary_data."""
    job_id = summary_data.get("job_id", "unknown")
    model_type = summary_data.get("model_type", "unknown")
    analyzed_at = summary_data.get("analyzed_at", "")
    protein_info = summary_data.get("summary", {}).get("protein_info", {})
    job_meta = protein_info.get("job_metadata", {})
    display_name = job_meta.get("display_name") or job_id

    lines = [
        f"# FoldRun Analysis Report: {display_name}",
        "",
        f"- **Job ID**: `{job_id}`",
        f"- **Model**: `{model_type}`",
        f"- **Analyzed At**: `{analyzed_at}`",
    ]
    if protein_info.get("sequence_length"):
        lines.append(
            f"- **Sequence / Token Length**: `{protein_info['sequence_length']}`"
        )
    if job_meta.get("duration_formatted"):
        lines.append(f"- **Pipeline Duration**: `{job_meta['duration_formatted']}`")

    best = summary_data.get("best_prediction") or {}
    if best:
        lines.extend(["", "## Top-Ranked Prediction Summary", ""])
        name = best.get("sample_name") or best.get("model_name") or "Rank 1"
        lines.append(f"- **Prediction**: `{name}`")
        if best.get("plddt_mean") is not None:
            lines.append(f"- **Mean pLDDT**: `{best['plddt_mean']:.2f}`")
        if best.get("ranking_score") is not None:
            lines.append(f"- **Ranking Score**: `{best['ranking_score']:.4f}`")
        if best.get("ranking_confidence") is not None:
            lines.append(
                f"- **Ranking Confidence**: `{best['ranking_confidence']:.4f}`"
            )
        if best.get("ptm") is not None:
            lines.append(f"- **pTM**: `{best['ptm']:.4f}`")
        if best.get("iptm") is not None:
            lines.append(f"- **ipTM**: `{best['iptm']:.4f}`")
        if best.get("pae_mean") is not None:
            lines.append(f"- **Mean PAE**: `{best['pae_mean']:.2f} Å`")
        if best.get("quality_assessment"):
            lines.append(f"- **Quality Assessment**: `{best['quality_assessment']}`")

    expert = summary_data.get("expert_analysis") or {}
    expert_text = expert.get("analysis") if isinstance(expert, dict) else None
    if expert_text:
        lines.extend(
            ["", "## Gemini Expert Analysis", "", str(expert_text).strip(), ""]
        )

    return "\n".join(lines) + "\n"


def _build_archive_readme(summary_data: dict, included_files: list[str]) -> str:
    """Create a human-readable README.md describing the contents of the ZIP bundle."""
    job_id = summary_data.get("job_id", "unknown")
    model_type = summary_data.get("model_type", "unknown")
    analyzed_at = summary_data.get("analyzed_at", "")
    protein_info = summary_data.get("summary", {}).get("protein_info", {})
    display_name = protein_info.get("job_metadata", {}).get("display_name") or job_id

    lines = [
        f"# FoldRun Artifact Bundle — {display_name}",
        "",
        f"- **Job ID**: `{job_id}`",
        f"- **Model Type**: `{model_type}`",
        f"- **Generated At**: `{analyzed_at}`",
        f"- **Total Files in Bundle**: `{len(included_files)}`",
        "",
        "## Directory Layout",
        "",
        "- `report/expert_analysis.md` — Standalone Gemini expert structural analysis report",
        "- `input/` — Pipeline input sequence or query specification (`.fasta`, `.json`, or `.yaml`)",
        "- `structures/` — Ranked 3D structure predictions (`.cif` or `.pdb`)",
        "- `plots/` — Ranked confidence & error plots (`_plddt.png`, `_pae.png` / `_pde.png`, `_iptm_matrix.png`)",
        "- `metrics/summary.json` — Consolidated rankings, confidence statistics, and metadata",
        "",
        "> Note: Large intermediate matrices (such as raw `.pkl`, `.npz`, full per-token JSONs, and MSA alignments) are excluded to keep this archive compact.",
        "",
        "## Ranked Predictions",
        "",
        "| Rank | Prediction | Mean pLDDT | Score / Confidence | Quality |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]

    for idx, pred in enumerate(summary_data.get("all_predictions_summary", [])):
        rank = pred.get("plddt_rank") or pred.get("rank") or (idx + 1)
        name = pred.get("sample_name") or pred.get("model_name") or f"pred_{rank}"
        plddt = (
            f"{pred['plddt_mean']:.1f}"
            if isinstance(pred.get("plddt_mean"), (int, float))
            else "N/A"
        )
        score_val = pred.get("ranking_score", pred.get("ranking_confidence"))
        score_str = f"{score_val:.4f}" if isinstance(score_val, (int, float)) else "N/A"
        qual = pred.get("quality_assessment", "N/A")
        lines.append(f"| {rank} | `{name}` | {plddt} | {score_str} | `{qual}` |")

    lines.append("")
    return "\n".join(lines)


def build_and_upload_run_archive(
    analysis_path: str,
    bucket_name: str,
    summary_data: dict,
) -> dict:
    """Build a compact, ranked ZIP archive of run artifacts and upload to GCS.

    Includes:
      - report/expert_analysis.md
      - input/ (FASTA, OpenFold3 query.json, or Boltz-2 query.yaml)
      - metrics/summary.json (and metrics/affinity.json if present)
      - structures/rank_XX_<name>.cif or .pdb
      - plots/rank_XX_<name>_<plot_type>.png
      - README.md

    Excludes raw .pkl, .npz, full token-pair _confidences.json, and MSA files.
    """
    import concurrent.futures
    import os
    import re
    import tempfile
    import zipfile

    if not analysis_path.endswith("/"):
        analysis_path += "/"

    bundle_uri = f"{analysis_path}artifacts_bundle.zip"
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    def _safe_name(val: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9._-]", "_", str(val or "prediction")).strip("._-")
        return cleaned or "prediction"

    def _uri_to_blob_name(gcs_uri: str) -> str | None:
        prefix = f"gs://{bucket_name}/"
        if not isinstance(gcs_uri, str) or not gcs_uri.startswith(prefix):
            return None
        blob_name = gcs_uri[len(prefix) :]
        if not blob_name or ".." in blob_name.split("/"):
            return None
        return blob_name

    # Collect (archive_path, gcs_uri) pairs to download in parallel
    download_tasks: list[tuple[str, str]] = []
    all_preds = list(summary_data.get("all_predictions_summary", []))
    all_preds.sort(key=lambda p: p.get("plddt_rank") or p.get("rank") or 999)

    for idx, pred in enumerate(all_preds):
        rank = int(pred.get("plddt_rank") or pred.get("rank") or (idx + 1))
        label = _safe_name(
            pred.get("sample_name") or pred.get("model_name") or f"model_{rank}"
        )
        rank_prefix = f"rank_{rank:02d}_{label}"

        if pred.get("cif_uri"):
            download_tasks.append((f"structures/{rank_prefix}.cif", pred["cif_uri"]))
        elif pred.get("uri"):
            raw_uri = str(pred["uri"])
            if raw_uri.endswith("/raw_prediction.pkl"):
                unrelaxed_uri = raw_uri.replace(
                    "/raw_prediction.pkl", "/unrelaxed_protein.pdb"
                )
                relaxed_uri = raw_uri.replace(
                    "/raw_prediction.pkl", "/relaxed_protein.pdb"
                )
                download_tasks.append(
                    (f"structures/{rank_prefix}_unrelaxed.pdb", unrelaxed_uri)
                )
                download_tasks.append(
                    (f"structures/{rank_prefix}_relaxed.pdb", relaxed_uri)
                )
            elif raw_uri.endswith(".pdb"):
                download_tasks.append((f"structures/{rank_prefix}.pdb", raw_uri))

        plots = pred.get("plots") or {}
        for plot_key, plot_uri in plots.items():
            if not plot_uri:
                continue
            clean_key = _safe_name(plot_key.replace("_plot", ""))
            download_tasks.append((f"plots/{rank_prefix}_{clean_key}.png", plot_uri))

    def _fetch_blob_bytes(item: tuple[str, str]) -> tuple[str, bytes | None]:
        arc_path, uri = item
        blob_name = _uri_to_blob_name(uri)
        if not blob_name:
            return arc_path, None
        try:
            blob = bucket.blob(blob_name)
            if not blob.exists():
                return arc_path, None
            return arc_path, blob.download_as_bytes()
        except Exception as e:
            logger.debug(f"Skipping optional archive member {uri}: {e}")
            return arc_path, None

    fetched_items: list[tuple[str, bytes]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for arc_path, data in pool.map(_fetch_blob_bytes, download_tasks):
            if data is not None:
                fetched_items.append((arc_path, data))

    protein_info = summary_data.get("summary", {}).get("protein_info", {})
    included_files: list[str] = []

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_zip:
        tmp_zip_path = tmp_zip.name

    try:
        with zipfile.ZipFile(
            tmp_zip_path, mode="w", compression=zipfile.ZIP_DEFLATED
        ) as zf:
            # 1. Expert analysis report (.md)
            report_md = build_expert_analysis_markdown(summary_data)
            zf.writestr("report/expert_analysis.md", report_md.encode("utf-8"))
            included_files.append("report/expert_analysis.md")

            # 2. Input files (FASTA / JSON / YAML)
            fasta_seq = protein_info.get("fasta_sequence")
            if fasta_seq:
                header = protein_info.get("fasta_header") or summary_data.get(
                    "job_id", "sequence"
                )
                fasta_content = f">{header}\n{fasta_seq}\n"
                zf.writestr("input/sequence.fasta", fasta_content.encode("utf-8"))
                included_files.append("input/sequence.fasta")

            if protein_info.get("input_query_json"):
                zf.writestr(
                    "input/query.json",
                    json.dumps(protein_info["input_query_json"], indent=2).encode(
                        "utf-8"
                    ),
                )
                included_files.append("input/query.json")

            if protein_info.get("input_query_yaml"):
                yaml_val = protein_info["input_query_yaml"]
                yaml_str = (
                    yaml_val
                    if isinstance(yaml_val, str)
                    else json.dumps(yaml_val, indent=2)
                )
                zf.writestr("input/query.yaml", yaml_str.encode("utf-8"))
                included_files.append("input/query.yaml")

            # 3. Affinity metrics if present (Boltz-2)
            affinity = summary_data.get("summary", {}).get("affinity")
            if affinity:
                zf.writestr(
                    "metrics/affinity.json",
                    json.dumps(
                        _sanitize_data(affinity), indent=2, allow_nan=False
                    ).encode("utf-8"),
                )
                included_files.append("metrics/affinity.json")

            # 4. Structures and plots downloaded from GCS
            for arc_path, content_bytes in fetched_items:
                zf.writestr(arc_path, content_bytes)
                included_files.append(arc_path)

            # 5. Consolidated summary.json
            summary_copy = _sanitize_data(dict(summary_data))
            summary_copy["artifacts_bundle_uri"] = bundle_uri
            zf.writestr(
                "metrics/summary.json",
                json.dumps(summary_copy, indent=2, allow_nan=False).encode("utf-8"),
            )
            included_files.append("metrics/summary.json")

            # 6. Root README.md
            readme_md = _build_archive_readme(summary_data, included_files)
            zf.writestr("README.md", readme_md.encode("utf-8"))
            included_files.append("README.md")

        bundle_size_bytes = os.path.getsize(tmp_zip_path)
        upload_to_gcs(tmp_zip_path, bundle_uri)
        logger.info(
            f"Built and uploaded artifact bundle {bundle_uri} "
            f"({bundle_size_bytes / 1024 / 1024:.2f} MB, {len(included_files)} files)"
        )
        return {
            "artifacts_bundle_uri": bundle_uri,
            "artifacts_bundle_size_bytes": bundle_size_bytes,
            "artifacts_bundle_file_count": len(included_files),
        }
    finally:
        if os.path.exists(tmp_zip_path):
            os.remove(tmp_zip_path)
