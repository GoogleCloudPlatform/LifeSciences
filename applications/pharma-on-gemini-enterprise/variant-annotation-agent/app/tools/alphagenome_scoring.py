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

"""AlphaGenome Variant Impact (AVI) scoring, multimodal parsing, and Atlas deep-linking."""

from __future__ import annotations

import csv
import io
import json
import math
import re
import urllib.parse
from typing import Any

from ._http import fetch_json

# 18 Standard AlphaGenome biological tracks/modalities
ALPHAGENOME_TRACKS = [
    "MERGED_SPLICING",
    "MAX_ABS_RNA_SEQ",
    "MAX_ABS_ATAC",
    "MAX_ABS_DNASE",
    "MAX_ABS_CHIP_TF",
    "MAX_ABS_CHIP_HISTONE",
    "MAX_ABS_CAGE",
    "MAX_ABS_PROCAP",
    "MAX_ABS_POLYADENYLATION",
    "MAX_ABS_CONTACT_MAPS",
    "ALPHAMISSENSE",
    "CACTUS_241_WAY",
    "PROTEIN_TERMINATION",
    "START_LOST",
    "STOP_LOST",
    "PHASTCONS_470_WAY",
    "IS_INSERTION",
    "IS_DELETION",
]

_COORD_RE = re.compile(
    r"^(?:chr)?(?P<chrom>[0-9XYM]{1,2}|MT):(?P<pos>\d+):(?P<ref>[ACGTN]+)>(?P<alt>[ACGTN]+)$",
    re.IGNORECASE,
)
_RSID_RE = re.compile(r"^rs\d+$", re.IGNORECASE)


def classify_avi_tier(phred: float) -> str:
    """Classifies AVI Phred score into standardized clinical impact tiers.

    Tiers:
      - >= 20.0: High Impact (top 1% genome-wide)
      - 15.0 - 19.99: Moderate-High Impact (top 3.2% genome-wide)
      - 10.0 - 14.99: Moderate Impact (top 10% genome-wide)
      - < 10.0: Low / Baseline Impact
    """
    if phred >= 20.0:
        return "High Impact"
    elif phred >= 15.0:
        return "Moderate-High Impact"
    elif phred >= 10.0:
        return "Moderate Impact"
    return "Low / Baseline Impact"


def compute_avi_score(
    raw_score: float | None = None,
    quantile: float | None = None,
    phred: float | None = None,
) -> dict[str, Any]:
    """Computes and aligns AVI metrics across raw, tail-quantile, and Phred scales.

    Follows the official AlphaGenome Atlas AVI schema (`alphagenome_atlas_avi.py`):
      - `avi_quantile` (`quantile`) is the tail quantile `1 - CDF` in `[1e-7, 1.0]`,
        where smaller values indicate rarer, higher-impact variants
        (e.g., `quantile = 0.01` -> `top_percentile = 1.0%` -> `phred = 20.0`).
      - If `phred` is missing but `quantile` is provided:
        `phred = -10 * log10(clamp(quantile, 1e-7, 1.0))`
      - If `quantile` is missing but `phred` is provided:
        `quantile = max(1e-7, 10^(-phred / 10))`
    """
    r_val: float | None = float(raw_score) if raw_score is not None else None

    if phred is not None:
        p_val = max(0.0, float(phred))
        q_val = (
            min(1.0, max(1e-7, float(quantile)))
            if quantile is not None
            else max(1e-7, math.pow(10.0, -p_val / 10.0))
        )
    elif quantile is not None:
        q_val = min(1.0, max(1e-7, float(quantile)))
        p_val = max(0.0, -10.0 * math.log10(q_val))
    elif r_val is not None:
        cdf = 1.0 / (1.0 + math.exp(-0.8 * r_val))
        q_val = max(1e-7, 1.0 - cdf)
        p_val = max(0.0, -10.0 * math.log10(q_val))
    else:
        q_val, p_val = 1.0, 0.0

    p_val_rounded = round(p_val, 2)
    q_val_rounded = round(q_val, 6)
    top_pct = round(q_val * 100.0, 5)
    raw_rounded = round(r_val, 4) if r_val is not None else None
    tier = classify_avi_tier(p_val_rounded)
    return {
        "avi_raw": raw_rounded,
        "raw_score": raw_rounded,
        "avi_quantile": q_val_rounded,
        "quantile": q_val_rounded,
        "top_percentile": top_pct,
        "avi_phred": p_val_rounded,
        "phred": p_val_rounded,
        "impact_tier": tier,
    }


DEFAULT_ATLAS_LITEMS = "avi,section:RNA_SEQ,section:DNASE,section:DNASE:REF_SCORES"


def build_alphagenome_atlas_url(
    chromosome: str,
    position: int,
    ref: str,
    alt: str,
    window_bp: int = 1000,
) -> dict[str, str]:
    """Generates AlphaGenome Atlas deep-links for single-variant and locus view.

    Format:
      variant: chr{chrom}:{pos}:{ref}>{alt} -> ?q=chr{chrom}:{pos}:{ref}%3E{alt}&m=variant&lItems=...
      locus: chr{chrom}:{start}-{end} (1-based closed) -> ?q=chr{chrom}:{start}-{end}&m=locus&lItems=...
    """
    chrom_bare = re.sub(r"^chr", "", str(chromosome).strip(), flags=re.IGNORECASE)
    chrom = f"chr{chrom_bare}"
    start = max(1, position - window_bp)
    end = position + window_bp

    variant_str = f"{chrom}:{position}:{ref}>{alt}"
    locus_str = f"{chrom}:{start}-{end}"

    base_url = "https://deepmind.google.com/science/alphagenome/atlas"
    variant_query = urllib.parse.urlencode(
        {"q": variant_str, "m": "variant", "lItems": DEFAULT_ATLAS_LITEMS},
        quote_via=urllib.parse.quote,
        safe=":,-",
    )
    locus_query = urllib.parse.urlencode(
        {"q": locus_str, "m": "locus", "lItems": DEFAULT_ATLAS_LITEMS},
        quote_via=urllib.parse.quote,
        safe=":,-",
    )
    return {
        "variant_url": f"{base_url}?{variant_query}",
        "locus_url": f"{base_url}?{locus_query}",
        "variant_formatted": variant_str,
        "locus_formatted": locus_str,
    }


_FLANKING_CONSEQUENCE_TERMS = frozenset(
    {"upstream_gene_variant", "downstream_gene_variant", "intergenic_variant"}
)


def _select_best_vep_transcript(
    tcs: list[dict[str, Any]],
    most_severe: str | None = None,
    expected_alt: str | None = None,
) -> dict[str, Any] | None:
    """Selects the highest-priority overlapping Ensembl VEP transcript consequence.

    Prioritizes:
      1. Matching variant allele (for multi-allelic rsIDs)
      2. Overlapping transcripts over flanking upstream/downstream transcripts
      3. Transcripts carrying `most_severe_consequence`
      4. MANE Select (`mane_select` / `MANE_Select`) and Ensembl canonical transcripts
      5. Coding consequence (`hgvsp`), `protein_coding` biotype, and `hgvsc`
    """
    exp_alt = expected_alt.strip().upper() if expected_alt else ""
    best_key: tuple[int, ...] | None = None
    best_tc: dict[str, Any] | None = None

    for idx, tc in enumerate(tcs or []):
        if not isinstance(tc, dict) or not tc.get("gene_symbol"):
            continue
        terms = [str(t) for t in (tc.get("consequence_terms") or []) if t]
        var_allele = str(tc.get("variant_allele") or "").strip().upper()
        allele_ok = 1 if (not exp_alt or not var_allele or var_allele == exp_alt) else 0
        is_flanking = (
            bool(terms) and set(terms).issubset(_FLANKING_CONSEQUENCE_TERMS)
        ) or (tc.get("distance") is not None)
        is_overlapping = 0 if is_flanking else 1
        matches_severe = 1 if (most_severe and most_severe in terms) else 0
        mane_list = tc.get("mane") or []
        is_mane = (
            1
            if (
                bool(tc.get("mane_select"))
                or (isinstance(mane_list, list) and "MANE_Select" in mane_list)
            )
            else 0
        )
        is_canonical = 1 if tc.get("canonical") == 1 else 0
        has_hgvsp = 1 if bool(tc.get("hgvsp")) else 0
        is_coding = 1 if tc.get("biotype") == "protein_coding" else 0
        has_hgvsc = 1 if bool(tc.get("hgvsc")) else 0

        rank = (
            allele_ok,
            is_overlapping,
            matches_severe,
            is_mane,
            is_canonical,
            has_hgvsp,
            is_coding,
            has_hgvsc,
            -idx,
        )
        if best_key is None or rank > best_key:
            best_key = rank
            best_tc = tc

    return best_tc


def _extract_vep_fields(
    first_hit: dict[str, Any],
    expected_alt: str | None = None,
) -> dict[str, Any]:
    """Extracts canonical/MANE transcript, HGVS, and consequence fields from a VEP record."""
    most_severe = first_hit.get("most_severe_consequence")
    tcs = first_hit.get("transcript_consequences") or []
    best_tc = _select_best_vep_transcript(
        tcs, most_severe=most_severe, expected_alt=expected_alt
    )

    matched_rsid: str | None = None
    hit_id = str(first_hit.get("id") or "").strip()
    if _RSID_RE.match(hit_id):
        matched_rsid = hit_id.lower()
    else:
        for cv in first_hit.get("colocated_variants") or []:
            cv_id = str((cv or {}).get("id") or "").strip()
            if _RSID_RE.match(cv_id):
                matched_rsid = cv_id.lower()
                break

    if not best_tc:
        return {
            "rsid": matched_rsid,
            "gene_symbol": None,
            "gene_id": None,
            "transcript_id": None,
            "mane_select": None,
            "hgvsc": None,
            "hgvsp": None,
            "amino_acids": None,
            "protein_start": None,
            "impact": None,
            "sift_score": None,
            "sift_prediction": None,
            "polyphen_score": None,
            "polyphen_prediction": None,
            "consequence": most_severe,
            "most_severe_consequence": most_severe,
        }

    tc_terms = [str(t) for t in (best_tc.get("consequence_terms") or []) if t]
    selected_consequence = (
        most_severe
        if (most_severe and most_severe in tc_terms)
        else (tc_terms[0] if tc_terms else most_severe)
    )
    return {
        "rsid": matched_rsid,
        "gene_symbol": best_tc.get("gene_symbol"),
        "gene_id": best_tc.get("gene_id"),
        "transcript_id": best_tc.get("transcript_id"),
        "mane_select": best_tc.get("mane_select"),
        "hgvsc": best_tc.get("hgvsc"),
        "hgvsp": best_tc.get("hgvsp"),
        "amino_acids": best_tc.get("amino_acids"),
        "protein_start": best_tc.get("protein_start"),
        "impact": best_tc.get("impact"),
        "sift_score": best_tc.get("sift_score"),
        "sift_prediction": best_tc.get("sift_prediction"),
        "polyphen_score": best_tc.get("polyphen_score"),
        "polyphen_prediction": best_tc.get("polyphen_prediction"),
        "consequence": selected_consequence,
        "most_severe_consequence": selected_consequence,
    }


async def resolve_variant_coordinates(variant_identifier: str) -> dict[str, Any]:
    """Resolves a variant string (rsID or chr:pos:ref>alt) to GRCh38 genomic coordinates.

    Uses Ensembl GRCh38 VEP REST API (`canonical=1`, `mane=1`) for transcript/HGVS
    annotation and attaches AlphaGenome Atlas deep-links. Never fabricates numeric
    AlphaGenome AVI or 18-modality track scores when raw score files are not supplied.
    """
    ident = variant_identifier.strip()
    match = _COORD_RE.match(ident)
    if match:
        chrom = re.sub(r"^chr", "", match.group("chrom"), flags=re.IGNORECASE).upper()
        pos = int(match.group("pos"))
        ref = match.group("ref").upper()
        alt = match.group("alt").upper()
        atlas_links = build_alphagenome_atlas_url(chrom, pos, ref, alt)
        result: dict[str, Any] = {
            "status": "success",
            "source": "coordinate_format",
            "scoring_source": "atlas_link_only",
            "chromosome": chrom,
            "position": pos,
            "ref": ref,
            "alt": alt,
            "rsid": None,
            "gene_symbol": None,
            "transcript_id": None,
            "mane_select": None,
            "avi_score": None,
            "multimodal_tracks": {},
            "atlas_links": atlas_links,
        }
        region_url = (
            f"https://rest.ensembl.org/vep/human/region/{chrom}:{pos}-{pos}:1/{alt}"
        )
        try:
            vep_data = await fetch_json(
                region_url,
                params={
                    "content-type": "application/json",
                    "hgvs": 1,
                    "canonical": 1,
                    "mane": 1,
                },
                headers={"Content-Type": "application/json"},
            )
            if (
                vep_data
                and isinstance(vep_data, list)
                and isinstance(vep_data[0], dict)
            ):
                vep_fields = _extract_vep_fields(vep_data[0], expected_alt=alt)
                for k, v in vep_fields.items():
                    if v is not None:
                        result[k] = v
                result["source"] = "ensembl_vep"
        except Exception:
            pass
        return result

    if _RSID_RE.match(ident):
        ident_lower = ident.lower()
        ensembl_url = f"https://rest.ensembl.org/vep/human/id/{ident_lower}"
        try:
            vep_data = await fetch_json(
                ensembl_url,
                params={
                    "content-type": "application/json",
                    "hgvs": 1,
                    "canonical": 1,
                    "mane": 1,
                },
                headers={"Content-Type": "application/json"},
            )
            if vep_data and isinstance(vep_data, list) and len(vep_data) > 0:
                first_hit = vep_data[0]
                chrom = str(first_hit.get("seq_region_name", ""))
                pos = int(first_hit.get("start", 0))
                allele_str = first_hit.get("allele_string", "")
                parts = allele_str.split("/")
                ref = parts[0] if len(parts) > 0 else ""
                alt = parts[1] if len(parts) > 1 else ""
                all_alts = parts[1:] if len(parts) > 1 else []

                vep_fields = _extract_vep_fields(first_hit, expected_alt=alt)
                atlas_links = build_alphagenome_atlas_url(chrom, pos, ref, alt)

                result_vep: dict[str, Any] = {
                    "status": "success",
                    "source": "ensembl_vep",
                    "scoring_source": "atlas_link_only",
                    "chromosome": chrom,
                    "position": pos,
                    "ref": ref,
                    "alt": alt,
                    "all_alt_alleles": all_alts,
                    **vep_fields,
                    "rsid": ident_lower,
                    "avi_score": None,
                    "multimodal_tracks": {},
                    "atlas_links": atlas_links,
                }
                return result_vep
            return {
                "status": "error",
                "message": f"Variant rsID '{ident}' not found in Ensembl VEP.",
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Failed resolving rsID '{ident}' via Ensembl VEP: {e}",
            }

    return {
        "status": "error",
        "message": f"Unrecognized variant identifier '{ident}'. Expected 'chr:pos:ref>alt' or 'rsID'.",
    }


_MISSING_TOKENS = frozenset({"", ".", "-", "na", "n/a", "nan", "null", "none"})


def _safe_float(val: Any) -> float | None:
    """Safely converts a value to float, returning None for missing or sentinel values."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return None if math.isnan(val) else float(val)
    s = str(val).strip()
    if s.lower() in _MISSING_TOKENS:
        return None
    try:
        parsed = float(s)
        return None if math.isnan(parsed) else parsed
    except (ValueError, TypeError):
        return None


def _extract_coords_from_str(coord_str: str) -> tuple[str, int, str, str] | None:
    m = _COORD_RE.match(coord_str.strip())
    if not m:
        return None
    return (
        re.sub(r"^chr", "", m.group("chrom"), flags=re.IGNORECASE),
        int(m.group("pos")),
        m.group("ref").upper(),
        m.group("alt").upper(),
    )


def parse_and_score_alphagenome_output(
    content_or_data: str | bytes | dict | list,
    filename_hint: str = "",
) -> dict[str, Any]:
    """Ingests AlphaGenome raw outputs, pre-computed AVI tables, JSON, or VCF.

    Supports:
    - AlphaGenome SDK analyze_variant_effects JSON dict/list or benchmark_variants dict
    - Tabular TSV/CSV with avi_phred / avi_quantile columns
    - VCF v4.2 text or decompressed bytes with AVI_PHRED / AVI_RAW / AVI_Q / GENE info tags
    """
    if isinstance(content_or_data, bytes):
        text_content = content_or_data.decode("utf-8", errors="replace")
    elif isinstance(content_or_data, str):
        text_content = content_or_data
    elif isinstance(content_or_data, (dict, list)):
        text_content = json.dumps(content_or_data)
    else:
        text_content = str(content_or_data)

    parsed_variants: list[dict[str, Any]] = []

    # 0. Guard against callers accidentally passing a GCS URI or relative file path string
    #    instead of file contents (prevents false permission/format error hallucinations).
    trimmed = text_content.strip()
    if (
        isinstance(content_or_data, str)
        and "\n" not in trimmed
        and (
            trimmed.lower().startswith("gs://")
            or re.match(
                r"^(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+\.(?:vcf\.gz|vcf|jsonl|json|tsv|csv)$",
                trimmed,
                re.IGNORECASE,
            )
        )
    ):
        return {
            "status": "error",
            "message": (
                f"Input '{trimmed}' looks like a file path or GCS URI rather than file contents. "
                "Call import_alphagenome_from_gcs(gcs_uri) to load files from Google Cloud Storage."
            ),
            "variants": [],
        }

    # 1. Try parsing as JSON or JSONL
    if (trimmed.startswith("{") and trimmed.endswith("}")) or (
        trimmed.startswith("[") and trimmed.endswith("]")
    ):
        try:
            try:
                data = json.loads(trimmed)
                json_fmt = "json"
            except json.JSONDecodeError:
                jsonl_lines = [ln.strip() for ln in trimmed.splitlines() if ln.strip()]
                if not jsonl_lines or not all(
                    ln.startswith("{") and ln.endswith("}") for ln in jsonl_lines
                ):
                    raise
                data = [json.loads(ln) for ln in jsonl_lines]
                json_fmt = "jsonl"

            if isinstance(data, list):
                items = data
            else:
                items = data.get("variants") or data.get("benchmark_variants") or [data]
            for item in items:
                chrom = str(
                    item.get("chromosome") or item.get("chrom") or item.get("chr") or ""
                )
                pos = int(item.get("position") or item.get("pos") or 0)
                ref = str(item.get("ref") or item.get("reference") or "").upper()
                alt = str(item.get("alt") or item.get("alternate") or "").upper()

                if not chrom or not pos:
                    coord_candidate = (
                        item.get("grch38_coordinates") or item.get("variant") or ""
                    )
                    parsed_c = _extract_coords_from_str(str(coord_candidate))
                    if parsed_c:
                        chrom, pos, ref, alt = parsed_c

                chrom = re.sub(r"^chr", "", chrom.strip(), flags=re.IGNORECASE)
                rsid = item.get("rsid") or item.get("variant_id")
                gene = item.get("gene_symbol") or item.get("gene")

                ag_pred = item.get("alphagenome_prediction") or {}

                modalities: dict[str, float] = {}
                for track in ALPHAGENOME_TRACKS:
                    if track in item:
                        v_f = _safe_float(item[track])
                        if v_f is not None:
                            modalities[track] = v_f
                    elif track.lower() in item:
                        v_f = _safe_float(item[track.lower()])
                        if v_f is not None:
                            modalities[track] = v_f
                if isinstance(ag_pred.get("multimodal_tracks"), dict):
                    for k, v in ag_pred["multimodal_tracks"].items():
                        v_f = _safe_float(v)
                        if v_f is not None:
                            modalities[k] = v_f

                raw_s = _safe_float(
                    item.get("avi_raw")
                    if item.get("avi_raw") is not None
                    else ag_pred.get("avi_raw")
                )
                q_s = _safe_float(
                    item.get("avi_quantile")
                    if item.get("avi_quantile") is not None
                    else ag_pred.get("avi_quantile")
                )
                p_s = _safe_float(
                    item.get("avi_phred")
                    if item.get("avi_phred") is not None
                    else ag_pred.get("avi_phred")
                )

                if raw_s is None and modalities:
                    raw_s = sum(abs(v) for v in modalities.values())

                has_ag_scores = any(x is not None for x in (raw_s, q_s, p_s)) or bool(
                    modalities
                )
                if has_ag_scores:
                    score_info: dict[str, Any] | None = compute_avi_score(
                        raw_score=raw_s, quantile=q_s, phred=p_s
                    )
                    score_info["scoring_source"] = "alphagenome_model"
                    row_source = "alphagenome_model"
                else:
                    score_info = None
                    row_source = "atlas_link_only"
                atlas_links = build_alphagenome_atlas_url(chrom, pos, ref, alt)

                parsed_variants.append(
                    {
                        "chromosome": chrom,
                        "position": pos,
                        "ref": ref,
                        "alt": alt,
                        "rsid": rsid,
                        "gene_symbol": gene,
                        "scoring_source": row_source,
                        "avi_score": score_info,
                        "modalities": modalities,
                        "atlas_links": atlas_links,
                    }
                )
            return {
                "status": "success",
                "format": json_fmt,
                "count": len(parsed_variants),
                "variants": parsed_variants,
            }
        except Exception:
            pass

    # 2. Try parsing as VCF
    if "#CHROM" in text_content:
        lines = text_content.splitlines()
        for line in lines:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 5:
                chrom = re.sub(r"^chr", "", parts[0].strip(), flags=re.IGNORECASE)
                try:
                    pos = int(parts[1].strip())
                except (ValueError, TypeError):
                    continue
                if not chrom or pos <= 0:
                    continue
                rsid_raw = parts[2].strip()
                rsid = (
                    rsid_raw
                    if rsid_raw and rsid_raw.lower() not in _MISSING_TOKENS
                    else None
                )
                ref = parts[3].strip().upper()
                alt = parts[4].strip().upper()
                info = parts[7] if len(parts) > 7 else ""

                m_phred = re.search(r"AVI_PHRED=([^;\s]+)", info)
                phred_val = _safe_float(m_phred.group(1)) if m_phred else None
                m_raw = re.search(r"AVI_RAW=([^;\s]+)", info)
                raw_val = _safe_float(m_raw.group(1)) if m_raw else None
                m_q = re.search(r"AVI_Q=([^;\s]+)", info)
                q_val = _safe_float(m_q.group(1)) if m_q else None
                m_gene = re.search(r"GENE=([^;\s]+)", info)
                gene_val = m_gene.group(1) if m_gene else None

                if any(x is not None for x in (raw_val, q_val, phred_val)):
                    score_info = compute_avi_score(
                        raw_score=raw_val, quantile=q_val, phred=phred_val
                    )
                    score_info["scoring_source"] = "alphagenome_model"
                    row_source = "alphagenome_model"
                else:
                    score_info = None
                    row_source = "atlas_link_only"
                atlas_links = build_alphagenome_atlas_url(chrom, pos, ref, alt)

                parsed_variants.append(
                    {
                        "chromosome": chrom,
                        "position": pos,
                        "ref": ref,
                        "alt": alt,
                        "rsid": rsid,
                        "gene_symbol": gene_val,
                        "scoring_source": row_source,
                        "avi_score": score_info,
                        "modalities": {},
                        "atlas_links": atlas_links,
                    }
                )
        return {
            "status": "success",
            "format": "vcf",
            "count": len(parsed_variants),
            "variants": parsed_variants,
        }

    # 3. Try parsing as TSV or CSV
    delimiter = "\t" if "\t" in text_content.split("\n", 1)[0] else ","
    reader = csv.DictReader(io.StringIO(text_content), delimiter=delimiter)
    if reader.fieldnames:
        for row in reader:
            chrom = (
                row.get("chromosome")
                or row.get("chrom")
                or row.get("chr")
                or row.get("#CHROM")
                or ""
            ).strip()
            pos_str = (
                row.get("position") or row.get("pos") or row.get("POS") or "0"
            ).strip()
            try:
                pos = int(pos_str)
            except (ValueError, TypeError):
                pos = 0
            ref = (
                (row.get("ref") or row.get("reference") or row.get("REF") or "")
                .strip()
                .upper()
            )
            alt = (
                (row.get("alt") or row.get("alternate") or row.get("ALT") or "")
                .strip()
                .upper()
            )

            if (not chrom or pos <= 0) and row.get("variant"):
                parsed_c = _extract_coords_from_str(str(row["variant"]))
                if parsed_c:
                    chrom, pos, ref, alt = parsed_c

            chrom = re.sub(r"^chr", "", chrom, flags=re.IGNORECASE)
            if chrom.lower() in _MISSING_TOKENS:
                chrom = ""

            raw_rsid = (row.get("rsid") or row.get("ID") or "").strip()
            rsid = (
                raw_rsid
                if raw_rsid and raw_rsid.lower() not in _MISSING_TOKENS
                else None
            )
            if not ((chrom and pos > 0) or rsid):
                continue

            raw_gene = (
                row.get("gene_symbol") or row.get("gene") or row.get("GENE") or ""
            ).strip()
            gene = (
                raw_gene
                if raw_gene and raw_gene.lower() not in _MISSING_TOKENS
                else None
            )

            phred_str = row.get("avi_phred") or row.get("AVI_PHRED")
            phred_val = _safe_float(phred_str)
            quantile_str = row.get("avi_quantile") or row.get("AVI_QUANTILE")
            quantile_val = _safe_float(quantile_str)
            raw_str = row.get("avi_raw") or row.get("AVI_RAW")
            raw_val = _safe_float(raw_str)

            if any(x is not None for x in (raw_val, quantile_val, phred_val)):
                score_info = compute_avi_score(
                    raw_score=raw_val, quantile=quantile_val, phred=phred_val
                )
                score_info["scoring_source"] = "alphagenome_model"
                row_source = "alphagenome_model"
            else:
                score_info = None
                row_source = "atlas_link_only"
            atlas_links = build_alphagenome_atlas_url(chrom, pos, ref, alt)

            parsed_variants.append(
                {
                    "chromosome": chrom,
                    "position": pos,
                    "ref": ref,
                    "alt": alt,
                    "rsid": rsid,
                    "gene_symbol": gene,
                    "scoring_source": row_source,
                    "avi_score": score_info,
                    "modalities": {},
                    "atlas_links": atlas_links,
                }
            )
        if parsed_variants:
            return {
                "status": "success",
                "format": "tabular",
                "count": len(parsed_variants),
                "variants": parsed_variants,
            }

    return {
        "status": "error",
        "message": "Unable to detect valid JSON, VCF, or TSV/CSV format for AlphaGenome input.",
        "variants": [],
    }
