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

"""`dde structure` — structural analysis utilities.

``interface``          Identify interface residues between chains in a
                       complex structure (PDB or mmCIF).  Produces a
                       residue list suitable for ``dde pocket run --near``,
                       closing the loop between complex prediction and
                       site-specific druggability.  This is a structural
                       computation, not an API call — it works directly
                       from the coordinate file.

``annotate-topology``  Map GPCR transmembrane topology onto pocket-lining
                       residues, answering "which pocket is the orthosteric
                       site?" by checking which pockets have residues in
                       TM3, TM6, and TM7 — the helices that form the
                       orthosteric binding cleft in Class A GPCRs.  TM
                       boundaries are fetched from UniProt (primary).

``surface``            Compute per-residue solvent-accessible surface area
                       (SASA) and relative solvent accessibility (RSA) from
                       a PDB or mmCIF structure using the Shrake-Rupley
                       algorithm.  Optionally integrates glycosylation sites
                       from UniProt and per-residue pLDDT as a disorder
                       proxy for AlphaFold structures.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import click
import numpy as np

from ..common import AppState, out_option, output_options, pass_state, resolve_artifact
from ..core import http, provenance
from ..core import thresholds as thresholds_mod
from ..core.errors import ArtifactError, SchemaError, UsageError
from ..core.output import Emitter
from ..core.paths import sanitize_slug
from ..core.qps import qps_for_host
from ..core.structures import detect_structure_format

ARTIFACT_SCHEMA = "dde.structure-interface.v1"
SURFACE_ARTIFACT_SCHEMA = "dde.structure-surface.v1"

# -- topology annotation constants ------------------------------------------

TOOL_TOPOLOGY = "topology-annotation"
ARTIFACT_CLASS = "structures"

UNIPROT_API = "https://rest.uniprot.org/uniprotkb"

# UniProt accession pattern (6 or 10 chars).
_UNIPROT_RE = re.compile(
    r"^[A-NR-Z][0-9][A-Z0-9]{3}[0-9]$"
    r"|^[OPQ][0-9][A-Z0-9]{3}[0-9]$"
    r"|^[A-Z0-9]{10}$"
)

# Orthosteric cleft helices for Class A GPCRs.
_ORTHOSTERIC_TM = {"TM3", "TM6", "TM7"}

# Bundle-void heuristic thresholds.
_BUNDLE_VOID_MIN_TM_SEGMENTS = 5
_BUNDLE_VOID_MIN_ALPHA_SPHERES = 200
_BUNDLE_VOID_MIN_VOLUME = 1500.0


# ---------------------------------------------------------------------------
# Atom coordinate extraction (interface)
# ---------------------------------------------------------------------------


def _parse_atoms_pdb(text: str) -> list[dict[str, Any]]:
    """Extract heavy-atom records from PDB-format text.

    Returns a list of dicts with keys: chain, resnum, resname, x, y, z,
    element, bfactor.  Hydrogen atoms (element H/D in columns 76-78, or
    atom name starting with H/digit-H) are excluded — interface contacts
    are defined on heavy atoms only.

    The B-factor (columns 60-66) is extracted because AlphaFold structures
    store per-residue pLDDT in this field, giving a disorder proxy for free.
    """
    atoms: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        try:
            # PDB fixed-column layout
            atom_name = line[12:16].strip()
            resname = line[17:20].strip()
            chain = line[21].strip() or "_"
            resnum = int(line[22:26])
            x = float(line[30:38])
            y = float(line[38:46])
            z = float(line[46:54])
        except (ValueError, IndexError):
            continue

        # B-factor: columns 60-66 (optional, defaults to 0.0).
        try:
            bfactor = float(line[60:66])
        except (ValueError, IndexError):
            bfactor = 0.0

        # Skip hydrogen atoms
        # Element symbol is at columns 76-78 in standard PDB; fall back
        # to first non-digit character of the atom name.
        element = line[76:78].strip() if len(line) >= 78 else ""
        if not element:
            element = atom_name.lstrip("0123456789")[:1]
        if element in ("H", "D"):
            continue

        atoms.append(
            {
                "chain": chain,
                "resnum": resnum,
                "resname": resname,
                "x": x,
                "y": y,
                "z": z,
                "element": element,
                "bfactor": bfactor,
            }
        )
    return atoms


def _parse_atoms_cif(text: str) -> list[dict[str, Any]]:
    """Extract heavy-atom records from mmCIF-format text.

    Reads ``_atom_site`` loop columns for ``auth_asym_id`` (chain),
    ``auth_seq_id`` (residue number), ``label_comp_id`` (residue name),
    and ``Cartn_x/y/z`` (coordinates).  Falls back to ``label_asym_id``
    and ``label_seq_id`` when auth variants are absent.

    Also extracts ``B_iso_or_equiv`` (B-factor) when available — for
    AlphaFold structures this is per-residue pLDDT.

    Hydrogen atoms are excluded via the ``type_symbol`` column when
    available, or by atom-name heuristic otherwise.
    """
    lines = text.splitlines()
    columns: list[str] = []
    data_start = 0
    in_atom_site = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("_atom_site."):
            in_atom_site = True
            columns.append(stripped.split(".")[1])
        elif in_atom_site:
            data_start = i
            break

    if not columns:
        return []

    def _col(preferred: str, fallback: str) -> int | None:
        if preferred in columns:
            return columns.index(preferred)
        if fallback in columns:
            return columns.index(fallback)
        return None

    col_chain = _col("auth_asym_id", "label_asym_id")
    col_resnum = _col("auth_seq_id", "label_seq_id")
    col_resname = _col("label_comp_id", "label_comp_id")
    col_x = columns.index("Cartn_x") if "Cartn_x" in columns else None
    col_y = columns.index("Cartn_y") if "Cartn_y" in columns else None
    col_z = columns.index("Cartn_z") if "Cartn_z" in columns else None
    col_element = columns.index("type_symbol") if "type_symbol" in columns else None
    col_atom_name = _col("auth_atom_id", "label_atom_id")
    col_bfactor = (
        columns.index("B_iso_or_equiv") if "B_iso_or_equiv" in columns else None
    )

    required = (col_chain, col_resnum, col_resname, col_x, col_y, col_z)
    if any(c is None for c in required):
        return []

    atoms: list[dict[str, Any]] = []
    for line in lines[data_start:]:
        if not line.startswith(("ATOM", "HETATM")):
            continue
        fields = line.split()
        try:
            chain = fields[col_chain] or "_"  # type: ignore[index]
            resnum = int(fields[col_resnum])  # type: ignore[index]
            resname = fields[col_resname]  # type: ignore[index]
            x = float(fields[col_x])  # type: ignore[index]
            y = float(fields[col_y])  # type: ignore[index]
            z = float(fields[col_z])  # type: ignore[index]
        except (ValueError, IndexError):
            continue

        # B-factor (optional, defaults to 0.0).
        bfactor = 0.0
        if col_bfactor is not None:
            try:
                bfactor = float(fields[col_bfactor])
            except (ValueError, IndexError):
                pass

        # Element symbol — extracted for both hydrogen filtering and
        # vdW radius lookup in SASA computation.
        element = ""
        if col_element is not None:
            try:
                element = fields[col_element]
                if element in ("H", "D"):
                    continue
            except IndexError:
                pass
        elif col_atom_name is not None:
            try:
                atom_name = fields[col_atom_name]
                element = atom_name.lstrip("0123456789")[:1]
                if element in ("H", "D"):
                    continue
            except IndexError:
                pass

        atoms.append(
            {
                "chain": chain,
                "resnum": resnum,
                "resname": resname,
                "x": x,
                "y": y,
                "z": z,
                "element": element,
                "bfactor": bfactor,
            }
        )
    return atoms


# ---------------------------------------------------------------------------
# Interface computation
# ---------------------------------------------------------------------------


def _group_atoms_by_chain(
    atoms: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group atom records by chain identifier."""
    chains: dict[str, list[dict[str, Any]]] = {}
    for atom in atoms:
        chains.setdefault(atom["chain"], []).append(atom)
    return chains


def _find_interface_residues(
    atoms_a: list[dict[str, Any]],
    atoms_b: list[dict[str, Any]],
    cutoff: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Find residues at the interface between two sets of atoms.

    A residue on chain A is an interface residue if any of its heavy
    atoms is within ``cutoff`` Angstroms of any heavy atom on chain B,
    and vice versa.

    Returns ``(residues_a, residues_b, n_contacts)`` where each residue
    list contains ``{"chain", "resnum", "resname"}`` dicts (deduplicated,
    sorted), and ``n_contacts`` is the number of atom-atom pairs within
    the cutoff.
    """
    cutoff_sq = cutoff * cutoff

    seen_a: dict[tuple[str, int], str] = {}
    seen_b: dict[tuple[str, int], str] = {}
    n_contacts = 0

    # Pure-Python pairwise distance check.  For the structures this tool
    # processes (AF3 complexes, typically <20k atoms), this is fast
    # enough; numpy would be marginal gain for the added complexity.
    for aa in atoms_a:
        ax, ay, az = aa["x"], aa["y"], aa["z"]
        for ab in atoms_b:
            dx = ax - ab["x"]
            dy = ay - ab["y"]
            dz = az - ab["z"]
            if dx * dx + dy * dy + dz * dz <= cutoff_sq:
                n_contacts += 1
                key_a = (aa["chain"], aa["resnum"])
                if key_a not in seen_a:
                    seen_a[key_a] = aa["resname"]
                key_b = (ab["chain"], ab["resnum"])
                if key_b not in seen_b:
                    seen_b[key_b] = ab["resname"]

    residues_a = [
        {"chain": chain, "resnum": resnum, "resname": seen_a[(chain, resnum)]}
        for chain, resnum in sorted(seen_a)
    ]
    residues_b = [
        {"chain": chain, "resnum": resnum, "resname": seen_b[(chain, resnum)]}
        for chain, resnum in sorted(seen_b)
    ]
    return residues_a, residues_b, n_contacts


def _near_query(residues: list[dict[str, Any]]) -> str:
    """Format residues as a ``--near`` query string: ``A:123,A:124,...``."""
    return ",".join(f"{r['chain']}:{r['resnum']}" for r in residues)


# ---------------------------------------------------------------------------
# UniProt resolution (topology annotation)
# ---------------------------------------------------------------------------


def _is_accession(identifier: str) -> bool:
    """Does *identifier* look like a UniProt accession?"""
    return bool(_UNIPROT_RE.match(identifier))


def resolve_accession(identifier: str) -> tuple[str, str | None]:
    """Resolve *identifier* to a UniProt accession.

    If it already looks like an accession, return it directly.
    Otherwise treat it as a gene symbol and search UniProt for a
    human (organism 9606) exact gene match.

    Returns ``(accession, gene_symbol)`` — the gene symbol is the
    query when we searched, or ``None`` when the input was already an
    accession.
    """
    if _is_accession(identifier):
        return identifier, None

    # Gene symbol → accession via UniProt search.
    gene = identifier.upper()
    url = (
        f"{UNIPROT_API}/search"
        f"?query=gene_exact:{gene}+AND+organism_id:9606"
        f"&fields=accession"
        f"&format=json"
        f"&size=5"
    )
    data = http.get_json(url, qps=qps_for_host("rest.uniprot.org"), timeout=30.0)
    results = data.get("results") or []
    if not results:
        raise UsageError(
            f"no UniProt entry found for gene symbol {gene!r} (human)",
            detail="the search returned zero results",
            remedy="pass a UniProt accession directly, or check the gene symbol",
        )
    accession = results[0].get("primaryAccession")
    if not accession:
        raise SchemaError(
            "UniProt search result missing primaryAccession",
            detail=f"first result: {json.dumps(results[0])[:300]}",
        )
    return accession, gene


# ---------------------------------------------------------------------------
# UniProt feature parsing (topology annotation)
# ---------------------------------------------------------------------------


def fetch_topology(accession: str) -> dict[str, Any]:
    """Fetch and parse transmembrane topology from UniProt.

    Returns a dict with keys:
      accession, gene, organism, tm_regions, topo_domains, sequence_length
    """
    url = f"{UNIPROT_API}/{accession}.json"
    data = http.get_json(url, qps=qps_for_host("rest.uniprot.org"), timeout=30.0)

    features = data.get("features") or []
    genes = data.get("genes") or []
    gene_symbol = None
    if genes and isinstance(genes[0], dict):
        gene_symbol = (genes[0].get("geneName") or {}).get("value")

    organism = None
    org_data = data.get("organism")
    if isinstance(org_data, dict):
        organism = org_data.get("scientificName")

    seq_length = None
    seq_data = data.get("sequence")
    if isinstance(seq_data, dict):
        seq_length = seq_data.get("length")

    tm_regions = parse_tm_regions(features)
    topo_domains = parse_topo_domains(features)

    return {
        "accession": accession,
        "gene": gene_symbol,
        "organism": organism,
        "sequence_length": seq_length,
        "tm_regions": tm_regions,
        "topo_domains": topo_domains,
    }


def parse_tm_regions(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract transmembrane regions from UniProt features array.

    Each TM region gets a label (TM1, TM2, ...) based on position
    along the sequence (N→C ordering).
    """
    raw = []
    for feat in features:
        if feat.get("type") != "Transmembrane":
            continue
        loc = feat.get("location") or {}
        start_obj = loc.get("start") or {}
        end_obj = loc.get("end") or {}
        start = start_obj.get("value")
        end = end_obj.get("value")
        if start is None or end is None:
            continue
        try:
            start, end = int(start), int(end)
        except (ValueError, TypeError):
            continue
        desc = feat.get("description") or ""
        raw.append({"start": start, "end": end, "description": desc})

    # Sort by start position for TM numbering.
    raw.sort(key=lambda r: r["start"])
    for idx, region in enumerate(raw, 1):
        region["label"] = f"TM{idx}"

    return raw


def parse_topo_domains(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract topological domain annotations (Extracellular, Cytoplasmic)."""
    domains = []
    for feat in features:
        ftype = feat.get("type") or ""
        if ftype not in ("Topological domain", "Intramembrane"):
            continue
        loc = feat.get("location") or {}
        start_obj = loc.get("start") or {}
        end_obj = loc.get("end") or {}
        start = start_obj.get("value")
        end = end_obj.get("value")
        if start is None or end is None:
            continue
        try:
            start, end = int(start), int(end)
        except (ValueError, TypeError):
            continue
        desc = feat.get("description") or ""
        domains.append(
            {
                "type": ftype,
                "start": start,
                "end": end,
                "description": desc,
            }
        )
    return domains


# ---------------------------------------------------------------------------
# Glycosylation site extraction
# ---------------------------------------------------------------------------


def _parse_glycosylation_sites(
    features: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract glycosylation site annotations from UniProt features.

    Follows the ``parse_tm_regions()`` pattern: filters for features of
    type ``"Glycosylation"`` and extracts position plus description
    (which encodes N-linked vs O-linked etc.).
    """
    sites: list[dict[str, Any]] = []
    for feat in features:
        if feat.get("type") != "Glycosylation":
            continue
        loc = feat.get("location") or {}
        start_obj = loc.get("start") or {}
        end_obj = loc.get("end") or {}
        start = start_obj.get("value")
        end = end_obj.get("value")
        if start is None:
            continue
        try:
            start = int(start)
            end = int(end) if end is not None else start
        except (ValueError, TypeError):
            continue
        desc = feat.get("description") or ""
        # Extract glycosylation type from description (e.g. "N-linked (GlcNAc...)")
        glyco_type = "unknown"
        desc_lower = desc.lower()
        if "n-linked" in desc_lower:
            glyco_type = "N-linked"
        elif "o-linked" in desc_lower:
            glyco_type = "O-linked"
        elif "c-linked" in desc_lower:
            glyco_type = "C-linked"
        elif "s-linked" in desc_lower:
            glyco_type = "S-linked"
        sites.append(
            {
                "position": start,
                "end": end,
                "type": glyco_type,
                "description": desc,
            }
        )
    return sites


# ---------------------------------------------------------------------------
# SASA computation (Shrake-Rupley algorithm)
# ---------------------------------------------------------------------------

# Atomic van der Waals radii (Angstroms).
# Shrake & Rupley 1973 / CHARMM22 consensus values for heavy atoms
# found in standard amino acids.
_VDW_RADII: dict[str, float] = {
    "C": 1.70,
    "N": 1.55,
    "O": 1.52,
    "S": 1.80,
    "SE": 1.90,
    "P": 1.80,
}
_VDW_DEFAULT = 1.70

# Maximum accessible surface area per residue type (Angstrom^2).
# Tien et al., PLoS ONE 2013;8:e80635 — theoretical maxASA from
# Gly-X-Gly tripeptides.
MAX_ASA_TIEN: dict[str, float] = {
    "ALA": 129.0,
    "ARG": 274.0,
    "ASN": 195.0,
    "ASP": 193.0,
    "CYS": 167.0,
    "GLN": 225.0,
    "GLU": 223.0,
    "GLY": 104.0,
    "HIS": 224.0,
    "ILE": 197.0,
    "LEU": 201.0,
    "LYS": 236.0,
    "MET": 224.0,
    "PHE": 240.0,
    "PRO": 159.0,
    "SER": 155.0,
    "THR": 172.0,
    "TRP": 285.0,
    "TYR": 263.0,
    "VAL": 174.0,
}


def _generate_sphere_points(n: int) -> np.ndarray:
    """Generate *n* approximately uniformly distributed points on a unit sphere.

    Uses the golden-section spiral method, which provides near-uniform
    coverage without the overhead of a random seed.  Returns an (n, 3)
    array of unit vectors.
    """
    indices = np.arange(n, dtype=np.float64)
    phi = math.pi * (3.0 - math.sqrt(5.0))  # golden angle
    y = 1.0 - (2.0 * indices / (n - 1)) if n > 1 else np.zeros(1)
    radius_at_y = np.sqrt(1.0 - y * y)
    theta = phi * indices
    x = np.cos(theta) * radius_at_y
    z = np.sin(theta) * radius_at_y
    return np.column_stack([x, y, z])


def _compute_sasa(
    atoms: list[dict[str, Any]],
    probe_radius: float = 1.4,
    n_points: int = 100,
) -> list[float]:
    """Compute per-atom SASA using the Shrake-Rupley algorithm.

    Uses grid-based spatial hashing for neighbor lookup and numpy for
    vectorized distance computations.

    Returns a list of SASA values (Angstrom^2), one per input atom.
    """
    n_atoms = len(atoms)
    if n_atoms == 0:
        return []

    # Build coordinate array and radius array.
    coords = np.array([[a["x"], a["y"], a["z"]] for a in atoms], dtype=np.float64)
    radii = np.array(
        [_VDW_RADII.get(a.get("element", "").upper(), _VDW_DEFAULT) for a in atoms],
        dtype=np.float64,
    )
    expanded = radii + probe_radius

    # Generate test points on the unit sphere.
    sphere_pts = _generate_sphere_points(n_points)

    # Grid-based spatial hashing — cell size must be >= max possible
    # interaction distance between two atoms.
    max_radius = float(expanded.max())
    cell_size = 2.0 * max_radius
    if cell_size < 1e-6:
        cell_size = 6.8  # fallback

    # Build grid: map each atom to a cell.
    grid: dict[tuple[int, int, int], list[int]] = {}
    for i in range(n_atoms):
        cx = math.floor(coords[i, 0] / cell_size)
        cy = math.floor(coords[i, 1] / cell_size)
        cz = math.floor(coords[i, 2] / cell_size)
        grid.setdefault((cx, cy, cz), []).append(i)

    sasa = np.zeros(n_atoms, dtype=np.float64)

    for i in range(n_atoms):
        ri = expanded[i]
        ci = coords[i]

        # Find neighbor atoms via grid lookup (26 neighbors + own cell).
        cx = math.floor(ci[0] / cell_size)
        cy = math.floor(ci[1] / cell_size)
        cz = math.floor(ci[2] / cell_size)

        neighbors = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    cell = (cx + dx, cy + dy, cz + dz)
                    if cell in grid:
                        for j in grid[cell]:
                            if j != i:
                                neighbors.append(j)

        # Generate test points for this atom.
        test_points = ci + sphere_pts * ri  # (n_points, 3)

        if not neighbors:
            # No neighbors — all points exposed.
            sasa[i] = 4.0 * math.pi * ri * ri
            continue

        # Vectorized occlusion check.
        neighbor_idx = np.array(neighbors)
        neighbor_coords = coords[neighbor_idx]  # (n_neighbors, 3)
        neighbor_radii = expanded[neighbor_idx]  # (n_neighbors,)

        # Distance from each test point to each neighbor center.
        # test_points: (n_points, 3), neighbor_coords: (n_neighbors, 3)
        diff = test_points[:, np.newaxis, :] - neighbor_coords[np.newaxis, :, :]
        dist_sq = np.sum(diff * diff, axis=2)  # (n_points, n_neighbors)

        # A point is occluded if it falls inside any neighbor's expanded sphere.
        radii_sq = neighbor_radii * neighbor_radii  # (n_neighbors,)
        occluded = np.any(dist_sq < radii_sq[np.newaxis, :], axis=1)  # (n_points,)

        n_exposed = int(np.sum(~occluded))
        sasa[i] = (n_exposed / n_points) * 4.0 * math.pi * ri * ri

    return sasa.tolist()


def _compute_rsa(
    atoms: list[dict[str, Any]],
    per_atom_sasa: list[float],
) -> list[dict[str, Any]]:
    """Compute per-residue RSA from per-atom SASA values.

    Groups atoms by (chain, resnum), sums SASA per residue, and divides
    by the Tien et al. 2013 theoretical maximum ASA for that residue type.
    RSA is clamped to [0, 1].

    Returns a list of per-residue dicts sorted by (chain, resnum).
    """
    # Group SASA and B-factor by residue.
    residue_data: dict[tuple[str, int], dict[str, Any]] = {}
    for i, atom in enumerate(atoms):
        key = (atom["chain"], atom["resnum"])
        if key not in residue_data:
            residue_data[key] = {
                "chain": atom["chain"],
                "resnum": atom["resnum"],
                "resname": atom["resname"],
                "sasa": 0.0,
                "bfactors": [],
            }
        residue_data[key]["sasa"] += per_atom_sasa[i]
        residue_data[key]["bfactors"].append(atom.get("bfactor", 0.0))

    # Compute RSA and mean pLDDT per residue.
    result: list[dict[str, Any]] = []
    for key in sorted(residue_data):
        rd = residue_data[key]
        resname = rd["resname"]
        sasa = rd["sasa"]
        max_asa = MAX_ASA_TIEN.get(resname)
        if max_asa is not None and max_asa > 0:
            rsa = min(1.0, max(0.0, sasa / max_asa))
        else:
            # Non-standard residue — report SASA but no RSA.
            rsa = None

        bfactors = rd["bfactors"]
        mean_bfactor = sum(bfactors) / len(bfactors) if bfactors else 0.0

        result.append(
            {
                "chain": rd["chain"],
                "resnum": rd["resnum"],
                "resname": resname,
                "sasa": round(sasa, 1),
                "rsa": round(rsa, 3) if rsa is not None else None,
                "plddt": round(mean_bfactor, 1),
            }
        )
    return result


# ---------------------------------------------------------------------------
# Pocket ↔ TM mapping
# ---------------------------------------------------------------------------


def residue_to_tm(resnum: int, tm_regions: list[dict[str, Any]]) -> str | None:
    """Return the TM label (e.g. 'TM3') for *resnum*, or None."""
    for region in tm_regions:
        if region["start"] <= resnum <= region["end"]:
            return region["label"]
    return None


def map_pocket_to_topology(
    pocket: dict[str, Any],
    tm_regions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Map a single pocket's residues onto TM segments.

    Returns a dict with per-TM residue lists, counts, and an
    orthosteric/bundle-void assessment.
    """
    residues = pocket.get("residues") or []
    rank = pocket.get("rank")
    drug_score = pocket.get("druggability_score")
    volume = pocket.get("volume")
    n_alpha = pocket.get("n_alpha_spheres")

    tm_mapping: dict[str, list[dict[str, Any]]] = {}
    non_tm_residues: list[dict[str, Any]] = []

    for res in residues:
        resnum = res.get("resnum")
        if resnum is None:
            continue
        label = residue_to_tm(resnum, tm_regions)
        if label:
            tm_mapping.setdefault(label, []).append(res)
        else:
            non_tm_residues.append(res)

    # Sort TM labels for consistent output.
    sorted_tms = sorted(tm_mapping.keys(), key=lambda k: int(k[2:]))

    tm_detail = []
    for label in sorted_tms:
        res_list = tm_mapping[label]
        tm_detail.append(
            {
                "label": label,
                "count": len(res_list),
                "residues": [
                    f"{r.get('resname', '?')}{r.get('resnum', '?')}"
                    for r in sorted(res_list, key=lambda r: r.get("resnum", 0))
                ],
            }
        )

    # Check for orthosteric candidacy.
    tm_labels_present = set(sorted_tms)
    is_orthosteric_candidate = _ORTHOSTERIC_TM.issubset(tm_labels_present)

    # Check for bundle-void advisory.
    is_bundle_void = False
    bundle_void_reason = None
    n_tm_segments = len(tm_labels_present)
    if n_tm_segments >= _BUNDLE_VOID_MIN_TM_SEGMENTS:
        alpha_check = n_alpha is not None and n_alpha > _BUNDLE_VOID_MIN_ALPHA_SPHERES
        volume_check = volume is not None and volume > _BUNDLE_VOID_MIN_VOLUME
        if alpha_check or volume_check:
            is_bundle_void = True
            bundle_void_reason = (
                f"Pocket {rank} has residues in {n_tm_segments} TM segments"
                f" with {volume} A^3 volume"
                " — likely TM bundle interior, not a discrete cavity"
            )

    return {
        "rank": rank,
        "druggability_score": drug_score,
        "volume": volume,
        "n_alpha_spheres": n_alpha,
        "n_total_residues": len(residues),
        "n_tm_residues": sum(len(v) for v in tm_mapping.values()),
        "n_non_tm_residues": len(non_tm_residues),
        "tm_segments": tm_detail,
        "n_tm_segments": n_tm_segments,
        "orthosteric_candidate": is_orthosteric_candidate,
        "bundle_void_advisory": is_bundle_void,
        "bundle_void_reason": bundle_void_reason,
    }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
def structure() -> None:
    """Structural analysis utilities."""


@structure.command("interface")
@click.argument("complex_structure", type=click.Path())
@click.option(
    "--chain",
    default=None,
    help="Limit analysis to interfaces involving this chain (default: all pairs).",
)
@click.option(
    "--cutoff",
    default=5.0,
    type=float,
    show_default=True,
    help="Distance cutoff in Angstroms for interface contact definition.",
)
@out_option
@output_options
@pass_state
def interface_cmd(
    state: AppState,
    complex_structure: str,
    chain: str | None,
    cutoff: float,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Identify interface residues between chains in a complex structure.

    Reads a multi-chain structure (PDB or mmCIF) — e.g., an AF3
    prediction — and finds residues at the interface between each pair
    of chains using a distance-based contact analysis.  A residue is an
    interface residue if any of its heavy atoms is within the distance
    cutoff of any heavy atom on a different chain.

    The output is formatted for direct use with ``dde pocket run --near``,
    closing the loop between complex prediction and site-specific
    druggability.

    \b
    Outputs:
      {stem}.interface.artifact.json — interface residue record
      {stem}.interface.meta.json     — provenance sidecar
    """
    emit = Emitter(as_json=as_json, quiet=quiet)

    source = resolve_artifact(state, complex_structure, "structure")
    fmt = detect_structure_format(source)
    text = source.read_text(encoding="utf-8", errors="replace")

    # Parse atoms
    if fmt == "cif":
        atoms = _parse_atoms_cif(text)
    else:
        atoms = _parse_atoms_pdb(text)

    if not atoms:
        raise ArtifactError(
            f"no atom coordinates found in {source.name}",
            detail="the structure file appears empty or unparseable",
            remedy="check that the file is a valid PDB or mmCIF structure "
            "with ATOM/HETATM records",
        )

    # Group by chain
    chain_atoms = _group_atoms_by_chain(atoms)
    all_chains = sorted(chain_atoms.keys())

    if len(all_chains) < 2:
        raise UsageError(
            f"structure {source.name} contains only chain(s) {', '.join(all_chains)}",
            detail="interface detection requires at least two chains",
            remedy="pass a multi-chain complex structure (e.g., an AF3 "
            "prediction with multiple chains)",
        )

    # Validate --chain filter
    if chain is not None and chain not in chain_atoms:
        raise UsageError(
            f"chain {chain!r} not found in {source.name}",
            detail=f"available chains: {', '.join(all_chains)}",
            remedy=f"pass --chain with one of: {', '.join(all_chains)}",
        )

    # Determine which chain pairs to analyse
    if chain is not None:
        pairs = [(chain, other) for other in all_chains if other != chain]
    else:
        pairs = []
        for i, ca in enumerate(all_chains):
            for cb in all_chains[i + 1 :]:
                pairs.append((ca, cb))

    # Compute interfaces
    interfaces: list[dict[str, Any]] = []
    for chain_a, chain_b in pairs:
        residues_a, residues_b, n_contacts = _find_interface_residues(
            chain_atoms[chain_a], chain_atoms[chain_b], cutoff
        )
        if not residues_a and not residues_b:
            continue

        # Build near_query from all interface residues on both sides
        all_interface = sorted(
            residues_a + residues_b,
            key=lambda r: (r["chain"], r["resnum"]),
        )
        interfaces.append(
            {
                "chain_pair": [chain_a, chain_b],
                "residues_chain_a": residues_a,
                "residues_chain_b": residues_b,
                "n_contacts": n_contacts,
                "near_query": _near_query(all_interface),
            }
        )

    # Determine output directory
    project = state.project()
    target_dir = project.artifact_dir("structures", out)
    stem = source.stem

    # Write artifact
    artifact_record: dict[str, Any] = {
        "schema": ARTIFACT_SCHEMA,
        "structure": source.name,
        "chains": all_chains,
        "cutoff_angstrom": cutoff,
        "interfaces": interfaces,
    }
    if chain is not None:
        artifact_record["chain_filter"] = chain

    artifact_path = target_dir / f"{stem}.interface.artifact.json"
    artifact_path.write_text(
        json.dumps(artifact_record, indent=2) + "\n", encoding="utf-8"
    )

    # Provenance sidecar
    params: dict[str, Any] = {
        "structure": source.name,
        "cutoff_angstrom": cutoff,
    }
    if chain is not None:
        params["chain_filter"] = chain

    sidecar = provenance.Sidecar(
        tool="structure",
        subcommand="interface",
        endpoint=None,
        parameters=params,
    )
    sidecar.note("structure_sha256", provenance.sha256_file(source))
    sidecar.note("chains_found", all_chains)
    sidecar.note("n_interfaces", len(interfaces))
    sidecar.note(
        "n_interface_residues",
        sum(
            len(iface["residues_chain_a"]) + len(iface["residues_chain_b"])
            for iface in interfaces
        ),
    )
    sidecar.add_output(artifact_path)
    meta_path = sidecar.write(target_dir / f"{stem}.interface.meta.json")

    # Emit output
    for iface in interfaces:
        pair = iface["chain_pair"]
        n_a = len(iface["residues_chain_a"])
        n_b = len(iface["residues_chain_b"])
        emit.line(
            f"# Interface {pair[0]}-{pair[1]}: "
            f"{n_a} residues on {pair[0]}, {n_b} on {pair[1]} "
            f"({iface['n_contacts']} contacts)"
        )
        emit.line(f'# Feed to: dde pocket run --near "{iface["near_query"]}"')

    if not interfaces:
        emit.line(f"No interfaces found at {cutoff} A cutoff in {source.name}")

    emit.data("schema", ARTIFACT_SCHEMA)
    emit.data("n_interfaces", len(interfaces))
    emit.data("interfaces", interfaces)
    emit.path(artifact_path, role="interface")
    emit.path(meta_path, role="sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# annotate-topology command
# ---------------------------------------------------------------------------


@structure.command("annotate-topology")
@click.argument("identifier")
@click.option(
    "--pocket-record",
    required=True,
    type=click.Path(),
    help="Path to pocket record from `dde pocket run` (JSON).",
)
@out_option
@output_options
@pass_state
def annotate_topology(
    state: AppState,
    identifier: str,
    pocket_record: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Annotate pocket-lining residues with GPCR transmembrane topology.

    Accepts a gene symbol (human) or UniProt accession plus a pocket
    record from `dde pocket run`.  Fetches TM boundaries from UniProt
    and maps them onto the pocket-lining residues, identifying candidate
    orthosteric sites (TM3+TM6+TM7 lining) and bundle-void pockets.
    """
    # -- resolve input --------------------------------------------------------
    accession, gene_from_search = resolve_accession(identifier)
    pocket_path = resolve_artifact(state, pocket_record, "pocket record")
    pocket_doc = provenance.read_json(pocket_path, "pocket record")

    pockets = pocket_doc.get("pockets") or []
    if not pockets:
        raise UsageError(
            "pocket record contains no pockets",
            detail=f"source: {pocket_path}",
            remedy="run `dde pocket run` on a structure with detectable pockets",
        )

    # -- fetch topology -------------------------------------------------------
    topology = fetch_topology(accession)
    tm_regions = topology["tm_regions"]

    gene_label = gene_from_search or topology.get("gene") or accession

    if not tm_regions:
        # Non-GPCR or no annotated TM regions — still report, don't fail.
        pass

    # -- map pockets ----------------------------------------------------------
    pocket_annotations = []
    bundle_void_relays: list[dict[str, str]] = []

    for pocket_entry in pockets:
        mapping = map_pocket_to_topology(pocket_entry, tm_regions)
        pocket_annotations.append(mapping)

        if mapping["bundle_void_advisory"]:
            bundle_void_relays.append(
                provenance.relay(
                    "pocket.likely_bundle_void",
                    mapping["bundle_void_reason"],
                )
            )

    # -- build artifact -------------------------------------------------------
    project = state.project()
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)

    stem = sanitize_slug(gene_label.lower())
    record = {
        "tool": TOOL_TOPOLOGY,
        "identifier": identifier,
        "accession": accession,
        "gene": topology.get("gene"),
        "organism": topology.get("organism"),
        "sequence_length": topology.get("sequence_length"),
        "n_tm_regions": len(tm_regions),
        "tm_regions": tm_regions,
        "topo_domains": topology.get("topo_domains") or [],
        "pocket_source": pocket_path.name,
        "pocket_source_sha256": provenance.sha256_file(pocket_path),
        "n_pockets_annotated": len(pocket_annotations),
        "pocket_annotations": pocket_annotations,
    }

    artifact_path = target_dir / f"{stem}.topology-annotation.artifact.json"
    artifact_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    # -- sidecar --------------------------------------------------------------
    sidecar = provenance.Sidecar(
        tool=TOOL_TOPOLOGY,
        subcommand="annotate-topology",
        endpoint=f"{UNIPROT_API}/{accession}.json",
        parameters={
            "identifier": identifier,
            "accession": accession,
            "pocket_record": pocket_path.name,
        },
    )
    sidecar.note("accession", accession)
    sidecar.note("gene", topology.get("gene"))
    sidecar.note("n_tm_regions", len(tm_regions))
    sidecar.note("pocket_source_sha256", provenance.sha256_file(pocket_path))
    sidecar.add_output(artifact_path)

    if not tm_regions:
        sidecar.warn(
            f"No transmembrane regions annotated in UniProt for {accession}. "
            "This target may not be a transmembrane protein, or TM annotations "
            "may not yet be curated."
        )

    for relay_rec in bundle_void_relays:
        sidecar.warn(relay_rec["message"], code="pocket.likely_bundle_void")

    meta_path = sidecar.write(target_dir / f"{stem}.topology-annotation.meta.json")

    # -- output ---------------------------------------------------------------
    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("accession", accession)
    emit.data("gene", topology.get("gene"))
    emit.data("n_tm_regions", len(tm_regions))
    emit.data("n_pockets_annotated", len(pocket_annotations))

    if not tm_regions:
        emit.line(f"{gene_label}: no TM regions in UniProt for {accession}")
    else:
        emit.line(f"{gene_label}: {len(tm_regions)} TM regions from UniProt")

    for ann in pocket_annotations:
        rank = ann["rank"]
        dscore = ann.get("druggability_score")
        dscore_str = (
            f"drug_score={dscore:.2f}" if dscore is not None else "drug_score=N/A"
        )
        header = f"Pocket {rank} (rank {rank}, {dscore_str}):"
        emit.line(header)
        if ann["tm_segments"]:
            for seg in ann["tm_segments"]:
                res_str = ", ".join(seg["residues"])
                emit.line(f"  {seg['label']}: {seg['count']} residues ({res_str})")
            if ann["orthosteric_candidate"]:
                emit.line("  → Candidate orthosteric site (TM3+TM6+TM7 lining)")
            if ann["bundle_void_advisory"]:
                n_segs = ann["n_tm_segments"]
                n_alpha = ann.get("n_alpha_spheres")
                emit.line(
                    f"  → Bundle void advisory: {n_segs} TM segments,"
                    f" {int(n_alpha) if n_alpha else '?'} alpha spheres"
                )
        else:
            emit.line("  No TM residues")

    for relay_rec in bundle_void_relays:
        emit.line(f"relay {relay_rec['code']}: {relay_rec['message']}")

    emit.path(artifact_path, role="topology_annotation")
    emit.path(meta_path, role="sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# surface command
# ---------------------------------------------------------------------------


def _parse_near_residues(near: str) -> list[tuple[str, int]]:
    """Parse a ``--near`` residue specification like ``A:42,A:43,B:10``.

    Returns a list of ``(chain, resnum)`` tuples.
    """
    result: list[tuple[str, int]] = []
    for token in near.split(","):
        token = token.strip()
        if ":" in token:
            parts = token.split(":", 1)
            try:
                result.append((parts[0], int(parts[1])))
            except ValueError:
                continue
        else:
            # Bare residue number — assume chain "_".
            try:
                result.append(("_", int(token)))
            except ValueError:
                continue
    return result


def _format_residue_ranges(resnums: list[int]) -> str:
    """Format a sorted list of residue numbers into compact ranges.

    E.g., [1, 2, 3, 7, 8, 15] → "1-3, 7-8, 15"
    """
    if not resnums:
        return ""
    resnums = sorted(set(resnums))
    ranges: list[str] = []
    start = resnums[0]
    end = start
    for n in resnums[1:]:
        if n == end + 1:
            end = n
        else:
            ranges.append(f"{start}-{end}" if end > start else str(start))
            start = end = n
    ranges.append(f"{start}-{end}" if end > start else str(start))
    return ", ".join(ranges)


@structure.command("surface")
@click.argument("structure_file", type=click.Path())
@click.option(
    "--gene",
    default=None,
    help="Gene symbol or UniProt accession — fetches glycosylation sites.",
)
@click.option(
    "--near",
    default=None,
    help="Residue specification (e.g. A:42,A:43) for patch-level analysis.",
)
@out_option
@output_options
@pass_state
def surface_cmd(
    state: AppState,
    structure_file: str,
    gene: str | None,
    near: str | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Compute per-residue solvent-accessible surface area (SASA) and RSA.

    Reads a PDB or mmCIF structure and applies the Shrake-Rupley algorithm
    to compute per-atom SASA, then aggregates to per-residue SASA and
    relative solvent accessibility (RSA).  RSA is normalised against
    Tien et al. 2013 theoretical maxASA values.

    When ``--gene`` is provided, glycosylation sites are fetched from
    UniProt and reported alongside the surface analysis.

    The B-factor column is extracted as a pLDDT proxy for AlphaFold
    structures — residues with pLDDT < 50 are flagged as likely disordered.

    \b
    Outputs:
      {stem}.surface.artifact.json — surface accessibility record
      {stem}.surface.meta.json     — provenance sidecar
    """
    emit = Emitter(as_json=as_json, quiet=quiet)

    source = resolve_artifact(state, structure_file, "structure")
    fmt = detect_structure_format(source)
    text = source.read_text(encoding="utf-8", errors="replace")

    # Parse atoms.
    if fmt == "cif":
        atoms = _parse_atoms_cif(text)
    else:
        atoms = _parse_atoms_pdb(text)

    if not atoms:
        raise ArtifactError(
            f"no atom coordinates found in {source.name}",
            detail="the structure file appears empty or unparseable",
            remedy="check that the file is a valid PDB or mmCIF structure "
            "with ATOM/HETATM records",
        )

    # Load thresholds.
    ts = thresholds_mod.load("surface", state.project().root)
    rsa_exposed_t = ts.get("rsa_exposed")
    rsa_highly_exposed_t = ts.get("rsa_highly_exposed")
    plddt_disorder_t = ts.get("plddt_disorder")

    # Compute SASA.
    probe_radius = 1.4
    n_points = 100
    per_atom_sasa = _compute_sasa(atoms, probe_radius=probe_radius, n_points=n_points)

    # Compute per-residue RSA.
    per_residue = _compute_rsa(atoms, per_atom_sasa)

    # Classify residues.
    for res in per_residue:
        rsa = res.get("rsa")
        if rsa is not None:
            if rsa > rsa_highly_exposed_t:
                res["classification"] = "highly_exposed"
            elif rsa > rsa_exposed_t:
                res["classification"] = "exposed"
            else:
                res["classification"] = "buried"
        else:
            res["classification"] = "unknown"

    # Summary statistics.
    total_sasa = sum(r["sasa"] for r in per_residue)
    rsa_values = [r["rsa"] for r in per_residue if r["rsa"] is not None]
    n_residues = len(rsa_values)
    n_exposed = sum(1 for v in rsa_values if v > rsa_exposed_t)
    n_highly_exposed = sum(1 for v in rsa_values if v > rsa_highly_exposed_t)
    mean_rsa = sum(rsa_values) / n_residues if n_residues > 0 else 0.0

    # Per-chain profiles.
    chain_data: dict[str, list[float]] = {}
    for res in per_residue:
        if res["rsa"] is not None:
            chain_data.setdefault(res["chain"], []).append(res["rsa"])
    chain_profiles: dict[str, dict[str, Any]] = {}
    for chain_id in sorted(chain_data):
        vals = chain_data[chain_id]
        chain_mean_rsa = sum(vals) / len(vals) if vals else 0.0
        chain_n_exposed = sum(1 for v in vals if v > rsa_exposed_t)
        chain_profiles[chain_id] = {
            "mean_rsa": round(chain_mean_rsa, 2),
            "fraction_exposed": round(chain_n_exposed / len(vals), 2) if vals else 0.0,
        }

    # Disorder residues (pLDDT < threshold).
    disorder_residues = [
        {"chain": r["chain"], "resnum": r["resnum"], "plddt": r["plddt"]}
        for r in per_residue
        if r["plddt"] < plddt_disorder_t and r["plddt"] > 0
    ]

    # Glycosylation sites (if --gene provided).
    glycosylation_sites: list[dict[str, Any]] = []
    if gene is not None:
        accession, _gene_from_search = resolve_accession(gene)
        url = f"{UNIPROT_API}/{accession}.json"
        data = http.get_json(url, qps=qps_for_host("rest.uniprot.org"), timeout=30.0)
        features = data.get("features") or []
        glycosylation_sites = _parse_glycosylation_sites(features)

    # --near patch analysis.
    patch_analysis: dict[str, Any] | None = None
    if near is not None:
        near_residues = _parse_near_residues(near)
        if near_residues:
            patch = [
                r for r in per_residue if (r["chain"], r["resnum"]) in near_residues
            ]
            if patch:
                patch_rsa = [r["rsa"] for r in patch if r["rsa"] is not None]
                patch_n_exposed = sum(1 for v in patch_rsa if v > rsa_exposed_t)
                patch_n_highly = sum(1 for v in patch_rsa if v > rsa_highly_exposed_t)
                patch_mean_rsa = sum(patch_rsa) / len(patch_rsa) if patch_rsa else 0.0

                # Check glycosylation sites in patch.
                patch_glyco = []
                for gs in glycosylation_sites:
                    if any(r["resnum"] == gs["position"] for r in patch):
                        patch_glyco.append(gs)

                patch_classification = "buried"
                if patch_mean_rsa > rsa_highly_exposed_t:
                    patch_classification = "highly_exposed"
                elif patch_mean_rsa > rsa_exposed_t:
                    patch_classification = "exposed"

                patch_analysis = {
                    "query": near,
                    "n_residues": len(patch),
                    "n_with_rsa": len(patch_rsa),
                    "mean_rsa": round(patch_mean_rsa, 2),
                    "classification": patch_classification,
                    "n_exposed": patch_n_exposed,
                    "n_highly_exposed": patch_n_highly,
                    "glycosylation_sites_in_patch": patch_glyco,
                    "residues": patch,
                }

    # Build artifact.
    project = state.project()
    target_dir = project.artifact_dir("structures", out)
    stem = source.stem

    summary = {
        "total_sasa": round(total_sasa, 1),
        "fraction_exposed": round(n_exposed / n_residues, 2) if n_residues else 0.0,
        "fraction_highly_exposed": (
            round(n_highly_exposed / n_residues, 2) if n_residues else 0.0
        ),
        "mean_rsa": round(mean_rsa, 2),
        "chain_profiles": chain_profiles,
    }

    artifact_record: dict[str, Any] = {
        "schema": SURFACE_ARTIFACT_SCHEMA,
        "structure": source.name,
        "probe_radius": probe_radius,
        "n_points": n_points,
        "rsa_reference": "Tien_2013",
        "per_residue": per_residue,
        "summary": summary,
        "glycosylation_sites": glycosylation_sites,
        "disorder_residues": disorder_residues,
    }
    if patch_analysis is not None:
        artifact_record["patch_analysis"] = patch_analysis

    artifact_path = target_dir / f"{stem}.surface.artifact.json"
    artifact_path.write_text(
        json.dumps(artifact_record, indent=2) + "\n", encoding="utf-8"
    )

    # Provenance sidecar.
    params: dict[str, Any] = {
        "structure": source.name,
        "probe_radius": probe_radius,
        "n_points": n_points,
    }
    if gene is not None:
        params["gene"] = gene
    if near is not None:
        params["near"] = near

    sidecar = provenance.Sidecar(
        tool="structure",
        subcommand="surface",
        endpoint=f"{UNIPROT_API}/{gene}.json" if gene else None,
        parameters=params,
    )
    sidecar.note("structure_sha256", provenance.sha256_file(source))
    sidecar.note("n_residues", len(per_residue))
    sidecar.note("total_sasa", round(total_sasa, 1))
    sidecar.note("threshold_set", ts.tag)
    sidecar.add_output(artifact_path)

    # Mandatory relays — always fired.
    sidecar.warn(
        provenance.RELAY_CODES["surface.sasa_is_static_snapshot"],
        code="surface.sasa_is_static_snapshot",
    )
    sidecar.warn(
        provenance.RELAY_CODES["surface.rsa_reference_values"],
        code="surface.rsa_reference_values",
    )

    meta_path = sidecar.write(target_dir / f"{stem}.surface.meta.json")

    # Human-readable output.
    emit.line(f"Surface accessibility analysis: {source.name}")
    emit.line(f"  Total SASA: {total_sasa:,.0f} A^2")
    emit.line(
        f"  Exposed residues: {n_exposed}/{n_residues} "
        f"({n_exposed * 100 // n_residues if n_residues else 0}%), "
        f"highly exposed: {n_highly_exposed}/{n_residues} "
        f"({n_highly_exposed * 100 // n_residues if n_residues else 0}%)"
    )
    emit.line(f"  Mean RSA: {mean_rsa:.2f}")

    for chain_id, profile in chain_profiles.items():
        emit.line(
            f"  Chain {chain_id}: mean RSA {profile['mean_rsa']}, "
            f"{int(profile['fraction_exposed'] * 100)}% exposed"
        )

    if disorder_residues:
        disorder_resnums = [r["resnum"] for r in disorder_residues]
        emit.line(
            f"  Disordered regions (pLDDT < {plddt_disorder_t:.0f}): "
            f"residues {_format_residue_ranges(disorder_resnums)}"
        )

    if glycosylation_sites:
        glyco_strs = []
        for gs in glycosylation_sites:
            glyco_strs.append(f"N{gs['position']} ({gs['type']})")
        emit.line(f"  Glycosylation sites: {', '.join(glyco_strs)}")

    if patch_analysis is not None:
        pa = patch_analysis
        emit.line(f"  Queried patch ({pa['query']}):")
        emit.line(f"    Mean RSA: {pa['mean_rsa']} ({pa['classification']})")
        emit.line(
            f"    {pa['n_exposed']}/{pa['n_residues']} residues exposed, "
            f"{pa['n_highly_exposed']} highly exposed"
        )
        if pa["glycosylation_sites_in_patch"]:
            for gs in pa["glycosylation_sites_in_patch"]:
                emit.line(
                    f"    Glycosylation site N{gs['position']} is within the patch"
                )
        min_patch = ts.get("min_exposed_patch_residues")
        if pa["n_exposed"] >= min_patch:
            emit.line("    -> Candidate antibody-accessible surface")

    emit.data("schema", SURFACE_ARTIFACT_SCHEMA)
    emit.data("total_sasa", round(total_sasa, 1))
    emit.data("n_residues", n_residues)
    emit.data("n_exposed", n_exposed)
    emit.data("mean_rsa", round(mean_rsa, 2))
    emit.path(artifact_path, role="surface")
    emit.path(meta_path, role="sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# superimpose command
# ---------------------------------------------------------------------------

SUPERIMPOSE_ARTIFACT_SCHEMA = "dde.structure-superposition.v1"

#: Standard one-letter codes for three-letter amino acid names.
_AA_3TO1: dict[str, str] = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "PHE": "F",
    "PRO": "P",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
    "MSE": "M",  # selenomethionine → methionine
}

#: Backbone atom names used for --atoms backbone mode.
_BACKBONE_ATOMS: frozenset[str] = frozenset({"N", "CA", "C", "O"})


def _parse_atoms_with_names_pdb(text: str) -> list[dict[str, Any]]:
    """Parse PDB ATOM records including atom name (for superposition)."""
    atoms: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        try:
            atom_name = line[12:16].strip()
            resname = line[17:20].strip()
            chain = line[21].strip() or "_"
            resnum = int(line[22:26])
            x = float(line[30:38])
            y = float(line[38:46])
            z = float(line[46:54])
        except (ValueError, IndexError):
            continue
        # Skip hydrogens
        element = line[76:78].strip() if len(line) >= 78 else ""
        if not element:
            element = atom_name.lstrip("0123456789")[:1]
        if element in ("H", "D"):
            continue
        atoms.append(
            {
                "chain": chain,
                "resnum": resnum,
                "resname": resname,
                "atom_name": atom_name,
                "x": x,
                "y": y,
                "z": z,
                "element": element,
            }
        )
    return atoms


def _parse_atoms_with_names_cif(text: str) -> list[dict[str, Any]]:
    """Parse mmCIF ATOM records including atom name (for superposition)."""
    lines = text.splitlines()
    columns: list[str] = []
    data_start = 0
    in_atom_site = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("_atom_site."):
            in_atom_site = True
            columns.append(stripped.split(".")[1])
        elif in_atom_site:
            data_start = i
            break

    if not columns:
        return []

    def _col(preferred: str, fallback: str) -> int | None:
        if preferred in columns:
            return columns.index(preferred)
        if fallback in columns:
            return columns.index(fallback)
        return None

    col_chain = _col("auth_asym_id", "label_asym_id")
    col_resnum = _col("auth_seq_id", "label_seq_id")
    col_resname = _col("label_comp_id", "label_comp_id")
    col_atom_name = _col("auth_atom_id", "label_atom_id")
    col_x = columns.index("Cartn_x") if "Cartn_x" in columns else None
    col_y = columns.index("Cartn_y") if "Cartn_y" in columns else None
    col_z = columns.index("Cartn_z") if "Cartn_z" in columns else None
    col_element = columns.index("type_symbol") if "type_symbol" in columns else None

    required = (col_chain, col_resnum, col_resname, col_atom_name, col_x, col_y, col_z)
    if any(c is None for c in required):
        return []

    atoms: list[dict[str, Any]] = []
    for line in lines[data_start:]:
        if not line.startswith(("ATOM", "HETATM")):
            continue
        fields = line.split()
        try:
            chain = fields[col_chain] or "_"  # type: ignore[index]
            resnum = int(fields[col_resnum])  # type: ignore[index]
            resname = fields[col_resname]  # type: ignore[index]
            atom_name = fields[col_atom_name]  # type: ignore[index]
            x = float(fields[col_x])  # type: ignore[index]
            y = float(fields[col_y])  # type: ignore[index]
            z = float(fields[col_z])  # type: ignore[index]
        except (ValueError, IndexError):
            continue
        element = ""
        if col_element is not None:
            try:
                element = fields[col_element]
            except IndexError:
                pass
        if not element:
            element = atom_name.lstrip("0123456789")[:1]
        if element in ("H", "D"):
            continue
        atoms.append(
            {
                "chain": chain,
                "resnum": resnum,
                "resname": resname,
                "atom_name": atom_name,
                "x": x,
                "y": y,
                "z": z,
                "element": element,
            }
        )
    return atoms


def _collect_residues(
    atoms: list[dict[str, Any]],
    chain_filter: str | None,
) -> list[dict[str, Any]]:
    """Group atoms by (chain, resnum) and collect per-atom coordinates.

    Returns sorted list of residue dicts with keys: chain, resnum,
    resname, one_letter, atom_coords (dict mapping atom_name → (x,y,z)).
    """
    grouped: dict[tuple[str, int], dict[str, Any]] = {}
    for atom in atoms:
        if chain_filter is not None and atom["chain"] != chain_filter:
            continue
        key = (atom["chain"], atom["resnum"])
        if key not in grouped:
            grouped[key] = {
                "chain": atom["chain"],
                "resnum": atom["resnum"],
                "resname": atom["resname"],
                "one_letter": _AA_3TO1.get(atom["resname"], "X"),
                "atom_coords": {},
            }
        grouped[key]["atom_coords"][atom["atom_name"]] = (
            atom["x"],
            atom["y"],
            atom["z"],
        )
    return [grouped[k] for k in sorted(grouped)]


def _sequence_from_residues(residues: list[dict[str, Any]]) -> str:
    """Build a one-letter sequence string from residue list."""
    return "".join(r["one_letter"] for r in residues)


def _align_sequences(seq_ref: str, seq_mob: str) -> list[tuple[int, int]]:
    """Align two sequences and return matched position pairs.

    Uses a simple Needleman-Wunsch global alignment with identity scoring:
    match=+2, mismatch=-1, gap=-2.  Returns a list of (ref_idx, mob_idx)
    tuples for aligned non-gap positions.

    This is a self-contained implementation to avoid external alignment
    dependencies.  For the structural comparison use-case (same or closely
    related proteins), even a simple aligner produces correct residue
    correspondence.
    """
    n, m = len(seq_ref), len(seq_mob)
    if n == 0 or m == 0:
        return []

    # Scoring parameters
    match_score = 2
    mismatch_score = -1
    gap_penalty = -2

    # DP matrix
    score = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = i * gap_penalty
    for j in range(1, m + 1):
        score[0][j] = j * gap_penalty

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            s = match_score if seq_ref[i - 1] == seq_mob[j - 1] else mismatch_score
            score[i][j] = max(
                score[i - 1][j - 1] + s,
                score[i - 1][j] + gap_penalty,
                score[i][j - 1] + gap_penalty,
            )

    # Traceback
    pairs: list[tuple[int, int]] = []
    i, j = n, m
    while i > 0 and j > 0:
        s = match_score if seq_ref[i - 1] == seq_mob[j - 1] else mismatch_score
        if score[i][j] == score[i - 1][j - 1] + s:
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif score[i][j] == score[i - 1][j] + gap_penalty:
            i -= 1
        else:
            j -= 1

    pairs.reverse()
    return pairs


def _kabsch_superimpose(
    ref_coords: np.ndarray,
    mob_coords: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Compute optimal rotation and translation to superimpose mob onto ref.

    Uses the Kabsch algorithm (SVD of the cross-covariance matrix).
    This is the same algorithm used by BioPython's Bio.PDB.Superimposer.

    Parameters
    ----------
    ref_coords : (N, 3) array — reference coordinates
    mob_coords : (N, 3) array — mobile coordinates

    Returns
    -------
    rotation : (3, 3) rotation matrix
    translation : (3,) translation vector
    rmsd : float — RMSD after optimal superposition

    The transformed mobile coordinates are: mob_coords @ rotation.T + translation

    Raises
    ------
    ArtifactError
        If the SVD fails (degenerate coordinate set) — per #84,
        an SVD failure must never read as a biological finding.
    """
    n = ref_coords.shape[0]
    if n < 3:
        raise ArtifactError(
            f"only {n} matched atom(s) — need at least 3 for superposition",
            detail="SVD-based superposition requires 3 non-collinear points",
            remedy="check chain selection and sequence matching; the two "
            "structures may share too few residues",
        )

    # Center both coordinate sets
    ref_center = ref_coords.mean(axis=0)
    mob_center = mob_coords.mean(axis=0)
    ref_centered = ref_coords - ref_center
    mob_centered = mob_coords - mob_center

    # Cross-covariance matrix
    H = mob_centered.T @ ref_centered

    try:
        U, _S, Vt = np.linalg.svd(H)
    except np.linalg.LinAlgError as exc:
        raise ArtifactError(
            "SVD failed during superposition — this is an alignment failure, "
            "not a finding about the structures",
            detail=str(exc),
            remedy="check that the coordinate sets are not degenerate "
            "(e.g. all atoms collinear)",
        ) from exc

    # Ensure proper rotation (det = +1, not reflection)
    d = np.linalg.det(Vt.T @ U.T)
    sign_matrix = np.eye(3)
    sign_matrix[2, 2] = np.sign(d)

    rotation = Vt.T @ sign_matrix @ U.T
    translation = ref_center - mob_center @ rotation.T

    # Compute RMSD
    transformed = mob_coords @ rotation.T + translation
    diff = ref_coords - transformed
    rmsd = float(np.sqrt(np.mean(np.sum(diff * diff, axis=1))))

    return rotation, translation, rmsd


def _write_transformed_pdb(
    atoms: list[dict[str, Any]],
    rotation: np.ndarray,
    translation: np.ndarray,
    output_path: Path,
) -> None:
    """Write a PDB file with transformed coordinates.

    Applies the rotation and translation to all atom coordinates and
    writes standard PDB ATOM records.
    """
    lines: list[str] = []
    lines.append("REMARK   Aligned by dde structure superimpose\n")
    for i, atom in enumerate(atoms, start=1):
        coord = np.array([atom["x"], atom["y"], atom["z"]])
        transformed = coord @ rotation.T + translation
        x, y, z = transformed
        atom_name = atom.get("atom_name", "CA")
        # Pad atom name to 4 characters as per PDB format
        if len(atom_name) < 4:
            atom_name_fmt = f" {atom_name:<3s}"
        else:
            atom_name_fmt = f"{atom_name:<4s}"
        chain = atom.get("chain", "_")
        if chain == "_":
            chain = " "
        resname = atom.get("resname", "UNK")
        resnum = atom.get("resnum", 0)
        element = atom.get("element", "")
        lines.append(
            f"ATOM  {i:5d} {atom_name_fmt}{resname:>3s} {chain}{resnum:4d}    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00          {element:>2s}\n"
        )
    lines.append("END\n")
    output_path.write_text("".join(lines), encoding="utf-8")


@structure.command("superimpose")
@click.argument("ref", type=click.Path())
@click.argument("mobile", type=click.Path())
@click.option(
    "--chain-ref",
    default=None,
    help="Chain ID to use from the reference structure.",
)
@click.option(
    "--chain-mobile",
    default=None,
    help="Chain ID to use from the mobile structure.",
)
@click.option(
    "--atoms",
    "atom_mode",
    type=click.Choice(["ca", "backbone", "all"], case_sensitive=False),
    default="ca",
    show_default=True,
    help="Which atoms to use for RMSD: ca (C-alpha only), backbone "
    "(N, CA, C, O), or all heavy atoms.",
)
@out_option
@output_options
@pass_state
def superimpose_cmd(
    state: AppState,
    ref: str,
    mobile: str,
    chain_ref: str | None,
    chain_mobile: str | None,
    atom_mode: str,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Superimpose two structures and compute RMSD.

    Reads a reference and mobile structure (PDB or mmCIF), aligns
    sequences to establish residue correspondence, then computes the
    optimal rigid-body superposition using the Kabsch (SVD) algorithm.

    Reports global RMSD, per-residue distances, matched residue count,
    and the transformation matrix.  Writes the aligned mobile structure
    to a PDB file.

    RMSD > 2.0 A is flagged as significant structural divergence.

    \b
    Outputs:
      {stem}.superimposed.pdb           — aligned mobile structure
      {stem}.superposition.artifact.json — superposition record
      {stem}.superposition.meta.json     — provenance sidecar
    """
    emit = Emitter(as_json=as_json, quiet=quiet)

    # --- resolve inputs ---
    ref_path = resolve_artifact(state, ref, "reference structure")
    mob_path = resolve_artifact(state, mobile, "mobile structure")

    ref_fmt = detect_structure_format(ref_path)
    mob_fmt = detect_structure_format(mob_path)

    ref_text = ref_path.read_text(encoding="utf-8", errors="replace")
    mob_text = mob_path.read_text(encoding="utf-8", errors="replace")

    # --- parse atoms with names ---
    if ref_fmt == "cif":
        ref_atoms = _parse_atoms_with_names_cif(ref_text)
    else:
        ref_atoms = _parse_atoms_with_names_pdb(ref_text)

    if mob_fmt == "cif":
        mob_atoms = _parse_atoms_with_names_cif(mob_text)
    else:
        mob_atoms = _parse_atoms_with_names_pdb(mob_text)

    if not ref_atoms:
        raise ArtifactError(
            f"no atom coordinates found in reference {ref_path.name}",
            detail="the structure file appears empty or unparseable",
            remedy="check that the file is a valid PDB or mmCIF structure",
        )
    if not mob_atoms:
        raise ArtifactError(
            f"no atom coordinates found in mobile {mob_path.name}",
            detail="the structure file appears empty or unparseable",
            remedy="check that the file is a valid PDB or mmCIF structure",
        )

    # --- collect residues with chain filter ---
    ref_residues = _collect_residues(ref_atoms, chain_ref)
    mob_residues = _collect_residues(mob_atoms, chain_mobile)

    if not ref_residues:
        chains = sorted({a["chain"] for a in ref_atoms})
        raise UsageError(
            "no residues found in reference"
            + (f" chain {chain_ref}" if chain_ref else ""),
            detail=f"available chains: {', '.join(chains)}",
        )
    if not mob_residues:
        chains = sorted({a["chain"] for a in mob_atoms})
        raise UsageError(
            "no residues found in mobile"
            + (f" chain {chain_mobile}" if chain_mobile else ""),
            detail=f"available chains: {', '.join(chains)}",
        )

    # --- sequence alignment for residue correspondence ---
    ref_seq = _sequence_from_residues(ref_residues)
    mob_seq = _sequence_from_residues(mob_residues)

    aligned_pairs = _align_sequences(ref_seq, mob_seq)

    if not aligned_pairs:
        raise ArtifactError(
            "no residues could be matched between the two structures",
            detail="sequence alignment produced no aligned positions",
            remedy="check that the structures contain overlapping protein sequences; "
            "use --chain-ref and --chain-mobile to select the correct chains",
        )

    # --- extract atom coordinates for matched residues ---
    ref_coord_list: list[np.ndarray] = []
    mob_coord_list: list[np.ndarray] = []
    matched_residue_info: list[dict[str, Any]] = []

    for ref_idx, mob_idx in aligned_pairs:
        ref_res = ref_residues[ref_idx]
        mob_res = mob_residues[mob_idx]
        ref_ac = ref_res["atom_coords"]
        mob_ac = mob_res["atom_coords"]

        if atom_mode == "ca":
            # C-alpha only
            if "CA" in ref_ac and "CA" in mob_ac:
                ref_coord_list.append(np.array(ref_ac["CA"]))
                mob_coord_list.append(np.array(mob_ac["CA"]))
                matched_residue_info.append(
                    {
                        "ref_chain": ref_res["chain"],
                        "ref_resnum": ref_res["resnum"],
                        "ref_resname": ref_res["resname"],
                        "mob_chain": mob_res["chain"],
                        "mob_resnum": mob_res["resnum"],
                        "mob_resname": mob_res["resname"],
                    }
                )
        elif atom_mode == "backbone":
            # All backbone atoms present in both
            common = _BACKBONE_ATOMS & set(ref_ac.keys()) & set(mob_ac.keys())
            if common:
                for aname in sorted(common):
                    ref_coord_list.append(np.array(ref_ac[aname]))
                    mob_coord_list.append(np.array(mob_ac[aname]))
                matched_residue_info.append(
                    {
                        "ref_chain": ref_res["chain"],
                        "ref_resnum": ref_res["resnum"],
                        "ref_resname": ref_res["resname"],
                        "mob_chain": mob_res["chain"],
                        "mob_resnum": mob_res["resnum"],
                        "mob_resname": mob_res["resname"],
                    }
                )
        else:
            # All heavy atoms — match by atom name
            common = set(ref_ac.keys()) & set(mob_ac.keys())
            if common:
                for aname in sorted(common):
                    ref_coord_list.append(np.array(ref_ac[aname]))
                    mob_coord_list.append(np.array(mob_ac[aname]))
                matched_residue_info.append(
                    {
                        "ref_chain": ref_res["chain"],
                        "ref_resnum": ref_res["resnum"],
                        "ref_resname": ref_res["resname"],
                        "mob_chain": mob_res["chain"],
                        "mob_resnum": mob_res["resnum"],
                        "mob_resname": mob_res["resname"],
                    }
                )

    if not ref_coord_list:
        raise ArtifactError(
            "no atom pairs found for superposition after sequence matching",
            detail=f"atom mode: {atom_mode}, matched residue pairs: "
            f"{len(aligned_pairs)}, but no common atoms found",
            remedy="try --atoms all or check that both structures have "
            "the expected atom types",
        )

    ref_coords = np.array(ref_coord_list)
    mob_coords = np.array(mob_coord_list)

    # --- superposition (Kabsch / SVD) ---
    rotation, translation, global_rmsd = _kabsch_superimpose(ref_coords, mob_coords)

    # --- per-residue Ca distances (always computed on Ca for interpretability) ---
    per_residue_distances: list[dict[str, Any]] = []
    for ref_idx, mob_idx in aligned_pairs:
        ref_res = ref_residues[ref_idx]
        mob_res = mob_residues[mob_idx]
        ref_ac = ref_res["atom_coords"]
        mob_ac = mob_res["atom_coords"]
        if "CA" in ref_ac and "CA" in mob_ac:
            ref_ca = np.array(ref_ac["CA"])
            mob_ca = np.array(mob_ac["CA"])
            # Apply transformation to mobile CA
            mob_ca_transformed = mob_ca @ rotation.T + translation
            dist = float(np.linalg.norm(ref_ca - mob_ca_transformed))
            per_residue_distances.append(
                {
                    "ref_chain": ref_res["chain"],
                    "ref_resnum": ref_res["resnum"],
                    "ref_resname": ref_res["resname"],
                    "mob_chain": mob_res["chain"],
                    "mob_resnum": mob_res["resnum"],
                    "mob_resname": mob_res["resname"],
                    "ca_distance": round(dist, 3),
                }
            )

    n_matched = len(matched_residue_info)
    total_ref = len(ref_residues)
    total_mob = len(mob_residues)
    matched_fraction = (
        n_matched / max(total_ref, total_mob) if max(total_ref, total_mob) > 0 else 0.0
    )

    # --- significant divergence flag ---
    significant_divergence = global_rmsd > 2.0

    # --- write aligned mobile structure ---
    project = state.project()
    target_dir = project.artifact_dir("structures", out)
    stem = f"{ref_path.stem}_vs_{mob_path.stem}"

    # Filter mobile atoms by chain if specified
    atoms_to_write = [
        a for a in mob_atoms if chain_mobile is None or a["chain"] == chain_mobile
    ]
    aligned_path = target_dir / f"{stem}.superimposed.pdb"
    _write_transformed_pdb(atoms_to_write, rotation, translation, aligned_path)

    # --- build artifact record ---
    artifact_record: dict[str, Any] = {
        "schema": SUPERIMPOSE_ARTIFACT_SCHEMA,
        "global_rmsd": round(global_rmsd, 4),
        "matched_residues": n_matched,
        "total_ref": total_ref,
        "total_mobile": total_mob,
        "matched_fraction": round(matched_fraction, 4),
        "atom_mode": atom_mode,
        "significant_divergence": significant_divergence,
        "per_residue_distances": [
            {
                "ref_chain": d["ref_chain"],
                "ref_resnum": d["ref_resnum"],
                "ref_resname": d["ref_resname"],
                "mob_chain": d["mob_chain"],
                "mob_resnum": d["mob_resnum"],
                "mob_resname": d["mob_resname"],
                "ca_distance": d["ca_distance"],
            }
            for d in per_residue_distances
        ],
        "ref_file": ref_path.name,
        "mobile_file": mob_path.name,
        "aligned_file": aligned_path.name,
        "transformation": {
            "rotation": rotation.tolist(),
            "translation": translation.tolist(),
        },
    }
    if chain_ref is not None:
        artifact_record["chain_ref"] = chain_ref
    if chain_mobile is not None:
        artifact_record["chain_mobile"] = chain_mobile

    artifact_path = target_dir / f"{stem}.superposition.artifact.json"
    artifact_path.write_text(
        json.dumps(artifact_record, indent=2) + "\n", encoding="utf-8"
    )

    # --- provenance sidecar ---
    params: dict[str, Any] = {
        "ref": ref_path.name,
        "mobile": mob_path.name,
        "atom_mode": atom_mode,
    }
    if chain_ref is not None:
        params["chain_ref"] = chain_ref
    if chain_mobile is not None:
        params["chain_mobile"] = chain_mobile

    sidecar = provenance.Sidecar(
        tool="structure",
        subcommand="superimpose",
        endpoint=None,
        parameters=params,
    )
    sidecar.note("ref_sha256", provenance.sha256_file(ref_path))
    sidecar.note("mobile_sha256", provenance.sha256_file(mob_path))
    sidecar.note("global_rmsd", round(global_rmsd, 4))
    sidecar.note("matched_residues", n_matched)
    sidecar.note("total_ref", total_ref)
    sidecar.note("total_mobile", total_mob)
    sidecar.add_output(artifact_path)
    sidecar.add_output(aligned_path)

    # --- relay: low sequence identity ---
    if matched_fraction < 0.5:
        sidecar.warn(
            f"Only {n_matched} of {max(total_ref, total_mob)} residues "
            f"({matched_fraction:.0%}) could be matched between "
            f"{ref_path.name} and {mob_path.name}. The RMSD describes "
            "the matched subset, not the full structures.",
            code="structure.low_sequence_identity",
        )

    if significant_divergence:
        sidecar.warn(
            f"Global RMSD of {global_rmsd:.2f} A exceeds 2.0 A, "
            "indicating significant structural divergence between "
            f"{ref_path.name} and {mob_path.name}.",
        )

    meta_path = sidecar.write(target_dir / f"{stem}.superposition.meta.json")

    # --- output ---
    emit.line(f"Superposition: {ref_path.name} vs {mob_path.name}")
    emit.line(f"  Atom mode: {atom_mode}")
    emit.line(f"  Matched residues: {n_matched} / ref={total_ref}, mobile={total_mob}")
    emit.line(f"  Global RMSD: {global_rmsd:.4f} A")
    if significant_divergence:
        emit.line("  *** Significant divergence (RMSD > 2.0 A) ***")
    if matched_fraction < 0.5:
        emit.line(f"  WARNING: low matched fraction ({matched_fraction:.0%})")

    emit.data("schema", SUPERIMPOSE_ARTIFACT_SCHEMA)
    emit.data("global_rmsd", round(global_rmsd, 4))
    emit.data("matched_residues", n_matched)
    emit.data("total_ref", total_ref)
    emit.data("total_mobile", total_mob)
    emit.data("significant_divergence", significant_divergence)
    emit.data("mandatory_relays", sidecar.relays)
    emit.path(artifact_path, role="superposition")
    emit.path(aligned_path, role="aligned_mobile")
    emit.path(meta_path, role="sidecar")
    emit.flush()
