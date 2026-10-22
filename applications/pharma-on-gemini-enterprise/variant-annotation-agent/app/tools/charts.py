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

"""Publication-style genomic chart generators (AlphaGenome 18-modality & gnomAD v4 ancestry AF).

Uses thread-safe `matplotlib.figure.Figure` + `FigureCanvasAgg` (no global pyplot state)
and integrates with ADK `ArtifactService` (`save_artifact`) so charts appear directly
in Gemini Enterprise's native Artifacts drawer.
"""

from __future__ import annotations

import io
import re
from typing import Any

import matplotlib

matplotlib.use("Agg")
from google.adk.tools import ToolContext
from google.genai import types
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

_NAVY = "#0b2545"
_STEEL = "#1d4e89"
_TEAL = "#2a9d8f"
_AMBER = "#f4a261"
_CORAL = "#e76f51"
_GRID = "#e3e7ee"

_CANONICAL_ANCESTRIES: tuple[tuple[str, str], ...] = (
    ("afr", "AFR"),
    ("amr", "AMR"),
    ("asj", "ASJ"),
    ("eas", "EAS"),
    ("fin", "FIN"),
    ("mid", "MID"),
    ("nfe", "NFE"),
    ("sas", "SAS"),
)


def slugify_artifact_label(label: str) -> str:
    """Normalizes a variant or file label into a safe artifact filename stem."""
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", label.strip()).strip("_")
    return (cleaned or "variant")[:48]


def _render_figure_to_png(fig: Figure) -> bytes:
    """Renders a Matplotlib Figure to PNG bytes in memory without global state."""
    FigureCanvasAgg(fig)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    return buf.getvalue()


def build_alphagenome_modality_chart_png(
    variant_label: str,
    modalities: dict[str, float],
    avi_phred: float | None = None,
    impact_tier: str | None = None,
) -> bytes | None:
    """Builds a horizontal bar chart PNG of AlphaGenome 18-modality track perturbations."""
    numeric_items: list[tuple[str, float]] = []
    for k, v in (modalities or {}).items():
        try:
            numeric_items.append((str(k), float(v)))
        except (TypeError, ValueError):
            continue

    if not numeric_items:
        return None

    # Sort ascending by absolute magnitude so highest-impact tracks appear at the top
    numeric_items.sort(key=lambda kv: abs(kv[1]))
    labels = [k for k, _ in numeric_items]
    values = [abs(v) for _, v in numeric_items]
    max_val = max(values) if values else 1.0

    height = max(3.2, 0.36 * len(labels) + 1.2)
    fig = Figure(figsize=(7.2, height))
    ax = fig.subplots()

    colors = [
        _CORAL if (max_val > 0 and v >= 0.75 * max_val) else _STEEL for v in values
    ]
    bars = ax.barh(range(len(labels)), values, color=colors, height=0.68)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=8.5, color=_NAVY)

    subtitle_parts: list[str] = []
    if avi_phred is not None:
        subtitle_parts.append(f"AVI Phred: {avi_phred:.2f}")
    if impact_tier:
        subtitle_parts.append(impact_tier)
    subtitle = f" ({' — '.join(subtitle_parts)})" if subtitle_parts else ""

    ax.set_title(
        f"AlphaGenome Multimodal Perturbation: {variant_label}{subtitle}",
        color=_NAVY,
        fontsize=11,
        fontweight="bold",
        pad=10,
    )
    ax.set_xlabel("Absolute Track Delta / Score Magnitude", color=_STEEL, fontsize=9)
    ax.tick_params(colors=_STEEL, labelsize=8.5)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.grid(axis="x", color=_GRID, linewidth=0.7)
    ax.set_axisbelow(True)

    x_pad = max(max_val * 1.18, 0.1)
    ax.set_xlim(0, x_pad)
    for bar, raw_val in zip(bars, values, strict=False):
        ax.text(
            bar.get_width() + x_pad * 0.015,
            bar.get_y() + bar.get_height() / 2.0,
            f"{raw_val:.3f}",
            va="center",
            ha="left",
            fontsize=7.8,
            color=_NAVY,
        )

    return _render_figure_to_png(fig)


def build_avi_phred_comparison_chart_png(
    variants: list[dict[str, Any]],
) -> bytes | None:
    """Builds an AVI Phred impact tier chart when batch VCF/TSV or VEP variants lack raw 18-track dicts."""
    rows: list[tuple[str, float]] = []
    for var in variants or []:
        avi = var.get("avi_score") or {}
        phred = avi.get("phred")
        if phred is None:
            continue
        try:
            phred_f = float(phred)
        except (TypeError, ValueError):
            continue
        ident = (
            var.get("rsid")
            or f"chr{str(var.get('chromosome', '')).lstrip('chr')}:{var.get('position')}:{var.get('ref')}>{var.get('alt')}"
        )
        gene = var.get("gene_symbol")
        lbl = f"{ident} ({gene})" if gene else str(ident)
        rows.append((lbl, phred_f))

    if not rows:
        return None

    rows.sort(key=lambda r: r[1])
    labels = [r[0] for r in rows]
    values = [r[1] for r in rows]

    height = max(2.8, 0.45 * len(labels) + 1.2)
    fig = Figure(figsize=(7.2, height))
    ax = fig.subplots()

    colors = [
        _CORAL
        if v >= 20.0
        else (_AMBER if v >= 15.0 else (_TEAL if v >= 10.0 else _STEEL))
        for v in values
    ]
    bars = ax.barh(range(len(labels)), values, color=colors, height=0.62)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=8.8, color=_NAVY)

    ax.axvline(20.0, color=_CORAL, linestyle="--", linewidth=1.0, label="High (>=20)")
    ax.axvline(
        15.0, color=_AMBER, linestyle=":", linewidth=1.0, label="Mod-High (>=15)"
    )
    ax.axvline(10.0, color=_TEAL, linestyle=":", linewidth=1.0, label="Moderate (>=10)")

    ax.set_title(
        "AlphaGenome Variant Impact (AVI) Phred Calibration",
        color=_NAVY,
        fontsize=11,
        fontweight="bold",
        pad=10,
    )
    ax.set_xlabel(
        "AVI Phred Score (-10 * log10(avi_quantile))", color=_STEEL, fontsize=9
    )
    ax.tick_params(colors=_STEEL, labelsize=8.5)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.grid(axis="x", color=_GRID, linewidth=0.7)
    ax.set_axisbelow(True)
    ax.legend(loc="lower right", frameon=True, fontsize=7.5)

    max_x = max(max(values), 22.0) * 1.15
    ax.set_xlim(0, max_x)
    for bar, val in zip(bars, values, strict=False):
        ax.text(
            bar.get_width() + max_x * 0.015,
            bar.get_y() + bar.get_height() / 2.0,
            f"{val:.2f}",
            va="center",
            ha="left",
            fontsize=8.0,
            color=_NAVY,
        )

    return _render_figure_to_png(fig)


def build_gnomad_ancestry_chart_png(
    variant_label: str,
    populations: dict[str, Any] | None,
    global_af: float | None = None,
) -> bytes | None:
    """Builds a bar chart PNG of gnomAD v4 continental ancestry allele frequencies."""
    if populations is None and global_af is None:
        return None

    pop_map: dict[str, float] = {}
    for raw_k, raw_v in (populations or {}).items():
        key_lower = str(raw_k).strip().lower()
        if isinstance(raw_v, dict):
            af_val = raw_v.get("af")
        else:
            af_val = raw_v
        try:
            pop_map[key_lower] = float(af_val or 0.0)
        except (TypeError, ValueError):
            pop_map[key_lower] = 0.0

    categories: list[str] = []
    af_values: list[float] = []
    for code, display in _CANONICAL_ANCESTRIES:
        if code in pop_map or populations is not None:
            categories.append(display)
            af_values.append(max(0.0, pop_map.get(code, 0.0)))

    if global_af is not None:
        try:
            categories.append("Global")
            af_values.append(max(0.0, float(global_af)))
        except (TypeError, ValueError):
            pass

    if not categories:
        return None

    fig = Figure(figsize=(7.0, 3.6))
    ax = fig.subplots()

    bar_colors = [_AMBER if c == "Global" else _NAVY for c in categories]
    bars = ax.bar(range(len(categories)), af_values, color=bar_colors, width=0.62)
    ax.set_xticks(range(len(categories)))
    ax.set_xticklabels(categories, fontsize=8.8, color=_NAVY)

    max_af = max(af_values) if af_values else 0.0
    if max_af >= 0.008:
        ax.axhline(
            0.05, color=_CORAL, linestyle="--", linewidth=1.0, label="ACMG BA1 (5%)"
        )
        ax.axhline(
            0.01, color=_AMBER, linestyle=":", linewidth=1.0, label="ACMG BS1 (1%)"
        )
        ax.legend(loc="upper right", frameon=True, fontsize=7.8)

    acmg_note = (
        "Absent / Ultra-Rare (PM2_Supporting)"
        if max_af <= 0.0001
        else (f"Max AF: {max_af:.4%}")
    )
    ax.set_title(
        f"gnomAD v4 Continental Ancestry Allele Frequency: {variant_label} ({acmg_note})",
        color=_NAVY,
        fontsize=10.5,
        fontweight="bold",
        pad=10,
    )
    ax.set_ylabel("Allele Frequency (AF)", color=_STEEL, fontsize=9)
    ax.tick_params(colors=_STEEL, labelsize=8.5)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.grid(axis="y", color=_GRID, linewidth=0.7)
    ax.set_axisbelow(True)

    y_top = max(max_af * 1.22, 0.001 if max_af == 0 else max_af * 1.22)
    ax.set_ylim(0, y_top)
    for bar, val in zip(bars, af_values, strict=False):
        label_str = "0" if val == 0 else (f"{val:.3f}" if val >= 0.01 else f"{val:.2e}")
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            bar.get_height() + y_top * 0.02,
            label_str,
            ha="center",
            va="bottom",
            fontsize=7.5,
            color=_NAVY,
        )

    return _render_figure_to_png(fig)


async def render_alphagenome_modality_chart(
    variant_label: str,
    modalities: dict[str, float],
    avi_phred: float | None = None,
    impact_tier: str | None = None,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Renders an AlphaGenome 18-modality perturbation bar chart PNG and saves it as a downloadable artifact.

    Args:
        variant_label: Variant label for the chart title and filename (e.g. 'rs12740374').
        modalities: Mapping of AlphaGenome modality track names to numeric delta scores.
        avi_phred: Optional calibrated AVI Phred score.
        impact_tier: Optional AVI impact tier label (e.g. 'High Impact').
    """
    png_bytes = build_alphagenome_modality_chart_png(
        variant_label=variant_label,
        modalities=modalities,
        avi_phred=avi_phred,
        impact_tier=impact_tier,
    )
    if png_bytes is None:
        return {
            "status": "error",
            "message": "No numeric AlphaGenome modality scores provided to chart.",
        }

    filename = f"alphagenome_modalities_{slugify_artifact_label(variant_label)}.png"
    if tool_context is not None:
        part = types.Part.from_bytes(data=png_bytes, mime_type="image/png")
        version = await tool_context.save_artifact(filename=filename, artifact=part)
        return {
            "status": "success",
            "filename": filename,
            "version": version,
            "bytes": len(png_bytes),
        }
    return {"status": "success", "filename": filename, "bytes": len(png_bytes)}


async def render_gnomad_ancestry_chart(
    variant_label: str,
    populations: dict[str, float],
    global_af: float | None = None,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Renders a gnomAD v4 continental ancestry allele frequency bar chart PNG and saves it as a downloadable artifact.

    Args:
        variant_label: Variant label for the chart title and filename (e.g. 'rs12740374').
        populations: Mapping of continental ancestry codes (AFR, AMR, ASJ, EAS, FIN, MID, NFE, SAS) to AF floats.
        global_af: Optional joint global allele frequency.
    """
    png_bytes = build_gnomad_ancestry_chart_png(
        variant_label=variant_label,
        populations=populations,
        global_af=global_af,
    )
    if png_bytes is None:
        return {
            "status": "error",
            "message": "No gnomAD population allele frequency values provided to chart.",
        }

    filename = f"gnomad_ancestry_af_{slugify_artifact_label(variant_label)}.png"
    if tool_context is not None:
        part = types.Part.from_bytes(data=png_bytes, mime_type="image/png")
        version = await tool_context.save_artifact(filename=filename, artifact=part)
        return {
            "status": "success",
            "filename": filename,
            "version": version,
            "bytes": len(png_bytes),
        }
    return {"status": "success", "filename": filename, "bytes": len(png_bytes)}
