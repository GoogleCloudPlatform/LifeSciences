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

"""`dde pocket` — ligand-binding-site detection with fpocket.

Phase 1 (`run`) executes fpocket over a structure and writes what it
produced: a parsed per-pocket record, and fpocket's own output tree
beside it so a structural biologist can dock into the pocket files
without re-running anything. It makes no judgement.

Phase 2 (`analyze`) reads that record, applies the `pocket` threshold
set by name, and says whether there is a site worth pursuing — and, when
asked with `--near`, whether there is one at a particular interface,
which is the question a protein-protein target actually poses.

Two properties of the instrument shape everything here:

* **The drug score is conformation-dependent, and by more than it looks.**
  Not just model-versus-crystal: the CDK2 ATP site — a site with drugs on
  the market — scores 0.939 in 1HCK, 0.293 in 2W1D and 0.172 in 1AQ1.
  One site, three crystal structures, a 0.77 spread across a 0.5 cutoff.
  So a sub-cutoff score is a fact about the coordinates it was computed
  on and nothing more. Every negative verdict here is named
  `…-in-this-conformation` and leaves with a mandatory relay carrying
  those three numbers, because the qualifier is the first thing lost when
  a result is summarised by someone who wanted a yes.
* **Volume is a Monte Carlo estimate seeded from the clock.** It moves
  between runs by a couple of percent and fpocket offers no seed flag.
  Volumes are reported with that tolerance attached, and no verdict is
  keyed on volume alone.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import click

from ..common import (
    AppState,
    beside_or_out,
    load_thresholds,
    out_option,
    output_options,
    pass_state,
    resolve_artifact,
)
from ..core import provenance
from ..core.errors import ArtifactError, DependencyError, UsageError
from ..core.output import Emitter
from ..core.structures import detect_structure_format

TOOL = "fpocket"
ARTIFACT_CLASS = "structures"
THRESHOLD_SET = "pocket"

#: Standard amino acid three-letter codes. Non-protein residues are
#: anything outside this set, after excluding crystallographic waters.
_STANDARD_AMINO_ACIDS: frozenset[str] = frozenset(
    {
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
        "MSE",  # selenomethionine
    }
)

#: Water residues — excluded from non-protein chain detection because
#: every crystal structure has them and they do not affect pocket scoring
#: in a practically relevant way.
_WATER_RESIDUES: frozenset[str] = frozenset({"HOH", "WAT", "DOD", "H2O"})

#: Descriptor lines in fpocket's info.txt, mapped to the field names we
#: store. Keys are matched on the text before the colon, whitespace
#: normalised. Anything unrecognised is kept verbatim under its own
#: label rather than dropped: this is Layer 0, and a descriptor we did
#: not anticipate is still something fpocket said.
_DESCRIPTORS = {
    "Score": "score",
    "Druggability Score": "druggability_score",
    "Number of Alpha Spheres": "n_alpha_spheres",
    "Total SASA": "total_sasa",
    "Polar SASA": "polar_sasa",
    "Apolar SASA": "apolar_sasa",
    "Volume": "volume",
    "Mean local hydrophobic density": "mean_local_hydrophobic_density",
    "Mean alpha sphere radius": "mean_alpha_sphere_radius",
    "Mean alp. sph. solvent access": "mean_alpha_sphere_solvent_access",
    "Apolar alpha sphere proportion": "apolar_alpha_sphere_proportion",
    "Hydrophobicity score": "hydrophobicity_score",
    "Volume score": "volume_score",
    "Polarity score": "polarity_score",
    "Charge score": "charge_score",
    "Proportion of polar atoms": "proportion_polar_atoms",
    "Alpha sphere density": "alpha_sphere_density",
    "Cent. of mass - Alpha Sphere max dist": "centre_of_mass_max_sphere_distance",
    "Flexibility": "flexibility",
}

_POCKET_HEADER = re.compile(r"^Pocket\s+(\d+)\s*:")


@click.group()
def pocket() -> None:
    """Binding-site detection and druggability (fpocket)."""


# ---------------------------------------------------------------------------
# Phase 1
# ---------------------------------------------------------------------------


def _require_fpocket() -> str:
    path = shutil.which(TOOL)
    if path:
        return path
    raise DependencyError(
        "fpocket is not on PATH",
        detail="pocket detection cannot run and nothing about tractability "
        "can be reported from this tool",
        remedy="source the shared tools env.sh, or re-provision the volume "
        "with `tools/install.sh --binaries-only`",
    )


def _is_experimental(structure: Path) -> tuple[bool, str]:
    """Does the file itself say it came from an experiment?

    Read out of the structure rather than out of its filename or its
    sidecar: the sidecar may be absent, and a name is a claim anyone can
    make. `EXPDTA` (PDB) and `_exptl.method` (mmCIF) are written by the
    deposition, so their presence is evidence and their absence is the
    honest default of "not established".
    """
    try:
        head = structure.read_text(encoding="utf-8", errors="replace")[:200_000]
    except OSError as exc:
        raise ArtifactError(f"could not read {structure}", detail=str(exc)) from exc
    for line in head.splitlines():
        if line.startswith("EXPDTA"):
            return True, line[6:].strip() or "EXPDTA"
        if line.strip().startswith("_exptl.method"):
            return True, line.split(None, 1)[-1].strip().strip("'\"")
    return False, "no EXPDTA/_exptl.method record"


def _parse_info(text: str) -> list[dict[str, Any]]:
    pockets: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        header = _POCKET_HEADER.match(line.strip())
        if header:
            current = {"rank": int(header.group(1))}
            pockets.append(current)
            continue
        if current is None or ":" not in line:
            continue
        label, _, value = line.partition(":")
        label = " ".join(label.split())
        value = value.strip()
        key = _DESCRIPTORS.get(label, label)
        try:
            current[key] = float(value)
        except ValueError:
            current[key] = value
    return pockets


def _parse_residues_pdb(text: str) -> list[dict[str, Any]]:
    """Parse residues from a PDB-format pocket atom file (fixed columns)."""
    seen: dict[tuple[str, int], dict[str, Any]] = {}
    for line in text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        try:
            resname = line[17:20].strip()
            chain = line[21].strip() or "_"
            resnum = int(line[22:26])
        except (ValueError, IndexError):
            continue
        seen.setdefault(
            (chain, resnum), {"chain": chain, "resnum": resnum, "resname": resname}
        )
    return [seen[key] for key in sorted(seen)]


def _parse_residues_cif(text: str) -> list[dict[str, Any]]:
    """Parse residues from an mmCIF-format pocket atom file.

    fpocket writes CIF pocket files when the input is CIF.  The column
    order is declared by ``_atom_site.*`` header lines preceding the
    data rows, so we read the header to find ``label_comp_id`` (residue
    name), ``auth_asym_id`` (chain — falls back to ``label_asym_id``),
    and ``auth_seq_id`` (residue number — falls back to ``label_seq_id``).
    """
    lines = text.splitlines()
    # Collect column names from the _atom_site.* header block.
    columns: list[str] = []
    data_start = 0
    in_atom_site = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("_atom_site."):
            in_atom_site = True
            columns.append(stripped.split(".")[1])
        elif in_atom_site:
            # First non-header line after _atom_site block → data starts.
            data_start = i
            break

    if not columns:
        return []

    # Determine column indices for the fields we need.  Prefer auth_*
    # variants (what the depositor called the chain/residue) over label_*
    # (what the mmCIF archive renumbered them to), because fpocket's
    # info.txt and PDB output both use auth numbering.
    def _col(preferred: str, fallback: str) -> int | None:
        if preferred in columns:
            return columns.index(preferred)
        if fallback in columns:
            return columns.index(fallback)
        return None

    col_resname = _col("label_comp_id", "label_comp_id")
    col_chain = _col("auth_asym_id", "label_asym_id")
    col_resnum = _col("auth_seq_id", "label_seq_id")

    if col_resname is None or col_chain is None or col_resnum is None:
        return []

    seen: dict[tuple[str, int], dict[str, Any]] = {}
    for line in lines[data_start:]:
        if not line.startswith(("ATOM", "HETATM")):
            continue
        fields = line.split()
        try:
            resname = fields[col_resname]
            chain = fields[col_chain] or "_"
            resnum = int(fields[col_resnum])
        except (ValueError, IndexError):
            continue
        seen.setdefault(
            (chain, resnum), {"chain": chain, "resnum": resnum, "resname": resname}
        )
    return [seen[key] for key in sorted(seen)]


def _find_pocket_atm(pockets_dir: Path, rank: int) -> Path | None:
    """Locate the atom file for a pocket, regardless of extension.

    fpocket writes ``pocketN_atm.pdb`` for PDB input and
    ``pocketN_atm.cif`` for CIF input.  Check both.
    """
    for ext in (".pdb", ".cif"):
        candidate = pockets_dir / f"pocket{rank}_atm{ext}"
        if candidate.is_file():
            return candidate
    return None


def _parse_residues(atm_file: Path) -> list[dict[str, Any]]:
    """(chain, resnum, resname) for the residues lining one pocket.

    Dispatches to PDB or CIF parsing based on the detected structure
    format.  Uses :func:`detect_structure_format` (content-verified)
    rather than trusting the file extension alone — a mislabelled
    extension should not silently misparse.

    fpocket 4.x writes ``.cif`` pocket files when the input structure
    was mmCIF.
    """
    fmt = detect_structure_format(atm_file)
    text = atm_file.read_text(encoding="utf-8", errors="replace")
    if fmt == "cif":
        return _parse_residues_cif(text)
    return _parse_residues_pdb(text)


def _detect_non_protein_chains(structure_path: Path) -> tuple[bool, list[str]]:
    """Detect whether a structure contains non-protein chains.

    Returns ``(has_non_protein, non_protein_chain_ids)`` where
    ``has_non_protein`` is True if at least one chain contains
    residues that are not standard amino acids (after excluding
    crystallographic waters).

    A chain is considered non-protein if it contains *no* standard
    amino acid residues — i.e., it is entirely composed of ligands,
    nucleic acids, ions, or other non-protein entities.
    """
    text = structure_path.read_text(encoding="utf-8", errors="replace")[:500_000]
    fmt = detect_structure_format(structure_path)

    # Collect residue names per chain.
    chain_residues: dict[str, set[str]] = {}

    if fmt == "cif":
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

        col_comp = (
            columns.index("label_comp_id") if "label_comp_id" in columns else None
        )
        col_chain = None
        for name in ("auth_asym_id", "label_asym_id"):
            if name in columns:
                col_chain = columns.index(name)
                break

        if col_comp is not None and col_chain is not None:
            for line in lines[data_start:]:
                if not line.startswith(("ATOM", "HETATM")):
                    continue
                fields = line.split()
                try:
                    resname = fields[col_comp]
                    chain = fields[col_chain]
                except IndexError:
                    continue
                chain_residues.setdefault(chain, set()).add(resname)
    else:
        # PDB format — fixed-column layout.
        for line in text.splitlines():
            if not line.startswith(("ATOM  ", "HETATM")):
                continue
            resname = line[17:20].strip()
            chain = line[21:22].strip() or "_"
            if resname:
                chain_residues.setdefault(chain, set()).add(resname)

    # A chain is non-protein if none of its residues are standard
    # amino acids, after excluding waters.
    non_protein_chains: list[str] = []
    for chain, residues in sorted(chain_residues.items()):
        non_water = residues - _WATER_RESIDUES
        if not non_water:
            # Chain contains only water — not interesting.
            continue
        if not (non_water & _STANDARD_AMINO_ACIDS):
            # No standard amino acids present → non-protein chain.
            non_protein_chains.append(chain)

    return bool(non_protein_chains), non_protein_chains


def _detect_short_chains(
    structure_path: Path,
    threshold: int = 20,
) -> list[dict[str, Any]]:
    """Detect chains with fewer than *threshold* residues.

    Short chains in crystal structures are often peptidic ligands
    built from standard amino acid residues.  Because they look like
    protein to chain-detection heuristics, they are silently retained
    as part of the receptor, occlude the binding site, and produce
    confidently low druggability scores on targets that are druggable
    in an appropriate conformation.

    Returns a list of dicts::

        [{"chain": "B", "residues": 12, "note": "Possible peptide ligand"}]
    """
    text = structure_path.read_text(encoding="utf-8", errors="replace")[:500_000]
    fmt = detect_structure_format(structure_path)

    # Collect unique residue identifiers per chain.
    chain_residues: dict[str, set[tuple[str, int]]] = {}

    if fmt == "cif":
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

        col_comp = (
            columns.index("label_comp_id") if "label_comp_id" in columns else None
        )
        col_chain = None
        for name in ("auth_asym_id", "label_asym_id"):
            if name in columns:
                col_chain = columns.index(name)
                break
        col_resnum = None
        for name in ("auth_seq_id", "label_seq_id"):
            if name in columns:
                col_resnum = columns.index(name)
                break

        if col_comp is not None and col_chain is not None and col_resnum is not None:
            for line in lines[data_start:]:
                if not line.startswith(("ATOM", "HETATM")):
                    continue
                fields = line.split()
                try:
                    resname = fields[col_comp]
                    chain = fields[col_chain]
                    resnum = int(fields[col_resnum])
                except (IndexError, ValueError):
                    continue
                # Only count standard amino acid residues toward
                # chain length — ligands and waters are not chain
                # residues for this purpose.
                if resname in _STANDARD_AMINO_ACIDS:
                    chain_residues.setdefault(chain, set()).add((resname, resnum))
    else:
        # PDB format — fixed-column layout.
        for line in text.splitlines():
            if not line.startswith(("ATOM  ", "HETATM")):
                continue
            resname = line[17:20].strip()
            chain = line[21:22].strip() or "_"
            try:
                resnum = int(line[22:26])
            except (ValueError, IndexError):
                continue
            if resname in _STANDARD_AMINO_ACIDS:
                chain_residues.setdefault(chain, set()).add((resname, resnum))

    short_chains: list[dict[str, Any]] = []
    for chain, residues in sorted(chain_residues.items()):
        n_residues = len(residues)
        if 0 < n_residues < threshold:
            short_chains.append(
                {
                    "chain": chain,
                    "residues": n_residues,
                    "note": "Possible peptide ligand",
                }
            )

    return short_chains


def _strip_chains(
    structure_path: Path,
    chains_to_strip: set[str],
    output_path: Path,
) -> dict[str, Any]:
    """Remove specified chains from a structure file.

    Supports both PDB and mmCIF formats.  Removes all ATOM/HETATM
    records belonging to the specified chain IDs.

    Returns a dict with provenance information::

        {"stripped_chains": ["B", "C"], "removed_atom_count": 142}
    """
    text = structure_path.read_text(encoding="utf-8", errors="replace")
    fmt = structure_path.suffix.lower()

    kept_lines: list[str] = []
    removed_atom_count = 0

    if fmt in (".cif", ".mmcif"):
        lines = text.splitlines(keepends=True)
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

        col_chain = None
        for name in ("auth_asym_id", "label_asym_id"):
            if name in columns:
                col_chain = columns.index(name)
                break

        kept_lines.extend(lines[:data_start])

        for line in lines[data_start:]:
            if not line.startswith(("ATOM", "HETATM")):
                kept_lines.append(line)
                continue
            if col_chain is not None:
                fields = line.split()
                try:
                    chain_id = fields[col_chain]
                except IndexError:
                    chain_id = ""
                if chain_id in chains_to_strip:
                    removed_atom_count += 1
                    continue
            kept_lines.append(line)
    else:
        # PDB format.
        for line in text.splitlines(keepends=True):
            if not line.startswith(("ATOM  ", "HETATM")):
                kept_lines.append(line)
                continue
            chain_id = line[21:22].strip() or "_"
            if chain_id in chains_to_strip:
                removed_atom_count += 1
            else:
                kept_lines.append(line)

    output_path.write_text("".join(kept_lines), encoding="utf-8")

    return {
        "stripped_chains": sorted(chains_to_strip),
        "removed_atom_count": removed_atom_count,
    }


#: Default druggability score threshold below which a score is
#: considered "low" for the purpose of peptide-occlusion and
#: holo-structure advisories.
_LOW_DRUGGABILITY_THRESHOLD = 0.5


@pocket.command()
@click.argument("structure", type=click.Path())
@click.option(
    "--strip-peptides/--keep-peptides",
    default=False,
    show_default=True,
    help="Automatically remove chains shorter than --peptide-threshold "
    "residues before running fpocket. Stripped chains are recorded "
    "in the sidecar.",
)
@click.option(
    "--peptide-threshold",
    default=20,
    type=int,
    show_default=True,
    help="Maximum residue count for a chain to be considered a possible "
    "peptide ligand (used by --strip-peptides and short-chain detection).",
)
@click.option(
    "--ignore-chain",
    multiple=True,
    help="Chain ID(s) to remove before running fpocket. May be specified "
    "multiple times (e.g. --ignore-chain B --ignore-chain C).",
)
@out_option
@output_options
@pass_state
def run(
    state: AppState,
    structure: str,
    strip_peptides: bool,
    peptide_threshold: int,
    ignore_chain: tuple[str, ...],
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Detect pockets in a structure. Writes descriptors, judges nothing.

    Runs fpocket over a copy of the input, so the program's structure
    file is never modified and fpocket's output tree never lands beside
    it by accident.

    When ``--strip-peptides`` is given, chains shorter than
    ``--peptide-threshold`` residues are removed before fpocket runs.
    When ``--ignore-chain`` is given, the specified chains are removed.
    Both options record which chains were stripped in the sidecar.
    """
    binary = _require_fpocket()
    source = resolve_artifact(state, structure, "structure")
    if source.suffix.lower() not in {".pdb", ".cif", ".mmcif", ".ent"}:
        raise UsageError(
            f"{source.name} is not a structure fpocket reads",
            detail="expected .pdb, .ent, .cif or .mmcif",
            remedy="pass the coordinate file, not its sidecar or analysis record",
        )

    project = state.project()
    target_dir = project.artifact_dir(ARTIFACT_CLASS, out)
    stem = source.stem

    experimental, evidence = _is_experimental(source)

    # --- detect non-protein chains before fpocket invocation ---
    has_non_protein, non_protein_chains = _detect_non_protein_chains(source)

    # --- detect short chains (possible peptide ligands) ---
    short_chains = _detect_short_chains(source, threshold=peptide_threshold)

    # --- determine chains to strip ---
    chains_to_strip: set[str] = set()
    if strip_peptides and short_chains:
        chains_to_strip.update(sc["chain"] for sc in short_chains)
    if ignore_chain:
        chains_to_strip.update(ignore_chain)

    sidecar_params: dict[str, Any] = {
        "structure": source.name,
        "fpocket_defaults": True,
        "peptide_threshold": peptide_threshold,
    }
    if strip_peptides:
        sidecar_params["strip_peptides"] = True
    if ignore_chain:
        sidecar_params["ignore_chain"] = list(ignore_chain)

    sidecar = provenance.Sidecar(
        tool=TOOL,
        subcommand="run",
        endpoint=None,
        parameters=sidecar_params,
    )
    sidecar.note("structure_sha256", provenance.sha256_file(source))
    sidecar.note("experimental_structure", experimental)
    sidecar.note("structure_origin_evidence", evidence)

    # --- optionally strip chains before fpocket ---
    strip_info: dict[str, Any] | None = None
    _strip_tmpdir: str | None = None
    effective_source = source
    if chains_to_strip:
        _strip_tmpdir = tempfile.mkdtemp(prefix="dde-strip-pocket-")
        stripped_path = Path(_strip_tmpdir) / source.name
        strip_info = _strip_chains(source, chains_to_strip, stripped_path)
        effective_source = stripped_path

    try:
        with tempfile.TemporaryDirectory(prefix="dde-fpocket-") as tmp:
            work = Path(tmp) / effective_source.name
            shutil.copyfile(effective_source, work)
            completed = subprocess.run(
                [binary, "-f", str(work)],
                capture_output=True,
                text=True,
                cwd=tmp,
                check=False,
            )
            produced = Path(tmp) / f"{work.stem}_out"
            info = produced / f"{work.stem}_info.txt"
            if completed.returncode != 0 or not info.is_file():
                raise ArtifactError(
                    f"fpocket produced no result for {source.name}",
                    detail=(
                        completed.stderr or completed.stdout or "no output"
                    ).strip()[:400],
                    remedy="check the file is a parseable structure with protein atoms; "
                    "fpocket exits 0 on some malformed inputs without writing output, "
                    "so a missing info.txt is treated as a failure here",
                )

            pockets = _parse_info(info.read_text(encoding="utf-8", errors="replace"))
            empty_residue_pockets: list[int] = []
            for entry in pockets:
                atm = _find_pocket_atm(produced / "pockets", entry["rank"])
                if atm is not None:
                    entry["residues"] = _parse_residues(atm)
                    if not entry["residues"]:
                        empty_residue_pockets.append(entry["rank"])
                else:
                    empty_residue_pockets.append(entry["rank"])
                    entry["residues"] = []

            # Every real pocket has lining residues — an empty list means the
            # parser failed to extract them, not that the pocket is unlinded.
            if empty_residue_pockets:
                ranks = ", ".join(str(r) for r in empty_residue_pockets)
                raise ArtifactError(
                    f"fpocket detected pockets but residue extraction failed "
                    f"for pocket(s) {ranks} in {source.name}",
                    detail="every pocket has lining residues; an empty residue "
                    "list is a parsing failure, not a legitimate finding of "
                    "'no residues line this pocket'. The pocket atom file may "
                    "be missing, empty, or in an unrecognised format.",
                    remedy="check the pocket atom files in the fpocket output "
                    "directory; if the format has changed, _parse_residues "
                    "needs updating",
                )

            # fpocket's own tree, kept whole.
            tree = target_dir / f"{stem}_fpocket"
            if tree.exists():
                shutil.rmtree(tree)
            shutil.copytree(produced, tree)
    finally:
        if _strip_tmpdir:
            shutil.rmtree(_strip_tmpdir, ignore_errors=True)

    record: dict[str, Any] = {
        "tool": TOOL,
        "structure": source.name,
        "structure_sha256": provenance.sha256_file(source),
        "experimental_structure": experimental,
        "structure_origin_evidence": evidence,
        "n_pockets": len(pockets),
        "pockets": pockets,
        "fpocket_output_dir": tree.name,
    }

    # --- non-protein chain context ---
    if has_non_protein:
        record["input_contains_non_protein_chains"] = True
        record["non_protein_chains"] = non_protein_chains
        record["note"] = (
            "Pocket scores computed with non-protein atoms present. "
            "Scores may differ from protein-only analysis."
        )

    # --- short chain context ---
    # Short chains that were NOT stripped are recorded so downstream
    # consumers know the pocket score may be affected by peptide
    # occlusion.
    retained_short_chains = [
        sc for sc in short_chains if sc["chain"] not in chains_to_strip
    ]
    if retained_short_chains:
        record["short_chains_retained"] = retained_short_chains

    # --- stripped chain context ---
    if strip_info is not None:
        record["stripped_chains"] = strip_info["stripped_chains"]
        record["stripped_atom_count"] = strip_info["removed_atom_count"]

    record_path = target_dir / f"{stem}.pockets.json"
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    sidecar.note("n_pockets", len(pockets))
    sidecar.add_output(record_path)
    sidecar.note("fpocket_output_dir", tree.name)

    if short_chains:
        sidecar.note("short_chains_detected", short_chains)
    if strip_info is not None:
        sidecar.note("stripped_chains", strip_info["stripped_chains"])
        sidecar.note("stripped_atom_count", strip_info["removed_atom_count"])

    if has_non_protein:
        sidecar.note("non_protein_chains", non_protein_chains)
        chains_str = ", ".join(non_protein_chains)
        sidecar.warn(
            f"Pocket analysis was run on a structure containing non-protein "
            f"chains ({chains_str}). Drug scores may be inflated relative to "
            "apo-structure scoring. Compare with protein-only analysis for "
            "accurate druggability assessment.",
            code="fpocket.ligand_present_in_input",
        )

    # --- SAFETY-CRITICAL: peptide occlusion relay (#133 Item 2) ---
    # When short chains are retained AND any pocket scores below the
    # druggability threshold, fire a mandatory relay.  A 0.004 score on
    # a druggable target kills programs — the fix makes it impossible
    # to report a confident low score without flagging potential
    # occlusion.
    if retained_short_chains and pockets:
        low_scoring_pockets = [
            p
            for p in pockets
            if (p.get("druggability_score") or 0.0) < _LOW_DRUGGABILITY_THRESHOLD
        ]
        if low_scoring_pockets:
            chains_str = ", ".join(sc["chain"] for sc in retained_short_chains)
            residue_counts = ", ".join(
                f"{sc['chain']}({sc['residues']} residues)"
                for sc in retained_short_chains
            )
            worst = min(
                low_scoring_pockets, key=lambda p: p.get("druggability_score") or 0.0
            )
            worst_score = worst.get("druggability_score") or 0.0
            sidecar.warn(
                f"Pocket {worst.get('rank', '?')} scored {worst_score:.3f} "
                f"but the input structure contains short chain(s) {chains_str} "
                f"({residue_counts}) that may be peptidic ligands occluding "
                "the binding site. Rerun on an apo or small-molecule-bound "
                "conformation before concluding the target is undruggable.",
                code="fpocket.possible_peptide_occlusion",
            )

    # --- Item 3: Low-score advisory on holo structures ---
    # When the structure contains ANY non-receptor content (ligands,
    # peptides, cofactors, non-protein chains, or retained short chains)
    # AND produces a low druggability score, fire a general advisory.
    has_non_receptor_content = has_non_protein or bool(retained_short_chains)
    if has_non_receptor_content and pockets:
        low_scoring = [
            p
            for p in pockets
            if (p.get("druggability_score") or 0.0) < _LOW_DRUGGABILITY_THRESHOLD
        ]
        if low_scoring:
            worst = min(low_scoring, key=lambda p: p.get("druggability_score") or 0.0)
            worst_score = worst.get("druggability_score") or 0.0
            sidecar.warn(
                f"Low druggability score ({worst_score:.3f}) computed on a "
                "structure containing non-receptor chain(s). Pocket scores are "
                "conformation-dependent; assess druggability from an apo or "
                "alternate-conformation structure before concluding "
                "undruggability.",
                code="fpocket.low_score_holo_structure",
            )

    sidecar.warn(
        "Pocket volume is a Monte Carlo estimate seeded from the clock; fpocket "
        "exposes no seed. Repeated runs on one input differ by a few percent, "
        "and two runs inside the same second are identical because the seed has "
        "one-second resolution. Do not read a volume difference below the "
        "declared tolerance as a change.",
    )
    if not experimental:
        sidecar.warn(
            f"{source.name} is not established as an experimental structure "
            f"({evidence}). Druggability scores are conformation-dependent and "
            "were trained on crystal structures; a low score on a predicted or "
            "modelled conformation is not evidence that the site is undruggable.",
            code="fpocket.conformation_dependent",
        )

    meta_path = sidecar.write(target_dir / f"{stem}.pockets.meta.json")

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("n_pockets", len(pockets))
    emit.data("experimental_structure", experimental)
    if short_chains:
        emit.data("short_chains_detected", short_chains)
    if retained_short_chains:
        emit.data("short_chains_retained", retained_short_chains)
    if strip_info is not None:
        emit.data("stripped_chains", strip_info["stripped_chains"])
    emit.path(record_path, role="pockets")
    emit.path(tree, role="fpocket_tree")
    emit.path(meta_path, role="sidecar")
    emit.flush()


# ---------------------------------------------------------------------------
# Phase 2
# ---------------------------------------------------------------------------


def _parse_near(spec: str) -> list[tuple[str, int]]:
    """`A:145,A:146,B:12` → [("A",145), ("A",146), ("B",12)]."""
    residues: list[tuple[str, int]] = []
    for token in spec.replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        chain, _, number = token.partition(":")
        if not number:
            raise UsageError(
                f"{token!r} is not a residue selector",
                detail="expected CHAIN:RESNUM, e.g. A:145",
                remedy="pass --near A:145,A:146 — the chain is required because "
                "residue numbering repeats across chains",
            )
        try:
            residues.append((chain.strip() or "_", int(number)))
        except ValueError as e:
            raise UsageError(
                f"{token!r} does not name a residue number",
                detail="expected CHAIN:RESNUM, e.g. A:145",
            ) from e
    if not residues:
        raise UsageError("--near was given no residues")
    return residues


def _band(dscore: float, thresholds) -> str:
    if dscore >= thresholds.get("druggable_dscore"):
        return "druggable"
    if dscore >= thresholds.get("borderline_dscore"):
        return "borderline"
    return "not-druggable"


@pocket.command()
@click.argument("path", type=click.Path())
@click.option(
    "--near",
    default=None,
    help="Residues defining a site of interest, CHAIN:RESNUM comma-separated. "
    "Answers whether a pocket lines that site, not merely whether the protein "
    "has one somewhere.",
)
@click.option(
    "--druggable", type=float, default=None, help="Override druggable_dscore."
)
@out_option
@output_options
@pass_state
def analyze(
    state: AppState,
    path: str,
    near: str | None,
    druggable: float | None,
    out: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Judge tractability from a pockets record. Reads disk, never the network."""
    source = resolve_artifact(state, path, "pockets record")
    doc = provenance.read_json(source, "pockets record")
    thresholds = load_thresholds(state, THRESHOLD_SET, {"druggable_dscore": druggable})

    pockets = doc.get("pockets") or []
    stem = source.name.replace(".pockets.json", "")

    meta_path = source.with_name(f"{stem}.pockets.meta.json")
    relays: list[dict] = []
    upstream_warnings: list[str] = []
    if meta_path.is_file():
        meta = provenance.read_json(meta_path, "provenance sidecar")
        relays = meta.get("mandatory_relays", []) or []
        upstream_warnings = meta.get("warnings", []) or []

    ranked = sorted(
        pockets,
        key=lambda p: p.get("druggability_score") or 0.0,
        reverse=True,
    )
    best = ranked[0] if ranked else None
    tolerance = thresholds.get("volume_estimate_tolerance")

    advisories: list[str] = []
    site: dict[str, Any] = {}
    if near:
        wanted = set(_parse_near(near))
        hits = []
        for entry in pockets:
            lining = {(r["chain"], r["resnum"]) for r in entry.get("residues", [])}
            overlap = sorted(wanted & lining)
            if overlap:
                hits.append(
                    {
                        "rank": entry.get("rank"),
                        "druggability_score": entry.get("druggability_score"),
                        "volume": entry.get("volume"),
                        "n_alpha_spheres": entry.get("n_alpha_spheres"),
                        "matched_residues": [f"{c}:{n}" for c, n in overlap],
                    }
                )
        hits.sort(key=lambda h: h.get("druggability_score") or 0.0, reverse=True)
        site = {
            "requested": sorted(f"{c}:{n}" for c, n in wanted),
            "pockets_at_site": hits,
        }
        if not hits:
            advisories.append(
                "No detected pocket includes any of the requested residues. "
                "fpocket finds cavities, so this is evidence of no cavity at "
                "that site in this conformation — not of an undruggable protein."
            )

    if best is None:
        verdict = "no-pockets-detected"
        statement = f"fpocket detected no pockets in {doc.get('structure', stem)}."
    else:
        best_score = best.get("druggability_score") or 0.0
        band = _band(best_score, thresholds)
        if near:
            hits = site["pockets_at_site"]
            if not hits:
                verdict = "no-pocket-at-site-in-this-conformation"
                statement = (
                    f"No pocket lines the requested residues; the best pocket "
                    f"anywhere in the structure scores {best_score:.3f} "
                    f"({band})."
                )
            else:
                site_score = hits[0].get("druggability_score") or 0.0
                site_band = _band(site_score, thresholds)
                verdict = {
                    "druggable": "site-druggable",
                    "borderline": "site-borderline",
                    "not-druggable": "site-not-druggable-in-this-conformation",
                }[site_band]
                statement = (
                    f"Pocket {hits[0]['rank']} lines the requested site and "
                    f"scores {site_score:.3f} ({site_band})"
                )
                statement += (
                    "; also the top-ranked pocket."
                    if hits[0]["rank"] == best.get("rank")
                    else f"; the best pocket in the structure scores {best_score:.3f}."
                )
        else:
            verdict = {
                "druggable": "druggable-pocket-present",
                "borderline": "borderline",
                # Named for what was measured, not for what it tempts a
                # reader to conclude. "no-druggable-pocket" travels as a
                # property of the target; this number cannot carry that.
                "not-druggable": "no-druggable-pocket-in-this-conformation",
            }[band]
            statement = (
                f"Best pocket scores {best_score:.3f} ({band}) across "
                f"{len(pockets)} detected pocket(s)."
            )

        if best.get("n_alpha_spheres") and best["n_alpha_spheres"] <= thresholds.get(
            "min_alpha_spheres"
        ):
            advisories.append(
                "The best-scoring pocket sits at fpocket's detection floor "
                f"({int(best['n_alpha_spheres'])} alpha spheres, minimum "
                f"{int(thresholds.get('min_alpha_spheres'))}); treat it as a "
                "candidate to inspect, not as a characterised site."
            )

    # The reported band, whichever score the verdict rests on. A --near
    # query answers about the site, so it is the site's score that must
    # carry the caveat.
    # Did the question asked come back negative? Not "is the best score
    # low" — with --near the question is about the site, and a structure
    # can hold a superb pocket somewhere irrelevant. Answering the site
    # question from the global best is how "no cavity here" becomes "no
    # cavity" on the way to a write-up.
    reported = reported_score = None
    if best is not None:
        reported_score = best.get("druggability_score") or 0.0
        if near:
            hits_here = site.get("pockets_at_site") or []
            # No pocket at the site is the most negative answer the site
            # question has, so it counts as negative rather than as
            # missing. It previously fell through and fired no relay at
            # all — the one negative verdict with nothing attached, and
            # the one a flat protein-protein interface actually returns.
            reported_score = (
                hits_here[0].get("druggability_score") or 0.0 if hits_here else 0.0
            )
        reported = _band(reported_score, thresholds)

    # The finding most likely to be over-read is the negative one, and it
    # is the one this instrument supports least. Measured on four crystal
    # structures of two well-drugged sites, the same CDK2 ATP pocket
    # scores 0.172 (1AQ1), 0.293 (2W1D) and 0.939 (1HCK). Three of those
    # four numbers would be reported by this command as "not druggable"
    # or "borderline" for a site that is drugged in the clinic. So the
    # caveat is attached as a mandatory relay at the moment the verdict
    # goes negative, rather than left to whoever writes it up to remember
    # on the day the answer is disappointing.
    if reported is not None and reported != "druggable":
        relays = [
            *relays,
            provenance.relay(
                "fpocket.single_conformation",
                # The calibration numbers ride in the message, not only
                # in RELAY_CODES: the registered guidance runs past the
                # 150-character stdout budget and clips precisely where
                # the evidence is. The reason has to fit on the line.
                (
                    f"no pocket at the requested site in {doc.get('structure', stem)}"
                    if near and not (site.get("pockets_at_site") or [])
                    else f"{reported_score:.3f} in {doc.get('structure', stem)}"
                )
                + "; the same CDK2 ATP site scores 0.17, 0.29 and 0.94 "
                "in three crystals.",
            ),
        ]

    # The positive verdict has the opposite exposure and needs its own
    # guard. A high score is the number a role with no affinity tool
    # reaches for — template-builder's §8.6 substitution, and the CLI
    # cannot see it happening, because `pocket analyze` is a legitimate
    # invocation whoever is asking. What the CLI can do is say, at the
    # moment the number is produced, what it is not. Conditional for the
    # same reason gnomad.constraint_is_not_safety is: below the cutoff
    # there is no affinity claim available to make, and a relay that
    # fired on every run would be quoted and ignored.
    if reported == "druggable":
        relays = [
            *relays,
            provenance.relay(
                "fpocket.druggability_is_not_affinity",
                # Kept under the stdout budget deliberately: the
                # conformation relay taught me that a clipped message
                # loses exactly its last clause, and the last clause
                # here is the one that names the substitution.
                f"{reported_score:.3f} is cavity shape in "
                f"{doc.get('structure', stem)}; not an affinity, not a "
                "potency, not evidence a compound binds.",
            ),
        ]

    if doc.get("experimental_structure") is False:
        advisories.append(
            "Scores were computed on a structure not established as "
            "experimental. Druggability is conformation-dependent; a low score "
            "here constrains this model, not the target."
        )

    advisories.append(
        f"Volumes are Monte Carlo estimates; differences under "
        f"{tolerance:.0%} between runs are noise, not change."
    )

    metrics = {
        "n_pockets": len(pockets),
        "best_pocket": (
            {
                "rank": best.get("rank"),
                "druggability_score": best.get("druggability_score"),
                "score": best.get("score"),
                "volume": best.get("volume"),
                "n_alpha_spheres": best.get("n_alpha_spheres"),
                # Diffuseness. Large values mean the alpha spheres are
                # spread over what may be several merged surface grooves
                # rather than one cavity — the shape of pocket that
                # scores low without the site being poor.
                "centre_of_mass_max_sphere_distance": best.get(
                    "centre_of_mass_max_sphere_distance"
                ),
            }
            if best
            else None
        ),
        "site": site or None,
        "volume_estimate_tolerance": tolerance,
    }
    assessment = {
        "verdict": verdict,
        "statement": statement,
        "advisories": advisories,
        "relayed_run_warnings": upstream_warnings,
    }

    analysis_path = beside_or_out(state, source, f"{stem}.pocket.analysis.json", out)
    provenance.write_analysis(
        analysis_path,
        source=str(source),
        threshold_set=thresholds.tag,
        thresholds_applied=thresholds.applied(),
        threshold_sources=thresholds.sources(),
        threshold_provenance=thresholds.provenance,
        metrics=metrics,
        assessment=assessment,
        mandatory_relays=relays,
        suppress_warnings=as_json,
    )

    emit = Emitter(as_json=as_json, quiet=quiet)
    emit.data("assessment", assessment)
    emit.data("metrics", metrics)
    emit.data("mandatory_relays", relays)
    emit.data("threshold_set", thresholds.tag)
    emit.line(f"{stem}  [threshold_set {thresholds.tag}]")
    emit.line(f"{verdict}: {statement}")
    for note in advisories:
        emit.line(f"  - {note}")
    # Relays print. A mandatory relay that reaches only the JSON payload
    # is mandatory on the reader who already opened the file, which is
    # not the reader it was written for.
    for record in relays:
        emit.line(f"relay {record['code']}: {record['message']}")
    emit.path(analysis_path, role="analysis")
    emit.flush()
